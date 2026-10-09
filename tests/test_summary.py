"""Plan 046: session-end summary, game-exit pending stop, nightly + weekly recap.

No network, no game client: the game state comes from an injected tasklist
(plan 008), everything else is EW's own store. Clocks are injected.
"""

import datetime as dt
import http.client
import json
import threading

import pytest

from server.ew import app as ewapp
from server.ew import events, gamewatch, grind, leveling, summary, today
from server.ew.store import Store

UTC = dt.timezone.utc
T0 = dt.datetime(2026, 10, 5, 12, 0, 0, tzinfo=UTC)  # a Monday


class Clock:
    def __init__(self, when=T0):
        self.t = when.timestamp()

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds

    def at(self):
        return dt.datetime.fromtimestamp(self.t, UTC)


@pytest.fixture()
def clock():
    return Clock()


@pytest.fixture()
def store(tmp_path):
    return Store(tmp_path / "store")


@pytest.fixture()
def gs(store, clock):
    g = grind.GrindService(store, clock=clock)
    g.add_spot("Orc Camp")
    return g


@pytest.fixture()
def ss(store, gs, clock):
    return summary.SummaryService(store, gs, clock=clock)


def _ts(dtm):
    return dtm.timestamp()


# --- aggregation windows -------------------------------------------------------

def test_empty_store_summary_is_empty(store):
    s = summary.session_summary(store, T0 - dt.timedelta(hours=2), T0)
    assert s["grind"] == {"sessions": 0, "minutes": 0, "silver": 0, "silver_per_h": 0,
                          "spots": []}
    assert s["xp"] is None and s["buffs"] == [] and s["dailies"] == [] and s["events"] == []
    assert summary.is_empty(s)
    assert s["since"] == (T0 - dt.timedelta(hours=2)).isoformat()


def test_grind_sessions_inside_window_only(store, gs, clock):
    gs.log({"spot": "orc-camp", "minutes": 60, "silver": 100_000_000, "trash": 0})  # 11:00
    clock.advance(3 * 3600)  # 15:00
    gs.log({"spot": "orc-camp", "minutes": 30, "silver": 50_000_000, "trash": 0})  # 14:30
    gs.add_spot("Hexe")
    gs.log({"spot": "hexe", "minutes": 30, "silver": 200_000_000, "trash": 0})  # 14:30
    s = summary.session_summary(store, T0 + dt.timedelta(hours=1), clock.at())
    g = s["grind"]
    assert g["sessions"] == 2 and g["minutes"] == 60 and g["silver"] == 250_000_000
    assert g["silver_per_h"] == 250_000_000
    assert [r["spot"] for r in g["spots"]] == ["hexe", "orc-camp"]
    assert g["spots"][0]["name"] == "Hexe" and g["spots"][0]["silver_per_h"] == 400_000_000
    # until is exclusive, since inclusive
    s = summary.session_summary(store, T0 - dt.timedelta(hours=1), T0 + dt.timedelta(hours=1))
    assert s["grind"]["sessions"] == 1


def test_loot_value_counts_over_typed_silver(store, gs, clock):
    gs.log({"spot": "orc-camp", "minutes": 60, "silver": 1, "trash": 0})
    doc = store.get("grind")
    doc["sessions"][-1]["loot_value"] = {"total": 900, "trash": 900, "market": 0, "unknown": []}
    store.put("grind", doc)
    s = summary.session_summary(store, T0 - dt.timedelta(hours=2), T0)
    assert s["grind"]["silver"] == 900


def test_xp_gained_spans_level_rollover(store, clock):
    lv = leveling.LevelingService(store, clock=clock)
    clock.t = _ts(T0 - dt.timedelta(hours=3))
    lv.sample({"level": 62, "pct": 80})
    clock.t = _ts(T0 - dt.timedelta(hours=1))
    lv.sample({"level": 63, "pct": 10.5})
    clock.t = _ts(T0 + dt.timedelta(hours=1))
    lv.sample({"level": 63, "pct": 50})  # after the window: ignored
    s = summary.session_summary(store, T0 - dt.timedelta(hours=2), T0)
    assert s["xp"] == {"gained_pct": 30.5, "from": {"level": 62, "pct": 80},
                       "to": {"level": 63, "pct": 10.5}}


def test_xp_needs_two_points(store, clock):
    lv = leveling.LevelingService(store, clock=clock)
    clock.t = _ts(T0 - dt.timedelta(hours=1))
    lv.sample({"level": 62, "pct": 80})
    assert summary.session_summary(store, T0 - dt.timedelta(hours=2), T0)["xp"] is None


def test_buffs_dailies_and_events_in_window(store, gs, clock):
    gs.buff({"name": "XP scroll", "minutes": 30})
    tsvc = today.TodayService(store, clock=clock)
    tsvc.tick("attendance-reward")
    esvc = events.EventsService(store, clock=clock)
    eid = esvc.add({"kind": "event", "title": "Season pass"})["item"]["id"]
    esvc.add({"kind": "event", "title": "Not claimed"})
    esvc.done({"id": eid, "done": True})
    clock.advance(60)
    s = summary.session_summary(store, T0 - dt.timedelta(minutes=5), clock.at())
    assert [b["name"] for b in s["buffs"]] == ["XP scroll"]
    assert [d["id"] for d in s["dailies"]] == ["attendance-reward"]
    assert s["dailies"][0]["title"] == "Attendance reward"
    assert [e["title"] for e in s["events"]] == ["Season pass"]
    assert not summary.is_empty(s)
    # a later window sees none of the ticks / claims; the buff timer ran out
    later = clock.at() + dt.timedelta(hours=2)
    s = summary.session_summary(store, later, later + dt.timedelta(hours=1))
    assert summary.is_empty(s)


def test_event_undone_loses_claim_time(store, clock):
    esvc = events.EventsService(store, clock=clock)
    eid = esvc.add({"kind": "event", "title": "Season pass"})["item"]["id"]
    esvc.done({"id": eid, "done": True})
    clock.advance(600)
    esvc.done({"id": eid, "done": True})  # re-done keeps the first claim time
    item = next(i for i in store.get("events")["items"] if i["id"] == eid)
    assert item["done_at"] == T0.isoformat()
    esvc.done({"id": eid, "done": False})
    item = next(i for i in store.get("events")["items"] if i["id"] == eid)
    assert "done_at" not in item


def test_day_and_week_windows(store, gs, clock):
    clock.t = _ts(dt.datetime(2026, 10, 1, 1, 0, tzinfo=UTC))  # Thursday 01:00
    gs.log({"spot": "orc-camp", "minutes": 30, "silver": 10, "trash": 0})
    clock.t = _ts(dt.datetime(2026, 9, 30, 23, 0, tzinfo=UTC))  # Wed 23:00: last week
    gs.log({"spot": "orc-camp", "minutes": 30, "silver": 1000, "trash": 0})
    clock.t = _ts(T0)
    gs.log({"spot": "orc-camp", "minutes": 30, "silver": 5, "trash": 0})
    day = summary.day_summary(store, T0)
    week = summary.week_summary(store, T0)
    assert day["since"] == "2026-10-05T00:00:00+00:00" and day["grind"]["silver"] == 5
    assert week["since"] == "2026-10-01T00:00:00+00:00" and week["grind"]["silver"] == 15


def test_reversed_window_is_swapped(store):
    s = summary.session_summary(store, T0, T0 - dt.timedelta(hours=1))
    assert s["since"] < s["until"]


# --- game exit hook: pending_stop ------------------------------------------------

def test_exit_with_session_sets_pending_never_stops(ss, gs, store, clock):
    ss.on_game(None, "running", clock())
    gs.start("orc-camp")
    clock.advance(90 * 60)
    ss.on_game("logged_in", "not_running", clock())
    exit_at = clock.at().isoformat()
    clock.advance(30 * 60)
    v = gs.view()
    assert v["active"] is not None  # never auto-stopped
    assert v["pending_stop"] == {"at": exit_at, "started": T0.isoformat(), "spot": "orc-camp",
                                 "minutes": 90}
    assert ss.last_game() == (T0, T0 + dt.timedelta(minutes=90))
    assert ss.view()["pending_stop"]["at"] == exit_at


def test_exit_without_session_sets_nothing(ss, gs, clock):
    ss.on_game(None, "running", clock())
    clock.advance(60)
    ss.on_game("running", "not_running", clock())
    assert gs.view()["pending_stop"] is None
    assert ss.last_game() is not None  # the play window is still recorded


@pytest.mark.parametrize("prev, new", [
    (None, "not_running"), ("not_running", "not_running"), ("unconfigured", "not_running"),
    ("running", "logged_in"), ("logged_in", "disconnected"), ("running", "unconfigured"),
])
def test_only_up_to_not_running_marks(ss, gs, clock, prev, new):
    gs.start("orc-camp")
    clock.advance(60)
    ss.on_game(prev, new, clock())
    assert gs.view()["pending_stop"] is None


def test_stop_at_exit_uses_exit_time_and_clears(ss, gs, clock):
    gs.start("orc-camp")
    clock.advance(45 * 60)
    ss.on_game("running", "not_running", clock())
    clock.advance(3 * 3600)
    v = gs.stop({"silver": 300, "trash": 2, "at_exit": True})
    assert v["active"] is None and v["pending_stop"] is None
    assert v["sessions"][0]["minutes"] == 45


def test_plain_stop_clears_pending(ss, gs, clock):
    gs.start("orc-camp")
    clock.advance(600)
    ss.on_game("running", "not_running", clock())
    v = gs.stop({"silver": 1, "trash": 0})
    assert v["pending_stop"] is None and v["sessions"][0]["minutes"] == 10


def test_keep_running_clears_pending_only(ss, gs, clock):
    gs.start("orc-camp")
    clock.advance(600)
    ss.on_game("running", "not_running", clock())
    v = gs.keep(True)
    assert v["pending_stop"] is None and v["active"] is not None
    with pytest.raises(ValueError):
        gs.keep(True)  # nothing pending any more
    with pytest.raises(ValueError):
        gs.stop({"silver": 1, "trash": 0, "at_exit": True})


@pytest.mark.parametrize("bad", [False, 1, "yes", None])
def test_keep_and_at_exit_need_true(ss, gs, clock, bad):
    gs.start("orc-camp")
    ss.on_game("running", "not_running", clock())
    with pytest.raises(ValueError):
        gs.keep(bad)
    with pytest.raises(ValueError):
        gs.stop({"silver": 1, "trash": 0, "at_exit": bad})


def test_relaunch_clears_pending_and_next_exit_wins(ss, gs, clock):
    gs.start("orc-camp")
    clock.advance(600)
    ss.on_game("running", "not_running", clock())
    assert gs.view()["pending_stop"] is not None
    clock.advance(600)
    ss.on_game("not_running", "running", clock())  # back in game
    assert gs.view()["pending_stop"] is None and gs.view()["active"] is not None
    clock.advance(600)
    ss.on_game("running", "not_running", clock())
    assert gs.view()["pending_stop"]["at"] == clock.at().isoformat()
    assert gs.view()["pending_stop"]["minutes"] == 30


def test_up_to_up_keeps_pending(ss, gs, clock):
    gs.start("orc-camp")
    ss.on_game("running", "not_running", clock())
    gs.mark_pending_stop(clock.at().isoformat())
    ss.on_game("running", "logged_in", clock())  # not a relaunch
    assert gs.view()["pending_stop"] is not None


def test_mark_twice_keeps_first(gs, clock):
    gs.start("orc-camp")
    clock.advance(60)
    assert gs.mark_pending_stop(clock.at().isoformat()) is True
    first = gs.view()["pending_stop"]["at"]
    clock.advance(60)
    assert gs.mark_pending_stop(clock.at().isoformat()) is False
    assert gs.view()["pending_stop"]["at"] == first


def test_buff_armed_after_window_is_excluded(store, gs, clock):
    gs.buff({"name": "Value Pack", "minutes": 43200})
    past = summary.session_summary(store, T0 - dt.timedelta(hours=2), T0 - dt.timedelta(hours=1))
    assert past["buffs"] == []
    now = summary.session_summary(store, T0 - dt.timedelta(hours=1), T0 + dt.timedelta(minutes=1))
    assert [b["name"] for b in now["buffs"]] == ["Value Pack"]
    gs.clear_buff("value-pack")
    assert summary.session_summary(store, T0, T0 + dt.timedelta(hours=1))["buffs"] == []


def test_legacy_buff_without_armed_counts_on_end(store, gs, clock):
    doc = store.get("grind")
    doc["buffs"].append({"id": "old", "name": "Old", "ends": T0.isoformat()})
    store.put("grind", doc)
    s = summary.session_summary(store, T0 - dt.timedelta(hours=1), T0)
    assert [b["name"] for b in s["buffs"]] == ["Old"]


def test_stale_pending_dropped_for_new_session(ss, gs, store, clock):
    gs.start("orc-camp")
    clock.advance(600)
    ss.on_game("running", "not_running", clock())
    doc = store.get("grind")
    doc["active"] = {"spot": "orc-camp", "started": clock.at().isoformat()}  # replaced
    store.put("grind", doc)
    assert gs.view()["pending_stop"] is None


def test_mark_before_session_start_is_ignored(gs, clock):
    gs.start("orc-camp")
    assert gs.mark_pending_stop((T0 - dt.timedelta(minutes=1)).isoformat()) is False
    assert gs.view()["pending_stop"] is None
    with pytest.raises(ValueError):
        gs.mark_pending_stop("not a time")


def test_session_view_after_exit(ss, gs, clock):
    ss.on_game(None, "running", clock())
    gs.start("orc-camp")
    clock.advance(3600)
    ss.on_game("running", "not_running", clock())
    gs.stop({"silver": 600, "trash": 0, "at_exit": True})
    v = ss.view()
    assert v["session"]["grind"]["sessions"] == 1 and v["session"]["empty"] is False
    assert v["session"]["grind"]["silver_per_h"] == 600
    assert v["day"]["grind"]["sessions"] == 1 and v["week"]["empty"] is False
    assert v["pending_stop"] is None


def test_view_without_a_game_session(ss):
    v = ss.view()
    assert v["session"] is None and v["day"]["empty"] is True and v["pending_stop"] is None


# --- plan 008 watcher -> hook ----------------------------------------------------

def test_gamewatch_listener_fires_on_change_only(tmp_path, clock):
    listed = [True]
    w = gamewatch.GameWatch(install_dir=str(tmp_path), clock=clock,
                            tasklist=lambda: listed[0])
    seen = []
    w.listeners.append(lambda p, n, at: seen.append((p, n, at)))
    w.listeners.insert(0, lambda p, n, at: 1 / 0)  # a broken listener is swallowed
    w.poll()
    w.poll()
    listed[0] = False
    clock.advance(5)
    w.poll()
    assert seen == [(None, "running", T0.timestamp()),
                    ("running", "not_running", T0.timestamp() + 5)]


def _req(s, method, path, body=None):
    c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=5)
    data = json.dumps(body).encode() if body is not None else None
    c.request(method, path, body=data, headers={"Content-Type": "application/json"})
    r = c.getresponse()
    out = r.read()
    c.close()
    return r.status, json.loads(out) if out else None


@pytest.fixture()
def esrv(tmp_path, clock):
    listed = [True]
    w = gamewatch.GameWatch(install_dir=str(tmp_path / "bdo"), clock=clock,
                            tasklist=lambda: listed[0])
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          sse_interval=0.05, market_seed=[], grind_clock=clock,
                          game_watch=w)
    s.listed = listed
    t = threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    t.start()
    yield s
    s.shutdown()
    s.server_close()


def test_routes_end_to_end(esrv, clock):
    assert _req(esrv, "POST", "/api/grind", {"add_spot": "Orc Camp"})[0] == 200
    esrv.game.poll()  # running
    assert _req(esrv, "POST", "/api/grind", {"start": "orc-camp"})[0] == 200
    clock.advance(1200)
    esrv.listed[0] = False
    esrv.game.poll()  # not_running -> pending
    st, g = _req(esrv, "GET", "/api/grind")
    assert st == 200 and g["active"] is not None and g["pending_stop"]["minutes"] == 20
    st, s = _req(esrv, "GET", "/api/summary")
    assert st == 200 and s["pending_stop"]["minutes"] == 20 and s["session"]["empty"] is True
    st, g = _req(esrv, "POST", "/api/grind", {"keep": True})
    assert st == 200 and g["pending_stop"] is None and g["active"] is not None
    st, err = _req(esrv, "POST", "/api/grind", {"keep": True})
    assert st == 400 and "pending" in err["error"]
