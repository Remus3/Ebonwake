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
from server.ew import events as ewevents
from server.ew import grind, leveling, levels
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
    assert doc["milestones"] == [56, 60, 61, 70, 75] and doc["milestones_seed"] is True
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
    {"level": 0, "pct": 1}, {"level": 76, "pct": 1}, {"level": 52, "pct": -0.001},
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
    assert svc.sample({"level": 75, "pct": 100})["level"] == 75
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


@pytest.mark.parametrize("arg", [[0], [76], [50, 50], [50.0], [True], "50", None,
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
    leveling.LevelingService(st, clock=lambda: 1791190000.0)
    doc = st.get("leveling")
    assert doc["milestones"] == list(leveling.SEED_MILESTONES)
    assert len(doc["samples"]) == 1


def _svc_for_refute(tmp_path):
    from server.ew import store as store_mod
    return leveling.LevelingService(store_mod.Store(tmp_path), clock=lambda: 1791190000.0)


# --- plan 024: level-gated deadlines -------------------------------------------

ENROL = T0 + dt.timedelta(days=10)  # 2026-10-15 12:00 UTC
DL = {"id": "olvia-test", "label": "Olvia Academy", "needs_level": 60,
      "enrol_by_utc": ENROL.isoformat(), "quests_by_utc": None, "source": "test", "verified": False}


def _dsvc(store, clock, deadlines=None, epochs=()):
    return leveling.LevelingService(store, clock=clock, epochs=list(epochs),
                                    deadlines=[dict(DL)] if deadlines is None else deadlines)


def _st(level, eta_s, now=T0):
    return leveling.deadline_status(levels.validate_deadline(DL), level, eta_s, now)


def test_eta_to_level():
    assert leveling.eta_to_level_s(58, 50.0, 10.0, 60) == 15 * 3600  # 150 pct at 10/h
    assert leveling.eta_to_level_s(60, 0, None, 60) == 0  # already there, no rate needed
    assert leveling.eta_to_level_s(58, 50.0, None, 60) is None
    assert leveling.eta_to_level_s(None, None, 10.0, 60) is None


def test_deadline_status_each_state():
    assert _st(60, 0) == {"state": "done", "reach_utc": None, "margin_h": None}
    assert _st(61, None)["state"] == "done"
    assert _st(58, None) == {"state": "unknown", "reach_utc": None, "margin_h": None}
    assert _st(None, None)["state"] == "unknown"
    on = _st(58, 24 * 3600)  # reach in 1 day, 9 days of margin
    assert on == {"state": "on_track", "reach_utc": (T0 + dt.timedelta(days=1)).isoformat(),
                  "margin_h": 216.0}
    tight = _st(58, 8 * 86400)  # 2 days margin < 72 h
    assert tight["state"] == "tight" and tight["margin_h"] == 48.0
    edge = _st(58, 7 * 86400)  # exactly 72 h is not tight
    assert edge["state"] == "on_track" and edge["margin_h"] == 72.0
    late = _st(58, 11 * 86400)
    assert late["state"] == "late" and late["margin_h"] == -24.0
    # enrolment already closed, level not reached: late even without a rate
    after = ENROL + dt.timedelta(hours=1)
    assert _st(58, None, now=after)["state"] == "late"


def test_deadline_state_agrees_with_rounded_margin():
    # verifier r1 minor 3: 71.96 h rounds to 72.0 and must not read tight
    assert _st(58, int(7 * 86400 + 0.04 * 3600)) == {
        "state": "on_track", "reach_utc": (T0 + dt.timedelta(seconds=int(7 * 86400 + 144))).isoformat(),
        "margin_h": 72.0}
    zero = _st(58, 10 * 86400 + 100)  # -0.03 h shows 0.0: tight, not late
    assert zero["state"] == "tight" and zero["margin_h"] == 0.0
    assert _st(58, 10 * 86400 + 200)["margin_h"] == -0.1


def test_deadline_hidden_after_final_cutoff(store, clock):
    # verifier r1 minor 5: past quests_by_utc (or enrol when null) the row
    # leaves the view, so the overlay pill does not stay red forever.
    quests = (ENROL + dt.timedelta(days=6)).isoformat()
    svc = _dsvc(store, clock, deadlines=[dict(DL, quests_by_utc=quests),
                                         dict(DL, id="no-quests")])
    clock.t = (ENROL + dt.timedelta(days=1)).timestamp()
    v = svc.view()
    assert [(d["id"], d["state"]) for d in v["deadlines"]] == [("olvia-test", "late")]
    clock.t = (ENROL + dt.timedelta(days=6)).timestamp()
    assert svc.view()["deadlines"] == []
    assert svc.deadline_del("olvia-test")["deadlines"] == []  # still deletable


def test_view_deadlines_from_rate(store, clock):
    svc = _dsvc(store, clock)
    assert svc.view()["deadlines"][0]["state"] == "unknown"
    svc.sample({"level": 58, "pct": 0})
    clock.advance(3600)
    v = svc.sample({"level": 58, "pct": 10})  # 10 pct/h -> 190 pct left = 19 h
    d = v["deadlines"][0]
    assert d["id"] == "olvia-test" and d["tracked"] is True and d["state"] == "on_track"
    assert d["reach_utc"] == (T0 + dt.timedelta(hours=1 + 19)).isoformat()
    assert d["enrol_in_s"] == int((ENROL - T0).total_seconds()) - 3600
    assert v["deadline_error"] is None


def test_view_deadline_late_from_slow_rate(store, clock):
    svc = _dsvc(store, clock)
    svc.sample({"level": 58, "pct": 0})
    clock.advance(36000)
    v = svc.sample({"level": 58, "pct": 1})  # 0.1 pct/h -> 1990 h, far past ENROL
    assert v["deadlines"][0]["state"] == "late"


def test_deadline_across_epoch_boundary(store, clock):
    # Fast pre-patch samples say on_track; the plan 018 epoch drops them, so
    # with one post-patch sample there is no rate (unknown), then the slow
    # post-patch rate makes it late.
    epoch = {"id": "patch", "starts_utc": (T0 + dt.timedelta(hours=2)).isoformat(),
             "label": "XP rescale", "source": "test", "verified": False}
    svc = _dsvc(store, clock, epochs=[epoch])
    svc.sample({"level": 58, "pct": 0})
    clock.advance(3600)
    assert svc.sample({"level": 59, "pct": 0})["deadlines"][0]["state"] == "on_track"
    clock.advance(3 * 3600)  # past the epoch
    assert svc.sample({"level": 59, "pct": 10})["deadlines"][0]["state"] == "unknown"
    clock.advance(10 * 3600)
    v = svc.sample({"level": 59, "pct": 11})  # 0.1 pct/h post-patch
    assert v["rate_pct_h"] == pytest.approx(0.1)
    assert v["deadlines"][0]["state"] == "late"


def test_tracked_deadlines_file_is_valid():
    rows = levels.load_deadlines()
    olvia = {r["id"]: r for r in rows}["olvia-class-3"]
    assert olvia["needs_level"] == 60 and olvia["verified"] is False
    assert olvia["enrol_by_utc"].startswith("2026-11-05")
    assert olvia["quests_by_utc"].startswith("2026-11-11")
    assert "official notice" in olvia["note"]


def test_bad_tracked_deadlines_file_degrades(tmp_path, store, clock, monkeypatch):
    bad = tmp_path / "deadlines.json"
    bad.write_text("{", encoding="ascii")
    monkeypatch.setattr(levels.load_deadlines, "__defaults__", (bad,))
    v = leveling.LevelingService(store, clock=clock, epochs=[]).view()
    assert v["deadlines"] == [] and "unreadable" in v["deadline_error"]


@pytest.mark.parametrize("patch,msg", [
    ({"id": "Bad Id"}, "id"), ({"label": ""}, "label"), ({"label": "x" * 41}, "label"),
    ({"needs_level": 0}, "needs_level"), ({"needs_level": 76}, "needs_level"),
    ({"needs_level": True}, "needs_level"), ({"enrol_by_utc": "2026-11-05"}, "enrol_by_utc"),
    ({"enrol_by_utc": "x"}, "enrol_by_utc"), ({"quests_by_utc": "2026-10-01T00:00:00Z"}, "quests"),
    ({"source": ""}, "source"), ({"verified": 1}, "verified"), ({"note": 5}, "note"),
    ({"extra": 1}, "deadline must be")])
def test_deadline_set_validation(store, clock, patch, msg):
    svc = _dsvc(store, clock)
    with pytest.raises(ValueError, match=msg):
        svc.deadline_set(dict(DL, **patch))
    missing = dict(DL)
    del missing["quests_by_utc"]
    with pytest.raises(ValueError, match="deadline must be"):
        svc.deadline_set(missing)
    with pytest.raises(ValueError):
        svc.deadline_set("x")
    assert store.get("leveling").get("deadlines_added", []) == []


def test_deadline_set_overrides_and_tombstones(store, clock):
    svc = _dsvc(store, clock)
    # correct the tracked row by id: operator copy wins, still flagged tracked
    fixed = dict(DL, enrol_by_utc="2026-10-20T14:00:00Z", verified=True, note="official")
    v = svc.deadline_set(fixed)
    d = v["deadlines"][0]
    assert d["enrol_by_utc"] == "2026-10-20T14:00:00+00:00" and d["verified"] is True
    assert d["tracked"] is True and d["note"] == "official"
    # a new operator row sorts by enrolment
    v = svc.deadline_set(dict(DL, id="season-end", label="Season end", needs_level=61,
                              enrol_by_utc="2026-10-10T00:00:00Z"))
    assert [x["id"] for x in v["deadlines"]] == ["season-end", "olvia-test"]
    assert v["deadlines"][0]["tracked"] is False
    # delete the operator row: gone, no tombstone
    v = svc.deadline_del("season-end")
    assert [x["id"] for x in v["deadlines"]] == ["olvia-test"]
    assert store.get("leveling")["deadlines_deleted"] == []
    # delete the tracked row: override dropped + tombstone
    v = svc.deadline_del("olvia-test")
    assert v["deadlines"] == [] and store.get("leveling")["deadlines_deleted"] == ["olvia-test"]
    with pytest.raises(ValueError, match="unknown deadline"):
        svc.deadline_del("olvia-test")
    with pytest.raises(ValueError, match="deadline id"):
        svc.deadline_del(5)
    # set again revives it (tombstone lifted)
    v = svc.deadline_set(dict(DL))
    assert [x["id"] for x in v["deadlines"]] == ["olvia-test"]
    assert store.get("leveling")["deadlines_deleted"] == []


def test_deadline_cap_and_corrupt_rows(store, clock):
    svc = _dsvc(store, clock, deadlines=[])
    for i in range(leveling.MAX_DEADLINES):
        svc.deadline_set(dict(DL, id=f"d{i}"))
    with pytest.raises(ValueError, match="at most"):
        svc.deadline_set(dict(DL, id="one-more"))
    svc.deadline_set(dict(DL, id="d0", label="replaced"))  # replacing at the cap is fine
    doc = store.get("leveling")
    doc["deadlines_added"] = [{"id": "bad"}, "x", dict(DL, id="ok")]
    doc["deadlines_deleted"] = ["ok-too", 5, "Bad Id"]
    store.put("leveling", doc)
    assert [x["id"] for x in svc.view()["deadlines"]] == ["ok"]


def test_events_lists_deadline_within_14_days(store, clock):
    rows = [dict(DL, id="near", state="late", reach_utc=None),
            dict(DL, id="far", enrol_by_utc=(T0 + dt.timedelta(days=15)).isoformat(),
                 state="on_track", reach_utc=None),
            dict(DL, id="done", state="done", reach_utc=None),
            dict(DL, id="past", enrol_by_utc=(T0 - dt.timedelta(hours=1)).isoformat(),
                 state="late", reach_utc=None)]
    ev = ewevents.EventsService(store, clock=clock, deadlines=lambda: rows)
    v = ev.view()
    assert [d["id"] for d in v["deadlines"]] == ["near"]
    assert v["deadlines"][0]["left_s"] == 10 * 86400 and v["deadlines"][0]["state"] == "late"
    assert v["items"] == []  # never stored as an event entry
    assert ewevents.EventsService(store, clock=clock).view()["deadlines"] == []


def test_route_deadline_ops_and_events(tmp_path, clock):
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          sse_interval=0.05, market_seed=[], grind_clock=clock,
                          leveling_clock=clock, events_clock=clock, profile_cfg={})
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    try:
        doc = _req(s, "GET", "/api/leveling")[1]
        assert "olvia-class-3" in [d["id"] for d in doc["deadlines"]]
        # Olvia closes 2026-11-05, > 14 days after T0: not on the Events tab yet
        assert _req(s, "GET", "/api/events")[1]["deadlines"] == []
        st, doc, _ = _req(s, "POST", "/api/leveling", {"deadline_set": dict(DL)})
        assert st == 200 and "olvia-test" in [d["id"] for d in doc["deadlines"]]
        ev = _req(s, "GET", "/api/events")[1]
        assert [d["id"] for d in ev["deadlines"]] == ["olvia-test"]
        st, doc, _ = _req(s, "POST", "/api/leveling", {"deadline_del": "olvia-class-3"})
        assert st == 200 and [d["id"] for d in doc["deadlines"]] == ["olvia-test"]
        st, doc, _ = _req(s, "POST", "/api/leveling", {"deadline_set": {"id": "x"}})
        assert st == 400 and "error" in doc
    finally:
        s.shutdown()
        s.server_close()


# --- plan 041: profile level markers ------------------------------------------

def _m(minutes, level):
    return {"ts": (T0 + dt.timedelta(minutes=minutes)).isoformat(), "level": level,
            "pct": None, "source": "profile"}


def test_clean_sample_keeps_profile_marker():
    m = leveling._clean_sample(_m(0, 61))
    assert m == {"ts": T0.isoformat(), "level": 61, "pct": None, "source": "profile"}


@pytest.mark.parametrize("bad", [
    {"pct": None}, {"pct": None, "source": "typed"}, {"pct": None, "source": "Profile"},
    {"pct": None, "source": None}, {"pct": "x", "source": "profile"}])
def test_clean_sample_drops_other_null_pct(bad):
    s = dict({"ts": T0.isoformat(), "level": 61}, **bad)
    assert leveling._clean_sample(s) is None


def test_clean_sample_typed_shape_unchanged():
    s = dict(_s(0, 52, 10), source="profile", extra=1)
    assert leveling._clean_sample(s) == {"ts": T0.isoformat(), "level": 52, "pct": 10}


def test_marker_between_typed_leaves_rate_and_eta(store, clock):
    plain = [_s(0, 60, 10), _s(60, 60, 14), _s(120, 60, 20)]
    with_m = [plain[0], _m(30, 61), plain[1], _m(90, 62), plain[2]]
    assert leveling.rate_pct_h(with_m) == leveling.rate_pct_h(plain)
    assert leveling._points(with_m) == leveling._points(plain)
    clock.advance(3 * 3600)
    store.put("leveling", {"samples": plain, "milestones": [70]})
    a = leveling.LevelingService(store, clock=clock).view()
    store.put("leveling", {"samples": [plain[0], _m(30, 60), plain[1], plain[2]],
                           "milestones": [70]})
    b = leveling.LevelingService(store, clock=clock).view()
    for k in ("level", "pct", "rate_pct_h", "eta_next_s"):
        assert a[k] == b[k], k
    assert b["level_source"] == "typed" and len(b["samples"]) == 4


def test_view_higher_marker_level_wins_pct_null(store, clock):
    store.put("leveling", {"samples": [_s(0, 60, 10), _s(60, 60, 14), _m(90, 61)],
                           "milestones": [61, 70]})
    clock.advance(2 * 3600)
    svc = leveling.LevelingService(store, clock=clock)
    v = svc.view()
    assert v["level"] == 61 and v["pct"] is None and v["level_source"] == "profile"
    assert v["rate_pct_h"] == pytest.approx(4.0)  # from the typed samples
    assert v["eta_next_s"] is None  # pct of the new level unknown
    assert v["next_milestone"] == 70
    assert v["samples"][0]["source"] == "profile" and "source" not in v["samples"][1]
    assert svc.current_level() == 61


def test_view_lower_marker_level_keeps_typed(store, clock):
    store.put("leveling", {"samples": [_m(0, 59), _s(60, 60, 14)], "milestones": [70]})
    v = leveling.LevelingService(store, clock=clock).view()
    assert v["level"] == 60 and v["pct"] == 14 and v["level_source"] == "typed"


def test_view_marker_only_and_empty(store, clock):
    svc = leveling.LevelingService(store, clock=clock)
    v = svc.view()
    assert v["level"] is None and v["level_source"] is None
    store.put("leveling", dict(store.get("leveling"), samples=[_m(0, 61)]))
    v = svc.view()
    assert v["level"] == 61 and v["pct"] is None and v["level_source"] == "profile"
    assert v["rate_pct_h"] is None and v["eta_next_s"] is None


def test_profile_marker_written_by_server_only(svc, clock):
    svc.sample({"level": 60, "pct": 50})
    clock.advance(60)
    assert svc.profile_marker(60) is False  # nothing new
    assert svc.profile_marker(61) is True
    v = svc.view()
    assert v["level"] == 61 and v["level_source"] == "profile"
    assert v["samples"][0] == {"ts": (T0 + dt.timedelta(seconds=60)).isoformat(),
                               "level": 61, "pct": None, "source": "profile",
                               "pre_patch": False}
    clock.advance(60)
    assert svc.profile_marker(61) is False  # already known
    for bad in (0, 76, "61", None, True):
        assert svc.profile_marker(bad) is False
    with pytest.raises(ValueError):
        svc.sample({"level": 62, "pct": None})


def test_profile_marker_never_replaces_typed_same_second(svc, clock):
    svc.sample({"level": 60, "pct": 50})
    assert svc.profile_marker(61) is False
    v = svc.view()
    assert v["samples"] == [{"ts": T0.isoformat(), "level": 60, "pct": 50, "pre_patch": False}]


def test_sample_del_deletes_marker(svc, clock):
    svc.profile_marker(61)
    ts = svc.view()["samples"][0]["ts"]
    v = svc.sample_del(ts)
    assert v["samples"] == [] and v["level"] is None


def test_typed_sample_after_marker_wins(svc, clock):
    svc.profile_marker(61)
    clock.advance(60)
    v = svc.sample({"level": 61, "pct": 2.5})
    assert v["level"] == 61 and v["pct"] == 2.5 and v["level_source"] == "typed"
