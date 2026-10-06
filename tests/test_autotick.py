"""Plan 068: auto-tick inferable Today rows (login, logged minutes), the dice
"ready" mark, the undo block until the next reset, and the boss-shot suggestion.

Signals only: the plan 008 session-log state, screenshot file times and the
clock. Nothing is read from or sent to the game.
"""

import datetime as dt

import pytest

from server.ew import app as ewapp
from server.ew import autotick, bosses, gamewatch, market, settings, today
from server.ew.store import Store

UTC = dt.timezone.utc


def T(*a):
    return dt.datetime(*a, tzinfo=UTC)


def ts(*a):
    return T(*a).timestamp()


class Clock:
    def __init__(self, when):
        self.t = when.timestamp()

    def __call__(self):
        return self.t

    def set(self, when):
        self.t = when.timestamp()


class FakeGame:
    def __init__(self):
        self.listeners, self.pollers = [], []
        self.state, self.shots = "not_running", []

    def view(self):
        return {"state": self.state, "screenshots": [dict(s) for s in self.shots]}


def _shot(name, when):
    return {"name": name, "size": 1, "mtime": when.replace(microsecond=0).isoformat()}


def _item(v, iid):
    return next(i for i in v["items"] if i["id"] == iid)


def _rig(tmp_path, when, enabled=True):
    clk = Clock(when)
    store = Store(tmp_path / "store")
    svc = today.TodayService(store, clock=clk)
    rule, grants, verified = today.dice_preset()
    dice = gamewatch.DiceClock(store, rule, grants, verified=verified, clock=clk)
    game = FakeGame()
    changes = []
    at = autotick.AutoTick(store, svc, dice, game, table=bosses.load_table(),
                           settings=lambda: {"checklist.auto": enabled}, clock=clk,
                           on_change=lambda: changes.append(1))
    return clk, svc, dice, game, at, changes


def _login(dice, at, when):
    dice.on_game("running", "logged_in", when.timestamp())
    at.on_game("running", "logged_in", when.timestamp())


def _logout(dice, at, when):
    dice.on_game("logged_in", "running", when.timestamp())
    at.on_game("logged_in", "running", when.timestamp())


# --- rules are data -----------------------------------------------------------------

@pytest.mark.parametrize("rule, want", [
    ("login", ("login", None)),
    ("logged_minutes:30", ("logged_minutes", 30)),
    ("ready_minutes:60", ("ready_minutes", 60)),
    ("logged_minutes:1440", ("logged_minutes", 1440)),
])
def test_parse_auto_ok(rule, want):
    assert today.parse_auto(rule) == want


@pytest.mark.parametrize("rule", [
    "", "Login", "login ", "logged_minutes", "logged_minutes:0", "logged_minutes:1441",
    "logged_minutes:-1", "logged_minutes:3.5", "logged_minutes:030", "dice", 7, None, True,
])
def test_parse_auto_bad(rule):
    with pytest.raises(ValueError):
        today.parse_auto(rule)


def test_seed_rows_carry_rules(tmp_path):
    svc = today.TodayService(Store(tmp_path / "store"), clock=Clock(T(2026, 10, 4, 12)))
    v = svc.view()
    assert _item(v, "attendance-reward")["auto"] == "login"
    assert _item(v, "black-spirits-adventure-dice")["auto"] == "ready_minutes:60"
    assert "auto" not in _item(v, "barter-run")


def test_existing_store_gets_seed_rules_once(tmp_path):
    store = Store(tmp_path / "store")
    store.put("today", {"items": [
        {"id": "attendance-reward", "title": "Attendance reward", "kind": "daily",
         "until": None, "order": 0},
        {"id": "mine", "title": "Mine", "kind": "daily", "until": None, "order": 1}],
        "ticks": {}})
    svc = today.TodayService(store, clock=Clock(T(2026, 10, 4, 12)))
    assert _item(svc.view(), "attendance-reward")["auto"] == "login"
    assert "auto" not in _item(svc.view(), "mine")


def test_add_accepts_rule_and_refuses_bad(tmp_path):
    svc = today.TodayService(Store(tmp_path / "store"), clock=Clock(T(2026, 10, 4, 12)))
    v = svc.add({"title": "Loyalty", "kind": "daily", "auto": "logged_minutes:15"})
    assert _item(v, "loyalty")["auto"] == "logged_minutes:15"
    with pytest.raises(ValueError):
        svc.add({"title": "Bad", "kind": "daily", "auto": "always"})
    with pytest.raises(ValueError):
        svc.add({"title": "Ev", "kind": "event", "until": "2026-10-09", "auto": "login"})


def test_corrupt_stored_rule_degrades_to_none(tmp_path):
    store = Store(tmp_path / "store")
    store.put("today", {"items": [{"id": "x", "title": "X", "kind": "daily", "until": None,
                                   "order": 0, "auto": "explode"}], "ticks": {}})
    svc = today.TodayService(store, clock=Clock(T(2026, 10, 4, 12)))
    assert set(_item(svc.view(), "x")) == {"id", "title", "kind", "until", "done", "ticked_at"}


# --- login rule ---------------------------------------------------------------------

def test_login_after_reset_ticks_once(tmp_path):
    clk, svc, dice, game, at, changes = _rig(tmp_path, T(2026, 10, 4, 9))
    _login(dice, at, T(2026, 10, 4, 9))
    row = _item(svc.view(), "attendance-reward")
    assert row["done"] is True and row["by"] == "auto"
    assert row["ticked_at"] == "2026-10-04T09:00:00+00:00"
    assert changes == [1]
    _logout(dice, at, T(2026, 10, 4, 10))
    clk.set(T(2026, 10, 4, 11))
    _login(dice, at, T(2026, 10, 4, 11))
    row = _item(svc.view(), "attendance-reward")
    assert row["ticked_at"] == "2026-10-04T09:00:00+00:00"  # first login only
    assert changes == [1]
    assert _item(svc.view(), "barter-run")["done"] is False  # no rule, never auto


def test_login_before_reset_does_not_carry_over(tmp_path):
    clk, svc, dice, game, at, _ = _rig(tmp_path, T(2026, 10, 3, 23, 50))
    _login(dice, at, T(2026, 10, 3, 23, 50))
    _logout(dice, at, T(2026, 10, 3, 23, 55))
    clk.set(T(2026, 10, 4, 0, 30))
    row = _item(svc.view(), "attendance-reward")
    assert row["done"] is False and row["by"] is None
    at.evaluate()  # not logged in now: nothing to infer
    assert _item(svc.view(), "attendance-reward")["done"] is False
    clk.set(T(2026, 10, 4, 8))
    _login(dice, at, T(2026, 10, 4, 8))
    assert _item(svc.view(), "attendance-reward")["done"] is True


def test_session_spanning_reset_ticks_on_first_poll_after_reset(tmp_path):
    clk, svc, dice, game, at, _ = _rig(tmp_path, T(2026, 10, 3, 23, 50))
    _login(dice, at, T(2026, 10, 3, 23, 50))
    game.state = "logged_in"
    clk.set(T(2026, 10, 4, 0, 1))
    at.on_poll("logged_in", clk())
    row = _item(svc.view(), "attendance-reward")
    assert row["done"] is True and row["ticked_at"] == "2026-10-04T00:01:00+00:00"


def test_manual_tick_is_not_auto(tmp_path):
    clk, svc, dice, game, at, _ = _rig(tmp_path, T(2026, 10, 4, 9))
    svc.tick("attendance-reward")
    _login(dice, at, T(2026, 10, 4, 9, 5))
    row = _item(svc.view(), "attendance-reward")
    assert row["by"] is None and row["ticked_at"] == "2026-10-04T09:00:00+00:00"


# --- undo -------------------------------------------------------------------------

def test_undo_blocks_retick_until_reset(tmp_path):
    clk, svc, dice, game, at, _ = _rig(tmp_path, T(2026, 10, 4, 9))
    _login(dice, at, T(2026, 10, 4, 9))
    clk.set(T(2026, 10, 4, 9, 1))
    row = _item(svc.untick("attendance-reward"), "attendance-reward")
    assert row["done"] is False and row["auto_blocked"] is True
    _logout(dice, at, T(2026, 10, 4, 9, 2))
    clk.set(T(2026, 10, 4, 10))
    _login(dice, at, T(2026, 10, 4, 10))
    game.state = "logged_in"
    at.evaluate()
    assert _item(svc.view(), "attendance-reward")["done"] is False
    # next reset lifts the block
    clk.set(T(2026, 10, 5, 0, 0, 30))
    at.evaluate()
    row = _item(svc.view(), "attendance-reward")
    assert row["done"] is True and row["by"] == "auto" and row["auto_blocked"] is False


def test_operator_can_still_tick_a_blocked_row(tmp_path):
    clk, svc, dice, game, at, _ = _rig(tmp_path, T(2026, 10, 4, 9))
    _login(dice, at, T(2026, 10, 4, 9))
    svc.untick("attendance-reward")
    row = _item(svc.tick("attendance-reward"), "attendance-reward")
    assert row["done"] is True and row["by"] is None


# --- logged minutes ------------------------------------------------------------------

def test_minutes_threshold_ticks_and_dice_ready(tmp_path):
    clk, svc, dice, game, at, _ = _rig(tmp_path, T(2026, 10, 4, 6))
    svc.add({"title": "Loyalty", "kind": "daily", "auto": "logged_minutes:30"})
    _login(dice, at, T(2026, 10, 4, 6))
    game.state = "logged_in"
    clk.set(T(2026, 10, 4, 6, 29, 59))
    at.evaluate()
    v = at.decorate(svc.view())
    assert _item(v, "loyalty")["done"] is False
    assert _item(v, "black-spirits-adventure-dice")["ready"] is False
    clk.set(T(2026, 10, 4, 6, 30))
    at.evaluate()
    v = at.decorate(svc.view())
    assert _item(v, "loyalty")["done"] is True and _item(v, "loyalty")["by"] == "auto"
    assert _item(v, "black-spirits-adventure-dice")["ready"] is False  # 60 min rule
    clk.set(T(2026, 10, 4, 7))
    at.evaluate()
    v = at.decorate(svc.view())
    dice_row = _item(v, "black-spirits-adventure-dice")
    assert dice_row["ready"] is True and dice_row["done"] is False  # the roll stays a tick


def test_minutes_from_before_the_row_reset_do_not_count(tmp_path):
    # Dice clock resets 05:00; a midnight row must not inherit yesterday's minutes.
    clk, svc, dice, game, at, _ = _rig(tmp_path, T(2026, 10, 3, 20))
    svc.add({"title": "Loyalty", "kind": "daily", "auto": "logged_minutes:30"})
    _login(dice, at, T(2026, 10, 3, 20))
    _logout(dice, at, T(2026, 10, 3, 22))
    clk.set(T(2026, 10, 4, 1))
    at.evaluate()
    v = at.decorate(svc.view())
    assert _item(v, "loyalty")["done"] is False
    assert _item(v, "black-spirits-adventure-dice")["ready"] is False


def test_disabled_setting_does_nothing(tmp_path):
    clk, svc, dice, game, at, changes = _rig(tmp_path, T(2026, 10, 4, 9), enabled=False)
    _login(dice, at, T(2026, 10, 4, 9))
    game.state = "logged_in"
    game.shots = [_shot("a.jpg", T(2026, 10, 4, 9))]
    clk.set(T(2026, 10, 4, 11))
    at.evaluate()
    at.scan_shots()
    assert _item(svc.view(), "attendance-reward")["done"] is False
    assert at.boss_view({"looted": {}})["suggested"] == {}
    assert changes == []


def test_settings_key_default_true():
    assert settings.defaults()["checklist.auto"] is True
    ok = settings.SPEC["checklist.auto"][0]
    assert ok(True) and ok(False) and not ok(1) and not ok("true")


# --- boss suggestion ------------------------------------------------------------------

def _first_spawn(table, after):
    return bosses.next_spawns(after, 1, table)[0]


@pytest.mark.parametrize("delta_s, hit", [
    (-20 * 60, True), (20 * 60, True), (5 * 60, True), (0, True),
    (-20 * 60 - 1, False), (20 * 60 + 1, False),
])
def test_boss_suggestion_window_edges(delta_s, hit):
    table = bosses.load_table()
    sp = _first_spawn(table, T(2026, 10, 6, 12))
    at = dt.datetime.fromisoformat(sp["at_utc"]).timestamp() + delta_s
    got = autotick.boss_suggestions(at, table)
    want = {(sp["day"], b) for b in sp["bosses"]}
    assert want <= set(got) if hit else not (want & set(got))


def test_shot_while_logged_in_suggests_never_ticks(tmp_path):
    clk, svc, dice, game, at, changes = _rig(tmp_path, T(2026, 10, 6, 12))
    sp = _first_spawn(at.table, T(2026, 10, 6, 12))
    spawn = dt.datetime.fromisoformat(sp["at_utc"])
    _login(dice, at, spawn - dt.timedelta(minutes=30))
    game.state = "logged_in"
    clk.set(spawn + dt.timedelta(minutes=6))
    game.shots = [_shot("boss.jpg", spawn + dt.timedelta(minutes=5))]
    at.scan_shots()
    view = at.boss_view({"looted": {}})
    assert set(view["suggested"][sp["day"]]) == set(sp["bosses"])
    assert changes  # dashboard told
    # never ticked: loot store untouched
    assert at.store.get("bosses").get("looted") in (None, {})
    # once looted, the suggestion is not shown
    view = at.boss_view({"looted": {sp["day"]: list(sp["bosses"])}})
    assert view["suggested"] == {}


def test_shot_while_not_logged_in_is_ignored(tmp_path):
    clk, svc, dice, game, at, _ = _rig(tmp_path, T(2026, 10, 6, 12))
    sp = _first_spawn(at.table, T(2026, 10, 6, 12))
    spawn = dt.datetime.fromisoformat(sp["at_utc"])
    _login(dice, at, spawn - dt.timedelta(minutes=30))
    _logout(dice, at, spawn - dt.timedelta(minutes=10))
    clk.set(spawn + dt.timedelta(minutes=6))
    game.shots = [_shot("boss.jpg", spawn + dt.timedelta(minutes=5))]
    at.scan_shots()
    assert at.boss_view({"looted": {}})["suggested"] == {}


def test_suggestion_survives_restart(tmp_path):
    clk, svc, dice, game, at, _ = _rig(tmp_path, T(2026, 10, 6, 12))
    sp = _first_spawn(at.table, T(2026, 10, 6, 12))
    spawn = dt.datetime.fromisoformat(sp["at_utc"])
    _login(dice, at, spawn - dt.timedelta(minutes=30))
    clk.set(spawn + dt.timedelta(minutes=6))
    game.shots = [_shot("boss.jpg", spawn + dt.timedelta(minutes=5))]
    at.scan_shots()
    at2 = autotick.AutoTick(at.store, svc, dice, FakeGame(), table=at.table,
                            settings=lambda: {}, clock=clk)
    assert set(at2.boss_view({"looted": {}})["suggested"][sp["day"]]) == set(sp["bosses"])


# --- acceptance: one fixture day through the real server wiring --------------------------

class FakeWatch(gamewatch.GameWatch):
    """Unconfigured GameWatch whose state and shots are set by the test."""

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
        for fn in list(self.pollers):
            fn(state, now)
        return state


def _no_network(url, timeout):
    raise AssertionError("test touched the network")


def test_acceptance_fixture_day(tmp_path):
    clk = Clock(T(2026, 10, 6, 5, 30))
    cfg = tmp_path / "local.json"
    cfg.write_text("{}", encoding="utf-8")
    w = FakeWatch(clk)
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          market_seed=[], today_clock=clk, grind_clock=clk, bosses_clock=clk,
                          market_client=market.ArshaClient(fetch=_no_network,
                                                           cache_dir=tmp_path / "cache"),
                          profile_cfg={}, game_watch=w, config_path=cfg)
    try:
        table = s.bosses.table
        w.forced = "logged_in"
        w.poll()
        v = s.today_view()
        att = _item(v, "attendance-reward")
        assert att["done"] is True and att["by"] == "auto"
        assert _item(v, "black-spirits-adventure-dice")["ready"] is False
        clk.set(T(2026, 10, 6, 6, 31))
        w.poll()
        v = s.today_view()
        assert _item(v, "black-spirits-adventure-dice")["ready"] is True
        assert _item(v, "black-spirits-adventure-dice")["done"] is False
        assert _item(v, "attendance-reward")["ticked_at"] == "2026-10-06T05:30:00+00:00"
        sp = _first_spawn(table, T(2026, 10, 6, 6, 31))
        spawn = dt.datetime.fromisoformat(sp["at_utc"])
        clk.set(spawn + dt.timedelta(minutes=6))
        w.poll()
        w._shots = [_shot("ScreenShot_boss.jpg", spawn + dt.timedelta(minutes=5))]
        s.autotick.scan_shots()
        bv = s.bosses_view()
        assert set(bv["suggested"][sp["day"]]) == set(sp["bosses"])
        assert bv["looted"] == {}
    finally:
        s.server_close()
