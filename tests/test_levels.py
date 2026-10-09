"""Plan 018: level cap 75 readiness - one level range, XP patch epochs,
re-seeded milestones, XP buff presets, spot level gap and re-verify badge.

Tracked data files and operator-typed data only; nothing is read from the
game. Every clock is injected. All times are UTC.
"""

import datetime as dt
import http.client
import json
import re
import threading
from pathlib import Path

import pytest

from server.ew import app as ewapp
from server.ew import grind, leveling, levels, progress, spots
from server.ew.store import Store

ROOT = Path(__file__).resolve().parent.parent
UTC = dt.timezone.utc
T0 = dt.datetime(2026, 10, 5, 12, 0, 0, tzinfo=UTC)
EPOCH_AT = dt.datetime(2026, 10, 8, 0, 0, 0, tzinfo=UTC)
EPOCH = {"id": "cap75", "starts_utc": EPOCH_AT.isoformat(), "label": "Lv 75 patch",
         "source": "fixture", "verified": False,
         "kill_xp_cap": [{"level_min": 56, "level_max": 61, "note": "cap ~0.2%"}]}


class Clock:
    def __init__(self, when=T0):
        self.t = when.timestamp()

    def __call__(self):
        return self.t

    def set(self, when):
        self.t = when.timestamp()


@pytest.fixture()
def clock():
    return Clock()


@pytest.fixture()
def store(tmp_path):
    return Store(tmp_path / "store")


def _svc(store, clock, epochs=None):
    return leveling.LevelingService(store, clock=clock,
                                    epochs=[dict(EPOCH)] if epochs is None else epochs)


# --- one level range ----------------------------------------------------------

def test_level_range_is_one_constant():
    assert levels.LEVEL_MAX == 75 and levels.LEVEL_RANGE == (1, 75)
    for mod in (progress, leveling, spots):
        assert mod.LEVEL_RANGE is levels.LEVEL_RANGE


def test_no_module_assigns_its_own_level_range():
    pat = re.compile(r"^\s*LEVEL_RANGE\s*=", re.M)
    offenders = [p.name for p in (ROOT / "server" / "ew").glob("*.py")
                 if p.name != "levels.py" and pat.search(p.read_text(encoding="utf-8"))]
    assert offenders == []


# --- data files -----------------------------------------------------------------

@pytest.mark.parametrize("path", [levels.EPOCHS_FILE, levels.BUFFS_FILE])
def test_tracked_data_is_ascii_lf(path):
    raw = path.read_bytes()
    raw.decode("ascii")
    assert b"\r" not in raw


def test_tracked_epochs_pass_schema():
    rows = levels.load_epochs()
    assert rows and rows[0]["id"] == "cap75-xp-rescale"
    assert rows[0]["verified"] is False and "verify" in rows[0]["source"]
    assert levels._parse_iso(rows[0]["starts_utc"]).date() == dt.date(2026, 10, 8)
    for c in rows[0]["kill_xp_cap"]:
        assert "verify" in c["note"] or "patch notes 2026-10-08" in c["note"]


def test_tracked_presets_pass_schema():
    rows = levels.load_buff_presets()
    by = {r["name"]: r for r in rows}
    assert by["Body Enhancement"]["xp_pct"] == 100 and by["Body Enhancement"]["pre_patch_xp_pct"] == 50
    assert by["Adventure Blessing"]["xp_pct"] == 30 and by["Adventure Blessing"]["pre_patch_xp_pct"] == 15
    assert by["Pearl outfit set"]["xp_pct"] == 50 and by["Pearl outfit set"]["pre_patch_xp_pct"] == 10
    for r in rows:
        assert r["source"].strip() and "verify" in r["notes"]
        assert levels.DATE_RE.match(r["verified"])


@pytest.mark.parametrize("patch", [
    {"id": "Bad"}, {"starts_utc": "2026-10-08T00:00:00"}, {"starts_utc": "nope"},
    {"label": ""}, {"label": "x" * 41}, {"source": ""}, {"source": "a\nb"},
    {"verified": "yes"}, {"extra": 1},
    {"kill_xp_cap": [{"level_min": 5, "level_max": 1, "note": "x"}]},
    {"kill_xp_cap": [{"level_min": 1, "level_max": 76, "note": "x"}]},
    {"kill_xp_cap": [{"level_min": 1, "level_max": 5}]},
    {"kill_xp_cap": "x"},
])
def test_epoch_schema_rejects(patch):
    with pytest.raises(ValueError):
        levels.validate_epoch(dict(EPOCH, **patch), tracked=True)


def test_operator_epoch_may_not_carry_kill_caps():
    with pytest.raises(ValueError):
        levels.validate_epoch(dict(EPOCH))
    row = {k: v for k, v in EPOCH.items() if k != "kill_xp_cap"}
    assert levels.validate_epoch(row)["starts_utc"] == "2026-10-08T00:00:00+00:00"


@pytest.mark.parametrize("patch", [
    {"xp_pct": 1001}, {"xp_pct": True}, {"pre_patch_xp_pct": -1}, {"pre_patch_xp_pct": 100},
    {"verified": "2026-1-1"}, {"source": ""}, {"name": ""}, {"notes": None}, {"id": "A"},
    {"extra": 1},
])
def test_preset_schema_rejects(patch):
    row = {"id": "body", "name": "Body Enhancement", "xp_pct": 100, "pre_patch_xp_pct": 50,
           "notes": "verify", "source": "fixture", "verified": "2026-10-05"}
    levels.validate_preset(dict(row))
    with pytest.raises(ValueError):
        levels.validate_preset(dict(row, **patch))


def test_bad_tracked_files_raise(tmp_path):
    p = tmp_path / "x.json"
    p.write_text("nope", encoding="ascii")
    with pytest.raises(ValueError):
        levels.load_epochs(p)
    with pytest.raises(ValueError):
        levels.load_buff_presets(p)
    p.write_text(json.dumps([dict(EPOCH), dict(EPOCH)]), encoding="ascii")
    with pytest.raises(ValueError):
        levels.load_epochs(p)


# --- pure helpers ---------------------------------------------------------------

def test_active_and_next_epoch():
    e = [levels.validate_epoch(dict(EPOCH), tracked=True)]
    assert levels.active_epoch(e, T0) is None and levels.next_epoch(e, T0)["id"] == "cap75"
    assert levels.active_epoch(e, EPOCH_AT)["id"] == "cap75"  # start inclusive
    assert levels.next_epoch(e, EPOCH_AT) is None


def test_kill_cap_note_by_band():
    e = levels.validate_epoch(dict(EPOCH), tracked=True)
    assert levels.kill_cap_note(e, 58) == "cap ~0.2%"
    assert levels.kill_cap_note(e, 62) is None and levels.kill_cap_note(None, 58) is None
    assert levels.kill_cap_note(e, None) is None


@pytest.mark.parametrize("lvl,mon,dr", [(60, 60, 0), (58, 60, 0), (61, 60, 3), (62, 60, 6),
                                        (63, 60, 9), (75, 60, 9), (None, 60, None)])
def test_outlevel_dr(lvl, mon, dr):
    assert levels.outlevel_dr(lvl, mon) == dr


def test_buff_hint_only_on_pre_patch_value():
    p = levels.load_buff_presets()
    assert levels.buff_hint("body enhancement ", 50, p) == levels.PRE_PATCH_HINT
    assert levels.buff_hint("Body Enhancement", 100, p) is None
    assert levels.buff_hint("XP scroll", 50, p) is None
    assert levels.buff_hint("Body Enhancement", None, p) is None


# --- leveling: epoch-bounded rate -------------------------------------------------

def _at(when, level, pct):
    return {"ts": when.isoformat(), "level": level, "pct": pct}


def test_rate_ignores_samples_before_since():
    pre = EPOCH_AT - dt.timedelta(hours=2)
    s = [_at(pre, 58, 0.0), _at(pre + dt.timedelta(hours=1), 58, 50.0),  # 50 %/h pre-patch
         _at(EPOCH_AT, 58, 60.0), _at(EPOCH_AT + dt.timedelta(hours=2), 58, 62.0)]
    assert leveling.rate_pct_h(s) == pytest.approx(6.0, abs=50)  # mixed, sanity only
    assert leveling.rate_pct_h(s, EPOCH_AT) == pytest.approx(1.0)
    assert leveling.rate_pct_h(s[:2] + s[2:3], EPOCH_AT) is None  # one post sample


def test_service_rate_across_epoch_boundary(store, clock):
    svc = _svc(store, clock)
    clock.set(EPOCH_AT - dt.timedelta(hours=2))
    svc.sample({"level": 58, "pct": 0})
    clock.set(EPOCH_AT - dt.timedelta(hours=1))
    doc = svc.sample({"level": 58, "pct": 50})
    assert doc["rate_pct_h"] == pytest.approx(50.0) and doc["epoch"] is None
    assert doc["epoch_next"]["id"] == "cap75" and doc["epoch_next"]["starts_in_s"] == 3600
    assert not any(s["pre_patch"] for s in doc["samples"])
    clock.set(EPOCH_AT + dt.timedelta(minutes=10))
    doc = svc.sample({"level": 58, "pct": 51})
    assert doc["rate_pct_h"] is None and doc["eta_next_s"] is None  # one post-patch sample
    assert doc["epoch"]["id"] == "cap75" and doc["epoch_next"] is None
    assert [s["pre_patch"] for s in doc["samples"]] == [False, True, True]  # newest first
    clock.set(EPOCH_AT + dt.timedelta(hours=2, minutes=10))
    doc = svc.sample({"level": 58, "pct": 55})
    assert doc["rate_pct_h"] == pytest.approx(2.0)
    assert len(doc["samples"]) == 4  # older samples stay listed


def test_view_kill_cap_for_current_band(store, clock):
    svc = _svc(store, clock)
    clock.set(EPOCH_AT + dt.timedelta(hours=1))
    assert svc.sample({"level": 58, "pct": 1})["kill_xp_cap"] == "cap ~0.2%"
    assert svc.sample({"level": 62, "pct": 1})["kill_xp_cap"] is None


def test_kill_cap_not_shown_before_the_epoch(store, clock):
    assert _svc(store, clock).sample({"level": 58, "pct": 1})["kill_xp_cap"] is None


# --- leveling: epoch editing ------------------------------------------------------

NEW = {"id": "next-patch", "starts_utc": "2026-11-05T07:00:00Z", "label": "XP patch 2",
       "source": "patch notes", "verified": True}


def test_epoch_add_correct_and_delete(store, clock):
    svc = _svc(store, clock)
    doc = svc.epoch_add(dict(NEW))
    assert [e["id"] for e in doc["epochs"]] == ["cap75", "next-patch"]
    assert doc["epochs"][0]["tracked"] is True and doc["epochs"][1]["tracked"] is False
    assert doc["epochs"][1]["starts_utc"] == "2026-11-05T07:00:00+00:00"
    # correcting the tracked row by id keeps its kill caps
    fix = {"id": "cap75", "starts_utc": "2026-10-08T14:00:00Z", "label": "Lv 75 patch",
           "source": "NA patch notes", "verified": True}
    doc = svc.epoch_add(fix)
    assert doc["epochs"][0]["verified"] is True and doc["epochs"][0]["tracked"] is True
    assert svc.epochs()[0]["kill_xp_cap"] == EPOCH["kill_xp_cap"]
    doc = svc.epoch_del("cap75")
    assert [e["id"] for e in doc["epochs"]] == ["next-patch"]
    # a restart keeps the deletion (tracked row stays deleted)
    again = _svc(store, clock)
    assert [e["id"] for e in again.view()["epochs"]] == ["next-patch"]
    doc = svc.epoch_del("next-patch")
    assert doc["epochs"] == []
    with pytest.raises(ValueError):
        svc.epoch_del("next-patch")
    # re-adding a deleted tracked id restores it
    doc = svc.epoch_add(fix)
    assert [e["id"] for e in doc["epochs"]] == ["cap75"]


@pytest.mark.parametrize("arg", [
    None, [], {k: v for k, v in NEW.items() if k != "verified"}, dict(NEW, x=1),
    dict(NEW, id="Bad Id"), dict(NEW, starts_utc="2026-11-05"), dict(NEW, label=""),
    dict(NEW, verified=1), dict(NEW, kill_xp_cap=[]),
])
def test_epoch_add_bad(store, clock, arg):
    with pytest.raises(ValueError):
        _svc(store, clock).epoch_add(arg)


@pytest.mark.parametrize("arg", [None, 5, "Bad", "unknown"])
def test_epoch_del_bad(store, clock, arg):
    with pytest.raises(ValueError):
        _svc(store, clock).epoch_del(arg)


def test_added_epochs_capped(store, clock):
    svc = _svc(store, clock)
    for i in range(leveling.MAX_EPOCHS):
        svc.epoch_add(dict(NEW, id=f"p{i}"))
    with pytest.raises(ValueError):
        svc.epoch_add(dict(NEW, id="one-more"))
    svc.epoch_add(dict(NEW, id="p0", label="re-dated"))  # replacing is not adding


def test_corrupt_epoch_entries_skipped(store, clock):
    store.put("leveling", {"milestones": [56], "epochs_added": [dict(NEW), {"id": "x"}, 5],
                           "epochs_deleted": ["cap75", 7, "Bad Id"]})
    doc = _svc(store, clock).view()
    assert [e["id"] for e in doc["epochs"]] == ["next-patch"]


def test_bad_tracked_epoch_file_degrades(store, clock, monkeypatch, tmp_path):
    bad = tmp_path / "e.json"
    bad.write_text("nope", encoding="ascii")
    monkeypatch.setattr(levels, "EPOCHS_FILE", bad)
    monkeypatch.setattr(levels.load_epochs, "__defaults__", (bad,))
    svc = leveling.LevelingService(store, clock=clock)
    doc = svc.view()
    assert doc["epochs"] == [] and doc["epoch_error"] and doc["epoch"] is None


def test_real_tracked_epochs_load_by_default(store, clock):
    doc = leveling.LevelingService(store, clock=clock).view()
    assert doc["epoch_error"] is None and doc["epoch_next"]["id"] == "cap75-xp-rescale"


# --- milestones -------------------------------------------------------------------

def test_new_seed_and_labels(store, clock):
    doc = _svc(store, clock).view()
    assert doc["milestones"] == [56, 60, 61, 70, 75] and doc["milestones_seed"] is True
    assert doc["milestone_labels"]["75"] == "level cap"
    assert doc["milestone_labels"]["60"] == "Rebirth of Darkness"
    assert doc["next_milestone"] == 56 and doc["next_milestone_label"] == "main questline end"


def test_old_seed_migrates(store, clock):
    store.put("leveling", {"milestones": [50, 56, 57, 58, 60, 61],
                           "samples": [_at(T0, 52, 10.0)]})
    doc = _svc(store, clock).view()
    assert doc["milestones"] == [56, 60, 61, 70, 75] and doc["milestones_seed"] is True
    assert doc["level"] == 52  # samples kept
    assert store.get("leveling")["milestones"] == [56, 60, 61, 70, 75]  # persisted


@pytest.mark.parametrize("edited", [[50, 56, 57, 58, 60], [50, 56, 57, 58, 60, 61, 65], [55], []])
def test_edited_milestones_kept(store, clock, edited):
    store.put("leveling", {"milestones": edited})
    assert _svc(store, clock).view()["milestones"] == edited


def test_level_75_milestones_accepted(store, clock):
    assert _svc(store, clock).set_milestones([75, 71])["milestones"] == [71, 75]


# --- grind presets ------------------------------------------------------------------

def test_grind_view_presets_and_pre_patch_hint(store, clock):
    g = grind.GrindService(store, clock=clock)
    g.buff({"name": "Body Enhancement", "minutes": 60, "xp_pct": 50})
    g.buff({"name": "Adventure Blessing", "minutes": 60, "xp_pct": 30})
    doc = g.view()
    names = [p["name"] for p in doc["xp_presets"]]
    assert names == ["Body Enhancement", "Adventure Blessing", "Pearl outfit set"]
    assert doc["xp_presets"][0]["xp_pct"] == 100 and doc["xp_presets_error"] is None
    by = {b["name"]: b for b in doc["buffs"]}
    assert by["Body Enhancement"]["xp_hint"] == levels.PRE_PATCH_HINT
    assert by["Body Enhancement"]["xp_pct"] == 50  # never rewritten
    assert by["Adventure Blessing"]["xp_hint"] is None
    assert by["XP scroll"]["xp_hint"] is None


def test_grind_presets_and_hint_wait_for_the_epoch(store, clock):
    # refute r1 minor 1: before the patch the pre-patch value is the live one
    ep = {"v": None}
    g = grind.GrindService(store, clock=clock, epoch=lambda: ep["v"])
    g.buff({"name": "Body Enhancement", "minutes": 60, "xp_pct": 50})
    doc = g.view()
    assert doc["xp_presets"][0]["xp_pct"] == 50 and doc["xp_presets"][0]["patched"] is False
    assert {b["name"]: b for b in doc["buffs"]}["Body Enhancement"]["xp_hint"] is None
    ep["v"] = levels.validate_epoch(dict(EPOCH), tracked=True)
    doc = g.view()
    assert doc["xp_presets"][0]["xp_pct"] == 100 and doc["xp_presets"][0]["patched"] is True
    assert {b["name"]: b for b in doc["buffs"]}["Body Enhancement"]["xp_hint"] == levels.PRE_PATCH_HINT


def test_route_grind_presets_follow_leveling_epoch(srv, clock):
    code, body = _req(srv, "GET", "/api/grind")
    assert code == 200 and body["xp_presets"][0]["patched"] is False  # T0 is pre-patch
    clock.set(EPOCH_AT + dt.timedelta(hours=1))
    code, body = _req(srv, "GET", "/api/grind")
    assert body["xp_presets"][0]["patched"] is True and body["xp_presets"][0]["xp_pct"] == 100


def test_kill_cap_survives_a_later_operator_epoch(store, clock):
    # refute r1 minor 2
    svc = _svc(store, clock)
    svc.epoch_add(dict(NEW))
    clock.set(dt.datetime(2026, 11, 6, tzinfo=UTC))
    doc = svc.sample({"level": 58, "pct": 1})
    assert doc["epoch"]["id"] == "next-patch" and doc["kill_xp_cap"] == "cap ~0.2%"


def test_grind_presets_injectable_and_bad_file_degrades(store, clock, monkeypatch, tmp_path):
    assert grind.GrindService(store, clock=clock, presets=[]).view()["xp_presets"] == []
    bad = tmp_path / "b.json"
    bad.write_text("nope", encoding="ascii")
    monkeypatch.setattr(levels.load_buff_presets, "__defaults__", (bad,))
    doc = grind.GrindService(store, clock=clock).view()
    assert doc["xp_presets"] == [] and doc["xp_presets_error"]


# --- spots: optional fields, level gap, re-verify ---------------------------------

def _row(**kw):
    r = {"id": "s", "name": "S", "region": "R", "ap_min": 1, "dp_min": 1, "level_min": 1,
         "xp_tier": 3, "silver_tier": 3, "notes": "verify", "source": "fixture",
         "verified": "2026-10-05"}
    r.update(kw)
    return r


def test_spot_optional_fields_validate():
    spots.validate_row(_row(monster_level=60, epoch="cap75"))
    for bad in ({"monster_level": 0}, {"monster_level": 100}, {"monster_level": "60"},
                {"epoch": "Bad Id"}, {"epoch": 5}):
        with pytest.raises(ValueError):
            spots.validate_row(_row(**bad))


def test_reverify_badge():
    e = levels.validate_epoch(dict(EPOCH), tracked=True)
    assert spots.reverify(_row(verified="2026-10-05"), e) is True
    assert spots.reverify(_row(verified="2026-10-08"), e) is False
    assert spots.reverify(_row(verified="2026-10-05", epoch="cap75"), e) is False
    assert spots.reverify(_row(), None) is False


def test_spots_view_gap_dr_and_badge():
    table = [_row(id="a", name="A", monster_level=56), _row(id="b", name="B")]
    ep = {"v": None}
    svc = spots.SpotsService(table, character=lambda: {"level": 58, "gs": {"ap": 9, "dp": 9}},
                             grind=lambda: {"spots": []}, epoch=lambda: ep["v"])
    by = {r["id"]: r for r in svc.view({})["top"]}
    assert by["a"]["level_gap"] == 2 and by["a"]["outlevel_dr"] == 6
    assert by["b"]["level_gap"] is None and by["b"]["outlevel_dr"] is None
    assert not by["a"]["reverify"]
    ep["v"] = levels.validate_epoch(dict(EPOCH), tracked=True)
    assert all(r["reverify"] for r in svc.view({})["top"])


def test_spots_epoch_callable_failure_is_not_fatal():
    def boom():
        raise RuntimeError("x")
    svc = spots.SpotsService([_row()], character=lambda: {"level": 5, "gs": {"ap": 9, "dp": 9}},
                             grind=lambda: {"spots": []}, epoch=boom)
    assert svc.view({})["top"][0]["reverify"] is False


# --- routes: 75 accepted, 76 rejected on all three ----------------------------------

@pytest.fixture()
def srv(tmp_path, clock):
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          market_seed=[], grind_clock=clock, leveling_clock=clock,
                          profile_cfg={})
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield s
    s.shutdown()
    s.server_close()


def _req(s, method, path, body=None):
    c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=5)
    data = None if body is None else json.dumps(body).encode()
    c.request(method, path, body=data, headers={"Content-Type": "application/json"})
    r = c.getresponse()
    out = r.read()
    c.close()
    return r.status, (json.loads(out) if out else None)


def test_routes_accept_75_reject_76(srv):
    assert _req(srv, "POST", "/api/leveling", {"sample": {"level": 75, "pct": 1}})[0] == 200
    assert _req(srv, "POST", "/api/leveling", {"sample": {"level": 76, "pct": 1}})[0] == 400
    assert _req(srv, "POST", "/api/progress", {"character": {"level": 75}})[0] == 200
    assert _req(srv, "POST", "/api/progress", {"character": {"level": 76}})[0] == 400
    assert _req(srv, "GET", "/api/spots?ap=300&dp=300&level=75")[0] == 200
    assert _req(srv, "GET", "/api/spots?ap=300&dp=300&level=76")[0] == 400


def test_route_epoch_ops(srv):
    code, body = _req(srv, "POST", "/api/leveling", {"epoch_add": dict(NEW)})
    assert code == 200 and "next-patch" in [e["id"] for e in body["epochs"]]
    code, body = _req(srv, "POST", "/api/leveling", {"epoch_del": "next-patch"})
    assert code == 200 and "next-patch" not in [e["id"] for e in body["epochs"]]
    code, body = _req(srv, "POST", "/api/leveling", {"epoch_del": "nope"})
    assert code == 400


def test_route_spots_carry_badge_fields(srv, clock):
    clock.set(EPOCH_AT + dt.timedelta(days=1))
    code, body = _req(srv, "GET", "/api/spots?ap=300&dp=300&level=60")
    assert code == 200 and body["top"]
    for r in body["top"]:
        stale = r["verified"] < "2026-10-08" and r.get("epoch") != "cap75-xp-rescale"
        assert r["reverify"] is stale
        assert "level_gap" in r and "outlevel_dr" in r
