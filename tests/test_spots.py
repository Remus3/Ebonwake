"""Plan 012: grind spot recommender - data schema, ranking, unlocks, route.

Ranking runs on a fixture table so the tracked community data can change
without touching these tests; the tracked file is schema-tested on its own.
No network, no game client.
"""

import http.client
import json
import threading

import pytest

from server.ew import app as ewapp
from server.ew import spots
from server.ew.store import Store


def _row(sid, ap, dp, lvl, xp, silver, name=None):
    return {"id": sid, "name": name or sid.title(), "region": "Test", "ap_min": ap,
            "dp_min": dp, "level_min": lvl, "xp_tier": xp, "silver_tier": silver,
            "notes": "community recommendation, verify", "source": "fixture",
            "verified": "2026-10-05"}


FIXTURE = [
    _row("low", 100, 150, 50, 2, 1),
    _row("mid-a", 200, 250, 56, 4, 2),
    _row("mid-b", 220, 260, 56, 4, 3),
    _row("mid-c", 210, 300, 56, 3, 3),
    _row("high", 280, 350, 60, 4, 4),
    _row("top", 300, 400, 61, 5, 5),
    _row("lvl", 150, 150, 65, 5, 1),
]


def _ids(rows):
    return [r["id"] for r in rows]


# --- data file schema ---------------------------------------------------------

def test_tracked_table_passes_schema():
    rows = spots.load_table()
    assert len(rows) >= 10
    assert len({r["id"] for r in rows}) == len(rows)
    for r in rows:
        assert set(r) == set(spots.FIELDS)
        assert r["source"].strip() and spots.DATE_RE.match(r["verified"])
        assert "verify" in r["notes"]
        assert 1 <= r["xp_tier"] <= 5 and 1 <= r["silver_tier"] <= 5


def test_tracked_table_is_ascii_lf():
    raw = spots.DATA_FILE.read_bytes()
    raw.decode("ascii")
    assert b"\r" not in raw


@pytest.mark.parametrize("patch", [
    {"source": ""}, {"source": "  "}, {"verified": "2026-13-01"}, {"verified": None},
    {"ap_min": -1}, {"dp_min": 1000}, {"level_min": 0}, {"level_min": 71},
    {"xp_tier": 0}, {"silver_tier": 6}, {"xp_tier": True}, {"ap_min": 1.5},
    {"id": "Bad Id"}, {"name": ""}, {"region": 3}, {"notes": None}, {"extra": 1},
])
def test_validate_row_rejects(patch):
    row = dict(_row("ok", 1, 1, 1, 1, 1), **patch)
    with pytest.raises(ValueError):
        spots.validate_row(row)


def test_validate_row_rejects_missing_field():
    row = _row("ok", 1, 1, 1, 1, 1)
    del row["source"]
    with pytest.raises(ValueError):
        spots.validate_row(row)


def test_validate_table_rejects_duplicate_ids_and_non_list():
    with pytest.raises(ValueError):
        spots.validate_table([_row("a", 1, 1, 1, 1, 1), _row("a", 2, 2, 2, 2, 2)])
    with pytest.raises(ValueError):
        spots.validate_table({"a": 1})


def test_load_table_bad_file_raises(tmp_path):
    p = tmp_path / "t.json"
    p.write_text("not json", encoding="utf-8")
    with pytest.raises(ValueError):
        spots.load_table(p)


# --- ranking ------------------------------------------------------------------

def test_eligible_only_and_score_by_goal_tier():
    out = spots.rank(FIXTURE, ap=230, dp=310, level=58, goal="silver")
    # eligible: low, mid-a, mid-b, mid-c; silver tiers 1,2,3,3
    assert _ids(out["top"]) == ["mid-b", "mid-c", "mid-a"]
    out = spots.rank(FIXTURE, ap=230, dp=310, level=58, goal="xp")
    assert _ids(out["top"]) == ["mid-b", "mid-a", "mid-c"]


def test_tie_break_smallest_non_negative_ap_headroom():
    # mid-b (headroom 10) beats mid-c (headroom 20) on equal silver tier 3
    out = spots.rank(FIXTURE, ap=230, dp=310, level=58, goal="silver")
    assert out["top"][0]["id"] == "mid-b" and out["top"][0]["ap_headroom"] == 10
    assert out["top"][1]["ap_headroom"] == 20


def test_boundaries_inclusive():
    out = spots.rank(FIXTURE, ap=300, dp=400, level=61, goal="xp")
    assert out["top"][0]["id"] == "top"
    out = spots.rank(FIXTURE, ap=299, dp=400, level=61, goal="xp")
    assert "top" not in _ids(out["top"])


def test_level_gate():
    out = spots.rank(FIXTURE, ap=999, dp=999, level=64, goal="xp")
    assert "lvl" not in _ids(out["top"])
    out = spots.rank(FIXTURE, ap=999, dp=999, level=65, goal="xp")
    assert _ids(out["top"])[:2] == ["top", "lvl"] or _ids(out["top"])[:2] == ["lvl", "top"]


def test_top_n_and_unlocks_n():
    out = spots.rank(FIXTURE, ap=999, dp=999, level=70, goal="xp")
    assert len(out["top"]) == 3 and out["unlocks"] == []
    out = spots.rank(FIXTURE, ap=0, dp=0, level=1, goal="xp")
    assert out["top"] == [] and len(out["unlocks"]) == 2


def test_unlocks_name_missing_ap_dp_level():
    out = spots.rank(FIXTURE, ap=230, dp=310, level=58, goal="silver")
    # best eligible silver tier is 3; unlocks prefer tier >= 3, smallest gap first
    u = out["unlocks"]
    assert _ids(u) == ["high", "top"]
    assert u[0]["need_ap"] == 50 and u[0]["need_dp"] == 40 and u[0]["need_level"] == 2
    assert u[1]["need_ap"] == 70 and u[1]["need_dp"] == 90 and u[1]["need_level"] == 3


def test_unlocks_fill_from_lower_tiers_when_needed():
    out = spots.rank(FIXTURE, ap=280, dp=350, level=60, goal="silver")
    # eligible best silver tier 4 (high); tier >= 4 ineligible: top only, then fill
    assert _ids(out["unlocks"])[0] == "top"
    assert len(out["unlocks"]) == 2


def test_result_rows_carry_tiers_and_score():
    out = spots.rank(FIXTURE, ap=230, dp=310, level=58, goal="xp")
    r = out["top"][0]
    for k in ("id", "name", "region", "ap_min", "dp_min", "level_min", "xp_tier",
              "silver_tier", "notes", "source", "verified", "score", "ap_headroom"):
        assert k in r
    assert r["score"] == r["xp_tier"]


def test_rank_rejects_bad_goal():
    with pytest.raises(ValueError):
        spots.rank(FIXTURE, ap=1, dp=1, level=1, goal="fun")


def test_rank_input_not_mutated():
    before = json.dumps(FIXTURE)
    spots.rank(FIXTURE, ap=230, dp=310, level=58, goal="xp")
    assert json.dumps(FIXTURE) == before


# --- service: character from progress, query overrides, logged silver/h -------

def _svc(character=None, grind=None, table=FIXTURE):
    char = character if character is not None else {
        "level": 58, "gs": {"ap": 230, "aap": 200, "dp": 310}}
    return spots.SpotsService(table, character=lambda: char,
                              grind=lambda: grind or {"spots": []})


def test_service_uses_progress_character():
    out = _svc().view({"goal": ["silver"]})
    assert out["goal"] == "silver"
    assert out["input"] == {"ap": 230, "dp": 310, "level": 58}
    assert out["from"] == {"ap": "progress", "dp": "progress", "level": "progress"}
    assert _ids(out["top"]) == ["mid-b", "mid-c", "mid-a"]


def test_service_ap_is_the_higher_of_ap_and_aap():
    out = _svc({"level": 58, "gs": {"ap": 200, "aap": 230, "dp": 310}}).view({})
    assert out["input"]["ap"] == 230
    out = _svc({"level": 58, "gs": {"ap": None, "aap": 230, "dp": 310}}).view({})
    assert out["input"]["ap"] == 230


def test_service_query_overrides_what_if():
    out = _svc().view({"goal": ["xp"], "ap": ["300"], "dp": ["400"], "level": ["61"]})
    assert out["input"] == {"ap": 300, "dp": 400, "level": 61}
    assert out["from"] == {"ap": "query", "dp": "query", "level": "query"}
    assert out["top"][0]["id"] == "top"


def test_service_default_goal_is_xp():
    assert _svc().view({})["goal"] == "xp"


@pytest.mark.parametrize("q", [
    {"goal": ["fun"]}, {"ap": ["x"]}, {"ap": ["-1"]}, {"ap": ["1000"]}, {"dp": ["1.5"]},
    {"level": ["0"]}, {"level": ["71"]}, {"ap": ["1", "2"]},
])
def test_service_bad_query_raises(q):
    with pytest.raises(ValueError):
        _svc().view(q)


def test_service_missing_character_reports_missing():
    out = _svc({"level": None, "gs": {"ap": None, "aap": None, "dp": 310}}).view({})
    assert out["missing"] == ["ap", "level"]
    assert out["top"] == [] and out["unlocks"] == []
    out = _svc({"level": None, "gs": {"ap": None, "aap": None, "dp": 310}}).view(
        {"ap": ["230"], "level": ["58"]})
    assert out["missing"] == [] and out["top"]


def test_service_logged_silver_per_h_by_name():
    grind = {"spots": [{"id": "x", "name": "MID-B", "sessions": 2, "minutes": 120,
                        "silver_per_h": 500000000},
                       {"id": "y", "name": "Mid-C", "sessions": 0, "minutes": 0,
                        "silver_per_h": 0}]}
    out = _svc(grind=grind).view({"goal": ["silver"]})
    by = {r["id"]: r for r in out["top"]}
    assert by["mid-b"]["logged_silver_per_h"] == 500000000
    assert by["mid-c"]["logged_silver_per_h"] is None  # never logged
    assert by["mid-a"]["logged_silver_per_h"] is None


def test_service_bad_table_degrades():
    svc = spots.SpotsService.from_file(path=spots.DATA_FILE.parent / "nope.json",
                                       character=lambda: {}, grind=lambda: {"spots": []})
    out = svc.view({"ap": ["1"], "dp": ["1"], "level": ["1"]})
    assert out["status"] == "error" and out["top"] == [] and out["error"]


def test_service_ok_status_and_count():
    out = _svc().view({})
    assert out["status"] == "ok" and out["count"] == len(FIXTURE)
    assert "verify" in out["disclaimer"]


# --- route --------------------------------------------------------------------

@pytest.fixture()
def srv(tmp_path):
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          market_seed=[], profile_cfg={})
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield s
    s.shutdown()
    s.server_close()


def _get(s, path):
    c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=5)
    c.request("GET", path)
    r = c.getresponse()
    body = json.loads(r.read().decode("utf-8"))
    c.close()
    return r.status, body


def test_route_uses_progress_character(srv):
    srv.progress.set_character({"level": 58, "gs": {"ap": 250, "dp": 310}})
    code, body = _get(srv, "/api/spots?goal=silver")
    assert code == 200 and body["goal"] == "silver" and body["status"] == "ok"
    assert body["input"] == {"ap": 250, "dp": 310, "level": 58}
    assert 1 <= len(body["top"]) <= 3 and len(body["unlocks"]) <= 2
    for r in body["top"]:
        assert r["ap_min"] <= 250 and r["dp_min"] <= 310 and r["level_min"] <= 58


def test_route_what_if_and_logged(srv):
    srv.grind.add_spot("Sausan Garrison")
    sid = srv.grind.view()["spots"][0]["id"]
    srv.grind.log({"spot": sid, "minutes": 60, "silver": 300000000, "trash": 1})
    code, body = _get(srv, "/api/spots?goal=xp&ap=195&dp=255&level=56")
    assert code == 200 and body["from"]["ap"] == "query"
    sausan = [r for r in body["top"] if r["id"] == "sausan-garrison"]
    assert sausan and sausan[0]["logged_silver_per_h"] == 300000000


def test_route_bad_query_400(srv):
    code, body = _get(srv, "/api/spots?goal=fun")
    assert code == 400 and "goal" in body["error"]
    code, _ = _get(srv, "/api/spots?ap=abc")
    assert code == 400


def test_route_get_only(srv):
    c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
    c.request("POST", "/api/spots", body=b"{}", headers={"Content-Type": "application/json"})
    r = c.getresponse()
    r.read()
    c.close()
    assert r.status == 404
