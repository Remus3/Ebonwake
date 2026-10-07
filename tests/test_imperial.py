"""Plan 053: imperial delivery planner - CP / 2 cap per type, 250 percent box
value on cached prices, missing prices, data schema, reset day + countdown,
operator ticks / boxes, and the /api/imperial route."""

import copy
import datetime as dt
import http.client
import json
import threading

import pytest

from server.ew import app as ewapp
from server.ew import imperial, market
from server.ew.store import Store, atomic_write_json

T0 = dt.datetime(2026, 10, 5, 22, 30, tzinfo=dt.timezone.utc).timestamp()  # 1h30 to reset


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


def _data(tmp_path, boxes=()):
    doc = json.loads(imperial.DATA_PATH.read_text(encoding="ascii"))
    doc["boxes"] = list(boxes)
    p = tmp_path / "imperial_boxes.json"
    p.write_text(json.dumps(doc), encoding="ascii")
    return p


SEED = {"type": "cooking", "name": "Beer box", "items": [{"id": 9213, "qty": 10}],
        "source": "https://example.org/beer", "verified": False}


def svc(tmp_path, cp=None, prices=None, boxes=(), clock=None):
    return imperial.ImperialService(Store(tmp_path / "store"), clock=clock or Clock(),
                                    cp=cp, prices=prices, data_path=_data(tmp_path, boxes))


# --- data file ----------------------------------------------------------------------

def test_data_file_schema_and_ascii():
    raw = imperial.DATA_PATH.read_bytes()
    assert all(b < 128 for b in raw) and b"\r" not in raw
    doc = imperial.load_data()
    r = doc["rules"]
    assert r["cap_divisor"] == 2 and r["payout_pct"] == 250 and r["taxed"] is False
    assert r["types"] == ["cooking", "alchemy"]
    assert r["reset"] == {"every": "day", "at": "00:00"}
    assert "wikiNo=133" in r["source"]
    for b in doc["boxes"]:  # seed rows must cite a source
        assert b["source"].startswith("https://")


def _bad(mut):
    doc = copy.deepcopy(imperial.load_data())
    doc["boxes"] = [copy.deepcopy(SEED)]
    mut(doc)
    return doc


@pytest.mark.parametrize("mut", [
    lambda d: d.pop("boxes"),
    lambda d: d["rules"].update(payout_pct=0),
    lambda d: d["rules"].update(cap_divisor=0),
    lambda d: d["rules"].update(taxed=True),
    lambda d: d["rules"].update(types=["cooking"]),
    lambda d: d["rules"].update(reset={"every": "week", "weekday": 3}),
    lambda d: d["rules"].update(source="http://insecure"),
    lambda d: d["rules"].update(verified="2026-13-40"),
    lambda d: d["rules"].update(extra=1),
    lambda d: d["boxes"][0].update(type="fishing"),
    lambda d: d["boxes"][0].update(name=""),
    lambda d: d["boxes"][0].update(items=[]),
    lambda d: d["boxes"][0].update(items=[{"id": 1, "qty": 0}]),
    lambda d: d["boxes"][0].update(items=[{"id": 1, "qty": 1}, {"id": 1, "qty": 2}]),
    lambda d: d["boxes"][0].update(items=[{"id": True, "qty": 1}]),
    lambda d: d["boxes"][0].pop("source"),
    lambda d: d["boxes"].append(copy.deepcopy(SEED)),
])
def test_validate_data_rejects(mut):
    with pytest.raises(ValueError):
        imperial.validate_data(_bad(mut))


def test_broken_data_file_never_breaks_the_card(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{", encoding="ascii")
    s = imperial.ImperialService(Store(tmp_path / "store"), clock=Clock(), cp=lambda: 300,
                                 data_path=p)
    v = s.view()
    assert v["data_error"] and v["rules"] is None and v["boxes"] == []
    assert v["types"][0]["cap"] == 150  # defaults still plan the day


# --- cap --------------------------------------------------------------------------

@pytest.mark.parametrize("cp,cap", [(0, 0), (1, 0), (2, 1), (301, 150), (300.0, 150)])
def test_daily_cap(cp, cap):
    assert imperial.daily_cap(cp) == cap


@pytest.mark.parametrize("cp", [None, -1, "300", True, float("nan")])
def test_daily_cap_unknown(cp):
    assert imperial.daily_cap(cp) is None


# --- valuation ----------------------------------------------------------------------

BOX = {"type": "cooking", "name": "B", "items": [{"id": 1, "qty": 10}, {"id": 2, "qty": 2}]}


def test_box_value_250_percent_of_base_vs_last_cost():
    prices = {1: {"base": 1000, "last": 800}, 2: {"base": 5000, "last": 6000}}
    v = imperial.box_value(BOX, prices)
    assert v["payout"] == (10 * 1000 + 2 * 5000) * 25 // 10  # 50000
    assert v["cost"] == 10 * 800 + 2 * 6000  # 20000
    assert v["ratio"] == 2.5 and v["missing"] == []


def test_box_value_mastery_and_int_prices():
    v = imperial.box_value(BOX, lambda i: {1: 1000, 2: 5000}[i], mastery_pct=12.5)
    assert v["payout"] == 56250  # 20000 x 2.5 x 1.125, exact
    assert v["cost"] == 20000


def test_box_value_falls_back_between_base_and_last():
    v = imperial.box_value(BOX, {1: {"base": None, "last": 100}, 2: {"base": 200, "last": None}})
    assert v["payout"] == (1000 + 400) * 25 // 10 and v["cost"] == 1400


@pytest.mark.parametrize("prices", [
    {1: {"base": 1000, "last": 800}},
    {1: {"base": 1000, "last": 800}, 2: {"base": None, "last": None}},
    {1: 1000, 2: 0},
    {1: 1000, 2: "5000"},
])
def test_box_value_missing_price(prices):
    v = imperial.box_value(BOX, prices)
    assert v == {"payout": None, "cost": None, "ratio": None, "missing": [2]}


def test_best_boxes_by_ratio_then_payout_unpriced_never_rank():
    rows = [{"id": "a", "name": "a", "ratio": 2.0, "payout": 10},
            {"id": "b", "name": "b", "ratio": 3.0, "payout": 5},
            {"id": "c", "name": "c", "ratio": 2.0, "payout": 50},
            {"id": "d", "name": "d", "ratio": None, "payout": None}]
    assert imperial.best_boxes(rows) == ["b", "c", "a"]
    assert imperial.best_boxes(rows, 1) == ["b"]


# --- service ------------------------------------------------------------------------

def test_view_cp_from_profile_wins_over_typed(tmp_path):
    s = svc(tmp_path, cp=lambda: 301)
    s.set_cp(100)
    v = s.view()
    assert v["cp"] == {"value": 301, "source": "profile", "typed": 100, "at": None,
                       "age_s": None, "stale": False}
    assert [t["cap"] for t in v["types"]] == [150, 150]


@pytest.mark.parametrize("profile", [lambda: None, lambda: "hidden",
                                     lambda: (_ for _ in ()).throw(RuntimeError())])
def test_view_cp_typed_when_profile_hides_it(tmp_path, profile):
    s = svc(tmp_path, cp=profile)
    assert s.view()["cp"]["value"] is None and s.view()["types"][0]["left"] is None
    v = s.set_cp(80)
    assert v["cp"] == {"value": 80, "source": "operator", "typed": 80,
                       "at": "2026-10-05T22:30:00+00:00", "age_s": 0, "stale": False}
    assert v["types"][1] == {"type": "alchemy", "cap": 40, "delivered": 0, "left": 40,
                             "done": False, "mastery_pct": 0}


# --- plan 081: CP from the CP readout screenshot ----------------------------------------

def _iso(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).replace(microsecond=0).isoformat()


def test_committed_cp_read_supersedes_typed_and_clears_the_badge(tmp_path):
    clock = Clock()
    reads = {}
    s = imperial.ImperialService(Store(tmp_path / "store"), clock=clock,
                                 data_path=_data(tmp_path), reads=lambda k: reads.get(k))
    s.set_cp(80)
    assert [r["key"] for r in s.typed_overrides()] == ["imperial.cp"]
    clock.t = T0 + 600
    reads["cp"] = {"value": 312, "at": _iso(T0 + 300), "source": "ocr:cp.jpg"}
    v = s.view()
    assert v["cp"]["value"] == 312 and v["cp"]["source"] == "ocr" and v["cp"]["typed"] == 80
    assert v["cp"]["age_s"] == 300 and v["cp"]["stale"] is False
    assert [t["cap"] for t in v["types"]] == [156, 156]
    assert s.typed_overrides() == []
    # typing after the read corrects it until the next read
    s.set_cp(300)
    assert s.view()["cp"]["source"] == "operator" and s.view()["cp"]["value"] == 300
    reads["cp"] = {"value": 320, "at": _iso(T0 + 900), "source": "ocr:cp2.jpg"}
    clock.t = T0 + 8 * 86400
    v = s.view()
    assert v["cp"]["value"] == 320 and v["cp"]["stale"] is True  # > 7 d old: muted, not silent


def test_profile_cp_still_wins_over_an_ocr_read(tmp_path):
    s = imperial.ImperialService(Store(tmp_path / "store"), clock=Clock(), cp=lambda: 301,
                                 data_path=_data(tmp_path),
                                 reads=lambda k: {"value": 312, "at": _iso(T0), "source": "x"})
    assert s.view()["cp"]["source"] == "profile"


def test_reset_countdown_and_day(tmp_path):
    v = svc(tmp_path).view()
    assert v["day"] == "2026-10-05"
    assert v["reset"] == {"next_utc": "2026-10-06T00:00:00+00:00", "left_s": 5400}


def test_deliver_ticks_clamp_and_reset_at_midnight(tmp_path):
    clock = Clock()
    s = svc(tmp_path, cp=lambda: 10, clock=clock)
    v = s.deliver({"type": "cooking", "add": 3})
    assert v["types"][0]["delivered"] == 3 and v["types"][0]["left"] == 2
    v = s.deliver({"type": "cooking", "add": 99})
    assert v["types"][0]["delivered"] == 5 and v["types"][0]["done"] is True
    v = s.deliver({"type": "cooking", "add": -1})
    assert v["types"][0]["delivered"] == 4
    v = s.deliver({"type": "alchemy", "add": -5})
    assert v["types"][1]["delivered"] == 0
    clock.t += 5400  # 00:00 UTC: new server day
    v = s.view()
    assert v["day"] == "2026-10-06" and v["types"][0]["delivered"] == 0
    assert v["reset"]["left_s"] == 86400
    v = s.deliver({"type": "cooking", "add": 1})
    assert v["types"][0]["delivered"] == 1


def test_deliver_without_cp_clamps_at_count_max(tmp_path):
    s = svc(tmp_path)
    v = s.deliver({"type": "cooking", "add": 7})
    assert v["types"][0]["delivered"] == 7 and v["types"][0]["left"] is None


@pytest.mark.parametrize("arg", [
    None, {}, {"type": "cooking"}, {"type": "fishing", "add": 1}, {"type": "cooking", "add": 0},
    {"type": "cooking", "add": 1.5}, {"type": "cooking", "add": True},
    {"type": "cooking", "add": imperial.COUNT_MAX + 1}, {"type": "cooking", "add": 1, "x": 1},
])
def test_deliver_rejects(tmp_path, arg):
    with pytest.raises(ValueError):
        svc(tmp_path).deliver(arg)


@pytest.mark.parametrize("cp", [-1, imperial.CP_MAX + 1, 1.5, "300", True])
def test_set_cp_rejects(tmp_path, cp):
    with pytest.raises(ValueError):
        svc(tmp_path).set_cp(cp)


def test_mastery_applies_per_type(tmp_path):
    prices = {9213: {"base": 1000, "last": 900}}.get
    s = svc(tmp_path, prices=prices, boxes=[SEED])
    v = s.set_mastery({"type": "cooking", "pct": 20})
    assert v["types"][0]["mastery_pct"] == 20
    row = v["boxes"][0]
    assert row["payout"] == 10 * 1000 * 25 // 10 * 12 // 10  # 30000
    assert row["cost"] == 9000 and row["origin"] == "seed" and row["id"] == "cooking-beer-box"
    assert v["best"] == {"cooking": ["cooking-beer-box"], "alchemy": []}


@pytest.mark.parametrize("arg", [{"type": "cooking"}, {"type": "cooking", "pct": -1},
                                 {"type": "x", "pct": 1},
                                 {"type": "cooking", "pct": imperial.MASTERY_MAX + 1}])
def test_mastery_rejects(tmp_path, arg):
    with pytest.raises(ValueError):
        svc(tmp_path).set_mastery(arg)


def test_operator_boxes_add_rank_delete(tmp_path):
    prices = {1: {"base": 100, "last": 100}, 2: {"base": 100, "last": 50}}.get
    s = svc(tmp_path, prices=prices)
    s.box_add({"type": "alchemy", "name": "Elixir crate", "items": [{"id": 1, "qty": 5}]})
    v = s.box_add({"type": "alchemy", "name": "Cheap crate", "items": [{"id": 2, "qty": 5}]})
    v = s.box_add({"type": "alchemy", "name": "Unpriced", "items": [{"id": 3, "qty": 1}]})
    assert [b["id"] for b in v["boxes"]] == ["alchemy-elixir-crate", "alchemy-cheap-crate",
                                             "alchemy-unpriced"]
    assert v["best"]["alchemy"] == ["alchemy-cheap-crate", "alchemy-elixir-crate"]
    assert v["boxes"][2]["missing"] == [3] and v["boxes"][2]["origin"] == "operator"
    with pytest.raises(ValueError):
        s.box_add({"type": "alchemy", "name": "elixir crate", "items": [{"id": 1, "qty": 1}]})
    v = s.box_del("alchemy-unpriced")
    assert len(v["boxes"]) == 2
    with pytest.raises(ValueError):
        s.box_del("alchemy-unpriced")


def test_operator_box_cannot_shadow_a_seed_row(tmp_path):
    s = svc(tmp_path, boxes=[SEED])
    with pytest.raises(ValueError):
        s.box_add({"type": "cooking", "name": "Beer Box", "items": [{"id": 1, "qty": 1}]})
    with pytest.raises(ValueError):
        s.box_del("cooking-beer-box")


@pytest.mark.parametrize("arg", [
    None, {"type": "cooking", "name": "x"},
    {"type": "cooking", "name": "", "items": [{"id": 1, "qty": 1}]},
    {"type": "cooking", "name": "x" * 41, "items": [{"id": 1, "qty": 1}]},
    {"type": "cooking", "name": "x", "items": [{"id": 1, "qty": imperial.QTY_MAX + 1}]},
    {"type": "cooking", "name": "x", "items": [{"id": 1, "qty": 1}] * 2},
    {"type": "cooking", "name": "x", "items": [{"id": i, "qty": 1} for i in range(1, 12)]},
])
def test_box_add_rejects(tmp_path, arg):
    with pytest.raises(ValueError):
        svc(tmp_path).box_add(arg)


def test_corrupt_store_rows_degrade(tmp_path):
    s = svc(tmp_path)
    s.store.put("imperial", {"day": "2026-10-05", "delivered": {"cooking": -3, "alchemy": "x"},
                             "cp": "lots", "mastery": {"cooking": 9999},
                             "boxes": [{"id": "nope"}, 5,
                                       {"id": "cooking-ok", "type": "cooking", "name": "ok",
                                        "items": [{"id": 1, "qty": 1}]}]})
    v = s.view()
    assert [t["delivered"] for t in v["types"]] == [0, 0]
    assert v["cp"]["typed"] is None and v["types"][0]["mastery_pct"] == 0
    assert [b["id"] for b in v["boxes"]] == ["cooking-ok"]


def test_item_names_ride_along(tmp_path):
    s = imperial.ImperialService(Store(tmp_path / "store"), clock=Clock(),
                                 name=lambda i: "Beer" if i == 9213 else None,
                                 data_path=_data(tmp_path, [SEED]))
    assert s.view()["boxes"][0]["items"] == [{"id": 9213, "qty": 10, "name": "Beer"}]


# --- routes ------------------------------------------------------------------------

def _no_network(url, timeout):
    raise AssertionError("test touched the network")


@pytest.fixture()
def srv(tmp_path):
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          market_seed=[], profile_cfg={},
                          market_client=market.ArshaClient(fetch=_no_network,
                                                           cache_dir=tmp_path / "cache"))
    t = threading.Thread(target=s.serve_forever, daemon=True)
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


def test_route_get_and_post(srv):
    assert ewapp.Handler.POST_ROUTES["/api/imperial"] is ewapp.Handler._post_imperial
    st, doc = _req(srv, "GET", "/api/imperial")
    assert st == 200 and doc["data_error"] is None and doc["cp"]["value"] is None
    assert doc["rules"]["payout_pct"] == 250 and doc["reset"]["left_s"] > 0
    st, doc = _req(srv, "POST", "/api/imperial", {"cp": 120})
    assert st == 200 and doc["types"][0]["cap"] == 60
    st, doc = _req(srv, "POST", "/api/imperial", {"deliver": {"type": "alchemy", "add": 4}})
    assert st == 200 and doc["types"][1]["left"] == 56
    st, doc = _req(srv, "POST", "/api/imperial", {"mastery": {"type": "alchemy", "pct": 15}})
    assert st == 200 and doc["types"][1]["mastery_pct"] == 15
    st, doc = _req(srv, "POST", "/api/imperial", {"box_add": {
        "type": "cooking", "name": "Test", "items": [{"id": 9213, "qty": 2}]}})
    assert st == 200 and doc["boxes"][-1]["missing"] == [9213]  # empty cache: no fetch
    st, doc = _req(srv, "POST", "/api/imperial", {"box_del": "cooking-test"})
    assert st == 200 and not any(b["origin"] == "operator" for b in doc["boxes"])


def test_route_values_boxes_from_the_cached_sublist(srv, tmp_path):
    atomic_write_json(tmp_path / "cache" / "sublist_9213_0.json",
                      {"fetched_at": 0, "data": {"id": 9213, "basePrice": 1000,
                                                 "lastSoldPrice": 800}})
    assert srv.market.cached_prices(9213) == {"base": 1000, "last": 800}
    assert srv.market.cached_prices(424242) == {"base": None, "last": None}
    st, doc = _req(srv, "POST", "/api/imperial", {"box_add": {
        "type": "cooking", "name": "Beer", "items": [{"id": 9213, "qty": 4}]}})
    row = doc["boxes"][-1]
    assert st == 200 and row["payout"] == 10000 and row["cost"] == 3200 and row["ratio"] == 3.125
    assert doc["best"]["cooking"] == ["cooking-beer"]


@pytest.mark.parametrize("body", [
    {}, {"tick": "x"}, {"cp": -1}, {"deliver": {"type": "cooking"}},
    {"cp": 1, "mastery": {"type": "cooking", "pct": 1}}, {"box_del": "nope"},
])
def test_route_post_bad(srv, body):
    assert _req(srv, "POST", "/api/imperial", body)[0] == 400
