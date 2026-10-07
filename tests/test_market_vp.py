"""Plan 079: market.vp goes through the override ledger - Grind, Crafting,
Inventory and the market watch all read the same effective VP.

A served app over tmp_path with an injected clock; no network, no real config.
"""

import datetime as dt
import http.client
import json
import os
import threading

import pytest

from server.ew import app as ewapp
from server.ew import market

NOW = 1_800_000_000.0
DAY = 86400.0
VP = {"vp": True, "fame_pct": 0}
NO_VP = {"vp": False, "fame_pct": 0}


class Clock:
    def __init__(self, t=NOW):
        self.t = t

    def __call__(self):
        return self.t


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
                          today_clock=clock, grind_clock=clock)
    s.market.settings = dict(NO_VP)  # never the host's config/local.json
    t = threading.Thread(target=s.serve_forever, daemon=True)
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


def _readers(s):
    """(grind loot tax, crafting tax, inventory vp active, market tax vp)."""
    st, g = _req(s, "POST", "/api/grind", {"add_spot": "Test Spot"}) if not s.grind.view()[
        "spots"] else (200, s.grind.view())
    spot = g["spots"][0]["id"]
    st1, loot = _req(s, "GET", f"/api/grind/loot?spot={spot}")
    st2, craft = _req(s, "GET", "/api/crafting")
    st3, inv = _req(s, "GET", "/api/inventory")
    assert (st, st1, st2, st3) == (200, 200, 200, 200)
    return (loot["tax"]["vp"], craft["tax"]["vp"], inv["vp"]["active"],
            s.market.tax()["vp"])


def test_no_config_no_vp_and_no_override(env):
    s, _ = env
    assert _readers(s) == (False, False, False, False)
    st, doc = _req(s, "GET", "/api/overrides")
    assert st == 200 and doc["count"] == 0 and doc["items"] == []


def test_vp_timer_armed_nets_with_vp_everywhere(env):
    s, _ = env
    assert _req(s, "POST", "/api/grind", {"buff": {"name": "Value Pack", "minutes": 600}})[0] \
        == 200
    assert _readers(s) == (True, True, True, True)
    _, inv = _req(s, "GET", "/api/inventory")
    assert inv["vp"]["from"] == "buff"
    assert _req(s, "GET", "/api/overrides")[1]["count"] == 0  # live, not an override


def test_typed_vp_badges_then_expires_after_30_days(env):
    s, clock = env
    st, out = _req(s, "POST", "/api/settings", {"set": {"market.vp": True}})
    assert st == 200
    keys = {i["key"]: i for i in out["overrides"]["items"]}
    assert keys["market.vp"]["source"] == "typed"
    assert {"grind", "crafting", "inventory"} <= set(keys["market.vp"]["cards"])
    assert keys["market.vp"]["expires_in_s"] == int(30 * DAY)
    assert _readers(s) == (True, True, True, True)
    _, sig = _req(s, "GET", "/api/signals")
    assert [i["key"] for i in sig["overrides"]["items"]] == ["market.vp"]
    clock.t += 31 * DAY
    assert _readers(s) == (False, False, False, False)
    _, doc = _req(s, "GET", "/api/overrides")
    assert doc["count"] == 0
    assert doc["history"][0]["key"] == "market.vp"
    assert doc["history"][0]["retired_by"] == "expired"


def test_vp_timer_supersedes_a_typed_override(env):
    s, _ = env
    _req(s, "POST", "/api/settings", {"set": {"market.vp": True}})
    _req(s, "POST", "/api/grind", {"buff": {"name": "Value Pack", "minutes": 60}})
    assert _readers(s) == (True, True, True, True)
    _, doc = _req(s, "GET", "/api/overrides")
    assert doc["count"] == 0 and doc["history"][0]["retired_by"] == "vp_timer"


def test_clear_retires_and_resets_the_setting(env):
    s, _ = env
    _req(s, "POST", "/api/settings", {"set": {"market.vp": True}})
    st, out = _req(s, "POST", "/api/settings", {"clear": "market.vp"})
    assert st == 200 and out["settings"]["market.vp"] is False
    assert out["overrides"]["count"] == 0
    assert out["overrides"]["history"][0]["retired_by"] == "operator"
    assert _readers(s) == (False, False, False, False)
    assert _req(s, "POST", "/api/settings", {"clear": "ui.theme"})[0] == 400
    assert _req(s, "POST", "/api/settings", {"clear": 5})[0] == 400


def test_config_value_imports_as_a_config_entry(env, tmp_path):
    s, _ = env
    cfg = tmp_path / "local.json"
    cfg.write_text('{"overlay": {"mode": {"dice": "pin"}}}\n', encoding="ascii")
    os.utime(cfg, (NOW - DAY, NOW - DAY))  # set_at = file mtime when first seen
    _, doc = _req(s, "GET", "/api/overrides")
    items = {i["key"]: i for i in doc["items"]}
    assert items["overlay.mode.dice"]["source"] == "config"
    assert items["overlay.mode.dice"]["value"] == "pin"
    assert items["overlay.mode.dice"]["expires_in_s"] == int(6 * DAY)
    assert s.eff_settings()["overlay.mode.dice"] == "pin"
    assert _req(s, "GET", "/api/overrides")[1] == doc  # idempotent re-read


def test_stale_config_value_expires_from_its_file_mtime(env, tmp_path):
    s, _ = env
    cfg = tmp_path / "local.json"
    cfg.write_text('{"overlay": {"mode": {"dice": "block"}}}\n', encoding="ascii")
    os.utime(cfg, (NOW - 8 * DAY, NOW - 8 * DAY))  # forgotten a week ago
    assert s.eff_settings()["overlay.mode.dice"] == "auto"
    _, doc = _req(s, "GET", "/api/overrides")
    assert doc["count"] == 0 and doc["history"][0]["retired_by"] == "expired"


def test_maintenance_override_retires_on_a_notice(env):
    s, clock = env
    # Plan 080: not settable any more - a config value is the ledger's source.
    st, _ = _req(s, "POST", "/api/settings", {"set": {"events.maintenance_start_utc": "09:00"}})
    assert st == 400
    s.settings.path.write_text('{"events": {"maintenance_start_utc": "09:00"}}\n',
                               encoding="ascii")
    os.utime(s.settings.path, (clock.t, clock.t))
    assert s._maint_start() == "09:00"
    now = dt.datetime.fromtimestamp(clock.t, dt.timezone.utc)
    day = (now + dt.timedelta(days=2)).date().isoformat()
    s.store.put("maint_notices", {"notices": [{
        "date": day, "start_utc": f"{day}T06:00:00+00:00", "end_utc": f"{day}T10:00:00+00:00",
        "source": "notice"}]})
    assert s._maint_start() == ""
    _, doc = _req(s, "GET", "/api/overrides")
    assert doc["history"][0]["retired_by"] == "maint_notice"


def test_typed_gear_and_watch_thresholds_are_listed_not_clearable(env):
    s, _ = env
    s.market.watchlist.add({"id": 5, "sid": 0, "below": 100})
    _, doc = _req(s, "GET", "/api/overrides")
    w = [i for i in doc["items"] if i["key"] == "market.watch.5.0"]
    assert w and w[0]["clearable"] is False and w[0]["cards"] == ["market"]
    assert w[0]["value"] == {"below": 100, "above": None}
