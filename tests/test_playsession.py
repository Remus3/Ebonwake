"""Plan 062: auto play-session. A GameWatch listener opens a play session (and
an auto grind session) on logged_in and closes it once not_running /
disconnected has held for grace_s, at the last moment seen logged in. State
sequences are driven by hand with an injected clock; nothing reads the game."""

import datetime as dt
import http.client
import json
import threading
import time

import pytest

from server.ew import app as ewapp
from server.ew import gamewatch, grind, market, playsession, settings, summary
from server.ew.store import Store

UTC = dt.timezone.utc
T10 = dt.datetime(2026, 10, 5, 10, 0, 0, tzinfo=UTC).timestamp()
MIN = 60.0


class Clock:
    def __init__(self, t=T10):
        self.t = t

    def __call__(self):
        return self.t


def iso(ts):
    return dt.datetime.fromtimestamp(ts, UTC).isoformat()


class Rig:
    """Store + grind + summary + play session, fed state changes like GameWatch."""

    def __init__(self, tmp_path, auto=True, grace=120, spot=True):
        self.clock = Clock()
        self.store = Store(tmp_path / "store")
        self.gs = grind.GrindService(self.store, clock=self.clock)
        if spot:
            self.gs.add_spot("Orc Camp")
        self.ss = summary.SummaryService(self.store, self.gs, clock=self.clock)
        self.conf = {"auto": auto, "grace": grace}
        self.bumps = 0
        self.ps = playsession.PlaySession(
            self.store, self.gs, self.ss,
            config=lambda: (self.conf["auto"], self.conf["grace"]),
            clock=self.clock, on_change=self._bump)
        self.state = None

    def _bump(self):
        self.bumps += 1

    def go(self, new, at):
        """One GameWatch change at `at`, listeners in app order, then a tick."""
        self.clock.t = at
        prev, self.state = self.state, new
        self.ss.on_game(prev, new, at)
        self.ps.on_game(prev, new, at)
        self.ps.tick(new, at)

    def tick(self, at):
        self.clock.t = at
        self.ps.tick(self.state, at)

    def sessions(self):
        return self.gs.view()["sessions"]


@pytest.fixture()
def rig(tmp_path):
    return Rig(tmp_path)


# --- acceptance ------------------------------------------------------------------

def test_acceptance_login_to_exit_one_closed_session_no_prompt(rig):
    rig.go("not_running", T10 - 5 * MIN)
    rig.go("logged_in", T10)
    v = rig.gs.view()
    assert v["active"]["auto"] is True and v["session"]["auto"] is True
    assert rig.ps.view()["open"]["start"] == iso(T10)
    rig.go("not_running", T10 + 90 * MIN)
    # Inside the grace: still open, and no pending-stop prompt for an auto session.
    assert rig.gs.view()["pending_stop"] is None
    assert rig.ps.view()["open"] is not None
    rig.tick(T10 + 92 * MIN)
    v = rig.gs.view()
    assert v["active"] is None and v["pending_stop"] is None
    assert len(v["sessions"]) == 1
    s = v["sessions"][0]
    assert s["started"] == iso(T10) and s["minutes"] == 90 and s["silver"] == 0
    last = rig.ps.view()["last"]
    assert last["start"] == iso(T10) and last["end"] == iso(T10 + 90 * MIN)
    summ = rig.ss.view()["session"]
    assert summ["since"] == iso(T10) and summ["until"] == iso(T10 + 90 * MIN)
    assert summ["grind"]["sessions"] == 1 and summ["grind"]["minutes"] == 90
    assert rig.ps.event() == {"state": "closed", "id": last["id"]}


def test_first_login_with_no_history_uses_last_spot_or_unspecified(tmp_path):
    r = Rig(tmp_path, spot=False)
    r.go("logged_in", T10)
    v = r.gs.view()
    assert v["active"]["spot"] == "unspecified"
    assert any(s["id"] == "unspecified" for s in v["spots"])


def test_last_used_spot_is_reused(rig):
    rig.gs.add_spot("Hystria")
    rig.gs.log({"spot": "hystria", "minutes": 30, "silver": 1, "trash": 0})
    rig.go("logged_in", T10)
    assert rig.gs.view()["active"]["spot"] == "hystria"


# --- grace ------------------------------------------------------------------------

def test_disconnect_inside_grace_continues(rig):
    rig.go("logged_in", T10)
    pid = rig.ps.view()["open"]["id"]
    rig.go("disconnected", T10 + 30 * MIN)
    rig.tick(T10 + 31 * MIN)
    rig.go("logged_in", T10 + 31 * MIN)
    rig.tick(T10 + 40 * MIN)
    o = rig.ps.view()["open"]
    assert o is not None and o["id"] == pid and rig.sessions() == []


def test_disconnect_held_past_grace_closes_at_disconnect(rig):
    rig.go("logged_in", T10)
    rig.go("disconnected", T10 + 30 * MIN)
    rig.tick(T10 + 31 * MIN)
    assert rig.ps.view()["open"] is not None
    rig.tick(T10 + 32 * MIN)
    assert rig.ps.view()["open"] is None
    assert rig.sessions()[0]["minutes"] == 30


def test_relaunch_inside_grace_continues_same_session(rig):
    rig.go("logged_in", T10)
    pid = rig.ps.view()["open"]["id"]
    rig.go("not_running", T10 + 60 * MIN)
    rig.go("running", T10 + 61 * MIN)
    rig.go("logged_in", T10 + 61.5 * MIN)
    rig.tick(T10 + 70 * MIN)
    assert rig.ps.view()["open"]["id"] == pid
    rig.go("not_running", T10 + 120 * MIN)
    rig.tick(T10 + 123 * MIN)
    assert len(rig.sessions()) == 1 and rig.sessions()[0]["minutes"] == 120


def test_relaunch_after_grace_opens_new_session(rig):
    rig.go("logged_in", T10)
    rig.go("not_running", T10 + 60 * MIN)
    rig.tick(T10 + 63 * MIN)
    rig.go("logged_in", T10 + 70 * MIN)
    o = rig.ps.view()["open"]
    assert o is not None and o["start"] == iso(T10 + 70 * MIN)
    assert rig.ps.event()["state"] == "open"
    assert len(rig.sessions()) == 1


def test_grace_setting_is_honoured(tmp_path):
    r = Rig(tmp_path, grace=600)
    r.go("logged_in", T10)
    r.go("not_running", T10 + 10 * MIN)
    r.tick(T10 + 19 * MIN)
    assert r.ps.view()["open"] is not None
    r.tick(T10 + 20 * MIN)
    assert r.ps.view()["open"] is None


def test_logout_to_character_select_does_not_close(rig):
    rig.go("logged_in", T10)
    rig.go("running", T10 + 10 * MIN)
    rig.tick(T10 + 30 * MIN)
    assert rig.ps.view()["open"] is not None


def test_exit_after_character_select_closes_at_leaving_logged_in(rig):
    rig.go("logged_in", T10)
    rig.go("running", T10 + 10 * MIN)
    rig.go("not_running", T10 + 15 * MIN)
    rig.tick(T10 + 18 * MIN)
    assert rig.sessions()[0]["minutes"] == 10


# --- restart resume (DiceClock rule) ------------------------------------------------

def test_restart_resume_when_first_poll_logged_in(rig, tmp_path):
    rig.go("logged_in", T10)
    pid = rig.ps.view()["open"]["id"]
    rig.tick(T10 + 5 * MIN)
    # EW restarts: fresh services over the same store; first poll still logged in.
    r2 = Rig.__new__(Rig)
    r2.clock, r2.store, r2.conf, r2.bumps = Clock(T10 + 20 * MIN), rig.store, rig.conf, 0
    r2.gs = grind.GrindService(r2.store, clock=r2.clock)
    r2.ss = summary.SummaryService(r2.store, r2.gs, clock=r2.clock)
    r2.ps = playsession.PlaySession(r2.store, r2.gs, r2.ss, config=lambda: (True, 120),
                                    clock=r2.clock, on_change=r2._bump)
    r2.state = None
    r2.go("logged_in", T10 + 20 * MIN)
    assert r2.ps.view()["open"]["id"] == pid
    assert r2.gs.view()["active"]["started"] == iso(T10)
    r2.go("not_running", T10 + 50 * MIN)
    r2.tick(T10 + 53 * MIN)
    assert r2.sessions()[0]["minutes"] == 50


def test_restart_with_game_closed_closes_at_last_seen(rig):
    rig.go("logged_in", T10)
    rig.tick(T10 + 5 * MIN)  # seen refreshed (>= 60 s since the last write)
    r2 = Rig.__new__(Rig)
    r2.clock, r2.store, r2.conf, r2.bumps = Clock(T10 + 300 * MIN), rig.store, rig.conf, 0
    r2.gs = grind.GrindService(r2.store, clock=r2.clock)
    r2.ss = summary.SummaryService(r2.store, r2.gs, clock=r2.clock)
    r2.ps = playsession.PlaySession(r2.store, r2.gs, r2.ss, config=lambda: (True, 120),
                                    clock=r2.clock, on_change=r2._bump)
    r2.state = None
    r2.go("not_running", T10 + 300 * MIN)
    assert r2.ps.view()["open"] is None
    s = r2.sessions()
    assert len(s) == 1 and s[0]["minutes"] == 5


# --- manual override ------------------------------------------------------------------

def test_manual_stop_suppresses_auto_open_until_next_login(rig):
    rig.go("logged_in", T10)
    rig.clock.t = T10 + 20 * MIN
    rig.gs.stop({"silver": 5, "trash": 1})
    rig.ps.manual_stop()
    assert rig.ps.view()["open"] is None and rig.ps.view()["suppressed"] is True
    s = rig.sessions()
    assert len(s) == 1 and s[0]["silver"] == 5 and s[0]["minutes"] == 20
    # Reconnect in the same run: no auto-open.
    rig.go("disconnected", T10 + 30 * MIN)
    rig.go("logged_in", T10 + 31 * MIN)
    assert rig.ps.view()["open"] is None and rig.gs.view()["active"] is None
    # Game exit then a fresh login: auto-open again.
    rig.go("not_running", T10 + 40 * MIN)
    rig.go("logged_in", T10 + 50 * MIN)
    assert rig.ps.view()["open"] is not None and rig.gs.view()["active"]["auto"] is True


def test_manual_stop_with_game_down_does_not_suppress(rig):
    rig.go("logged_in", T10)
    rig.go("not_running", T10 + 30 * MIN)
    rig.clock.t = T10 + 31 * MIN
    rig.gs.stop({"silver": 0, "trash": 0})
    rig.ps.manual_stop()
    assert rig.ps.view()["suppressed"] is False
    rig.go("logged_in", T10 + 40 * MIN)
    assert rig.ps.view()["open"] is not None


def test_manual_session_kept_and_keeps_pending_prompt(rig):
    rig.gs.start("orc-camp")
    rig.go("logged_in", T10)
    a = rig.gs.view()["active"]
    assert a["auto"] is False and rig.gs.view()["session"]["auto"] is False
    rig.go("not_running", T10 + 30 * MIN)
    assert rig.gs.view()["pending_stop"] is not None  # plan 046 prompt kept
    rig.tick(T10 + 33 * MIN)
    # Play session closes; the manual grind session is left for the operator.
    assert rig.ps.view()["open"] is None
    assert rig.gs.view()["active"] is not None and rig.sessions() == []


def test_manual_stop_without_play_session_is_noop(rig):
    rig.ps.manual_stop()
    assert rig.ps.view() == {"open": None, "last": None, "suppressed": False}


def test_auto_session_off_never_opens(tmp_path):
    r = Rig(tmp_path, auto=False)
    r.go("logged_in", T10)
    assert r.ps.view()["open"] is None and r.gs.view()["active"] is None


def test_bad_grace_falls_back_to_default(tmp_path):
    r = Rig(tmp_path, grace="x")
    r.go("logged_in", T10)
    r.go("not_running", T10 + 10 * MIN)
    r.tick(T10 + 11 * MIN)
    assert r.ps.view()["open"] is not None
    r.tick(T10 + 12 * MIN)
    assert r.ps.view()["open"] is None


def test_change_hook_fires_on_open_and_close(rig):
    rig.go("logged_in", T10)
    assert rig.bumps == 1
    rig.go("not_running", T10 + MIN)
    rig.tick(T10 + 4 * MIN)
    assert rig.bumps == 2


def test_corrupt_store_degrades(rig):
    rig.store.put("playsession", {"open": {"id": 5, "start": "nope"}, "next_id": "x",
                                  "suppressed": "yes"})
    assert rig.ps.view() == {"open": None, "last": None, "suppressed": False}
    rig.go("logged_in", T10)
    assert rig.ps.view()["open"]["id"] == "p1"


def test_auto_stop_ignores_other_sessions(rig):
    rig.gs.start("orc-camp")
    started = rig.gs.view()["active"]["started"]
    assert rig.gs.auto_stop(started, iso(T10 + MIN)) is False  # manual: never auto-stopped
    assert rig.gs.view()["active"] is not None


# --- settings ---------------------------------------------------------------------------

def test_settings_allowlist():
    # Plan 080: fixed values (a config value is a 24 h incident switch only).
    d = settings.fixed()
    assert d["play.auto_session"] is True and d["play.grace_s"] == 120
    assert "play.grace_s" not in settings.SPEC and "play.auto_session" not in settings.SPEC
    ok = settings.FIXED["play.grace_s"][0]
    assert ok(60) and ok(600) and not ok(59) and not ok(601) and not ok(True)
    assert not settings.FIXED["play.auto_session"][0](1)


# --- server: GET /api/grind session.auto + SSE play_session ----------------------------

class FakeWatch(gamewatch.GameWatch):
    """Unconfigured GameWatch whose state is set by the test."""

    def __init__(self):
        super().__init__(None, None, tasklist=lambda: False)
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


@pytest.fixture()
def psrv(tmp_path):
    w = FakeWatch()
    cfg = tmp_path / "local.json"
    cfg.write_text("{}", encoding="utf-8")
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          sse_interval=0.05, market_seed=[],
                          market_client=market.ArshaClient(fetch=_no_network,
                                                           cache_dir=tmp_path / "cache"),
                          profile_cfg={}, game_watch=w, config_path=cfg)
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield s, w
    s.shutdown()
    s.server_close()


def _get(s, path):
    c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=5)
    c.request("GET", path)
    r = c.getresponse()
    out = r.read()
    c.close()
    return r.status, json.loads(out)


def _post(s, path, body):
    c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=5)
    data = json.dumps(body).encode("utf-8")
    c.request("POST", path, body=data, headers={"Content-Type": "application/json",
                                                "Content-Length": str(len(data))})
    r = c.getresponse()
    out = r.read()
    c.close()
    return r.status, json.loads(out)


def test_server_get_grind_session_auto(psrv):
    s, w = psrv
    assert _get(s, "/api/grind")[1]["session"]["auto"] is False
    w.forced = "logged_in"
    w.poll()
    st, doc = _get(s, "/api/grind")
    assert st == 200 and doc["session"]["auto"] is True
    assert doc["session"]["play"]["state"] == "open"


def test_server_manual_stop_suppresses(psrv):
    s, w = psrv
    w.forced = "logged_in"
    w.poll()
    st, _ = _post(s, "/api/grind", {"stop": {"silver": 1, "trash": 0}})
    assert st == 200
    assert s.play.view()["suppressed"] is True and s.play.view()["open"] is None


def test_server_sse_play_session_event(psrv):
    s, w = psrv
    w.forced = "not_running"
    w.poll()
    c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=5)
    c.request("GET", "/events")
    r = c.getresponse()
    assert b"heartbeat" in r.fp.readline()
    w.forced = "logged_in"
    w.poll()
    got = None
    end = time.monotonic() + 3
    while time.monotonic() < end:
        line = r.fp.readline()
        if line.startswith(b"event: play_session"):
            got = json.loads(r.fp.readline()[6:])
            break
    c.close()
    assert got == {"state": "open", "id": s.play.view()["open"]["id"]}
