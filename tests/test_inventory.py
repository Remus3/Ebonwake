"""Plan 045: inventory / weight / storage planner + Value Pack ledger. Operator-typed
LT sources, slots and town storage; the VP state comes from the plan 005 buff
timer (or the plan 030 market.vp setting); the ledger prices logged sales with
plan 027's net_proceeds. Nothing reads the game."""

import datetime as dt
import http.client
import json
import threading
from pathlib import Path

import pytest

from server.ew import app as ewapp
from server.ew import inventory, market
from server.ew.store import Store

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "server" / "ew" / "data" / "weight_sources.json"
T0 = dt.datetime(2026, 10, 5, 12, 0, tzinfo=dt.timezone.utc).timestamp()
DAY = 86400


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


class Env:
    """Injected plan 005 buffs + plan 027 market settings."""

    def __init__(self):
        self.buffs = []
        self.tax = {"vp": False, "fame_pct": 0}


@pytest.fixture()
def env():
    return Env()


@pytest.fixture()
def clock():
    return Clock()


@pytest.fixture()
def svc(tmp_path, env, clock):
    return inventory.InventoryService(Store(tmp_path / "store"), clock=clock,
                                      buffs=lambda: env.buffs, tax=lambda: env.tax)


def src(view, sid):
    return next(r for r in view["sources"] if r["id"] == sid)


def vp_buff(left_s):
    ends = dt.datetime.fromtimestamp(T0 + left_s, dt.timezone.utc).isoformat()
    return {"id": "value-pack", "name": "Value Pack", "ends": ends, "left_s": left_s}


# --- data file ----------------------------------------------------------------

def test_data_file_schema():
    raw = DATA.read_bytes()
    raw.decode("ascii")
    assert b"\r" not in raw
    doc = inventory.load_data()
    ids = [r["id"] for r in doc["sources"]["rows"]]
    assert ids == ["strength", "weight_skill", "pearl", "belt", "jewelry_set",
                   "alchemy_stone", "crystals"]
    # research 0004 section 3 marks every LT range unverified
    assert all(r["verified"] is False for r in doc["sources"]["rows"])
    assert src({"sources": doc["sources"]["rows"]}, "weight_skill")["max"] == 150
    assert doc["value_pack"]["lt"] == 200 and doc["value_pack"]["inventory_slots"] == 16
    assert doc["value_pack"]["verified"] is False
    wh = doc["warehouse"]
    assert (wh["base_vt"], wh["fame_vt"], wh["transfer_vt"]) == (5000, 2000, 200)
    assert wh["source"] == "https://www.naeu.playblackdesert.com/en-us/Wiki?wikiNo=47"
    assert wh["verified"] == "2025-08-06"


@pytest.mark.parametrize("mutate", [
    lambda d: d.pop("warehouse"),
    lambda d: d["sources"]["rows"].append(dict(d["sources"]["rows"][0])),  # dup id
    lambda d: d["sources"]["rows"][0].update(min=50, max=10),
    lambda d: d["sources"]["rows"][0].update(id="Bad Id"),
    lambda d: d["sources"]["rows"][0].update(verified="yes"),
    lambda d: d["warehouse"].update(source="http://x"),
    lambda d: d["warehouse"].update(verified="2025-13-40"),
    lambda d: d["value_pack"].update(lt=-1),
])
def test_validate_data_rejects(mutate):
    doc = json.loads(DATA.read_text(encoding="ascii"))
    mutate(doc)
    with pytest.raises(ValueError):
        inventory.validate_data(doc)


def test_broken_data_degrades(tmp_path, env):
    bad = tmp_path / "w.json"
    bad.write_text("{", encoding="ascii")
    s = inventory.InventoryService(Store(tmp_path / "store"), clock=Clock(), data_path=bad,
                                   buffs=lambda: env.buffs, tax=lambda: env.tax)
    v = s.view()
    assert v["error"] and v["sources"] == []
    with pytest.raises(ValueError, match="unavailable"):
        s.set({"base_lt": 100})


# --- totals -------------------------------------------------------------------------

def test_empty_view(svc):
    v = svc.view()
    assert v["error"] is None
    assert len(v["sources"]) == 7 and all(r["owned_lt"] is None for r in v["sources"])
    assert v["lt_owned"] == 0 and v["lt_total"] is None  # no base typed yet
    assert v["slots"] == {"base": None, "used": None, "vp": 0, "total": None, "free": None}
    assert v["warehouse"]["vt"] == 5000
    assert v["towns"] == [] and v["ledger"]["count"] == 0


def test_lt_totals_and_next_cheapest(svc):
    svc.set({"base_lt": 1000})
    svc.source({"id": "strength", "lt": 40})
    svc.source({"id": "weight_skill", "lt": 100, "next_lt": 10, "next_cost": 50_000_000})
    svc.source({"id": "pearl", "lt": 50, "next_lt": 50, "next_cost": 100_000_000})
    v = svc.source({"id": "belt", "next_lt": 80, "next_cost": 320_000_000,
                    "note": "Basilisk from boss"})
    assert v["lt_owned"] == 190
    assert v["lt_total"] == 1190  # VP off
    assert src(v, "belt")["note"] == "Basilisk from boss"
    nxt = v["next_cheapest"]
    assert [n["id"] for n in nxt] == ["pearl", "belt", "weight_skill"]
    assert nxt[0]["cost_per_lt"] == 2_000_000 and nxt[2]["cost_per_lt"] == 5_000_000
    # clearing a value with null
    v = svc.source({"id": "pearl", "next_lt": None, "next_cost": None})
    assert [n["id"] for n in v["next_cheapest"]] == ["belt", "weight_skill"]


def test_zero_cost_sorts_first(svc):
    svc.source({"id": "crystals", "next_lt": 20, "next_cost": 0})
    v = svc.source({"id": "belt", "next_lt": 80, "next_cost": 1})
    assert [n["id"] for n in v["next_cheapest"]] == ["crystals", "belt"]
    assert v["next_cheapest"][0]["cost_per_lt"] == 0


def test_range_warning_not_rejection(svc):
    v = svc.source({"id": "strength", "lt": 60})
    assert src(v, "strength")["owned_lt"] == 60
    assert "above" in src(v, "strength")["warn"]
    v = svc.source({"id": "strength", "lt": 40})
    assert src(v, "strength")["warn"] == ""


@pytest.mark.parametrize("arg", [
    {"id": "nope", "lt": 1}, {"lt": 1}, {"id": "strength"}, {"id": "strength", "lt": -1},
    {"id": "strength", "lt": 5001}, {"id": "strength", "lt": True}, {"id": "strength", "lt": 1.5},
    {"id": "strength", "next_lt": 0}, {"id": "strength", "next_cost": -1},
    {"id": "strength", "note": "x" * 121}, {"id": "strength", "note": "caf" + chr(233)},
    {"id": "strength", "lt": 1, "extra": 1}, "strength",
])
def test_source_validation(svc, arg):
    with pytest.raises(ValueError):
        svc.source(arg)


def test_slots_and_warehouse(svc):
    v = svc.set({"slots": 120, "slots_used": 100, "fame_vt": True})
    assert v["slots"] == {"base": 120, "used": 100, "vp": 0, "total": 120, "free": 20}
    assert v["warehouse"]["vt"] == 7000 and v["warehouse"]["fame"] is True
    v = svc.set({"slots": None})
    assert v["slots"]["total"] is None and v["slots"]["free"] is None


@pytest.mark.parametrize("arg", [{}, {"slots": 0}, {"slots": 500}, {"slots_used": -1},
                                 {"base_lt": 20001}, {"fame_vt": 1}, {"vp_cost": -5},
                                 {"nope": 1}, []])
def test_set_validation(svc, arg):
    with pytest.raises(ValueError):
        svc.set(arg)


def test_towns(svc):
    v = svc.town_add({"name": "Heidel", "used": 150, "total": 192, "note": "mats"})
    v = svc.town_add({"name": "Heidel", "total": 100})
    assert [t["id"] for t in v["towns"]] == ["heidel", "heidel-2"]
    assert v["towns"][0] == {"id": "heidel", "name": "Heidel", "used": 150, "total": 192,
                             "note": "mats", "free": 42}
    assert v["towns"][1]["used"] is None and v["towns"][1]["free"] is None
    assert v["town_slots"] == {"used": 150, "total": 292}
    v = svc.town_edit({"id": "heidel-2", "name": "Calpheon", "used": 10})
    assert v["towns"][1]["name"] == "Calpheon" and v["towns"][1]["free"] == 90
    v = svc.town_del("heidel")
    assert [t["id"] for t in v["towns"]] == ["heidel-2"]
    with pytest.raises(ValueError):
        svc.town_del("heidel")
    with pytest.raises(ValueError):
        svc.town_add({"name": ""})
    with pytest.raises(ValueError):
        svc.town_add({"name": "X", "used": 10, "total": 5})
    with pytest.raises(ValueError):
        svc.town_edit({"id": "heidel-2"})


def test_hand_edited_store_is_cleaned(tmp_path, env):
    st = Store(tmp_path / "store")
    st.put("inventory", {"base_lt": "lots", "slots": 120, "slots_used": 999,
                         "sources": {"strength": {"lt": 40, "note": 5}, "nope": {"lt": 1},
                                     "belt": "x"},
                         "towns": [{"id": "a", "name": "A"}, {"id": "a", "name": "dup"}, 3],
                         "sales": [{"id": "s1", "at": "2026-10-05T11:00:00+00:00",
                                    "price": 100, "vp": True, "fame_pct": 0},
                                   {"id": "s2", "price": -1}]})
    s = inventory.InventoryService(st, clock=Clock(), buffs=lambda: [], tax=lambda: env.tax)
    v = s.view()
    assert v["base_lt"] is None and v["slots"]["base"] == 120 and v["slots"]["used"] is None
    assert src(v, "strength")["owned_lt"] == 40 and src(v, "strength")["note"] == ""
    assert [t["name"] for t in v["towns"]] == ["A"]
    assert [x["id"] for x in v["ledger"]["sales"]] == ["s1"]


# --- Value Pack -----------------------------------------------------------------

def test_vp_off_reminder(svc):
    svc.set({"base_lt": 1000, "slots": 100})
    v = svc.view()
    assert v["vp"]["active"] is False and v["vp"]["from"] is None
    assert "+200 LT" in v["vp"]["reminder"] and "+16" in v["vp"]["reminder"]
    assert v["lt_total"] == 1000 and v["slots"]["total"] == 100


def test_vp_from_buff_timer(svc, env):
    env.buffs = [{"id": "xp", "name": "XP scroll", "ends": None, "left_s": None},
                 vp_buff(3 * DAY)]
    svc.set({"base_lt": 1000, "slots": 100, "slots_used": 110})
    v = svc.view()
    assert v["vp"]["active"] is True and v["vp"]["from"] == "buff"
    assert v["vp"]["left_s"] == 3 * DAY and v["vp"]["ends"].startswith("2026-10-08")
    assert v["vp"]["reminder"] is None
    assert v["lt_total"] == 1200
    assert v["slots"] == {"base": 100, "used": 110, "vp": 16, "total": 116, "free": 6}


def test_vp_disarmed_buff_is_off(svc, env):
    env.buffs = [{"id": "value-pack", "name": "value pack", "ends": None, "left_s": None}]
    assert svc.view()["vp"]["active"] is False


def test_vp_from_settings_without_timer(svc, env):
    env.tax = {"vp": True, "fame_pct": 0}
    v = svc.view()
    assert v["vp"]["active"] is True and v["vp"]["from"] == "settings"
    assert v["vp"]["left_s"] is None and v["vp"]["ends"] is None


def test_vp_buffs_callable_failure_degrades(tmp_path, env):
    def boom():
        raise RuntimeError("grind down")
    s = inventory.InventoryService(Store(tmp_path / "store"), clock=Clock(), buffs=boom,
                                   tax=lambda: env.tax)
    assert s.view()["vp"]["active"] is False


# --- plan 081: weight / slots from the inventory screenshot -------------------------

def _iso(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).replace(microsecond=0).isoformat()


def test_committed_read_supersedes_typed_and_clears_the_badge(tmp_path, env, clock):
    reads = {}
    s = inventory.InventoryService(Store(tmp_path / "store"), clock=clock,
                                   buffs=lambda: env.buffs, tax=lambda: env.tax,
                                   reads=lambda k: reads.get(k))
    s.set({"base_lt": 1000, "slots": 100, "slots_used": 10})
    s.source({"id": "pearl", "lt": 100})
    assert sorted(r["key"] for r in s.typed_overrides()) == [
        "inventory.base_lt", "inventory.slots", "inventory.slots_used"]
    env.buffs = [vp_buff(DAY)]  # VP on: +200 LT, +16 slots
    clock.t = T0 + 600
    reads["weight"] = {"value": {"used": 812.35, "max": 1560}, "at": _iso(T0 + 300),
                       "source": "ocr:inv.jpg"}
    reads["slots"] = {"value": {"used": 48, "total": 192}, "at": _iso(T0 + 300),
                      "source": "ocr:inv.jpg"}
    v = s.view()
    assert v["base_lt"] == 1560 - 100 - 200 and v["lt_total"] == 1560
    assert v["inputs"]["base_lt"]["source"] == "ocr:inv.jpg"
    assert v["inputs"]["base_lt"]["typed"] == 1000 and v["inputs"]["base_lt"]["age_s"] == 300
    assert v["slots"] == {"base": 176, "used": 48, "vp": 16, "total": 192, "free": 144}
    assert v["weight_now"]["used"] == 812.35 and v["weight_now"]["max"] == 1560
    assert s.typed_overrides() == []
    # typing after the read corrects it
    clock.t = T0 + 900
    v = s.set({"slots_used": 50})
    assert v["slots"]["used"] == 50 and v["inputs"]["slots_used"]["source"] == "typed"
    assert [r["key"] for r in s.typed_overrides()] == ["inventory.slots_used"]
    # stale after 7 days, never silent
    clock.t = T0 + 8 * DAY
    assert s.view()["inputs"]["base_lt"]["stale"] is True


def test_implausible_weight_read_keeps_the_typed_base(tmp_path, env, clock):
    reads = {"weight": {"value": {"used": 10, "max": 50}, "at": _iso(T0), "source": "ocr:x"}}
    s = inventory.InventoryService(Store(tmp_path / "store"), clock=clock,
                                   buffs=lambda: env.buffs, tax=lambda: env.tax,
                                   reads=lambda k: reads.get(k))
    s.source({"id": "pearl", "lt": 100})
    clock.t = T0 - 60
    v = s.set({"base_lt": 1000})
    clock.t = T0 + 60
    v = s.view()   # max 50 - owned 100 < 0: no base LT from that read
    assert v["base_lt"] == 1000 and v["inputs"]["base_lt"]["source"] == "typed"


def test_fame_source_rides_the_view(tmp_path, env):
    s = inventory.InventoryService(Store(tmp_path / "store"), clock=Clock(),
                                   fame=lambda: {"value": 1.25, "source": "ocr", "at": _iso(T0),
                                                 "age_s": 0, "stale": False})
    assert s.view()["fame"]["source"] == "ocr"
    boom = inventory.InventoryService(Store(tmp_path / "s2"), clock=Clock(),
                                      fame=lambda: 1 / 0)
    assert boom.view()["fame"] is None


# --- ledger ---------------------------------------------------------------------------

def test_ledger_maths(svc, env, clock):
    env.buffs = [vp_buff(DAY)]
    v = svc.sale({"price": 100_000_000})  # vp defaults to the live state (on)
    s1 = v["ledger"]["sales"][0]
    assert s1["vp"] is True and s1["fame_pct"] == 0
    assert s1["net"] == market.net_proceeds(100_000_000, True, 0) == 84_500_000
    assert s1["gain"] == 84_500_000 - 65_000_000
    env.tax = {"vp": False, "fame_pct": 1.5}
    v = svc.sale({"price": 1_000_000, "vp": False})
    s2 = v["ledger"]["sales"][0]  # newest first
    assert s2["vp"] is False and s2["gain"] == 0 and s2["fame_pct"] == 1.5
    assert s2["net"] == market.net_proceeds(1_000_000, False, 1.5)
    assert v["ledger"]["count"] == 2 and v["ledger"]["gain_total"] == 19_500_000
    assert v["ledger"]["gain_30d"] == 19_500_000
    clock.t += 31 * DAY
    v = svc.view()
    assert v["ledger"]["gain_total"] == 19_500_000 and v["ledger"]["gain_30d"] == 0


def test_ledger_vp_cost_net(svc, env):
    env.buffs = [vp_buff(DAY)]
    svc.sale({"price": 100_000_000})
    v = svc.set({"vp_cost": 15_000_000})
    assert v["ledger"]["vp_cost"] == 15_000_000
    assert v["ledger"]["net_30d"] == 19_500_000 - 15_000_000
    v = svc.set({"vp_cost": None})
    assert v["ledger"]["net_30d"] is None


def test_ledger_fame_rides_on_the_sale(svc, env):
    env.tax = {"vp": False, "fame_pct": 1}
    v = svc.sale({"price": 10_000_000, "vp": True})
    s = v["ledger"]["sales"][0]
    assert s["gain"] == (market.net_proceeds(10_000_000, True, 1)
                         - market.net_proceeds(10_000_000, False, 1))
    env.tax = {"vp": False, "fame_pct": 0}  # later fame change does not rewrite history
    assert svc.view()["ledger"]["sales"][0]["fame_pct"] == 1


def test_sale_delete_and_validation(svc):
    svc.sale({"price": 5, "vp": True})
    v = svc.sale({"price": 7, "vp": False})
    assert [x["id"] for x in v["ledger"]["sales"]] == ["s2", "s1"]
    v = svc.sale_del("s1")
    assert [x["id"] for x in v["ledger"]["sales"]] == ["s2"]
    v = svc.sale({"price": 9})
    assert v["ledger"]["sales"][0]["id"] == "s3"  # ids never reused
    for bad in ({}, {"price": 0}, {"price": -1}, {"price": 1.5}, {"price": True},
                {"price": 10 ** 13 + 1}, {"price": 5, "vp": "yes"}, {"price": 5, "x": 1}, 5):
        with pytest.raises(ValueError):
            svc.sale(bad)
    with pytest.raises(ValueError):
        svc.sale_del("s1")


def test_sales_capped(svc, monkeypatch):
    monkeypatch.setattr(inventory, "MAX_SALES", 3)
    for p in range(1, 6):
        v = svc.sale({"price": p, "vp": False})
    assert [x["price"] for x in v["ledger"]["sales"]] == [5, 4, 3]


# --- routes -----------------------------------------------------------------------

def _no_network(url, timeout):
    raise AssertionError(f"network: {url}")


@pytest.fixture()
def srv(tmp_path):
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          sse_interval=0.05, market_seed=[],
                          market_client=market.ArshaClient(fetch=_no_network,
                                                           cache_dir=tmp_path / "cache"),
                          config_path=tmp_path / "local.json")
    t = threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
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
    st, doc = _req(srv, "GET", "/api/inventory")
    assert st == 200 and len(doc["sources"]) == 7 and doc["vp"]["active"] is False
    st, doc = _req(srv, "POST", "/api/inventory", {"set": {"base_lt": 900, "slots": 100}})
    assert st == 200 and doc["lt_total"] == 900
    st, doc = _req(srv, "POST", "/api/inventory", {"source": {"id": "strength", "lt": 40}})
    assert st == 200 and doc["lt_total"] == 940
    st, doc = _req(srv, "POST", "/api/inventory", {"town_add": {"name": "Velia", "total": 80}})
    assert st == 200 and doc["towns"][0]["id"] == "velia"
    st, doc = _req(srv, "POST", "/api/inventory", {"town_edit": {"id": "velia", "used": 8}})
    assert st == 200 and doc["towns"][0]["free"] == 72
    st, doc = _req(srv, "POST", "/api/inventory", {"town_del": "velia"})
    assert st == 200 and doc["towns"] == []
    st, doc = _req(srv, "POST", "/api/inventory", {"sale": {"price": 1000, "vp": True}})
    assert st == 200 and doc["ledger"]["sales"][0]["gain"] > 0
    st, doc = _req(srv, "POST", "/api/inventory", {"sale_del": "s1"})
    assert st == 200 and doc["ledger"]["count"] == 0
    # the VP buff timer of plan 005 switches the planner to VP on
    _req(srv, "POST", "/api/grind", {"buff": {"name": "Value Pack", "minutes": 600}})
    st, doc = _req(srv, "GET", "/api/inventory")
    assert doc["vp"]["from"] == "buff" and doc["lt_total"] == 1140
    assert ewapp.Handler.POST_ROUTES["/api/inventory"] is ewapp.Handler._post_inventory


@pytest.mark.parametrize("body", [{}, {"nope": 1}, {"sale_del": "a", "town_del": "a"},
                                  {"source": {"id": "nope", "lt": 1}}, {"town_del": "missing"},
                                  {"sale": {"price": 0}}])
def test_route_bad_body(srv, body):
    assert _req(srv, "POST", "/api/inventory", body)[0] == 400


def test_route_guards(srv):
    ok = {"set": {"base_lt": 1}}
    assert _req(srv, "POST", "/api/inventory", ok, host="evil.example.com")[0] == 403
    assert _req(srv, "POST", "/api/inventory", ok, ctype="text/plain")[0] == 415
