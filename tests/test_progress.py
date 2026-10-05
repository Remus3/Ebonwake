"""Plan 004 slice A: progress store, step toggles, pct math, profile client
(cache/backoff/pending/none), /api/progress routes and POST guards.

No network: every ProfileClient gets an injected fake fetch; the family name is
a fake, never one from config/local.json.
"""

import http.client
import json
import threading

import pytest

from server.ew import app as ewapp
from server.ew import httpcache, market, progress
from server.ew.store import Store

FAMILY = "Testfam"
HIT = [{"familyName": "Testfam", "profileTarget": "OPAQUE", "region": "NA",
        "guild": {"name": "Guildy"},
        "characters": [{"name": "Shooty", "class": "Deadeye", "main": True, "level": 62},
                       {"name": "Alt", "class": "Warrior", "main": False}]}]


class Clock:
    def __init__(self, t=2_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class FakeFetch:
    def __init__(self, body):
        self.body = body
        self.calls = []

    def __call__(self, url, timeout):
        self.calls.append(url)
        if isinstance(self.body, Exception):
            raise self.body
        return self.body if isinstance(self.body, bytes) else json.dumps(self.body).encode()


def _pc(tmp_path, body=HIT, clock=None, family=FAMILY, base_url=None):
    f = FakeFetch(body)
    c = progress.ProfileClient(family, base_url=base_url, fetch=f, clock=clock or Clock(),
                               cache_dir=tmp_path / "cache")
    return c, f


def _svc(tmp_path, client=None):
    return progress.ProgressService(Store(tmp_path / "store"), client)


# --- httpcache (shared with plan 002) -------------------------------------

def test_market_client_uses_shared_cache():
    assert issubclass(market.ArshaClient, httpcache.CachedClient)
    assert market.UpstreamError is httpcache.UpstreamError


def test_cached_client_ttl_and_backoff(tmp_path):
    clk = Clock()
    c = httpcache.CachedClient(clock=clk, cache_dir=tmp_path)
    calls = []

    def ok():
        calls.append(1)
        return {"v": 1}

    def bad():
        calls.append(1)
        raise httpcache.UpstreamError("x")

    assert c.cached_get("k", 10, ok)["data"] == {"v": 1}
    c.cached_get("k", 10, ok)
    assert len(calls) == 1
    clk.t += 11
    r = c.cached_get("k", 10, bad)
    assert r["stale"] is True and r["data"] == {"v": 1} and r["error"] == "x"
    c.cached_get("k", 10, bad)
    assert len(calls) == 2  # inside backoff
    assert c.key_backoff("k")["until"] - clk.t == 30


def test_pending_is_transient_flat_retry(tmp_path):
    clk = Clock()
    c = httpcache.CachedClient(clock=clk, cache_dir=tmp_path)

    def pend():
        raise httpcache.Pending("being fetched, retry later")

    for _ in range(4):
        r = c.cached_get("k", 10, pend)
        assert r["data"] is None and "retry later" in r["error"]
        bo = c.key_backoff("k")
        assert bo["until"] - clk.t == httpcache.PENDING_RETRY_S and bo["n"] == 0
        clk.t += httpcache.PENDING_RETRY_S


def test_peek_never_fetches(tmp_path):
    clk = Clock()
    c = httpcache.CachedClient(clock=clk, cache_dir=tmp_path)
    assert c.peek("k", 10) is None
    c.cached_get("k", 10, lambda: [1])
    assert c.peek("k", 10)["stale"] is False
    clk.t += 11
    assert c.peek("k", 10)["stale"] is True


# --- profile client --------------------------------------------------------

def test_profile_url_shape_read_only_na(tmp_path):
    c, f = _pc(tmp_path)
    c.get()
    url = f.calls[0]
    assert url.startswith(progress.DEFAULT_BASE + "/adventurer/search?")
    assert "query=Testfam" in url and "searchType=familyName" in url and "region=NA" in url


def test_profile_normalised_and_no_opaque_target(tmp_path):
    c, _ = _pc(tmp_path)
    r = c.get()
    d = r["data"]
    assert d["family"] == "Testfam" and d["guild"] == "Guildy" and d["region"] == "NA"
    assert d["characters"][0] == {"name": "Shooty", "cls": "Deadeye", "level": 62,
                                  "main": True}
    assert d["characters"][1]["level"] is None
    assert "OPAQUE" not in json.dumps(r) and r["ttl_s"] == 3600


def test_profile_picks_exact_family(tmp_path):
    other = dict(HIT[0], familyName="Testfamily", guild=None)
    c, _ = _pc(tmp_path, [other, HIT[0]])
    assert c.get()["data"]["family"] == "Testfam"


def test_profile_ttl_hourly(tmp_path):
    clk = Clock()
    c, f = _pc(tmp_path, clock=clk)
    c.get()
    clk.t += 3599
    c.get()
    assert len(f.calls) == 1
    clk.t += 2
    c.get()
    assert len(f.calls) == 2


@pytest.mark.parametrize("bad", [OSError("boom"), b"not json", [], {"code": 500}])
def test_profile_failures_back_off_and_serve_stale(tmp_path, bad):
    clk = Clock()
    c, f = _pc(tmp_path, clock=clk)
    c.get()
    clk.t += 3601
    f.body = bad
    r = c.get()
    assert r["stale"] is True and r["error"] and r["data"]["family"] == "Testfam"
    c.get()
    assert len(f.calls) == 2


def test_profile_pending_message_body_is_transient(tmp_path):
    c, f = _pc(tmp_path, {"message": "Data is being fetched, try again later"})
    r = c.get()
    assert r["data"] is None and "retry later" in r["error"]
    assert c.key_backoff(c.key)["n"] == 0


def test_profile_cache_key_hides_family(tmp_path):
    c, _ = _pc(tmp_path)
    c.get()
    names = " ".join(p.name for p in (tmp_path / "cache").iterdir())
    assert FAMILY.lower() not in names.lower()


@pytest.mark.parametrize("cfg,want", [
    ({}, None), (None, None), ({"family": ""}, None), ({"family": "a b"}, None),
    ({"family": "x" * 40}, None), ({"family": 5}, None),
    ({"family": "Testfam"}, "Testfam"),
])
def test_profile_family_from_config(cfg, want):
    assert progress.family_from_config(cfg) == want


def test_profile_base_url_must_be_https(tmp_path):
    c, f = _pc(tmp_path, base_url="http://evil.example.com")
    c.get()
    assert f.calls[0].startswith(progress.DEFAULT_BASE)
    c2, f2 = _pc(tmp_path / "other", base_url="https://mirror.example.com/v1/")
    c2.get()
    assert f2.calls[0].startswith("https://mirror.example.com/v1/adventurer/search?")


# --- progress service ------------------------------------------------------

def test_seed_tracks_and_character(tmp_path):
    v = _svc(tmp_path).view()
    kinds = [t["kind"] for t in v["tracks"]]
    assert kinds == ["quest", "season", "gear"]
    assert v["character"]["cls"] == "Deadeye"
    for t in v["tracks"]:
        assert t["total"] == len(t["steps"]) > 0 and t["done"] == 0 and t["pct"] == 0
    gear = v["tracks"][2]
    assert gear["total"] == 13


def test_seed_once(tmp_path):
    s = _svc(tmp_path)
    tid = s.view()["tracks"][0]["id"]
    s.remove_track(tid)
    assert len(_svc(tmp_path).view()["tracks"]) == 2


def test_profile_none_when_unconfigured(tmp_path):
    s = _svc(tmp_path)
    assert s.view()["profile"] == {"data": None, "freshness": None, "status": "none"}
    assert s.source() == {"updated": None, "ttl_s": 3600, "status": "none"}


def test_profile_in_view_and_source(tmp_path):
    clk = Clock()
    c, f = _pc(tmp_path, clock=clk)
    s = _svc(tmp_path, c)
    assert s.source()["status"] == "none" and f.calls == []  # state never fetches
    p = s.view()["profile"]
    assert p["status"] == "ok" and p["data"]["family"] == "Testfam"
    assert set(p["freshness"]) == {"fetched_at", "age_s", "ttl_s", "stale", "error"}
    assert s.source()["status"] == "ok" and s.source()["updated"]
    clk.t += 3601
    f.body = OSError("down")
    assert s.view()["profile"]["status"] == "stale"
    assert s.source()["status"] == "stale"


def test_profile_error_status_without_cache(tmp_path):
    c, _ = _pc(tmp_path, OSError("down"))
    s = _svc(tmp_path, c)
    assert s.view()["profile"]["status"] == "error"
    assert s.source()["status"] == "error"


def _step(s, track, step, done):
    return s.step({"track": track, "step": step, "done": done})


def test_step_toggle_and_pct(tmp_path):
    clk = Clock()
    s = progress.ProgressService(Store(tmp_path / "store"), None, clock=clk)
    t = s.view()["tracks"][2]
    ids = [st["id"] for st in t["steps"]]
    v = _step(s, t["id"], ids[0], True)
    t2 = v["tracks"][2]
    assert t2["done"] == 1 and t2["pct"] == 7
    assert t2["steps"][0]["done_at"].endswith("+00:00") and t2["steps"][0]["done"] is True
    assert t2["steps"][1]["done_at"] is None and t2["steps"][1]["done"] is False
    first = t2["steps"][0]["done_at"]
    clk.t += 100
    v = _step(s, t["id"], ids[0], True)  # idempotent: keeps the first stamp
    assert v["tracks"][2]["steps"][0]["done_at"] == first
    for sid in ids:
        v = _step(s, t["id"], sid, True)
    assert v["tracks"][2]["pct"] == 100
    v = _step(s, t["id"], ids[0], False)
    assert v["tracks"][2]["done"] == 12 and v["tracks"][2]["steps"][0]["done_at"] is None


@pytest.mark.parametrize("done,total,want", [(0, 0, 0), (0, 3, 0), (1, 3, 33), (2, 3, 66),
                                             (3, 3, 100), (1, 8, 12), (1, 200, 0),
                                             (199, 200, 99), (5, 3, 100)])
def test_pct_math(done, total, want):
    assert progress.pct(done, total) == want


@pytest.mark.parametrize("arg", [None, [], {"track": "x"}, {"track": "x", "step": "y"},
                                 {"track": "nope", "step": "y", "done": True},
                                 {"track": "main-story", "step": "nope", "done": True},
                                 {"track": "main-story", "step": "balenos", "done": 1},
                                 {"track": "main-story", "step": "balenos", "done": True,
                                  "x": 1},
                                 {"track": "BAD ID", "step": "balenos", "done": True}])
def test_step_validation(tmp_path, arg):
    s = _svc(tmp_path)
    with pytest.raises(ValueError):
        s.step(arg)


def test_character_update_partial_merge(tmp_path):
    s = _svc(tmp_path)
    v = s.set_character({"level": 61, "gs": {"ap": 300, "aap": 302, "dp": 380}})
    assert v["character"]["level"] == 61 and v["character"]["gs"]["dp"] == 380
    v = s.set_character({"name": "Shooty", "gs": {"dp": 381}})
    c = v["character"]
    assert c["name"] == "Shooty" and c["level"] == 61 and c["gs"] == {"ap": 300, "aap": 302,
                                                                       "dp": 381}
    assert c["cls"] == "Deadeye"


@pytest.mark.parametrize("ch", [
    {"level": 0}, {"level": 71}, {"level": 1.5}, {"level": True}, {"level": "60"},
    {"gs": {"ap": -1}}, {"gs": {"aap": 1000}}, {"gs": {"dp": None}}, {"gs": {"xp": 1}},
    {"gs": []}, {"name": 5}, {"name": "x" * 41}, {"name": "a\nb"}, {"cls": ""},
    {"unknown": 1}, {}, [],
])
def test_character_validation(tmp_path, ch):
    with pytest.raises(ValueError):
        _svc(tmp_path).set_character(ch)


@pytest.mark.parametrize("ch", [{"level": 1}, {"level": 70}, {"gs": {"ap": 0, "aap": 999,
                                                                      "dp": 999}},
                                {"name": ""}])
def test_character_edges_ok(tmp_path, ch):
    _svc(tmp_path).set_character(ch)


def test_add_and_remove_track(tmp_path):
    s = _svc(tmp_path)
    v = s.add_track({"title": "Garmoth heart", "kind": "gear",
                     "steps": ["Scale 1", "Scale 1", "Heart"]})
    t = v["tracks"][-1]
    assert t["id"] == "garmoth-heart" and t["total"] == 3
    assert [st["id"] for st in t["steps"]] == ["scale-1", "scale-1-2", "heart"]
    v = s.add_track({"title": "Garmoth heart", "kind": "quest", "steps": ["a"]})
    assert v["tracks"][-1]["id"] == "garmoth-heart-2"
    v = s.remove_track("garmoth-heart")
    assert [t["id"] for t in v["tracks"]][-1] == "garmoth-heart-2"
    with pytest.raises(ValueError):
        s.remove_track("garmoth-heart")


@pytest.mark.parametrize("tr", [
    None, {"title": "x", "kind": "daily", "steps": ["a"]}, {"title": "", "kind": "gear",
                                                            "steps": ["a"]},
    {"title": "x", "kind": "gear", "steps": []}, {"title": "x", "kind": "gear", "steps": "a"},
    {"title": "x", "kind": "gear", "steps": [""]}, {"title": "x", "kind": "gear", "steps": [1]},
    {"title": "x" * 81, "kind": "gear", "steps": ["a"]},
    {"title": "x", "kind": "gear", "steps": ["a"] * 61},
    {"title": "x", "kind": "gear", "steps": ["a"], "extra": 1},
])
def test_add_track_validation(tmp_path, tr):
    with pytest.raises(ValueError):
        _svc(tmp_path).add_track(tr)


def test_track_cap(tmp_path):
    s = _svc(tmp_path)
    for n in range(progress.MAX_TRACKS - 3):
        s.add_track({"title": f"t{n}", "kind": "gear", "steps": ["a"]})
    with pytest.raises(ValueError):
        s.add_track({"title": "one more", "kind": "gear", "steps": ["a"]})


def test_corrupt_doc_degrades(tmp_path):
    st = Store(tmp_path / "store")
    st.put("progress", {"character": "junk", "tracks": [
        "junk", {"id": "ok", "title": "Ok", "kind": "gear",
                 "steps": [{"id": "a", "title": "A", "done_at": "not a date"}, 5]},
        {"id": "BAD", "title": "x", "kind": "gear", "steps": []}]})
    v = progress.ProgressService(st, None).view()
    assert [t["id"] for t in v["tracks"]] == ["ok"]
    assert v["tracks"][0]["steps"] == [{"id": "a", "title": "A", "done": False,
                                        "done_at": None}]
    assert v["character"]["cls"] == "Deadeye" and v["character"]["level"] is None


# --- routes ----------------------------------------------------------------

def _no_network(url, timeout):
    raise AssertionError("test touched the network")


@pytest.fixture()
def psrv(tmp_path):
    pc = progress.ProfileClient(FAMILY, fetch=FakeFetch(HIT), clock=Clock(),
                                cache_dir=tmp_path / "pcache")
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          sse_interval=0.05, market_seed=[],
                          market_client=market.ArshaClient(fetch=_no_network,
                                                           cache_dir=tmp_path / "cache"),
                          profile_client=pc)
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield s
    s.shutdown()
    s.server_close()


def _req(s, method, path, body=None, ctype="application/json", host=None):
    c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=5)
    headers = {}
    if ctype and body is not None:
        headers["Content-Type"] = ctype
    if host:
        headers["Host"] = host
    data = None if body is None else (body if isinstance(body, bytes)
                                      else json.dumps(body).encode())
    c.request(method, path, body=data, headers=headers)
    r = c.getresponse()
    out = r.read()
    c.close()
    return r.status, (json.loads(out) if out else None)


def test_route_get_progress(psrv):
    st, doc = _req(psrv, "GET", "/api/progress")
    assert st == 200 and set(doc) == {"character", "tracks", "profile"}
    assert doc["profile"]["status"] == "ok" and doc["profile"]["data"]["family"] == "Testfam"
    assert all({"done", "total", "pct"} <= set(t) for t in doc["tracks"])


def test_route_state_sources_profile(psrv):
    st, doc = _req(psrv, "GET", "/api/state")
    assert st == 200 and doc["sources"]["profile"]["status"] == "none"
    _req(psrv, "GET", "/api/progress")
    doc = _req(psrv, "GET", "/api/state")[1]
    assert doc["sources"]["profile"]["status"] == "ok"


def test_route_no_profile_configured(tmp_path):
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", market_seed=[],
                          profile_cfg={})
    try:
        assert s.progress.view()["profile"]["status"] == "none"
    finally:
        s.server_close()


def test_route_post_ops(psrv):
    st, doc = _req(psrv, "POST", "/api/progress", {"character": {"level": 62}})
    assert st == 200 and doc["character"]["level"] == 62
    st, doc = _req(psrv, "POST", "/api/progress",
                   {"step": {"track": "main-story", "step": "balenos", "done": True}})
    assert st == 200 and doc["tracks"][0]["done"] == 1
    st, doc = _req(psrv, "POST", "/api/progress",
                   {"add_track": {"title": "New", "kind": "quest", "steps": ["a", "b"]}})
    assert st == 200 and doc["tracks"][-1]["id"] == "new"
    st, doc = _req(psrv, "POST", "/api/progress", {"remove_track": "new"})
    assert st == 200 and all(t["id"] != "new" for t in doc["tracks"])


@pytest.mark.parametrize("body", [b"not json", b"[]", {}, {"nope": 1},
                                  {"character": {"level": 1}, "remove_track": "x"},
                                  {"character": {"level": 99}}, {"remove_track": "missing"},
                                  {"step": {"track": "main-story"}}])
def test_route_post_bad_body(psrv, body):
    assert _req(psrv, "POST", "/api/progress", body)[0] == 400


def test_route_post_guards(psrv):
    ok = {"character": {"level": 2}}
    assert _req(psrv, "POST", "/api/progress", ok, host="evil.example.com")[0] == 403
    assert _req(psrv, "POST", "/api/progress", ok, ctype="text/plain")[0] == 415
    big = json.dumps({"character": {"name": "x" * 5000}}).encode()
    assert _req(psrv, "POST", "/api/progress", big)[0] == 413
    assert ewapp.Handler.POST_ROUTES["/api/progress"] is ewapp.Handler._post_progress
