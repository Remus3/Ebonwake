"""Plan 054: cooking / alchemy margin calculator. Operator-typed recipes priced
from the cached arsha sublist (never a fetch) or an operator vendor price; the
sale side is taxed with plan 027's net_proceeds. Nothing reads the game."""

import http.client
import json
import threading

import pytest

from server.ew import app as ewapp
from server.ew import crafting, market
from server.ew.store import Store

NO_VP = {"vp": False, "fame_pct": 0}
VP = {"vp": True, "fame_pct": 0}


def recipe(**kw):
    r = {"name": "Beer", "inputs": [{"id": 1, "qty": 5}, {"name": "Leavening Agent", "qty": 2,
                                                            "vendor_price": 20}],
         "outputs": [{"id": 9, "qty_avg": 2.5}]}
    r.update(kw)
    return r


# --- margin --------------------------------------------------------------------

def test_margin_no_vp_collects_65_percent():
    m = crafting.margin(crafting.validate_recipe(recipe()), {1: 100, 9: 1000}, False, 0)
    assert m["cost"] == 5 * 100 + 2 * 20
    assert m["gross"] == 2500
    assert m["net"] == 1625  # 650 per unit x 2.5
    assert m["profit"] == 1625 - 540
    assert m["profit_1000"] == (1625 - 540) * 1000
    assert m["complete"] is True and m["missing"] == []


def test_margin_vp_collects_845_percent_and_fame():
    r = crafting.validate_recipe(recipe(outputs=[{"id": 9, "qty_avg": 1}]))
    assert crafting.margin(r, {1: 100, 9: 1000000}, True, 0)["net"] == 845000
    assert crafting.margin(r, {1: 100, 9: 1000000}, True, 1.5)["net"] == \
        market.net_proceeds(1000000, True, 1.5)


def test_margin_fractional_yield_is_exact_per_1000():
    r = crafting.validate_recipe(recipe(inputs=[{"id": 1, "qty": 1}],
                                        outputs=[{"id": 9, "qty_avg": 1.3}]))
    m = crafting.margin(r, {1: 0, 9: 100}, False, 0)
    assert m["net"] == 84  # floor(65 * 1.3 = 84.5)
    assert m["profit_1000"] == 84500
    assert m["profit"] == 84


def test_margin_loss_is_negative_and_floored_down():
    r = crafting.validate_recipe(recipe(inputs=[{"id": 1, "qty": 1}],
                                        outputs=[{"id": 9, "qty_avg": 0.5}]))
    m = crafting.margin(r, {1: 100, 9: 1}, False, 0)
    assert m["net"] == 0
    assert m["profit"] == -100
    assert m["profit_1000"] == -100000


def test_vendor_price_wins_over_market():
    r = crafting.validate_recipe(recipe(inputs=[{"id": 1, "qty": 3, "vendor_price": 10}]))
    assert crafting.margin(r, {1: 9999, 9: 0}, False, 0)["cost"] == 30


def test_procs_add_to_the_sale_side():
    r = crafting.validate_recipe(recipe(inputs=[{"id": 1, "qty": 1}],
                                        procs=[{"id": 7, "qty_avg": 0.1}]))
    m = crafting.margin(r, {1: 0, 9: 1000, 7: 10000}, False, 0)
    assert m["gross"] == 2500 + 1000
    assert m["net"] == 1625 + 650


def test_missing_price_is_flagged_not_guessed():
    m = crafting.margin(crafting.validate_recipe(recipe()), {9: 1000}, False, 0)
    assert m["complete"] is False
    assert m["profit"] is None and m["profit_1000"] is None
    assert m["missing"] == [{"side": "input", "id": 1, "name": None}]
    assert m["cost"] == 40  # the priced lines still sum
    m = crafting.margin(crafting.validate_recipe(recipe()), {1: 100}, False, 0)
    assert m["missing"] == [{"side": "output", "id": 9, "name": None}]


def test_named_input_without_vendor_price_is_missing():
    r = crafting.validate_recipe(recipe(inputs=[{"name": "Flour", "qty": 1}]))
    m = crafting.margin(r, {9: 10}, False, 0)
    assert m["missing"] == [{"side": "input", "id": None, "name": "Flour"}]


def test_margin_pct():
    r = crafting.validate_recipe(recipe(inputs=[{"id": 1, "qty": 1}],
                                        outputs=[{"id": 9, "qty_avg": 1}]))
    assert crafting.margin(r, {1: 500, 9: 1000}, False, 0)["margin_pct"] == 30.0
    assert crafting.margin(r, {1: 0, 9: 1000}, False, 0)["margin_pct"] is None


# --- validation -------------------------------------------------------------------

@pytest.mark.parametrize("mutate", [
    lambda r: r.update(name=""),
    lambda r: r.update(name="x" * 61),
    lambda r: r.update(name="Bier" + chr(233)),
    lambda r: r.update(inputs=[]),
    lambda r: r.update(inputs=[{"id": 1, "qty": 1}] * 21),
    lambda r: r.update(inputs=[{"id": 1, "qty": 0}]),
    lambda r: r.update(inputs=[{"id": 1, "qty": -1}]),
    lambda r: r.update(inputs=[{"id": 1, "qty": 1.5}]),
    lambda r: r.update(inputs=[{"id": 1, "qty": True}]),
    lambda r: r.update(inputs=[{"qty": 1}]),
    lambda r: r.update(inputs=[{"id": -1, "qty": 1}]),
    lambda r: r.update(inputs=[{"id": 1, "qty": 1, "vendor_price": -5}]),
    lambda r: r.update(inputs=[{"id": 1, "qty": 1, "extra": 1}]),
    lambda r: r.update(outputs=[]),
    lambda r: r.update(outputs=[{"id": 9, "qty_avg": 0}]),
    lambda r: r.update(outputs=[{"id": 9, "qty_avg": float("nan")}]),
    lambda r: r.update(outputs=[{"name": "Beer", "qty_avg": 1}]),
    lambda r: r.update(outputs=[{"id": 9, "qty_avg": 1}] * 11),
    lambda r: r.update(procs=[{"id": 9, "qty_avg": -0.1}]),
    lambda r: r.update(kind="fishing"),
    lambda r: r.update(nope=1),
])
def test_validate_rejects(mutate):
    r = recipe()
    mutate(r)
    with pytest.raises(ValueError):
        crafting.validate_recipe(r)


def test_validate_normalises():
    r = crafting.validate_recipe(recipe())
    assert r["kind"] == "cooking" and r["procs"] == []
    assert r["inputs"][0] == {"id": 1, "name": None, "qty": 5, "vendor_price": None}
    assert r["outputs"][0] == {"id": 9, "name": None, "qty_avg": 2.5}
    assert crafting.validate_recipe(recipe(kind="alchemy"))["kind"] == "alchemy"


# --- service ------------------------------------------------------------------------

class Env:
    def __init__(self):
        self.prices = {1: 100, 9: 1000}
        self.tax = dict(NO_VP)
        self.peeks = []

    def price(self, iid):
        self.peeks.append(iid)
        return self.prices.get(iid)


@pytest.fixture()
def env():
    return Env()


@pytest.fixture()
def svc(tmp_path, env):
    return crafting.CraftingService(Store(tmp_path / "store"), price=env.price,
                                    tax=lambda: env.tax,
                                    name=lambda iid: {9: "Beer"}.get(iid))


def test_add_view_edit_delete(svc, env):
    doc = svc.add(recipe())
    row = doc["recipes"][0]
    assert row["id"] == "beer" and row["margin"]["profit"] == 1085
    assert row["outputs"][0]["label"] == "Beer"
    assert row["inputs"][0]["label"] == "#1" and row["inputs"][0]["unit"] == 100
    assert row["inputs"][1]["label"] == "Leavening Agent" and row["inputs"][1]["unit"] == 20
    assert doc["tax"] == NO_VP
    assert svc.add(recipe())["recipes"][1]["id"] == "beer-2"
    env.tax = dict(VP)
    assert svc.view()["recipes"][0]["margin"]["net"] == 2112  # floor(845 * 2.5)
    doc = svc.edit(dict(recipe(name="Strong Beer"), id="beer"))
    assert doc["recipes"][0]["name"] == "Strong Beer" and doc["recipes"][0]["id"] == "beer"
    doc = svc.delete("beer")
    assert [r["id"] for r in doc["recipes"]] == ["beer-2"]
    with pytest.raises(ValueError):
        svc.delete("beer")
    with pytest.raises(ValueError):
        svc.edit(dict(recipe(), id="missing"))


def test_cap_on_recipes(svc, monkeypatch):
    monkeypatch.setattr(crafting, "MAX_RECIPES", 2)
    svc.add(recipe())
    svc.add(recipe())
    with pytest.raises(ValueError):
        svc.add(recipe())


def test_vendor_input_is_never_priced_from_market(svc, env):
    svc.add(recipe(inputs=[{"id": 5, "qty": 1, "vendor_price": 3}]))
    assert 5 not in env.peeks


def test_hand_edited_store_is_cleaned(tmp_path, env):
    st = Store(tmp_path / "store")
    st.put("crafting", {"recipes": [dict(crafting.validate_recipe(recipe()), id="ok"),
                                    {"id": "bad", "name": ""}, "junk", None]})
    s = crafting.CraftingService(st, price=env.price, tax=lambda: env.tax)
    assert [r["id"] for r in s.view()["recipes"]] == ["ok"]
    st.put("crafting", {"recipes": "nope"})
    assert s.view()["recipes"] == []


def test_bad_tax_degrades(tmp_path, env):
    s = crafting.CraftingService(Store(tmp_path / "store"), price=env.price,
                                 tax=lambda: {"vp": "yes", "fame_pct": 99})
    s.add(recipe())
    assert s.view()["tax"] == NO_VP


# --- routes ------------------------------------------------------------------------

def _no_network(url, timeout):
    raise AssertionError(f"network: {url}")


@pytest.fixture()
def srv(tmp_path):
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          sse_interval=0.05, market_seed=[],
                          market_client=market.ArshaClient(fetch=_no_network,
                                                           cache_dir=tmp_path / "cache"),
                          config_path=tmp_path / "local.json")
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield s
    s.shutdown()
    s.server_close()


def _req(s, method, path, body=None, ctype="application/json", host=None):
    c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=5)
    headers = {}
    if ctype and body is not None:
        headers["Content-Type"] = ctype
    if host:
        headers["Host"] = host
    data = None if body is None else json.dumps(body).encode()
    c.request(method, path, body=data, headers=headers)
    r = c.getresponse()
    out = r.read()
    c.close()
    return r.status, (json.loads(out) if out else None)


def test_routes(srv):
    st, doc = _req(srv, "GET", "/api/crafting")
    assert st == 200 and doc["recipes"] == [] and doc["tax"] == NO_VP
    st, doc = _req(srv, "POST", "/api/crafting", {"add": recipe()})
    row = doc["recipes"][0]
    assert st == 200 and row["id"] == "beer"
    # nothing cached and no fetch: market-priced lines are missing
    assert row["margin"]["complete"] is False and len(row["margin"]["missing"]) == 2
    st, doc = _req(srv, "POST", "/api/crafting", {"edit": dict(recipe(kind="alchemy"),
                                                              id="beer")})
    assert st == 200 and doc["recipes"][0]["kind"] == "alchemy"
    st, doc = _req(srv, "POST", "/api/crafting", {"delete": "beer"})
    assert st == 200 and doc["recipes"] == []
    assert ewapp.Handler.POST_ROUTES["/api/crafting"] is ewapp.Handler._post_crafting


def test_route_prices_from_cache_with_tax_settings(srv):
    srv.market.cached_price = lambda iid, sid=0: {1: 100, 9: 1000}.get(iid)
    srv.market.settings = {"vp": True, "fame_pct": 0}
    st, doc = _req(srv, "POST", "/api/crafting", {"add": recipe()})
    assert st == 200 and doc["recipes"][0]["margin"]["net"] == 2112


def test_full_recipe_fits_the_post_cap(srv):
    big = recipe(name="N" * 60,
                 inputs=[{"name": "I" * 40, "qty": 9999, "vendor_price": 10 ** 12}] * 20,
                 outputs=[{"id": 2 ** 31 - 1, "qty_avg": 999.999}] * 10,
                 procs=[{"id": 2 ** 31 - 1, "qty_avg": 999.999}] * 10)
    assert _req(srv, "POST", "/api/crafting", {"add": big})[0] == 200


@pytest.mark.parametrize("body", [{}, {"nope": 1}, {"add": recipe(), "delete": "beer"},
                                  {"add": {"name": "x"}}, {"delete": "missing"},
                                  {"delete": 5}])
def test_route_bad_body(srv, body):
    assert _req(srv, "POST", "/api/crafting", body)[0] == 400


def test_route_guards(srv):
    ok = {"add": recipe()}
    assert _req(srv, "POST", "/api/crafting", ok, host="evil.example.com")[0] == 403
    assert _req(srv, "POST", "/api/crafting", ok, ctype="text/plain")[0] == 415
