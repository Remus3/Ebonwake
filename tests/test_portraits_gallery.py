"""Plan 083: screenshot attribution, class gallery, pick as a plan 079 override.

Every BMP / screenshot is a tiny synthetic file in tmp_path; no test reads the
real Documents folder. Times in loads / windows are tz-aware ISO so the
attribution never depends on the host time zone.
"""

import os

import pytest

from server.ew import overrides as ov
from server.ew import portraits
from server.ew.store import Store
from tests.test_portraits import CHAR_A, CHAR_B, T0, Clock, bmp


def iso(t):
    return portraits._iso(t)


@pytest.fixture()
def env(tmp_path):
    docs = tmp_path / "docs"
    (docs / "FaceTexture").mkdir(parents=True)
    (docs / "ScreenShot").mkdir(parents=True)
    st = {"loads": [], "cls": "Deadeye", "shots": [], "windows": [], "events": 0}
    clk = Clock()
    store = Store(tmp_path / "store")
    led = ov.OverrideLedger(store, clock=clk)

    def bump():
        st["events"] += 1

    svc = portraits.PortraitService(
        store, tmp_path / "runtime" / "portraits",
        documents=lambda: docs, loads=lambda: st["loads"], progress_cls=lambda: st["cls"],
        clock=clk, on_change=bump, shots=lambda: st["shots"], windows=lambda: st["windows"],
        ledger=led)
    return svc, docs, st, clk, led


def face(docs, char_no, data, mtime):
    p = docs / "FaceTexture" / f"{char_no}.bmp"
    p.write_bytes(data)
    os.utime(p, (mtime, mtime))


def shot(docs, st, name, t, data=b"\xff\xd8jpeg"):
    (docs / "ScreenShot" / name).write_bytes(data)
    st["shots"].insert(0, {"name": name, "size": len(data), "mtime": iso(t)})


def ids(rows):
    return [r["id"] for r in rows]


# -- attribution (pure) --------------------------------------------------------

WIN = [{"start": iso(T0), "end": iso(T0 + 3600)}]


def test_load_before_shot_in_same_window_attributes():
    loads = [{"char_no": CHAR_A, "at": iso(T0 + 60)}]
    assert portraits.attribute(iso(T0 + 120), loads, WIN) == CHAR_A


def test_newest_load_before_the_shot_wins():
    loads = [{"char_no": CHAR_A, "at": iso(T0 + 60)}, {"char_no": CHAR_B, "at": iso(T0 + 600)},
             {"char_no": CHAR_A, "at": iso(T0 + 900)}]
    assert portraits.attribute(iso(T0 + 700), loads, WIN) == CHAR_B


def test_shot_before_any_load_is_unknown():
    loads = [{"char_no": CHAR_A, "at": iso(T0 + 600)}]
    assert portraits.attribute(iso(T0 + 120), loads, WIN) is None


def test_shot_after_logout_is_unknown():
    loads = [{"char_no": CHAR_A, "at": iso(T0 + 60)}]
    assert portraits.attribute(iso(T0 + 7200), loads, WIN) is None
    assert portraits.attribute(iso(T0 + 120), loads, []) is None  # no window at all


def test_load_from_an_earlier_window_never_carries_over():
    wins = WIN + [{"start": iso(T0 + 7200), "end": None}]  # second window still open
    loads = [{"char_no": CHAR_A, "at": iso(T0 + 60)}]
    assert portraits.attribute(iso(T0 + 7300), loads, wins) is None
    loads.append({"char_no": CHAR_B, "at": iso(T0 + 7250)})
    assert portraits.attribute(iso(T0 + 7300), loads, wins) == CHAR_B


def test_naive_log_date_is_local_wall_time():
    import datetime as dt
    local = dt.datetime.fromtimestamp(T0 + 60).strftime("%Y-%m-%d %H:%M:%S")
    assert portraits.attribute(iso(T0 + 120), [{"char_no": CHAR_A, "at": local}], WIN) == CHAR_A


# -- index ---------------------------------------------------------------------

def test_shots_indexed_once_and_capped(env, monkeypatch):
    svc, docs, st, _, _ = env
    monkeypatch.setattr(portraits, "SHOT_CAP", 3)
    for i in range(5):
        shot(docs, st, f"shot_{i}.jpg", T0 + i)
    svc.poll("logged_in", T0 + 10)
    rows = svc.store.get("portraits")["shots"]
    assert [r["file"] for r in rows] == ["shot_2.jpg", "shot_3.jpg", "shot_4.jpg"]
    assert all(r["char_no"] is None for r in rows)  # no window: unknown
    n = st["events"]
    svc.poll("logged_in", T0 + 11)  # re-listing the same files is a no-op
    assert svc.store.get("portraits")["shots"] == rows and st["events"] == n
    assert not (docs / "ScreenShot" / "shot_0.jpg").stat().st_size == 0  # never touched


def test_shot_attributed_at_record_time(env):
    svc, docs, st, _, _ = env
    st["windows"] = [{"start": iso(T0), "end": None}]
    st["loads"] = [{"char_no": CHAR_A, "at": iso(T0 + 5)}]
    shot(docs, st, "a.jpg", T0 + 10)
    svc.poll("logged_in", T0 + 20)
    assert svc.store.get("portraits")["shots"][0]["char_no"] == CHAR_A


def test_bad_names_never_indexed(env):
    svc, docs, st, _, _ = env
    st["shots"] = [{"name": "..\\x.jpg", "size": 1, "mtime": iso(T0)},
                   {"name": "a/b.jpg", "size": 1, "mtime": iso(T0)},
                   {"name": "x.txt", "size": 1, "mtime": iso(T0)},
                   {"name": "ok.jpg", "size": 1, "mtime": "nope"}]
    svc.poll("logged_in", T0)
    assert svc.store.get("portraits").get("shots", []) == []


# -- gallery -------------------------------------------------------------------

def _two_chars(env):
    """CHAR_A bound to Deadeye (A1), CHAR_B unbound; one shot each + one unknown."""
    svc, docs, st, clk, led = env
    st["windows"] = [{"start": iso(T0 - 1000), "end": None}]
    st["loads"] = [{"char_no": CHAR_A, "at": iso(T0 - 900)}]
    face(docs, CHAR_A, bmp(4, 4), T0 - 800)
    shot(docs, st, "dead.jpg", T0 - 700)
    svc.poll("logged_in", T0)
    st["loads"].append({"char_no": CHAR_B, "at": iso(T0 - 600)})
    face(docs, CHAR_B, bmp(4, 4, pixel=lambda x, y: (1, 2, 3)), T0 - 500)
    shot(docs, st, "other.jpg", T0 - 400)
    st["windows"] = [{"start": iso(T0 - 1000), "end": iso(T0 - 300)}]
    shot(docs, st, "after.jpg", T0 - 200)  # after logout: unknown
    svc.poll("logged_in", T0)
    return svc


def test_gallery_shots_for_a_class_never_other_or_unknown(env):
    svc = _two_chars(env)
    v = svc.view(cls="Deadeye")
    rows = svc.store.get("portraits")["shots"]
    by = {r["file"]: r for r in rows}
    assert by["dead.jpg"]["char_no"] == CHAR_A and by["other.jpg"]["char_no"] == CHAR_B
    assert by["after.jpg"]["char_no"] is None
    assert ids(v["shots"]) == [by["dead.jpg"]["id"]]
    assert [h["char_no"] for h in v["history"]] == [CHAR_A]
    unk = {i["id"] for i in v["unknown_items"]}
    assert by["other.jpg"]["id"] in unk and by["after.jpg"]["id"] in unk
    assert any(i["kind"] == "portrait" and i["char_no"] == CHAR_B for i in v["unknown_items"])
    assert not unk & set(ids(v["shots"]) + ids(v["history"]))
    w = svc.view(cls="Wizard")
    assert w["shots"] == [] and w["history"] == [] and w["classes"]["Wizard"]["current"] is None


def test_history_newest_first_and_shots_capped(env, monkeypatch):
    svc, docs, st, clk, _ = env
    monkeypatch.setattr(portraits, "SHOTS_PER_CLASS", 2)
    st["windows"] = [{"start": iso(T0 - 5000), "end": None}]
    st["loads"] = [{"char_no": CHAR_A, "at": iso(T0 - 4000)}]
    face(docs, CHAR_A, bmp(4, 4), T0 - 3000)
    svc.poll("logged_in", T0)
    face(docs, CHAR_A, bmp(4, 4, pixel=lambda x, y: (9, 9, 9)), T0 - 2000)
    for i in range(3):
        shot(docs, st, f"s{i}.jpg", T0 - 1000 + i)
    svc.poll("logged_in", T0)
    v = svc.view(cls="deadeye")  # class name is matched case-insensitively
    assert v["cls"] == "Deadeye"
    assert [h["at"] for h in v["history"]] == [iso(T0 - 2000), iso(T0 - 3000)]
    assert [s["at"] for s in v["shots"]] == [iso(T0 - 998), iso(T0 - 999)]


def test_no_cls_keeps_082_shape(env):
    svc = _two_chars(env)
    v = svc.view()
    assert "history" not in v and "shots" not in v and isinstance(v["unknown"], int)


def test_typed_bind_moves_unknown_into_a_class(env):
    svc = _two_chars(env)
    rows = {r["file"]: r for r in svc.store.get("portraits")["shots"]}
    v = svc.bind(rows["after.jpg"]["id"], "Deadeye")  # no char_no: the shot carries the class
    assert rows["after.jpg"]["id"] in ids(v["shots"])
    v = svc.bind(rows["other.jpg"]["id"], "Wizard")  # char_no: binds CHAR_B, typed
    ch = svc.store.get("portraits")["characters"][CHAR_B]
    assert ch["cls"] == "Wizard" and ch["cls_src"] == "typed"
    assert [h["char_no"] for h in v["history"]] == [CHAR_B]
    with pytest.raises(ValueError):
        svc.bind("s0000000000000000", "Deadeye")
    with pytest.raises(ValueError):
        svc.bind(rows["other.jpg"]["id"], "NotAClass")


# -- pick (plan 079 override) ----------------------------------------------------

def test_pick_overrides_then_newer_portrait_retires_it(env):
    svc = _two_chars(env)
    _, docs, st, clk, led = env
    old = svc.current("Deadeye")
    sid = svc.view(cls="Deadeye")["shots"][0]["id"]
    clk.t = T0 + 100
    v = svc.pick("Deadeye", sid)
    cur = v["classes"]["Deadeye"]["current"]
    assert cur["id"] == sid and cur["from"] == "override" and cur["kind"] == "shot"
    assert cur["entry"]["key"] == "portrait.Deadeye" and cur["entry"]["expires_in_s"] is None
    live = led.doc()["live"]["portrait.Deadeye"]
    assert live["source"] == "typed" and live["value"] == sid and live["expires_at"] is None
    clk.t = T0 + 10 * 86400 * 365  # no time expiry (rule none)
    assert svc.view()["classes"]["Deadeye"]["current"]["from"] == "override"
    face(docs, CHAR_A, bmp(4, 4, pixel=lambda x, y: (200, 1, 1)), clk.t - 10)
    svc.poll("logged_in", clk.t)
    cur = svc.view()["classes"]["Deadeye"]["current"]
    assert cur["from"] == "auto" and cur["id"] != old["id"] and cur["kind"] == "portrait"
    assert "portrait.Deadeye" not in led.doc()["live"]
    assert led.history()[0]["retired_by"] == "portrait"


def test_pick_of_an_older_portrait_is_kept_until_a_newer_one(env):
    svc, docs, st, clk, led = env
    st["loads"] = [{"char_no": CHAR_A, "at": iso(T0 - 9000)}]
    face(docs, CHAR_A, bmp(4, 4), T0 - 3000)
    svc.poll("logged_in", T0)
    face(docs, CHAR_A, bmp(4, 4, pixel=lambda x, y: (9, 9, 9)), T0 - 2000)
    svc.poll("logged_in", T0)
    older = svc.view(cls="Deadeye")["history"][1]["id"]
    v = svc.pick("Deadeye", older)
    assert v["classes"]["Deadeye"]["current"]["id"] == older
    assert svc.view()["classes"]["Deadeye"]["current"]["id"] == older  # sticky


def test_clear_returns_to_auto(env):
    svc = _two_chars(env)
    _, _, _, clk, led = env
    newest = svc.current("Deadeye")["id"]
    sid = svc.view(cls="Deadeye")["shots"][0]["id"]
    clk.t = T0 + 100
    svc.pick("Deadeye", sid)
    v = svc.use_newest("portrait.Deadeye")
    cur = v["classes"]["Deadeye"]["current"]
    assert cur["id"] == newest and cur["from"] == "auto"
    assert led.history()[0]["retired_by"] == "operator"
    with pytest.raises(ValueError):
        svc.use_newest("market.vp")


def test_pinned_id_missing_retires(env, monkeypatch):
    svc = _two_chars(env)
    _, docs, st, clk, led = env
    sid = svc.view(cls="Deadeye")["shots"][0]["id"]
    clk.t = T0 + 100
    svc.pick("Deadeye", sid)
    monkeypatch.setattr(portraits, "SHOT_CAP", 1)  # push the pinned shot out of the index
    st["windows"] = [{"start": iso(T0 - 1000), "end": None}]
    shot(docs, st, "late.jpg", T0 + 50)
    svc.poll("logged_in", T0 + 200)
    assert sid not in {r["id"] for r in svc.store.get("portraits")["shots"]}
    cur = svc.view()["classes"]["Deadeye"]["current"]
    assert cur["from"] == "auto"
    assert led.history()[0]["retired_by"] == "missing"


def test_pick_rejects_other_class_and_unknown_items(env):
    svc = _two_chars(env)
    v = svc.view(cls="Deadeye")
    for item in v["unknown_items"]:
        with pytest.raises(ValueError):
            svc.pick("Deadeye", item["id"])
    with pytest.raises(ValueError):
        svc.pick("Nope", v["history"][0]["id"])


# -- image -----------------------------------------------------------------------

def test_blob_serves_indexed_shot_original_only(env):
    svc = _two_chars(env)
    _, docs, _, _, _ = env
    sid = svc.view(cls="Deadeye")["shots"][0]["id"]
    data, mime = svc.blob(sid, "s")
    assert data == (docs / "ScreenShot" / "dead.jpg").read_bytes() and mime == "image/jpeg"
    for bad in ["s" + "0" * 16, "../dead.jpg", "dead.jpg", "S" + sid[1:], None]:
        assert svc.blob(bad) is None
    pid = svc.current("Deadeye")["id"]
    assert svc.blob(pid, "s")[1] == "image/png"
