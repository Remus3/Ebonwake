"""Plan 070: prompt hygiene - stale-prompt expiry, dedupe, game-closed quiet,
the 15 / 5 / 1 min alert ladder.

Local state and the clock only; nothing touches the game.
"""

import datetime as dt
import json
import threading

import pytest

from server.ew import app as ewapp
from server.ew import market, prompts, settings
from server.ew.store import Store
from tests.test_context import Clock, FakeWatch, _no_network
from tests.test_server import _get

UTC = dt.timezone.utc
MIN = 60
DAY = 86400
NOW = dt.datetime(2026, 10, 6, 15, 0, tzinfo=UTC).timestamp()
CFG = prompts.load_config()


def reg(tmp_path, clk, s=None):
    return prompts.PromptRegistry(Store(tmp_path / "store"), clock=clk,
                                  settings=lambda: s or {})


# --- tracked config ------------------------------------------------------------------

def test_ttl_defaults_from_tracked_file():
    assert CFG["ttl_s"]["coupon"] == 7 * DAY
    assert CFG["ttl_s"]["event_suggestion"] == 7 * DAY
    assert CFG["ttl_s"]["toast"] == 10 * MIN
    assert CFG["ttl_s"]["pending_stop"] == prompts.SESSION
    assert CFG["ladder_min"] == [15, 5, 1]
    assert CFG["quiet"]["states"] == ["not_running"]
    assert "marketAlert" in CFG["quiet"]["allow"] and "couponExpiry" in CFG["quiet"]["allow"]
    assert "bossSoon" not in CFG["quiet"]["allow"]


def test_load_config_refuses_a_bad_file(tmp_path):
    p = tmp_path / "t.json"
    good = json.loads(prompts.DATA.read_text(encoding="utf-8"))
    for bad in ({"ttl_s": {"x": -1}}, {"ttl_s": {"x": "forever"}}, {"quiet": {"states": "x"}},
                {"ladder_min": [1, 5]}, {"ladder_min": []}, {"ladder_min": [0]}):
        p.write_text(json.dumps(dict(good, **bad)), encoding="utf-8")
        with pytest.raises(ValueError):
            prompts.load_config(p)


def test_ladder_setting_parse_and_validate():
    assert prompts.parse_ladder("15,5,1") == (15, 5, 1)
    assert prompts.parse_ladder("30, 10") == (30, 10)
    for bad in ("", "1,5", "5,5", "0", "121", "a,b", "15;5", "1,2,3,4,5,6,7", None, 15, chr(0x661)):
        assert prompts.parse_ladder(bad) is None, bad
    ok, dflt = settings.SPEC["notify.ladder_min"]
    assert dflt == "15,5,1" and ok("10,2") and not ok("2,10") and not ok([15, 5, 1])
    ok, dflt = settings.SPEC["notify.quiet_closed"]
    assert dflt is True and ok(False) and not ok("no")
    assert settings.SPEC["notify.resetSoon"][1] is False


# --- expiry + dedupe -----------------------------------------------------------------------

def test_stale_suggestion_vanishes_with_no_click(tmp_path):
    clk = Clock(NOW)
    r = reg(tmp_path, clk)
    rows = [{"code": "OLD", "date": "2026-09-27"}, {"code": "NEW", "date": None},
            {"code": "NEW", "date": None}]  # a duplicate row shows once
    def keep():
        return [c["code"] for c in r.keep("coupon", rows, lambda c: c["code"],
                                          lambda c: c["date"])]
    assert keep() == ["NEW"]  # OLD is 9 days old: past the 7 d suggestion TTL
    clk.t += 7 * DAY - 1
    assert keep() == ["NEW"]
    clk.t += 1
    assert keep() == []  # NEW reaches 7 d since first seen


def test_registry_first_registration_wins_and_prunes_gone_rows(tmp_path):
    clk = Clock(NOW)
    r = reg(tmp_path, clk)
    a = r.register("coupon:A", "coupon")
    clk.t += 100
    assert r.register("coupon:A", "coupon") == a  # dedupe by key: created is kept
    r.keep("coupon", [{"code": "B"}], lambda c: c["code"])
    doc = Store(tmp_path / "store").get("prompts")
    assert sorted(doc["items"]) == ["coupon:B"]  # A's row is gone, so is its record
    # a reappearing row starts fresh only after it had gone
    assert r.alive("coupon:B") and r.alive("coupon:unknown")


def test_odd_rows_are_shown_never_dropped(tmp_path):
    r = reg(tmp_path, Clock(NOW))
    rows = [{"nocode": 1}, {"code": ""}, {"code": "X"}]
    assert r.keep("coupon", rows, lambda c: c["code"]) == rows


def test_never_expiring_kind(tmp_path):
    clk = Clock(NOW)
    r = reg(tmp_path, clk)
    rows = [{"id": "step"}]
    assert r.keep("onboarding", rows, lambda s: s["id"]) == rows
    clk.t += 365 * DAY
    assert r.keep("onboarding", rows, lambda s: s["id"]) == rows


def test_pending_stop_expires_when_the_next_session_opens(tmp_path):
    clk = Clock(NOW)
    r = reg(tmp_path, clk)
    rows = [{"at": "x"}]
    def k(p):
        return p["at"]
    assert r.keep("pending_stop", rows, k) == rows
    r.on_game("logged_in", "not_running", NOW)
    r.on_game("not_running", "running", NOW)
    assert r.keep("pending_stop", rows, k) == rows  # launcher open is not a session
    r.on_game("running", "logged_in", NOW + 60)
    assert r.keep("pending_stop", rows, k) == []
    r.on_game("logged_in", "logged_in", NOW + 61)  # a repeat is not another session
    assert Store(tmp_path / "store").get("prompts")["session"] == 1


# --- quiet gate -------------------------------------------------------------------------------

def H(rule, key, ladder=False):
    return {"rule": rule, "key": key, "ladder": ladder}


def test_quiet_gate():
    hits = [H("marketAlert", "m"), H("couponExpiry", "c"), H("resetPassed", "r"),
            H("bossSoon", "b:15", ladder=True)]
    fire, drop = prompts.gate(hits, "not_running", CFG)
    assert [h["key"] for h in fire] == ["m", "c"]
    assert [h["key"] for h in drop] == ["b:15"]  # stale: never fires late
    for state in ("logged_in", "running", "disconnected", "unconfigured", None):
        assert prompts.gate(hits, state, CFG) == (hits, [])
    assert prompts.gate(hits, "not_running", CFG, enabled=False) == (hits, [])


# --- ladder -------------------------------------------------------------------------------------

def test_ladder_step():
    assert prompts.ladder_step(16 * MIN) is None
    assert prompts.ladder_step(15 * MIN) == 15
    assert prompts.ladder_step(6 * MIN) == 15
    assert prompts.ladder_step(5 * MIN) == 5
    assert prompts.ladder_step(61) == 5
    assert prompts.ladder_step(60) == 1
    assert prompts.ladder_step(1) == 1
    assert prompts.ladder_step(0) is None and prompts.ladder_step(-5) is None
    assert prompts.ladder_step(25 * MIN, (30, 10)) == 30


def test_ladder_fires_once_per_step():
    at = NOW + 16 * MIN
    led, fired = prompts.Ledger(), []
    for t in range(int(NOW), int(at) + 30, 5):
        fired += led.take(prompts.ladder_hits([{"key": "boss:K", "at": at, "title": "Kzarka",
                                                 "rule": "bossSoon"}], t))
    assert [h["key"] for h in fired] == ["boss:K:15", "boss:K:5", "boss:K:1"]
    assert [h["title"] for h in fired] == ["Kzarka in 15m", "Kzarka in 5m", "Kzarka in 1m"]


# --- acceptance fixture -----------------------------------------------------------------------------

def test_acceptance_closed_game_then_login(tmp_path):
    """9-day-old suggestion + a duplicate toast + a boss in 16 min while the game
    is closed: nothing shows until login, then the 5 and 1 min alerts once each."""
    clk = Clock(NOW)
    r = reg(tmp_path, clk)
    boss = {"key": "bossSoon:K", "at": NOW + 16 * MIN, "title": "Kzarka", "rule": "bossSoon"}
    sugg = [{"code": "OLD9", "date": "2026-09-27"}]
    toast = H("hotTime", "hotTime:x")  # raised at NOW, twice (two tabs)
    led, shown = prompts.Ledger(), []
    login = NOW + 10.5 * MIN  # boss 5.5 min away; the 10 min toast TTL has lapsed
    for t in range(int(NOW), int(NOW + 17 * MIN), 5):
        clk.t = t
        state = "logged_in" if t >= login else "not_running"
        assert r.keep("coupon", sugg, lambda c: c["code"], lambda c: c["date"]) == []
        toasts = r.keep("toast", [dict(toast), dict(toast)], lambda h: h["key"])
        assert len(toasts) <= 1  # dedupe: one prompt shows once
        fire, drop = prompts.gate(prompts.ladder_hits([boss], t) + toasts, state, CFG)
        led.take(drop)
        new = led.take(fire)
        if t < login:
            assert new == []
        shown += new
    assert [h["key"] for h in shown] == ["bossSoon:K:5", "bossSoon:K:1"]


# --- server -----------------------------------------------------------------------------------------

@pytest.fixture()
def psrv(tmp_path):
    clk = Clock(NOW)
    w = FakeWatch(clk)
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          sse_interval=0.05, market_seed=[],
                          market_client=market.ArshaClient(fetch=_no_network,
                                                           cache_dir=tmp_path / "cache"),
                          profile_cfg={}, game_watch=w, config_path=tmp_path / "local.json",
                          today_clock=clk, context_clock=clk)
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield s, w, clk
    s.shutdown()
    s.server_close()


def test_prompts_route(psrv):
    s, w, clk = psrv
    w.forced = "not_running"
    w.poll()
    st, _, body = _get(s, "/api/prompts")
    d = json.loads(body)
    assert st == 200 and d["quiet"] is True and d["quiet_closed"] is True
    assert d["ladder_min"] == [15, 5, 1] and "marketAlert" in d["allow_closed"]
    names = [t["key"].split(":")[1] for t in d["timers"]]
    assert names[:2] == ["daily", "weekly"]
    assert all(dt.datetime.fromisoformat(t["at"]).timestamp() > NOW for t in d["timers"])
    w.forced = "logged_in"
    w.poll()
    d = json.loads(_get(s, "/api/prompts")[2])
    assert d["quiet"] is False and d["session"] == 1


def test_payloads_drop_stale_prompts(psrv):
    s, w, clk = psrv
    clk.t = NOW - 9 * DAY
    s.events_out({"items": []}, {"candidates": [{"code": "old1"}]}, {"candidates": []})
    clk.t = NOW
    rev = {"review": [{"id": "r1"}]}
    s.ocr_auto_out(rev)  # first seen now
    out = s.events_out({"items": []},
                       {"candidates": [{"code": "OLD1"}, {"code": "NEW1"}, {"code": "new1"}]},
                       {"candidates": [{"group_no": 7}, {"group_no": 7}]})
    # first seen 9 days ago (case-folded key); duplicates show once
    assert [c["code"] for c in out["suggested"]["candidates"]] == ["NEW1"]
    assert out["suggested_events"]["candidates"] == [{"group_no": 7}]
    pend = {"at": "2026-10-06T14:00:00+00:00", "started": "2026-10-06T12:00:00+00:00"}
    assert s.grind_out({"pending_stop": pend})["pending_stop"] == pend
    w.forced = "logged_in"
    w.poll()
    assert s.grind_out({"pending_stop": pend})["pending_stop"] is None
    clk.t = NOW + 7 * DAY - 10
    rev = {"review": [{"id": "r1"}, {"id": "r2"}]}
    assert [x["id"] for x in s.ocr_auto_out(rev)["review"]] == ["r1", "r2"]
    clk.t = NOW + 7 * DAY
    assert [x["id"] for x in s.ocr_auto_out(rev)["review"]] == ["r2"]
    st, _, body = _get(s, "/api/grind")
    assert st == 200 and json.loads(body)["pending_stop"] is None


def test_whatnow_ocr_count_skips_stale_review_rows(psrv):
    # merge seam 069 x 070: the What-now card counts only the review rows the
    # System tab still shows, never a stale one plan 070 hides.
    s, w, clk = psrv
    rev = {"review": [{"id": "r1"}]}
    s.ocr_auto.view = lambda: rev
    assert [x["id"] for x in s.whatnow.inputs["ocr"]()["review"]] == ["r1"]  # first seen now
    clk.t = NOW + 7 * DAY
    rev["review"] = [{"id": "r1"}, {"id": "r2"}]
    assert [x["id"] for x in s.whatnow.inputs["ocr"]()["review"]] == ["r2"]
