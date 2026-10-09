"""Plan 088: Monster Zone Info OCR - zone, recommended level and per-kill EXP
from a synthetic panel render (stub OCR docs, plan 009 bench style at two UI
scales), the digit guard, cap math per level band, the zone table, the auto-OCR
pipeline and the surfaces (spots, leveling card, What now). No network, no
Tesseract, no PowerShell."""

import datetime as dt
import http.client
import json
import threading

import pytest

from server.ew import app as ewapp
from server.ew import grind, leveling, levels, ocr, ocrauto, spots, whatnow, xpbooks, zoneinfo
from server.ew.store import Store

T0 = 1_790_000_000.0

# Bands as the official 2026-10-08 notes give them (plan 087 seeds the rest).
CAPS = [
    {"level_min": 1, "level_max": 5, "note": "per-kill XP cap ~20% of the level"},
    {"level_min": 50, "level_max": 55, "note": "per-kill XP cap ~2% of the level"},
    {"level_min": 56, "level_max": 61, "note": "per-kill XP cap ~0.2% of the level"},
    {"level_min": 62, "level_max": 64, "note": "per-kill XP cap ~0.02% of the level"},
    {"level_min": 62, "level_max": 75, "note": "per-kill XP cap ~0.01% of the level"},
    {"level_min": 65, "level_max": 75, "note": "per-kill XP cap ~0.01% of the level"},
]


def _iso(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).replace(microsecond=0).isoformat()


def _panel(scale=1.0, zone="Polly's Forest", level=56, rows=None, split=False):
    """A synthetic Monster Zone Info render: title, the zone line with its
    recommended level, then one row per monster (name and EXP in one OCR line,
    or in two cells on the same row with `split`)."""
    rows = rows if rows is not None else [("Polly Forest Fighter", "0.0450%"),
                                          ("Polly Forest Shaman", "0.0520%"),
                                          ("Polly Forest Elder", "0.1200%")]
    h = round(18 * scale)

    def ln(text, x, y, w=300):
        return {"text": text, "x": round(x * scale), "y": round(y * scale),
                "w": round(w * scale), "h": h}

    lines = [ln("Monster Zone Info", 600, 120), ln(f"Lv. {level} {zone}", 420, 170)]
    for i, (name, pct) in enumerate(rows):
        y = 210 + 30 * i
        if split:
            lines.append(ln(name, 440, y, 240))
            lines.append(ln(pct, 900, y, 90))
        else:
            lines.append(ln(f"{name}  {pct}", 440, y, 560))
    return {"text": "\n".join(x["text"] for x in lines), "lines": lines}


# -- parser -----------------------------------------------------------------------

@pytest.mark.parametrize("scale", [1.0, 1.5])
@pytest.mark.parametrize("split", [False, True])
def test_parse_zone_level_and_three_monsters(scale, split):
    fs = zoneinfo.parse(_panel(scale, split=split))
    assert len(fs) == 1
    f = fs[0]
    assert f["kind"] == "zone_xp" and f["name"] == "Polly's Forest"
    assert f["value"]["zone_name"] == "Polly's Forest"
    assert f["value"]["recommended_level"] == 56
    assert f["value"]["monsters"] == [
        {"name": "Polly Forest Fighter", "xp_per_kill_pct": 0.045},
        {"name": "Polly Forest Shaman", "xp_per_kill_pct": 0.052},
        {"name": "Polly Forest Elder", "xp_per_kill_pct": 0.12}]
    if split:
        assert f["conf"] < ocrauto.AUTO_COMMIT_MIN  # a cell beside the name: reviewed
    else:
        assert f["conf"] >= ocrauto.AUTO_COMMIT_MIN, f
    assert "3 monsters" in f["why"]


def test_no_title_no_read():
    doc = _panel()
    doc["lines"] = doc["lines"][1:]
    assert zoneinfo.parse(doc) == []
    assert zoneinfo.parse({"text": "", "lines": []}) == []
    assert zoneinfo.parse(None) == []


def test_title_is_case_insensitive_and_from_the_region_file():
    doc = _panel()
    doc["lines"][0]["text"] = "MONSTER ZONE INFO"
    assert zoneinfo.parse(doc)
    titles = zoneinfo.load_titles()
    assert "Monster Zone Info" in titles
    assert zoneinfo.load_titles("no/such/file.json") == zoneinfo.TITLES


def test_two_zones_each_keep_their_monsters():
    doc = _panel()
    extra = [{"text": "Lv 62 Gyfin Rhasia Temple", "x": 420, "y": 400, "w": 300, "h": 18},
             {"text": "Gyfin Rhasia Lunar 0.0100%", "x": 440, "y": 440, "w": 300, "h": 18}]
    doc["lines"] += extra
    fs = zoneinfo.parse(doc)
    assert [f["value"]["zone_name"] for f in fs] == ["Polly's Forest", "Gyfin Rhasia Temple"]
    assert len(fs[0]["value"]["monsters"]) == 3
    assert fs[1]["value"]["monsters"] == [{"name": "Gyfin Rhasia Lunar", "xp_per_kill_pct": 0.01}]


@pytest.mark.parametrize("tok,value,lo,hi", [
    ("0.0125", 0.0125, 0.9, 1.0),
    ("2", 2, 0.85, 0.95),
    ("0,0125", 0.0125, 0.6, 0.89),       # decimal comma: a misread dot
    ("00125", 125, 0, 0.5),              # point dropped: leading zero
    ("0125", 125, 0, 0.5),               # comma dropped: leading zero
    ("125", 125, 0, 0.5),                # 1.25 with the point dropped: over 100
    ("1,234.5", 1234.5, 0, 0.5),         # digit groups in a percent
    ("0.01.25", 1.25, 0, 0.5),
])
def test_digit_guard(tok, value, lo, hi):
    v, conf, why = zoneinfo.pct_guard(tok)
    assert v == pytest.approx(value) and lo <= conf <= hi and why


def test_zone_line_split_into_two_cells():
    doc = _panel()
    zl = doc["lines"][1]
    zl["text"], zl["w"] = "Lv. 56", 60
    doc["lines"].insert(2, dict(zl, text="Polly's Forest", x=zl["x"] + 80, w=200))
    [f] = zoneinfo.parse(doc)
    assert f["value"]["zone_name"] == "Polly's Forest" and f["value"]["recommended_level"] == 56
    assert len(f["value"]["monsters"]) == 3


def test_dropped_separator_goes_to_review():
    doc = _panel(rows=[("Naga Fighter", "0.0450%"), ("Naga Shaman", "00520%"),
                       ("Naga Elder", "0.1200%")])
    f = zoneinfo.parse(doc)[0]
    assert f["conf"] < ocrauto.AUTO_COMMIT_MIN
    assert "Naga Shaman" in f["why"] and "dropped" in f["why"]


def test_zone_without_monsters_is_level_only_and_reviewed():
    f = zoneinfo.parse(_panel(rows=[]))[0]
    assert f["value"]["monsters"] == [] and f["conf"] < ocrauto.AUTO_COMMIT_MIN


def test_check_value():
    ok = {"zone_name": "X", "recommended_level": 56,
          "monsters": [{"name": "A", "xp_per_kill_pct": 0.1}]}
    assert zoneinfo.check_value(ok) == ok
    for bad in ({"zone_name": "X"}, dict(ok, recommended_level=99),
                dict(ok, monsters=[{"name": "A", "xp_per_kill_pct": 125}]),
                dict(ok, monsters=[{"name": "A", "xp_per_kill_pct": 0}])):
        with pytest.raises(ValueError):
            zoneinfo.check_value(bad)


# -- zone ids ---------------------------------------------------------------------

TABLE = [{"id": "polly-forest", "name": "Polly's Forest"},
         {"id": "gyfin-rhasia-temple", "name": "Gyfin Rhasia Temple"}]


def test_match_zone_exact_normalised_fuzzy_unmatched():
    assert zoneinfo.match_zone("polly's forest", TABLE) == ("polly-forest", "exact")
    assert zoneinfo.match_zone("Pollys Forest", TABLE)[0] == "polly-forest"
    assert zoneinfo.match_zone("Gyfin Rhasla Temple", TABLE) == ("gyfin-rhasia-temple", "fuzzy")
    assert zoneinfo.match_zone("Mirumok Ruins", TABLE) == (None, None)


# -- cap math ---------------------------------------------------------------------

@pytest.mark.parametrize("level,cap", [(55, 2.0), (62, 0.02), (66, 0.01), (3, 20.0), (30, None)])
def test_cap_per_band(level, cap):
    assert zoneinfo.cap_pct(CAPS, level) == cap


def test_cap_from_tracked_epoch_file_parses():
    ep = levels.load_epochs()
    caps = [c for e in ep for c in e.get("kill_xp_cap", [])]
    assert caps and all(zoneinfo.band_cap(c) is not None for c in caps)
    assert zoneinfo.band_cap({"cap_pct": 0.02, "note": "~5%"}) == 0.02


def test_superseded_band_is_skipped():
    caps = [{"level_min": 62, "level_max": 75, "note": "~0.01%", "superseded_by": ["x"]},
            {"level_min": 60, "level_max": 70, "note": "~0.5%"}]
    assert zoneinfo.cap_pct(caps, 62) == 0.5


def test_kill_math_cap_bound_and_effective():
    m = zoneinfo.kill_math(0.05, 100, 0.2)
    assert m == {"base": 0.05, "effective": 0.1, "cap_bound": False}
    m = zoneinfo.kill_math(0.15, 100, 0.2)
    assert m["effective"] == 0.2 and m["cap_bound"] is False   # buff hits the cap
    m = zoneinfo.kill_math(0.2, 100, 0.2)
    assert m["effective"] == 0.2 and m["cap_bound"] is True    # base already at cap
    assert zoneinfo.kill_math(0.3, 0, None)["cap_bound"] is False


def _row(level_at, monsters):
    return {"zone_name": "Z", "recommended_level": 60, "monsters": monsters,
            "level_at_read": level_at}


def test_zone_numbers_kills_to_level():
    row = _row(57, [{"name": "a", "xp_per_kill_pct": 0.05}, {"name": "b", "xp_per_kill_pct": 0.1}])
    n = zoneinfo.zone_numbers(row, 57, 40.0, 0, CAPS)
    assert n["current"] is True and n["cap_pct"] == 0.2
    assert n["effective_pct"] == pytest.approx(0.075)
    assert n["kills_to_level"] == 800 and n["next_level"] == 58
    assert n["cap_bound"] is False
    # +100 % stack: a 0.1 / 0.2, mean 0.15 -> 400 kills
    n = zoneinfo.zone_numbers(row, 57, 40.0, 100, CAPS)
    assert n["kills_to_level"] == 400


def test_zone_numbers_cap_bound_at_62():
    row = _row(62, [{"name": "a", "xp_per_kill_pct": 0.03}, {"name": "b", "xp_per_kill_pct": 0.02}])
    n = zoneinfo.zone_numbers(row, 62, 0.0, 200, CAPS)
    assert n["cap_bound"] is True and n["effective_pct"] == 0.02
    assert n["kills_to_level"] == 5000


def test_zone_numbers_other_band_says_reread():
    row = _row(55, [{"name": "a", "xp_per_kill_pct": 0.5}])
    n = zoneinfo.zone_numbers(row, 57, 10.0, 0, CAPS)
    assert n["current"] is False and n["reread_level"] == 57 and n["kills_to_level"] is None
    # same band (56-61): still current
    assert zoneinfo.zone_numbers(_row(56, row["monsters"]), 61, 10.0, 0, CAPS)["current"]


# -- service + pipeline -----------------------------------------------------------

class FakeGame:
    def __init__(self, shot_dir):
        self.shot_dir = shot_dir
        self.shots = []

    def add(self, name, ts, size=10):
        (self.shot_dir / name).write_bytes(b"x" * size)
        self.shots.insert(0, {"name": name, "size": size, "mtime": _iso(ts)})

    def view(self):
        return {"screenshots": [dict(s) for s in self.shots]}


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


EPOCHS = [{"id": "cap75-xp-rescale", "starts_utc": "2026-01-01T00:00:00+00:00", "label": "cap",
           "source": "test", "verified": False, "kill_xp_cap": CAPS}]


@pytest.fixture
def env(tmp_path):
    shot_dir = tmp_path / "ScreenShot"
    shot_dir.mkdir()
    game = FakeGame(shot_dir)
    docs = {}
    clock = Clock()
    store = Store(tmp_path / "store")
    g = grind.GrindService(store, clock=clock, presets=[])
    lev = leveling.LevelingService(store, clock=clock, epochs=EPOCHS, deadlines=[],
                                   books=xpbooks.XpBooksService(store, clock=clock),
                                   buffs=lambda: g.view()["buffs"])
    table = spots.load_table()
    spot = {"name": None}
    zones = zoneinfo.ZoneXpService(store, table, state=lev.xp_state,
                                   active_spot=lambda: spot["name"], clock=clock)
    lev.zone_here = zones.here
    reader = ocr.OcrService(game, tmp_path / "ocr", runner=lambda p: docs[p.name])
    auto = ocrauto.AutoOcr(store, game, reader, g, settings=lambda: {}, clock=clock,
                           leveling=lev, zones=zones)

    class Env:
        pass

    e = Env()
    e.__dict__.update(game=game, docs=docs, clock=clock, store=store, grind=g, lev=lev,
                      zones=zones, auto=auto, spot=spot, table=table)
    return e


def _run(e, shots, start=T0 - 7200):
    e.auto.on_game(None, "logged_in", start)
    for name, ts, doc in shots:
        e.game.add(name, ts)
        e.docs[name] = doc
    e.clock.t = max(ts for _, ts, _ in shots) + 60
    e.auto.scan()
    e.auto.drain()


def test_acceptance_shot_yields_zone_row(env):
    env.lev.ocr_sample(57, 40.0, T0 - 600)
    _run(env, [("z1.jpg", T0, _panel())])
    v = env.zones.view()
    assert v["level"] == 57 and v["cap_pct"] == 0.2
    [z] = v["zones"]
    assert z["key"] == "polly-forest" and z["zone_id"] == "polly-forest"
    assert z["unmatched"] is False and z["level_at_read"] == 57
    assert z["screenshot"] == "z1.jpg" and z["read_at"] == _iso(T0)
    assert z["current"] is True and z["kills_to_level"] == 830  # 60 % / mean 0.0723 %
    assert z["source"].startswith("in-game zone info 20")
    commits = [c for c in env.auto.view()["commits"] if c["kind"] == "zone_xp"]
    assert len(commits) == 1


def test_low_confidence_goes_to_review_never_committed(env):
    env.lev.ocr_sample(57, 40.0, T0 - 600)
    doc = _panel(rows=[("Polly Forest Fighter", "00450%")])
    _run(env, [("z1.jpg", T0, doc)])
    assert env.zones.view()["zones"] == []
    [r] = [r for r in env.auto.view()["review"] if r["kind"] == "zone_xp"]
    with pytest.raises(ValueError):  # a zone read is accept / discard only
        env.auto.review({"id": r["id"], "action": "fix", "value": r["value"]})
    with pytest.raises(ValueError):  # 450 % is no per-kill EXP: accept refuses
        env.auto.review({"id": r["id"], "action": "accept"})
    env.auto.review({"id": r["id"], "action": "discard"})
    assert env.zones.view()["zones"] == []


def test_zone_panel_is_never_a_level_read(env):
    # "Lv. 58 Polly's Forest" over a "0.120%" row would pair as a level sample
    doc = _panel(level=58, rows=[("Polly Forest Elder", "0.120%")])
    _run(env, [("z1.jpg", T0, doc)])
    assert [s for s in env.lev.samples() if s.get("source") == "ocr"] == []
    assert not [r for r in env.auto.view()["review"] if r["kind"] == "level"]
    assert env.zones.view()["zones"][0]["recommended_level"] == 58


def test_review_accept_commits(env):
    _run(env, [("z1.jpg", T0, _panel(split=True))])
    [r] = [r for r in env.auto.view()["review"] if r["kind"] == "zone_xp"]
    env.auto.review({"id": r["id"], "action": "accept"})
    assert [z["key"] for z in env.zones.view()["zones"]] == ["polly-forest"]


def test_unknown_zone_stays_unmatched(env):
    _run(env, [("z1.jpg", T0, _panel(zone="Nowhere Hollow"))])
    [z] = env.zones.view()["zones"]
    assert z["unmatched"] is True and z["zone_id"] is None and z["key"] == "name:nowhere hollow"
    assert env.zones.by_spot() == {}


def test_newer_read_replaces_older_and_undo_restores(env):
    env.lev.ocr_sample(57, 40.0, T0 - 600)
    _run(env, [("z1.jpg", T0, _panel())])
    newer = _panel(rows=[("Polly Forest Fighter", "0.0900%")])
    _run(env, [("z2.jpg", T0 + 600, newer)])
    [z] = env.zones.view()["zones"]
    assert z["screenshot"] == "z2.jpg" and z["monsters"][0]["xp_per_kill_pct"] == 0.09
    # an older shot read late never overwrites the newer row: it waits in review
    _run(env, [("z0.jpg", T0 - 300, _panel())])
    assert env.zones.view()["zones"][0]["screenshot"] == "z2.jpg"
    assert any(r["kind"] == "zone_xp" and "newer" in r["why"] for r in env.auto.view()["review"])
    uid = next(c["id"] for c in env.auto.view()["commits"] if c["file"] == "z2.jpg")
    env.auto.undo(uid)
    assert env.zones.view()["zones"][0]["screenshot"] == "z1.jpg"


def test_read_at_other_level_keeps_level_at_read(env):
    env.lev.ocr_sample(55, 40.0, T0 - 600)
    _run(env, [("z1.jpg", T0, _panel())])
    env.lev.ocr_sample(57, 10.0, T0 + 600)
    [z] = env.zones.view()["zones"]
    assert z["level_at_read"] == 55 and z["current"] is False and z["reread_level"] == 57


def test_cap_bound_flips_leveling_card(env):
    env.lev.ocr_sample(62, 10.0, T0 - 600)
    doc = _panel(zone="Gyfin Rhasia Temple", level=62,
                 rows=[("Gyfin Rhasia Lunar", "0.0300%"), ("Gyfin Rhasia Solar", "0.0250%")])
    _run(env, [("z1.jpg", T0, doc)])
    assert env.lev.view()["zone_cap"] is None   # no grind session: no line
    env.spot["name"] = "Gyfin Rhasia Temple"
    zc = env.lev.view()["zone_cap"]
    assert zc["cap_bound"] is True and zc["zone_name"] == "Gyfin Rhasia Temple"
    assert env.zones.cap_bound_here() is True
    env.spot["name"] = "Polly's Forest"
    assert env.lev.view()["zone_cap"] is None


def test_spots_panel_level_wins_and_kills_text(env):
    env.lev.ocr_sample(57, 40.0, T0 - 600)
    _run(env, [("z1.jpg", T0, _panel(level=58))])
    table = [r for r in env.table if r["id"] in ("polly-forest", "desert-naga-temple")]
    svc = spots.SpotsService(table, character=lambda: {"level": 57, "gs": {"ap": 300, "dp": 400}},
                             grind=lambda: {})
    svc.zones = env.zones.by_spot
    out = svc.view({"goal": ["xp"]})
    assert [r["id"] for r in out["top"]] == ["desert-naga-temple"]  # in-game Lv 58 > 57
    [p] = out["unlocks"]
    assert p["id"] == "polly-forest" and p["need_level"] == 1
    assert p["level_min"] == 58 and p["level_min_community"] == 50
    assert p["level_source"].startswith("in-game zone info")
    assert p["zone_xp"]["kills_to_level"] == 830 and p["zone_xp"]["next_level"] == 58
    assert p["zone_xp"]["recommended_level"] == 58
    assert out["top"][0]["zone_xp"] is None
    out = svc.view({"goal": ["xp"], "level": ["58"]})
    assert "polly-forest" in {r["id"] for r in out["top"]}
    assert spots.SpotsService(table, character=lambda: {}, grind=lambda: {})._table({}) == table


def test_whatnow_never_suggests_xp_buff_when_cap_bound():
    now = dt.datetime.fromtimestamp(T0, dt.timezone.utc)
    ends = _iso(T0 + 600)
    view = {"active": {"spot": "x"}, "buffs": [
        {"name": "Book of Combat", "ends": ends, "xp_pct": 50},
        {"name": "Elixir", "ends": ends, "xp_pct": None}]}
    assert {c["text"] for c in whatnow.from_grind(view, now)} == \
        {"Re-arm Book of Combat", "Re-arm Elixir"}
    out = whatnow.from_grind(dict(view, zone_cap_bound=True), now)
    assert [c["text"] for c in out] == ["Re-arm Elixir"]
    # the seeded "XP scroll" carries no xp_pct until typed: its name marks it
    # every seeded buff (grind.SEED_BUFFS) with no xp_pct, plus a preset-named one
    seeded = {"active": {"spot": "x"}, "xp_presets": [{"name": "Body Enhancement"}],
              "buffs": [{"name": n, "ends": ends, "xp_pct": None, "xp_hint": None}
                        for n in grind.SEED_BUFFS + ("Body Enhancement",)]}
    out = whatnow.from_grind(dict(seeded, zone_cap_bound=True), now)
    got = {c["text"] for c in out}
    assert "Re-arm XP scroll" not in got and "Re-arm Hot Time" not in got
    assert "Re-arm Body Enhancement" not in got and "Re-arm Drop rate scroll" in got
    assert len(whatnow.from_grind(seeded, now)) == len(grind.SEED_BUFFS) + 1
    # the Hot Time nudge is dropped too while the session's zone is cap-bound
    lev = {"hot": {"active": [{"pct": 50, "ends_in_s": 600, "label": "Hot Time"}]}}
    assert len(whatnow.hot_time(lev, now)) == 1
    assert whatnow.hot_time(dict(lev, zone_cap={"cap_bound": True}), now) == []
    assert len(whatnow.hot_time(dict(lev, zone_cap={"cap_bound": False}), now)) == 1


def test_labelled_value_cell_is_never_a_monster():
    # the value cell sits 1 px above its name and carries its own label
    doc = _panel(split=True)
    for ln in doc["lines"]:
        if ln["text"].endswith("%"):
            ln["text"] = "Combat EXP " + ln["text"]
            ln["y"] -= 1
    f = zoneinfo.parse(doc)[0]
    assert [m["name"] for m in f["value"]["monsters"]] == [
        "Polly Forest Fighter", "Polly Forest Shaman", "Polly Forest Elder"]
    doc = _panel(rows=[])
    doc["lines"].append({"text": "Combat EXP 0.0450%", "x": 440, "y": 210, "w": 200, "h": 18})
    assert zoneinfo.parse(doc)[0]["value"]["monsters"] == []


def test_prune_keeps_the_row_just_written(tmp_path, monkeypatch):
    monkeypatch.setattr(zoneinfo, "MAX_ZONES", 2)
    z = zoneinfo.ZoneXpService(Store(tmp_path / "s"), TABLE)
    v = {"recommended_level": 56, "monsters": []}
    z.commit(dict(v, zone_name="A Zone"), "a.jpg", T0, 56)
    z.commit(dict(v, zone_name="B Zone"), "b.jpg", T0 + 10, 56)
    key, _ = z.commit(dict(v, zone_name="Old Zone"), "o.jpg", T0 - 999, 56)
    keys = {r["key"] for r in z.view()["zones"]}
    assert key in keys and keys == {"name:old zone", "name:b zone"}
    z.restore(key, None, _iso(T0 - 999), "o.jpg")
    assert {r["key"] for r in z.view()["zones"]} == {"name:b zone"}


def test_no_network(env, monkeypatch):
    import socket

    def refuse(*a, **k):
        raise AssertionError("plan 088 must not touch the network")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    env.lev.ocr_sample(57, 40.0, T0 - 600)
    _run(env, [("z1.jpg", T0, _panel())])
    env.spot["name"] = "Polly's Forest"
    assert env.zones.view()["zones"] and env.lev.view()["zone_cap"] is not None


def test_level_at_read_unknown_without_an_older_sample(env):
    _run(env, [("z1.jpg", T0, _panel())])
    env.lev.ocr_sample(57, 40.0, T0 + 600)
    [z] = env.zones.view()["zones"]
    assert z["level_at_read"] is None and z["current"] is False and z["reread_level"] == 57


# -- route ------------------------------------------------------------------------

@pytest.fixture()
def srv(tmp_path):
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          market_seed=[], profile_cfg={})
    t = threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    t.start()
    yield s
    s.shutdown()
    s.server_close()


def _get(s, path):
    c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=5)
    c.request("GET", path)
    r = c.getresponse()
    body = json.loads(r.read().decode("utf-8"))
    c.close()
    return r.status, body


def test_route_zones_xp(srv):
    code, body = _get(srv, "/api/zones/xp")
    assert code == 200 and body["zones"] == [] and set(body) >= {"zones", "level", "cap_pct"}
    srv.zones.commit({"zone_name": "Polly's Forest", "recommended_level": 56,
                      "monsters": [{"name": "a", "xp_per_kill_pct": 0.05}]}, "z.jpg", T0, None)
    code, body = _get(srv, "/api/zones/xp")
    assert code == 200 and body["zones"][0]["key"] == "polly-forest"
    code, body = _get(srv, "/api/whatnow")
    assert code == 200
    code, body = _get(srv, "/api/leveling")
    assert code == 200 and "zone_cap" in body
