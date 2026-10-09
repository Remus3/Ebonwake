"""Plan 031: NA world boss table (sourced data), DST-correct next spawns, loot ticks.

Static sourced data plus operator ticks; nothing reads the game.
"""

import datetime as dt
import http.client
import json
import re
import threading
from pathlib import Path

import pytest

from server.ew import app as ewapp
from server.ew import bosses, market
from server.ew.store import Store

UTC = dt.timezone.utc
REPO = Path(__file__).resolve().parents[1]
RESEARCH = REPO / "docs" / "research" / "0003-deep-dive-progression-20261005.md"
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def T(*a):
    return dt.datetime(*a, tzinfo=UTC)


class Clock:
    def __init__(self, when):
        self.t = when.timestamp()

    def __call__(self):
        return self.t

    def set(self, when):
        self.t = when.timestamp()


# --- data file ---------------------------------------------------------------

def test_data_file_schema():
    doc = bosses.load_table()
    assert doc["tz"] == "PT" and doc["verified"] == "2026-09-24"
    assert doc["source"].startswith("https://www.naeu.playblackdesert.com/")
    assert doc["rules"] == {"despawn_min": 30, "short_despawn": {"Quint": 15, "Muraka": 15},
                            "garmoth_loot_per_week": 3}
    for s in doc["slots"]:
        assert set(s) == {"weekday", "at", "bosses"}
        assert 0 <= s["weekday"] <= 6 and re.match(r"^\d\d:\d\d$", s["at"])
        assert s["bosses"] and all(isinstance(b, str) and b for b in s["bosses"])
    raw = (REPO / "server" / "ew" / "data" / "world_bosses_na.json").read_bytes()
    raw.decode("ascii")


def _research_table():
    """{weekday: {at: [bosses]}} parsed from the research 0003 section 1.3 table."""
    out = {d: {} for d in range(7)}
    rows = [ln for ln in RESEARCH.read_text(encoding="utf-8").splitlines()
            if re.match(r"^\| \d\d:\d\d \|", ln)]
    assert rows, "research table not found"
    for ln in rows:
        cells = [c.strip() for c in ln.strip("|").split("|")]
        at, per_day = cells[0], cells[1:]
        assert len(per_day) == 7
        for d, cell in enumerate(per_day):
            if cell != "-":
                names = [re.sub(r"\s*\(.*\)$", "", n.strip()) for n in cell.split(",")]
                out[d][at] = names
    return out


def test_data_matches_research_table():
    want = _research_table()
    got = {d: {} for d in range(7)}
    for s in bosses.load_table()["slots"]:
        got[s["weekday"]][s["at"]] = s["bosses"]
    assert {d: len(v) for d, v in got.items()} == {d: len(v) for d, v in want.items()}
    assert got == want


def test_load_table_rejects_bad(tmp_path):
    p = tmp_path / "t.json"
    good = json.loads((REPO / "server" / "ew" / "data" / "world_bosses_na.json").read_text())
    for mut in (lambda d: d["slots"][0].update(weekday=7),
                lambda d: d["slots"][0].update(at="24:00"),
                lambda d: d["slots"][0].update(bosses=[]),
                lambda d: d.update(tz="UTC"),
                lambda d: d.pop("rules")):
        doc = json.loads(json.dumps(good))
        mut(doc)
        p.write_text(json.dumps(doc))
        with pytest.raises(ValueError):
            bosses.load_table(p)


# --- Pacific time -------------------------------------------------------------

@pytest.mark.parametrize("utc, want_offset", [
    (T(2026, 7, 1, 12), -7),
    (T(2026, 12, 1, 12), -8),
    (T(2026, 11, 1, 8, 59, 59), -7),    # 01:59:59 PDT
    (T(2026, 11, 1, 9, 0, 0), -8),      # 01:00:00 PST (fall back)
    (T(2027, 3, 14, 9, 59, 59), -8),    # 01:59:59 PST
    (T(2027, 3, 14, 10, 0, 0), -7),     # 03:00:00 PDT (spring forward)
])
def test_pt_offset(utc, want_offset):
    assert bosses.pt_offset_hours(utc) == want_offset


@pytest.mark.parametrize("pt, want", [
    (dt.datetime(2026, 11, 1, 0, 0), T(2026, 11, 1, 7)),     # Sun 00:00 still PDT
    (dt.datetime(2026, 11, 1, 10, 0), T(2026, 11, 1, 18)),   # Sun 10:00 now PST
    (dt.datetime(2027, 3, 14, 0, 0), T(2027, 3, 14, 8)),     # Sun 00:00 still PST
    (dt.datetime(2027, 3, 14, 10, 0), T(2027, 3, 14, 17)),   # Sun 10:00 now PDT
])
def test_pt_to_utc_dst_edges(pt, want):
    assert bosses.pt_to_utc(pt) == want


def test_utc_pt_roundtrip_year():
    t = T(2026, 1, 1)
    while t < T(2027, 1, 1):
        pt = bosses.utc_to_pt(t)
        if not (pt.month == 11 and pt.day == 1 and pt.hour == 1):  # ambiguous hour
            assert bosses.pt_to_utc(pt) == t
        t += dt.timedelta(minutes=37)


# --- next spawns ----------------------------------------------------------------

def test_muraka_cross_check():
    # Official Known Issues: NA Muraka Thu 21:00 UTC and Sun 00:00 UTC (summer).
    got = [s for s in bosses.next_spawns(T(2026, 10, 5, 12), n=60) if "Muraka" in s["bosses"]]
    assert [s["at_utc"] for s in got[:2]] == ["2026-10-08T21:00:00+00:00",
                                              "2026-10-11T00:00:00+00:00"]
    assert [s["at_pt"] for s in got[:2]] == ["2026-10-08T14:00:00-07:00",
                                             "2026-10-10T17:00:00-07:00"]


def test_next_spawns_shape_and_order():
    out = bosses.next_spawns(T(2026, 10, 5, 12), n=3)   # Mon 05:00 PDT
    assert [s["bosses"] for s in out] == [["Uturi", "Nouver"], ["Garmoth"],
                                          ["Sangoon", "Karanda"]]
    assert out[0] == {"bosses": ["Uturi", "Nouver"], "at_utc": "2026-10-05T17:00:00+00:00",
                      "at_pt": "2026-10-05T10:00:00-07:00", "day": "2026-10-05",
                      "despawn_min": 30}


def test_next_spawns_sunday_to_monday_wrap():
    now = T(2026, 10, 12, 5, 30)    # Sun 2026-10-11 22:30 PDT, after the last slot
    out = bosses.next_spawns(now, n=2)
    assert out[0]["bosses"] == ["Golden Pig King", "Kzarka"]
    assert out[0]["at_pt"] == "2026-10-12T00:00:00-07:00" and out[0]["day"] == "2026-10-12"
    assert out[1]["bosses"] == ["Uturi", "Nouver"]


def test_next_spawns_across_fall_back():
    now = T(2026, 11, 1, 7, 30)     # Sun 00:30 PDT; next is Sun 10:00 PST
    out = bosses.next_spawns(now, n=1)
    assert out[0]["at_utc"] == "2026-11-01T18:00:00+00:00"
    assert out[0]["at_pt"] == "2026-11-01T10:00:00-08:00"


def test_next_spawns_across_spring_forward():
    now = T(2027, 3, 14, 8, 30)     # Sun 00:30 PST; next is Sun 10:00 PDT
    out = bosses.next_spawns(now, n=1)
    assert out[0]["at_utc"] == "2027-03-14T17:00:00+00:00"


def test_next_spawns_at_exact_time_and_short_despawn():
    now = T(2026, 10, 8, 21)        # Thu 14:00 PDT exactly
    out = bosses.next_spawns(now, n=1)
    assert out[0]["bosses"] == ["Quint", "Muraka"] and out[0]["despawn_min"] == 15


def test_next_spawns_rejects_naive():
    with pytest.raises(ValueError):
        bosses.next_spawns(dt.datetime(2026, 10, 5), n=1)


# --- service: today, ticks, garmoth ----------------------------------------------

def _svc(tmp_path, when):
    clock = Clock(when)
    return bosses.BossService(Store(tmp_path / "store"), clock=clock), clock


def test_view_shape(tmp_path):
    svc, _ = _svc(tmp_path, T(2026, 10, 5, 19, 10))   # Mon 12:10 PDT, Garmoth up
    v = svc.view()
    assert v["tz"] == "PT" and v["dst_assumption"]
    assert len(v["next"]) == 3 and v["next"][0]["bosses"] == ["Sangoon", "Karanda"]
    assert v["today"]["day"] == "2026-10-05"
    rem = v["today"]["remaining"]
    assert [r["bosses"] for r in rem][0] == ["Garmoth"] and rem[0]["up"] is True
    assert all(not r["up"] for r in rem[1:])
    # Plan 032: every slot of the PT day, so a despawned boss can still be ticked.
    slots = v["today"]["slots"]
    assert [s["bosses"] for s in slots][:3] == [["Golden Pig King", "Kzarka"],
                                                ["Uturi", "Nouver"], ["Garmoth"]]
    assert [(s["up"], s["past"]) for s in slots[:4]] == [
        (False, True), (False, True), (True, False), (False, False)]
    assert len(slots) == 7 and [s["at_utc"] for s in slots] == sorted(s["at_utc"] for s in slots)
    assert v["garmoth"] == {"looted": 0, "cap": 3,
                            "week_reset": "2026-10-08T00:00:00+00:00"}
    assert v["looted"] == {}


def test_tick_untick(tmp_path):
    svc, _ = _svc(tmp_path, T(2026, 10, 5, 19, 10))
    v = svc.tick({"boss": "Kzarka", "day": "2026-10-05"})
    assert v["looted"] == {"2026-10-05": ["Kzarka"]}
    v = svc.tick({"boss": "Kzarka", "day": "2026-10-05"})       # idempotent
    assert v["looted"] == {"2026-10-05": ["Kzarka"]}
    v = svc.untick({"boss": "Kzarka", "day": "2026-10-05"})
    assert v["looted"] == {}
    svc.untick({"boss": "Kzarka", "day": "2026-10-05"})         # no-op


@pytest.mark.parametrize("arg", [
    None, "Kzarka", {}, {"boss": "Kzarka"}, {"day": "2026-10-05"},
    {"boss": "Kzarka", "day": "2026-10-05", "x": 1},
    {"boss": "Nobody", "day": "2026-10-05"},
    {"boss": "Vell", "day": "2026-10-05"},          # Vell does not spawn on Monday
    {"boss": "Kzarka", "day": "2026-13-05"},
    {"boss": "Kzarka", "day": "05/10/2026"},
    {"boss": "Kzarka", "day": "2026-10-06"},        # future PT day
    {"boss": "Kzarka", "day": "2026-09-21"},        # older than 7 days
    {"boss": 5, "day": "2026-10-05"},
])
def test_tick_validation(tmp_path, arg):
    svc, _ = _svc(tmp_path, T(2026, 10, 5, 19, 10))
    with pytest.raises(ValueError):
        svc.tick(arg)


def test_garmoth_weekly_count(tmp_path):
    svc, clock = _svc(tmp_path, T(2026, 10, 3, 20))           # Sat
    svc.tick({"boss": "Garmoth", "day": "2026-10-03"})
    clock.set(T(2026, 10, 5, 20))                              # Mon
    svc.tick({"boss": "Garmoth", "day": "2026-10-04"})
    v = svc.tick({"boss": "Garmoth", "day": "2026-10-05"})
    assert v["garmoth"]["looted"] == 3
    clock.set(T(2026, 10, 8, 0, 0, 1))                         # Thursday reset passed
    v = svc.view()
    assert v["garmoth"]["looted"] == 0
    assert v["garmoth"]["week_reset"] == "2026-10-15T00:00:00+00:00"


def test_old_ticks_pruned(tmp_path):
    svc, clock = _svc(tmp_path, T(2026, 10, 5, 20))
    svc.tick({"boss": "Kzarka", "day": "2026-10-05"})
    clock.set(T(2026, 10, 25, 20))
    svc.tick({"boss": "Kutum", "day": "2026-10-25"})
    assert list(svc.store.get("bosses")["looted"]) == ["2026-10-25|Kutum"]


def test_corrupt_store_degrades(tmp_path):
    svc, _ = _svc(tmp_path, T(2026, 10, 5, 20))
    svc.store.put("bosses", {"looted": {"bad": 1, "2026-10-05|Kzarka": "x",
                                        "2026-10-05|Kutum": "2026-10-05T20:00:00+00:00"}})
    assert svc.view()["looted"] == {"2026-10-05": ["Kutum"]}


# --- routes -----------------------------------------------------------------------

def _no_network(url, timeout):
    raise AssertionError("test touched the network")


@pytest.fixture()
def srv(tmp_path):
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          market_seed=[], profile_cfg={},
                          market_client=market.ArshaClient(fetch=_no_network,
                                                           cache_dir=tmp_path / "cache"),
                          bosses_clock=Clock(T(2026, 10, 5, 19, 10)))
    t = threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    t.start()
    yield s
    s.shutdown()
    s.server_close()


def _req(s, method, path, body=None):
    c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=5)
    headers = {"Content-Type": "application/json"} if body is not None else {}
    c.request(method, path, body=None if body is None else json.dumps(body).encode(),
              headers=headers)
    r = c.getresponse()
    out = r.read()
    c.close()
    return r.status, json.loads(out)


def test_route_get(srv):
    st, doc = _req(srv, "GET", "/api/bosses")
    assert st == 200 and len(doc["next"]) == 3 and "today" in doc and "garmoth" in doc


def test_route_post_tick_untick(srv):
    st, doc = _req(srv, "POST", "/api/bosses", {"tick": {"boss": "Garmoth", "day": "2026-10-05"}})
    assert st == 200 and doc["looted"] == {"2026-10-05": ["Garmoth"]}
    assert doc["garmoth"]["looted"] == 1
    st, doc = _req(srv, "POST", "/api/bosses",
                   {"untick": {"boss": "Garmoth", "day": "2026-10-05"}})
    assert st == 200 and doc["looted"] == {}


@pytest.mark.parametrize("body", [
    {}, {"tick": {"boss": "Nobody", "day": "2026-10-05"}}, {"add": {}},
    {"tick": {"boss": "Kzarka", "day": "2026-10-05"}, "untick": {}},
])
def test_route_post_bad(srv, body):
    assert _req(srv, "POST", "/api/bosses", body)[0] == 400
