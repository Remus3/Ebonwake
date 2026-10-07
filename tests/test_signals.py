"""Plan 073: signal health digest - per-signal liveness with one-line fix hints.

Every row is built from fixture timestamps; nothing reads the game, the disk
outside tmp_path or the network.
"""

import http.client
import json
import threading

import pytest

from server.ew import app as ewapp
from server.ew import gamewatch, market, signals

NOW = 1_800_000_000.0
MIN = 60.0


def _game(state="logged_in", line_age=30.0, **kw):
    d = {"configured": True, "log_dir_ok": True, "state": state,
         "line_at": None if line_age is None else NOW - line_age, "since": NOW - 3600,
         "polled_at": NOW - 2}
    d.update(kw)
    return d


def _inputs(**over):
    base = {
        "session_log": _game(),
        "screenshots": {"configured": True, "dir_ok": True, "state": "logged_in",
                        "last_at": NOW - 300},
        "ocr": {"enabled": True, "state": "logged_in", "ok_at": NOW - 300, "error": None,
                "error_at": None, "pending": 0},
        "notices": {"enabled": True, "robots": "allow", "ok_at": NOW - 3600,
                    "fail_since": None, "at": NOW - 3600},
        "market": {"watched": 3, "ok_at": NOW - 600, "blocked": 0},
        "profile": {"status": "ok", "reason": None, "updated": NOW - 1800},
        "boss_drift": None,
    }
    base.update(over)
    return base


def _row(doc, rid):
    rows = {r["id"]: r for r in doc["rows"]}
    return rows[rid]


# --- hints data -------------------------------------------------------------------

def test_hints_file_is_ascii_and_covers_every_reason():
    signals.HINTS_FILE.read_bytes().decode("ascii")
    h = signals.load_hints()
    for rid, reasons in signals.REASONS.items():
        for reason in reasons:
            text = h["hints"].get(f"{rid}.{reason}")
            assert isinstance(text, str) and text.strip(), f"{rid}.{reason}"
            assert len(text) <= 100 and "\n" not in text  # one line


def test_hints_bad_file_falls_back(tmp_path):
    p = tmp_path / "h.json"
    p.write_text("{not json", encoding="utf-8")
    h = signals.load_hints(p)
    assert h["hints"]["session_log.silent"]
    assert h["thresholds"]["session_log"]["bad_s"] == 600


def test_hint_lookup_unknown_reason_is_generic():
    h = signals.load_hints()
    assert signals.hint(h, "market", "no_such_reason") == signals.GENERIC_HINT
    assert signals.hint(h, "session_log", "silent") == h["hints"]["session_log.silent"]


# --- the acceptance pair ------------------------------------------------------------

def test_logged_in_no_line_for_10_min_is_bad_with_hint():
    doc = signals.digest(_inputs(session_log=_game("logged_in", line_age=10 * MIN + 1)), NOW)
    r = _row(doc, "session_log")
    assert r["level"] == "bad"
    assert r["reason"] == "silent"
    assert r["hint"] == signals.load_hints()["hints"]["session_log.silent"]
    assert r["age_s"] == 601
    assert doc["bad"] == 1 and doc["worst"] == "bad"


def test_same_fixture_game_closed_is_off():
    doc = signals.digest(_inputs(session_log=_game("not_running", line_age=10 * MIN + 1)), NOW)
    r = _row(doc, "session_log")
    assert r["level"] == "off"
    assert r["reason"] == "game_closed"
    assert r["hint"]
    assert doc["bad"] == 0


# --- session log ---------------------------------------------------------------------

@pytest.mark.parametrize("state,age,level,reason", [
    ("logged_in", 30, "ok", None),
    ("logged_in", 6 * MIN, "warn", "quiet"),
    ("logged_in", 10 * MIN + 1, "bad", "silent"),
    ("running", 30 * MIN, "warn", "quiet"),        # launcher / loading: never bad
    ("disconnected", 30 * MIN, "warn", "quiet"),
    ("not_running", 99 * MIN, "off", "game_closed"),
])
def test_session_log_levels(state, age, level, reason):
    r = _row(signals.digest(_inputs(session_log=_game(state, line_age=age)), NOW), "session_log")
    assert (r["level"], r["reason"]) == (level, reason)
    assert (r["hint"] is None) == (reason is None)


def test_session_log_no_line_yet_counts_from_state_since():
    g = _game("logged_in", line_age=None, since=NOW - 11 * MIN)
    r = _row(signals.digest(_inputs(session_log=g), NOW), "session_log")
    assert (r["level"], r["reason"], r["age_s"]) == ("bad", "silent", 660)


def test_session_log_config_problems():
    r = _row(signals.digest(_inputs(session_log=_game(configured=False, state="unconfigured")),
                            NOW), "session_log")
    assert (r["level"], r["reason"]) == ("warn", "unconfigured")
    r = _row(signals.digest(_inputs(session_log=_game(log_dir_ok=False)), NOW), "session_log")
    assert (r["level"], r["reason"]) == ("bad", "log_dir_missing")


def test_session_log_stalled_watcher_is_bad_even_with_game_closed():
    g = _game("not_running", polled_at=NOW - 5 * MIN)
    r = _row(signals.digest(_inputs(session_log=g), NOW), "session_log")
    assert (r["level"], r["reason"]) == ("bad", "watcher_stalled")
    g = _game("not_running", polled_at=None)
    r = _row(signals.digest(_inputs(session_log=g), NOW), "session_log")
    assert (r["level"], r["reason"]) == ("bad", "watcher_stalled")


# --- screenshots -----------------------------------------------------------------------

def test_screenshots_rows():
    def r(**kw):
        s = {"configured": True, "dir_ok": True, "state": "logged_in", "last_at": NOW - 300}
        s.update(kw)
        return _row(signals.digest(_inputs(screenshots=s), NOW), "screenshots")
    ok = r()
    assert (ok["level"], ok["age_s"]) == ("ok", 300)
    assert r(last_at=None)["level"] == "ok"            # no shot yet is not a fault
    assert (r(state="not_running")["level"], r(state="not_running")["reason"]) == \
        ("off", "game_closed")
    assert (r(configured=False)["level"], r(configured=False)["reason"]) == \
        ("warn", "unconfigured")
    assert (r(dir_ok=False)["level"], r(dir_ok=False)["reason"]) == ("warn", "folder_missing")


# --- OCR -------------------------------------------------------------------------------

def test_ocr_rows():
    def r(**kw):
        s = {"enabled": True, "state": "logged_in", "ok_at": NOW - 300, "error": None,
             "error_at": None, "pending": 0}
        s.update(kw)
        return _row(signals.digest(_inputs(ocr=s), NOW), "ocr")
    assert r()["level"] == "ok" and r()["age_s"] == 300
    assert (r(enabled=False)["level"], r(enabled=False)["reason"]) == ("off", "disabled")
    bad = r(error="engine missing", error_at=NOW - 60)
    assert (bad["level"], bad["reason"]) == ("bad", "error")
    assert "engine missing" in bad["detail"]
    # an error older than the last good read has healed
    assert r(error="x", error_at=NOW - 900)["level"] == "ok"
    assert (r(pending=5)["level"], r(pending=5)["reason"]) == ("warn", "backlog")
    closed = r(state="not_running")
    assert (closed["level"], closed["reason"]) == ("off", "game_closed")
    # expected silence wins: a closed game is off, the old error kept in the detail
    closed_err = r(state="not_running", error="engine x", error_at=NOW - 60)
    assert (closed_err["level"], closed_err["reason"]) == ("off", "game_closed")
    assert "engine x" in closed_err["detail"]
    # a shot still queued after logout is read, so its failure stays visible
    assert r(state="not_running", pending=1, error="x", error_at=NOW - 60)["level"] == "bad"


# --- official notices ---------------------------------------------------------------------

def test_notices_rows():
    def r(**kw):
        s = {"enabled": True, "robots": "allow", "ok_at": NOW - 3600, "fail_since": None,
             "at": NOW - 3600}
        s.update(kw)
        return _row(signals.digest(_inputs(notices=s), NOW), "notices")
    assert r()["level"] == "ok" and r()["age_s"] == 3600
    assert (r(enabled=False)["level"], r(enabled=False)["reason"]) == ("off", "disabled")
    assert (r(robots="disallow")["level"], r(robots="disallow")["reason"]) == \
        ("off", "robots_disallow")
    assert (r(robots="unreachable")["level"], r(robots="unreachable")["reason"]) == \
        ("warn", "robots_unreachable")
    assert (r(fail_since=NOW - 3600)["level"], r(fail_since=NOW - 3600)["reason"]) == \
        ("warn", "failing")
    assert (r(fail_since=NOW - 3 * 86400)["level"], r(fail_since=NOW - 3 * 86400)["reason"]) \
        == ("bad", "failing")
    assert (r(ok_at=NOW - 3 * 86400)["level"], r(ok_at=NOW - 3 * 86400)["reason"]) == \
        ("warn", "stale")
    assert r(ok_at=None, at=None)["level"] == "ok"     # not fetched yet: nothing wrong
    assert r(ok_at=None, at=None)["age_s"] is None


# --- market -------------------------------------------------------------------------------

def test_market_rows():
    def r(**kw):
        s = {"watched": 3, "ok_at": NOW - 600, "blocked": 0}
        s.update(kw)
        return _row(signals.digest(_inputs(market=s), NOW), "market")
    assert r()["level"] == "ok"
    assert (r(watched=0, ok_at=None)["level"], r(watched=0, ok_at=None)["reason"]) == \
        ("off", "empty")
    # nothing watched is expected silence even with a leftover block
    assert r(watched=0, ok_at=None, blocked=1)["level"] == "off"
    w = r(blocked=2)
    assert (w["level"], w["reason"]) == ("warn", "blocked")
    assert "2" in w["detail"]
    assert (r(blocked=2, ok_at=None)["level"], r(blocked=2, ok_at=None)["reason"]) == \
        ("bad", "blocked")
    assert (r(ok_at=NOW - 7 * 3600)["level"], r(ok_at=NOW - 7 * 3600)["reason"]) == \
        ("warn", "stale")


# --- profile ------------------------------------------------------------------------------

@pytest.mark.parametrize("status,reason_in,level,reason", [
    ("ok", None, "ok", None),
    ("pending", None, "ok", None),
    ("none", None, "off", "no_family"),
    ("off", "no_base", "off", "no_base"),
    ("off", "robots", "off", "robots"),
    ("stale", None, "warn", "stale"),
    ("error", None, "bad", "error"),
])
def test_profile_rows(status, reason_in, level, reason):
    p = {"status": status, "reason": reason_in, "updated": NOW - 1800}
    r = _row(signals.digest(_inputs(profile=p), NOW), "profile")
    assert (r["level"], r["reason"]) == (level, reason)


# --- boss drift ---------------------------------------------------------------------------

def test_boss_drift_rows():
    r = _row(signals.digest(_inputs(boss_drift=None), NOW), "boss_drift")
    assert (r["level"], r["reason"]) == ("off", "not_built")
    r = _row(signals.digest(_inputs(boss_drift={"status": "ok", "checked_at": NOW - 60}), NOW),
             "boss_drift")
    assert (r["level"], r["age_s"]) == ("ok", 60)
    r = _row(signals.digest(_inputs(boss_drift={"status": "drift", "checked_at": NOW}), NOW),
             "boss_drift")
    assert (r["level"], r["reason"]) == ("warn", "drift")
    r = _row(signals.digest(_inputs(boss_drift={"status": "error", "checked_at": NOW}), NOW),
             "boss_drift")
    assert (r["level"], r["reason"]) == ("bad", "error")


# --- digest shape ------------------------------------------------------------------------

def test_digest_shape_and_order():
    doc = signals.digest(_inputs(), NOW)
    assert [r["id"] for r in doc["rows"]] == list(signals.REASONS)
    for r in doc["rows"]:
        assert set(r) == {"id", "name", "level", "age_s", "reason", "detail", "hint"}
        assert r["level"] in signals.LEVELS
    assert doc["bad"] == 0 and doc["worst"] in ("ok", "off")
    assert set(doc) == {"rows", "bad", "worst", "at", "overrides"}
    assert doc["overrides"] == {"count": 0, "items": []}  # plan 079: none by default


def test_digest_carries_the_overrides_section():
    item = {"key": "market.vp", "label": "Value Pack (typed)", "value": True,
            "source": "typed", "expires_in_s": 3600, "cards": ["grind"]}
    doc = signals.digest(_inputs(), NOW, overrides={"count": 9, "items": [item, "junk"]})
    assert doc["overrides"] == {"count": 1, "items": [item]}
    assert signals.digest(_inputs(), NOW, overrides="junk")["overrides"]["count"] == 0


def test_service_overrides_fault_lists_none():
    def boom():
        raise RuntimeError("ledger")
    svc = signals.SignalService({}, clock=lambda: NOW, overrides=boom)
    assert svc.view()["overrides"] == {"count": 0, "items": []}
    svc = signals.SignalService({}, clock=lambda: NOW,
                                overrides=lambda: {"items": [{"key": "a"}]})
    assert svc.view()["overrides"]["count"] == 1


def test_missing_or_junk_input_never_raises():
    doc = signals.digest({"session_log": "junk", "ocr": {"pending": "x"}}, NOW)
    assert [r["id"] for r in doc["rows"]] == list(signals.REASONS)


def test_worst_ranks_bad_over_warn_over_ok():
    doc = signals.digest(_inputs(market={"watched": 1, "ok_at": NOW - 7 * 3600, "blocked": 0}),
                         NOW)
    assert doc["worst"] == "warn" and doc["bad"] == 0


# --- collectors over the live services -----------------------------------------------------

class Clock:
    def __init__(self, t=NOW):
        self.t = t

    def __call__(self):
        return self.t


def test_gamewatch_health_tracks_last_line_and_folders(tmp_path):
    inst, docs = tmp_path / "install", tmp_path / "docs"
    (inst / "Log").mkdir(parents=True)
    clock = Clock()
    w = gamewatch.GameWatch(install_dir=str(inst), documents_dir=str(docs), clock=clock,
                            tasklist=lambda: True)
    h = w.health()
    assert h["configured"] and h["log_dir_ok"] and h["shots_configured"]
    assert h["shots_dir_ok"] is False and h["line_at"] is None and h["polled_at"] is None
    log = inst / "Log" / "Client_2026-10-05_120000.json"
    line = json.dumps({"Date": "x", "LogType": "Info", "Log": "connect success"}) + "\r\n"
    log.write_bytes(b"\xff\xfe" + line.encode("utf-16-le"))
    w.poll()
    h = w.health()
    assert h["line_at"] == NOW and h["polled_at"] == NOW
    clock.t += 700
    w.poll()  # no new bytes: the line age keeps growing
    assert w.health()["line_at"] == NOW
    assert w.health()["polled_at"] == NOW + 700


def test_market_health_counts_blocked_keys(tmp_path):
    def fetch(url, timeout):
        raise market.UpstreamError("HTTP 500 code 103 blocked by Imperva")
    c = market.ArshaClient(fetch=fetch, cache_dir=tmp_path / "cache", clock=Clock())
    wl = market.Watchlist(_Store(), seed=[])
    svc = market.MarketService(c, wl)
    assert svc.health() == {"watched": 0, "ok_at": None, "blocked": 0}
    c.sublist(1, 0)
    c.sublist(2, 0)
    wl.add({"id": 1, "sid": 0})
    # only watched keys count: item 2's block is not this watchlist's problem
    assert svc.health() == {"watched": 1, "ok_at": None, "blocked": 1}


def test_market_health_last_ok_from_watched_cache(tmp_path):
    clock = Clock()
    body = json.dumps([{"id": 1, "sid": 0, "name": "x", "basePrice": 10}])
    c = market.ArshaClient(fetch=lambda url, timeout: body, cache_dir=tmp_path / "cache",
                           clock=clock)
    wl = market.Watchlist(_Store(), seed=[])
    wl.add({"id": 1, "sid": 0})
    svc = market.MarketService(c, wl)
    c.sublist(1, 0)
    clock.t += 100
    assert svc.health() == {"watched": 1, "ok_at": NOW, "blocked": 0}


def test_gamewatch_line_at_resets_on_new_log_file(tmp_path):
    inst = tmp_path / "install"
    (inst / "Log").mkdir(parents=True)
    clock = Clock()
    w = gamewatch.GameWatch(install_dir=str(inst), clock=clock, tasklist=lambda: True)
    line = json.dumps({"Date": "x", "LogType": "Info", "Log": "hello"}) + "\r\n"
    (inst / "Log" / "Client_2026-10-05_120000.json").write_bytes(
        b"\xff\xfe" + line.encode("utf-16-le"))
    w.poll()
    assert w.health()["line_at"] == NOW
    clock.t += 50
    (inst / "Log" / "Client_2026-10-06_120000.json").write_bytes(b"\xff\xfe")
    w.poll()
    assert w.health()["line_at"] is None


class _Store:
    def __init__(self):
        self.d = {}

    def get(self, k):
        return self.d.get(k, {})

    def put(self, k, v):
        self.d[k] = v


# --- route ------------------------------------------------------------------------------

def _no_network(url, timeout):
    raise AssertionError("test touched the network")


@pytest.fixture()
def srv(tmp_path):
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          market_seed=[],
                          market_client=market.ArshaClient(fetch=_no_network,
                                                           cache_dir=tmp_path / "cache"),
                          profile_cfg={})
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield s
    s.shutdown()
    s.server_close()


def test_route_serves_digest(srv):
    c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
    c.request("GET", "/api/signals")
    r = c.getresponse()
    doc = json.loads(r.read())
    c.close()
    assert r.status == 200
    assert [x["id"] for x in doc["rows"]] == list(signals.REASONS)
    rows = {x["id"]: x for x in doc["rows"]}
    assert rows["session_log"]["reason"] == "unconfigured"  # a bare server has no game folder
    assert rows["boss_drift"]["level"] == "off"
    assert rows["profile"]["level"] == "off"
    assert doc["overrides"]["count"] == len(doc["overrides"]["items"])
