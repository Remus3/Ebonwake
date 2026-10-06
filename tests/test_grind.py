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
    assert doc["active"] == {"spot": "oluns-valley", "started": T0.isoformat(), "elapsed_s": 0}
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
    threading.Thread(target=s.serve_forever, daemon=True).start()
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
