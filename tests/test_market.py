"""Plan 002 slice A: arsha client cache/backoff, alerts, watchlist, market routes.

No network: every ArshaClient gets an injected fake fetch.
"""

import http.client
import json
import threading
from pathlib import Path

import pytest

from server.ew import app as ewapp
from server.ew import market
from server.ew.store import Store

SUB = {"name": "Black Stone", "id": 4901, "sid": 0, "basePrice": 200, "currentStock": 5000,
       "totalTrades": 99, "priceMin": 100, "priceMax": 300, "lastSoldPrice": 210,
       "lastSoldTime": 1700000000}


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class FakeFetch:
    """Maps an endpoint name to a body (bytes/obj) or an Exception to raise."""

    def __init__(self, routes=None):
        self.routes = dict(routes or {})
        self.calls = []

    def __call__(self, url, timeout):
        self.calls.append(url)
        ep = url.split("/v2/na/", 1)[1].split("?", 1)[0]
        val = self.routes[ep]
        if isinstance(val, Exception):
            raise val
        return val if isinstance(val, bytes) else json.dumps(val).encode()


def _client(tmp_path, routes, clock=None):
    f = FakeFetch(routes)
    c = market.ArshaClient(fetch=f, clock=clock or Clock(), cache_dir=tmp_path / "cache")
    return c, f


# --- cache / TTL -----------------------------------------------------------

def test_ttl_hit_then_miss(tmp_path):
    clk = Clock()
    c, f = _client(tmp_path, {"GetWorldMarketSubList": SUB}, clk)
    r = c.sublist(4901)
    assert r["data"]["name"] == "Black Stone" and r["stale"] is False and r["error"] is None
    assert r["ttl_s"] == 300 and r["age_s"] == 0 and r["fetched_at"].endswith("+00:00")
    clk.t += 299
    assert c.sublist(4901)["age_s"] == 299
    assert len(f.calls) == 1
    clk.t += 2
    c.sublist(4901)
    assert len(f.calls) == 2


def test_ttls_per_endpoint():
    assert market.TTL == {"sublist": 300, "orders": 120, "history": 3600, "hot": 600}


def test_cache_survives_new_client(tmp_path):
    clk = Clock()
    c, f = _client(tmp_path, {"GetWorldMarketSubList": SUB}, clk)
    c.sublist(4901)
    c2, f2 = _client(tmp_path, {"GetWorldMarketSubList": SUB}, clk)
    assert c2.sublist(4901)["data"]["id"] == 4901 and f2.calls == []
    assert not list((tmp_path / "cache").glob("*.tmp"))


def test_cache_key_includes_sid(tmp_path):
    c, f = _client(tmp_path, {"GetMarketPriceInfo": {"id": 1, "sid": 0, "history": {}}})
    c.history(1, 0)
    c.history(1, 5)
    assert len(f.calls) == 2


def test_list_vs_object_normalisation(tmp_path):
    lst = [dict(SUB, sid=0), dict(SUB, sid=1, lastSoldPrice=999)]
    c, _ = _client(tmp_path, {"GetWorldMarketSubList": lst})
    assert c.sublist(4901, 1)["data"]["lastSoldPrice"] == 999
    assert c.sublist(4901, 0)["data"]["lastSoldPrice"] == 210
    c2, _ = _client(tmp_path / "b", {"GetWorldMarketSubList": SUB})
    assert c2.sublist(4901, 0)["data"]["id"] == 4901


def test_hot_normalised_to_list(tmp_path):
    c, _ = _client(tmp_path, {"GetWorldMarketHotList": SUB})
    assert c.hot()["data"] == [SUB]
    c2, _ = _client(tmp_path / "b", {"GetWorldMarketHotList": [SUB, SUB]})
    assert len(c2.hot()["data"]) == 2


def test_urls_are_read_only_gets_on_na(tmp_path):
    c, f = _client(tmp_path, {"GetWorldMarketSubList": SUB,
                              "GetBiddingInfoList": {"id": 1, "sid": 0, "orders": []}})
    c.sublist(4901)
    c.orders(4901, 2)
    assert f.calls[0].startswith("https://api.arsha.io/v2/na/GetWorldMarketSubList?")
    assert "id=4901" in f.calls[0] and "lang=en" in f.calls[0]
    assert "id=4901" in f.calls[1] and "sid=2" in f.calls[1]


# --- backoff / stale -------------------------------------------------------

def test_stale_on_error_serves_cache(tmp_path):
    clk = Clock()
    c, f = _client(tmp_path, {"GetWorldMarketSubList": SUB}, clk)
    c.sublist(4901)
    clk.t += 400
    f.routes["GetWorldMarketSubList"] = {"status": 500, "code": 103, "message": "blocked"}
    r = c.sublist(4901)
    assert r["stale"] is True and r["data"]["id"] == 4901 and "103" in r["error"]
    assert r["age_s"] == 400


def test_error_without_cache_gives_null(tmp_path):
    c, _ = _client(tmp_path, {"GetWorldMarketSubList": OSError("timed out")})
    r = c.sublist(4901)
    assert r["data"] is None and r["stale"] is True and r["error"]
    assert r["fetched_at"] is None and r["age_s"] is None


@pytest.mark.parametrize("bad", [b"not json", OSError("boom"), {"code": 103, "message": "x"}])
def test_failure_kinds_all_back_off(tmp_path, bad):
    c, f = _client(tmp_path, {"GetWorldMarketSubList": bad})
    c.sublist(4901)
    c.sublist(4901)
    assert len(f.calls) == 1


def test_backoff_growth_and_cap(tmp_path):
    clk = Clock()
    c, f = _client(tmp_path, {"GetWorldMarketSubList": OSError("x")}, clk)
    waits = []
    for _ in range(9):
        n = len(f.calls)
        c.sublist(4901)
        assert len(f.calls) == n + 1
        st = c.backoff_state("sublist", 4901, 0)
        waits.append(st["until"] - clk.t)
        clk.t += waits[-1] - 1
        c.sublist(4901)
        assert len(f.calls) == n + 1  # still inside backoff
        clk.t += 1
    assert waits == [30, 60, 120, 240, 480, 960, 1800, 1800, 1800]


def test_backoff_persisted_across_restart(tmp_path):
    clk = Clock()
    c, f = _client(tmp_path, {"GetWorldMarketSubList": OSError("x")}, clk)
    c.sublist(4901)
    c2, f2 = _client(tmp_path, {"GetWorldMarketSubList": SUB}, clk)
    r = c2.sublist(4901)
    assert f2.calls == [] and r["data"] is None and r["error"]
    clk.t += 30
    assert c2.sublist(4901)["data"]["id"] == 4901


def test_success_resets_backoff(tmp_path):
    clk = Clock()
    c, f = _client(tmp_path, {"GetWorldMarketSubList": OSError("x")}, clk)
    c.sublist(4901)
    clk.t += 30
    f.routes["GetWorldMarketSubList"] = SUB
    c.sublist(4901)
    assert c.backoff_state("sublist", 4901, 0) == {}
    clk.t += 301
    f.routes["GetWorldMarketSubList"] = OSError("x")
    c.sublist(4901)
    assert c.backoff_state("sublist", 4901, 0)["until"] - clk.t == 30


# --- alerts ----------------------------------------------------------------

@pytest.mark.parametrize("price,below,above,want", [
    (100, 100, None, "below"), (101, 100, None, None), (99, 100, 200, "below"),
    (200, None, 200, "above"), (199, None, 200, None), (150, 100, 200, None),
    (None, 100, 200, None), (150, None, None, None), (0, 0, None, "below"),
])
def test_alert_edges(price, below, above, want):
    assert market.alert_for(price, below, above) == want


def test_price_of_falls_back_to_base():
    assert market.price_of(SUB) == 210
    assert market.price_of(dict(SUB, lastSoldPrice=0)) == 200
    assert market.price_of(dict(SUB, lastSoldPrice=None)) == 200
    assert market.price_of(None) is None


# --- watchlist -------------------------------------------------------------

def test_watchlist_seeded_once_from_config(tmp_path):
    st = Store(tmp_path)
    wl = market.Watchlist(st, seed=[4901, 721003])
    assert wl.items() == [{"id": 4901, "sid": 0, "below": None, "above": None},
                          {"id": 721003, "sid": 0, "below": None, "above": None}]
    wl.remove({"id": 4901, "sid": 0})
    wl.remove({"id": 721003, "sid": 0})
    assert market.Watchlist(st, seed=[4901]).items() == []


def test_watchlist_seed_ignores_non_ints(tmp_path):
    wl = market.Watchlist(Store(tmp_path), seed=[4901, "x", True, -1, 2.5])
    assert [w["id"] for w in wl.items()] == [4901]


def test_watchlist_add_update_remove(tmp_path):
    wl = market.Watchlist(Store(tmp_path), seed=None)
    wl.add({"id": 4901, "sid": 0, "below": 100})
    wl.add({"id": 4901, "sid": 1})
    wl.add({"id": 4901, "sid": 0, "below": 150, "above": 300})
    assert wl.items() == [{"id": 4901, "sid": 0, "below": 150, "above": 300},
                          {"id": 4901, "sid": 1, "below": None, "above": None}]
    wl.remove({"id": 4901, "sid": 0})
    assert wl.items() == [{"id": 4901, "sid": 1, "below": None, "above": None}]
    assert Store(tmp_path).get("market")["watch"] == wl.items()


@pytest.mark.parametrize("entry", [
    {}, {"id": "4901"}, {"id": 4901, "sid": "0"}, {"id": True}, {"id": -1},
    {"id": 4901, "below": 1.5}, {"id": 4901, "above": "9"}, {"id": 4901, "sid": -2},
    {"id": 10 ** 12}, "4901", None,
])
def test_watchlist_validation(tmp_path, entry):
    wl = market.Watchlist(Store(tmp_path), seed=None)
    with pytest.raises(ValueError):
        wl.add(entry)


# --- service ---------------------------------------------------------------

def _svc(tmp_path, routes, seed=(4901,), clock=None):
    c, f = _client(tmp_path, routes, clock)
    return market.MarketService(c, market.Watchlist(Store(tmp_path / "store"), seed=list(seed))), f


def test_service_watch_view(tmp_path):
    svc, _ = _svc(tmp_path, {"GetWorldMarketSubList": SUB})
    svc.watchlist.add({"id": 4901, "sid": 0, "below": 250})
    doc = svc.watch()
    it = doc["items"][0]
    assert it == {"id": 4901, "sid": 0, "name": "Black Stone", "price": 210, "stock": 5000,
                  "trades": 99, "below": 250, "above": None, "alert": "below",
                  "net": 136, "preorder": None, "freshness": it["freshness"],
                  "p20": False, "bands": it["bands"]}
    assert it["bands"]["basis"] == "samples" and it["bands"]["p50"] == 210
    assert it["freshness"]["stale"] is False and doc["updated"]
    assert set(it["freshness"]) == {"fetched_at", "age_s", "ttl_s", "stale", "error"}


def test_service_watch_no_data(tmp_path):
    svc, _ = _svc(tmp_path, {"GetWorldMarketSubList": OSError("x")})
    it = svc.watch()["items"][0]
    assert it["name"] is None and it["price"] is None and it["alert"] is None
    fr = it["freshness"]
    assert "data" not in fr and fr["error"] and fr["stale"] is True and fr["fetched_at"] is None


def test_service_item_history_sorted(tmp_path):
    hist = {"id": 4901, "sid": 0, "history": {"300": 3, "100": 1, "200": 2}}
    orders = {"id": 4901, "sid": 0, "orders": [{"price": 1, "sellers": 2, "buyers": 0}]}
    svc, _ = _svc(tmp_path, {"GetWorldMarketSubList": SUB, "GetMarketPriceInfo": hist,
                             "GetBiddingInfoList": orders})
    doc = svc.item(4901, 0)
    assert doc["history"] == [[100, 1], [200, 2], [300, 3]]
    assert doc["orders"] == orders["orders"] and doc["sub"]["id"] == 4901
    assert set(doc["freshness"]) == {"sub", "history", "orders"}


def test_service_source_status(tmp_path):
    clk = Clock()
    svc, f = _svc(tmp_path, {"GetWorldMarketSubList": SUB}, clock=clk)
    assert svc.source()["status"] == "none"
    svc.watch()
    s = svc.source()
    assert s["status"] == "ok" and s["ttl_s"] == 300 and s["updated"]
    clk.t += 400
    f.routes["GetWorldMarketSubList"] = OSError("x")
    svc.watch()
    assert svc.source()["status"] == "stale"


def test_service_source_error_without_cache(tmp_path):
    svc, _ = _svc(tmp_path, {"GetWorldMarketSubList": OSError("x")})
    svc.watch()
    assert svc.source()["status"] == "error" and svc.source()["updated"] is None


# --- routes ----------------------------------------------------------------

@pytest.fixture()
def msrv(tmp_path):
    hist = {"id": 4901, "sid": 0, "history": {"2": 20, "1": 10}}
    orders = {"id": 4901, "sid": 0, "orders": []}
    f = FakeFetch({"GetWorldMarketSubList": SUB, "GetMarketPriceInfo": hist,
                   "GetBiddingInfoList": orders, "GetWorldMarketHotList": [SUB]})
    client = market.ArshaClient(fetch=f, clock=Clock(), cache_dir=tmp_path / "cache")
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          sse_interval=0.05, market_client=client, market_seed=[4901],
                          profile_cfg={})
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield s
    s.shutdown()
    s.server_close()


def _req(s, method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=5)
    c.request(method, path, body=body, headers=headers or {})
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, (json.loads(data) if data else None)


def test_route_watch_get(msrv):
    st, doc = _req(msrv, "GET", "/api/market/watch")
    assert st == 200 and doc["items"][0]["name"] == "Black Stone"


def test_route_item(msrv):
    st, doc = _req(msrv, "GET", "/api/market/item?id=4901&sid=0")
    assert st == 200 and doc["history"] == [[1, 10], [2, 20]]


def test_route_item_bad_params(msrv):
    assert _req(msrv, "GET", "/api/market/item?id=abc")[0] == 400
    assert _req(msrv, "GET", "/api/market/item")[0] == 400
    assert _req(msrv, "GET", "/api/market/item?id=-3&sid=0")[0] == 400


def test_route_item_sid_defaults_zero(msrv):
    st, doc = _req(msrv, "GET", "/api/market/item?id=4901")
    assert st == 200 and doc["sub"]["sid"] == 0


def test_route_hot(msrv):
    st, doc = _req(msrv, "GET", "/api/market/hot")
    assert st == 200 and doc["items"] == [dict(SUB, preorder=None)]
    assert doc["freshness"]["stale"] is False


def test_state_reports_market_source(msrv):
    _req(msrv, "GET", "/api/market/watch")
    st, doc = _req(msrv, "GET", "/api/state")
    assert doc["sources"]["market"]["status"] == "ok"


# --- concurrency / corrupt state (verifier round 1) --------------------------

def test_watchlist_concurrent_read_write(tmp_path):
    wl = market.Watchlist(Store(tmp_path / "store"), seed=[])
    errors = []
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            try:
                wl.items()
            except Exception as e:  # noqa: BLE001
                errors.append(e)

    t = threading.Thread(target=reader)
    t.start()
    try:
        for i in range(300):
            wl.add({"id": 1 + i % 7, "sid": 0})
    except Exception as e:  # noqa: BLE001
        errors.append(e)
    finally:
        stop.set()
        t.join()
    assert errors == []
    assert len(wl.items()) == 7


def test_slow_fetch_does_not_block_other_keys(tmp_path):
    gate = threading.Event()

    def fetch(url, timeout):
        if "id=1&" in url or url.endswith("id=1"):
            gate.wait(5)
        return json.dumps(dict(SUB, id=2)).encode()

    c = market.ArshaClient(fetch=fetch, clock=Clock(), cache_dir=tmp_path / "cache")
    c.sublist(2)  # warm key 2
    t = threading.Thread(target=c.sublist, args=(1,))
    t.start()
    done = threading.Event()
    threading.Thread(target=lambda: (c.sublist(2), done.set())).start()
    assert done.wait(2), "fresh key 2 waited behind slow key 1"
    gate.set()
    t.join()


def test_corrupt_cache_and_backoff_degrade(tmp_path):
    c, f = _client(tmp_path, {"GetWorldMarketSubList": SUB})
    (tmp_path / "cache").mkdir(parents=True, exist_ok=True)
    key = c._key("sublist", 4901, 0)
    (tmp_path / "cache" / f"{key}.json").write_text('{"fetched_at": "x", "data": 1}')
    (tmp_path / "cache" / "backoff.json").write_text(json.dumps({key: "junk"}))
    res = c.sublist(4901)
    assert res["data"]["id"] == 4901 and res["error"] is None


# --- plan 027: net proceeds after tax + pre-order badge ----------------------

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "market"


def test_market_rules_file_shape():
    r = market.load_rules()
    assert r["tax"] == 0.35 and r["vp_bonus"] == 0.30
    assert "pearl" in r["pearl_tax_exempt"].lower()
    assert r["fame_steps"]["verified"] is False and r["fame_steps"]["steps"]
    assert r["source"].startswith("https://") and "wikiNo=47" in r["source"]
    assert r["verified"] == "2025-08-06"
    assert all(0 <= s["bonus_pct"] <= market.FAME_MAX for s in r["fame_steps"]["steps"])


@pytest.mark.parametrize("price,vp,fame,want", [
    (100_000_000, False, 0, 65_000_000),
    (100_000_000, True, 0, 84_500_000),
    (100_000_000, True, 1.5, 85_475_000),
    (100_000_000, False, 1.5, 65_975_000),
    (100_000_000, False, 0.5, 65_325_000),
    (210, False, 0, 136),        # 136.5 floors
    (1, True, 0, 0),             # 0.845 floors
    (0, True, 1.5, 0),
    (20_000_000_000, True, 1.5, 17_095_000_000),
    (999_999_999_999, True, 1.5, 854_749_999_999),  # no float drift at 1 T
    (100_000_000, False, 0.125, 65_084_500),  # 12.5 bp rounds half up, as JS Math.round
    (100_000_000, False, 0.005, 65_006_500),
    (100_000_000, False, 0.145, 65_091_000),  # 14.499.. double -> 14 bp on both sides
])
def test_net_proceeds(price, vp, fame, want):
    assert market.net_proceeds(price, vp, fame) == want


@pytest.mark.parametrize("price", [None, -1, 1.5, "100", True])
def test_net_proceeds_bad_price_is_none(price):
    assert market.net_proceeds(price, True, 0) is None


@pytest.mark.parametrize("fame", [-0.1, 1.6, "1", None, True])
def test_net_proceeds_bad_fame_rejected(fame):
    with pytest.raises(ValueError):
        market.net_proceeds(100, False, fame)


def test_preorder_states_from_fixture():
    hot = json.loads((FIXTURES / "hot_preorder.json").read_text(encoding="utf-8"))
    assert [market.preorder_state(x) for x in hot] == ["capped", "no_stock", None]


@pytest.mark.parametrize("item,want", [
    ({"currentStock": 5, "lastSoldPrice": 300, "priceMax": 300}, "capped"),
    ({"currentStock": 0, "lastSoldPrice": 300, "priceMax": 300}, "capped"),
    ({"currentStock": 0, "lastSoldPrice": 200, "priceMax": 300}, "no_stock"),
    ({"currentStock": 0}, "no_stock"),
    ({"currentStock": 5, "lastSoldPrice": 0, "priceMax": 0}, None),
    ({"currentStock": None, "lastSoldPrice": None, "priceMax": None}, None),
    ({"currentStock": False, "lastSoldPrice": 1, "priceMax": 2}, None),
    ({}, None), (None, None), ([], None),
])
def test_preorder_state_edges(item, want):
    assert market.preorder_state(item) == want


@pytest.mark.parametrize("doc,want", [
    (None, {"vp": False, "fame_pct": 0}),
    ({}, {"vp": False, "fame_pct": 0}),
    ({"market": {"vp": True, "fame_pct": 1.5}}, {"vp": True, "fame_pct": 1.5}),
    ({"market": {"vp": True, "fame_pct": 1}}, {"vp": True, "fame_pct": 1}),
    ({"market": {"vp": "yes", "fame_pct": 9}}, {"vp": False, "fame_pct": 0}),
    ({"market": {"fame_pct": -1}}, {"vp": False, "fame_pct": 0}),
    ({"market": {"fame_pct": True}}, {"vp": False, "fame_pct": 0}),
    ({"market": []}, {"vp": False, "fame_pct": 0}),
])
def test_settings_from_config(doc, want):
    assert market.settings_from(doc) == want


def test_config_market_reads_local_json(tmp_path):
    assert ewapp.config_market(tmp_path) == {"vp": False, "fame_pct": 0}
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "local.json").write_text(
        json.dumps({"market": {"vp": True, "fame_pct": 0.5}}), encoding="utf-8")
    assert ewapp.config_market(tmp_path) == {"vp": True, "fame_pct": 0.5}
    (tmp_path / "config" / "local.json").write_text("{bad", encoding="utf-8")
    assert ewapp.config_market(tmp_path) == {"vp": False, "fame_pct": 0}


def test_example_config_documents_market_settings():
    root = Path(__file__).resolve().parents[1]
    doc = json.loads((root / "config" / "local.example.json").read_text(encoding="utf-8"))
    assert doc["market"]["vp"] is False and doc["market"]["fame_pct"] == 0
    assert market.settings_from(doc) == {"vp": False, "fame_pct": 0}


def test_service_watch_net_with_settings(tmp_path):
    capped = dict(SUB, lastSoldPrice=100_000_000, priceMax=100_000_000, currentStock=0)
    c, _ = _client(tmp_path, {"GetWorldMarketSubList": capped})
    svc = market.MarketService(c, market.Watchlist(Store(tmp_path / "store"), seed=[4901]),
                               settings={"vp": True, "fame_pct": 0})
    doc = svc.watch()
    it = doc["items"][0]
    assert it["net"] == 84_500_000 and it["preorder"] == "capped"
    assert doc["tax"] == {"vp": True, "fame_pct": 0, "tax": 0.35, "vp_bonus": 0.30,
                          "fame_verified": False}


def test_service_watch_no_price_no_net(tmp_path):
    svc, _ = _svc(tmp_path, {"GetWorldMarketSubList": OSError("x")})
    it = svc.watch()["items"][0]
    assert it["net"] is None and it["preorder"] is None


def test_service_hot_adds_preorder_without_mutating_cache(tmp_path):
    hot = json.loads((FIXTURES / "hot_preorder.json").read_text(encoding="utf-8"))
    svc, _ = _svc(tmp_path, {"GetWorldMarketHotList": hot})
    doc = svc.hot()
    assert [x["preorder"] for x in doc["items"]] == ["capped", "no_stock", None]
    assert "preorder" not in svc.client.hot()["data"][0]


def test_service_hot_skips_junk_rows(tmp_path):
    svc, _ = _svc(tmp_path, {"GetWorldMarketHotList": [SUB, "x", None]})
    items = svc.hot()["items"]
    assert items[0]["preorder"] is None and items[1:] == ["x", None]


def test_route_watch_carries_net_and_preorder(msrv):
    st, doc = _req(msrv, "GET", "/api/market/watch")
    it = doc["items"][0]
    assert st == 200 and "net" in it and "preorder" in it and "tax" in doc


# --- plan 052: price bands, cached-history fallback, below-p20 alert ---------

DAY_MS = 86_400_000
NOW_S = 1_000_000_000.0  # band tests run the clock here (2001-09-09)
NOW_MS = int(NOW_S * 1000)


@pytest.mark.parametrize("prices,want", [
    ([5], (5, 5, 5)),
    ([1, 2], (1, 1, 2)),
    ([1, 2, 3, 4, 5], (1, 3, 4)),
    (list(range(1, 11)), (2, 5, 8)),
    (list(range(10, 0, -1)), (2, 5, 8)),  # order does not matter
    ([7, 7, 7, 7], (7, 7, 7)),
])
def test_price_bands_nearest_rank(prices, want):
    series = [[NOW_MS - i * DAY_MS, p] for i, p in enumerate(prices)]
    b = market.price_bands(series, now_ms=NOW_MS)
    assert (b["p20"], b["p50"], b["p80"]) == want and b["n"] == len(prices)
    assert b["from"] == min(t for t, _ in series) and b["to"] == max(t for t, _ in series)


def test_price_bands_empty_and_junk():
    assert market.price_bands([], now_ms=NOW_MS) is None
    assert market.price_bands(None, now_ms=NOW_MS) is None
    junk = [[NOW_MS, 0], [NOW_MS, -1], [NOW_MS, "9"], [NOW_MS, True], ["x", 5], [NOW_MS],
            None, [NOW_MS, 1.5]]
    assert market.price_bands(junk, now_ms=NOW_MS) is None


def test_price_bands_window_days():
    series = [[NOW_MS - 100 * DAY_MS, 1], [NOW_MS - 10 * DAY_MS, 50], [NOW_MS, 60]]
    b = market.price_bands(series, now_ms=NOW_MS)
    assert b["n"] == 2 and b["p20"] == 50 and b["from"] == NOW_MS - 10 * DAY_MS
    assert market.price_bands(series, days=200, now_ms=NOW_MS)["n"] == 3
    assert market.price_bands(series, days=5, now_ms=NOW_MS)["n"] == 1


def test_price_bands_seconds_timestamps_normalised():
    b = market.price_bands([[int(NOW_S) - 86400, 10], [int(NOW_S), 20]], now_ms=NOW_MS)
    assert b["n"] == 2 and b["to"] == NOW_MS


@pytest.mark.parametrize("price,p20,on,want", [
    (99, 100, True, "below_p20"), (100, 100, True, None), (101, 100, True, None),
    (99, 100, False, None), (None, 100, True, None), (99, None, True, None),
])
def test_alert_below_p20_edge(price, p20, on, want):
    bands = None if p20 is None else {"p20": p20}
    assert market.alert_for(price, None, None, bands=bands, p20=on) == want


def test_alert_threshold_wins_over_p20():
    assert market.alert_for(50, 60, None, bands={"p20": 100}, p20=True) == "below"
    assert market.alert_for(50, None, 40, bands={"p20": 100}, p20=True) == "above"


def test_samples_one_per_hour_pruned_90d(tmp_path):
    clk = Clock(NOW_S)
    s = market.PriceSamples(tmp_path / "samples.json", clock=clk)
    s.record(4901, 0, NOW_S - 91 * 86400, 5)  # older than the window: dropped
    s.record(4901, 0, NOW_S - 10800, 100)
    s.record(4901, 0, NOW_S - 10800, 100)     # same refresh: no duplicate
    s.record(4901, 0, NOW_S - 10000, 999)     # under an hour after the last: skipped
    s.record(4901, 0, NOW_S - 7200, 110)      # exactly an hour: kept
    s.record(4901, 0, NOW_S, 120)
    s.record(4901, 1, NOW_S, 7)
    s.record(4901, 0, NOW_S + 7200, None)     # no price: ignored
    want = [[int(NOW_S - 10800) * 1000, 100], [int(NOW_S - 7200) * 1000, 110], [NOW_MS, 120]]
    assert s.series(4901, 0) == want
    assert market.PriceSamples(tmp_path / "samples.json", clock=clk).series(4901, 0) == want
    assert s.series(4901, 1) == [[NOW_MS, 7]] and s.series(1, 0) == []
    assert not list(tmp_path.glob("*.tmp"))


def test_samples_pruned_when_clock_moves(tmp_path):
    clk = Clock(NOW_S)
    s = market.PriceSamples(tmp_path / "samples.json", clock=clk)
    s.record(1, 0, NOW_S, 5)
    clk.t += 91 * 86400
    s.record(1, 0, clk.t, 6)
    assert s.series(1, 0) == [[int(clk.t) * 1000, 6]]


def test_samples_corrupt_file_degrades(tmp_path):
    p = tmp_path / "samples.json"
    p.write_text("{bad", encoding="utf-8")
    s = market.PriceSamples(p, clock=Clock(NOW_S))
    assert s.series(4901, 0) == []
    p.write_text(json.dumps({"4901_0": "junk", "1_0": [[NOW_MS, 2.5], "x", [NOW_MS, 3]]}),
                 encoding="utf-8")
    s = market.PriceSamples(p, clock=Clock(NOW_S))
    assert s.series(4901, 0) == [] and s.series(1, 0) == [[NOW_MS, 3]]
    s.record(4901, 0, NOW_S, 9)
    assert s.series(4901, 0) == [[NOW_MS, 9]]


def _bands_svc(tmp_path, routes, clk):
    c, f = _client(tmp_path, routes, clk)
    svc = market.MarketService(c, market.Watchlist(Store(tmp_path / "store"), seed=[4901]),
                               samples=market.PriceSamples(tmp_path / "samples.json", clock=clk))
    return svc, f


def test_watch_bands_from_cached_history_without_new_calls(tmp_path):
    clk = Clock(NOW_S)
    hist = {"id": 4901, "sid": 0,
            "history": {str(NOW_MS - i * DAY_MS): 100 + i for i in range(10)}}
    svc, f = _bands_svc(tmp_path, {"GetWorldMarketSubList": SUB, "GetMarketPriceInfo": hist,
                                   "GetBiddingInfoList": {"orders": []}}, clk)
    svc.item(4901, 0)  # operator opened the detail: history now cached
    n = len(f.calls)
    clk.t += 5000      # history cache past its TTL: still used, never refetched by watch
    it = svc.watch()["items"][0]
    assert [u for u in f.calls[n:] if "GetMarketPriceInfo" in u] == []
    b = it["bands"]
    assert (b["p20"], b["p50"], b["p80"], b["n"]) == (101, 104, 107, 10)
    assert b["basis"] == "history" and b["age_s"] == 5000
    assert it["p20"] is False and it["alert"] is None


def test_watch_bands_fall_back_to_samples_when_history_blocked(tmp_path):
    clk = Clock(NOW_S)
    blocked = FIXTURES / "history_blocked.json"
    assert json.loads(blocked.read_text(encoding="utf-8"))["code"] == 103
    svc, f = _bands_svc(tmp_path, {"GetWorldMarketSubList": SUB,
                                   "GetMarketPriceInfo": blocked.read_bytes(),
                                   "GetBiddingInfoList": {"orders": []}}, clk)
    assert svc.item(4901, 0)["freshness"]["history"]["error"]
    svc.watchlist.add({"id": 4901, "sid": 0, "p20": True})
    for p in [300, 250, 260, 270, 280]:
        clk.t += 3600  # past the sublist TTL: one refresh, one sample
        f.routes["GetWorldMarketSubList"] = dict(SUB, lastSoldPrice=p)
        it = svc.watch()["items"][0]
    b = it["bands"]
    assert b["basis"] == "samples" and b["n"] == 5 and b["p20"] == 250 and b["age_s"] == 0
    assert it["p20"] is True and it["alert"] is None  # 280 is not below 250
    clk.t += 3600
    f.routes["GetWorldMarketSubList"] = dict(SUB, lastSoldPrice=200)
    it = svc.watch()["items"][0]
    assert it["alert"] == "below_p20" and it["bands"]["n"] == 6


def test_watch_bands_none_without_any_data(tmp_path):
    svc, _ = _bands_svc(tmp_path, {"GetWorldMarketSubList": OSError("x")}, Clock(NOW_S))
    it = svc.watch()["items"][0]
    assert it["bands"] is None and it["alert"] is None


def test_watch_records_one_sample_per_refresh(tmp_path):
    clk = Clock(NOW_S)
    svc, _ = _bands_svc(tmp_path, {"GetWorldMarketSubList": SUB}, clk)
    svc.watch()
    clk.t += 10
    svc.watch()
    assert svc.samples.series(4901, 0) == [[NOW_MS, 210]]


def test_item_carries_bands(tmp_path):
    clk = Clock(NOW_S)
    hist = {"id": 4901, "sid": 0, "history": {str(NOW_MS): 10, str(NOW_MS - DAY_MS): 20}}
    svc, _ = _bands_svc(tmp_path, {"GetWorldMarketSubList": SUB, "GetMarketPriceInfo": hist,
                                   "GetBiddingInfoList": {"orders": []}}, clk)
    b = svc.item(4901, 0)["bands"]
    assert b["basis"] == "history" and (b["p20"], b["p80"]) == (10, 20)


def test_watchlist_p20_opt_in(tmp_path):
    wl = market.Watchlist(Store(tmp_path), seed=None)
    wl.add({"id": 4901, "sid": 0, "p20": True})
    assert wl.items() == [{"id": 4901, "sid": 0, "below": None, "above": None, "p20": True}]
    wl.add({"id": 4901, "sid": 0, "p20": False})
    assert wl.items() == [{"id": 4901, "sid": 0, "below": None, "above": None}]
    with pytest.raises(ValueError):
        wl.add({"id": 4901, "p20": "yes"})


def test_route_watch_rows_carry_bands(msrv):
    st, doc = _req(msrv, "GET", "/api/market/watch")
    it = doc["items"][0]
    assert st == 200 and "bands" in it and it["p20"] is False
