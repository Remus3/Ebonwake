"""Plan 082 routes: /api/portraits and /api/portraits/img/<id> (indexed ids only)."""

import http.client
import json
import os
import threading
import time

import pytest

from server.ew import app as ewapp
from server.ew import gamewatch
from tests.test_portraits import bmp

CHAR = "12345678901234567"


def _no_network(url, timeout=None):
    raise AssertionError(f"no network in tests: {url}")


class Tasklist:
    def __call__(self):
        return True


@pytest.fixture()
def srv(tmp_path):
    inst, docs = tmp_path / "install", tmp_path / "docs"
    (inst / "Log").mkdir(parents=True)
    (docs / "ScreenShot").mkdir(parents=True)
    (docs / "FaceTexture").mkdir(parents=True)
    w = gamewatch.GameWatch(install_dir=str(inst), documents_dir=str(docs), tasklist=Tasklist())
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          market_seed=[], profile_cfg={}, game_watch=w)
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield s, inst, docs
    s.shutdown()
    s.server_close()


def _req(s, path):
    c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=5)
    c.request("GET", path)
    r = c.getresponse()
    out = r.read()
    c.close()
    return r.status, dict(r.getheaders()), out


def _log(inst):
    line = json.dumps({"Date": "2026-10-06 20:00:00", "LogType": "Info",
                       "Log": f"GameVariableManager (<documents>/black desert/UserCache/9/500/{CHAR}/"
                              "gameVariable.xml)"}) + "\r\n"
    (inst / "Log" / "Client_2026-10-06_200000.json").write_bytes(
        b"\xff\xfe" + line.encode("utf-16-le"))


def test_portraits_route_and_image(srv):
    s, inst, docs = srv
    st, _, body = _req(s, "/api/portraits")
    assert st == 200 and json.loads(body)["classes"] == {"Deadeye": {"current": None}}
    _log(inst)
    p = docs / "FaceTexture" / f"{CHAR}.bmp"
    p.write_bytes(bmp(6, 8))
    old = time.time() - 60
    os.utime(p, (old, old))
    before = s.bus.snapshot().get("portraits", 0)
    s.game.poll()  # the poller registers the load, binds A1, archives
    assert s.bus.snapshot().get("portraits", 0) > before
    v = json.loads(_req(s, "/api/portraits")[2])
    cur = v["classes"]["Deadeye"]["current"]
    assert cur["char_no"] == CHAR and cur["from"] == "auto" and v["char_no"] == CHAR
    assert v["loaded_cls"] == "Deadeye" and v["unknown"] == 0
    for size in ("s", "m", "full"):
        st, hdr, png = _req(s, f"/api/portraits/img/{cur['id']}?size={size}")
        assert st == 200 and png[:8] == b"\x89PNG\r\n\x1a\n"
        assert hdr["Content-Type"] == "image/png" and hdr["Cache-Control"] == "no-store"
    assert _req(s, f"/api/portraits/img/{cur['id']}")[0] == 200  # default full


@pytest.mark.parametrize("bad", [f"{CHAR}-1", "..%2F..%2Fstore%2Fportraits.json",
                                 "../../store/portraits.json", "..", "%2e%2e", "x", "",
                                 f"{CHAR}-1/../../x", "%5Cwindows%5Cx", "%2Fetc%2Fx"])
def test_image_rejects_unindexed_and_pathlike_ids(srv, bad):
    s, _, _ = srv
    st, _, body = _req(s, "/api/portraits/img/" + bad)
    assert st == 404 and json.loads(body) == {"error": "not found"}


def test_image_rejects_bad_size(srv):
    s, inst, docs = srv
    _log(inst)
    p = docs / "FaceTexture" / f"{CHAR}.bmp"
    p.write_bytes(bmp(4, 4))
    os.utime(p, (time.time() - 60,) * 2)
    s.game.poll()
    pid = s.portraits.current("Deadeye")["id"]
    assert _req(s, f"/api/portraits/img/{pid}?size=../x")[0] == 404


def _post(s, path, body):
    c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=5)
    data = json.dumps(body).encode("utf-8")
    c.request("POST", path, body=data, headers={"Content-Type": "application/json",
                                                "Content-Length": str(len(data))})
    r = c.getresponse()
    out = r.read()
    c.close()
    return r.status, json.loads(out)


def test_gallery_routes_bind_pick_clear_and_shot_image(srv):
    # Plan 083: ?cls= gallery, a screenshot served by index id, POST bind / pick,
    # {"clear": "portrait.<cls>"} through /api/settings = Use newest.
    s, inst, docs = srv
    _log(inst)
    p = docs / "FaceTexture" / f"{CHAR}.bmp"
    p.write_bytes(bmp(4, 4))
    os.utime(p, (time.time() - 60,) * 2)
    sp = docs / "ScreenShot" / "shot_1.jpg"
    sp.write_bytes(b"\xff\xd8shot")
    # The watcher lists only shots with mtime >= its start; a fresh file's mtime
    # can land a hair below time.time() on Windows (refute r1 flake), so pin it.
    os.utime(sp, (s.game.started + 2,) * 2)
    s.game.poll()
    v = json.loads(_req(s, "/api/portraits?cls=Deadeye")[2])
    assert v["cls"] == "Deadeye" and len(v["history"]) == 1 and v["shots"] == []
    item = [i for i in v["unknown_items"] if i["kind"] == "shot"][0]  # no window: unknown
    st, hdr, data = _req(s, f"/api/portraits/img/{item['id']}?size=s")
    assert st == 200 and data == b"\xff\xd8shot" and hdr["Content-Type"] == "image/jpeg"
    assert _post(s, "/api/portraits", {"pick": {"cls": "Deadeye", "id": item["id"]}})[0] == 400
    st, v = _post(s, "/api/portraits", {"bind": {"id": item["id"], "cls": "Deadeye"}})
    assert st == 200 and [x["id"] for x in v["shots"]] == [item["id"]]
    st, v = _post(s, "/api/portraits", {"pick": {"cls": "Deadeye", "id": item["id"]}})
    cur = v["classes"]["Deadeye"]["current"]
    assert st == 200 and cur["id"] == item["id"] and cur["from"] == "override"
    keys = [i["key"] for i in json.loads(_req(s, "/api/overrides")[2])["items"]]
    assert "portrait.Deadeye" in keys
    st, out = _post(s, "/api/settings", {"clear": "portrait.Deadeye"})
    assert st == 200 and out["cleared"] == "portrait.Deadeye"
    cur = json.loads(_req(s, "/api/portraits")[2])["classes"]["Deadeye"]["current"]
    assert cur["from"] == "auto" and cur["kind"] == "portrait"
    assert _post(s, "/api/portraits", {"nope": 1})[0] == 400
