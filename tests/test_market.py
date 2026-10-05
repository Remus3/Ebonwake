"""Plan 002 slice A: arsha client cache/backoff, alerts, watchlist, market routes.

No network: every ArshaClient gets an injected fake fetch.
"""

import http.client
import json
import threading

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
                  "freshness": it["freshness"]}
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
                          sse_interval=0.05, market_client=client, market_seed=[4901])
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
    assert st == 200 and doc["items"] == [SUB] and doc["freshness"]["stale"] is False


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
