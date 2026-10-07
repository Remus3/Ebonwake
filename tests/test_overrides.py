"""Plan 079: override ledger - source, set-at, expiry, superseded by live signals.

Pure-doc tests plus the store-backed ledger over tmp_path; nothing reads the
game, the network or a real config file.
"""

import json

from server.ew import overrides as ov
from server.ew.store import Store

NOW = 1_800_000_000.0
DAY = 86400.0
POLICY = ov.clean_policy({"keys": {
    "market.vp": {"label": "VP", "expiry": "days:30", "cards": ["grind", "crafting"]},
    "market.fame_pct": {"label": "Fame", "expiry": "none", "cards": ["grind"]},
    "events.maintenance_start_utc": {"label": "Maint", "expiry": "until:maint_end",
                                     "cards": ["events"]},
    "overlay.mode.*": {"label": "Mode", "expiry": "days:7", "cards": ["overlay"]},
    "bdo.install_dir": {"label": "Install", "expiry": "until:path_missing", "cards": []},
}})


def _ledger(tmp_path, **kw):
    return ov.OverrideLedger(Store(tmp_path / "store"), clock=lambda: NOW, policy=POLICY, **kw)


# --- policy -----------------------------------------------------------------------

def test_tracked_policy_is_ascii_and_every_row_valid():
    raw = json.loads(ov.POLICY_FILE.read_bytes().decode("ascii"))
    pol = ov.load_policy()
    assert set(pol) == set(raw["keys"])  # nothing dropped by clean_policy
    for key, row in pol.items():
        assert ov.RULE_RE.fullmatch(row["expiry"])
        if row["expiry"] == "none":
            assert key in ov.NONE_OK
    assert pol["market.vp"]["expiry"] == "days:30"
    assert {"grind", "crafting", "inventory"} <= set(pol["market.vp"]["cards"])
    assert pol["events.maintenance_start_utc"]["expiry"] == "until:maint_end"
    assert ov.policy_for(pol, "overlay.mode.whatNow")["expiry"] == "days:7"


def test_portrait_pick_allowlisted_none():
    # Plan 083: a gallery pick is cosmetic (changes no number): rule none, no expiry.
    pol = ov.load_policy()
    assert "portrait.*" in ov.NONE_OK
    row = ov.policy_for(pol, "portrait.Deadeye")
    assert row["expiry"] == "none" and row["cards"] == ["portraits"]
    assert ov.expiry_at(row["expiry"], NOW) is None
    pol2 = ov.clean_policy({"keys": {"portrait.*": {"expiry": "none"}}})
    assert pol2["portrait.*"]["expiry"] == "none"


def test_none_outside_allowlist_and_bad_rules_are_dropped():
    pol = ov.clean_policy({"keys": {"market.vp": {"expiry": "none"},
                                    "x.y": {"expiry": "days:0"},
                                    "a.b": {"expiry": "weeks:2"},
                                    "market.fame_pct": {"expiry": "none"}}})
    assert set(pol) == {"market.fame_pct"}


# --- live beats override -------------------------------------------------------------

def test_live_value_beats_override_and_retires_it():
    doc = ov.empty()
    ov.put(doc, "market.vp", True, "typed", NOW - DAY, "days:30")
    out = ov.resolve(doc, "market.vp", None, False, NOW, "days:30")
    assert out["from"] == "override" and out["value"] is True
    out = ov.resolve(doc, "market.vp", {"value": True, "signal": "vp_timer"}, False, NOW,
                     "days:30")
    assert out == {"value": True, "from": "live", "entry": None}
    assert "market.vp" not in doc["live"]
    assert doc["retired"][-1]["retired_by"] == "vp_timer"
    assert doc["retired"][-1]["retired_at"] == ov._iso(NOW)
    # once the timer lapses there is no override left: back to the default
    assert ov.resolve(doc, "market.vp", None, False, NOW + 1, "days:30")["from"] == "default"


def test_no_live_no_override_is_default():
    out = ov.resolve(ov.empty(), "market.vp", None, False, NOW, "days:30")
    assert out == {"value": False, "from": "default", "entry": None}


# --- expiry ------------------------------------------------------------------------

def test_days_rule_expires_and_retires_expired():
    doc = ov.empty()
    e = ov.put(doc, "market.vp", True, "typed", NOW, "days:30")
    assert e["expires_at"] == ov._iso(NOW + 30 * DAY)
    assert ov.resolve(doc, "market.vp", None, False, NOW + 30 * DAY - 1, "days:30")["value"]
    out = ov.resolve(doc, "market.vp", None, False, NOW + 30 * DAY, "days:30")
    assert out["from"] == "default" and out["value"] is False
    assert doc["retired"][-1]["retired_by"] == "expired"


def test_until_maint_end_uses_the_next_window_end():
    doc = ov.empty()
    end = NOW + 3 * DAY
    e = ov.put(doc, "events.maintenance_start_utc", "08:00", "typed", NOW, "until:maint_end",
               maint_end=lambda at: end)
    assert e["expires_at"] == ov._iso(end)
    rule = "until:maint_end"
    assert ov.resolve(doc, "events.maintenance_start_utc", None, "", end - 1, rule)["value"] \
        == "08:00"
    out = ov.resolve(doc, "events.maintenance_start_utc", None, "", end, rule)
    assert out["value"] == "" and doc["retired"][-1]["retired_by"] == "expired"


def test_until_maint_end_without_a_window_falls_back_to_a_week():
    e = ov.put(ov.empty(), "events.maintenance_start_utc", "08:00", "typed", NOW,
               "until:maint_end", maint_end=lambda at: None)
    assert e["expires_at"] == ov._iso(NOW + ov.MAINT_FALLBACK_S)


def test_none_never_expires():
    doc = ov.empty()
    e = ov.put(doc, "market.fame_pct", 1.5, "typed", NOW, "none")
    assert e["expires_at"] is None
    out = ov.resolve(doc, "market.fame_pct", None, 0, NOW + 10_000 * DAY, "none")
    assert out["from"] == "override" and out["value"] == 1.5


def test_path_missing_retires(tmp_path):
    doc = ov.empty()
    ov.put(doc, "bdo.install_dir", str(tmp_path), "typed", NOW, "until:path_missing")
    rule = "until:path_missing"
    assert ov.resolve(doc, "bdo.install_dir", None, "", NOW, rule)["from"] == "override"
    out = ov.resolve(doc, "bdo.install_dir", None, "", NOW, rule, path_exists=lambda p: False)
    assert out["from"] == "default"
    assert doc["retired"][-1]["retired_by"] == "path_missing"


# --- one live entry per key ----------------------------------------------------------

def test_at_most_one_live_entry_per_key():
    doc = ov.empty()
    ov.put(doc, "market.vp", True, "typed", NOW, "days:30")
    ov.put(doc, "market.vp", True, "typed", NOW + 5, "days:30")
    assert list(doc["live"]) == ["market.vp"]
    assert doc["live"]["market.vp"]["set_at"] == ov._iso(NOW + 5)
    assert [r["retired_by"] for r in doc["retired"]] == ["replaced"]


def test_history_keeps_the_last_fifty():
    doc = ov.empty()
    for i in range(ov.MAX_RETIRED + 7):
        ov.put(doc, "market.vp", True, "typed", NOW + i, "days:30")
    assert len(doc["retired"]) == ov.MAX_RETIRED


def test_clean_drops_junk_and_retired_live_rows():
    doc = ov.clean({"live": {"market.vp": {"key": "other", "source": "typed",
                                           "set_at": ov._iso(NOW)},
                             "market.fame_pct": "junk"},
                    "retired": [1, {"key": "x"}], "seen": []})
    assert doc == ov.empty()


# --- config import -------------------------------------------------------------------

def test_config_import_is_idempotent():
    doc = ov.empty()
    vals, dflt = {"market.vp": True, "market.fame_pct": 0}, {"market.vp": False,
                                                             "market.fame_pct": 0}
    mtime = NOW - 2 * DAY
    ov.from_config(doc, vals, dflt, mtime, list(vals), POLICY, now=NOW)
    first = json.dumps(doc, sort_keys=True)
    assert doc["live"]["market.vp"]["source"] == "config"
    assert doc["live"]["market.vp"]["set_at"] == ov._iso(mtime)
    assert "market.fame_pct" not in doc["live"]  # equals its default
    ov.from_config(doc, vals, dflt, NOW - DAY, list(vals), POLICY, now=NOW + 60)  # mtime moved
    assert json.dumps(doc, sort_keys=True) == first


def test_config_value_once_expired_is_not_reimported():
    doc = ov.empty()
    vals, dflt = {"market.vp": True}, {"market.vp": False}
    ov.from_config(doc, vals, dflt, NOW, ["market.vp"], POLICY)
    ov.resolve(doc, "market.vp", None, False, NOW + 31 * DAY, "days:30")
    ov.from_config(doc, vals, dflt, NOW + 31 * DAY, ["market.vp"], POLICY)
    assert "market.vp" not in doc["live"]
    # back to the default then on again: a new entry
    ov.from_config(doc, {"market.vp": False}, dflt, NOW + 32 * DAY, ["market.vp"], POLICY)
    ov.from_config(doc, vals, dflt, NOW + 33 * DAY, ["market.vp"], POLICY)
    assert doc["live"]["market.vp"]["set_at"] == ov._iso(NOW + 33 * DAY)


def test_config_back_to_default_retires_a_config_entry():
    doc = ov.empty()
    ov.from_config(doc, {"market.vp": True}, {"market.vp": False}, NOW, ["market.vp"], POLICY)
    ov.from_config(doc, {"market.vp": False}, {"market.vp": False}, NOW + 1, ["market.vp"],
                   POLICY)
    assert doc["live"] == {} and doc["retired"][-1]["retired_by"] == "config"


def test_typed_write_is_not_reimported_as_config():
    doc = ov.empty()
    ov.put(doc, "market.vp", True, "typed", NOW, "days:30")
    ov.from_config(doc, {"market.vp": True}, {"market.vp": False}, NOW + 1, ["market.vp"],
                   POLICY)
    assert doc["live"]["market.vp"]["source"] == "typed"
    assert doc["retired"] == []


# --- store-backed ledger -------------------------------------------------------------

def test_ledger_round_trip_and_items(tmp_path):
    led = _ledger(tmp_path)
    led.set("market.vp", True)
    led.set("overlay.mode.whatNow", "pin")
    items = {i["key"]: i for i in led.items()}
    assert items["market.vp"]["label"] == "VP"
    assert items["market.vp"]["cards"] == ["grind", "crafting"]
    assert items["market.vp"]["expires_in_s"] == int(30 * DAY)
    assert items["market.vp"]["source"] == "typed"
    assert items["overlay.mode.whatNow"]["label"] == "Mode: whatNow"
    assert items["overlay.mode.whatNow"]["expires_in_s"] == int(7 * DAY)
    led.clear("market.vp")
    assert [i["key"] for i in led.items()] == ["overlay.mode.whatNow"]
    assert led.history()[0]["retired_by"] == "operator"


def test_ledger_effective_writes_retirement(tmp_path):
    led = _ledger(tmp_path)
    led.set("market.vp", True, now=NOW - 31 * DAY)
    out = led.effective("market.vp", None, False)
    assert out["value"] is False and out["from"] == "default"
    assert led.history()[0]["retired_by"] == "expired"
    assert led.items() == []


def test_ledger_rejects_unknown_keys(tmp_path):
    led = _ledger(tmp_path)
    for fn in (lambda: led.set("ui.theme", "dark"),
               lambda: led.effective("ui.theme", None, "dark")):
        try:
            fn()
        except ValueError:
            continue
        raise AssertionError("expected ValueError")


def test_ledger_sweep_retires_expired_without_live(tmp_path):
    led = _ledger(tmp_path)
    led.set("overlay.mode.dice", "block", now=NOW - 8 * DAY)
    led.sweep()
    assert led.items() == [] and led.history()[0]["retired_by"] == "expired"


def test_ledger_read_without_change_never_writes(tmp_path):
    led = _ledger(tmp_path)
    led.effective("market.vp", None, False)
    led.items()
    assert not (tmp_path / "store" / "overrides.json").exists()
