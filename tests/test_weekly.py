"""Plan 033: weekly content planner gated by level and gear."""

import datetime as dt
import http.client
import json
import threading
from pathlib import Path

import pytest

from server.ew import app as ewapp
from server.ew import brackets, market, today, weekly
from server.ew.store import Store

UTC = dt.timezone.utc
DATA = Path(weekly.__file__).resolve().parent / "data" / "weekly_content.json"
FIX = {"level": 58, "gs": {"ap": 240, "aap": None, "dp": None}}


def T(*a):
    return dt.datetime(*a, tzinfo=UTC)


class Clock:
    def __init__(self, when):
        self.t = when.timestamp()

    def __call__(self):
        return self.t

    def set(self, when):
        self.t = when.timestamp()


def _row(**kw):
    base = {"id": "x", "name": "X", "reset": {"every": "week", "weekday": 3, "at": "00:00"},
            "min_level": 60, "ap": 250, "dp": 300, "ap_kind": "main", "per_week": 1,
            "source": "https://example.invalid/x", "verified": "2026-07-02", "note": ""}
    base.update(kw)
    return base


def _rows():
    return {r["id"]: r for r in weekly.load_content()}


# -- data file --------------------------------------------------------------------

def test_tracked_file_ascii_lf_and_schema():
    raw = DATA.read_bytes()
    raw.decode("ascii")
    assert b"\r" not in raw
    rows = weekly.load_content()
    assert [r["id"] for r in rows] == ["black-shrine", "atoraxxion", "jetina", "loml-bosses",
                                       "garmoth", "edania-weeklies"]
    for r in json.loads(raw):
        assert set(r) == set(weekly.FIELDS)
        assert r["source"].startswith("https://")


def test_tracked_values_match_research():
    r = _rows()
    assert r["black-shrine"]["per_week"] == 5
    assert r["black-shrine"]["reset"] == {"every": "week", "weekday": 6, "at": "00:00"}
    assert r["atoraxxion"]["min_level"] == 60 and r["atoraxxion"]["ap"] == 250
    assert r["atoraxxion"]["dp"] == 300 and r["atoraxxion"]["ap_kind"] == "kutum"
    assert r["jetina"]["min_level"] == 60
    assert r["garmoth"]["per_week"] == 3
    assert r["edania-weeklies"]["ap"] == 350 and r["edania-weeklies"]["dp"] == 427
    for k in ("atoraxxion", "jetina", "loml-bosses", "garmoth", "edania-weeklies"):
        assert r[k]["reset"] == today.DEFAULT_RULES["weekly"]


@pytest.mark.parametrize("bad", [
    _row(extra=1),
    _row(id="Bad Id"),
    _row(name=""),
    _row(reset={"every": "day", "at": "00:00"}),
    _row(min_level=-1),
    _row(min_level=True),
    _row(ap=1000),
    _row(dp="300"),
    _row(ap_kind="awakening"),
    _row(per_week=0),
    _row(per_week=8),
    _row(source=""),
    _row(verified="yesterday"),
    _row(verified="2026-02-30"),
    _row(note=5),
    _row(name="caf" + chr(0xE9)),
])
def test_validate_row_rejects(bad):
    with pytest.raises(ValueError):
        weekly.validate_row(bad)


def test_load_rejects_duplicate_ids(tmp_path):
    p = tmp_path / "w.json"
    p.write_text(json.dumps([_row(), _row()]), encoding="ascii")
    with pytest.raises(ValueError):
        weekly.load_content(p)


def test_load_rejects_non_list(tmp_path):
    p = tmp_path / "w.json"
    p.write_text("{}", encoding="ascii")
    with pytest.raises(ValueError):
        weekly.load_content(p)


# -- gating -----------------------------------------------------------------------

def test_gate_eligible():
    g = weekly.gate(_row(), {"level": 61, "gs": {"ap": 250, "aap": None, "dp": 300}})
    assert g["state"] == "eligible" and g["needs"] == {} and g["unknown"] == []


def test_gate_locked_with_gaps():
    g = weekly.gate(_row(), {"level": 58, "gs": {"ap": 240, "aap": None, "dp": 290}})
    assert g["state"] == "locked"
    assert g["needs"] == {"level": 2, "ap": 10, "dp": 10}


def test_gate_known_gap_beats_unknown():
    g = weekly.gate(_row(), {"level": 58, "gs": {"ap": None, "aap": None, "dp": None}})
    assert g["state"] == "locked" and g["needs"] == {"level": 2}
    assert g["unknown"] == ["ap", "dp"]


def test_gate_unknown_requirement_or_stat():
    g = weekly.gate(_row(ap=None), {"level": 60, "gs": {"ap": 300, "aap": None, "dp": 300}})
    assert g["state"] == "unknown" and g["unknown"] == ["ap"]
    g = weekly.gate(_row(), {"level": None, "gs": {"ap": 300, "aap": None, "dp": 300}})
    assert g["state"] == "unknown" and g["unknown"] == ["level"]


def test_gate_zero_requirement_needs_no_stat():
    g = weekly.gate(_row(min_level=0, ap=0, dp=0), {"level": None, "gs": {}})
    assert g["state"] == "eligible"


def test_gate_aap_kind_reads_aap():
    row = _row(min_level=0, dp=0, ap_kind="aap")
    assert weekly.gate(row, {"level": 1, "gs": {"ap": 400, "aap": 240}})["needs"] == {"ap": 10}
    assert weekly.gate(row, {"level": 1, "gs": {"ap": 240, "aap": 260}})["state"] == "eligible"


def test_gate_bracket_hint():
    tables = brackets.load_tracked()
    summ = brackets.summary({"ap": 240, "aap": None, "dp": None}, tables)
    g = weekly.gate(_row(), dict(FIX), summ)
    # 240 sits in 235-244 (+40); the next bracket starts at 245 (+48).
    assert g["bracket"] == {"ap": 240, "to_next": 5, "next_gain": 8}


def test_gate_no_bracket_hint_without_ap_gap():
    tables = brackets.load_tracked()
    summ = brackets.summary({"ap": 260, "aap": None, "dp": 300}, tables)
    g = weekly.gate(_row(), {"level": 59, "gs": {"ap": 260, "dp": 300}}, summ)
    assert g["needs"] == {"level": 1} and g["bracket"] is None


def test_acceptance_lv58_240ap_only_black_shrine_eligible():
    out = [(r["id"], weekly.gate(r, FIX)["state"]) for r in weekly.load_content()]
    assert [i for i, s in out if s == "eligible"] == ["black-shrine"]
    st = dict(out)
    assert st["atoraxxion"] == "locked" and st["jetina"] == "locked"
    assert st["edania-weeklies"] == "locked"


# -- service: periods, ticks, cap -------------------------------------------------

def _svc(tmp_path, when, character=None, summary=None):
    clk = Clock(when)
    svc = weekly.WeeklyService(Store(tmp_path / "store"), clock=clk,
                               character=lambda: character or FIX,
                               brackets=lambda: summary)
    return svc, clk


def _r(view, rid):
    return next(r for r in view["rows"] if r["id"] == rid)


def test_view_shape(tmp_path):
    svc, _ = _svc(tmp_path, T(2026, 10, 5, 12))  # Monday
    v = svc.view()
    assert v["error"] is None and len(v["rows"]) == 6
    bs = _r(v, "black-shrine")
    assert bs["done"] == 0 and bs["per_week"] == 5 and bs["gate"]["state"] == "eligible"
    assert bs["next_reset"] == "2026-10-11T00:00:00+00:00"  # Sunday
    assert _r(v, "garmoth")["next_reset"] == "2026-10-08T00:00:00+00:00"  # Thursday
    assert v["character"] == {"level": 58, "ap": 240, "aap": None, "dp": None}


def test_tick_counts_and_caps(tmp_path):
    svc, _ = _svc(tmp_path, T(2026, 10, 5, 12))
    for n in range(1, 6):
        assert _r(svc.tick("black-shrine"), "black-shrine")["done"] == n
    with pytest.raises(ValueError):
        svc.tick("black-shrine")
    assert _r(svc.untick("black-shrine"), "black-shrine")["done"] == 4


def test_untick_at_zero_rejected(tmp_path):
    svc, _ = _svc(tmp_path, T(2026, 10, 5, 12))
    with pytest.raises(ValueError):
        svc.untick("garmoth")


def test_unknown_id_rejected(tmp_path):
    svc, _ = _svc(tmp_path, T(2026, 10, 5, 12))
    for bad in ("nope", "Bad Id", 5, None):
        with pytest.raises(ValueError):
            svc.tick(bad)


def test_sunday_vs_thursday_periods(tmp_path):
    svc, clk = _svc(tmp_path, T(2026, 10, 7, 12))  # Wednesday
    svc.tick("black-shrine")
    svc.tick("garmoth")
    clk.set(T(2026, 10, 8, 0, 0, 1))  # Thursday reset passed: garmoth resets, shrine not
    v = svc.view()
    assert _r(v, "garmoth")["done"] == 0 and _r(v, "black-shrine")["done"] == 1
    clk.set(T(2026, 10, 11, 0, 0, 0))  # Sunday 00:00: shrine resets
    assert _r(svc.view(), "black-shrine")["done"] == 0


def test_tick_prunes_old_periods(tmp_path):
    svc, clk = _svc(tmp_path, T(2026, 10, 7, 12))
    svc.tick("garmoth")
    clk.set(T(2026, 10, 9, 12))
    svc.tick("garmoth")
    assert len(svc.store.get("weekly")["ticks"]["garmoth"]) == 1


def test_corrupt_store_degrades(tmp_path):
    svc, _ = _svc(tmp_path, T(2026, 10, 5, 12))
    svc.store.put("weekly", {"ticks": {"garmoth": ["junk", 5, "2026-10-05T01:00:00+00:00"],
                                       "black-shrine": "nope", "ghost": ["x"]}})
    v = svc.view()
    assert _r(v, "garmoth")["done"] == 1 and _r(v, "black-shrine")["done"] == 0


def test_broken_data_file_never_breaks_view(tmp_path):
    p = tmp_path / "w.json"
    p.write_text("[", encoding="ascii")
    svc = weekly.WeeklyService(Store(tmp_path / "store"), clock=Clock(T(2026, 10, 5)),
                               character=lambda: FIX, data_file=p)
    v = svc.view()
    assert v["rows"] == [] and v["error"]
    with pytest.raises(ValueError):
        svc.tick("garmoth")


def test_broken_character_feed_degrades(tmp_path):
    def boom():
        raise RuntimeError("x")
    svc = weekly.WeeklyService(Store(tmp_path / "store"), clock=Clock(T(2026, 10, 5)),
                               character=boom, brackets=boom)
    v = svc.view()
    assert _r(v, "black-shrine")["gate"]["state"] == "eligible"
    assert _r(v, "atoraxxion")["gate"]["state"] == "unknown"


# -- routes -----------------------------------------------------------------------

def _no_network(url, timeout):
    raise AssertionError("test touched the network")


@pytest.fixture()
def srv(tmp_path):
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          sse_interval=0.05, market_seed=[],
                          market_client=market.ArshaClient(fetch=_no_network,
                                                           cache_dir=tmp_path / "cache"),
                          profile_cfg={})
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield s
    s.shutdown()
    s.server_close()


def _req(s, method, path, body=None):
    c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=5)
    data = None if body is None else json.dumps(body).encode()
    c.request(method, path, body=data,
              headers={"Content-Type": "application/json"} if data else {})
    r = c.getresponse()
    out = r.read()
    c.close()
    return r.status, (json.loads(out) if out else None)


def test_route_weekly_plan(srv):
    st, doc = _req(srv, "POST", "/api/progress", {"character": {"level": 58, "gs": {"ap": 240}}})
    assert st == 200
    st, doc = _req(srv, "GET", "/api/today")
    assert st == 200 and "items" in doc
    rows = doc["weekly_plan"]["rows"]
    assert [r["id"] for r in rows if r["gate"]["state"] == "eligible"] == ["black-shrine"]
    atx = next(r for r in rows if r["id"] == "atoraxxion")
    assert atx["gate"]["needs"] == {"level": 2, "ap": 10}
    assert atx["gate"]["bracket"] == {"ap": 240, "to_next": 5, "next_gain": 8}
    st, doc = _req(srv, "POST", "/api/today", {"weekly_tick": "black-shrine"})
    assert st == 200 and "items" in doc
    assert next(r for r in doc["weekly_plan"]["rows"] if r["id"] == "black-shrine")["done"] == 1
    st, doc = _req(srv, "POST", "/api/today", {"weekly_untick": "black-shrine"})
    assert st == 200
    st, doc = _req(srv, "POST", "/api/today", {"weekly_tick": "nope"})
    assert st == 400
    # Plain today writes still carry the plan.
    st, doc = _req(srv, "POST", "/api/today", {"add": {"title": "x", "kind": "daily"}})
    assert st == 200 and "weekly_plan" in doc
