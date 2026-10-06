"""Plan 042: Life & CP card - life skill ranks, energy and contribution points
from the plan 041 profile snapshot, with the next sourced CP milestone.

No network: profiles come from recorded fixtures through an injected fake
fetch; the family name is a fake, never one from config/local.json.
"""

import http.client
import json
import threading
from pathlib import Path

import pytest

from server.ew import app as ewapp
from server.ew import market, progress
from server.ew.store import Store

FAMILY = "Testfam"
LOCAL = progress.SELF_HOST_EXAMPLE  # plan 061: loopback base, robots gate skipped
FIX =Path(__file__).parent / "fixtures" / "profile"
DATA = Path(progress.__file__).resolve().parent / "data" / "cp_milestones.json"


def _fixture(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


PUBLIC = _fixture("adventurer_search.json")
PRIVATE = _fixture("lifeskill_private.json")
PARTIAL = _fixture("lifeskill_partial.json")


class Clock:
    def __init__(self, t=2_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class FakeFetch:
    def __init__(self, body):
        self.body = body
        self.calls = []

    def __call__(self, url, timeout):
        self.calls.append(url)
        return json.dumps(self.body).encode()


def _sync(fn):
    fn()


def _no_network(url, timeout):
    raise AssertionError("test touched the network")


def _main(hit):
    return next(s for s in progress.profile_snapshots(hit[0]) if s["main"])


# --- data file ----------------------------------------------------------------

def test_data_file_schema():
    raw = DATA.read_bytes()
    raw.decode("ascii")
    assert b"\r" not in raw
    doc = progress.load_cp_milestones()
    assert doc["source"] == "https://www.blackdesertfoundry.com/contribution-points-guide/"
    assert doc["verified"] == "2026-07-01"
    cps = [m["cp"] for m in doc["milestones"]]
    assert cps == sorted(set(cps)) and 220 in cps and 250 in cps
    for m in doc["milestones"]:
        assert m["label"] and (m["verified"] is False or m["verified"] == "2026-07-01")
    assert next(m for m in doc["milestones"] if m["cp"] == 220)["verified"] == "2026-07-01"


@pytest.mark.parametrize("mutate", [
    lambda d: d.pop("milestones"),
    lambda d: d.update(milestones=[]),
    lambda d: d.update(source="http://insecure"),
    lambda d: d.update(verified="yesterday"),
    lambda d: d["milestones"].append({"cp": 220, "label": "dup", "verified": False}),
    lambda d: d["milestones"].reverse(),
    lambda d: d["milestones"][0].update(cp="220"),
    lambda d: d["milestones"][0].update(cp=0),
    lambda d: d["milestones"][0].update(label=""),
    lambda d: d["milestones"][0].update(extra=1),
    lambda d: d.update(extra=1),
])
def test_data_file_validation_rejects(tmp_path, mutate):
    doc = json.loads(DATA.read_text(encoding="ascii"))
    mutate(doc)
    p = tmp_path / "cp.json"
    p.write_text(json.dumps(doc), encoding="ascii")
    with pytest.raises(ValueError):
        progress.load_cp_milestones(p)


# --- card builder ---------------------------------------------------------------

MS = {"source": "https://example.invalid/", "verified": "2026-07-01",
      "milestones": [{"cp": 220, "label": "weekly", "verified": "2026-07-01"},
                     {"cp": 250, "label": "workers", "verified": "2026-07-01"},
                     {"cp": 350, "label": "band top", "verified": False}]}


def test_card_public_fixture():
    card = progress.lifeskill_card(_main(PUBLIC), MS)
    assert card["status"] == "ok" and card["character"] == "Shooty"
    assert card["energy"] == 401
    assert card["skills"] == [{"key": "gathering", "name": "Gathering", "rank": "Artisan 2"},
                              {"key": "fishing", "name": "Fishing", "rank": "Skilled 9"},
                              {"key": "trading", "name": "Trading", "rank": "Beginner 1"}]
    cp = card["cp"]
    assert cp["value"] == 312 and cp["gap"] == 38
    assert cp["next"] == {"cp": 350, "label": "band top", "verified": False}
    assert cp["reached"] == [{"cp": 220, "label": "weekly", "verified": "2026-07-01"},
                             {"cp": 250, "label": "workers", "verified": "2026-07-01"}]
    assert card["source"] == MS["source"] and card["verified"] == "2026-07-01"
    assert card["error"] is None


def test_card_private_fixture_shows_hidden():
    card = progress.lifeskill_card(_main(PRIVATE), MS)
    assert card["status"] == "ok"
    assert card["skills"] == "hidden" and card["energy"] == "hidden" and card["cp"] == "hidden"


def test_card_partial_fixture():
    card = progress.lifeskill_card(_main(PARTIAL), MS)
    assert card["energy"] == "hidden"
    # in-game order (cooking before trading before sailing), rank text as given
    assert [(s["name"], s["rank"]) for s in card["skills"]] == [
        ("Cooking", "Master 12"), ("Trading", "Beginner 1"), ("Sailing", "Apprentice 4")]
    assert card["cp"]["value"] == 180 and card["cp"]["gap"] == 40
    assert card["cp"]["next"]["cp"] == 220 and card["cp"]["reached"] == []


def test_card_unknown_skill_after_known_and_numeric_rank():
    snap = {"name": "Shooty", "main": True,
            "spec_levels": {"weaving": "Guru 1", "alchemy": 31, "fishing": "Skilled 2"}}
    card = progress.lifeskill_card(snap, MS)
    assert [(s["key"], s["rank"]) for s in card["skills"]] == [
        ("fishing", "Skilled 2"), ("alchemy", "31"), ("weaving", "Guru 1")]


def test_card_cp_milestone_edges():
    def cp(v):
        return progress.lifeskill_card({"name": "S", "main": True, "contribution": v}, MS)["cp"]

    assert cp(220)["next"]["cp"] == 250 and cp(220)["gap"] == 30
    assert [m["cp"] for m in cp(220)["reached"]] == [220]
    assert cp(0)["next"]["cp"] == 220 and cp(0)["gap"] == 220
    top = cp(400)
    assert top["next"] is None and top["gap"] is None and len(top["reached"]) == 3


def test_card_none_without_snapshot():
    card = progress.lifeskill_card(None, MS)
    assert card["status"] == "none" and card["character"] is None
    assert card["skills"] is None and card["energy"] is None and card["cp"] is None


def test_card_without_milestones_keeps_value():
    card = progress.lifeskill_card({"name": "S", "main": True, "contribution": 300},
                                   {"error": "broken", "milestones": []})
    assert card["cp"] == {"value": 300, "next": None, "gap": None, "reached": []}
    assert card["error"] == "broken"


def test_card_ignores_junk_snapshot_values():
    card = progress.lifeskill_card({"name": "S", "main": True, "energy": "lots",
                                    "contribution": -5, "spec_levels": "x"}, MS)
    assert card["energy"] == "hidden" and card["cp"] == "hidden" and card["skills"] == "hidden"


# --- service + route ----------------------------------------------------------------

def _svc(tmp_path, body):
    clk = Clock()
    pc = progress.ProfileClient(FAMILY, base_url=LOCAL, fetch=FakeFetch(body), clock=clk,
                                cache_dir=tmp_path / "pcache")
    hist = progress.ProfileHistory(tmp_path / "rt" / "profile_history.jsonl", clock=clk)
    s = progress.ProgressService(Store(tmp_path / "store"), pc, clock=clk, spawn=_sync,
                                 history=hist)
    return s, pc, clk


def test_view_lifeskill_from_newest_refresh(tmp_path):
    s, pc, clk = _svc(tmp_path, PUBLIC)
    v = s.view()
    assert v["lifeskill"]["status"] == "ok" and v["lifeskill"]["energy"] == 401
    assert v["lifeskill"]["cp"]["value"] == 312
    assert v["lifeskill"]["at"] == s.history.rows()[0]["at"]
    clk.t += 3601
    pc.fetch.body = PRIVATE
    v = s.view()
    assert v["lifeskill"]["energy"] == "hidden" and v["lifeskill"]["skills"] == "hidden"
    assert len(pc.fetch.calls) == 2  # the card never adds a request


def test_view_lifeskill_none_without_history(tmp_path):
    v = progress.ProgressService(Store(tmp_path / "store"), None).view()
    assert v["lifeskill"]["status"] == "none"


def test_broken_milestones_never_break_the_tab(tmp_path, monkeypatch):
    p = tmp_path / "cp.json"
    p.write_text("{", encoding="ascii")
    monkeypatch.setattr(progress, "CP_MILESTONES", p)
    s, *_ = _svc(tmp_path, PUBLIC)
    card = s.view()["lifeskill"]
    assert card["error"] and card["cp"]["value"] == 312 and card["cp"]["next"] is None


@pytest.fixture()
def srv(tmp_path):
    pc = progress.ProfileClient(FAMILY, base_url=LOCAL, fetch=FakeFetch(PARTIAL), clock=Clock(),
                                cache_dir=tmp_path / "pcache")
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          sse_interval=0.05, market_seed=[],
                          market_client=market.ArshaClient(fetch=_no_network,
                                                           cache_dir=tmp_path / "cache"),
                          profile_client=pc)
    s.progress.spawn = _sync
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield s
    s.shutdown()
    s.server_close()


def test_route_progress_has_lifeskill(srv):
    c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
    c.request("GET", "/api/progress")
    r = c.getresponse()
    doc = json.loads(r.read())
    c.close()
    assert r.status == 200
    ls = doc["lifeskill"]
    assert ls["status"] == "ok" and ls["energy"] == "hidden"
    assert ls["cp"]["value"] == 180 and ls["cp"]["next"]["cp"] == 220
    assert "OPAQUE" not in json.dumps(doc)
