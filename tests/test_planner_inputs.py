"""Plan 081 acceptance: after one inventory screenshot, one CP readout, one
market sell dialog (committed OCR reads) and three logged-in play sessions,
Deadeye shopping, Inventory, Imperial and the net-proceeds fame show derived
values with source + age, and no typed value is left in /api/overrides.

A served app over tmp_path with an injected clock; the OCR reads and play
sessions are seeded in the store (the OCR pipeline itself is covered in
test_ocr_infer.py). No network, no real config, nothing reads the game.
"""

import datetime as dt
import http.client
import json
import threading

import pytest

from server.ew import app as ewapp
from server.ew import market

NOW = 1_800_000_000.0
DAY = 86400.0


class Clock:
    def __init__(self, t=NOW):
        self.t = t

    def __call__(self):
        return self.t


def _iso(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).replace(microsecond=0).isoformat()


def _no_network(url, timeout):
    raise AssertionError(f"network: {url}")


@pytest.fixture()
def env(tmp_path):
    clock = Clock()
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          sse_interval=0.05, market_seed=[],
                          market_client=market.ArshaClient(fetch=_no_network,
                                                           cache_dir=tmp_path / "cache"),
                          config_path=tmp_path / "local.json", profile_cfg={},
                          today_clock=clock, grind_clock=clock, deadeye_clock=clock,
                          leveling_clock=clock)
    s.market.settings = {"vp": False, "fame_pct": 0}  # never the host's config/local.json
    t = threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    t.start()
    yield s, clock
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
    return r.status, (json.loads(out) if out else None)


def _type_everything(s):
    for path, body in (("/api/deadeye", {"shop_set": {"silver_on_hand": 5, "hours_per_day": 9}}),
                       ("/api/inventory", {"set": {"base_lt": 900, "slots": 100,
                                                   "slots_used": 10}}),
                       ("/api/imperial", {"cp": 80}),
                       ("/api/settings", {"set": {"market.fame_pct": 0.5}})):
        st, doc = _req(s, "POST", path, body)
        assert st == 200, (path, doc)


def _seed_live(s, at):
    rid = iter(range(900, 1000))

    def read(kind, value):
        return {"id": f"u{next(rid)}", "at": _iso(at), "value": value,
                "source": f"ocr:{kind}.jpg"}
    s.store.put("ocr_reads", {"weight": [read("inv", {"used": 812.35, "max": 1560})],
                              "slots": [read("inv", {"used": 48, "total": 176})],
                              "cp": [read("cp", 312)], "fame": [read("sell", 1.25)]})
    s.store.put("silver", {"samples": [{"id": "u1", "at": _iso(at), "value": 42_000_000,
                                        "source": "ocr:silver.jpg"}]})
    hist = [{"id": f"p{i}", "start": _iso(at - i * DAY - 3 * 3600),
             "end": _iso(at - i * DAY - 3 * 3600 + h * 3600)}
            for i, h in ((1, 2), (2, 3), (3, 4))]
    hist[-1]["end"] = _iso(at - 60)  # newest session closed after the typing
    hist[-1]["start"] = _iso(at - 60 - 4 * 3600)
    s.store.put("playsession", {"open": None, "last": None, "suppressed": False,
                                "next_id": 4, "history": hist})


def _typed_keys(s):
    _, doc = _req(s, "GET", "/api/overrides")
    return sorted(i["key"] for i in doc["items"])


def test_acceptance_derived_inputs_replace_typed_ones(env):
    s, clock = env
    clock.t = NOW - 2 * DAY
    _type_everything(s)
    assert _typed_keys(s) == ["imperial.cp", "inventory.base_lt", "inventory.slots",
                              "inventory.slots_used", "market.fame_pct",
                              "shopping.hours_per_day", "shopping.silver_on_hand"]
    clock.t = NOW
    _seed_live(s, NOW - 600)
    _, shop = _req(s, "GET", "/api/deadeye/shopping")
    assert shop["settings"]["silver_on_hand"] == 42_000_000
    assert shop["inputs"]["silver_on_hand"]["source"] == "ocr:silver.jpg"
    assert shop["inputs"]["silver_on_hand"]["age_s"] == 600
    assert shop["inputs"]["hours_per_day"]["source"] == "sessions:3"
    assert shop["settings"]["hours_per_day"] == 3
    _, inv = _req(s, "GET", "/api/inventory")
    assert inv["inputs"]["base_lt"]["source"] == "ocr:inv.jpg" and inv["lt_total"] == 1560
    assert inv["slots"]["total"] == 176 and inv["slots"]["used"] == 48
    assert inv["fame"]["value"] == 1.25 and inv["fame"]["source"] == "ocr"
    assert inv["fame"]["age_s"] == 600
    _, imp = _req(s, "GET", "/api/imperial")
    assert imp["cp"]["value"] == 312 and imp["cp"]["source"] == "ocr"
    assert s.tax()["fame_pct"] == 1.25   # every net-proceeds reader
    assert _typed_keys(s) == []           # no typed value left
    _, hist = _req(s, "GET", "/api/overrides")
    assert any(h["key"] == "market.fame_pct" and h["retired_by"] == "ocr_fame"
               for h in hist["history"])


def test_fame_typed_after_the_read_wins(env):
    s, clock = env
    _seed_live(s, NOW - 600)
    assert s.tax()["fame_pct"] == 1.25
    _req(s, "POST", "/api/settings", {"set": {"market.fame_pct": 0.5}})
    assert s.tax()["fame_pct"] == 0.5
    _, inv = _req(s, "GET", "/api/inventory")
    assert inv["fame"]["source"] == "typed"


def test_typed_hot_window_ends_at_the_next_maintenance(env):
    s, clock = env
    st, doc = _req(s, "POST", "/api/leveling", {"hot_add": {
        "days": [0], "start": "11:00", "end": "13:00", "label": "Hot", "pct": 50}})
    assert st == 200
    until = dt.datetime.fromisoformat(doc["hot_windows"][0]["until"]).timestamp()
    assert NOW < until <= NOW + 8 * DAY
