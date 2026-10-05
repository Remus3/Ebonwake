"""Plan 011: leveling store domain, XP rate / ETA math, Hot Time windows, XP stack.

Operator-typed data only: nothing is read from the game. Every clock is
injected so rates, countdowns and window states are exact. All times are UTC.
"""

import datetime as dt
import http.client
import json
import threading
import time

import pytest

from server.ew import app as ewapp
from server.ew import grind, leveling
from server.ew.store import Store

UTC = dt.timezone.utc
# 2026-10-05 is a Monday (weekday 0).
T0 = dt.datetime(2026, 10, 5, 12, 0, 0, tzinfo=UTC)


class Clock:
    def __init__(self, when=T0):
        self.t = when.timestamp()

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def _s(minutes, level, pct):
    return {"ts": (T0 + dt.timedelta(minutes=minutes)).isoformat(), "level": level, "pct": pct}


@pytest.fixture()
def clock():
    return Clock()


@pytest.fixture()
def store(tmp_path):
    return Store(tmp_path / "store")


@pytest.fixture()
def svc(store, clock):
    return leveling.LevelingService(store, clock=clock)


# --- rate math ---------------------------------------------------------------

def test_rate_none_with_fewer_than_two_samples():
    assert leveling.rate_pct_h([]) is None
    assert leveling.rate_pct_h([_s(0, 52, 10.0)]) is None


def test_rate_simple_one_hour():
    assert leveling.rate_pct_h([_s(0, 52, 10.0), _s(60, 52, 14.0)]) == pytest.approx(4.0)


def test_rate_level_rollover_counts_100_per_level():
    # 52 90% -> 53 10% in one hour = 20 pct/h
    assert leveling.rate_pct_h([_s(0, 52, 90.0), _s(60, 53, 10.0)]) == pytest.approx(20.0)
    # two levels: 52 90% -> 54 10% = 120 pct
    assert leveling.rate_pct_h([_s(0, 52, 90.0), _s(60, 54, 10.0)]) == pytest.approx(120.0)


def test_rate_samples_closer_than_two_minutes_are_merged():
    # The 1-minute sample cannot form its own delta: the newest pairs with the
    # latest sample at least 2 minutes older.
    s = [_s(0, 52, 10.0), _s(59, 52, 13.0), _s(60, 52, 14.0)]
    # deltas: 60 -> 0 (59 is under 2 min from 60): 4 pct in 1 h
    assert leveling.rate_pct_h(s) == pytest.approx(4.0)


def test_rate_all_within_two_minutes_is_none():
    assert leveling.rate_pct_h([_s(0, 52, 10.0), _s(1, 52, 11.0)]) is None


def test_rate_median_of_last_five_deltas():
    # deltas (oldest first): 1, 2, 3, 100, 4, 5 pct per 60 min; last five = 2,3,100,4,5
    pts = [0.0, 1.0, 3.0, 6.0, 106.0, 110.0, 115.0]
    s = []
    for i, p in enumerate(pts):
        lvl = 52 + int(p // 100)
        s.append(_s(60 * i, lvl, p - 100 * (lvl - 52)))
    assert leveling.rate_pct_h(s) == pytest.approx(4.0)


def test_rate_sparse_samples_unsorted_input():
    s = [_s(600, 52, 30.0), _s(0, 52, 10.0)]  # 10 h apart, input out of order
    assert leveling.rate_pct_h(s) == pytest.approx(2.0)


def test_rate_non_positive_is_none():
    assert leveling.rate_pct_h([_s(0, 52, 10.0), _s(60, 52, 10.0)]) is None
    assert leveling.rate_pct_h([_s(0, 52, 10.0), _s(60, 52, 5.0)]) is None


def test_eta_next():
    assert leveling.eta_next_s(60.0, 4.0) == 36000
    assert leveling.eta_next_s(60.0, None) is None
    assert leveling.eta_next_s(60.0, 0.0) is None
    assert leveling.eta_next_s(None, 4.0) is None


def test_next_milestone():
    assert leveling.next_milestone(52, [50, 56, 57]) == 56
    assert leveling.next_milestone(56, [50, 56, 57]) == 57
    assert leveling.next_milestone(61, [50, 61]) is None
    assert leveling.next_milestone(None, [50, 61]) == 50


# --- hot windows -------------------------------------------------------------

def _w(wid, days, start, end, pct=50, label="Hot Time"):
    return {"id": wid, "days": days, "start": start, "end": end, "label": label, "pct": pct}


def test_hot_active_and_ends_in():
    now = T0  # Monday 12:00 UTC
    st = leveling.hot_status([_w("h1", [0], "11:00", "13:30")], now)
    assert [a["id"] for a in st["active"]] == ["h1"]
    assert st["active"][0]["ends_in_s"] == 5400


def test_hot_next_starts_in():
    st = leveling.hot_status([_w("h1", [0], "14:00", "15:00")], T0)
    assert st["active"] == []
    assert st["next"]["id"] == "h1" and st["next"]["starts_in_s"] == 7200


def test_hot_next_rolls_to_following_week():
    # Monday-only window that already ended today: next start is next Monday.
    st = leveling.hot_status([_w("h1", [0], "09:00", "10:00")], T0)
    assert st["next"]["starts_in_s"] == 7 * 86400 - 3 * 3600


def test_hot_wrap_past_midnight_active_after_midnight():
    # Sunday (6) 22:00 -> 02:00 Monday; now = Monday 01:00 UTC
    now = dt.datetime(2026, 10, 5, 1, 0, tzinfo=UTC)
    st = leveling.hot_status([_w("h1", [6], "22:00", "02:00")], now)
    assert [a["id"] for a in st["active"]] == ["h1"] and st["active"][0]["ends_in_s"] == 3600


def test_hot_wrap_past_midnight_not_active_on_wrong_day():
    # Monday 01:00 but the window is Saturday 22:00 -> Sunday 02:00
    now = dt.datetime(2026, 10, 5, 1, 0, tzinfo=UTC)
    st = leveling.hot_status([_w("h1", [5], "22:00", "02:00")], now)
    assert st["active"] == []


def test_hot_wrap_active_before_midnight():
    now = dt.datetime(2026, 10, 5, 23, 0, tzinfo=UTC)  # Monday 23:00
    st = leveling.hot_status([_w("h1", [0], "22:00", "02:00")], now)
    assert st["active"][0]["ends_in_s"] == 3 * 3600


def test_hot_end_boundary_exclusive_start_inclusive():
    w = [_w("h1", [0], "12:00", "13:00")]
    assert leveling.hot_status(w, T0)["active"][0]["ends_in_s"] == 3600
    end = dt.datetime(2026, 10, 5, 13, 0, tzinfo=UTC)
    assert leveling.hot_status(w, end)["active"] == []


def test_hot_utc_only_for_aware_offsets():
    # Same instant expressed at +02:00 must give the same answer.
    now = T0.astimezone(dt.timezone(dt.timedelta(hours=2)))
    st = leveling.hot_status([_w("h1", [0], "11:00", "13:30")], now)
    assert st["active"][0]["ends_in_s"] == 5400


def test_hot_none_when_no_windows():
    assert leveling.hot_status([], T0) == {"active": [], "next": None}


def test_xp_stack_sums_active_hot_and_xp_buffs():
    active = [{"id": "h1", "pct": 50}, {"id": "h2", "pct": 20}]
    buffs = [{"name": "XP scroll", "left_s": 600, "xp_pct": 100},
             {"name": "Old", "left_s": None, "xp_pct": 300},       # unarmed: not counted
             {"name": "Value Pack", "left_s": 9000},                # no xp_pct: not counted
             {"name": "Null", "left_s": 9000, "xp_pct": None}]
    assert leveling.xp_stack(active, buffs) == 170


# --- service -----------------------------------------------------------------

def test_seed_milestones_and_empty(svc):
    doc = svc.view()
    assert doc["milestones"] == [50, 56, 57, 58, 60, 61] and doc["milestones_seed"] is True
    assert doc["hot_windows"] == [] and doc["samples"] == []
    assert doc["level"] is None and doc["pct"] is None and doc["rate_pct_h"] is None
    assert doc["eta_next_s"] is None and doc["hot"] == {"active": [], "next": None}
    assert doc["xp_stack_pct"] == 0 and doc["now"] == T0.isoformat()


def test_seed_not_reapplied(store, clock):
    a = leveling.LevelingService(store, clock=clock)
    a.set_milestones([55])
    b = leveling.LevelingService(store, clock=clock)
    assert b.view()["milestones"] == [55] and b.view()["milestones_seed"] is False


def test_samples_rate_eta_and_milestone(svc, clock):
    svc.sample({"level": 52, "pct": 10})
    clock.advance(3600)
    doc = svc.sample({"level": 52, "pct": 14.125})
    assert doc["level"] == 52 and doc["pct"] == 14.125
    assert doc["rate_pct_h"] == pytest.approx(4.125)
    assert doc["eta_next_s"] == int((100 - 14.125) / 4.125 * 3600)
    assert doc["next_milestone"] == 56
    assert [s["level"] for s in doc["samples"]] == [52, 52]
    assert doc["samples"][0]["ts"] == (T0 + dt.timedelta(hours=1)).isoformat()  # newest first


def test_sample_same_second_replaces(svc):
    svc.sample({"level": 52, "pct": 10})
    doc = svc.sample({"level": 52, "pct": 11})
    assert len(doc["samples"]) == 1 and doc["pct"] == 11


def test_samples_capped_newest_kept(svc, clock):
    for i in range(leveling.MAX_SAMPLES + 5):
        svc.sample({"level": 52, "pct": i % 100})
        clock.advance(60)
    doc = svc.store.get("leveling")
    assert len(doc["samples"]) == leveling.MAX_SAMPLES
    assert len(svc.view()["samples"]) == leveling.VIEW_SAMPLES


def test_sample_delete(svc, clock):
    svc.sample({"level": 52, "pct": 10})
    clock.advance(60)
    doc = svc.sample({"level": 52, "pct": 12})
    ts = doc["samples"][0]["ts"]
    doc = svc.sample_del(ts)
    assert doc["pct"] == 10 and len(doc["samples"]) == 1
    with pytest.raises(ValueError):
        svc.sample_del(ts)
    with pytest.raises(ValueError):
        svc.sample_del(5)


@pytest.mark.parametrize("arg", [
    {"level": 0, "pct": 1}, {"level": 71, "pct": 1}, {"level": 52, "pct": -0.001},
    {"level": 52, "pct": 100.001}, {"level": 52, "pct": 1.2345}, {"level": 52.0, "pct": 1},
    {"level": True, "pct": 1}, {"level": 52, "pct": True}, {"level": 52, "pct": "1"},
    {"level": 52}, {"level": 52, "pct": 1, "x": 1}, [52, 1], None,
    {"level": 52, "pct": float("nan")}, {"level": 52, "pct": float("inf")},
])
def test_sample_bad(svc, arg):
    with pytest.raises(ValueError):
        svc.sample(arg)


def test_sample_limits_inclusive(svc):
    assert svc.sample({"level": 1, "pct": 0})["pct"] == 0
    assert svc.sample({"level": 70, "pct": 100})["level"] == 70
    assert svc.sample({"level": 70, "pct": 37.512})["pct"] == 37.512


def test_hot_add_view_and_delete(svc, clock):
    doc = svc.hot_add({"days": [0, 2], "start": "11:00", "end": "13:00", "label": "Hot Time",
                       "pct": 50})
    w = doc["hot_windows"][0]
    assert w == {"id": "h1", "days": [0, 2], "start": "11:00", "end": "13:00",
                 "label": "Hot Time", "pct": 50}
    assert doc["hot"]["active"][0]["ends_in_s"] == 3600 and doc["xp_stack_pct"] == 50
    doc = svc.hot_add({"days": [6, 5], "start": "22:00", "end": "02:00", "label": "Late",
                       "pct": 100})
    assert doc["hot_windows"][1]["id"] == "h2" and doc["hot_windows"][1]["days"] == [5, 6]
    doc = svc.hot_del("h1")
    assert [w["id"] for w in doc["hot_windows"]] == ["h2"]
    with pytest.raises(ValueError):
        svc.hot_del("h1")
    # ids are never reused
    doc = svc.hot_add({"days": [1], "start": "00:00", "end": "23:59", "label": "x", "pct": 0})
    assert doc["hot_windows"][-1]["id"] == "h3"


@pytest.mark.parametrize("arg", [
    {"days": [], "start": "11:00", "end": "12:00", "label": "a", "pct": 5},
    {"days": [7], "start": "11:00", "end": "12:00", "label": "a", "pct": 5},
    {"days": [0, 0], "start": "11:00", "end": "12:00", "label": "a", "pct": 5},
    {"days": [True], "start": "11:00", "end": "12:00", "label": "a", "pct": 5},
    {"days": 0, "start": "11:00", "end": "12:00", "label": "a", "pct": 5},
    {"days": [0], "start": "24:00", "end": "12:00", "label": "a", "pct": 5},
    {"days": [0], "start": "11:60", "end": "12:00", "label": "a", "pct": 5},
    {"days": [0], "start": "1:00", "end": "12:00", "label": "a", "pct": 5},
    {"days": [0], "start": "11:00", "end": "11:00", "label": "a", "pct": 5},
    {"days": [0], "start": "11:00", "end": "12:00", "label": "", "pct": 5},
    {"days": [0], "start": "11:00", "end": "12:00", "label": "x" * 41, "pct": 5},
    {"days": [0], "start": "11:00", "end": "12:00", "label": "a\nb", "pct": 5},
    {"days": [0], "start": "11:00", "end": "12:00", "label": "a", "pct": 1001},
    {"days": [0], "start": "11:00", "end": "12:00", "label": "a", "pct": -1},
    {"days": [0], "start": "11:00", "end": "12:00", "label": "a", "pct": 5.5},
    {"days": [0], "start": "11:00", "end": "12:00", "label": "a"},
    {"days": [0], "start": "11:00", "end": "12:00", "label": "a", "pct": 5, "x": 1},
])
def test_hot_add_bad(svc, arg):
    with pytest.raises(ValueError):
        svc.hot_add(arg)


def test_hot_windows_capped(svc):
    for _ in range(leveling.MAX_HOT):
        svc.hot_add({"days": [0], "start": "01:00", "end": "02:00", "label": "a", "pct": 1})
    with pytest.raises(ValueError):
        svc.hot_add({"days": [0], "start": "01:00", "end": "02:00", "label": "a", "pct": 1})


def test_milestones_set_sorted_unique(svc):
    doc = svc.set_milestones([61, 50, 56])
    assert doc["milestones"] == [50, 56, 61] and doc["milestones_seed"] is False
    assert svc.set_milestones([])["milestones"] == []


@pytest.mark.parametrize("arg", [[0], [71], [50, 50], [50.0], [True], "50", None,
                                 list(range(1, 23))])
def test_milestones_bad(svc, arg):
    with pytest.raises(ValueError):
        svc.set_milestones(arg)


def test_corrupt_entries_skipped(store, clock):
    store.put("leveling", {"samples": [{"ts": "nope", "level": 5, "pct": 1},
                                       {"ts": T0.isoformat(), "level": 99, "pct": 1},
                                       {"ts": T0.isoformat(), "level": 52, "pct": 10.5}, 7],
                           "hot_windows": [{"id": "h1", "days": [9], "start": "x", "end": "y",
                                            "label": "a", "pct": 1},
                                           _w("h2", [0], "11:00", "13:00"), "junk"],
                           "milestones": [50, "x", 99, 56], "next_id": "bad"})
    s = leveling.LevelingService(store, clock=clock)
    doc = s.view()
    assert [x["pct"] for x in doc["samples"]] == [10.5]
    assert [w["id"] for w in doc["hot_windows"]] == ["h2"]
    assert doc["milestones"] == [50, 56]
    assert s.hot_add({"days": [1], "start": "01:00", "end": "02:00", "label": "a",
                      "pct": 1})["hot_windows"][-1]["id"] == "h3"


def test_xp_stack_counts_grind_buffs(store, clock):
    g = grind.GrindService(store, clock=clock)
    s = leveling.LevelingService(store, clock=clock, buffs=lambda: g.view()["buffs"])
    g.buff({"name": "XP scroll", "minutes": 30, "xp_pct": 100})
    s.hot_add({"days": [0], "start": "11:00", "end": "13:00", "label": "Hot Time", "pct": 50})
    doc = s.view()
    assert doc["xp_stack_pct"] == 150
    assert {"name": "XP scroll", "pct": 100} in doc["xp_parts"]
    clock.advance(31 * 60)
    assert s.view()["xp_stack_pct"] == 50


def test_change_seq_bumps_on_write(svc):
    a = svc.seq
    svc.sample({"level": 52, "pct": 1})
    assert svc.seq == a + 1
    with pytest.raises(ValueError):
        svc.sample({"level": 0, "pct": 1})
    assert svc.seq == a + 1


def test_source(svc):
    src = svc.source()
    assert src == {"updated": T0.isoformat(), "status": "ok"}


# --- grind xp_pct (backward compatible) --------------------------------------

def test_grind_buff_xp_pct_optional(store, clock):
    g = grind.GrindService(store, clock=clock)
    doc = g.buff({"name": "XP scroll", "minutes": 30})
    b = next(x for x in doc["buffs"] if x["name"] == "XP scroll")
    assert b["xp_pct"] is None
    doc = g.buff({"name": "XP scroll", "minutes": 30, "xp_pct": 100})
    assert next(x for x in doc["buffs"] if x["name"] == "XP scroll")["xp_pct"] == 100
    # re-arm without xp_pct keeps the stored value
    doc = g.buff({"name": "xp scroll", "minutes": 10})
    assert next(x for x in doc["buffs"] if x["name"] == "XP scroll")["xp_pct"] == 100
    # cleared buffs keep their xp_pct too (one-tap re-arm)
    doc = g.clear_buff("xp-scroll")
    assert next(x for x in doc["buffs"] if x["name"] == "XP scroll")["xp_pct"] == 100


@pytest.mark.parametrize("xp", [-1, 1001, 5.5, True, "10", None])
def test_grind_buff_xp_pct_bad(store, clock, xp):
    g = grind.GrindService(store, clock=clock)
    with pytest.raises(ValueError):
        g.buff({"name": "XP scroll", "minutes": 30, "xp_pct": xp})


def test_grind_old_store_without_xp_pct_loads(store, clock):
    store.put("grind", {"spots": [], "sessions": [], "active": None, "next_sid": 1,
                        "buffs": [{"id": "xp-scroll", "name": "XP scroll", "ends": None},
                                  {"id": "bad", "name": "Bad", "ends": None, "xp_pct": 5000}]})
    g = grind.GrindService(store, clock=clock)
    bs = {b["id"]: b for b in g.view()["buffs"]}
    assert bs["xp-scroll"]["xp_pct"] is None and bs["bad"]["xp_pct"] is None


# --- routes ------------------------------------------------------------------

@pytest.fixture()
def lsrv(tmp_path, clock):
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          sse_interval=0.05, market_seed=[], grind_clock=clock,
                          leveling_clock=clock, profile_cfg={})
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield s
    s.shutdown()
    s.server_close()


def _req(s, method, path, body=None, ctype="application/json", host=None):
    c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=5)
    headers = {"Content-Type": ctype} if ctype else {}
    if host:
        headers["Host"] = host
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
    c.request(method, path, body=data, headers=headers)
    r = c.getresponse()
    out = r.read()
    acao = r.getheader("Access-Control-Allow-Origin")
    c.close()
    return r.status, (json.loads(out) if out else None), acao


def test_route_get(lsrv):
    st, doc, _ = _req(lsrv, "GET", "/api/leveling")
    assert st == 200
    assert set(doc) >= {"now", "level", "pct", "rate_pct_h", "eta_next_s", "next_milestone",
                        "hot", "xp_stack_pct", "milestones", "hot_windows", "samples"}


def test_route_post_ops(lsrv, clock):
    st, doc, acao = _req(lsrv, "POST", "/api/leveling", {"sample": {"level": 52, "pct": 10}})
    assert st == 200 and acao is None and doc["level"] == 52
    clock.advance(3600)
    st, doc, _ = _req(lsrv, "POST", "/api/leveling", {"sample": {"level": 52, "pct": 14}})
    assert st == 200 and doc["rate_pct_h"] == pytest.approx(4.0)
    st, doc, _ = _req(lsrv, "POST", "/api/leveling", {"sample_del": doc["samples"][0]["ts"]})
    assert st == 200 and len(doc["samples"]) == 1
    hot = {"days": [0], "start": "12:00", "end": "14:00", "label": "Hot Time", "pct": 50}
    st, doc, _ = _req(lsrv, "POST", "/api/leveling", {"hot_add": hot})
    assert st == 200 and doc["xp_stack_pct"] == 50
    st, doc, _ = _req(lsrv, "POST", "/api/leveling", {"hot_del": "h1"})
    assert st == 200 and doc["hot_windows"] == []
    st, doc, _ = _req(lsrv, "POST", "/api/leveling", {"milestones": [55]})
    assert st == 200 and doc["milestones"] == [55]
    # grind buff with xp_pct reaches the stack through the server wiring
    st, _, _ = _req(lsrv, "POST", "/api/grind",
                    {"buff": {"name": "XP scroll", "minutes": 5, "xp_pct": 100}})
    assert st == 200
    assert _req(lsrv, "GET", "/api/leveling")[1]["xp_stack_pct"] == 100


def test_route_post_guards(lsrv):
    body = {"sample": {"level": 52, "pct": 1}}
    assert _req(lsrv, "POST", "/api/leveling", body, host="evil.example.com")[0] == 403
    assert _req(lsrv, "POST", "/api/leveling", body, ctype="text/plain")[0] == 415
    big = json.dumps({"milestones": [50], "pad": "x" * 5000}).encode()
    assert _req(lsrv, "POST", "/api/leveling", big)[0] == 413
    assert _req(lsrv, "GET", "/api/leveling")[1]["samples"] == []


@pytest.mark.parametrize("body", [b"not json", b"[]", {}, {"nope": 1},
                                  {"sample": {"level": 52, "pct": 1}, "milestones": []},
                                  {"sample": {"level": 0, "pct": 1}}, {"sample_del": "x"},
                                  {"hot_add": {}}, {"hot_del": "h9"}, {"milestones": "x"}])
def test_route_post_bad_body(lsrv, body):
    st, doc, _ = _req(lsrv, "POST", "/api/leveling", body)
    assert st == 400 and "error" in doc


def test_state_reports_leveling_source(lsrv):
    _, doc, _ = _req(lsrv, "GET", "/api/state")
    assert doc["sources"]["leveling"] == {"updated": T0.isoformat(), "status": "ok"}


def test_sse_pushes_leveling_event_on_change(lsrv):
    c = http.client.HTTPConnection("127.0.0.1", lsrv.server_address[1], timeout=5)
    c.request("GET", "/events")
    r = c.getresponse()
    assert b"heartbeat" in r.fp.readline()
    assert _req(lsrv, "POST", "/api/leveling", {"sample": {"level": 53, "pct": 2}})[0] == 200
    got = None
    end = time.monotonic() + 3
    while time.monotonic() < end:
        line = r.fp.readline()
        if line.startswith(b"event: leveling"):
            data = r.fp.readline()
            assert data.startswith(b"data: ")
            got = json.loads(data[6:])
            break
    c.close()
    assert got is not None and got["level"] == 53


def test_refute_r1_sample_del_overflow_is_value_error(tmp_path):
    svc = _svc_for_refute(tmp_path)
    with pytest.raises(ValueError):
        svc.sample_del("9999-12-31T23:59:59-01:00")


def test_refute_r1_missing_milestones_keeps_samples(tmp_path):
    from server.ew import store as store_mod
    st = store_mod.Store(tmp_path)
    st.put("leveling", {"samples": [{"ts": "2026-10-05T10:00:00+00:00", "level": 52,
                                     "pct": 10.0}], "hot_windows": [], "next_id": 1})
    svc = leveling.LevelingService(st, clock=lambda: 1791190000.0)
    doc = st.get("leveling")
    assert doc["milestones"] == list(leveling.SEED_MILESTONES)
    assert len(doc["samples"]) == 1


def _svc_for_refute(tmp_path):
    from server.ew import store as store_mod
    return leveling.LevelingService(store_mod.Store(tmp_path), clock=lambda: 1791190000.0)
