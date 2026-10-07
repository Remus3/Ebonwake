"""Plan 030: allowlisted settings over config/local.json."""

import json
import threading
from pathlib import Path

import pytest

from server.ew import app as ewapp
from server.ew import market, settings
from tests.test_server import _get, _no_network, _post

EXAMPLE = Path(__file__).resolve().parents[1] / "config" / "local.example.json"

SAMPLE = (
    '{\n'
    '  "_doc": "keep me",\n'
    '  "secrets": {\n'
    '    "ocr":   {"env": "EW_OCR_API_KEY"},\n'
    '    "other": {"env":"X_KEY"}\n'
    '  },\n'
    '  "overlay": {\n'
    '    "_doc": "plan 022",\n'
    '    "anchor": "ml",\n'
    '    "scale": 1.0,\n'
    '    "widgets": {"grindSession": true, "leveling": false}\n'
    '  },\n'
    '  "loop": {"max_notes_per_day": 6,   "max_new_plans_per_day": 2},\n'
    '  "zzz_unknown": [1, 2, {"a": null}]\n'
    '}\n'
)


def _block(text, key):
    spans, _ = settings._scan(text)
    s, e = spans[(key,)]
    return text[s:e]


@pytest.fixture()
def cfg(tmp_path):
    p = tmp_path / "config" / "local.json"
    p.parent.mkdir()
    p.write_bytes(SAMPLE.encode())
    return p


# ---- allowlist + validators ----

def test_get_has_every_allowlisted_key_with_defaults(tmp_path):
    v = settings.Settings(tmp_path / "missing.json").view()
    assert v["settings"] == settings.defaults() and v["error"] is None
    keys = set(v["settings"])
    assert "overlay.mode.leveling" in keys and "notify.mute_until" in keys
    assert "market.fame_pct" in keys and "bdo.install_dir" in keys
    assert not any(k.startswith(("secrets", "loop")) for k in keys)
    assert "env" not in json.dumps(v)


def test_get_reads_values_and_drops_invalid(cfg):
    v = settings.Settings(cfg).view()["settings"]
    assert v["overlay.anchor"] == "ml" and "overlay.widgets.leveling" not in v
    cfg.write_text('{"overlay": {"scale": 9, "anchor": {"x": 1, "y": 2}}, "ui": {"theme": "pink"}}')
    v = settings.Settings(cfg).view()["settings"]
    assert v["overlay.scale"] == 1.0 and v["overlay.anchor"] == {"x": 1, "y": 2}
    assert v["ui.theme"] == "system"  # plan 080: the default follows the OS theme


@pytest.mark.parametrize("key,good,bad", [
    ("overlay.mode.season", ["auto", "pin", "block"], [True, "always", None]),
    ("overlay.anchor", ["tl", "mr", {"x": 0, "y": -5}],
     ["xx", {"x": 1}, {"x": 1, "y": 2, "z": 3}, {"x": 1.5, "y": 2}, {"env": "K"}, 3]),
    ("overlay.display", [None, 0, 2], [-1, 1.5, True, "0", 99]),
    ("overlay.scale", [0.8, 1, 1.6], [0.79, 1.61, True, "1", float("nan")]),
    ("overlay.opacity", [0.5, 0.95], [0.49, 0.96, None]),
    ("hotkeys.toggleOverlay", ["Control+Alt+E", "Shift+F12", "CommandOrControl+Shift+9"],
     ["E", "Control+", "Control+Alt+e", "Hyper+E", "Control+Alt+F25", "", 5]),
    ("profile.family", ["", "Some_Name", "ab"], ["a", "x" * 17, "bad name", "a-b", None]),
    ("ui.theme", ["system", "dark", "light"], ["Dark", "", None]),
    ("ui.scale", [0.9, 1.3, 1.1], [0.89, 1.31, False]),
    ("market.vp", [False], ["false"]),
    ("market.fame_pct", [0, 1.5, 0.75], [-0.1, 1.6, True]),
    ("notify.mute_until", ["", "2000-01-01T00:00:00+00:00"],
     ["2000-01-01T00:00:00", "soon", "9999-01-01T00:00:00+00:00", None, 0]),
])
def test_each_validator(key, good, bad):
    for v in good:
        assert settings.validate({"set": {key: v}}) == {key: v}, (key, v)
    for v in bad:
        with pytest.raises(ValueError):
            settings.validate({"set": {key: v}})


@pytest.mark.parametrize("body", [
    {}, {"set": {}}, {"set": []}, {"set": {"x": 1}, "y": 2}, [],
    {"set": {"secrets.ocr": {"env": "K"}}}, {"set": {"secrets": {}}},
    {"set": {"loop.max_notes_per_day": 99}}, {"set": {"overlay": {"scale": 1}}},
    {"set": {"notify.bogus": True}}, {"set": {"region": "eu"}},
    {"set": {"overlay.widgets.nope": True}}, {"set": {"overlay.mode.nope": "pin"}},
])
def test_unknown_or_forbidden_keys_rejected(body):
    with pytest.raises(ValueError):
        settings.validate(body)


@pytest.mark.parametrize("key,v", [
    ("hotkeys.toggleOverlay", "Control+Alt+F\n"), ("profile.family", "abc\n"),
    ("overlay.scale", 10 ** 400), ("market.fame_pct", -(10 ** 400)), ("overlay.display", 10 ** 400),
])
def test_refute_r1_edge_values_rejected(key, v):
    with pytest.raises(ValueError):
        settings.validate({"set": {key: v}})


def test_duplicate_keys_edit_the_one_json_loads_keeps(tmp_path):
    p = tmp_path / "c.json"
    p.write_text('{"overlay": {"scale": 1, "w": {"a": 1}}, "overlay": {"anchor": "tl"}}\n')
    settings.Settings(p).apply({"set": {"overlay.scale": 1.2, "overlay.mode.season": "pin"}})
    assert json.loads(p.read_text())["overlay"] == {"anchor": "tl", "scale": 1.2,
                                                     "mode": {"season": "pin"}}


def test_too_many_keys_rejected():
    with pytest.raises(ValueError):
        settings.validate({"set": {f"k{i}": 1 for i in range(settings.MAX_SET + 1)}})


# ---- write: splice, preserve, atomic ----

def test_round_trip_keeps_secrets_loop_and_unknown_bytes(cfg):
    s = settings.Settings(cfg)
    out = s.apply({"set": {"overlay.scale": 1.2, "overlay.mode.leveling": "pin",
                           "ui.theme": "light", "notify.mute_until": "2000-01-01T00:00:00+00:00",
                           "hotkeys.toggleOverlay": "Control+Shift+O"}})
    text = cfg.read_text()
    for key in ("secrets", "loop", "zzz_unknown", "_doc"):
        assert _block(text, key) == _block(SAMPLE, key), key
    doc = json.loads(text)
    assert list(doc) == ["_doc", "secrets", "overlay", "loop", "zzz_unknown", "ui", "notify", "hotkeys"]
    assert list(doc["overlay"]) == ["_doc", "anchor", "scale", "widgets", "mode"]
    assert doc["overlay"]["scale"] == 1.2 and doc["overlay"]["mode"] == {"leveling": "pin"}
    assert doc["overlay"]["widgets"] == {"grindSession": True, "leveling": False}  # untouched
    assert doc["ui"] == {"theme": "light"}
    assert doc["notify"] == {"mute_until": "2000-01-01T00:00:00+00:00"}
    assert out["settings"]["overlay.scale"] == 1.2
    assert set(out["changed"]) == {"overlay.scale", "overlay.mode.leveling", "ui.theme",
                                   "notify.mute_until", "hotkeys.toggleOverlay"}
    assert out["restart"] == []
    # Lines that were not edited are untouched (only the scale line changed in overlay).
    assert '    "_doc": "plan 022",\n    "anchor": "ml",\n    "scale": 1.2,\n' in text
    assert text.endswith("}\n") and "\r" not in text and text.isascii()


def test_round_trip_on_the_example_config(tmp_path):
    p = tmp_path / "local.json"
    raw = EXAMPLE.read_bytes()
    p.write_bytes(raw)
    s = settings.Settings(p)
    s.apply({"set": {k: v for k, v in settings.defaults().items()
                     if k not in ("profile.family", "profile.base_url")}})
    s.apply({"set": {"overlay.anchor": {"x": 40, "y": 300}, "market.fame_pct": 1.5,
                     "profile.family": "Somebody", "overlay.mode.dice": "block"}})
    text, orig = p.read_text(), raw.decode()
    for key in ("secrets", "loop", "bdo", "ocr", "region", "_doc"):
        assert _block(text, key) == _block(orig, key), key
    doc = json.loads(text)
    assert doc["profile"]["base_url"] == json.loads(orig)["profile"]["base_url"]
    assert doc["overlay"]["mode"]["dice"] == "block"
    assert doc["overlay"]["anchor"] == {"x": 40, "y": 300}


def test_restart_keys_reported(cfg):
    out = settings.Settings(cfg).apply({"set": {"profile.family": "Abc", "ui.scale": 1.1}})
    assert out["restart"] == ["profile.family"]
    assert settings.RESTART_KEYS == ("profile.family", "profile.base_url")


def test_ancestor_not_an_object_is_replaced(tmp_path):
    p = tmp_path / "c.json"
    p.write_text('{"ui": 5, "notify": null}\n')
    settings.Settings(p).apply({"set": {"ui.scale": 1.1, "notify.mute_until": ""}})
    assert json.loads(p.read_text()) == {"ui": {"scale": 1.1}, "notify": {"mute_until": ""}}


def test_missing_file_is_created(tmp_path):
    p = tmp_path / "config" / "local.json"
    settings.Settings(p).apply({"set": {"ui.theme": "system"}})
    assert json.loads(p.read_text()) == {"ui": {"theme": "system"}}


def test_bad_json_is_refused_not_clobbered(tmp_path):
    p = tmp_path / "c.json"
    p.write_bytes(b'{"secrets": {"a": {"env": "K"}},')
    with pytest.raises(ValueError):
        settings.Settings(p).apply({"set": {"ui.theme": "dark"}})
    assert p.read_bytes() == b'{"secrets": {"a": {"env": "K"}},'
    assert settings.Settings(p).view()["error"]


def test_invalid_pair_writes_nothing(cfg):
    before = cfg.read_bytes()
    with pytest.raises(ValueError):
        settings.Settings(cfg).apply({"set": {"ui.theme": "dark", "ui.scale": 5}})
    assert cfg.read_bytes() == before


def test_duplicate_hotkeys_refused(cfg):
    with pytest.raises(ValueError):
        settings.Settings(cfg).apply({"set": {"hotkeys.showDashboard": "Control+Alt+E"}})


def test_write_is_atomic_tmp_then_replace(cfg, monkeypatch):
    seen = []
    real = Path.replace

    def spy(self, target):
        seen.append((self.name, Path(target).name, Path(target).read_bytes() == SAMPLE.encode()))
        return real(self, target)

    monkeypatch.setattr(Path, "replace", spy)
    settings.Settings(cfg).apply({"set": {"ui.theme": "light"}})
    assert len(seen) == 1 and seen[0][0].endswith(".tmp") and seen[0][1] == "local.json"
    assert seen[0][2] is True  # target untouched until the replace
    assert not list(cfg.parent.glob("*.tmp"))


def test_splice_handles_escapes_and_nesting():
    text = '{"a\\"b": "x}y", "c": {"d": [1, {"e": "}"}], "f": {}}}'
    out = settings.splice(text, ("c", "f", "g"), True)
    assert json.loads(out) == {"a\"b": "x}y", "c": {"d": [1, {"e": "}"}], "f": {"g": True}}}
    out = settings.splice(out, ("c", "d"), 7)
    assert json.loads(out)["c"]["d"] == 7 and out.startswith('{"a\\"b": "x}y"')


# ---- HTTP routes ----

@pytest.fixture()
def srv(tmp_path, cfg):
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          sse_interval=0.05, market_seed=[],
                          market_client=market.ArshaClient(fetch=_no_network,
                                                           cache_dir=tmp_path / "cache"),
                          profile_cfg={}, config_path=cfg)
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield s
    s.shutdown()
    s.server_close()


def test_http_get_and_post(srv, cfg):
    st, _, body = _get(srv, "/api/settings")
    doc = json.loads(body)
    assert st == 200 and doc["settings"]["overlay.anchor"] == "ml"
    assert "EW_OCR_API_KEY" not in body.decode()
    st, out = _post(srv, "/api/settings", {"set": {"market.vp": True, "market.fame_pct": 1}})
    assert st == 200 and out["settings"]["market.vp"] is True
    assert srv.market.settings == {"vp": True, "fame_pct": 1}  # applied live
    assert _block(cfg.read_text(), "secrets") == _block(SAMPLE, "secrets")


@pytest.mark.parametrize("body", [{"set": {"secrets.ocr": "x"}}, {"set": {"ui.scale": 2}},
                                  {"ui.scale": 1}])
def test_http_post_rejects(srv, cfg, body):
    before = cfg.read_bytes()
    st, out = _post(srv, "/api/settings", body)
    assert st == 400 and out["error"]
    assert cfg.read_bytes() == before


# ---- plan 080: settings purge ----

REMOVED = (["ocr.auto", "play.auto_session", "notices.auto_add", "events.notice_check",
            "coupons.check", "checklist.auto", "overlay.auto",
            "ocr.auto_commit_min", "ocr.daily_cap", "play.grace_s", "overlay.idle_min",
            "notify.ladder_min", "notify.quiet_closed", "events.maintenance_start_utc"]
           + [f"overlay.widgets.{w}" for w in settings.WIDGETS]
           + [f"notify.{n}" for n in ("marketAlert", "buffEnding", "hotTime", "resetPassed",
                                      "newCoupon", "gameExit", "bossSoon", "resetSoon",
                                      "loginRisk", "maintLoss")])


@pytest.mark.parametrize("key", REMOVED)
def test_removed_key_rejected_by_post(srv, cfg, key):
    before = cfg.read_bytes()
    st, out = _post(srv, "/api/settings", {"set": {key: settings.fixed().get(key, True)}})
    assert st == 400 and "not a settable key" in out["error"], key
    assert cfg.read_bytes() == before


def test_get_lists_no_removed_key(srv):
    doc = json.loads(_get(srv, "/api/settings")[2])
    assert not set(REMOVED) & set(doc["settings"])
    assert not set(REMOVED) & set(doc["defaults"])
    # No automation switch, notification checkbox, manual layout or tunable is left:
    # every bool left is the Value Pack fact.
    assert [k for k, v in doc["defaults"].items() if isinstance(v, bool)] == ["market.vp"]


def test_zero_config_every_notification_rule_enabled(srv):
    """Nothing settable can switch a rule off; the fixed policy is on, quiet + ladder."""
    doc = json.loads(_get(srv, "/api/settings")[2])
    assert [k for k in doc["settings"] if k.startswith("notify.")] == ["notify.mute_until"]
    p = json.loads(_get(srv, "/api/prompts")[2])
    assert p["muted_until"] is None and p["quiet_closed"] is True
    assert p["ladder_min"] == [15, 5, 1]
    eff = srv.eff_settings()
    for key, v in settings.fixed().items():
        assert eff[key] == v, key


def test_mute_until_is_a_ledger_entry_and_mutes(srv, cfg):
    srv.overrides.clock = lambda: 1_000_000.0
    srv.prompts.clock = srv.overrides.clock
    until = "1970-01-12T14:46:40+00:00"  # 1_000_000 + 3600
    st, out = _post(srv, "/api/settings", {"set": {"notify.mute_until": until}})
    assert st == 200
    item = [i for i in out["overrides"]["items"] if i["key"] == "notify.mute_until"][0]
    assert item["cards"] == ["shell"] and item["expires_in_s"] == 86400
    assert json.loads(_get(srv, "/api/prompts")[2])["muted_until"] == until
    srv.overrides.clock = srv.prompts.clock = lambda: 1_000_000.0 + 3601
    assert json.loads(_get(srv, "/api/prompts")[2])["muted_until"] is None


def test_mute_until_over_24h_rejected():
    import time
    with pytest.raises(ValueError):
        settings.validate({"set": {"notify.mute_until": _iso(time.time() + 25 * 3600)}})
    assert settings.validate({"set": {"notify.mute_until": _iso(time.time() + 23 * 3600)}})


def _iso(t):
    import datetime as dt
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).replace(microsecond=0).isoformat()


def test_incident_switch_in_config_badged_then_expires(srv, cfg):
    """An old config with ocr.auto false shows an override badge; auto-OCR
    resumes 24 h after EW first read it (not 24 h after the file mtime)."""
    import os
    cfg.write_text('{"ocr": {"auto": false}, "overlay": {"idle_min": 30}}\n')
    os.utime(cfg, (1000.0, 1000.0))  # a months-old file
    t0 = 50_000_000.0
    srv.overrides.clock = lambda: t0
    assert srv.eff_settings()["ocr.auto"] is False
    assert srv.eff_settings()["overlay.idle_min"] == 30
    items = {i["key"]: i for i in srv.overrides_view()["items"]}
    assert items["ocr.auto"]["source"] == "config" and "shell" in items["ocr.auto"]["cards"]
    assert items["ocr.auto"]["expires_in_s"] == 86400
    assert "incident" in items["ocr.auto"]["reason"]
    srv.overrides.clock = lambda: t0 + 86400
    assert srv.eff_settings()["ocr.auto"] is True and srv.eff_settings()["overlay.idle_min"] == 20
    v = srv.overrides_view()
    assert not [i for i in v["items"] if i["key"] == "ocr.auto"]
    assert [h["retired_by"] for h in v["history"] if h["key"] == "ocr.auto"] == ["expired"]
    srv.overrides.clock = lambda: t0 + 2 * 86400  # the same file never re-imports it
    assert srv.eff_settings()["ocr.auto"] is True


def test_incident_switch_clear_route(srv, cfg):
    cfg.write_text('{"checklist": {"auto": false}}\n')
    assert srv.eff_settings()["checklist.auto"] is False
    st, out = _post(srv, "/api/settings", {"clear": "checklist.auto"})
    assert st == 200 and out["changed"] == []
    assert srv.eff_settings()["checklist.auto"] is True
    assert json.loads(cfg.read_text()) == {"checklist": {"auto": False}}  # config untouched
    cfg.write_text('{"coupons": {"check": false}}\n')
    srv.eff_settings()
    st, out = _post(srv, "/api/settings", {"clear": "coupons.check"})
    assert st == 200 and out["restart"] == ["coupons.check"]  # read at server start


def test_coupon_and_notice_incident_switch_gates_the_clients(tmp_path, cfg):
    cfg.write_text('{"coupons": {"check": false}, "events": {"notice_check": false}}\n')
    s = ewapp.make_server(port=0, store_root=tmp_path / "store2", commit="a" * 40,
                          market_seed=[], profile_cfg={}, config_path=cfg,
                          market_client=market.ArshaClient(fetch=_no_network,
                                                           cache_dir=tmp_path / "cache"),
                          coupon_client=object(), notice_client=object())
    try:
        assert s.coupons.client is None and s.notices.client is None
    finally:
        s.server_close()


def test_settings_tab_is_last(srv):
    ids = [t["id"] for t in json.loads(_get(srv, "/api/state")[2])["tabs"]]
    assert ids[-1] == "settings"
