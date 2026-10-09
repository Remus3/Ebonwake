"""Plan 035: enhancement EV calculator (per-step chance tables, expected
attempts and cost, Agris pity cap) and GET /api/deadeye/enhance.

Math on tracked, sourced tables and cached read-only prices only; no test
reaches the network (the market client's fetch raises).
"""

import http.client
import json
import math
import threading

import pytest

from server.ew import app as ewapp
from server.ew import enhance, market
from server.ew.store import Store


def _row(**kw):
    base = {"family": "fam", "step": "TET", "softcap_fs": 100, "max_pct_at_softcap": 10.01,
            "base_pct": 0.91, "source": "test", "verified": True}
    base.update(kw)
    return base


@pytest.fixture()
def table():
    return enhance.load_table()


@pytest.fixture()
def svc(tmp_path):
    return enhance.EnhanceService(Store(tmp_path / "store"), prices=lambda i: None)


# --- tracked table -------------------------------------------------------------

def test_tracked_table_validates(table):
    assert table["hard_cap_pct"] == 90
    assert table["cron_item_id"] == 16080
    keys = set()
    for r in table["rows"]:
        assert enhance.validate_row(r) == r
        assert (r["family"], r["step"]) not in keys
        keys.add((r["family"], r["step"]))
    assert {"kharazad", "sovereign", "edana", "blackstar"} <= {r["family"] for r in table["rows"]}


# research 0004 s1.1 bdogearguide rows: (family, step, softcap_fs, max %)
SOFTCAP_ROWS = [
    ("kharazad", "PRI", 40, 72.37), ("kharazad", "TET", 130, 40.46),
    ("kharazad", "PEN", 170, 34.38), ("kharazad", "NOV", 280, 9.28),
    ("sovereign", "PRI", 60, 59.85), ("sovereign", "TET", 100, 10.01),
    ("sovereign", "PEN", 150, 7.50), ("sovereign", "DEC", 300, 0.75),
    ("edana", "PRI", 120, 68.90), ("edana", "TET", 210, 24.20),
    ("edana", "PEN", 240, 14.87), ("edana", "NOV", 320, 2.32),
]


@pytest.mark.parametrize("family,step,fs,pct", SOFTCAP_ROWS)
def test_research_rows_reproduce_exactly_at_softcap(svc, family, step, fs, pct):
    c = svc.chance(family, step, fs)
    assert c["pct"] == pct and c["approx"] is False


@pytest.mark.parametrize("family,steps", [
    ("sovereign", {"PRI": 3, "DUO": 5, "TRI": 10, "TET": 20, "PEN": 30, "HEX": 35}),
    ("kharazad", {"PRI": 3, "DUO": 5, "TRI": 7, "TET": 8, "PEN": 10}),
    ("blackstar", {"TET": 12, "PEN": 20}),
])
def test_agris_thresholds_seeded(svc, family, steps):
    for step, t in steps.items():
        assert svc.row(family, step)["agris_threshold"] == t


def test_preview_values_flagged_unverified(table):
    for r in table["rows"]:
        if "crons_per_attempt" in r or "points" in r:
            assert r["verified"] is False and r["unverified"]


# --- chance --------------------------------------------------------------------

def test_base_point_exact_at_zero_fs():
    c = enhance.chance(_row(), 0)
    assert c == {"pct": 0.91, "approx": False}


def test_formula_below_softcap_is_approx():
    c = enhance.chance(_row(), 50)
    assert c["approx"] is True
    assert c["pct"] == pytest.approx(0.91 * 6)


def test_formula_capped_at_softcap_value():
    # 16.30 * (1 + 0.1 * 39) = 79.87 > 72.37: the soft-cap value caps it
    r = _row(base_pct=16.30, softcap_fs=40, max_pct_at_softcap=72.37)
    assert enhance.chance(r, 39)["pct"] == 72.37


def test_beyond_softcap_adds_two_percent_of_base_per_fs():
    c = enhance.chance(_row(), 150)
    assert c["approx"] is True
    assert c["pct"] == pytest.approx(10.01 + 0.91 * 0.02 * 50)


def test_hard_cap_90():
    r = _row(base_pct=20.0, softcap_fs=30, max_pct_at_softcap=80.0)
    assert enhance.chance(r, 1000)["pct"] == 90


def test_no_base_derives_from_softcap():
    r = _row(base_pct=None, softcap_fs=120, max_pct_at_softcap=68.90)
    del r["base_pct"]
    c = enhance.chance(r, 0)
    assert c["approx"] is True
    assert c["pct"] == pytest.approx(68.90 / 13)


def test_interpolation_inside_points():
    r = _row(softcap_fs=None, max_pct_at_softcap=None, points=[[150, 3.20], [160, 3.40]])
    del r["base_pct"]
    assert enhance.chance(r, 150) == {"pct": 3.20, "approx": False}
    assert enhance.chance(r, 160) == {"pct": 3.40, "approx": False}
    c = enhance.chance(r, 155)
    assert c["approx"] is False and c["pct"] == pytest.approx(3.30)


def test_outside_points_without_formula_is_none():
    r = _row(softcap_fs=None, max_pct_at_softcap=None, points=[[150, 3.20], [160, 3.40]])
    del r["base_pct"]
    assert enhance.chance(r, 100) == {"pct": None, "approx": True}


def test_points_win_over_formula_inside_their_span():
    r = _row(points=[[40, 5.0], [60, 7.0]])
    assert enhance.chance(r, 50) == {"pct": pytest.approx(6.0), "approx": False}
    assert enhance.chance(r, 70)["approx"] is True  # outside: the formula


def test_row_without_chance_data_is_none():
    r = _row(softcap_fs=None, max_pct_at_softcap=None)
    del r["base_pct"]
    assert enhance.chance(r, 50) == {"pct": None, "approx": True}


# --- expected ------------------------------------------------------------------

def _brute(p, threshold):
    """Sum over the truncated distribution: k fails then a success, k < T, or
    T fails then the guaranteed attempt."""
    q = 1 - p
    mean = sum((k + 1) * q ** k * p for k in range(threshold)) + (threshold + 1) * q ** threshold
    crons = sum((k + 1) * q ** k * p for k in range(threshold)) + threshold * q ** threshold
    return mean, crons


@pytest.mark.parametrize("p,t", [(0.0091, 20), (0.7237, 3), (0.0002, 35), (0.05, 1),
                                 (0.5, 12), (0.034, 20)])
def test_pity_truncation_matches_brute_force(p, t):
    out = enhance.attempts(p, t)
    mean, crons = _brute(p, t)
    assert abs(out["mean"] - mean) < 1e-9
    assert abs(out["crons_attempts"] - crons) < 1e-9
    assert out["cap"] == t + 1


def test_untruncated_geometric():
    out = enhance.attempts(0.25, None)
    assert out["mean"] == pytest.approx(4.0)
    assert out["crons_attempts"] == pytest.approx(4.0)
    assert out["cap"] is None


@pytest.mark.parametrize("p", [0.9, 0.5, 0.25, 0.0091, 0.0002, 0.1])
def test_p90_is_smallest_k_reaching_ninety_percent(p):
    k = enhance.attempts(p, None)["p90"]
    assert 1 - (1 - p) ** k >= 0.9 - 1e-12
    assert k == 1 or 1 - (1 - p) ** (k - 1) < 0.9


def test_p90_truncated_by_pity():
    assert enhance.attempts(0.0091, 20)["p90"] == 21
    assert enhance.attempts(0.5, 20)["p90"] == 4


def test_attempts_needs_positive_p():
    with pytest.raises(ValueError):
        enhance.attempts(0, 3)


def test_expected_with_crons_and_price():
    r = _row(crons_per_attempt=780, agris_threshold=20)
    out = enhance.expected(r, 100, True, {16080: 3_000_000}, cron_id=16080)
    a = enhance.attempts(0.1001, 20)
    assert out["p"] == pytest.approx(0.1001)
    assert out["attempts_mean"] == pytest.approx(a["mean"])
    assert out["pity_cap"] == 21 and out["agris_threshold"] == 20
    assert out["crons_mean"] == pytest.approx(780 * a["crons_attempts"])
    assert out["cost_mean_silver"] == pytest.approx(780 * a["crons_attempts"] * 3_000_000)
    assert out["missing_prices"] == []


def test_expected_missing_price_is_null_cost():
    r = _row(crons_per_attempt=780)
    out = enhance.expected(r, 100, True, {16080: None}, cron_id=16080)
    assert out["cost_mean_silver"] is None and out["missing_prices"] == [16080]


def test_expected_materials_billed_every_attempt():
    r = _row(materials=[[820979, 2]], agris_threshold=3)
    out = enhance.expected(r, 100, False, {820979: 1000}, cron_id=16080)
    assert out["cost_mean_silver"] == pytest.approx(2000 * enhance.attempts(0.1001, 3)["mean"])
    assert out["crons_mean"] == 0


def test_expected_nothing_to_bill_is_null_cost():
    out = enhance.expected(_row(), 100, False, {}, cron_id=16080)
    assert out["cost_mean_silver"] is None and out["cost_note"]


def test_expected_without_chance_keeps_pity():
    r = _row(softcap_fs=None, max_pct_at_softcap=None, agris_threshold=12)
    del r["base_pct"]
    out = enhance.expected(r, 100, False, {}, cron_id=16080)
    assert out["p"] is None and out["attempts_mean"] is None and out["attempts_p90"] is None
    assert out["pity_cap"] == 13 and out["cost_mean_silver"] is None


# --- schema --------------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    {"family": "Bad Name"}, {"family": "abc\n"}, {"step": "XYZ"}, {"softcap_fs": -1}, {"max_pct_at_softcap": 95},
    {"softcap_fs": None}, {"base_pct": 0}, {"points": [[160, 3.4], [150, 3.2]]},
    {"points": [[150, 3.2, 1]]}, {"points": []}, {"crons_per_attempt": -1},
    {"agris_threshold": 0}, {"agris_threshold": True}, {"materials": [[1, 0]]},
    {"source": ""}, {"verified": "yes"}, {"unverified": ["nope"]}, {"extra": 1},
])
def test_validate_row_rejects(bad):
    with pytest.raises(ValueError):
        enhance.validate_row(_row(**bad))


def test_validate_row_needs_required_keys():
    r = _row()
    del r["source"]
    with pytest.raises(ValueError):
        enhance.validate_row(r)


# --- service: overrides (store domain `enhance`) --------------------------------

def test_override_replaces_tracked_row(svc):
    row = dict(svc.row("sovereign", "TET"), max_pct_at_softcap=11.0, source="operator",
               verified=False)
    svc.override_set(row)
    assert svc.chance("sovereign", "TET", 100)["pct"] == 11.0
    assert svc.row("sovereign", "TET")["override"] is True
    svc.override_del({"family": "sovereign", "step": "TET"})
    assert svc.chance("sovereign", "TET", 100)["pct"] == 10.01
    assert "override" not in svc.row("sovereign", "TET")


def test_override_adds_new_family(svc):
    svc.override_set(_row(family="mine", step="PRI"))
    assert "mine" in svc.view()["families"]


def test_override_rejects_bad_row(svc):
    with pytest.raises(ValueError):
        svc.override_set(_row(max_pct_at_softcap=99))


def test_override_del_unknown(svc):
    with pytest.raises(ValueError):
        svc.override_del({"family": "sovereign", "step": "TET"})


def test_corrupt_override_store_degrades(tmp_path):
    st = Store(tmp_path / "store")
    st.put("enhance", {"rates": [{"family": "x"}, "junk", _row(family="ok", step="PRI")]})
    s = enhance.EnhanceService(st, prices=lambda i: None)
    assert s.row("ok", "PRI") is not None


def test_unknown_row_raises(svc):
    with pytest.raises(KeyError):
        svc.row("sovereign", "+3")


def test_service_evaluate_uses_prices(tmp_path):
    asked = []

    def prices(i):
        asked.append(i)
        return 2_000_000

    s = enhance.EnhanceService(Store(tmp_path / "store"), prices=prices)
    out = s.evaluate("sovereign", "TET", 100, True)
    assert asked == [16080]
    assert out["cost_mean_silver"] == pytest.approx(out["crons_mean"] * 2_000_000)
    assert out["family"] == "sovereign" and out["step"] == "TET" and out["fs"] == 100
    assert out["verified"] is False and out["unverified"] == ["crons_per_attempt"]


def test_unverified_used_follows_crons(svc):
    # Sovereign TET: verified chance, preview cron count
    assert svc.evaluate("sovereign", "TET", 100, False)["unverified_used"] == []
    assert svc.evaluate("sovereign", "TET", 100, True)["unverified_used"] == ["crons_per_attempt"]
    assert svc.evaluate("blackstar", "PEN", 155, False)["unverified_used"] == ["points"]


def test_unverified_row_without_field_list_is_wholly_unverified(svc):
    svc.override_set(_row(family="mine", step="PRI", verified=False))
    assert svc.evaluate("mine", "PRI", 0, False)["unverified_used"] == ["row"]


@pytest.mark.parametrize("arg", [{"family": ["x"], "step": "PRI"}, {"family": "x", "step": {}},
                                 {"family": "x"}, "x"])
def test_override_del_rejects_non_strings(svc, arg):
    with pytest.raises(ValueError):
        svc.override_del(arg)


def test_evaluate_without_crons_does_not_ask_cron_price(tmp_path):
    asked = []
    s = enhance.EnhanceService(Store(tmp_path / "store"), prices=lambda i: asked.append(i))
    s.evaluate("sovereign", "TET", 100, False)
    assert asked == []


# --- routes --------------------------------------------------------------------

def _no_network(url, timeout):
    raise AssertionError("test touched the network")


@pytest.fixture()
def srv(tmp_path):
    client = market.ArshaClient(fetch=_no_network, cache_dir=tmp_path / "cache")
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          sse_interval=0.05, market_seed=[], market_client=client,
                          profile_cfg={})
    t = threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    t.start()
    yield s
    s.shutdown()
    s.server_close()


def _req(s, method, path, body=None):
    c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=5)
    headers = {"Content-Type": "application/json"} if body is not None else {}
    c.request(method, path, body=json.dumps(body) if body is not None else None,
              headers=headers)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, json.loads(data)


def test_route_table_listing(srv):
    st, doc = _req(srv, "GET", "/api/deadeye/enhance")
    assert st == 200
    assert "sovereign" in doc["families"]
    assert any(r["family"] == "edana" and r["step"] == "PRI" for r in doc["rows"])


def test_route_evaluate(srv):
    st, doc = _req(srv, "GET", "/api/deadeye/enhance?family=sovereign&step=TET&fs=100&crons=0")
    assert st == 200
    assert doc["chance_pct"] == 10.01 and doc["approx"] is False
    assert doc["pity_cap"] == 21
    assert math.isclose(doc["attempts_mean"], enhance.attempts(0.1001, 20)["mean"])
    assert doc["cost_mean_silver"] is None  # no materials, crons off


def test_route_cost_null_without_cached_price(srv):
    st, doc = _req(srv, "GET", "/api/deadeye/enhance?family=sovereign&step=TET&fs=100&crons=1")
    assert st == 200 and doc["cost_mean_silver"] is None and doc["missing_prices"] == [16080]


def test_route_prices_from_cache_only(srv, tmp_path):
    from server.ew.store import atomic_write_json
    atomic_write_json(tmp_path / "cache" / "sublist_16080_0.json",
                      {"fetched_at": 0, "data": {"id": 16080, "lastSoldPrice": 3_000_000}})
    st, doc = _req(srv, "GET", "/api/deadeye/enhance?family=sovereign&step=TET&fs=100&crons=1")
    assert st == 200 and doc["missing_prices"] == []
    assert doc["cost_mean_silver"] == pytest.approx(doc["crons_mean"] * 3_000_000)


@pytest.mark.parametrize("q", [
    "family=sovereign", "step=TET", "family=sovereign&step=TET&fs=-1",
    "family=sovereign&step=TET&fs=abc", "family=sovereign&step=TET&fs=1000",
    "family=sovereign&step=TET&crons=2", "family=nope&step=TET", "family=sovereign&step=+3",
])
def test_route_rejects_bad_query(srv, q):
    st, doc = _req(srv, "GET", "/api/deadeye/enhance?" + q)
    assert st == 400 and doc["error"]


def test_route_post_rate_del_bad_shape_is_400(srv):
    st, doc = _req(srv, "POST", "/api/deadeye", {"rate_del": {"family": ["x"], "step": "PRI"}})
    assert st == 400 and doc["error"]


def test_route_post_override(srv):
    row = _row(family="sovereign", step="TET", max_pct_at_softcap=12.0, source="operator",
               verified=False)
    st, doc = _req(srv, "POST", "/api/deadeye", {"rate_set": row})
    assert st == 200 and any(r.get("override") for r in doc["rows"])
    st, doc = _req(srv, "GET", "/api/deadeye/enhance?family=sovereign&step=TET&fs=100")
    assert doc["chance_pct"] == 12.0
    st, _ = _req(srv, "POST", "/api/deadeye", {"rate_del": {"family": "sovereign",
                                                             "step": "TET"}})
    assert st == 200
    st, doc = _req(srv, "GET", "/api/deadeye/enhance?family=sovereign&step=TET&fs=100")
    assert doc["chance_pct"] == 10.01
