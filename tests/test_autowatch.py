"""Plan 071: self-curating market watch.

Auto-watch set = union of the plan 037 shopping list, the top 5 loot items of
the current / last plan 039 spot and plan 054 recipe inputs, capped at 15 and
ranked by silver at stake; band thresholds (plan 052 p20 / p80) for auto
entries; one batched GetWorldMarketSearchList GET per refresh. No network:
every client gets a stub fetch.
"""

import json

import pytest

from server.ew import autowatch, grind, market
from server.ew.store import Store


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class StubFetch:
    """Endpoint name -> body or Exception; records every URL."""

    def __init__(self, routes):
        self.routes = dict(routes)
        self.calls = []

    def __call__(self, url, timeout):
        self.calls.append(url)
        ep = url.split("/v2/na/", 1)[1].split("?", 1)[0]
        val = self.routes.get(ep, OSError(f"no route {ep}"))
        if isinstance(val, Exception):
            raise val
        return json.dumps(val).encode()


# -- fixtures: plans 037 / 039 / 054 ---------------------------------------------

def _shopping():
    # Plan 037 view lines: total = qty x unit, null when unpriced.
    return {"lines": [
        {"id": 16001, "qty": 400, "unit": 200_000, "total": 80_000_000},
        {"id": 16002, "qty": 300, "unit": 150_000, "total": 45_000_000},
        {"id": 44195, "qty": 5000, "unit": 2_000, "total": 10_000_000},
        {"id": 721003, "qty": 900, "unit": 3_000_000, "total": 2_700_000_000},
        {"id": 4987, "qty": 2, "unit": None, "total": None},
        {"id": 752023, "qty": 1, "unit": 9_000_000_000, "total": 9_000_000_000},
    ]}


LOOT_PRICES = {9001: 1_000, 9002: 2_000, 9003: 3_000, 9004: 4_000, 9005: 5_000,
               9006: 6_000, 9007: 7_000, 16001: 200_000}


def _loot():
    # GrindService.loot_candidates(): marketable ids of the current / last spot.
    return {"spot": "polly-forest", "items": [
        {"id": i, "name": f"L{i}", "count": 100} for i in sorted(LOOT_PRICES)]}


def _recipes():
    # Plan 054 view: inputs carry `unit` (vendor price or cached market price).
    def inp(iid, qty, unit, vendor=None):
        return {"id": iid, "name": None, "qty": qty, "vendor_price": vendor, "unit": unit}
    return {"recipes": [
        {"id": "beer", "inputs": [inp(7001, 5, 1_000), inp(7002, 1, 500, vendor=500),
                                  inp(None, 2, 300)]},
        {"id": "elixir", "inputs": [inp(7003, 2, 40_000), inp(7004, 3, 10_000),
                                    inp(7005, 1, None), inp(7006, 10, 2_500),
                                    inp(7007, 1, 90_000), inp(44195, 1, 2_000)]},
    ]}


def _price(iid):
    return LOOT_PRICES.get(iid)


def _aw(wl, **kw):
    src = dict(shopping=_shopping, loot=_loot, recipes=_recipes, price=_price)
    src.update(kw)
    return autowatch.AutoWatch(wl, every_s=0, **src)


def _wl(tmp_path, seed=()):
    return market.Watchlist(Store(tmp_path / "store"), seed=list(seed))


# -- union + cap + rank ----------------------------------------------------------

def test_stakes_per_source():
    assert autowatch.shopping_stakes(_shopping())[:2] == [(16001, 80_000_000),
                                                          (16002, 45_000_000)]
    assert (4987, 0) in autowatch.shopping_stakes(_shopping())  # unpriced: stake 0
    loot = autowatch.loot_stakes(_loot(), _price)
    assert [i for i, _ in loot] == [16001, 9007, 9006, 9005, 9004]  # top 5 by value
    assert loot[0] == (16001, 20_000_000)                          # price x count
    rec = dict(autowatch.recipe_stakes(_recipes()))
    assert 7002 not in rec and None not in rec                     # vendor / no id
    assert rec[7001] == 5 * 1_000 * autowatch.RECIPE_CRAFTS and rec[7005] == 0


def test_rank_merges_sources_and_sums_stake():
    r = autowatch.rank({"shopping": [(1, 10), (2, 5)], "loot": [(2, 7), (3, 1)],
                        "recipes": [(4, 0)]})
    assert [m["id"] for m in r] == [2, 1, 3, 4]
    assert r[0] == {"id": 2, "stake": 12, "sources": ["shopping", "loot"]}


def test_union_capped_at_15_ranked_by_stake(tmp_path):
    wl = _wl(tmp_path)
    aw = _aw(wl)
    cands = aw.candidates()
    assert len(cands) > autowatch.CAP                   # fixtures overflow the cap
    watch = aw.sync()
    assert len(watch) == autowatch.CAP == 15
    assert all(w["auto"] is True and w["sid"] == 0 for w in watch)
    assert [w["id"] for w in watch] == [m["id"] for m in cands][:15]
    assert watch[0]["id"] == 752023 and watch[1]["id"] == 721003
    # Loot outside the top 5 never joins, even if the cap had room.
    assert not {9001, 9002, 9003} & {w["id"] for w in watch}


def test_manual_entries_always_stay(tmp_path):
    wl = _wl(tmp_path, seed=[1234])
    wl.add({"id": 16001, "sid": 0, "below": 150_000})     # manual, also a candidate
    wl.add({"id": 16001, "sid": 3})                       # enhanced copy, manual
    watch = _aw(wl).sync()
    manual = [w for w in watch if not w.get("auto")]
    assert [(w["id"], w["sid"]) for w in manual] == [(1234, 0), (16001, 0), (16001, 3)]
    assert manual[1]["below"] == 150_000
    auto_ids = [w["id"] for w in watch if w.get("auto")]
    assert 16001 not in auto_ids and len(auto_ids) == 14  # the manual one fills a slot
    assert len(watch) == 17


def test_stale_auto_entries_dropped_manual_kept(tmp_path):
    wl = _wl(tmp_path, seed=[1234])
    _aw(wl).sync()
    watch = _aw(wl, shopping=lambda: {"lines": []}, loot=lambda: {"items": []},
                recipes=lambda: {"recipes": []}).sync()
    assert watch == [{"id": 1234, "sid": 0, "below": None, "above": None}]


def test_source_failure_skips_curation(tmp_path):
    wl = _wl(tmp_path)
    before = _aw(wl).sync()

    def boom():
        raise RuntimeError("shopping broke")
    assert _aw(wl, shopping=boom).sync() == before      # never wipes on a hiccup


def test_sync_throttled(tmp_path):
    wl = _wl(tmp_path)
    clk = Clock()
    calls = []

    def shop():
        calls.append(1)
        return _shopping()
    aw = autowatch.AutoWatch(wl, shopping=shop, loot=_loot, recipes=_recipes, price=_price,
                             clock=clk, every_s=60)
    aw.sync()
    aw.sync()
    assert len(calls) == 1
    clk.t += 60
    aw.sync()
    assert len(calls) == 2


# -- removed entry not re-added ----------------------------------------------------

def test_removed_auto_entry_not_readded(tmp_path):
    wl = _wl(tmp_path)
    aw = _aw(wl)
    aw.sync()
    wl.remove({"id": 752023, "sid": 0})
    assert wl.store.get("market")["auto_removed"] == [752023]
    watch = aw.sync()
    ids = [w["id"] for w in watch]
    assert 752023 not in ids and len(ids) == 15         # the next candidate moves up
    # Restarting the service keeps the memory (it is in the store).
    again = _aw(market.Watchlist(Store(tmp_path / "store"))).sync()
    assert 752023 not in [w["id"] for w in again]


def test_removing_manual_entry_is_not_remembered(tmp_path):
    wl = _wl(tmp_path, seed=[1234])
    wl.remove({"id": 1234, "sid": 0})
    assert wl.store.get("market").get("auto_removed", []) == []


def test_operator_edit_turns_auto_entry_manual(tmp_path):
    wl = _wl(tmp_path)
    _aw(wl).sync()
    wl.add({"id": 752023, "sid": 0, "above": 2_000_000_000})
    row = next(w for w in wl.items() if w["id"] == 752023)
    assert "auto" not in row and row["above"] == 2_000_000_000
    # A later curate keeps it manual and does not duplicate it.
    watch = _aw(wl).sync()
    assert [w["id"] for w in watch].count(752023) == 1


# -- band thresholds / manual wins -------------------------------------------------

BANDS = {"p20": 100, "p50": 150, "p80": 200, "n": 30, "basis": "history", "age_s": 0}


def test_band_thresholds_for_auto_entry():
    e = {"id": 1, "sid": 0, "below": None, "above": None, "auto": True}
    assert autowatch.band_thresholds(e, BANDS) == (100, 200, "auto band")


def test_manual_threshold_wins():
    e = {"id": 1, "sid": 0, "below": 90, "above": None, "auto": True}
    assert autowatch.band_thresholds(e, BANDS) == (90, 200, "auto band")
    e = dict(e, above=500)
    assert autowatch.band_thresholds(e, BANDS) == (90, 500, None)
    manual = {"id": 1, "sid": 0, "below": None, "above": None}
    assert autowatch.band_thresholds(manual, BANDS) == (None, None, None)


@pytest.mark.parametrize("bands", [None, {}, dict(BANDS, n=autowatch.MIN_BAND_N - 1),
                                   dict(BANDS, p20=None, p80=None)])
def test_no_band_without_enough_points(bands):
    e = {"id": 1, "sid": 0, "below": None, "above": None, "auto": True}
    assert autowatch.band_thresholds(e, bands) == (None, None, None)


# -- one batched GET per refresh ---------------------------------------------------

def _rows(ids, price=1_000):
    return [{"name": f"I{i}", "id": i, "currentStock": 7, "totalTrades": 3,
             "basePrice": price} for i in ids]


def _service(tmp_path, routes, clock=None):
    f = StubFetch(routes)
    c = market.ArshaClient(fetch=f, clock=clock or Clock(), cache_dir=tmp_path / "cache")
    wl = _wl(tmp_path)
    svc = market.MarketService(c, wl)
    aw = _aw(wl)
    svc.curate = aw.sync
    return svc, f


def test_fixture_plans_give_15_band_watch_with_one_batched_get(tmp_path):
    ids = [m["id"] for m in _aw(_wl(tmp_path / "x")).candidates()][:15]
    svc, f = _service(tmp_path, {"GetWorldMarketSearchList": _rows(ids, 150)})
    # A 90-day band per item from the (stale-ok) history cache: p20 100, p80 200.
    hist = {str(1_000_000 - k * 3600): v for k, v in
            enumerate([100, 100, 120, 150, 150, 160, 180, 200, 200, 200])}
    for i in ids:
        (tmp_path / "cache").mkdir(exist_ok=True)
        (tmp_path / "cache" / f"history_{i}_0.json").write_text(json.dumps(
            {"fetched_at": 999_000.0, "data": {"id": i, "sid": 0, "history": hist}}))
    doc = svc.watch()
    assert len(f.calls) == 1 and "GetWorldMarketSearchList" in f.calls[0]
    assert "ids=" in f.calls[0] and "lang=en" in f.calls[0]
    items = doc["items"]
    assert len(items) == 15 and [it["id"] for it in items] == ids
    for it in items:
        assert it["auto"] is True and it["threshold"] == "auto band"
        assert it["auto_band"] == {"below": 100, "above": 200}
        assert it["price"] == 150 and it["alert"] is None and it["name"] == f"I{it['id']}"
    # Second refresh inside the TTL: no new GET at all.
    svc.watch()
    assert len(f.calls) == 1


def test_band_alert_fires_on_auto_entry(tmp_path):
    svc, f = _service(tmp_path, {"GetWorldMarketSearchList": _rows([752023], 90)},
                      )
    svc.curate = _aw(svc.watchlist, shopping=lambda: {"lines": [
        {"id": 752023, "total": 5}]}, loot=lambda: {"items": []},
        recipes=lambda: {"recipes": []}).sync
    svc.bands = lambda iid, sid=0: dict(BANDS)
    it = svc.watch()["items"][0]
    assert it["alert"] == "below" and it["below"] is None   # stored value untouched


def test_manual_rows_keep_their_shape(tmp_path):
    svc, f = _service(tmp_path, {"GetWorldMarketSearchList": _rows([1234], 500)})
    svc.curate = None
    svc.watchlist.add({"id": 1234, "sid": 0, "below": 600})
    it = svc.watch()["items"][0]
    assert "auto" not in it and "auto_band" not in it and "threshold" not in it
    assert it["alert"] == "below" and it["price"] == 500
    assert len(f.calls) == 1


def test_blocked_batch_falls_back_to_plan_002_cache_and_backoff(tmp_path):
    clk = Clock()
    blocked = {"status": 500, "code": 103, "message": "Request blocked (Imperva)"}
    sub = {"name": "Black Stone", "id": 16001, "sid": 0, "basePrice": 200,
           "lastSoldPrice": 210, "currentStock": 1, "totalTrades": 1}
    svc, f = _service(tmp_path, {"GetWorldMarketSearchList": blocked,
                                 "GetWorldMarketSubList": sub}, clock=clk)
    svc.curate = None
    svc.watchlist.add({"id": 16001, "sid": 0})
    it = svc.watch()["items"][0]
    assert it["price"] == 210                      # plan 002 per-item cache path
    eps = [u.split("/v2/na/")[1].split("?")[0] for u in f.calls]
    assert eps == ["GetWorldMarketSearchList", "GetWorldMarketSubList"]
    # Inside both backoff / TTL windows: no request at all.
    clk.t += 10
    svc.watch()
    assert len(f.calls) == 2
    bo = svc.client.key_backoff(market.SEARCH_KEY)
    assert bo["n"] == 1 and "103" in bo["error"]


def test_stale_batch_rows_served_when_nothing_else(tmp_path):
    clk = Clock()
    svc, f = _service(tmp_path, {"GetWorldMarketSearchList": _rows([16001], 333)}, clock=clk)
    svc.curate = None
    svc.watchlist.add({"id": 16001, "sid": 0})
    svc.watch()
    clk.t += 400
    f.routes["GetWorldMarketSearchList"] = OSError("down")
    it = svc.watch()["items"][0]
    assert it["price"] == 333 and it["freshness"]["stale"] is True


def test_batch_cache_feeds_cached_price(tmp_path):
    svc, _ = _service(tmp_path, {"GetWorldMarketSearchList": _rows([16001], 444)})
    svc.curate = None
    svc.watchlist.add({"id": 16001, "sid": 0})
    assert svc.cached_price(16001) is None
    svc.watch()
    assert svc.cached_price(16001) == 444 and svc.cached_quote(16001)["price"] == 444
    assert svc.cached_price(16001, sid=2) is None


SUB = {"name": "Black Stone", "id": 16001, "sid": 0, "basePrice": 200,
       "lastSoldPrice": 210, "currentStock": 1, "totalTrades": 1, "priceMax": 300}


def test_newer_batch_row_beats_old_sublist_cache(tmp_path):
    clk = Clock()
    svc, f = _service(tmp_path, {"GetWorldMarketSubList": SUB}, clock=clk)
    svc.curate = None
    svc.client.sublist(16001)                      # old sublist cache: 210
    clk.t += 3600
    f.routes["GetWorldMarketSearchList"] = _rows([16001], 555)
    svc.watchlist.add({"id": 16001, "sid": 0})
    svc.watch()
    assert svc.cached_price(16001) == 555          # the newer batch row wins
    clk.t += 1
    svc.client.cached_get(svc.client._key("sublist", 16001, 0), 0, lambda: dict(SUB))
    assert svc.cached_price(16001) == 210          # now the sublist is newer


def test_fresh_sublist_cache_kept_over_batch_row(tmp_path):
    svc, f = _service(tmp_path, {"GetWorldMarketSubList": SUB,
                                 "GetWorldMarketSearchList": _rows([16001], 555)})
    svc.curate = None
    svc.client.sublist(16001)
    svc.watchlist.add({"id": 16001, "sid": 0})
    it = svc.watch()["items"][0]
    assert it["price"] == 210 and it["stock"] == 1  # lastSoldPrice, no extra GET
    assert len([u for u in f.calls if "GetWorldMarketSubList" in u]) == 1


def test_edited_then_removed_auto_entry_not_readded(tmp_path):
    wl = _wl(tmp_path)
    aw = _aw(wl)
    aw.sync()
    wl.add({"id": 752023, "sid": 0, "below": 5})
    wl.remove({"id": 752023, "sid": 0})
    assert 752023 in wl.removed()
    assert 752023 not in [w["id"] for w in _aw(wl).sync()]


def test_changed_id_set_refetches_once(tmp_path):
    svc, f = _service(tmp_path, {"GetWorldMarketSearchList": _rows([1, 2])})
    svc.curate = None
    svc.watchlist.add({"id": 1, "sid": 0})
    svc.watch()
    svc.watchlist.add({"id": 2, "sid": 0})
    svc.watch()
    svc.watch()
    assert len(f.calls) == 2 and "ids=1%2C2" in f.calls[1]


def test_search_body_shapes(tmp_path):
    f = StubFetch({"GetWorldMarketSearchList": {"name": "X", "id": 5, "basePrice": 9}})
    c = market.ArshaClient(fetch=f, clock=Clock(), cache_dir=tmp_path / "cache")
    r = c.search([5])
    assert r["data"]["rows"] == [{"name": "X", "id": 5, "basePrice": 9}]
    assert r["ttl_s"] == market.TTL["search"]
    f.routes["GetWorldMarketSearchList"] = "nope"
    c2 = market.ArshaClient(fetch=f, clock=Clock(), cache_dir=tmp_path / "c2")
    assert c2.search([5])["data"] is None


# -- grind: current / last spot loot candidates --------------------------------------

class GClock:
    def __init__(self):
        self.t = 1_760_000_000.0

    def __call__(self):
        return self.t


def test_grind_loot_candidates_current_then_last_spot(tmp_path):
    tables = {"polly-forest": {"items": [
        {"id": 16001, "name": "Black Stone (Weapon)", "marketable": True},
        {"name": "Trash", "marketable": False, "vendor_price": 10},
        {"id": 16002, "name": "Black Stone (Armor)", "marketable": True}],
        "source": "https://x", "verified": False}}
    clk = GClock()
    s = grind.GrindService(Store(tmp_path / "store"), clock=clk, loot_tables=tables,
                           prices={}.get, tax=lambda: {"vp": False, "fame_pct": 0})
    assert s.loot_candidates() == {"spot": None, "items": []}
    s.add_spot("Polly's Forest")
    s.add_spot("Gyfin")
    s.start("pollys-forest")
    got = s.loot_candidates()
    assert got["spot"] == "pollys-forest"
    assert got["items"] == [{"id": 16001, "name": "Black Stone (Weapon)", "count": 1},
                            {"id": 16002, "name": "Black Stone (Armor)", "count": 1}]
    clk.t += 3600
    s.stop({"silver": 0, "trash": 0, "loot": [{"id": 16001, "count": 40}]})
    got = s.loot_candidates()                     # no active: the last session's spot
    assert got["spot"] == "pollys-forest" and got["items"][0]["count"] == 40
    s.log({"spot": "gyfin", "minutes": 10, "silver": 1, "trash": 0})
    assert s.loot_candidates() == {"spot": "gyfin", "items": []}
