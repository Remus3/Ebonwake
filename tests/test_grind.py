"""Plan 005 slice A: grind store domain, silver/h math, buff timers, routes.

No network, no game client: everything is operator input plus a seed of buff
names. The clock is injected so elapsed/left times are exact.
"""

import ast
import datetime as dt
import http.client
import json
import re
import threading
from pathlib import Path

import pytest

from server.ew import app as ewapp
from server.ew import grind
from server.ew.store import Store

UTC = dt.timezone.utc
T0 = dt.datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)


class Clock:
    def __init__(self, when=T0):
        self.t = when.timestamp()

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


@pytest.fixture()
def clock():
    return Clock()


@pytest.fixture()
def svc(tmp_path, clock):
    return grind.GrindService(Store(tmp_path / "store"), clock=clock)


def _spot(doc, name):
    return next(s for s in doc["spots"] if s["name"] == name)


def _buff(doc, name):
    return next(b for b in doc["buffs"] if b["name"] == name)


# --- math --------------------------------------------------------------------

@pytest.mark.parametrize("silver, minutes, want", [
    (600_000_000, 60, 600_000_000),
    (100, 30, 200),
    (1, 7, 8),            # 60/7 = 8.57 -> floor
    (0, 15, 0),
    (10 ** 13, 1, 6 * 10 ** 14),
])
def test_silver_per_hour(silver, minutes, want):
    assert grind.silver_per_hour(silver, minutes) == want


def test_silver_per_hour_zero_minutes_is_zero():
    assert grind.silver_per_hour(100, 0) == 0


# --- seed --------------------------------------------------------------------

def test_seed_spots_empty_buff_names_unarmed(svc):
    doc = svc.view()
    assert doc["spots"] == [] and doc["sessions"] == [] and doc["active"] is None
    names = [b["name"] for b in doc["buffs"]]
    assert names == [n for n in grind.SEED_BUFFS]
    assert all(b["ends"] is None and b["left_s"] is None for b in doc["buffs"])


def test_seed_not_reapplied(tmp_path, clock):
    st = Store(tmp_path / "store")
    a = grind.GrindService(st, clock=clock)
    a.add_spot("Gyfin Underground")
    b = grind.GrindService(st, clock=clock)
    assert [s["name"] for s in b.view()["spots"]] == ["Gyfin Underground"]


# --- spots -------------------------------------------------------------------

def test_add_spot_and_duplicate(svc):
    doc = svc.add_spot("  Gyfin Underground ")
    s = _spot(doc, "Gyfin Underground")
    assert s == {"id": "gyfin-underground", "name": "Gyfin Underground", "sessions": 0,
                 "minutes": 0, "silver_per_h": 0}
    with pytest.raises(ValueError):
        svc.add_spot("gyfin underground")


@pytest.mark.parametrize("name", ["", "   ", "x" * 61, 5, None, "a\nb"])
def test_add_spot_bad_name(svc, name):
    with pytest.raises(ValueError):
        svc.add_spot(name)


def test_add_spot_60_chars_ok(svc):
    assert len(svc.add_spot("x" * 60)["spots"]) == 1


# --- start / stop ------------------------------------------------------------

def test_start_stop_session(svc, clock):
    svc.add_spot("Olun's Valley")
    doc = svc.start("oluns-valley")
    assert doc["active"] == {"spot": "oluns-valley", "started": T0.isoformat(), "elapsed_s": 0,
                             "auto": False}  # plan 062: manual start
    clock.advance(90 * 60 + 20)
    assert svc.view()["active"]["elapsed_s"] == 90 * 60 + 20
    doc = svc.stop({"silver": 900_000_000, "trash": 12000})
    assert doc["active"] is None
    (s,) = doc["sessions"]
    assert s["spot"] == "oluns-valley" and s["minutes"] == 90
    assert s["started"] == T0.isoformat() and s["silver"] == 900_000_000 and s["trash"] == 12000
    sp = _spot(doc, "Olun's Valley")
    assert sp["sessions"] == 1 and sp["minutes"] == 90 and sp["silver_per_h"] == 600_000_000


def test_stop_minimum_one_minute(svc, clock):
    svc.add_spot("A")
    svc.start("a")
    clock.advance(5)
    assert svc.stop({"silver": 10, "trash": 0})["sessions"][0]["minutes"] == 1


def test_stop_clamps_to_max_minutes(svc, clock):
    svc.add_spot("A")
    svc.start("a")
    clock.advance(3 * 24 * 3600)
    assert svc.stop({"silver": 10, "trash": 0})["sessions"][0]["minutes"] == grind.MAX_MINUTES


def test_start_unknown_spot_and_double_start(svc):
    with pytest.raises(ValueError):
        svc.start("nope")
    svc.add_spot("A")
    svc.start("a")
    with pytest.raises(ValueError):
        svc.start("a")


def test_stop_without_active(svc):
    with pytest.raises(ValueError):
        svc.stop({"silver": 1, "trash": 1})


@pytest.mark.parametrize("arg", [
    {"silver": -1, "trash": 0}, {"silver": 10 ** 13 + 1, "trash": 0},
    {"silver": 1, "trash": -1}, {"silver": 1, "trash": 10 ** 6 + 1},
    {"silver": True, "trash": 0}, {"silver": 1.5, "trash": 0}, {"silver": "1", "trash": 0},
    {"silver": 1}, {"silver": 1, "trash": 0, "x": 1}, [1, 2], None,
])
def test_stop_bad_body_keeps_active(svc, arg):
    svc.add_spot("A")
    svc.start("a")
    with pytest.raises(ValueError):
        svc.stop(arg)
    assert svc.view()["active"] is not None


def test_stop_limits_inclusive(svc):
    svc.add_spot("A")
    svc.start("a")
    s = svc.stop({"silver": 10 ** 13, "trash": 10 ** 6})["sessions"][0]
    assert s["silver"] == 10 ** 13 and s["trash"] == 10 ** 6


# --- manual log / delete ------------------------------------------------------

def test_log_manual_and_weighted_average(svc, clock):
    svc.add_spot("A")
    svc.log({"spot": "a", "minutes": 60, "silver": 600, "trash": 1})
    clock.advance(10)
    doc = svc.log({"spot": "a", "minutes": 30, "silver": 0, "trash": 0})
    # weighted by minutes: 600 * 60 / 90 = 400, not mean(600, 0) = 300
    sp = _spot(doc, "A")
    assert sp["silver_per_h"] == 400 and sp["minutes"] == 90 and sp["sessions"] == 2
    # newest first; manual started = now - minutes
    assert [s["minutes"] for s in doc["sessions"]] == [30, 60]
    want = (T0 + dt.timedelta(seconds=10) - dt.timedelta(minutes=30)).isoformat()
    assert doc["sessions"][0]["started"] == want


@pytest.mark.parametrize("arg", [
    {"spot": "a", "minutes": 0, "silver": 1, "trash": 0},
    {"spot": "a", "minutes": 1441, "silver": 1, "trash": 0},
    {"spot": "a", "minutes": True, "silver": 1, "trash": 0},
    {"spot": "nope", "minutes": 5, "silver": 1, "trash": 0},
    {"spot": "a", "minutes": 5, "silver": 1},
    {"spot": "a", "minutes": 5, "silver": 1, "trash": 0, "extra": 1},
    "a",
])
def test_log_bad(svc, arg):
    svc.add_spot("A")
    with pytest.raises(ValueError):
        svc.log(arg)
    assert svc.view()["sessions"] == []


def test_log_limits_inclusive(svc):
    svc.add_spot("A")
    svc.log({"spot": "a", "minutes": 1, "silver": 0, "trash": 0})
    doc = svc.log({"spot": "a", "minutes": 1440, "silver": 10 ** 13, "trash": 10 ** 6})
    assert len(doc["sessions"]) == 2


def test_delete_session(svc):
    svc.add_spot("A")
    doc = svc.log({"spot": "a", "minutes": 10, "silver": 1, "trash": 0})
    sid = doc["sessions"][0]["id"]
    doc = svc.delete(sid)
    assert doc["sessions"] == [] and _spot(doc, "A")["sessions"] == 0
    with pytest.raises(ValueError):
        svc.delete(sid)
    with pytest.raises(ValueError):
        svc.delete(5)


def test_session_ids_unique_after_delete(svc):
    svc.add_spot("A")
    a = svc.log({"spot": "a", "minutes": 1, "silver": 0, "trash": 0})["sessions"][0]["id"]
    b = svc.log({"spot": "a", "minutes": 1, "silver": 0, "trash": 0})["sessions"][0]["id"]
    svc.delete(b)
    c = svc.log({"spot": "a", "minutes": 1, "silver": 0, "trash": 0})["sessions"][0]["id"]
    assert len({a, b, c}) == 3


def test_view_caps_sessions_at_200_newest_first(svc, clock):
    svc.add_spot("A")
    for n in range(205):
        clock.advance(60)
        svc.log({"spot": "a", "minutes": 1, "silver": n, "trash": 0})
    doc = svc.view()
    assert len(doc["sessions"]) == 200
    assert doc["sessions"][0]["silver"] == 204 and doc["sessions"][-1]["silver"] == 5
    # the spot average still covers every stored session
    assert _spot(doc, "A")["sessions"] == 205


def test_spots_sorted_order_stable(svc):
    svc.add_spot("B")
    svc.add_spot("A")
    assert [s["name"] for s in svc.view()["spots"]] == ["B", "A"]


# --- buffs -------------------------------------------------------------------

def test_arm_seeded_buff_and_countdown(svc, clock):
    doc = svc.buff({"name": "Hot Time", "minutes": 60})
    b = _buff(doc, "Hot Time")
    assert b["ends"] == (T0 + dt.timedelta(minutes=60)).isoformat() and b["left_s"] == 3600
    clock.advance(600)
    assert _buff(svc.view(), "Hot Time")["left_s"] == 3000
    # re-arming replaces, never stacks
    clock.advance(10)
    doc = svc.buff({"name": "hot time", "minutes": 30})
    assert _buff(doc, "Hot Time")["left_s"] == 1800
    assert sum(1 for b in doc["buffs"] if b["name"].lower() == "hot time") == 1


def test_expired_buff_reported_unarmed(svc, clock):
    svc.buff({"name": "Hot Time", "minutes": 1})
    clock.advance(60)
    b = _buff(svc.view(), "Hot Time")
    assert b["ends"] is None and b["left_s"] is None


def test_arm_new_buff_name(svc):
    doc = svc.buff({"name": "Elixir set", "minutes": 120})
    assert _buff(doc, "Elixir set")["left_s"] == 7200


def test_buff_value_pack_month_ok(svc):
    doc = svc.buff({"name": "Value Pack", "minutes": grind.MAX_BUFF_MINUTES})
    assert _buff(doc, "Value Pack")["left_s"] == grind.MAX_BUFF_MINUTES * 60


@pytest.mark.parametrize("arg", [
    {"name": "Hot Time", "minutes": 0}, {"name": "Hot Time", "minutes": 43201},
    {"name": "", "minutes": 5}, {"name": "x" * 61, "minutes": 5},
    {"name": "Hot Time"}, {"name": "Hot Time", "minutes": 5, "x": 1}, "Hot Time",
])
def test_buff_bad(svc, arg):
    with pytest.raises(ValueError):
        svc.buff(arg)


def test_clear_buff(svc):
    svc.buff({"name": "Hot Time", "minutes": 60})
    doc = svc.clear_buff("hot-time")
    assert _buff(doc, "Hot Time")["ends"] is None
    with pytest.raises(ValueError):
        svc.clear_buff("nope")


# --- store degradation -------------------------------------------------------

def test_corrupt_entries_skipped(tmp_path, clock):
    st = Store(tmp_path / "store")
    st.put("grind", {"spots": [{"id": "a", "name": "A"}, "junk", {"id": 5}],
                     "sessions": [{"id": "s1", "spot": "a", "started": T0.isoformat(),
                                   "minutes": 60, "silver": 60, "trash": 0},
                                  {"id": "s2", "spot": "a", "minutes": "x"}, 7],
                     "active": "junk", "buffs": [{"id": "x"}, None]})
    doc = grind.GrindService(st, clock=clock).view()
    assert [s["id"] for s in doc["spots"]] == ["a"]
    assert [s["id"] for s in doc["sessions"]] == ["s1"]
    assert doc["active"] is None and doc["buffs"] == []


@pytest.mark.parametrize("bad", ["0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00"])
def test_out_of_range_stamps_degrade_to_none(tmp_path, clock, bad):
    st = Store(tmp_path / "store")
    st.put("grind", {"spots": [{"id": "a", "name": "A"}],
                     "buffs": [{"id": "hot-time", "name": "Hot Time", "ends": bad}],
                     "updated": bad})
    svc = grind.GrindService(st, clock=clock)
    b = _buff(svc.view(), "Hot Time")
    assert b["ends"] is None and b["left_s"] is None
    assert svc.source() == {"updated": None, "status": "ok"}
    doc = svc.buff({"name": "Hot Time", "minutes": 5})
    assert _buff(doc, "Hot Time")["left_s"] == 300


def test_out_of_range_stamp_route_ok(tmp_path, clock):
    st = Store(tmp_path / "store")
    st.put("grind", {"spots": [],
                     "buffs": [{"id": "x", "name": "X", "ends": "0001-01-01T00:00:00+01:00"}],
                     "updated": "0001-01-01T00:00:00+01:00"})
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          sse_interval=0.05, market_seed=[], grind_clock=clock)
    threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    try:
        status, doc, _ = _req(s, "GET", "/api/grind")
        assert status == 200 and _buff(doc, "X")["ends"] is None
        status, doc, _ = _req(s, "POST", "/api/grind", {"buff": {"name": "X", "minutes": 5}})
        assert status == 200 and _buff(doc, "X")["left_s"] == 300
    finally:
        s.shutdown()
        s.server_close()


def test_source(svc):
    src = svc.source()
    assert src["status"] == "ok" and src["updated"] == T0.isoformat()


# --- routes ------------------------------------------------------------------

@pytest.fixture()
def gsrv(tmp_path, clock):
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          sse_interval=0.05, market_seed=[], grind_clock=clock)
    t = threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
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


def test_route_get(gsrv):
    st, doc, _ = _req(gsrv, "GET", "/api/grind")
    assert st == 200 and set(doc) >= {"active", "sessions", "spots", "buffs"}


def test_route_post_ops(gsrv, clock):
    st, doc, acao = _req(gsrv, "POST", "/api/grind", {"add_spot": "Gyfin"})
    assert st == 200 and acao is None and doc["spots"][0]["id"] == "gyfin"
    st, doc, _ = _req(gsrv, "POST", "/api/grind", {"start": "gyfin"})
    assert st == 200 and doc["active"]["spot"] == "gyfin"
    clock.advance(3600)
    st, doc, _ = _req(gsrv, "POST", "/api/grind", {"stop": {"silver": 500, "trash": 3}})
    assert st == 200 and doc["sessions"][0]["minutes"] == 60
    st, doc, _ = _req(gsrv, "POST", "/api/grind",
                      {"log": {"spot": "gyfin", "minutes": 30, "silver": 1, "trash": 0}})
    assert st == 200 and len(doc["sessions"]) == 2
    st, doc, _ = _req(gsrv, "POST", "/api/grind", {"delete": doc["sessions"][0]["id"]})
    assert st == 200 and len(doc["sessions"]) == 1
    st, doc, _ = _req(gsrv, "POST", "/api/grind", {"buff": {"name": "Hot Time", "minutes": 5}})
    assert st == 200 and _buff(doc, "Hot Time")["left_s"] == 300
    st, doc, _ = _req(gsrv, "POST", "/api/grind", {"clear_buff": "hot-time"})
    assert st == 200 and _buff(doc, "Hot Time")["ends"] is None


def test_route_post_guards(gsrv):
    assert _req(gsrv, "POST", "/api/grind", {"add_spot": "A"}, host="evil.example.com")[0] == 403
    assert _req(gsrv, "POST", "/api/grind", {"add_spot": "A"}, ctype="text/plain")[0] == 415
    big = json.dumps({"add_spot": "A", "pad": "x" * 5000}).encode()
    assert _req(gsrv, "POST", "/api/grind", big)[0] == 413
    assert _req(gsrv, "GET", "/api/grind")[1]["spots"] == []
    assert _req(gsrv, "GET", "/api/grind", host="evil.example.com")[0] == 403


@pytest.mark.parametrize("body", [b"not json", b"[]", {}, {"nope": 1},
                                  {"add_spot": "A", "start": "a"}, {"start": "nope"},
                                  {"stop": {"silver": 1, "trash": 0}}, {"delete": "s9"},
                                  {"buff": {"name": "X", "minutes": 0}},
                                  {"clear_buff": "nope"}])
def test_route_post_bad_body(gsrv, body):
    st, doc, _ = _req(gsrv, "POST", "/api/grind", body)
    assert st == 400 and "error" in doc


def test_state_reports_grind_source(gsrv):
    _, doc, _ = _req(gsrv, "GET", "/api/state")
    assert doc["sources"]["grind"]["status"] == "ok"
    assert doc["sources"]["grind"]["updated"] == T0.isoformat()


# --- plan 038: drop-buff caps + Blessing of Agris ROI -------------------------

CAPS = {"base_pct": 300, "bypass_pct": 400, "beyond_pct": 500}
SCROLL = {"price_silver": 1_000_000_000, "minutes": 60, "sale_until_utc": "2026-10-22",
          "removed_utc": "2026-11-05", "per_week": 10}


def _row(rid, rate=None, amount=None, bypass="none"):
    r = {"id": rid, "name": rid, "bypass": bypass}
    if rate is not None:
        r["rate_pct"] = rate
    if amount is not None:
        r["amount_pct"] = amount
    return r


def test_drop_stack_under_cap():
    out = grind.drop_stack([_row("a", 100), _row("b", 50)], CAPS)
    assert out["rate_total"] == 150 and out["rate_capped"] == 150
    assert out["wasted"] == 0 and out["cap_used"] == 300 and out["over_cap"] is False


def test_drop_stack_base_cap_wasted():
    out = grind.drop_stack([_row("a", 100), _row("b", 100), _row("c", 100), _row("d", 50)], CAPS)
    assert out["rate_total"] == 350 and out["rate_capped"] == 300
    assert out["wasted"] == 50 and out["over_cap"] is True and out["cap_used"] == 300


def test_drop_stack_bypass_order():
    # 350 normal capped to 300, then +130 bypass sources up to 400, then +50 Agris to 500.
    rows = [_row("a", 200), _row("b", 150), _row("arsha", 50, bypass="to400"),
            _row("castle", 50, bypass="to400"), _row("earth", 20, bypass="to400"),
            _row("node", 10, bypass="to400")]
    out = grind.drop_stack(rows, CAPS)
    assert out["rate_capped"] == 400 and out["cap_used"] == 400
    assert out["rate_total"] == 480 and out["wasted"] == 80
    out = grind.drop_stack(rows + [_row("agris", 50, bypass="to500")], CAPS)
    assert out["rate_capped"] == 450 and out["cap_used"] == 500 and out["wasted"] == 80


def test_drop_stack_bypass_never_lifts_normal_sources():
    # Bypass sources only add their own value; they do not unlock wasted normal %.
    out = grind.drop_stack([_row("a", 400), _row("node", 10, bypass="to400")], CAPS)
    assert out["rate_capped"] == 310 and out["wasted"] == 100


def test_drop_stack_agris_alone_from_low_base():
    out = grind.drop_stack([_row("a", 100), _row("agris", 50, bypass="to500")], CAPS)
    assert out["rate_capped"] == 150 and out["wasted"] == 0


def test_drop_stack_amount_kept_separate():
    out = grind.drop_stack([_row("scroll", 100, 50), _row("fever", None, 50),
                            _row("x", 300)], CAPS)
    assert out["amount_total"] == 100
    assert out["rate_total"] == 400 and out["rate_capped"] == 300  # amount never capped
    assert grind.drop_stack([], CAPS) == {"rate_total": 0, "rate_capped": 0, "wasted": 0,
                                          "amount_total": 0, "cap_used": 300,
                                          "over_cap": False}


def test_drop_stack_caps_come_from_the_row():
    out = grind.drop_stack([_row("a", 260), _row("b", 30, bypass="to400")],
                           {"base_pct": 250, "bypass_pct": 270, "beyond_pct": 290})
    assert out["rate_capped"] == 270 and out["cap_used"] == 270 and out["wasted"] == 20


def test_drop_stack_fractional_values():
    out = grind.drop_stack([_row("luck", 12.5), _row("a", 100)], CAPS)
    assert out["rate_total"] == 112.5 and out["rate_capped"] == 112.5


def test_agris_roi_arithmetic():
    now = dt.datetime(2026, 10, 10, tzinfo=UTC)
    # before 300 %, after 350 %: drops x4 -> x4.5 = +12.5 %; break-even 1 B / 0.125 = 8 B/h.
    r = grind.agris_roi(10_000_000_000, SCROLL, 300, 350, now)
    assert r["gain_pct"] == 12.5 and r["break_even_silver_h"] == 8_000_000_000
    assert r["gain_silver"] == 1_250_000_000 and r["net_silver"] == 250_000_000
    assert r["worth"] is True and r["hidden"] is False and r["on_sale"] is True
    assert r["price_silver"] == 1_000_000_000 and r["minutes"] == 60
    r = grind.agris_roi(1_000_000_000, SCROLL, 300, 350, now)
    assert r["worth"] is False and r["net_silver"] == -875_000_000


def test_agris_roi_minutes_and_no_gain():
    now = dt.datetime(2026, 10, 10, tzinfo=UTC)
    r = grind.agris_roi(1_000, dict(SCROLL, minutes=30, price_silver=100), 0, 50, now)
    # +50 % for half an hour: break-even = 100 / (0.5 * 0.5) = 400/h
    assert r["break_even_silver_h"] == 400 and r["worth"] is True
    r = grind.agris_roi(1_000, SCROLL, 400, 400, now)
    assert r["gain_pct"] == 0 and r["break_even_silver_h"] is None and r["worth"] is False
    r = grind.agris_roi(None, SCROLL, 300, 350, now)
    assert r["silver_h"] is None and r["gain_silver"] is None and r["worth"] is None


def test_agris_roi_dates():
    sale_end = dt.datetime(2026, 10, 22, 23, 0, tzinfo=UTC)
    r = grind.agris_roi(1, SCROLL, 0, 50, sale_end)
    assert r["on_sale"] is True and r["hidden"] is False
    r = grind.agris_roi(1, SCROLL, 0, 50, dt.datetime(2026, 10, 23, tzinfo=UTC))
    assert r["on_sale"] is False and r["hidden"] is False
    r = grind.agris_roi(1, SCROLL, 0, 50, dt.datetime(2026, 11, 5, tzinfo=UTC))
    assert r["hidden"] is True
    assert r["sale_until_utc"] == "2026-10-22" and r["removed_utc"] == "2026-11-05"


# data file


def test_drop_data_schema():
    data = grind.load_drop_data()
    assert set(data) == {"caps", "agris_scroll", "buffs"}
    caps = data["caps"]
    assert caps["base_pct"] < caps["bypass_pct"] < caps["beyond_pct"]
    assert caps["source"] and caps["verified"]
    s = data["agris_scroll"]
    assert s["price_silver"] > 0 and s["minutes"] > 0 and s["per_week"] > 0
    assert s["sale_until_utc"] < s["removed_utc"] and s["source"]
    ids, names = set(), set()
    for r in data["buffs"]:
        assert r["bypass"] in ("none", "to400", "to500")
        assert "rate_pct" in r or "amount_pct" in r
        assert r["source"] and (r["verified"] is False or isinstance(r["verified"], str))
        if r["verified"] is False:
            assert r["note"], r["id"]
        for n in [r["name"]] + r.get("aliases", []):
            assert n.lower() not in names, n
            names.add(n.lower())
        assert r["id"] not in ids
        ids.add(r["id"])
    by = {r["id"]: r for r in data["buffs"]}
    assert by["agris-fever"]["verified"] is False and "rate_pct" not in by["agris-fever"]
    assert by["ecology-knowledge"]["verified"] is False
    assert by["agris-scroll"]["bypass"] == "to500"
    assert {r["id"] for r in data["buffs"] if r["bypass"] == "to400"} == {
        "arsha-server", "node-investment", "castle-buff", "thriving-earth"}


@pytest.mark.parametrize("mutate", [
    lambda d: d["caps"].update(bypass_pct=200),
    lambda d: d["caps"].pop("source"),
    lambda d: d["agris_scroll"].update(sale_until_utc="2026-13-01"),
    lambda d: d["agris_scroll"].update(price_silver=-1),
    lambda d: d["buffs"][0].update(bypass="to600"),
    lambda d: d["buffs"][0].update(rate_pct=True),
    lambda d: d["buffs"][0].pop("rate_pct") and d["buffs"][0].pop("amount_pct"),
    lambda d: d["buffs"][0].update(verified=False, note=""),
    lambda d: d["buffs"].append(dict(d["buffs"][0])),
    lambda d: d["buffs"][0].update(extra=1),
])
def test_drop_data_rejects_bad(tmp_path, mutate):
    data = json.loads(grind.DROPS_FILE.read_text(encoding="ascii"))
    mutate(data)
    p = tmp_path / "d.json"
    p.write_text(json.dumps(data), encoding="ascii")
    with pytest.raises(ValueError):
        grind.load_drop_data(p)


def _forbidden_constants(src):
    tree = ast.parse(src)
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docs.add(id(body[0].value))
    bad = []
    date = re.compile(r"\b2026-10-22\b|\b2026-11-05\b")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or id(node) in docs:
            continue
        v = node.value
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)) and v in (300, 400, 500, 1_000_000_000):
            bad.append(v)
        elif isinstance(v, str) and date.search(v):
            bad.append(v)
    return bad


def test_no_drop_game_constants_in_grind_code():
    src = (Path(grind.__file__)).read_text(encoding="ascii")
    assert _forbidden_constants(src) == []


@pytest.mark.parametrize("src, hits", [
    ("x = 300", 1), ("x = 4e2", 1), ("x = 1_000_000_000", 1), ("x = 1e9", 1),
    ("x = 500.0", 1), ("x = '2026-10-22'", 1), ("x = 'until 2026-11-05 maint'", 1),
    ("x = 3000", 0), ("x = 1500", 0), ("x = 'x300'", 0), ("# 300\nx = 1", 0),
    ("def f():\n    '''cap 300, 2026-10-22'''\n    return 1", 0), ("cap_300 = 1", 0),
])
def test_forbidden_constant_scanner(src, hits):
    assert len(_forbidden_constants(src)) == hits


# service integration


def test_view_drops_from_timers_and_toggles(svc, clock):
    d = svc.view()["drops"]
    assert d["error"] is None and d["rate_total"] == 0 and d["cap_used"] == 300
    svc.buff({"name": "Drop rate scroll", "minutes": 60})      # alias -> item collection scroll
    svc.buff({"name": "Kamasylve blessing", "minutes": 60})
    d = svc.drop_toggle({"id": "node-investment", "on": True})["drops"]
    assert d["rate_total"] == 130 and d["rate_capped"] == 130 and d["amount_total"] == 50
    assert d["cap_used"] == 400
    via = {a["id"]: a["via"] for a in d["active"]}
    assert via == {"item-collection-scroll": "timer", "kamasylve-blessing": "timer",
                   "node-investment": "toggle"}
    row = next(r for r in d["buffs"] if r["id"] == "node-investment")
    assert row["on"] is True and row["overridden"] is False
    clock.advance(3601)
    d = svc.view()["drops"]
    assert d["rate_total"] == 10  # timers expired, toggle stays
    d = svc.drop_toggle({"id": "node-investment", "on": False})["drops"]
    assert d["rate_total"] == 0


def test_drop_toggle_bad(svc):
    for arg in ({"id": "nope", "on": True}, {"id": "luck", "on": 1}, {"id": "luck"}, "luck"):
        with pytest.raises(ValueError):
            svc.drop_toggle(arg)


def test_drop_override_rows_caps_scroll(svc):
    d = svc.drop_override({"id": "tent-adventurers-luck", "field": "rate_pct",
                           "value": 30})["drops"]
    row = next(r for r in d["buffs"] if r["id"] == "tent-adventurers-luck")
    assert row["rate_pct"] == 30 and row["overridden"] is True
    doc = svc.drop_override({"id": "caps", "field": "base_pct", "value": 320})
    assert doc["drops"]["caps"]["base_pct"] == 320 and doc["drops"]["cap_used"] == 320
    doc = svc.drop_override({"id": "agris_scroll", "field": "price_silver", "value": 500})
    assert doc["agris_roi"]["price_silver"] == 500
    doc = svc.drop_override({"id": "agris_scroll", "field": "removed_utc",
                             "value": "2026-10-01"})
    assert doc["agris_roi"]["hidden"] is True
    doc = svc.drop_override({"id": "agris_scroll", "field": "removed_utc", "value": None})
    assert doc["agris_roi"]["hidden"] is False
    d = svc.drop_override({"id": "tent-adventurers-luck", "field": "rate_pct",
                           "value": None})["drops"]
    row = next(r for r in d["buffs"] if r["id"] == "tent-adventurers-luck")
    assert row["rate_pct"] == 50 and row["overridden"] is False


@pytest.mark.parametrize("arg", [
    {"id": "nope", "field": "rate_pct", "value": 1},
    {"id": "luck", "field": "name", "value": "x"},
    {"id": "luck", "field": "rate_pct", "value": -1},
    {"id": "luck", "field": "rate_pct", "value": True},
    {"id": "luck", "field": "bypass", "value": "to600"},
    {"id": "caps", "field": "base_pct", "value": 450},          # above bypass cap
    {"id": "caps", "field": "source", "value": "x"},
    {"id": "agris_scroll", "field": "removed_utc", "value": "soon"},
    {"id": "agris_scroll", "field": "minutes", "value": 0},
    {"id": "luck", "field": "rate_pct"},
])
def test_drop_override_bad(svc, arg):
    with pytest.raises(ValueError):
        svc.drop_override(arg)


def test_agris_roi_in_view_uses_spot_average(svc):
    svc.add_spot("Gyfin")
    svc.log({"spot": "gyfin", "minutes": 60, "silver": 2_000_000_000, "trash": 0})
    svc.buff({"name": "Drop rate scroll", "minutes": 60})
    roi = svc.view()["agris_roi"]
    # 100 % -> 150 %: x2 -> x2.5 = +25 %; break-even 4 B/h; 2 B/h earns 500 M
    assert roi["spot"] == "gyfin" and roi["silver_h"] == 2_000_000_000
    assert roi["gain_pct"] == 25 and roi["break_even_silver_h"] == 4_000_000_000
    assert roi["worth"] is False


def test_agris_scroll_active_counts_once(svc):
    svc.buff({"name": "Agris scroll", "minutes": 60})
    doc = svc.view()
    assert doc["drops"]["rate_capped"] == 50 and doc["drops"]["cap_used"] == 500
    assert doc["agris_roi"]["gain_pct"] == 50  # before excludes the scroll itself


def test_drop_state_survives_other_writes(svc):
    svc.drop_toggle({"id": "night", "on": True})
    svc.drop_override({"id": "night", "field": "rate_pct", "value": 15})
    svc.add_spot("Gyfin")
    assert svc.view()["drops"]["rate_total"] == 15


def test_corrupt_drop_state_degrades(tmp_path, clock):
    st = Store(tmp_path / "store")
    grind.GrindService(st, clock=clock)
    doc = st.get("grind")
    doc["drop_on"] = ["night", 5, "nope", None]
    doc["drop_overrides"] = {"night": {"rate_pct": "x", "amount_pct": 5},
                             "caps": {"base_pct": 999}, "zzz": 1, "luck": []}
    st.put("grind", doc)
    d = grind.GrindService(st, clock=clock).view()["drops"]
    assert d["rate_total"] == 10 and d["amount_total"] == 5 and d["caps"]["base_pct"] == 300


def test_bad_drop_file_degrades(tmp_path, clock):
    p = tmp_path / "d.json"
    p.write_text("{", encoding="ascii")
    s = grind.GrindService(Store(tmp_path / "store"), clock=clock, drops_path=p)
    doc = s.view()
    assert doc["drops"]["error"] and doc["agris_roi"] is None
    with pytest.raises(ValueError):
        s.drop_toggle({"id": "night", "on": True})


def test_route_drop_ops(gsrv):
    st, doc, _ = _req(gsrv, "POST", "/api/grind", {"drop_toggle": {"id": "night", "on": True}})
    assert st == 200 and doc["drops"]["rate_total"] == 10 and "agris_roi" in doc
    st, doc, _ = _req(gsrv, "POST", "/api/grind",
                      {"drop_override": {"id": "night", "field": "rate_pct", "value": 20}})
    assert st == 200 and doc["drops"]["rate_total"] == 20
    st, _, _ = _req(gsrv, "POST", "/api/grind", {"drop_toggle": {"id": "nope", "on": True}})
    assert st == 400


# --- plan 039: loot-valued sessions -------------------------------------------

M = 1_000_000


def test_loot_tables_schema_and_keys():
    from server.ew import spots
    tables = grind.load_loot_tables()
    ids = {r["id"] for r in spots.load_table()}
    assert tables and set(tables) <= ids
    for entry in tables.values():
        assert entry["source"].startswith("https://")
        assert entry["verified"] is False or grind.DATE_RE.match(entry["verified"])
        names = [it["name"] for it in entry["items"]]
        assert names and len(names) == len({n.lower() for n in names})
        for it in entry["items"]:
            assert isinstance(it["marketable"], bool)
            assert it["marketable"] or "vendor_price" in it


@pytest.mark.parametrize("doc", [
    [], {"spots": []}, {"spots": {"Bad Id": {"items": [], "source": "https://x", "verified": False}}},
    {"spots": {"a": {"items": [{"name": "X"}], "source": "https://x", "verified": False}}},
    {"spots": {"a": {"items": [{"name": "X", "marketable": "yes"}], "source": "https://x",
                     "verified": False}}},
    {"spots": {"a": {"items": [{"name": "X", "marketable": False, "vendor_price": -1}],
                     "source": "https://x", "verified": False}}},
    {"spots": {"a": {"items": [{"name": "X", "marketable": True, "id": True}],
                     "source": "https://x", "verified": False}}},
    {"spots": {"a": {"items": [], "source": "", "verified": False}}},
    {"spots": {"a": {"items": [], "source": "https://x", "verified": True}}},
    {"spots": {"a": {"items": [{"name": "X", "marketable": True},
                               {"name": "x", "marketable": True}],
                     "source": "https://x", "verified": False}}},
])
def test_loot_tables_bad_schema(doc):
    with pytest.raises(ValueError):
        grind.validate_loot_tables(doc)


def test_loot_value_tax_paths():
    loot = [{"name": "Trash", "count": 1000, "vendor_price": 1500, "marketable": False},
            {"name": "Stone", "id": 16001, "count": 10, "marketable": True}]
    prices = {16001: 100_000}
    no_vp = grind.loot_value(loot, prices, False, 0)
    assert no_vp["trash"] == 1_500_000          # vendor: no tax
    assert no_vp["market"] == 650_000           # 1 M gross at 65 percent
    assert no_vp["total"] == 2_150_000 and no_vp["unknown"] == []
    vp = grind.loot_value(loot, prices, True, 0)
    assert vp["market"] == 845_000 and vp["total"] == 2_345_000   # 84.5 percent
    fame = grind.loot_value(loot, prices, True, 1.5)
    assert fame["market"] == 1_000_000 * 6500 * 13150 // 10 ** 8
    kinds = {i["name"]: i["kind"] for i in no_vp["items"]}
    assert kinds == {"Trash": "vendor", "Stone": "market"}


def test_loot_value_missing_price_flagged():
    loot = [{"name": "Stone", "id": 16001, "count": 3, "marketable": True},
            {"name": "NoId", "count": 2, "marketable": True},
            {"name": "Junk", "count": 5, "marketable": False}]
    v = grind.loot_value(loot, {}, False, 0)
    assert v["total"] == 0 and v["unknown"] == ["Stone", "NoId", "Junk"]
    assert all(i["kind"] == "unknown" and i["value"] == 0 for i in v["items"])


@pytest.mark.parametrize("item, price, want, diff", [
    ({"name": "T", "marketable": False, "vendor_price": 100}, None, "vendor", None),
    ({"name": "S", "marketable": True}, 1000, "market", None),
    ({"name": "B", "marketable": True, "vendor_price": 500}, 1000, "market", 150),   # 650 vs 500
    ({"name": "B", "marketable": True, "vendor_price": 700}, 1000, "vendor", 50),    # 650 vs 700
    ({"name": "B", "marketable": True, "vendor_price": 650}, 1000, "either", 0),
    ({"name": "B", "marketable": True, "vendor_price": 650}, None, "vendor", None),
    ({"name": "U", "marketable": True}, None, "unknown", None),
    ({"name": "U", "marketable": False}, None, "unknown", None),
])
def test_sell_or_vendor(item, price, want, diff):
    r = grind.sell_or_vendor(item, price, False, 0)
    assert r["choice"] == want and r["diff"] == diff


def _loot_svc(tmp_path, clock, prices=None, vp=False):
    tables = {"polly-forest": {"items": [
        {"id": 16001, "name": "Black Stone (Weapon)", "marketable": True}],
        "source": "https://x", "verified": False}}
    return grind.GrindService(Store(tmp_path / "store"), clock=clock, loot_tables=tables,
                              prices=(prices or {}).get,
                              tax=lambda: {"vp": vp, "fame_pct": 0})


def test_loot_only_session_shows_silver_per_h(tmp_path, clock):
    s = _loot_svc(tmp_path, clock, prices={16001: 200_000})
    s.add_spot("Polly's Forest")          # maps to plan 012 id polly-forest by name
    s.loot_item({"spot": "pollys-forest", "name": "Trash Pile", "marketable": False,
                 "vendor_price": 1000})
    s.start("pollys-forest")
    clock.advance(1800)
    doc = s.stop({"silver": 0, "trash": 2000,
                  "loot": [{"name": "Trash Pile", "count": 2000},
                           {"id": 16001, "count": 10}]})
    ses = doc["sessions"][0]
    assert ses["loot_value"]["trash"] == 2 * M
    assert ses["loot_value"]["market"] == 1_300_000
    assert ses["valued_silver"] == 3_300_000 and ses["silver_per_h"] == 6_600_000
    assert _spot(doc, "Polly's Forest")["silver_per_h"] == 6_600_000
    assert ses["loot"][0] == {"name": "Trash Pile", "id": None, "count": 2000,
                              "kind": "vendor", "unit": 1000, "value": 2 * M}


def test_typed_silver_unchanged_without_loot(tmp_path, clock):
    s = _loot_svc(tmp_path, clock)
    s.add_spot("Gyfin")
    doc = s.log({"spot": "gyfin", "minutes": 60, "silver": 5 * M, "trash": 0})
    ses = doc["sessions"][0]
    assert ses["valued_silver"] == 5 * M and ses["loot"] is None and ses["loot_value"] is None
    assert _spot(doc, "Gyfin")["silver_per_h"] == 5 * M


def test_log_with_loot_flags_unknown_price(tmp_path, clock):
    s = _loot_svc(tmp_path, clock)
    s.add_spot("Polly's Forest")
    doc = s.log({"spot": "pollys-forest", "minutes": 60, "silver": 7, "trash": 0,
                 "loot": [{"name": "black stone (weapon)", "count": 4}]})
    ses = doc["sessions"][0]
    assert ses["loot_value"]["unknown"] == ["Black Stone (Weapon)"]
    assert ses["valued_silver"] == 0


@pytest.mark.parametrize("loot", [
    "x", [{"name": "Nope", "count": 1}], [{"name": "Black Stone (Weapon)", "count": 0}],
    [{"name": "Black Stone (Weapon)", "count": 1, "extra": 1}], [{"count": 1}],
    [{"id": 16001, "count": 1}, {"name": "Black Stone (Weapon)", "count": 1}],
    [{"name": "Black Stone (Weapon)", "count": True}],
])
def test_bad_loot_rejected(tmp_path, clock, loot):
    s = _loot_svc(tmp_path, clock)
    s.add_spot("Polly's Forest")
    with pytest.raises(ValueError):
        s.log({"spot": "pollys-forest", "minutes": 5, "silver": 0, "trash": 0, "loot": loot})
    assert s.view()["sessions"] == []


def test_loot_item_upsert_forget_and_bad(tmp_path, clock):
    s = _loot_svc(tmp_path, clock)
    s.add_spot("Gyfin")
    s.loot_item({"spot": "gyfin", "name": "Rag", "marketable": False, "vendor_price": 5})
    s.loot_item({"spot": "gyfin", "name": "rag", "marketable": False, "vendor_price": 9})
    items = s.loot("gyfin")["items"]
    assert [(i["name"], i["vendor_price"], i["origin"]) for i in items] == [("rag", 9, "operator")]
    for bad in ({"spot": "gyfin", "name": "X", "marketable": False},
                {"spot": "nope", "name": "X", "marketable": True},
                {"spot": "gyfin", "name": "X", "marketable": 1},
                {"spot": "gyfin", "name": "X", "marketable": True, "id": -1}):
        with pytest.raises(ValueError):
            s.loot_item(bad)
    s.loot_forget({"spot": "gyfin", "name": "RAG"})
    assert s.loot("gyfin")["items"] == []
    with pytest.raises(ValueError):
        s.loot_forget({"spot": "gyfin", "name": "RAG"})


def test_loot_view_hints_and_table_merge(tmp_path, clock):
    s = _loot_svc(tmp_path, clock, prices={16001: 1000}, vp=True)
    s.add_spot("Polly's Forest")
    s.loot_item({"spot": "pollys-forest", "name": "Black Stone (Weapon)", "id": 16001,
                 "marketable": True, "vendor_price": 900})
    v = s.loot("pollys-forest")
    assert v["table"] == "polly-forest" and v["source"] == "https://x" and v["verified"] is False
    (it,) = v["items"]
    assert it["origin"] == "operator" and it["price"] == 1000 and it["net"] == 845
    assert it["hint"] == {"choice": "vendor", "vendor": 900, "market_net": 845, "diff": 55}
    with pytest.raises(ValueError):
        s.loot("nope")


def test_price_lookup_failure_degrades(tmp_path, clock):
    def boom(_iid):
        raise RuntimeError("down")
    s = grind.GrindService(Store(tmp_path / "store"), clock=clock,
                           loot_tables={"gyfin": {"items": [{"id": 1, "name": "A",
                                                             "marketable": True}],
                                                  "source": "https://x", "verified": False}},
                           prices=boom)
    s.add_spot("Gyfin")
    assert s.loot("gyfin")["items"][0]["price"] is None
    doc = s.log({"spot": "gyfin", "minutes": 5, "silver": 0, "trash": 0,
                 "loot": [{"name": "A", "count": 1}]})
    assert doc["sessions"][0]["loot_value"]["unknown"] == ["A"]


def test_corrupt_loot_fields_degrade(tmp_path, clock):
    st = Store(tmp_path / "store")
    st.put("grind", {"spots": [{"id": "a", "name": "A"}], "buffs": [], "next_sid": 3,
                     "loot_items": {"a": [{"name": 5}], "zz": "x"},
                     "sessions": [
                         {"id": "s1", "spot": "a", "started": T0.isoformat(), "minutes": 60,
                          "silver": 10, "trash": 0, "loot": "bad", "loot_value": {"total": "x"}},
                         {"id": "s2", "spot": "a", "started": T0.isoformat(), "minutes": 60,
                          "silver": 10, "trash": 0, "loot": [],
                          "loot_value": {"total": 120, "trash": 120, "market": 0,
                                         "unknown": []}}]})
    s = grind.GrindService(st, clock=clock, loot_tables={})
    doc = s.view()
    by = {x["id"]: x for x in doc["sessions"]}
    assert by["s1"]["valued_silver"] == 10 and by["s1"]["loot_value"] is None
    assert by["s2"]["valued_silver"] == 120
    assert s.loot("a")["items"] == []


def _arsha_fetch(price):
    def f(url, timeout):
        return json.dumps({"id": 16001, "sid": 0, "name": "Black Stone (Weapon)",
                           "basePrice": price, "lastSoldPrice": price}).encode()
    return f


def test_route_loot_get_and_post(tmp_path, clock):
    from server.ew import market
    mc = market.ArshaClient(fetch=_arsha_fetch(100_000), clock=clock,
                            cache_dir=tmp_path / "cache")
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          sse_interval=0.05, market_seed=[], grind_clock=clock,
                          market_client=mc)
    threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    try:
        _req(s, "POST", "/api/grind", {"add_spot": "Polly's Forest"})
        st, doc, _ = _req(s, "POST", "/api/grind",
                          {"loot_item": {"spot": "pollys-forest", "name": "Pile",
                                         "marketable": False, "vendor_price": 10}})
        assert st == 200
        st, doc, _ = _req(s, "GET", "/api/grind/loot?spot=pollys-forest")
        assert st == 200 and doc["table"] == "polly-forest"
        stone = next(i for i in doc["items"] if i["id"] == 16001)
        assert stone["price"] == 100_000 and stone["hint"]["choice"] == "market"
        st, doc, _ = _req(s, "POST", "/api/grind",
                          {"log": {"spot": "pollys-forest", "minutes": 60, "silver": 0,
                                   "trash": 0, "loot": [{"id": 16001, "count": 10},
                                                        {"name": "Pile", "count": 100}]}})
        assert st == 200 and doc["sessions"][0]["silver_per_h"] == 650_000 + 1000
        assert _req(s, "GET", "/api/grind/loot?spot=nope")[0] == 400
        assert _req(s, "GET", "/api/grind/loot")[0] == 400
        st, doc, _ = _req(s, "POST", "/api/grind",
                          {"loot_forget": {"spot": "pollys-forest", "name": "Pile"}})
        assert st == 200
    finally:
        s.shutdown()
        s.server_close()
