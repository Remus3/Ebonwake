"""Plan 005 slice A: grind store domain, silver/h math, buff timers, routes.

No network, no game client: everything is operator input plus a seed of buff
names. The clock is injected so elapsed/left times are exact.
"""

import datetime as dt
import http.client
import json
import threading

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
