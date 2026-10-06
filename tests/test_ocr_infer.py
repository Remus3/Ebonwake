"""Plan 066: progress inference from screenshot OCR - level / XP %, AP / AAP /
DP, sanity over history, buff timers, silver/h, book-use suggestion. Stub OCR
docs only; nothing runs Tesseract or PowerShell."""

import datetime as dt
import json
import struct

import pytest

from server.ew import grind, leveling, ocr, ocrauto, ocrinfer, progress, xpbooks
from server.ew.store import Store

T0 = 1_790_000_000.0


def _iso(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).replace(microsecond=0).isoformat()


def _ln(text, y=0, x=0):
    return {"text": text, "x": x, "y": y, "w": 100, "h": 10}


def _doc(*texts):
    lines = [_ln(t, y=20 * i) for i, t in enumerate(texts)]
    return {"text": "\n".join(texts), "lines": lines}


# -- extractors -------------------------------------------------------------------

@pytest.mark.parametrize("text,level,pct,lo,hi", [
    ("Lv 61 12.500%", 61, 12.5, 0.9, 1.0),
    ("Lv.62  0.400 %", 62, 0.4, 0.9, 1.0),
    ("Shooty Lv 61 13.105%", 61, 13.105, 0.9, 1.0),
    ("LV 70 99.999%", 70, 99.999, 0.9, 1.0),
    ("Level 75 100.000%", 75, 100, 0.9, 1.0),
    ("Lv 61 12,500%", 61, 12.5, 0.6, 0.89),   # decimal comma: a misread dot
    ("Lv 61 12%", 61, 12, 0.6, 0.89),         # BDO prints 3 decimals
])
def test_level_field(text, level, pct, lo, hi):
    f = ocrinfer.level_field(_doc(text))
    assert f["kind"] == "level" and f["value"] == {"level": level, "pct": pct}
    assert lo <= f["conf"] <= hi, f
    assert f["why"]


@pytest.mark.parametrize("texts", [
    ("Lv 99 12.500%",), ("Lv 61 112.500%",), ("12.500%",), ("Silver 1,234,567",),
    ("Lv 61",), ("Lv 0 5.000%",),
])
def test_level_field_none(texts):
    assert ocrinfer.level_field(_doc(*texts)) is None


def test_level_pct_from_neighbour_line_is_below_default_gate():
    f = ocrinfer.level_field(_doc("Lv 61", "12.500%"))
    assert f["value"] == {"level": 61, "pct": 12.5}
    assert f["conf"] < ocrauto.AUTO_COMMIT_MIN


def test_level_region_breaks_a_tie():
    # A nameplate "Lv 58 3.000%" mid-screen and the HUD read top-left: the hit
    # inside the tracked level region wins when the frame size is known.
    doc = {"text": "", "lines": [_ln("Lv 58 3.000%", y=500, x=900),
                                  _ln("Lv 61 12.500%", y=20, x=40)]}
    regions = ocrinfer.load_regions()
    f = ocrinfer.level_field(doc, size=(1920, 1080), regions=regions)
    assert f["value"]["level"] == 61
    assert ocrinfer.level_field(doc)["value"]["level"] == 58  # no size: first read wins


def test_gear_fields():
    fs = ocrinfer.gear_fields(_doc("AP 296", "Awakening AP 300", "DP 380"))
    assert {(f["name"], f["value"]) for f in fs} == {("ap", 296), ("aap", 300), ("dp", 380)}
    assert all(f["kind"] == "gear" and f["conf"] >= 0.9 for f in fs)
    fs = ocrinfer.gear_fields(_doc("AAP: 301  DP: 381"))
    assert {(f["name"], f["value"]) for f in fs} == {("aap", 301), ("dp", 381)}
    assert ocrinfer.gear_fields(_doc("AP 1296", "DP", "Map 300")) == []


def test_regions_file_is_relative_and_unverified():
    doc = ocrinfer.load_regions()
    assert doc["scales"], doc
    for row in doc["scales"]:
        assert row["verified"] is False
        for box in row["regions"].values():
            assert len(box) == 4 and all(0 <= v <= 1 for v in box) and box[0] < box[2] \
                and box[1] < box[3]


def test_image_size_png_jpeg_bmp(tmp_path):
    png = tmp_path / "a.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR"
                    + struct.pack(">II", 1920, 1080) + b"\x08\x02\x00\x00\x00")
    assert ocrinfer.image_size(png) == (1920, 1080)
    jpg = tmp_path / "a.jpg"
    jpg.write_bytes(b"\xff\xd8" + b"\xff\xe0" + struct.pack(">H", 4) + b"JF"
                    + b"\xff\xc0" + struct.pack(">HBHH", 11, 8, 1440, 2560) + b"\x03" * 6)
    assert ocrinfer.image_size(jpg) == (2560, 1440)
    bmp = tmp_path / "a.bmp"
    bmp.write_bytes(b"BM" + b"\x00" * 12 + struct.pack("<Iii", 40, 1920, -1080))
    assert ocrinfer.image_size(bmp) == (1920, 1080)
    junk = tmp_path / "a.bin"
    junk.write_bytes(b"nope")
    assert ocrinfer.image_size(junk) is None
    assert ocrinfer.image_size(tmp_path / "missing.jpg") is None


# -- sanity over history -----------------------------------------------------------

def _s(ts, level, pct, source=None):
    d = {"ts": _iso(ts), "level": level, "pct": pct}
    if source:
        d["source"] = source
    return d


def test_check_level_rules():
    hist = [_s(T0, 61, 12.5, "ocr")]
    assert ocrinfer.check_level(hist, 61, 13.1, T0 + 600) is None
    assert ocrinfer.check_level(hist, 62, 0.4, T0 + 600) is None          # level-up reset
    assert "back" in ocrinfer.check_level(hist, 61, 12.0, T0 + 600)        # XP went back
    assert "below" in ocrinfer.check_level(hist, 60, 50.0, T0 + 600)       # level went down
    assert "older" in ocrinfer.check_level(hist, 61, 13.0, T0 - 60)        # shot before it
    assert ocrinfer.check_level([], 61, 1.0, T0) is None
    # a profile marker (pct unknown) above the read level holds it too
    mark = [{"ts": _iso(T0), "level": 63, "pct": None, "source": "profile"}]
    assert "below" in ocrinfer.check_level(mark, 62, 10.0, T0 + 60)
    assert ocrinfer.check_level(mark, 63, 10.0, T0 + 60) is None


def test_silver_rate_inside_one_session():
    samples = [{"at": _iso(T0 - 7200), "value": 1, "source": "ocr:x.jpg"},
               {"at": _iso(T0), "value": 1_000_000, "source": "ocr:a.jpg"},
               {"at": _iso(T0 + 1800), "value": 51_000_000, "source": "ocr:b.jpg"}]
    r = ocrinfer.silver_rate(samples, {"id": "p3", "start": _iso(T0 - 60), "end": None})
    assert r == {"session": "p3", "per_h": 100_000_000, "span_s": 1800, "n": 2}
    assert ocrinfer.silver_rate(samples[:2], {"id": "p3", "start": _iso(T0 - 60),
                                              "end": None}) is None
    assert ocrinfer.silver_rate(samples, None) is None


# -- pipeline (AutoOcr + leveling / progress / books) -----------------------------

class FakeGame:
    def __init__(self, shot_dir):
        self.shot_dir = shot_dir
        self.shots = []
        self.listeners = []

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


class FakePlay:
    def __init__(self):
        self.doc = {"open": None, "last": None}

    def view(self):
        return self.doc


@pytest.fixture
def env(tmp_path):
    shot_dir = tmp_path / "ScreenShot"
    shot_dir.mkdir()
    game = FakeGame(shot_dir)
    docs = {}
    clock = Clock()
    store = Store(tmp_path / "store")
    g = grind.GrindService(store, clock=clock, presets=[])
    books = xpbooks.XpBooksService(store, clock=clock)
    lev = leveling.LevelingService(store, clock=clock, epochs=[], deadlines=[], books=books)
    prog = progress.ProgressService(store, None, clock=clock)
    reader = ocr.OcrService(game, tmp_path / "ocr", runner=lambda p: docs[p.name])
    play = FakePlay()
    auto = ocrauto.AutoOcr(store, game, reader, g, settings=lambda: {}, clock=clock,
                           leveling=lev, progress=prog, play=play.view)

    class Env:
        pass

    e = Env()
    e.__dict__.update(game=game, docs=docs, clock=clock, store=store, grind=g, books=books,
                      lev=lev, prog=prog, auto=auto, play=play)
    return e


def _run(e, shots, start=T0 - 7200):
    """shots: [(name, ts, doc)] -> run them through scan / drain with now = last ts + 60."""
    e.auto.on_game(None, "logged_in", start)
    for name, ts, doc in shots:
        e.game.add(name, ts)
        e.docs[name] = doc
    e.clock.t = max(ts for _, ts, _ in shots) + 60
    e.auto.scan()
    e.auto.drain()


def _ocr_samples(e):
    return [s for s in e.lev.samples() if s.get("source") == "ocr"]


def test_acceptance_four_shot_series(env):
    """Lv 61 12.5 % -> 13.1 % -> Lv 62 0.4 % -> stale 12 %: 3 accepted, 1 queued,
    and a level ETA with no typing."""
    _run(env, [("s1.jpg", T0, _doc("Lv 61 12.500%")),
               ("s2.jpg", T0 + 1200, _doc("Lv 61 13.100%")),
               ("s3.jpg", T0 + 3600 * 30, _doc("Lv 62 0.400%")),
               ("s4.jpg", T0 + 3600 * 30 + 600, _doc("Lv 61 12.000%"))])
    got = [(s["level"], s["pct"]) for s in _ocr_samples(env)]
    assert got == [(61, 12.5), (61, 13.1), (62, 0.4)]
    review = [r for r in env.auto.view()["review"] if r["kind"] == "level"]
    assert len(review) == 1 and review[0]["file"] == "s4.jpg"
    assert review[0]["value"] == {"level": 61, "pct": 12}
    assert "below" in review[0]["why"]
    v = env.lev.view()
    assert v["level"] == 62 and v["pct"] == 0.4
    assert v["rate_pct_h"] and v["eta_next_s"] and v["eta_next_s"] > 0
    commits = [c for c in env.auto.view()["commits"] if c["kind"] == "level"]
    assert len(commits) == 3


def test_ocr_sample_ts_is_the_shot_time_and_source_is_tagged(env):
    _run(env, [("a.jpg", T0, _doc("Lv 61 12.500%"))])
    s = _ocr_samples(env)
    assert s == [{"ts": _iso(T0), "level": 61, "pct": 12.5, "source": "ocr"}]
    assert env.lev.view()["samples"][0]["source"] == "ocr"


def test_non_monotonic_sample_queued_then_accept_commits(env):
    _run(env, [("a.jpg", T0, _doc("Lv 61 20.000%")), ("b.jpg", T0 + 600, _doc("Lv 61 19.000%"))])
    assert [s["pct"] for s in _ocr_samples(env)] == [20]
    item = next(r for r in env.auto.view()["review"] if r["kind"] == "level")
    assert "back" in item["why"]
    env.auto.review({"id": item["id"], "action": "accept"})  # e.g. a death penalty: real
    assert [s["pct"] for s in _ocr_samples(env)] == [20, 19]


def test_level_review_fix_and_undo(env):
    _run(env, [("a.jpg", T0, _doc("Lv 61 12%"))])  # no decimals: below the gate
    item = next(r for r in env.auto.view()["review"] if r["kind"] == "level")
    env.auto.review({"id": item["id"], "action": "fix", "value": {"level": 61, "pct": 12.345}})
    assert [(s["level"], s["pct"]) for s in _ocr_samples(env)] == [(61, 12.345)]
    uid = next(c["id"] for c in env.auto.view()["commits"] if c["kind"] == "level")
    env.auto.undo(uid)
    assert _ocr_samples(env) == []
    for i, bad in enumerate(({"level": 99, "pct": 1}, {"level": 61}, 5,
                             {"level": 61, "pct": 101})):
        _run(env, [(f"x{i}.jpg", T0 + 5 + i, _doc("Lv 61 12%"))])
        it = next(r for r in env.auto.view()["review"] if r["kind"] == "level")
        with pytest.raises(ValueError):
            env.auto.review({"id": it["id"], "action": "fix", "value": bad})
        env.auto.review({"id": it["id"], "action": "discard"})


def test_manual_entry_wins_over_an_older_ocr_sample(env):
    env.clock.t = T0 + 3600
    env.lev.sample({"level": 61, "pct": 40})        # typed now
    _run(env, [("old.jpg", T0, _doc("Lv 61 45.000%"))])  # shot an hour before it
    assert _ocr_samples(env) == []
    item = next(r for r in env.auto.view()["review"] if r["kind"] == "level")
    assert "older" in item["why"]
    v = env.lev.view()
    assert (v["level"], v["pct"]) == (61, 40)
    # a typed sample in the same second as an OCR sample replaces it
    env.clock.t = T0 + 7200
    _run(env, [("new.jpg", T0 + 7200, _doc("Lv 61 45.000%"))])
    assert [s["pct"] for s in _ocr_samples(env)] == [45]
    env.clock.t = T0 + 7200
    env.lev.sample({"level": 61, "pct": 46})
    assert _ocr_samples(env) == []
    assert env.lev.view()["pct"] == 46


def test_buff_timer_started_from_the_same_shot(env):
    _run(env, [("a.jpg", T0, _doc("Lv 61 12.500%", "XP scroll 30 min"))])
    buff = next(b for b in env.grind.view()["buffs"] if b["name"] == "XP scroll")
    assert buff["left_s"] == 29 * 60
    assert len(_ocr_samples(env)) == 1


def test_gear_update_with_source_tag_and_undo(env):
    _run(env, [("gear.jpg", T0, _doc("AP 296", "Awakening AP 300", "DP 380"))])
    ch = env.prog.view(refresh=False)["character"]
    assert ch["gs"] == {"ap": 296, "aap": 300, "dp": 380}
    assert {k: v["source"] for k, v in ch["gs_src"].items()} == {"ap": "ocr", "aap": "ocr",
                                                                "dp": "ocr"}
    uid = next(c["id"] for c in env.auto.view()["commits"] if c["name"] == "dp")
    env.auto.undo(uid)
    ch = env.prog.view(refresh=False)["character"]
    assert ch["gs"]["dp"] is None and "dp" not in ch["gs_src"]


def test_gear_typed_after_the_shot_wins(env):
    env.clock.t = T0 + 600
    env.prog.set_character({"gs": {"ap": 290}})
    assert env.prog.view(refresh=False)["character"]["gs_src"]["ap"]["source"] == "typed"
    _run(env, [("gear.jpg", T0, _doc("AP 296", "DP 380"))])
    ch = env.prog.view(refresh=False)["character"]
    assert ch["gs"]["ap"] == 290 and ch["gs"]["dp"] == 380
    item = next(r for r in env.auto.view()["review"] if r["kind"] == "gear")
    assert item["name"] == "ap" and "typed" in item["why"]
    env.auto.review({"id": item["id"], "action": "accept"})  # the operator's call wins
    assert env.prog.view(refresh=False)["character"]["gs"]["ap"] == 296


def test_gear_from_an_older_shot_never_overwrites_a_newer_read(env):
    env.prog.gs_ocr("ap", 300, _iso(T0 + 600))
    _run(env, [("old.jpg", T0, _doc("AP 296"))])
    assert env.prog.view(refresh=False)["character"]["gs"]["ap"] == 300
    item = next(r for r in env.auto.view()["review"] if r["kind"] == "gear")
    assert "after this screenshot" in item["why"]


def test_book_use_suggested_after_a_book_was_just_added(env):
    env.clock.t = T0 - 600
    env.books.add({"size": "large", "n": 1})
    _run(env, [("a.jpg", T0, _doc("Lv 61 10.000%")),
               ("b.jpg", T0 + 300, _doc("Lv 61 17.600%"))])
    assert [s["pct"] for s in _ocr_samples(env)] == [10, 17.6]
    item = next(r for r in env.auto.view()["review"] if r["kind"] == "book_use")
    assert item["value"] == {"size": "large", "pct_before": 10, "pct_after": 17.6}
    env.auto.review({"id": item["id"], "action": "accept"})
    used = env.books.view(61, 17.6, None)["used"]
    assert used[0]["size"] == "large" and used[0]["pct_after"] == 17.6
    assert env.books.view(61, 17.6, None)["owned"]["large"] == 0
    uid = next(c["id"] for c in env.auto.view()["commits"] if c["kind"] == "book_use")
    env.auto.undo(uid)
    assert env.books.view(61, 17.6, None)["used"] == []
    assert env.books.view(61, 17.6, None)["owned"]["large"] == 1


def test_no_book_suggestion_without_a_recent_book(env):
    _run(env, [("a.jpg", T0, _doc("Lv 61 10.000%")),
               ("b.jpg", T0 + 300, _doc("Lv 61 17.600%"))])
    assert not [r for r in env.auto.view()["review"] if r["kind"] == "book_use"]


def test_no_book_suggestion_for_an_ordinary_gain(env):
    env.clock.t = T0 - 600
    env.books.add({"size": "large", "n": 1})
    _run(env, [("a.jpg", T0, _doc("Lv 61 10.000%")),
               ("b.jpg", T0 + 300, _doc("Lv 61 10.300%"))])
    assert not [r for r in env.auto.view()["review"] if r["kind"] == "book_use"]


def test_silver_per_hour_in_the_open_play_session(env):
    env.play.doc = {"open": {"id": "p2", "start": _iso(T0 - 600), "spot": None, "auto": True},
                    "last": None}
    _run(env, [("a.jpg", T0, _doc("Silver 1,000,000")),
               ("b.jpg", T0 + 1800, _doc("Silver 6,000,000"))])
    assert env.auto.view()["silver_h"] == {"session": "p2", "per_h": 10_000_000,
                                           "span_s": 1800, "n": 2}
    env.play.doc = {"open": None, "last": {"id": "p2", "start": _iso(T0 - 600),
                                           "end": _iso(T0 + 3600), "spot": None, "auto": True}}
    assert env.auto.view()["silver_h"]["per_h"] == 10_000_000  # the last session when none is open


def test_without_services_the_plan_063_fields_only(tmp_path):
    shot_dir = tmp_path / "ScreenShot"
    shot_dir.mkdir()
    game = FakeGame(shot_dir)
    clock = Clock()
    store = Store(tmp_path / "store")
    g = grind.GrindService(store, clock=clock, presets=[])
    reader = ocr.OcrService(game, tmp_path / "ocr", runner=lambda p: _doc("Lv 61 12.500%",
                                                                            "AP 296"))
    auto = ocrauto.AutoOcr(store, game, reader, g, clock=clock)
    auto.on_game(None, "logged_in", T0 - 600)
    game.add("a.jpg", T0 - 60)
    auto.scan()
    auto.drain()
    v = auto.view()
    assert v["review"] == [] and v["commits"] == [] and v["silver_h"] is None


def test_leveling_clean_sample_keeps_ocr_source_only():
    ok = leveling._clean_sample({"ts": _iso(T0), "level": 61, "pct": 1.5, "source": "ocr"})
    assert ok["source"] == "ocr"
    other = leveling._clean_sample({"ts": _iso(T0), "level": 61, "pct": 1.5, "source": "x"})
    assert "source" not in other


def test_regions_data_file_is_ascii_json():
    raw = ocrinfer.REGIONS_FILE.read_bytes()
    raw.decode("ascii")
    assert b"\r" not in raw
    json.loads(raw)
