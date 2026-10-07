"""Plan 075: login-day reward tracker (qualifying days per event, days left, at risk)."""

import datetime as dt
import json
from pathlib import Path

import pytest

from server.ew import eventnotices, events, logindays, whatnow
from server.ew.store import Store

FIX = Path(__file__).resolve().parent / "fixtures"
UTC = dt.timezone.utc


def ts(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def D(s):
    return dt.date.fromisoformat(s)


class Clock:
    def __init__(self, t):
        self.t = ts(t) if isinstance(t, str) else t

    def __call__(self):
        return self.t


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "store")


# -- interval split + history ---------------------------------------------------

def test_split_at_utc_midnight():
    out = logindays.split_minutes(ts("2026-10-05T23:30:00Z"), ts("2026-10-06T00:45:00Z"))
    assert out == {"2026-10-05": 30.0, "2026-10-06": 45.0}


def test_split_spans_whole_days_and_ignores_reversed():
    out = logindays.split_minutes(ts("2026-10-01T23:00:00Z"), ts("2026-10-03T01:00:00Z"))
    assert out == {"2026-10-01": 60.0, "2026-10-02": 1440.0, "2026-10-03": 60.0}
    assert logindays.split_minutes(ts("2026-10-02T00:00:00Z"), ts("2026-10-01T00:00:00Z")) == {}


def test_trim_keeps_last_120_days():
    dates = {"2026-06-08": 5, "2026-06-09": 5, "2026-10-06": 5, "bad": 3, "2026-10-07": -1}
    out = logindays.trim(dates, D("2026-10-06"))
    assert out == {"2026-06-09": 5, "2026-10-06": 5}  # 2026-06-09 is today - 119


def test_listener_accumulates_and_splits(store):
    clk = Clock("2026-10-05T23:00:00Z")
    ld = logindays.LoginDays(store, clock=clk)
    ld.on_game(None, "running", ts("2026-10-05T22:50:00Z"))
    ld.on_game("running", "logged_in", ts("2026-10-05T23:20:00Z"))
    ld.on_game("logged_in", "running", ts("2026-10-06T00:10:00Z"))
    assert store.get("logindays")["dates"] == {"2026-10-05": 40.0, "2026-10-06": 10.0}
    assert store.get("logindays")["open"] is None


def test_live_open_interval_counts_in_history(store):
    clk = Clock("2026-10-06T00:30:00Z")
    ld = logindays.LoginDays(store, clock=clk)
    ld.on_game("running", "logged_in", ts("2026-10-06T00:00:00Z"))
    h = ld.history()
    assert h["dates"]["2026-10-06"] == 30.0
    assert h["logged_in"] is True


def test_orphan_closes_at_last_seen_poll(store):
    clk = Clock("2026-10-06T10:00:00Z")
    ld = logindays.LoginDays(store, clock=clk)
    ld.on_game("running", "logged_in", ts("2026-10-06T10:00:00Z"))
    clk.t = ts("2026-10-06T10:05:00Z")
    ld.tick("logged_in", clk.t)
    clk.t = ts("2026-10-06T10:20:00Z")
    ld.tick("logged_in", clk.t)
    # EW dies; the game closes; EW restarts hours later and first sees not_running.
    ld2 = logindays.LoginDays(store, clock=Clock("2026-10-06T15:00:00Z"))
    ld2.on_game(None, "not_running", ts("2026-10-06T15:00:00Z"))
    assert store.get("logindays")["dates"] == {"2026-10-06": 20.0}


def test_restart_still_logged_in_resumes(store):
    ld = logindays.LoginDays(store, clock=Clock("2026-10-06T10:00:00Z"))
    ld.on_game("running", "logged_in", ts("2026-10-06T10:00:00Z"))
    ld2 = logindays.LoginDays(store, clock=Clock("2026-10-06T10:30:00Z"))
    ld2.on_game(None, "logged_in", ts("2026-10-06T10:30:00Z"))
    ld2.on_game("logged_in", "running", ts("2026-10-06T11:00:00Z"))
    assert store.get("logindays")["dates"] == {"2026-10-06": 60.0}


def test_backfill_once_from_stored_play_sessions(store):
    store.put("playsession", {"last": {"id": "p3", "start": "2026-10-04T20:00:00+00:00",
                                       "end": "2026-10-04T21:30:00+00:00", "spot": None,
                                       "auto": True}})
    store.put("grind", {"sessions": [
        {"id": "s1", "spot": "x", "started": "2026-10-02T10:00:00+00:00", "minutes": 45},
        {"id": "s2", "spot": "x", "started": "bad", "minutes": 45}]})
    ld = logindays.LoginDays(store, clock=Clock("2026-10-06T12:00:00Z"))
    h = ld.history()
    assert h["dates"] == {"2026-10-02": 45.0, "2026-10-04": 90.0}
    assert h["since"] == "2026-10-02"
    store.put("grind", {"sessions": [
        {"id": "s3", "spot": "x", "started": "2026-09-30T10:00:00+00:00", "minutes": 45}]})
    assert ld.history()["dates"] == {"2026-10-02": 45.0, "2026-10-04": 90.0}  # once only


def test_since_is_first_run_day_without_history(store):
    ld = logindays.LoginDays(store, clock=Clock("2026-10-06T12:00:00Z"))
    assert ld.history()["since"] == "2026-10-06"


def test_mark_past_day(store):
    ld = logindays.LoginDays(store, clock=Clock("2026-10-06T12:00:00Z"))
    ld.mark({"date": "2026-10-03", "on": True})
    assert ld.history()["marked"] == ["2026-10-03"]
    ld.mark({"date": "2026-10-03", "on": False})
    assert ld.history()["marked"] == []
    for bad in ({"date": "2026-10-06", "on": True}, {"date": "2026-10-07", "on": True},
                {"date": "2026-06-08", "on": True}, {"date": "2026-10-03"},
                {"date": "x", "on": True}, {"date": "2026-10-03", "on": 1}, "2026-10-03"):
        with pytest.raises(ValueError):
            ld.mark(bad)


# -- rule validation --------------------------------------------------------------

def test_validate_rule():
    assert logindays.validate_rule({"days_needed": 14, "min_minutes": 0}) == {
        "days_needed": 14, "min_minutes": 0}
    assert logindays.validate_rule({"days_needed": 14, "min_minutes": 0, "weekend_minutes": 60}) \
        == {"days_needed": 14, "min_minutes": 0, "weekend_minutes": 60}
    assert logindays.validate_rule(None) is None
    for bad in ({"days_needed": 0, "min_minutes": 0}, {"days_needed": 61, "min_minutes": 0},
                {"days_needed": 14, "min_minutes": 601}, {"days_needed": 14},
                {"days_needed": True, "min_minutes": 0}, {"days_needed": 14, "min_minutes": 0,
                                                          "weekend_minutes": -1},
                {"days_needed": 14, "min_minutes": 0, "x": 1}, [14, 0], "14"):
        with pytest.raises(ValueError):
            logindays.validate_rule(bad)
    assert logindays.clean_rule({"days_needed": 99, "min_minutes": 0}) is None


def test_events_store_accepts_login_rule(store):
    ev = events.EventsService(store, clock=Clock("2026-10-06T12:00:00Z"))
    out = ev.add({"kind": "event", "title": "Special Login Reward", "starts": "2026-10-02",
                  "ends": "2026-10-28", "login_rule": {"days_needed": 14, "min_minutes": 0}})
    iid = out["item"]["id"]
    assert out["item"]["login_rule"] == {"days_needed": 14, "min_minutes": 0}
    out = ev.edit({"id": iid, "login_rule": {"days_needed": 7, "min_minutes": 60}})
    assert out["items"][0]["login_rule"] == {"days_needed": 7, "min_minutes": 60}
    with pytest.raises(ValueError):
        ev.edit({"id": iid, "login_rule": {"days_needed": 0, "min_minutes": 0}})
    out = ev.edit({"id": iid, "login_rule": None})
    assert "login_rule" not in out["items"][0]


# -- progress math ------------------------------------------------------------------

RULE14 = {"days_needed": 14, "min_minutes": 0}


def _dates(n, start="2026-10-02", minutes=30):
    d0 = D(start)
    return {(d0 + dt.timedelta(days=i)).isoformat(): minutes for i in range(n)}


def test_credited_counts_window_dates_capped():
    p = logindays.progress({"days_needed": 3, "min_minutes": 0}, D("2026-10-02"), D("2026-10-28"),
                           _dates(6), D("2026-10-21"))
    assert p["credited"] == 3 and p["complete"] is True and p["at_risk"] is False


def test_credited_window_edges():
    dates = {"2026-10-01": 30, "2026-10-02": 30, "2026-10-28": 30, "2026-10-29": 30}
    p = logindays.progress(RULE14, D("2026-10-02"), D("2026-10-28"), dates, D("2026-10-29"))
    assert p["credited"] == 2 and p["days_left"] == 0


def test_min_minutes_rule_and_zero_needs_a_minute():
    dates = {"2026-10-02": 59.9, "2026-10-03": 60, "2026-10-04": 120, "2026-10-05": 0.5}
    p = logindays.progress({"days_needed": 14, "min_minutes": 60}, D("2026-10-02"),
                           D("2026-10-28"), dates, D("2026-10-10"))
    assert p["credited"] == 2
    p = logindays.progress(RULE14, D("2026-10-02"), D("2026-10-28"), dates, D("2026-10-10"))
    assert p["credited"] == 3  # 0.5 min is not a login day


def test_marked_day_credits():
    p = logindays.progress({"days_needed": 14, "min_minutes": 60}, D("2026-10-02"),
                           D("2026-10-28"), {}, D("2026-10-10"), marked=["2026-10-03"])
    assert p["credited"] == 1


@pytest.mark.parametrize("today,left,at_risk,lost", [
    ("2026-10-21", 8, True, False), ("2026-10-22", 7, False, True),
    ("2026-10-20", 9, False, False)])
def test_at_risk_and_lost_boundaries(today, left, at_risk, lost):
    p = logindays.progress(RULE14, D("2026-10-02"), D("2026-10-28"), _dates(6), D(today))
    assert (p["credited"], p["days_left"], p["at_risk"], p["lost"]) == (6, left, at_risk, lost)


def test_today_credited_is_not_at_risk():
    dates = dict(_dates(6), **{"2026-10-21": 30})
    p = logindays.progress(RULE14, D("2026-10-02"), D("2026-10-28"), dates, D("2026-10-21"))
    assert (p["credited"], p["today_done"], p["at_risk"], p["lost"]) == (7, True, False, False)


def test_upcoming_window_days_left_counts_from_start():
    p = logindays.progress(RULE14, D("2026-10-08"), D("2026-11-04"), {}, D("2026-10-06"))
    assert p["days_left"] == 28 and p["at_risk"] is False and p["lost"] is False


def test_weekend_bonus_count():
    dates = {"2026-10-03": 61, "2026-10-04": 30, "2026-10-05": 90, "2026-10-10": 60}
    p = logindays.progress(dict(RULE14, weekend_minutes=60), D("2026-10-02"), D("2026-10-28"),
                           dates, D("2026-10-11"))
    assert p["weekend"] == {"credited": 2, "total": 4, "minutes": 60}
    assert logindays.progress(RULE14, D("2026-10-02"), D("2026-10-28"), dates,
                              D("2026-10-11"))["weekend"] is None


# -- suggestion patterns ----------------------------------------------------------------

def _lines(name):
    return eventnotices._lines((FIX / name).read_text(encoding="utf-8"))


def test_patterns_file_loads():
    pats = logindays.load_patterns()
    assert pats["gate"] and pats["rules"]
    doc = json.loads(logindays.PATTERNS_FILE.read_text(encoding="utf-8"))
    assert "_doc" in doc


def test_suggest_10658():
    s = logindays.suggest_rule(_lines("notice_10658_login.html"))
    assert s == {"days_needed": 14, "min_minutes": 0, "weekend_minutes": 60}


def test_suggest_10619():
    s = logindays.suggest_rule(_lines("notice_10619_login.html"))
    assert s == {"days_needed": None, "min_minutes": 60}


def test_suggest_four_weeks_and_none():
    assert logindays.suggest_rule(["Get a Dream Horse just by logging in for four weeks!"]) == {
        "days_needed": 28, "min_minutes": 0}
    assert logindays.suggest_rule(["Kill 14 days worth of monsters."]) is None
    assert logindays.suggest_rule([]) is None


def test_parse_notice_carries_login_suggestion():
    page = (FIX / "notice_10619_login.html").read_text(encoding="utf-8")
    d = eventnotices.parse_notice(page, "[Donghwa's Gift] 60-Min Login", 2026, 9)
    assert d["login"] == {"days_needed": None, "min_minutes": 60}
    entry = dict(d, stamp=None, fetched_at=1.0)
    assert eventnotices._clean_detail(entry)["login"] == {"days_needed": None, "min_minutes": 60}
    assert eventnotices._clean_detail(dict(entry, login={"days_needed": 0}))["login"] is None


# -- rows, acceptance, What now ------------------------------------------------------------

def _setup(store, now, today_minutes=None):
    clk = Clock(now)
    ev = events.EventsService(store, clock=clk)
    ev.add({"kind": "event", "title": "Special Login Reward", "starts": "2026-10-02",
            "ends": "2026-10-28", "url": "https://www.naeu.playblackdesert.com/x?groupContentNo=10658",
            "login_rule": RULE14})
    dates = _dates(6, minutes=45)
    if today_minutes is not None:
        dates[now[:10]] = today_minutes
    store.put("logindays", {"dates": dates, "marked": [], "open": None, "seen": None,
                            "since": "2026-10-01", "backfilled": True})
    return ev, logindays.LoginDays(store, clock=clk)


def test_acceptance_at_risk_row_and_whatnow(store):
    ev, ld = _setup(store, "2026-10-21T10:00:00Z")
    view = ld.rows(ev.view()["items"])
    row = view["rows"][0]
    assert (row["credited"], row["needed"], row["days_left"], row["at_risk"]) == (6, 14, 8, True)
    assert logindays.label(row) == "Login days 6/14 - 8 days left"
    now = dt.datetime(2026, 10, 21, 10, tzinfo=UTC)
    cands = whatnow.login_days(view, now)
    assert [c["text"] for c in cands] == ["Log in today for Special Login Reward"]
    assert cands[0]["source"] == "deadline" and cands[0]["due"] == "2026-10-22T00:00:00+00:00"
    ranked = whatnow.collect({"logins": lambda: view}, now, whatnow.load_weights())
    assert ranked["top"]["text"] == "Log in today for Special Login Reward"


def test_acceptance_today_credited_no_whatnow_row(store):
    ev, ld = _setup(store, "2026-10-21T10:00:00Z", today_minutes=5)
    view = ld.rows(ev.view()["items"])
    assert view["rows"][0]["today_done"] is True
    assert whatnow.login_days(view, dt.datetime(2026, 10, 21, 10, tzinfo=UTC)) == []


def test_acceptance_60_min_rule(store):
    ev, ld = _setup(store, "2026-10-21T10:00:00Z")
    iid = ev.view()["items"][0]["id"]
    ev.edit({"id": iid, "login_rule": {"days_needed": 14, "min_minutes": 60}})
    d = store.get("logindays")
    d["dates"]["2026-10-03"] = 60
    d["dates"]["2026-10-04"] = 75
    store.put("logindays", d)
    row = ld.rows(ev.view()["items"])["rows"][0]
    assert row["credited"] == 2 and row["min_minutes"] == 60


def test_rows_skip_untracked_done_and_expired(store):
    ev, ld = _setup(store, "2026-10-21T10:00:00Z")
    ev.add({"kind": "event", "title": "Plain", "ends": "2026-10-28"})
    ev.add({"kind": "event", "title": "Gone", "starts": "2026-10-01", "ends": "2026-10-05",
            "login_rule": RULE14})
    out = ev.add({"kind": "event", "title": "Claimed", "ends": "2026-10-28",
                  "login_rule": RULE14})
    ev.done({"id": out["item"]["id"], "done": True})
    assert [r["title"] for r in ld.rows(ev.view()["items"])["rows"]] == ["Special Login Reward"]


def test_rows_counting_since_when_history_starts_late(store):
    ev, ld = _setup(store, "2026-10-21T10:00:00Z")
    d = store.get("logindays")
    d["since"] = "2026-10-05"
    store.put("logindays", d)
    assert ld.rows(ev.view()["items"])["rows"][0]["counting_since"] == "2026-10-05"


def test_route_login_days_block_and_mark(tmp_path):
    import http.client
    import threading

    from server.ew import app as ewapp
    clk = Clock("2026-10-21T10:00:00Z")
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          sse_interval=0.05, market_seed=[], events_clock=clk, today_clock=clk)
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()

    def req(method, body=None):
        c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=5)
        c.request(method, "/api/events", body=None if body is None else json.dumps(body).encode(),
                  headers={"Content-Type": "application/json"})
        r = c.getresponse()
        out = (r.status, json.loads(r.read()))
        c.close()
        return out

    try:
        st, doc = req("POST", {"add": {"kind": "event", "title": "Special Login Reward",
                                       "starts": "2026-10-02", "ends": "2026-10-28",
                                       "login_rule": RULE14}})
        assert st == 200 and doc["login_days"]["rows"][0]["credited"] == 0
        st, doc = req("POST", {"login_mark": {"date": "2026-10-20", "on": True}})
        assert st == 200 and doc["login_days"]["rows"][0]["credited"] == 1
        st, doc = req("POST", {"login_mark": {"date": "2026-10-21", "on": True}})
        assert st == 400
        wn = s.whatnow.view()  # lost (13 more needed, 8 days left): no row
        assert "logins" not in wn["errors"]
        assert not any(r["text"].startswith("Log in today")
                       for r in [wn["top"] or {"text": ""}] + wn["next"])
        st, doc = req("GET")
        assert st == 200 and doc["login_days"]["rows"][0]["lost"] is True
    finally:
        s.shutdown()
        s.server_close()


def test_rows_suggest_from_notice_rule(store):
    clk = Clock("2026-10-06T12:00:00Z")
    ev = events.EventsService(store, clock=clk)
    url = "https://www.naeu.playblackdesert.com/x?groupContentNo=10619"
    ev.add({"kind": "event", "title": "60-Min Login", "starts": "2026-10-06",
            "ends": "2026-10-12", "url": url})
    ld = logindays.LoginDays(store, clock=clk)
    view = ld.rows(ev.view()["items"], {url: {"days_needed": None, "min_minutes": 60}})
    assert view["rows"] == []
    s = view["suggest"][0]
    assert s["rule"] == {"days_needed": 7, "min_minutes": 60}
    assert s["label"] == "track logins? (7 days)"
