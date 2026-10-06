"""Plan 037: shopping list from the enhancement plan (materials x cached arsha
prices, can-afford-by) and GET /api/deadeye/shopping.

Math on the plan 035 rate rows, the plan 007 steps and cached read-only prices;
no test reaches the network (the market client's fetch raises).
"""

import datetime as dt
import http.client
import json
import threading

import pytest

from server.ew import app as ewapp
from server.ew import deadeye, enhance, market, shopping
from server.ew.store import Store, atomic_write_json

T0 = 1_790_000_000  # 2026-09-21T14:13:20Z
CRON = 16080


def _rows():
    base = {"source": "test", "verified": True}
    return [
        # p = 50 % at fs 0 (soft cap 0) -> mean 2 attempts
        dict(base, family="fam", step="PRI", softcap_fs=0, max_pct_at_softcap=50.0,
             materials=[[100, 2]], crons_per_attempt=10),
        # p = 25 % -> mean 4 attempts
        dict(base, family="fam", step="DUO", softcap_fs=0, max_pct_at_softcap=25.0,
             materials=[[100, 1], [200, 3]]),
        # p = 50 %, Agris threshold 1 -> mean 1.5, crons on 1 attempt
        dict(base, family="pity", step="PRI", softcap_fs=0, max_pct_at_softcap=50.0,
             materials=[[300, 1]], crons_per_attempt=4, agris_threshold=1),
        dict(base, family="nodata", step="PRI", softcap_fs=None, max_pct_at_softcap=None,
             materials=[[400, 1]]),
    ]


TABLE = {"schema": 1, "hard_cap_pct": 90, "cron_item_id": CRON, "rows": _rows()}
PRICES = {100: 1000, 200: 50, 300: 7, CRON: 3_000_000}


class World:
    def __init__(self, tmp_path, prices=None, sph=10_000_000):
        self.store = Store(tmp_path / "store")
        self.clock = lambda: T0
        self.deadeye = deadeye.DeadeyeService(self.store, clock=self.clock)
        self.enhance = enhance.EnhanceService(self.store, prices=lambda i: None, table=TABLE)
        self.prices = dict(PRICES if prices is None else prices)
        self.sph = sph
        self.watched = set()
        self.svc = shopping.ShoppingService(
            self.store, plan=lambda: self.deadeye.view()["plan"], enhance=self.enhance,
            quote=self.quote, name=lambda i: {100: "Black Stone"}.get(i),
            silver_per_h=lambda: self.sph, watched=lambda: set(self.watched), clock=self.clock)

    def quote(self, item_id):
        p = self.prices.get(item_id)
        return {"price": p, "preorder": "capped" if item_id == 200 else None}

    def add(self, item, current, target):
        return self.deadeye.add_step({"item": item, "current": current, "target": target})["plan"][-1]


@pytest.fixture()
def w(tmp_path):
    return World(tmp_path)


def _lines(body):
    return {ln["id"]: ln for ln in body["lines"]}


# --- totals ---------------------------------------------------------------------

def test_empty_plan(w):
    body = w.svc.view()
    assert body["lines"] == [] and body["total"] == 0 and body["steps"] == []
    assert body["settings"] == {"silver_on_hand": 0, "hours_per_day": 3}


def test_totals_over_levels_and_crons(w):
    st = w.add("Fam Ring", "+15", "DUO")
    w.svc.step({"id": st["id"], "crons": True})
    body = w.svc.view()
    lines = _lines(body)
    assert lines[100]["qty"] == 8 and lines[100]["unit"] == 1000 and lines[100]["total"] == 8000
    assert lines[100]["name"] == "Black Stone"
    assert lines[200]["qty"] == 12 and lines[200]["total"] == 600
    assert lines[200]["preorder"] == "capped"
    assert lines[CRON]["qty"] == 20 and lines[CRON]["total"] == 60_000_000
    assert body["total"] == 60_008_600
    assert body["missing_prices"] == []
    step = body["steps"][0]
    assert step["family"] == "fam" and step["family_source"] == "guess"
    assert [lv["step"] for lv in step["levels"]] == ["PRI", "DUO"]
    assert step["levels"][0]["attempts_mean"] == pytest.approx(2.0)


def test_crons_off_by_default(w):
    w.add("Fam Ring", "+15", "PRI")
    assert CRON not in _lines(w.svc.view())


def test_agris_pity_truncates_attempts(w):
    st = w.add("Pity Belt", "+15", "PRI")
    w.svc.step({"id": st["id"], "crons": True})
    lines = _lines(w.svc.view())
    assert lines[300]["expected"] == pytest.approx(1.5) and lines[300]["qty"] == 2
    assert lines[CRON]["expected"] == pytest.approx(4.0) and lines[CRON]["qty"] == 4


def test_done_steps_excluded(w):
    st = w.add("Fam Ring", "+15", "PRI")
    w.deadeye.step_done({"id": st["id"], "done": True})
    body = w.svc.view()
    assert body["lines"] == [] and body["steps"] == []


def test_same_id_sums_across_steps(w):
    w.add("Fam Ring", "+15", "PRI")
    w.add("Fam Necklace", "+15", "PRI")
    assert _lines(w.svc.view())[100]["qty"] == 8


def test_watched_flag(w):
    w.add("Fam Ring", "+15", "PRI")
    w.watched.add(100)
    assert _lines(w.svc.view())[100]["watched"] is True


# --- missing price / missing data ----------------------------------------------

def test_missing_price_line_and_null_total(tmp_path):
    w = World(tmp_path, prices={100: 1000})
    w.add("Fam Ring", "+15", "DUO")
    body = w.svc.view()
    ln = _lines(body)[200]
    assert ln["unit"] is None and ln["total"] is None and ln["note"] == "missing price"
    assert body["total"] is None and body["priced_total"] == 8000
    assert body["missing_prices"] == [200]
    assert body["can_afford_by"] is None


def test_missing_lines_sort_first(tmp_path):
    w = World(tmp_path, prices={100: 1000})
    w.add("Fam Ring", "+15", "DUO")
    assert [ln["id"] for ln in w.svc.view()["lines"]] == [200, 100]


def test_no_family_step_is_noted(w):
    w.add("Mystery Bow", "+15", "PRI")
    body = w.svc.view()
    assert body["lines"] == []
    assert body["steps"][0]["family"] is None and body["steps"][0]["note"] == "no gear family"


def test_level_without_row_or_chance_is_skipped(w):
    w.add("Fam Ring", "PRI", "TRI")  # DUO has a row, TRI does not
    w.add("Nodata Ring", "+15", "PRI")
    steps = w.svc.view()["steps"]
    tri = steps[0]["levels"][1]
    assert tri["step"] == "TRI" and tri["attempts_mean"] is None and tri["note"] == "no rate row"
    nd = steps[1]["levels"][0]
    assert nd["attempts_mean"] is None and nd["note"] == "no chance data at this FS"
    assert 400 not in _lines(w.svc.view())


def test_tiny_chance_override_never_overflows(w):
    w.enhance.override_set({"family": "fam", "step": "PRI", "softcap_fs": 0,
                            "max_pct_at_softcap": 1e-320, "materials": [[100, 1]],
                            "source": "operator", "verified": False})
    w.add("Fam Ring", "+15", "PRI")
    body = w.svc.view()
    assert body["lines"] == [] and body["steps"][0]["levels"][0]["note"] == "chance too small to total"


def test_explicit_family_and_fs(w):
    st = w.add("Mystery Bow", "+15", "PRI")
    w.svc.step({"id": st["id"], "family": "fam", "fs": 5})
    step = w.svc.view()["steps"][0]
    assert step["family"] == "fam" and step["family_source"] == "set" and step["fs"] == 5
    w.svc.step({"id": st["id"], "family": None})
    assert w.svc.view()["steps"][0]["family"] is None


# --- can afford by -------------------------------------------------------------

def _date(days):
    return (dt.datetime.fromtimestamp(T0, dt.timezone.utc).date()
            + dt.timedelta(days=days)).isoformat()


def test_afford_date(w):
    st = w.add("Fam Ring", "+15", "DUO")
    w.svc.step({"id": st["id"], "crons": True})
    w.svc.set({"silver_on_hand": 8_600})
    body = w.svc.view()
    # need 60 M at 10 M/h x 3 h/day = 30 M/day -> 2 days
    assert body["afford"]["need"] == 60_000_000 and body["afford"]["days"] == 2
    assert body["can_afford_by"] == _date(2)
    w.svc.set({"hours_per_day": 1.5})  # 15 M/day -> 4 days
    assert w.svc.view()["can_afford_by"] == _date(4)


def test_afford_zero_rate_is_null(tmp_path):
    w = World(tmp_path, sph=0)
    w.add("Fam Ring", "+15", "PRI")
    body = w.svc.view()
    assert body["can_afford_by"] is None and body["afford"]["reason"] == "no grind silver/h logged"


def test_afford_rate_source_none_is_null(tmp_path):
    w = World(tmp_path, sph=None)
    w.add("Fam Ring", "+15", "PRI")
    assert w.svc.view()["can_afford_by"] is None


def test_afford_already_covered_is_today(w):
    w.add("Fam Ring", "+15", "PRI")
    w.svc.set({"silver_on_hand": 10 ** 9})
    body = w.svc.view()
    assert body["can_afford_by"] == _date(0) and body["afford"]["days"] == 0
    assert body["afford"]["need"] == 0


def test_afford_rate_error_degrades(tmp_path):
    w = World(tmp_path)

    def boom():
        raise RuntimeError("grind down")
    w.svc.silver_per_h = boom
    w.add("Fam Ring", "+15", "PRI")
    assert w.svc.view()["can_afford_by"] is None


def test_average_silver_per_h_is_minute_weighted():
    spots = [{"minutes": 60, "silver_per_h": 100}, {"minutes": 180, "silver_per_h": 300},
             {"minutes": 0, "silver_per_h": 0}]
    assert shopping.average_silver_per_h(spots) == 250
    assert shopping.average_silver_per_h([]) is None
    assert shopping.average_silver_per_h([{"minutes": "x", "silver_per_h": 1}]) is None


# --- writes --------------------------------------------------------------------

@pytest.mark.parametrize("arg", [
    {}, {"silver_on_hand": -1}, {"silver_on_hand": 1.5}, {"silver_on_hand": True},
    {"silver_on_hand": 10 ** 16}, {"hours_per_day": 0}, {"hours_per_day": 25},
    {"hours_per_day": "3"}, {"hours_per_day": float("nan")}, {"x": 1}, [], "3",
])
def test_set_rejects(w, arg):
    with pytest.raises(ValueError):
        w.svc.set(arg)


def test_set_roundtrip(w):
    body = w.svc.set({"silver_on_hand": 5, "hours_per_day": 2})
    assert body["settings"] == {"silver_on_hand": 5, "hours_per_day": 2}
    assert w.svc.set({"hours_per_day": 4})["settings"]["silver_on_hand"] == 5


def test_step_rejects(w):
    st = w.add("Fam Ring", "+15", "PRI")
    for arg in [{"id": st["id"]}, {"id": "d999", "fs": 1}, {"id": st["id"], "fs": -1},
                {"id": st["id"], "fs": 1000}, {"id": st["id"], "fs": True},
                {"id": st["id"], "crons": 1}, {"id": st["id"], "family": "nope"},
                {"id": st["id"], "family": 3}, {"id": st["id"], "x": 1}, "d1", {"id": 1, "fs": 1}]:
        with pytest.raises(ValueError):
            w.svc.step(arg)


def test_corrupt_store_degrades(w):
    w.add("Fam Ring", "+15", "PRI")
    w.store.put("shopping", {"silver_on_hand": "lots", "hours_per_day": -2,
                             "steps": {"d1": {"family": 7, "fs": "x", "crons": "y"},
                                       "bad": 3}})
    body = w.svc.view()
    assert body["settings"] == {"silver_on_hand": 0, "hours_per_day": 3}
    assert body["steps"][0]["family"] == "fam" and body["steps"][0]["fs"] == 0
    w.store.put("shopping", {"steps": []})
    assert w.svc.view()["settings"]["hours_per_day"] == 3


def test_family_guess():
    fams = ["blackstar", "kharazad", "old_moon"]
    assert shopping.family_guess("Kharazad Ring", fams) == "kharazad"
    assert shopping.family_guess("ring of the old moon", fams) == "old_moon"
    assert shopping.family_guess("Bow", fams) is None


# --- routes --------------------------------------------------------------------

def _no_network(url, timeout):
    raise AssertionError("test touched the network")


@pytest.fixture()
def srv(tmp_path):
    client = market.ArshaClient(fetch=_no_network, cache_dir=tmp_path / "cache")
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          sse_interval=0.05, market_seed=[], market_client=client,
                          profile_cfg={})
    t = threading.Thread(target=s.serve_forever, daemon=True)
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


def test_route_shopping(srv, tmp_path):
    atomic_write_json(tmp_path / "cache" / "sublist_16080_0.json",
                      {"fetched_at": 0, "data": {"id": 16080, "name": "Cron Stone",
                                                 "lastSoldPrice": 3_000_000,
                                                 "currentStock": 0}})
    st, doc = _req(srv, "POST", "/api/deadeye", {"add_step": {
        "item": "Sovereign Bow", "current": "TRI", "target": "TET"}})
    assert st == 200
    sid = doc["plan"][-1]["id"]
    st, doc = _req(srv, "POST", "/api/deadeye", {"shop_step": {"id": sid, "fs": 100,
                                                               "crons": True}})
    assert st == 200 and doc["steps"][0]["fs"] == 100
    st, doc = _req(srv, "POST", "/api/deadeye", {"shop_set": {"silver_on_hand": 1}})
    assert st == 200 and doc["settings"]["silver_on_hand"] == 1
    st, doc = _req(srv, "GET", "/api/deadeye/shopping")
    assert st == 200
    ln = _lines(doc)[16080]
    assert ln["unit"] == 3_000_000 and ln["preorder"] == "no_stock" and ln["name"] == "Cron Stone"
    a = enhance.attempts(0.1001, 20)
    assert ln["expected"] == pytest.approx(780 * a["crons_attempts"])
    assert doc["total"] == ln["total"]
    assert doc["can_afford_by"] is None  # no grind sessions logged


def test_route_watch_flag_follows_watchlist(srv):
    _req(srv, "POST", "/api/deadeye", {"add_step": {
        "item": "Sovereign Bow", "current": "TRI", "target": "TET"}})
    st, doc = _req(srv, "GET", "/api/deadeye/shopping")
    assert doc["lines"] == []  # crons off, no seeded materials
    _req(srv, "POST", "/api/deadeye", {"shop_step": {"id": "d1", "crons": True}})
    _req(srv, "POST", "/api/market/watch", {"add": {"id": 16080, "sid": 0}})
    st, doc = _req(srv, "GET", "/api/deadeye/shopping")
    assert _lines(doc)[16080]["watched"] is True


@pytest.mark.parametrize("body", [
    {"shop_set": {"hours_per_day": 0}}, {"shop_step": {"id": "d1", "fs": 1}},
    {"shop_set": "x"},
])
def test_route_post_rejects(srv, body):
    st, doc = _req(srv, "POST", "/api/deadeye", body)
    assert st == 400 and doc["error"]


def test_route_never_fetches(srv):
    _req(srv, "POST", "/api/deadeye", {"add_step": {
        "item": "Sovereign Bow", "current": "TRI", "target": "TET"}})
    _req(srv, "POST", "/api/deadeye", {"shop_step": {"id": "d1", "crons": True}})
    st, doc = _req(srv, "GET", "/api/deadeye/shopping")
    assert st == 200 and doc["missing_prices"] == [16080]
    assert _lines(doc)[16080]["note"] == "missing price"
