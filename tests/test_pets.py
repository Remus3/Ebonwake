"""Plan 043: pet roster (5 out, tier, talents, Alpha), special-skill coverage and
the exchange planner. Operator-typed roster plus sourced static data; nothing
reads the game."""

import datetime as dt
import http.client
import json
import threading
from pathlib import Path

import pytest

from server.ew import app as ewapp
from server.ew import market, pets
from server.ew.store import Store

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "server" / "ew" / "data" / "pets.json"
T0 = dt.datetime(2026, 10, 5, 12, 0, tzinfo=dt.timezone.utc).timestamp()


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture()
def svc(tmp_path):
    return pets.PetService(Store(tmp_path / "store"), clock=Clock())


def add(svc, name, species="cat", tier=1, **kw):
    return svc.add(dict({"name": name, "species": species, "tier": tier}, **kw))


def row(view, name):
    return next(r for r in view["roster"] if r["name"] == name)


# --- data file ----------------------------------------------------------------

def test_data_file_schema():
    raw = DATA.read_bytes()
    raw.decode("ascii")
    assert b"\r" not in raw
    doc = pets.load_data()
    assert doc["max_out"] == 5
    for sec in ("skills", "feeds", "alpha", "exchange"):
        assert doc[sec]["source"].startswith("https://")
        assert doc[sec]["verified"] == "2025-07-25"
    assert doc["alpha"]["source"] == "https://www.naeu.playblackdesert.com/en-us/Wiki?wikiNo=264"
    assert doc["skills"]["source"] == "https://www.blackdesertfoundry.com/pets-guide/"
    feeds = {f["id"]: f["hunger"] for f in doc["feeds"]["items"]}
    assert feeds == {"cheap": 12, "good": 80, "organic": 140}
    sp = {s["id"]: s["skills"] for s in doc["species"]}
    assert sp["cat"] == ["gathering_detection"] and sp["dog"] == ["player_detection"]
    assert sp["dragon"] == ["player_detection", "auto_fishing"]
    assert sp["bird"] == ["elite_marking"] and sp["fox"] == ["desert_resistance"]
    assert sp["llama"] == ["gathering_amount"] and sp["panda"] == ["taunt"]
    assert set(doc["skills"]["names"]) == {s for v in sp.values() for s in v}
    ex = doc["exchange"]
    assert (ex["target_tier"], ex["max_pets"], ex["full_chance_pets"], ex["full_chance_pct"]) == \
        (4, 5, 5, 100)
    assert doc["alpha"]["loot_bonus_pct"] == 15
    for g in doc["goals"]:
        assert g["skills"] and set(g["skills"]) <= set(doc["skills"]["names"])


@pytest.mark.parametrize("mutate", [
    lambda d: d.pop("species"),
    lambda d: d["species"].append({"id": "cat", "name": "Cat2", "skills": []}),
    lambda d: d["species"][0].update(skills=["nope"]),
    lambda d: d["feeds"].update(source="http://insecure"),
    lambda d: d["alpha"].update(verified="yesterday"),
    lambda d: d["exchange"].update(max_pets=0),
    lambda d: d["goals"].append({"id": "x", "title": "X", "skills": ["nope"]}),
    lambda d: d.update(max_out="5"),
])
def test_data_file_validation_rejects(tmp_path, mutate):
    doc = json.loads(DATA.read_text(encoding="ascii"))
    mutate(doc)
    p = tmp_path / "pets.json"
    p.write_text(json.dumps(doc), encoding="ascii")
    with pytest.raises(ValueError):
        pets.load_data(p)


def test_broken_data_file_never_breaks_the_card(tmp_path):
    p = tmp_path / "pets.json"
    p.write_text("{", encoding="ascii")
    s = pets.PetService(Store(tmp_path / "store"), clock=Clock(), data_path=p)
    v = s.view()
    assert v["error"] and v["roster"] == [] and v["species"] == []
    with pytest.raises(ValueError):
        add(s, "Kitty")


# --- roster + validation ----------------------------------------------------------

def test_empty_view_shape(svc):
    v = svc.view()
    assert v["roster"] == [] and v["max_out"] == 5 and v["error"] is None
    assert v["goals"] == []
    assert {g["id"] for g in v["goal_options"]} == {"detection", "elite", "fishing", "gathering",
                                                    "desert"}
    assert v["coverage"]["missing"] == ["loot"]
    assert v["exchange"] == []
    assert v["feeds"] == [{"id": "cheap", "name": "Cheap Feed", "hunger": 12},
                          {"id": "good", "name": "Good Feed", "hunger": 80},
                          {"id": "organic", "name": "Organic Feed", "hunger": 140}]
    assert "15 percent" in v["alpha_rule"]["text"] and v["alpha_rule"]["source"].startswith("https")
    assert "destroys" in v["exchange_rule"]["text"]


def test_add_normalises_and_ids(svc):
    v = add(svc, "  Kitty ", talents=["Combat EXP", "Item drop"], out=True)
    r = row(v, "Kitty")
    assert r["id"] == "kitty" and r["species"] == "cat" and r["species_name"] == "Cat"
    assert r["skills"] == ["gathering_detection"] and r["skill_names"] == ["gathering detection"]
    assert r["tier"] == 1 and r["talents"] == ["Combat EXP", "Item drop"]
    assert r["out"] is True and r["alpha"] is False and r["fed_at"] is None
    v = add(svc, "Kitty")
    assert [x["id"] for x in v["roster"]] == ["kitty", "kitty-2"]


@pytest.mark.parametrize("arg", [
    None, [], {}, {"name": "A", "species": "cat"},
    {"name": "", "species": "cat", "tier": 1},
    {"name": "x" * 41, "species": "cat", "tier": 1},
    {"name": "A" + chr(233), "species": "cat", "tier": 1},
    {"name": "A", "species": "unicorn", "tier": 1},
    {"name": "A", "species": "cat", "tier": 0},
    {"name": "A", "species": "cat", "tier": 6},
    {"name": "A", "species": "cat", "tier": True},
    {"name": "A", "species": "cat", "tier": 1, "talents": "x"},
    {"name": "A", "species": "cat", "tier": 1, "talents": ["a"] * 6},
    {"name": "A", "species": "cat", "tier": 1, "talents": [""]},
    {"name": "A", "species": "cat", "tier": 1, "out": 1},
    {"name": "A", "species": "cat", "tier": 1, "alpha": True},
    {"name": "A", "species": "cat", "tier": 1, "fed_at": "2026-10-05T00:00:00+00:00"},
    {"name": "A", "species": "cat", "tier": 1, "extra": 1},
])
def test_add_rejects(svc, arg):
    with pytest.raises(ValueError):
        svc.add(arg)
    assert svc.view()["roster"] == []


def test_at_most_five_out(svc):
    for n in range(5):
        add(svc, f"P{n}", out=True)
    with pytest.raises(ValueError, match="5"):
        add(svc, "P5", out=True)
    v = add(svc, "P5")
    assert sum(r["out"] for r in v["roster"]) == 5
    with pytest.raises(ValueError, match="5"):
        svc.edit({"id": "p5", "out": True})
    v = svc.edit({"id": "p0", "out": False})
    v = svc.edit({"id": "p5", "out": True})
    assert row(v, "P5")["out"] and not row(v, "P0")["out"]


def test_alpha_only_one_and_only_t5(svc):
    with pytest.raises(ValueError, match="T5|tier 5"):
        add(svc, "Low", tier=4, alpha=True)
    add(svc, "Boss", tier=5, alpha=True, out=True)
    with pytest.raises(ValueError, match="one alpha|one Alpha"):
        add(svc, "Boss2", tier=5, alpha=True)
    add(svc, "Boss2", tier=5)
    with pytest.raises(ValueError, match="one alpha|one Alpha"):
        svc.edit({"id": "boss2", "alpha": True})
    with pytest.raises(ValueError, match="T5|tier 5"):
        svc.edit({"id": "boss", "tier": 4})  # alpha would sit on a T4
    svc.edit({"id": "boss", "alpha": False})
    v = svc.edit({"id": "boss2", "alpha": True})
    assert row(v, "Boss2")["alpha"] and not row(v, "Boss")["alpha"]


def test_edit_remove_feed(svc):
    add(svc, "Kitty")
    clock = svc.clock
    v = svc.edit({"id": "kitty", "name": "Mittens", "species": "dog", "tier": 3,
                  "talents": ["Combat EXP"]})
    r = v["roster"][0]
    assert (r["id"], r["name"], r["species"], r["tier"], r["talents"]) == \
        ("kitty", "Mittens", "dog", 3, ["Combat EXP"])
    v = svc.feed("kitty")
    assert v["roster"][0]["fed_at"] == "2026-10-05T12:00:00+00:00"
    clock.t += 5400
    v = svc.view()
    assert v["roster"][0]["fed_ago_s"] == 5400
    with pytest.raises(ValueError):
        svc.edit({"id": "kitty"})
    with pytest.raises(ValueError):
        svc.edit({"id": "nope", "tier": 2})
    with pytest.raises(ValueError):
        svc.feed("nope")
    with pytest.raises(ValueError):
        svc.remove("nope")
    assert svc.remove("kitty")["roster"] == []


def test_roster_cap(svc):
    for n in range(pets.MAX_ROSTER):
        add(svc, f"P{n}")
    with pytest.raises(ValueError, match=str(pets.MAX_ROSTER)):
        add(svc, "Over")


def test_corrupt_store_rows_are_cleaned(tmp_path):
    st = Store(tmp_path / "store")
    good = {"id": "a", "name": "A", "species": "cat", "tier": 5, "talents": [], "alpha": True,
            "out": True, "fed_at": None}
    rows = [good, dict(good, id="b", name="B"),  # second alpha dropped
            dict(good, id="c", name="C", tier=9), {"junk": 1}, "x",
            dict(good, id="a", name="dup")]
    rows += [dict(good, id=f"o{n}", name=f"O{n}", tier=1, alpha=False) for n in range(6)]
    st.put("pets", {"roster": rows, "goals": ["fishing", "nope"]})
    v = pets.PetService(st, clock=Clock()).view()
    ids = [r["id"] for r in v["roster"]]
    assert ids[:2] == ["a", "b"] and "c" not in ids and ids.count("a") == 1
    assert sum(r["alpha"] for r in v["roster"]) == 1
    assert sum(r["out"] for r in v["roster"]) == 5
    assert v["goals"] == ["fishing"]


# --- coverage ---------------------------------------------------------------------

def test_coverage_loot_always_and_goals(svc):
    v = svc.set_goals(["detection", "fishing", "gathering"])
    assert v["goals"] == ["detection", "fishing", "gathering"]
    cov = v["coverage"]
    assert cov["missing"] == ["loot", "detection", "fishing", "gathering"]
    add(svc, "Kitty", out=True)
    v = add(svc, "Drake", species="dragon", tier=4, out=True)
    cov = v["coverage"]
    assert cov["out"] == 2 and cov["free_slots"] == 3
    assert cov["loot"]["covered"] and cov["loot"]["t4_plus"] == 1
    goals = {g["id"]: g for g in cov["goals"]}
    assert goals["detection"]["covered"] and goals["detection"]["by"] == ["Drake"]
    assert goals["fishing"]["covered"]
    assert not goals["gathering"]["covered"]
    assert goals["gathering"]["missing"] == ["gathering_amount"]
    assert goals["gathering"]["missing_names"] == ["gathering amount"]
    assert cov["missing"] == ["gathering"]


def test_coverage_counts_only_out_pets(svc):
    svc.set_goals(["elite"])
    v = add(svc, "Birdie", species="bird")
    assert v["coverage"]["missing"] == ["loot", "elite"]


def test_coverage_alpha_bonus_and_warning(svc):
    v = add(svc, "Boss", tier=5, alpha=True)
    cov = v["coverage"]
    assert cov["loot"]["alpha"] is None and cov["loot"]["alpha_bonus_pct"] == 0
    assert any("Boss" in w and "not out" in w for w in cov["warnings"])
    v = svc.edit({"id": "boss", "out": True})
    cov = v["coverage"]
    assert cov["loot"]["alpha"] == "Boss" and cov["loot"]["alpha_bonus_pct"] == 15
    assert cov["warnings"] == []


@pytest.mark.parametrize("arg", ["fishing", ["nope"], ["fishing", "fishing"], [1], None])
def test_set_goals_rejects(svc, arg):
    with pytest.raises(ValueError):
        svc.set_goals(arg)


# --- exchange planner -------------------------------------------------------------

def test_exchange_planner_full_chance_at_five(svc):
    for n in range(6):
        add(svc, f"Cat{n}", tier=1 + n % 3, out=n == 0)
    add(svc, "BigCat", tier=4)
    add(svc, "Doggo", species="dog")
    ex = svc.view()["exchange"]
    assert [e["species"] for e in ex] == ["cat"]
    e = ex[0]
    assert e["count"] == 6 and e["use"] == 5 and e["target_tier"] == 4
    assert e["chance_pct"] == 100 and e["can_exchange"] and e["need_for_full"] == 0
    assert e["destroyed"] == ["Cat0", "Cat1", "Cat2", "Cat3", "Cat4"]
    assert "BigCat" not in e["candidates"]
    assert e["out_used"] == ["Cat0"]
    assert "destroyed" in e["warning"] and "Cat0" in e["warning"]


def test_exchange_planner_below_five_chance_unsourced(svc):
    add(svc, "A", species="otter", tier=3)
    add(svc, "B", species="otter", tier=3)
    add(svc, "C", species="otter", tier=5, alpha=False)  # T5 can no longer be exchanged
    e = svc.view()["exchange"][0]
    assert e["species"] == "otter" and e["count"] == 2 and e["use"] == 2
    assert e["chance_pct"] is None and e["need_for_full"] == 3 and e["can_exchange"]
    assert e["destroyed"] == ["A", "B"]


def test_exchange_query_one_species(svc):
    add(svc, "A", species="fox")
    e = svc.exchange("fox")
    assert e["count"] == 1 and not e["can_exchange"] and e["chance_pct"] is None
    assert e["destroyed"] == []
    with pytest.raises(ValueError):
        svc.exchange("unicorn")
    with pytest.raises(ValueError):
        svc.exchange("other")  # no type to match


# --- routes -----------------------------------------------------------------------

def _no_network(url, timeout):
    raise AssertionError(f"network: {url}")


@pytest.fixture()
def srv(tmp_path):
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          sse_interval=0.05, market_seed=[],
                          market_client=market.ArshaClient(fetch=_no_network,
                                                           cache_dir=tmp_path / "cache"))
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
    st, doc = _req(srv, "GET", "/api/pets")
    assert st == 200 and doc["roster"] == []
    st, doc = _req(srv, "POST", "/api/pets",
                   {"add": {"name": "Kitty", "species": "cat", "tier": 2, "out": True}})
    assert st == 200 and doc["roster"][0]["id"] == "kitty"
    st, doc = _req(srv, "POST", "/api/pets", {"edit": {"id": "kitty", "tier": 3}})
    assert st == 200 and doc["roster"][0]["tier"] == 3
    st, doc = _req(srv, "POST", "/api/pets", {"feed": "kitty"})
    assert st == 200 and doc["roster"][0]["fed_at"]
    st, doc = _req(srv, "POST", "/api/pets", {"goals": ["gathering"]})
    assert st == 200 and doc["goals"] == ["gathering"]
    st, doc = _req(srv, "GET", "/api/pets?exchange=cat")
    assert st == 200 and doc["species"] == "cat" and doc["count"] == 1
    assert _req(srv, "GET", "/api/pets?exchange=unicorn")[0] == 400
    st, doc = _req(srv, "POST", "/api/pets", {"remove": "kitty"})
    assert st == 200 and doc["roster"] == []
    assert ewapp.Handler.POST_ROUTES["/api/pets"] is ewapp.Handler._post_pets


@pytest.mark.parametrize("body", [{}, {"nope": 1}, {"feed": "a", "remove": "a"},
                                  {"add": {"name": "A"}}, {"remove": "missing"},
                                  {"goals": "fishing"}])
def test_route_bad_body(srv, body):
    assert _req(srv, "POST", "/api/pets", body)[0] == 400


def test_route_guards(srv):
    ok = {"goals": []}
    assert _req(srv, "POST", "/api/pets", ok, host="evil.example.com")[0] == 403
    assert _req(srv, "POST", "/api/pets", ok, ctype="text/plain")[0] == 415
