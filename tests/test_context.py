"""Plan 067: context-aware overlay - widgets chosen by state, pins and blocks.

Context comes from the plan 008 game state, the clock and public notices only;
no input hook, no game read. Pure parts take an injected `now`.
"""

import datetime as dt
import http.client
import json
import threading
import time

import pytest

from server.ew import app as ewapp
from server.ew import bosses, context, gamewatch, maint, market, settings

UTC = dt.timezone.utc
MIN = 60


def T(*a):
    return dt.datetime(*a, tzinfo=UTC)


NOW = T(2026, 10, 6, 15, 0).timestamp()  # Tue 15:00 UTC: no reset, maint or boss near


def sig(**kw):
    base = {"game": "logged_in", "last_activity": NOW - 60, "maint_start": None,
            "maint_end": None, "reset_at": NOW + 9 * 3600, "boss_at": None, "hot": False}
    base.update(kw)
    return base


RULES = context.load_rules()


# --- each context from fixture signals ------------------------------------------

def test_closed_stands_alone():
    s = sig(game="not_running", boss_at=NOW + 5 * MIN, hot=True, reset_at=NOW + MIN)
    assert context.active_contexts(s, NOW) == ["closed"]


def test_in_game_is_the_base():
    assert context.active_contexts(sig(), NOW) == ["in_game"]
    for st in ("running", "disconnected", "unconfigured", None):
        assert context.active_contexts(sig(game=st), NOW) == ["in_game"]


@pytest.mark.parametrize("kw,want", [
    ({"maint_start": NOW + 60 * MIN, "maint_end": NOW + 240 * MIN}, "maint_soon"),
    ({"maint_start": NOW - MIN, "maint_end": NOW + MIN}, "maint_soon"),  # under way
    ({"boss_at": NOW + 15 * MIN}, "boss_soon"),
    ({"boss_at": NOW}, "boss_soon"),
    ({"reset_at": NOW + 30 * MIN}, "reset_soon"),
    ({"hot": True}, "hot_time"),
    ({"last_activity": NOW - 20 * MIN}, "idle"),
])
def test_each_context_from_signals(kw, want):
    got = context.active_contexts(sig(**kw), NOW)
    assert got == [want, "in_game"]


@pytest.mark.parametrize("kw", [
    {"maint_start": NOW + 61 * MIN, "maint_end": NOW + 240 * MIN},
    {"maint_start": NOW - 200 * MIN, "maint_end": NOW - MIN},  # over
    {"boss_at": NOW + 15 * MIN + 1},
    {"boss_at": NOW - 1},
    {"reset_at": NOW + 30 * MIN + 1},
    {"hot": "yes"},
    {"last_activity": NOW - 20 * MIN + 1},
    {"last_activity": None},
])
def test_outside_the_windows_only_in_game(kw):
    assert context.active_contexts(sig(**kw), NOW) == ["in_game"]


def test_idle_needs_logged_in_and_honours_idle_min():
    s = sig(last_activity=NOW - 10 * MIN)
    assert context.active_contexts(s, NOW, idle_min=10) == ["idle", "in_game"]
    assert context.active_contexts(s, NOW, idle_min=20) == ["in_game"]
    assert context.active_contexts(dict(s, game="running"), NOW, idle_min=5) == ["in_game"]


def test_timestamps_accept_iso_and_datetime():
    at = dt.datetime.fromtimestamp(NOW + 5 * MIN, UTC)
    assert "boss_soon" in context.active_contexts(sig(boss_at=at), NOW)
    assert "boss_soon" in context.active_contexts(sig(boss_at=at.isoformat()), NOW)
    assert "boss_soon" not in context.active_contexts(sig(boss_at="nope"), NOW)
    assert "boss_soon" not in context.active_contexts(sig(boss_at=True), NOW)


# --- priority ---------------------------------------------------------------------

def test_priority_orders_several_contexts():
    s = sig(maint_start=NOW + 30 * MIN, maint_end=NOW + 200 * MIN, boss_at=NOW + 5 * MIN,
            reset_at=NOW + 10 * MIN, hot=True, last_activity=NOW - 3600)
    got = context.active_contexts(s, NOW)
    assert got == ["maint_soon", "boss_soon", "reset_soon", "hot_time", "idle", "in_game"]
    out = context.derive(s, NOW, {}, RULES)
    assert out["context"] == "maint_soon" and out["active"] == got
    assert out["widgets"][0] == "maintenance" and len(out["widgets"]) == 4
    assert out["maint_at"] == context._iso(NOW + 30 * MIN)


def test_boss_soon_puts_world_boss_first():
    out = context.derive(sig(boss_at=NOW + 10 * MIN), NOW, {}, RULES)
    assert out["context"] == "boss_soon" and out["widgets"][0] == "worldBoss"
    assert out["maint_at"] is None


def test_closed_hides_the_overlay():
    out = context.derive(sig(game="not_running"), NOW, {"modes": {"dice": "pin"}}, RULES)
    assert out == {"context": "closed", "active": ["closed"], "widgets": [], "hidden": True,
                   "auto": True, "maint_at": None}


def test_rules_file_shape():
    raw = json.loads(context.DATA.read_text(encoding="ascii"))
    assert raw["max_widgets"] == 4
    assert raw["priority"] == list(context.CONTEXTS)
    assert set(raw["contexts"]) == set(context.CONTEXTS)
    known = set(context.WIDGETS) | set(context.EXTRA_WIDGETS)
    for c, r in raw["contexts"].items():
        assert set(r["widgets"]) <= known, c
    assert raw["contexts"]["closed"]["hidden"] is True
    # Plan 069: the What now row leads the calm contexts.
    assert raw["contexts"]["in_game"]["widgets"][:4] == ["whatNow", "grindSession", "grindBuff",
                                                         "leveling"]
    assert raw["contexts"]["idle"]["widgets"][0] == "whatNow"
    assert raw["contexts"]["boss_soon"]["widgets"][0] == "worldBoss"
    assert raw["contexts"]["maint_soon"]["widgets"][0] == "maintenance"
    assert settings.WIDGETS.keys() == set(context.WIDGETS)


def test_load_rules_drops_junk(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"max_widgets": 99, "priority": ["in_game", "nope", "in_game"],
                             "contexts": {"in_game": {"widgets": ["dice", "x", 3, "dice"]},
                                          "bogus": {"widgets": ["dice"]}}}), encoding="utf-8")
    r = context.load_rules(p)
    assert r["max_widgets"] == 4 and r["priority"][0] == "in_game"
    assert sorted(r["priority"]) == sorted(context.CONTEXTS)
    assert r["contexts"]["in_game"]["widgets"] == ["dice"]
    assert context.load_rules(tmp_path / "missing.json")["contexts"]["in_game"]["widgets"] == []


# --- pins / blocks / manual ---------------------------------------------------------

def test_pins_come_first_and_count_toward_the_cap():
    modes = {"season": "pin", "marketTicker": "pin"}
    w, hidden = context.pick_widgets(RULES, ["in_game"], modes)
    assert not hidden and w == ["season", "marketTicker", "whatNow", "grindSession"]


def test_pins_are_never_cut():
    modes = {k: "pin" for k in context.WIDGETS[:6]}
    w, _ = context.pick_widgets(RULES, ["boss_soon", "in_game"], modes)
    assert w == list(context.WIDGETS[:6])


def test_blocks_never_show_and_the_next_rule_widget_fills():
    w, _ = context.pick_widgets(RULES, ["boss_soon", "in_game"],
                                {"worldBoss": "block", "grindBuff": "block"})
    assert "worldBoss" not in w and "grindBuff" not in w
    assert w == ["grindSession", "whatNow", "leveling", "eventsSoon"]


def test_manual_mode_uses_the_plan030_booleans():
    # Plan 078: a key missing from `manual` takes its widget default (the
    # renderer's overlayWidgets semantics), so grindSession / grindBuff /
    # eventsSoon / whatNow show unless set False.
    prefs = {"auto": False, "manual": {"dice": True, "season": True, "leveling": False,
                                       "grindBuff": False}}
    out = context.derive(sig(boss_at=NOW + MIN), NOW, prefs, RULES)
    assert out["auto"] is False
    assert out["widgets"] == ["grindSession", "eventsSoon", "season", "dice", "whatNow"]
    assert out["context"] == "boss_soon" and out["hidden"] is False


def test_auto_off_with_no_booleans_keeps_the_default_widgets():
    # Research 0009 M1: context mode off with no overlay.widgets.* stored used
    # to read every missing boolean as off and blank the overlay.
    want = [w for w in context.WIDGETS if settings.WIDGETS[w]]
    assert want == ["grindSession", "grindBuff", "eventsSoon", "whatNow"]
    p = context.prefs_from_settings({"overlay.auto": False})
    assert p["auto"] is False
    assert [w for w in context.WIDGETS if p["manual"][w]] == want
    assert context.derive(sig(), NOW, p, RULES)["widgets"] == want
    assert context.derive(sig(), NOW, {"auto": False}, RULES)["widgets"] == want
    # A literal False still turns a default-on widget off; True turns opt-in on.
    p = context.prefs_from_settings({"overlay.auto": False, "overlay.widgets.whatNow": False,
                                     "overlay.widgets.dice": True})
    assert context.derive(sig(), NOW, p, RULES)["widgets"] == [
        "grindSession", "grindBuff", "eventsSoon", "dice"]
    # Non-bool junk falls back to the default, never to off.
    p = context.prefs_from_settings({"overlay.auto": False, "overlay.widgets.grindSession": "x"})
    assert p["manual"]["grindSession"] is True


def test_prefs_from_settings_defaults():
    p = context.prefs_from_settings(settings.defaults())
    assert p["auto"] is True and p["idle_min"] == 20
    assert set(p["modes"].values()) == {"auto"}
    assert p["manual"]["grindSession"] is True and p["manual"]["dice"] is False


def test_settings_keys_validate():
    ok = settings.validate({"set": {"overlay.auto": False, "overlay.idle_min": 5,
                                    "overlay.mode.dice": "pin",
                                    "overlay.mode.worldBoss": "block"}})
    assert ok["overlay.mode.dice"] == "pin"
    for bad in ({"overlay.auto": 1}, {"overlay.idle_min": 4}, {"overlay.idle_min": 241},
                {"overlay.idle_min": 20.0}, {"overlay.mode.dice": "always"},
                {"overlay.mode.maintenance": "pin"}):
        with pytest.raises(ValueError):
            settings.validate({"set": bad})


# --- maintenance window -------------------------------------------------------------

def test_next_window_slot_and_notice():
    slot = {"weekday": "thu", "start_utc": "07:00", "duration_min": 180}
    tue = T(2026, 10, 6, 15)
    assert maint.next_window(tue, slot) == (T(2026, 10, 8, 7), T(2026, 10, 8, 10))
    during = T(2026, 10, 8, 8)
    assert maint.next_window(during, slot) == (T(2026, 10, 8, 7), T(2026, 10, 8, 10))
    after = T(2026, 10, 8, 10)
    assert maint.next_window(after, slot)[0] == T(2026, 10, 15, 7)
    notices = {"2026-10-07": {"start_utc": "2026-10-07T06:00:00+00:00",
                              "end_utc": "2026-10-07T11:00:00+00:00", "source": "n"}}
    assert maint.next_window(tue, slot, notices) == (T(2026, 10, 7, 6), T(2026, 10, 7, 11))


# --- service: caching, provider failure, fixture timeline -------------------------------

class Clock:
    def __init__(self, t):
        self.t = float(t)

    def __call__(self):
        return self.t


def _svc(clock, state, boss=None, writes=None):
    def settings_view():
        return settings.defaults()
    return context.ContextService(
        clock=clock, game=lambda: {"state": state[0], "since": None, "screenshots": []},
        settings=settings_view, boss_at=lambda now: boss[0] if boss else None,
        reset_at=lambda now: T(2026, 10, 7), rules=RULES)


def test_fixture_timeline_switches_widgets():
    """closed -> in_game -> boss_soon -> in_game -> closed."""
    clk, state, boss = Clock(NOW), ["not_running"], [None]
    svc = _svc(clk, state, boss)
    seen = []

    def step(st, boss_at=None):
        state[0], boss[0] = st, boss_at
        svc.invalidate()  # the server does this on each game change / settings save
        v = svc.view()
        seen.append((v["context"], v["widgets"], v["hidden"]))

    step("not_running")
    step("logged_in")
    clk.t += 10 * MIN
    step("logged_in", boss_at=clk.t + 10 * MIN)
    clk.t += 30 * MIN
    step("logged_in", boss_at=clk.t + 3600)
    step("not_running")
    assert [s[0] for s in seen] == ["closed", "in_game", "boss_soon", "in_game", "closed"]
    assert seen[0][1] == [] and seen[0][2] is True
    assert seen[1][1] == ["whatNow", "grindSession", "grindBuff", "leveling"]
    assert seen[2][1][0] == "worldBoss"
    assert seen[3][1] == seen[1][1] and seen[4] == seen[0]


def test_view_cached_until_invalidated_or_stale():
    clk, state = Clock(NOW), ["logged_in"]
    svc = _svc(clk, state)
    assert svc.view()["context"] == "in_game"
    state[0] = "not_running"
    assert svc.view()["context"] == "in_game"  # cached
    clk.t += context.CACHE_S
    assert svc.view()["context"] == "closed"
    state[0] = "logged_in"
    svc.invalidate()
    assert svc.view()["context"] == "in_game"


def test_provider_failure_is_no_signal():
    def boom(*a):
        raise RuntimeError("x")
    svc = context.ContextService(clock=lambda: NOW, game=boom, settings=boom, boss_at=boom,
                                 maint_window=boom, reset_at=boom, hot=boom, rules=RULES)
    v = svc.view()
    assert v["context"] == "in_game" and v["widgets"][0] == "whatNow"


def test_last_activity_uses_screenshots():
    g = {"since": "2026-10-06T14:00:00+00:00",
         "screenshots": [{"mtime": "2026-10-06T14:50:00+00:00"}, {"mtime": "bad"}]}
    assert context.last_activity(g) == T(2026, 10, 6, 14, 50).timestamp()
    assert context.last_activity(None) is None


# --- server: route + SSE push, no settings write, no recreate ---------------------------

class FakeWatch(gamewatch.GameWatch):
    """Unconfigured GameWatch whose state is set by the test."""

    def __init__(self, clock):
        super().__init__(None, None, clock=clock, tasklist=lambda: False)
        self.forced = None

    def poll(self):
        now = self.clock()
        with self._lock:
            prev, state = self._state, self.forced
            changed = state != prev
            if changed:
                self._state, self._since = state, now
                self.seq += 1
                self._cond.notify_all()
        if changed:
            for fn in list(self.listeners):
                fn(prev, state, now)
        return state


def _no_network(url, timeout):
    raise AssertionError("test touched the network")


def _quiet_spawn():
    """A boss spawn with no reset (00:00 UTC), maintenance (Thu) or Hot Time near it."""
    for row in bosses.next_spawns(T(2026, 10, 6, 0), 40):
        at = dt.datetime.fromisoformat(row["at_utc"])
        if at.weekday() in (2, 3) or not 3 <= at.hour <= 22:
            continue
        return at.timestamp()
    raise AssertionError("no quiet spawn")


@pytest.fixture()
def csrv(tmp_path):
    at = _quiet_spawn()
    clk = Clock(at - 60 * MIN)
    w = FakeWatch(clk)
    cfg = tmp_path / "local.json"
    cfg.write_text('{"overlay": {"idle_min": 240}}\n', encoding="utf-8")  # no idle mid-test
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          sse_interval=0.05, market_seed=[],
                          market_client=market.ArshaClient(fetch=_no_network,
                                                           cache_dir=tmp_path / "cache"),
                          profile_cfg={}, game_watch=w, config_path=cfg, context_clock=clk,
                          leveling_clock=clk, bosses_clock=clk)
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield s, w, clk, at, cfg
    s.shutdown()
    s.server_close()


def _get(s, path):
    c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=5)
    c.request("GET", path)
    r = c.getresponse()
    out = r.read()
    c.close()
    return r.status, json.loads(out)


def _next_ctx(r, deadline=3.0):
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        line = r.fp.readline()
        if line.startswith(b"event: overlay_context"):
            return json.loads(r.fp.readline()[6:])
    return None


def test_server_route(csrv):
    s, w, clk, at, _ = csrv
    w.forced = "not_running"
    w.poll()
    st, doc = _get(s, "/api/overlay/context")
    assert st == 200 and doc["context"] == "closed" and doc["hidden"] is True
    assert set(doc) == {"context", "active", "widgets", "hidden", "auto", "maint_at", "updated"}


def test_server_sse_timeline_no_settings_write(csrv):
    s, w, clk, at, cfg = csrv
    before = cfg.read_bytes()
    w.forced = "not_running"
    w.poll()
    c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=5)
    c.request("GET", "/events")
    r = c.getresponse()
    assert b"heartbeat" in r.fp.readline()
    got = []
    w.forced = "logged_in"
    w.poll()
    got.append(_next_ctx(r))
    clk.t = at - 10 * MIN  # past the cache window; the SSE loop re-derives
    got.append(_next_ctx(r))
    clk.t = at + 5 * MIN
    s.context.invalidate()
    got.append(_next_ctx(r))
    w.forced = "not_running"
    w.poll()
    got.append(_next_ctx(r))
    c.close()
    assert all(g is not None for g in got), got
    assert [g["context"] for g in got] == ["in_game", "boss_soon", "in_game", "closed"]
    assert got[1]["widgets"][0] == "worldBoss"
    assert got[3]["hidden"] is True and got[3]["widgets"] == []
    assert cfg.read_bytes() == before


def test_server_settings_pin_applies_live(csrv):
    s, w, clk, at, cfg = csrv
    w.forced = "logged_in"
    w.poll()
    assert "dice" not in _get(s, "/api/overlay/context")[1]["widgets"]
    body = json.dumps({"set": {"overlay.mode.dice": "pin"}}).encode("utf-8")
    c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=5)
    c.request("POST", "/api/settings", body=body,
              headers={"Content-Type": "application/json", "Content-Length": str(len(body))})
    assert c.getresponse().status == 200
    c.close()
    assert _get(s, "/api/overlay/context")[1]["widgets"][0] == "dice"
