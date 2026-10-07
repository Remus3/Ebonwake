"""Plan 004 slice A: progress store, step toggles, pct math, profile client
(cache/backoff/pending/none), /api/progress routes and POST guards.

No network: every ProfileClient gets an injected fake fetch; the family name is
a fake, never one from config/local.json.
"""

import http.client
import json
import re
import threading
from pathlib import Path

import pytest

from server.ew import app as ewapp
from server.ew import httpcache, market, progress
from server.ew.store import Store

FAMILY = "Testfam"
LOCAL = progress.SELF_HOST_EXAMPLE  # plan 061: loopback base, robots gate skipped
HIT =[{"familyName": "Testfam", "profileTarget": "OPAQUE", "region": "NA",
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


def _pc(tmp_path, body=HIT, clock=None, family=FAMILY, base_url=LOCAL):
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
    assert url.startswith(LOCAL + "/adventurer/search?")
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


def test_profile_base_url_https_or_loopback(tmp_path):
    # Plan 061: plain http off loopback = no base = off, zero requests.
    c, f = _pc(tmp_path, base_url="http://evil.example.com")
    c.get()
    assert f.calls == [] and c.off_reason() == "no_base"
    c2, f2 = _pc(tmp_path / "other", base_url="http://127.0.0.1:8001/v1/")
    c2.get()
    assert f2.calls[0].startswith("http://127.0.0.1:8001/v1/adventurer/search?")


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
    s = progress.ProgressService(Store(tmp_path / "store"), c, spawn=_sync)
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
    s = progress.ProgressService(Store(tmp_path / "store"), c, spawn=_sync)
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
    {"level": 0}, {"level": 76}, {"level": 1.5}, {"level": True}, {"level": "60"},
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
                                        "done_at": None, "gates": [], "ready": None}]  # 034
    assert v["character"]["cls"] == "Deadeye" and v["character"]["level"] is None


# --- routes ----------------------------------------------------------------

def _no_network(url, timeout):
    raise AssertionError("test touched the network")


@pytest.fixture()
def psrv(tmp_path):
    pc = progress.ProfileClient(FAMILY, base_url=LOCAL, fetch=FakeFetch(HIT), clock=Clock(),
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
    assert st == 200 and set(doc) == {"character", "tracks", "season", "profile",
                                    "brackets", "seeds", "lifeskill"}  # 013, 023, 034, 042
    assert doc["profile"]["status"] in ("pending", "ok")  # refresh runs off the request
    assert _wait(lambda: _req(psrv, "GET", "/api/progress")[1]["profile"]["status"] == "ok")
    doc = _req(psrv, "GET", "/api/progress")[1]
    assert doc["profile"]["data"]["family"] == "Testfam"
    assert all({"done", "total", "pct"} <= set(t) for t in doc["tracks"])


def test_route_state_sources_profile(psrv):
    st, doc = _req(psrv, "GET", "/api/state")
    assert st == 200 and doc["sources"]["profile"]["status"] == "none"
    _req(psrv, "GET", "/api/progress")
    assert _wait(lambda: _req(psrv, "GET", "/api/state")[1]["sources"]["profile"]["status"] == "ok")


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


# --- verifier fixes (plan 004 refute round) ---------------------------------

def _sync(fn):
    fn()


def _wait(pred, timeout=3.0):
    import time as _t
    end = _t.monotonic() + timeout
    while _t.monotonic() < end:
        if pred():
            return True
        _t.sleep(0.02)
    return False


class SlowFetch(FakeFetch):
    """Blocks until released, like an upstream near the 10 s timeout."""

    def __init__(self, body):
        super().__init__(body)
        self.gate = threading.Event()

    def __call__(self, url, timeout):
        self.calls.append(url)
        self.gate.wait(10)
        return json.dumps(self.body).encode()


def test_fixtures_never_read_real_config():
    import re
    from pathlib import Path
    here = Path(__file__).parent
    for name in ("test_server.py", "test_today.py", "test_market.py"):
        src = (here / name).read_text(encoding="utf-8")
        calls = re.findall(r"make_server\((.*?)\)\n", src, re.S)
        assert calls, name
        for c in calls:
            assert "profile_cfg={}" in c or "profile_client=" in c, name


def test_post_view_never_fetches(tmp_path):
    c, f = _pc(tmp_path)
    s = progress.ProgressService(Store(tmp_path / "store"), c, spawn=_sync)
    v = s.set_character({"level": 5})
    assert f.calls == [] and v["profile"]["status"] == "none"
    s.view()  # GET path: refresh (synchronous spawn here) fills the cache
    assert len(f.calls) == 1
    v = s.step({"track": "main-story", "step": "balenos", "done": True})
    assert len(f.calls) == 1 and v["profile"]["status"] == "ok"


def test_get_view_refreshes_off_request_path(tmp_path):
    f = SlowFetch(HIT)
    c = progress.ProfileClient(FAMILY, base_url=LOCAL, fetch=f, clock=Clock(), cache_dir=tmp_path / "cache")
    s = _svc(tmp_path, c)
    import time as _t
    t0 = _t.monotonic()
    v1 = s.view()
    v2 = s.view()
    assert _t.monotonic() - t0 < 1.0, "GET blocked on upstream"
    assert v1["profile"]["status"] == "pending" and v1["profile"]["data"] is None
    assert v2["profile"]["status"] == "pending"
    assert _wait(lambda: len(f.calls) == 1)
    f.gate.set()
    assert _wait(lambda: s.view()["profile"]["status"] == "ok")
    assert len(f.calls) == 1, "single in-flight refresh per key"


def test_pending_status_when_upstream_says_being_fetched(tmp_path):
    c, f = _pc(tmp_path, httpcache.Pending("being fetched, retry later"))
    s = progress.ProgressService(Store(tmp_path / "store"), c, spawn=_sync)
    p = s.view()["profile"]
    assert p["status"] == "pending" and p["data"] is None
    assert s.source()["status"] == "pending"
    f.body = OSError("down")
    c.clock.t += httpcache.PENDING_RETRY_S + 1
    assert s.view()["profile"]["status"] == "error"


def test_route_post_does_not_wait_on_slow_upstream(tmp_path):
    f = SlowFetch(HIT)
    pc = progress.ProfileClient(FAMILY, base_url=LOCAL, fetch=f, clock=Clock(), cache_dir=tmp_path / "pcache")
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          sse_interval=0.05, market_seed=[],
                          market_client=market.ArshaClient(fetch=_no_network,
                                                           cache_dir=tmp_path / "cache"),
                          profile_client=pc)
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    try:
        import time as _t
        t0 = _t.monotonic()
        st, doc = _req(s, "GET", "/api/progress")
        assert st == 200 and doc["profile"]["status"] == "pending"
        st, doc = _req(s, "POST", "/api/progress", {"character": {"level": 9}})
        assert st == 200 and doc["character"]["level"] == 9
        assert _t.monotonic() - t0 < 2.0
        f.gate.set()
        assert _wait(lambda: _req(s, "GET", "/api/progress")[1]["profile"]["status"] == "ok")
        assert len(f.calls) == 1
    finally:
        f.gate.set()
        s.shutdown()
        s.server_close()


def test_get_view_survives_thread_start_failure(tmp_path):
    c, f = _pc(tmp_path)

    def boom(fn):
        raise RuntimeError("can't start new thread")

    s = progress.ProgressService(Store(tmp_path / "store"), c, spawn=boom)
    v = s.view()
    assert v["profile"]["status"] in ("none", "pending", "error")
    s2 = progress.ProgressService(Store(tmp_path / "store"), c, spawn=_sync)
    s2.view()
    assert len(f.calls) == 1  # in-flight slot was released after the failed start


# --- plan 034: track seeds (gear roadmap, graduation readiness, adventure logs)

SEED_IDS = ["gear_roadmap", "graduation_readiness", "fughar_journal", "igor_bartali",
            "emma_bartali"]
AP_TABLE = {"ap": {"source": "https://example.test/ap", "verified": "2026-08-13", "note": "",
                   "rows": [{"min": 0, "value": 0}, {"min": 240, "value": 40},
                            {"min": 250, "value": 57}, {"min": 340, "value": 250}]}}


def _seed_svc(tmp_path, tables=None, level=None):
    return progress.ProgressService(Store(tmp_path / "store"), bracket_tables=tables or {},
                                    level=level)


def _track(view, seed_id):
    return next(t for t in view["tracks"] if t.get("seed_id") == seed_id)


def _stepof(track, sid):
    return next(st for st in track["steps"] if st["id"] == sid)


def _gates(step):
    return {g["stat"]: g for g in step["gates"]}


def _seed_doc(**over):
    step = {"id": "a", "title": "a", "note": "", "source": "https://a.b", "verified": False}
    step.update(over.pop("step", {}))
    doc = {"id": "ok", "title": "t", "kind": "quest", "note": "", "source": "https://a.b",
           "verified": False, "steps": [step]}
    doc.update(over)
    return doc


def test_seed_files_present_and_ordered():
    seeds, errors = progress.load_seeds()
    assert errors == []
    assert [s["id"] for s in seeds] == SEED_IDS
    assert sorted(p.stem for p in progress.SEED_DIR.glob("*.json")) == sorted(SEED_IDS)


@pytest.mark.parametrize("seed_id", SEED_IDS)
def test_seed_file_schema(seed_id):
    raw = (progress.SEED_DIR / f"{seed_id}.json").read_bytes()
    assert all(b < 128 for b in raw) and b"\r" not in raw  # ASCII + LF
    doc = json.loads(raw)
    assert doc["id"] == seed_id and doc["kind"] in ("quest", "gear")
    assert progress.validate_seed(doc)["id"] == seed_id
    ids = [s["id"] for s in doc["steps"]]
    assert ids and len(ids) == len(set(ids))
    for st in doc["steps"]:  # every row sourced; verified is a date or false
        assert {"id", "title", "note", "source", "verified"} <= set(st)
        assert st["source"].startswith("https://"), st["id"]
        assert st["verified"] is False or re.match(r"^\d{4}-\d{2}-\d{2}$", st["verified"])


def test_research_facts_carried():
    by = {s["id"]: s for s in progress.load_seeds()[0]}
    fj = {st["id"]: st for st in by["fughar_journal"]["steps"]}
    assert fj["task-kratuga"]["ap"] == 250 and fj["task-kratuga"]["dp"] == 310
    assert sum(re.match(r"^b\d-c\d$", k) is not None for k in fj) == 15  # 3 books x 5
    gr = {st["id"]: st for st in by["gear_roadmap"]["steps"]}
    assert gr["olvia-academy"]["min_level"] == 60 and gr["hammer-challenge"]["ap"] == 340
    assert list(gr)[:3] == ["tuvala-pen", "graduate", "olvia-academy"]
    assert any(st["verified"] is False for st in by["igor_bartali"]["steps"])


@pytest.mark.parametrize("bad", [
    {"id": "x"},
    _seed_doc(id="Bad-id"),
    _seed_doc(kind="season"),
    _seed_doc(steps=[]),
    _seed_doc(step={"source": "http://a.b"}),
    _seed_doc(step={"verified": "2026-13-01"}),
    _seed_doc(step={"verified": True}),
    _seed_doc(step={"title": "a" + chr(233)}),
    _seed_doc(step={"title": ""}),
    _seed_doc(step={"ap": 1000}),
    _seed_doc(step={"min_level": 0}),
    _seed_doc(step={"dp": True}),
    _seed_doc(step={"extra": 1}),
    _seed_doc(step={"id": "A_b"}),
    _seed_doc(extra=1),
    dict(_seed_doc(), steps=_seed_doc()["steps"] * 2),
])
def test_validate_seed_rejects(bad):
    with pytest.raises(ValueError):
        progress.validate_seed(bad)


def test_broken_seed_file_skipped(tmp_path):
    good = json.loads((progress.SEED_DIR / "igor_bartali.json").read_text(encoding="ascii"))
    (tmp_path / "igor_bartali.json").write_text(json.dumps(good), encoding="ascii")
    (tmp_path / "broken.json").write_text("{nope", encoding="ascii")
    (tmp_path / "mismatch.json").write_text(json.dumps(dict(good, id="not_the_stem")),
                                            encoding="ascii")
    seeds, errors = progress.load_seeds(tmp_path)
    assert [s["id"] for s in seeds] == ["igor_bartali"] and len(errors) == 2


def test_get_lists_seeds_none_added(tmp_path):
    v = _seed_svc(tmp_path).view()
    assert [s["id"] for s in v["seeds"]] == SEED_IDS
    for s in v["seeds"]:
        assert s["added"] is False and s["track"] is None and s["steps"] > 0
        assert {"title", "kind", "unverified", "source", "verified"} <= set(s)
    assert next(s for s in v["seeds"] if s["id"] == "igor_bartali")["unverified"] == 2
    assert all(t.get("seed_id") is None for t in v["tracks"])


def test_seed_adds_custom_track_with_steps_copied(tmp_path):
    s = _seed_svc(tmp_path)
    n0 = len(s.view()["tracks"])
    v = s.track_seed("gear_roadmap")
    assert len(v["tracks"]) == n0 + 1
    t = _track(v, "gear_roadmap")
    assert t["kind"] == "gear" and t["title"] == "Post-graduation gear roadmap"
    assert t["id"] == "post-graduation-gear-roadmap"
    assert t["steps"][0]["id"] == "tuvala-pen" and t["done"] == 0
    olvia = _stepof(t, "olvia-academy")
    assert olvia["min_level"] == 60 and olvia["verified"] is False  # official dates unread
    assert olvia["source"].startswith("https://") and olvia["note"]
    seed = next(x for x in v["seeds"] if x["id"] == "gear_roadmap")
    assert seed["added"] is True and seed["track"] == t["id"]
    # an ordinary plan 004 track: steps toggle and survive a restart
    s.step({"track": t["id"], "step": "tuvala-pen", "done": True})
    t2 = _track(_seed_svc(tmp_path).view(), "gear_roadmap")
    assert t2["done"] == 1 and _stepof(t2, "olvia-academy")["note"] == olvia["note"]


def test_seed_twice_is_noop(tmp_path):
    s = _seed_svc(tmp_path)
    tid = _track(s.track_seed("fughar_journal"), "fughar_journal")["id"]
    s.step({"track": tid, "step": "b1-c1", "done": True})
    before = s.store.get("progress")["tracks"]
    v = s.track_seed("fughar_journal")
    assert s.store.get("progress")["tracks"] == before
    assert sum(t.get("seed_id") == "fughar_journal" for t in v["tracks"]) == 1
    assert _track(v, "fughar_journal")["done"] == 1


def test_seed_after_remove_re_adds(tmp_path):
    s = _seed_svc(tmp_path)
    tid = _track(s.track_seed("igor_bartali"), "igor_bartali")["id"]
    s.remove_track(tid)
    assert next(x for x in s.view()["seeds"] if x["id"] == "igor_bartali")["added"] is False
    assert _track(s.track_seed("igor_bartali"), "igor_bartali")["done"] == 0


def test_seed_title_clash_gets_unique_id(tmp_path):
    s = _seed_svc(tmp_path)
    s.add_track({"title": "Igor Bartali adventure log", "kind": "quest", "steps": ["mine"]})
    assert _track(s.track_seed("igor_bartali"), "igor_bartali")["id"] == \
        "igor-bartali-adventure-log-2"


def test_seed_all_five_on_fresh_store(tmp_path):
    s = _seed_svc(tmp_path)
    for sid in SEED_IDS:
        v = s.track_seed(sid)
    assert all(x["added"] for x in v["seeds"])
    assert len(v["tracks"]) == 3 + 5


@pytest.mark.parametrize("arg", ["nope", "", 5, None, {"id": "gear_roadmap"}, "GEAR_ROADMAP",
                                 "../gear_roadmap"])
def test_seed_validation(tmp_path, arg):
    with pytest.raises(ValueError):
        _seed_svc(tmp_path).track_seed(arg)


def test_seed_respects_track_cap(tmp_path):
    s = _seed_svc(tmp_path)
    for n in range(progress.MAX_TRACKS - 3):
        s.add_track({"title": f"T{n}", "kind": "quest", "steps": ["a"]})
    with pytest.raises(ValueError):
        s.track_seed("gear_roadmap")


def test_gates_unknown_without_character(tmp_path):
    t = _track(_seed_svc(tmp_path).track_seed("fughar_journal"), "fughar_journal")
    k = _stepof(t, "task-kratuga")
    g = _gates(k)
    assert set(g) == {"level", "ap", "dp"}
    assert all(x["state"] == "unknown" and x["have"] is None and x["gap"] is None
               for x in g.values())
    assert k["ready"] is None
    assert set(_gates(_stepof(t, "b1-c1"))) == {"level"}


def test_gates_ready_and_needs(tmp_path):
    s = _seed_svc(tmp_path, tables=AP_TABLE)
    s.track_seed("fughar_journal")
    v = s.set_character({"level": 56, "gs": {"ap": 245, "dp": 320}})
    k = _stepof(_track(v, "fughar_journal"), "task-kratuga")
    g = _gates(k)
    assert g["level"] == {"stat": "level", "need": 55, "have": 56, "gap": 0, "state": "ready",
                          "label": "ready"}
    assert g["dp"]["state"] == "ready" and g["dp"]["label"] == "ready"
    assert g["ap"]["state"] == "needs" and g["ap"]["gap"] == 5
    assert g["ap"]["label"] == "needs +5 AP"
    assert g["ap"]["bonus_gain"] == 17  # plan 023: bracket 240 (+40) -> 250 (+57)
    assert k["ready"] is False
    v = s.set_character({"gs": {"ap": 250}})
    k = _stepof(_track(v, "fughar_journal"), "task-kratuga")
    assert k["ready"] is True and _gates(k)["ap"]["label"] == "ready"


def test_gate_level_uses_sample_level(tmp_path):
    t = _track(_seed_svc(tmp_path, level=lambda: 60).track_seed("gear_roadmap"), "gear_roadmap")
    assert _gates(_stepof(t, "olvia-academy"))["level"]["state"] == "ready"
    assert t["steps"][0]["gates"] == [] and t["steps"][0]["ready"] is None


def test_gate_needs_level_label(tmp_path):
    s = _seed_svc(tmp_path)
    s.track_seed("gear_roadmap")
    v = s.set_character({"level": 57})
    lv = _gates(_stepof(_track(v, "gear_roadmap"), "olvia-academy"))["level"]
    assert lv["label"] == "needs +3 lv" and "bonus_gain" not in lv


def test_gate_dp_needs_label(tmp_path):
    s = _seed_svc(tmp_path)
    s.track_seed("fughar_journal")
    v = s.set_character({"gs": {"dp": 300}})
    assert _gates(_stepof(_track(v, "fughar_journal"), "task-kratuga"))["dp"]["label"] == \
        "needs +10 DP"


def test_bonus_gain_none_without_ap_table(tmp_path):
    s = _seed_svc(tmp_path)
    s.track_seed("gear_roadmap")
    v = s.set_character({"gs": {"ap": 300}})
    ap = _gates(_stepof(_track(v, "gear_roadmap"), "hammer-challenge"))["ap"]
    assert ap["gap"] == 40 and ap["bonus_gain"] is None


def test_custom_track_steps_have_no_gates(tmp_path):
    gear = next(t for t in _seed_svc(tmp_path).view()["tracks"] if t["kind"] == "gear")
    assert all(st["gates"] == [] and st["ready"] is None for st in gear["steps"])
    assert all("verified" not in st for st in gear["steps"])
    assert gear["seed_id"] is None


def test_corrupt_seed_meta_degrades(tmp_path):
    s = _seed_svc(tmp_path)
    tid = _track(s.track_seed("fughar_journal"), "fughar_journal")["id"]
    doc = s.store.get("progress")
    t = next(t for t in doc["tracks"] if t["id"] == tid)
    t["steps"][0].update(min_level=999, ap="x", note=5, verified="never", source="ftp://x")
    t["seed_id"] = "Bad Id"
    s.store.put("progress", doc)
    t = next(t for t in _seed_svc(tmp_path).view()["tracks"] if t["id"] == tid)
    st = t["steps"][0]
    assert st["gates"] == [] and "note" not in st and "verified" not in st
    assert "source" not in st and t["seed_id"] is None


def test_seed_error_does_not_break_tab(tmp_path, monkeypatch):
    monkeypatch.setattr(progress, "SEED_DIR", tmp_path / "missing")
    s = _seed_svc(tmp_path)
    assert s.view()["seeds"] == []
    with pytest.raises(ValueError):
        s.track_seed("gear_roadmap")


def test_route_post_track_seed(psrv):
    st, doc = _req(psrv, "POST", "/api/progress", {"track_seed": "graduation_readiness"})
    assert st == 200 and _track(doc, "graduation_readiness")["total"] == 6
    st, doc2 = _req(psrv, "POST", "/api/progress", {"track_seed": "graduation_readiness"})
    assert st == 200 and len(doc2["tracks"]) == len(doc["tracks"])
    assert _req(psrv, "POST", "/api/progress", {"track_seed": "nope"})[0] == 400
    doc = _req(psrv, "GET", "/api/progress")[1]
    assert next(x for x in doc["seeds"] if x["id"] == "graduation_readiness")["added"] is True


# --- plan 041: profile history snapshots + auto level marker -----------------

PROFILE_FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "profile"
                              / "adventurer_search.json").read_text(encoding="utf-8"))


def _hist(tmp_path, clk):
    return progress.ProfileHistory(tmp_path / "rt" / "profile_history.jsonl", clock=clk)


def _hsvc(tmp_path, body=None, clk=None, auto_level=True):
    clk = clk or Clock()
    c, f = _pc(tmp_path, PROFILE_FIXTURE if body is None else body, clock=clk)
    marks = []
    s = progress.ProgressService(Store(tmp_path / "store"), c, clock=clk, spawn=_sync,
                                 history=_hist(tmp_path, clk), on_level=marks.append,
                                 auto_level=auto_level)
    return s, c, f, clk, marks


def _bump(f, level):
    f.body = json.loads(json.dumps(PROFILE_FIXTURE))
    f.body[0]["characters"][0]["level"] = level


def test_snapshots_from_fixture_privacy_gaps_absent():
    snaps = progress.profile_snapshots(PROFILE_FIXTURE[0])
    main, alt = snaps
    assert main == {"name": "Shooty", "main": True, "level": 62, "gs": 640, "energy": 401,
                    "contribution": 312, "combat_fame": 1820, "life_fame": 2210,
                    "spec_levels": {"gathering": "Artisan 2", "fishing": "Skilled 9",
                                    "trading": "Beginner 1"}}
    # hidden level and no gs / spec levels: absent, never zero
    assert "level" not in alt and "gs" not in alt and "spec_levels" not in alt
    assert alt["energy"] == 401 and alt["main"] is False
    assert "OPAQUE" not in json.dumps(snaps) and "mastery" not in json.dumps(snaps)


@pytest.mark.parametrize("bad", [None, True, -1, "lots", 1.5, 10 ** 12])
def test_snapshot_bad_values_absent(bad):
    hit = dict(PROFILE_FIXTURE[0], energy=bad, contributionPoints=bad,
               characters=[{"name": "Shooty", "class": "Deadeye", "main": True, "level": bad,
                            "gs": bad, "specLevels": bad}])
    (s,) = progress.profile_snapshots(hit)
    assert set(s) == {"name", "main", "combat_fame", "life_fame"}


def test_snapshot_written_once_per_refresh(tmp_path):
    s, c, f, clk, _ = _hsvc(tmp_path)
    s.view()
    s.view()
    s.view(refresh=False)
    assert len(f.calls) == 1 and len(s.history.rows()) == 2  # two characters, one refresh
    clk.t += 1800
    s.view()
    assert len(f.calls) == 1 and len(s.history.rows()) == 2
    clk.t += 1801
    s.view()
    assert len(f.calls) == 2 and len(s.history.rows()) == 4
    rows = s.history.rows()
    assert rows[0]["at"] < rows[2]["at"] and {r["name"] for r in rows} == {"Shooty", "Alt"}


def test_failed_refresh_writes_no_snapshot(tmp_path):
    s, c, f, clk, _ = _hsvc(tmp_path)
    s.view()
    clk.t += 3601
    f.body = OSError("down")
    s.view()
    assert len(f.calls) == 2 and len(s.history.rows()) == 2


def test_history_rotation_keeps_90_days(tmp_path):
    clk = Clock()
    h = _hist(tmp_path, clk)
    h.append([{"name": "Shooty", "main": True, "level": 60}])
    clk.t += 89 * 86400
    h.append([{"name": "Shooty", "main": True, "level": 61}])
    assert len(h.rows()) == 2
    clk.t += 2 * 86400
    h.append([{"name": "Shooty", "main": True, "level": 62}])
    assert [r["level"] for r in h.rows()] == [61, 62]
    assert not list((tmp_path / "rt").glob("*.tmp"))


def test_history_row_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(progress, "HISTORY_MAX_ROWS", 3)
    clk = Clock()
    h = _hist(tmp_path, clk)
    for lv in range(1, 6):
        clk.t += 60
        h.append([{"name": "Shooty", "main": True, "level": lv}])
    assert [r["level"] for r in h.rows()] == [3, 4, 5]


def test_history_corrupt_lines_skipped(tmp_path):
    clk = Clock()
    h = _hist(tmp_path, clk)
    h.append([{"name": "Shooty", "main": True, "level": 60}])
    with open(h.path, "a", encoding="utf-8") as fh:
        fh.write('not json\n[1]\n{"at": "nope", "name": "x"}\n')
    assert len(h.rows()) == 1
    clk.t += 60
    h.append([{"name": "Shooty", "main": True, "level": 61}])
    assert [r["level"] for r in h.rows()] == [60, 61]


def test_series_main_by_default_with_gaps(tmp_path):
    clk = Clock()
    h = _hist(tmp_path, clk)
    h.append([{"name": "Shooty", "main": True, "level": 60, "gs": 600},
              {"name": "Alt", "main": False, "level": 20}])
    clk.t += 3600
    h.append([{"name": "Shooty", "main": True, "level": 61}])  # gs hidden this hour
    clk.t += 3600
    h.append([{"name": "Shooty", "main": True, "level": 61, "gs": 612}])
    out = h.series(["level", "gs"], 30)
    assert out["character"] == "Shooty" and out["days"] == 30
    assert [p["v"] for p in out["series"]["level"]["points"]] == [60, 61, 61]
    gs = out["series"]["gs"]
    assert [p["v"] for p in gs["points"]] == [600, 612] and gs["delta"] == 12
    alt = h.series(["level"], 30, character="Alt")
    assert [p["v"] for p in alt["series"]["level"]["points"]] == [20]
    assert h.series(["level"], 1)["series"]["level"]["points"][0]["v"] == 60
    clk.t += 2 * 86400
    assert h.series(["level"], 1)["series"]["level"] == {"points": [], "first": None,
                                                         "last": None, "delta": None}


def test_series_empty_history(tmp_path):
    out = _hist(tmp_path, Clock()).series(["energy"], 7)
    assert out["character"] is None and out["series"]["energy"]["points"] == []


def test_auto_marker_on_main_level_rise_only(tmp_path):
    s, c, f, clk, marks = _hsvc(tmp_path)
    s.view()
    assert marks == []  # first snapshot: no rise known
    clk.t += 3601
    s.view()
    assert marks == []  # same level
    clk.t += 3601
    _bump(f, 63)
    f.body[0]["characters"][1]["level"] = 90  # an alt never marks
    s.view()
    assert marks == [63]
    clk.t += 3601
    f.body[0]["characters"][0].pop("level")  # privacy hides it: no marker
    s.view()
    clk.t += 3601
    f.body[0]["characters"][0]["level"] = 63  # back, same level as the last known one
    s.view()
    assert marks == [63]


def test_auto_marker_disabled(tmp_path):
    s, c, f, clk, marks = _hsvc(tmp_path, auto_level=False)
    s.view()
    clk.t += 3601
    _bump(f, 63)
    s.view()
    assert marks == [] and len(s.history.rows()) == 4


def test_marker_callback_failure_never_breaks_refresh(tmp_path):
    clk = Clock()
    c, f = _pc(tmp_path, PROFILE_FIXTURE, clock=clk)

    def boom(level):
        raise RuntimeError("x")

    s = progress.ProgressService(Store(tmp_path / "store"), c, clock=clk, spawn=_sync,
                                 history=_hist(tmp_path, clk), on_level=boom)
    s.view()
    clk.t += 3601
    _bump(f, 63)
    assert s.view()["profile"]["status"] == "ok"
    assert s.view()["profile"]["data"]["characters"][0]["level"] == 63


def test_history_query_validation(tmp_path):
    s, *_ = _hsvc(tmp_path)
    assert s.history_view({"field": ["level,gs"]})["days"] == 30
    for bad in ({}, {"field": ["mastery"]}, {"field": ["level"], "days": ["0"]},
                {"field": ["level"], "days": ["91"]}, {"field": ["level"], "days": ["x"]},
                {"field": ["level"], "character": ["x" * 41]}):
        with pytest.raises(ValueError):
            s.history_view(bad)
    assert progress.ProgressService(Store(tmp_path / "s2"), None).history_view(
        {"field": ["level"]})["series"]["level"]["points"] == []


@pytest.fixture()
def hsrv(tmp_path):
    clk = Clock()
    pc = progress.ProfileClient(FAMILY, base_url=LOCAL, fetch=FakeFetch(PROFILE_FIXTURE), clock=clk,
                                cache_dir=tmp_path / "pcache")
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          sse_interval=0.05, market_seed=[], leveling_clock=clk,
                          market_client=market.ArshaClient(fetch=_no_network,
                                                           cache_dir=tmp_path / "cache"),
                          profile_client=pc)
    s.progress.spawn = _sync
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield s, clk, pc
    s.shutdown()
    s.server_close()


def test_route_history_and_marker_end_to_end(hsrv, tmp_path):
    s, clk, pc = hsrv
    assert s.progress.history.path == tmp_path / "profile_history.jsonl"
    _req(s, "GET", "/api/progress")
    st, doc = _req(s, "GET", "/api/progress/history?field=level,gs,energy,contribution&days=7")
    assert st == 200 and doc["character"] == "Shooty"
    assert {k: v["last"] for k, v in doc["series"].items()} == {
        "level": 62, "gs": 640, "energy": 401, "contribution": 312}
    assert _req(s, "GET", "/api/progress/history?field=bogus")[0] == 400
    assert _req(s, "GET", "/api/progress/history")[0] == 400
    clk.t += 3601
    _bump(pc.fetch, 63)
    _req(s, "GET", "/api/progress")
    assert len(pc.fetch.calls) == 2  # no request beyond the hourly cadence
    lv = _req(s, "GET", "/api/leveling")[1]
    assert lv["level"] == 63 and lv["pct"] is None and lv["level_source"] == "profile"
    assert lv["samples"][0]["source"] == "profile"


def test_route_auto_level_off_by_config(tmp_path):
    pc = progress.ProfileClient(FAMILY, base_url=LOCAL, fetch=FakeFetch(PROFILE_FIXTURE), clock=Clock(),
                                cache_dir=tmp_path / "pcache")
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", market_seed=[],
                          market_client=market.ArshaClient(fetch=_no_network,
                                                           cache_dir=tmp_path / "cache"),
                          profile_client=pc, profile_cfg={"auto_level": False})
    try:
        assert s.progress.auto_level is False
    finally:
        s.server_close()
