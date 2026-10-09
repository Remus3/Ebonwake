"""Plan 044: mount tracker (horses: tier, level, skills), T10 materials with a
Royal Fern Root days-to-go counter, and the T10 breed pity calculator
(3 percent base, +0.2 percent per failure; BDFoundry 2026-07-03).

Operator-typed data plus a tracked sourced data file; nothing reads the game.
"""

import http.client
import json
import random
import threading
from fractions import Fraction
from pathlib import Path

import pytest

from server.ew import app as ewapp
from server.ew import market, mounts
from server.ew.store import Store

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "server" / "ew" / "data" / "mounts.json"
T10 = "https://www.blackdesertfoundry.com/dreamy-horses-tier-10/"


def svc(tmp_path):
    return mounts.MountsService(Store(tmp_path / "store"), clock=lambda: 1_790_000_000.0)


# --- data file -------------------------------------------------------------------

def test_data_file_schema_and_ascii():
    DATA.read_bytes().decode("ascii")
    doc = mounts.load_data()
    t = doc["t10"]
    assert t["names"] == ["Arduanatt", "Dine", "Doom"]
    assert (t["parent_tier"], t["parent_level"]) == (9, 30)
    need = {m["key"]: m["need"] for m in t["materials"]}
    assert need == {"censer": 1, "royal_fern_root": 100, "flower_of_oblivion": 100,
                    "mythical_feather": 10}
    assert t["pity"]["base_pct"] == 3 and t["pity"]["per_fail_pct"] == 0.2
    assert t["pity"]["cap_pct"] == 100
    # every row carries its own source + verified (date or false)
    rows = t["materials"] + [t["pity"], t["per_attempt"]] + doc["unlocks"]
    for r in rows:
        assert r["source"].startswith("https://")
        assert r["verified"] is False or (isinstance(r["verified"], str) and len(r["verified"]) == 10)
    assert t["pity"]["source"] == T10 and t["pity"]["verified"] == "2026-07-03"
    kinds = {u["kind"]: u for u in doc["unlocks"]}
    assert kinds["camel"]["min_level"] == 54 and kinds["elephant"]["min_level"] == 55
    assert {k["kind"]: k["max_level"] for k in doc["kinds"]}["camel"] == 20


@pytest.mark.parametrize("mutate", [
    lambda d: d["t10"]["pity"].update(base_pct=-1),
    lambda d: d["t10"]["pity"].update(per_fail_pct=0),
    lambda d: d["t10"]["pity"].update(cap_pct=101),
    lambda d: d["t10"]["materials"][0].update(need=0),
    lambda d: d["t10"]["materials"][0].update(key="Bad Key"),
    lambda d: d["t10"]["materials"][1].update(source="http://x"),
    lambda d: d["t10"]["materials"][1].update(verified="yesterday"),
    lambda d: d["unlocks"][0].update(verified=True),
    lambda d: d["kinds"].append({"kind": "horse", "max_level": 30}),
    lambda d: d["t10"].pop("pity"),
])
def test_validate_data_rejects(mutate):
    doc = json.loads(DATA.read_text(encoding="ascii"))
    mutate(doc)
    with pytest.raises(ValueError):
        mounts.validate_data(doc)


def test_broken_data_file_never_breaks_the_card(tmp_path):
    bad = tmp_path / "mounts.json"
    bad.write_text("{nope", encoding="ascii")
    s = mounts.MountsService(Store(tmp_path / "store"), data_path=bad)
    v = s.view()
    assert v["data_error"] and v["materials"] == [] and v["odds"] is None
    assert v["mounts"] == []
    with pytest.raises(ValueError):
        s.materials({"royal_fern_root": 3})


# --- pity math --------------------------------------------------------------------

PITY = {"base_pct": 3, "per_fail_pct": 0.2, "cap_pct": 100}


def _p(f):
    return min(Fraction(1), Fraction(3, 100) + Fraction(2, 1000) * f)


def _brute_cum(f, k):
    miss = Fraction(1)
    for i in range(k):
        miss *= 1 - _p(f + i)
    return 1 - miss


def _brute_attempts(f, target):
    k = 1
    while _brute_cum(f, k) < target:
        k += 1
    return k


def test_breed_odds_linear_then_capped():
    assert mounts.breed_odds(0, PITY) == pytest.approx(0.03)
    assert mounts.breed_odds(1, PITY) == pytest.approx(0.032)
    assert mounts.breed_odds(10, PITY) == pytest.approx(0.05)
    assert mounts.breed_odds(484, PITY) == pytest.approx(0.998)
    assert mounts.breed_odds(485, PITY) == 1.0
    assert mounts.breed_odds(5000, PITY) == 1.0


@pytest.mark.parametrize("bad", [-1, 1.5, True, "3", None])
def test_breed_odds_rejects_bad_failures(bad):
    with pytest.raises(ValueError):
        mounts.breed_odds(bad, PITY)


@pytest.mark.parametrize("f", [0, 1, 7, 50, 200, 480, 485, 600])
@pytest.mark.parametrize("target", [0.01, 0.25, 0.5, 0.9, 0.99, 1.0])
def test_attempts_for_matches_brute_force(f, target):
    assert mounts.attempts_for(target, f, PITY) == _brute_attempts(f, Fraction(target))


def test_attempts_for_known_values():
    # P(first try) = 3 percent, so any target up to 0.03 needs one attempt.
    assert mounts.attempts_for(0.03, 0, PITY) == 1
    assert mounts.attempts_for(0.0301, 0, PITY) == 2
    # The pity guarantees success by the attempt where the chance reaches 100.
    assert mounts.attempts_for(1.0, 0, PITY) == 486
    assert mounts.attempts_for(1.0, 485, PITY) == 1


@pytest.mark.parametrize("bad", [0, -0.1, 1.01, "0.5", None, True])
def test_attempts_for_rejects_bad_target(bad):
    with pytest.raises(ValueError):
        mounts.attempts_for(bad, 0, PITY)


@pytest.mark.parametrize("f", [0, 30, 300])
def test_expected_attempts_matches_brute_force(f):
    exp, miss, k = Fraction(0), Fraction(1), 0
    while miss > 0:
        exp += miss
        miss *= 1 - _p(f + k)
        k += 1
    assert mounts.expected_attempts(f, PITY) == pytest.approx(float(exp), rel=1e-12)


def test_cumulative_matches_brute_force_and_simulation():
    for f, k in [(0, 1), (0, 20), (12, 33), (400, 90)]:
        assert mounts.cumulative(f, k, PITY) == pytest.approx(float(_brute_cum(f, k)), rel=1e-12)
    # Seeded Monte Carlo: mean attempts from 0 failures lands on the expectation.
    rng = random.Random(44)
    runs, total = 4000, 0
    for _ in range(runs):
        n = 0
        while True:
            n += 1
            if rng.random() < float(_p(n - 1)):
                break
        total += n
    assert total / runs == pytest.approx(mounts.expected_attempts(0, PITY), rel=0.04)


# --- store: mounts -----------------------------------------------------------------

def test_empty_view(tmp_path):
    v = svc(tmp_path).view()
    assert v["mounts"] == [] and v["failures"] == 0
    assert [m["key"] for m in v["materials"]] == ["censer", "royal_fern_root",
                                                  "flower_of_oblivion", "mythical_feather"]
    assert all(m["have"] == 0 and not m["done"] for m in v["materials"])
    assert v["fern"] == {"have": 0, "need": 100, "left": 100, "per_day": None, "days": None}
    assert v["odds"]["next_pct"] == 3.0 and v["odds"]["guaranteed_in"] == 486
    assert v["t10"]["names"] == ["Arduanatt", "Dine", "Doom"]
    assert v["t10"]["ready"] == {"materials": False, "parents": False}
    assert v["data_error"] is None and v["source"] == T10


def test_add_edit_delete_mount(tmp_path):
    s = svc(tmp_path)
    v = s.add({"name": "Snow", "kind": "horse", "tier": 9, "level": 27, "gender": "female",
               "skills": ["Instant Accel", "Sprint", "Sprint"]})
    m = v["mounts"][0]
    assert m == {"id": "snow", "name": "Snow", "kind": "horse", "tier": 9, "level": 27,
                 "gender": "female", "skills": ["Instant Accel", "Sprint"]}
    v = s.add({"name": "Snow", "kind": "horse"})
    assert [x["id"] for x in v["mounts"]] == ["snow", "snow-2"]
    assert v["mounts"][1] == {"id": "snow-2", "name": "Snow", "kind": "horse", "tier": None,
                              "level": 1, "gender": None, "skills": []}
    v = s.edit({"id": "snow", "level": 30, "skills": ["Drift"]})
    assert v["mounts"][0]["level"] == 30 and v["mounts"][0]["skills"] == ["Drift"]
    assert v["mounts"][0]["tier"] == 9
    v = s.delete("snow-2")
    assert [x["id"] for x in v["mounts"]] == ["snow"]
    # persisted
    assert svc(tmp_path).view()["mounts"][0]["level"] == 30


@pytest.mark.parametrize("arg", [
    None, {}, {"kind": "horse"}, {"name": "", "kind": "horse"},
    {"name": "x" * 41, "kind": "horse"}, {"name": "A\x01", "kind": "horse"},
    {"name": "Caf" + chr(233), "kind": "horse"},
    {"name": "A", "kind": "dragon"}, {"name": "A", "kind": "horse", "tier": 11},
    {"name": "A", "kind": "horse", "tier": 0}, {"name": "A", "kind": "horse", "level": 31},
    {"name": "A", "kind": "camel", "level": 21}, {"name": "A", "kind": "horse", "level": 0},
    {"name": "A", "kind": "horse", "level": True}, {"name": "A", "kind": "horse", "gender": "m"},
    {"name": "A", "kind": "horse", "skills": "Sprint"},
    {"name": "A", "kind": "horse", "skills": [""]},
    {"name": "A", "kind": "horse", "skills": ["s" * 41]},
    {"name": "A", "kind": "horse", "skills": ["s%d" % i for i in range(41)]},
    {"name": "A", "kind": "horse", "colour": "grey"},
])
def test_add_rejects(tmp_path, arg):
    with pytest.raises(ValueError):
        svc(tmp_path).add(arg)


def test_edit_and_delete_reject(tmp_path):
    s = svc(tmp_path)
    s.add({"name": "Camel", "kind": "camel", "level": 20})
    for bad in [{"id": "camel"}, {"id": "nope", "level": 3}, {"id": "camel", "level": 21},
                {"id": "camel", "kind": "dragon"}, {"id": "Bad Id", "level": 3},
                {"id": "camel", "name": ""}]:
        with pytest.raises(ValueError):
            s.edit(bad)
    with pytest.raises(ValueError):
        s.delete("nope")
    with pytest.raises(ValueError):
        s.delete(3)
    # a kind change re-checks the level against the new kind's cap
    s.edit({"id": "camel", "kind": "horse", "level": 30})
    with pytest.raises(ValueError):
        s.edit({"id": "camel", "kind": "camel"})


def test_mount_cap(tmp_path):
    s = svc(tmp_path)
    for i in range(mounts.MAX_MOUNTS):
        s.add({"name": f"H{i}", "kind": "horse"})
    with pytest.raises(ValueError):
        s.add({"name": "One more", "kind": "horse"})


def test_corrupt_rows_are_dropped_not_fatal(tmp_path):
    st = Store(tmp_path / "store")
    st.put("mounts", {"mounts": [{"id": "ok", "name": "Ok", "kind": "horse", "tier": 9,
                                  "level": 30, "gender": "male", "skills": ["Drift", 3]},
                                 {"id": "bad", "name": "Bad", "kind": "dragon"},
                                 "junk", {"id": "ok", "name": "Dup", "kind": "horse"}],
                      "materials": {"royal_fern_root": 63, "censer": -4, "zzz": 9},
                      "fern_per_day": "fast", "failures": -2})
    v = mounts.MountsService(st).view()
    assert [m["id"] for m in v["mounts"]] == ["ok"] and v["mounts"][0]["skills"] == ["Drift"]
    assert v["fern"]["have"] == 63 and v["fern"]["per_day"] is None
    assert {m["key"]: m["have"] for m in v["materials"]}["censer"] == 0
    assert v["failures"] == 0


# --- materials, fern rate, failures ----------------------------------------------

def test_materials_and_fern_days(tmp_path):
    s = svc(tmp_path)
    v = s.materials({"royal_fern_root": 63, "mythical_feather": 10})
    assert v["fern"] == {"have": 63, "need": 100, "left": 37, "per_day": None, "days": None}
    v = s.fern_rate(4)
    assert v["fern"]["per_day"] == 4 and v["fern"]["days"] == 10  # ceil(37 / 4)
    v = s.fern_rate(2.5)
    assert v["fern"]["days"] == 15  # ceil(37 / 2.5) = ceil(14.8)
    v = s.fern_rate(None)
    assert v["fern"]["days"] is None
    v = s.materials({"royal_fern_root": 140})
    assert v["fern"]["left"] == 0 and v["fern"]["days"] == 0
    feather = next(m for m in v["materials"] if m["key"] == "mythical_feather")
    assert feather["done"] and feather["have"] == 10


@pytest.mark.parametrize("arg", [None, {}, {"royal_fern_root": -1}, {"royal_fern_root": 1.5},
                                 {"royal_fern_root": 100000}, {"gold": 3},
                                 {"censer": True}])
def test_materials_reject(tmp_path, arg):
    with pytest.raises(ValueError):
        svc(tmp_path).materials(arg)


@pytest.mark.parametrize("arg", [0, -1, 101, "4", True, float("nan"), float("inf")])
def test_fern_rate_reject(tmp_path, arg):
    with pytest.raises(ValueError):
        svc(tmp_path).fern_rate(arg)


def test_failures_drive_the_odds(tmp_path):
    s = svc(tmp_path)
    v = s.failures(10)
    o = v["odds"]
    assert v["failures"] == 10 and o["next_pct"] == 5.0
    assert o["by"] == {"50": mounts.attempts_for(0.5, 10, PITY),
                       "90": mounts.attempts_for(0.9, 10, PITY),
                       "99": mounts.attempts_for(0.99, 10, PITY)}
    assert o["guaranteed_in"] == 476
    assert o["expected"] == pytest.approx(mounts.expected_attempts(10, PITY), abs=0.01)
    for bad in [-1, 1001, 2.0, "3", None, True]:
        with pytest.raises(ValueError):
            s.failures(bad)


def test_t10_ready(tmp_path):
    s = svc(tmp_path)
    s.add({"name": "Stud", "kind": "horse", "tier": 9, "level": 30, "gender": "male"})
    v = s.add({"name": "Mare", "kind": "horse", "tier": 9, "level": 29, "gender": "female"})
    assert v["t10"]["ready"]["parents"] is False
    v = s.edit({"id": "mare", "level": 30})
    assert v["t10"]["ready"]["parents"] is True and v["t10"]["parents"] == ["stud", "mare"]
    v = s.materials({"censer": 1, "royal_fern_root": 100, "flower_of_oblivion": 100,
                     "mythical_feather": 9})
    assert v["t10"]["ready"]["materials"] is False
    v = s.materials({"mythical_feather": 10})
    assert v["t10"]["ready"] == {"materials": True, "parents": True}


# --- routes ------------------------------------------------------------------------

def _no_network(url, timeout):
    raise AssertionError("test touched the network")


@pytest.fixture()
def srv(tmp_path):
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          market_seed=[], profile_cfg={},
                          market_client=market.ArshaClient(fetch=_no_network,
                                                           cache_dir=tmp_path / "cache"))
    t = threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
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
    st, doc = _req(srv, "GET", "/api/mounts")
    assert st == 200 and doc["mounts"] == [] and doc["odds"]["next_pct"] == 3.0
    st, doc = _req(srv, "POST", "/api/mounts", {"add": {"name": "Doom hope", "kind": "horse",
                                                        "tier": 9, "level": 30}})
    assert st == 200 and doc["mounts"][0]["id"] == "doom-hope"
    st, doc = _req(srv, "POST", "/api/mounts", {"materials": {"royal_fern_root": 63}})
    assert st == 200 and doc["fern"]["have"] == 63
    st, doc = _req(srv, "POST", "/api/mounts", {"fern_rate": 3})
    assert st == 200 and doc["fern"]["days"] == 13
    st, doc = _req(srv, "POST", "/api/mounts", {"failures": 2})
    assert st == 200 and doc["odds"]["next_pct"] == 3.4
    st, doc = _req(srv, "POST", "/api/mounts", {"edit": {"id": "doom-hope", "gender": "male"}})
    assert st == 200 and doc["mounts"][0]["gender"] == "male"
    st, doc = _req(srv, "POST", "/api/mounts", {"delete": "doom-hope"})
    assert st == 200 and doc["mounts"] == []


@pytest.mark.parametrize("body", [
    {}, {"add": {}}, {"tick": "x"}, {"delete": "nope"},
    {"failures": 1, "fern_rate": 2},
])
def test_route_post_bad(srv, body):
    assert _req(srv, "POST", "/api/mounts", body)[0] == 400
