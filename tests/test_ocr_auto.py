"""Plan 063: auto-OCR of screenshots taken while logged in, confidence-gated
commit + review queue. Stub engines only; nothing runs Tesseract or PowerShell."""

import datetime as dt
import http.client
import json
import random
import threading

import pytest

from server.ew import app as ewapp
from server.ew import grind, market, ocr, ocrauto, settings
from server.ew.store import Store

T0 = 1_790_000_000.0


def _iso(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).replace(microsecond=0).isoformat()


def _ln(text, y=0):
    return {"text": text, "x": 0, "y": y, "w": 100, "h": 10}


def _doc(*texts):
    lines = [_ln(t, y=20 * i) for i, t in enumerate(texts)]
    return {"text": "\n".join(texts), "lines": lines}


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

    def stop(self):
        pass


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def env(tmp_path):
    shot_dir = tmp_path / "ScreenShot"
    shot_dir.mkdir()
    game = FakeGame(shot_dir)
    docs, calls = {}, []

    def runner(path):
        calls.append(path.name)
        return docs[path.name]

    clock = Clock()
    store = Store(tmp_path / "store")
    g = grind.GrindService(store, clock=clock, presets=[])
    reader = ocr.OcrService(game, tmp_path / "ocr", runner=runner)
    cfg = {}
    changes = []
    auto = ocrauto.AutoOcr(store, game, reader, g, settings=lambda: dict(cfg), clock=clock,
                           on_change=lambda: changes.append(1))

    class Env:
        pass

    e = Env()
    e.__dict__.update(game=game, docs=docs, calls=calls, clock=clock, store=store, grind=g,
                      reader=reader, auto=auto, cfg=cfg, changes=changes, tmp=tmp_path)
    return e


def _login(e, start, end=None):
    e.auto.on_game(None, "logged_in", start)
    if end is not None:
        e.auto.on_game("logged_in", "running", end)


def _buff(e, name):
    return next(b for b in e.grind.view()["buffs"] if b["name"] == name)


CLEAN = _doc("Silver 1,234,567", "XP scroll 30 min")
NOISY = _doc("Silver 1 234 567", "Value Pack 2 hr 75 min")


# -- trigger -------------------------------------------------------------------

def test_shot_while_logged_in_is_enqueued_closed_is_not(env):
    _login(env, T0 - 1000, T0 - 500)
    env.game.add("before.jpg", T0 - 1500)   # game closed
    env.game.add("during.jpg", T0 - 800)    # logged in
    env.game.add("grace.jpg", T0 - 450)     # 50 s after logout
    env.game.add("late.jpg", T0 - 400)      # 100 s after logout
    for n in ("before.jpg", "during.jpg", "grace.jpg", "late.jpg"):
        env.docs[n] = CLEAN
    assert env.auto.scan() == 2
    assert env.auto.drain() == 2
    assert sorted(env.calls) == ["during.jpg", "grace.jpg"]
    assert env.auto.scan() == 0 and env.auto.drain() == 0  # idempotent: seen shots stay seen


def test_no_login_window_nothing_is_read(env):
    env.game.add("a.jpg", T0 - 10)
    env.docs["a.jpg"] = CLEAN
    assert env.auto.scan() == 0
    _login(env, T0)  # a later login never reaches back to an older shot
    assert env.auto.scan() == 0 and env.calls == []


def test_auto_off_reads_nothing(env):
    env.cfg["ocr.auto"] = False
    _login(env, T0 - 100)
    env.game.add("a.jpg", T0 - 10)
    env.docs["a.jpg"] = CLEAN
    assert env.auto.scan() == 0 and env.auto.drain() == 0 and env.calls == []


def test_shot_listed_while_still_being_written_is_read_once_when_complete(env):
    """QA 009 (2026-10-07): BDO creates the PNG at 0 bytes and writes ~19 MB;
    the watcher listed it mid-write, the partial file was OCR'd (junk) and the
    finished one again (two reads, two cap slots). A 0-byte listing is not
    queued nor marked seen; the finished listing is read once."""
    _login(env, T0 - 100)
    env.game.add("w.png", T0 - 10, size=0)
    env.docs["w.png"] = CLEAN
    assert env.auto.scan() == 0 and env.auto.drain() == 0 and env.calls == []
    env.game.shots.clear()
    env.game.add("w.png", T0 - 9, size=50)
    assert env.auto.scan() == 1 and env.auto.drain() == 1
    assert env.calls == ["w.png"]
    assert env.store.get("ocr_auto")["count"] == 1


def test_shot_still_growing_at_read_time_is_not_read_or_counted(env):
    """Listed at a partial size, larger on disk when the worker reaches it: no
    OCR of the partial image, no cap slot; the re-listed final size is read."""
    _login(env, T0 - 100)
    env.game.add("g.png", T0 - 10, size=20)
    env.docs["g.png"] = CLEAN
    (env.game.shot_dir / "g.png").write_bytes(b"x" * 80)  # still being written
    assert env.auto.scan() == 1 and env.auto.drain() == 1
    assert env.calls == [] and env.store.get("ocr_auto").get("count", 0) == 0
    env.game.shots.clear()
    env.game.add("g.png", T0 - 9, size=80)
    assert env.auto.scan() == 1 and env.auto.drain() == 1
    assert env.calls == ["g.png"] and env.store.get("ocr_auto")["count"] == 1


def test_daily_cap_respected(env):
    env.cfg["ocr.daily_cap"] = 2
    _login(env, T0 - 1000)
    for i in range(3):
        env.game.add(f"s{i}.jpg", T0 - 100 + i)
        env.docs[f"s{i}.jpg"] = CLEAN
    env.auto.scan()
    env.auto.drain()
    assert len(env.calls) == 2
    assert env.auto.view()["today"] == 2
    env.game.add("s9.jpg", T0 - 5)
    env.docs["s9.jpg"] = CLEAN
    assert env.auto.scan() == 0


def test_restart_does_not_reread_or_recommit(env):
    _login(env, T0 - 100)
    env.game.add("a.jpg", T0 - 10)
    env.docs["a.jpg"] = CLEAN
    env.auto.scan()
    env.auto.drain()
    again = ocrauto.AutoOcr(env.store, env.game, env.reader, env.grind, clock=env.clock)
    again.on_game(None, "logged_in", T0 - 100)
    assert again.scan() == 0
    assert len(env.store.get("silver")["samples"]) == 1


# -- commit gate ----------------------------------------------------------------

def test_high_conf_silver_committed_with_undo_low_conf_queued(env):
    _login(env, T0 - 1000)
    env.game.add("good.jpg", T0 - 60)
    env.game.add("bad.jpg", T0 - 30)
    env.docs["good.jpg"] = _doc("Silver 1,234,567")
    env.docs["bad.jpg"] = _doc("Silver 1 234 568")
    env.auto.scan()
    env.auto.drain()
    samples = env.store.get("silver")["samples"]
    assert [(s["value"], s["source"]) for s in samples] == [(1234567, "ocr:good.jpg")]
    v = env.auto.view()
    assert [(r["kind"], r["value"], r["file"]) for r in v["review"]] == [
        ("silver", 1234568, "bad.jpg")]
    assert v["review"][0]["conf"] < 0.9 and v["review"][0]["why"]
    uid = v["commits"][0]["id"]
    out = env.auto.undo(uid)
    assert env.store.get("silver")["samples"] == []
    assert out["commits"] == []
    with pytest.raises(ValueError):
        env.auto.undo(uid)


def test_buff_commit_counts_time_since_shot_and_undo_restores(env):
    _login(env, T0 - 1000)
    env.game.add("b.jpg", T0 - 600)  # 10 min ago
    env.docs["b.jpg"] = _doc("XP scroll 30 min")
    env.auto.scan()
    env.auto.drain()
    b = _buff(env, "XP scroll")
    assert b["left_s"] == 20 * 60
    uid = env.auto.view()["commits"][0]["id"]
    env.auto.undo(uid)
    assert _buff(env, "XP scroll")["left_s"] is None


def test_buff_undo_restores_previous_timer(env):
    env.grind.buff({"name": "Hot Time", "minutes": 90})
    before = _buff(env, "Hot Time")["ends"]
    _login(env, T0 - 1000)
    env.game.add("b.jpg", T0 - 5)
    env.docs["b.jpg"] = _doc("Hot Time 40 min")
    env.auto.scan()
    env.auto.drain()
    assert _buff(env, "Hot Time")["left_s"] == 40 * 60
    env.auto.undo(env.auto.view()["commits"][0]["id"])
    assert _buff(env, "Hot Time")["ends"] == before


def _stored(env, name):
    return next(b for b in env.store.get("grind")["buffs"] if b["name"] == name)


def test_buff_undo_restores_armed_too(env):
    env.clock.t = T0 - 3600
    env.grind.buff({"name": "Hot Time", "minutes": 180})
    before = dict(_stored(env, "Hot Time"))
    env.clock.t = T0
    _login(env, T0 - 1000)
    env.game.add("b.jpg", T0 - 5)
    env.docs["b.jpg"] = _doc("Hot Time 40 min")
    env.auto.scan()
    env.auto.drain()
    assert _stored(env, "Hot Time")["armed"] != before["armed"]
    env.auto.undo(env.auto.view()["commits"][0]["id"])
    after = _stored(env, "Hot Time")
    assert (after["ends"], after["armed"]) == (before["ends"], before["armed"])


def test_buff_undo_refused_once_the_timer_moved_on(env):
    _login(env, T0 - 1000)
    env.game.add("a.jpg", T0 - 20)
    env.game.add("b.jpg", T0 - 10)
    env.docs["a.jpg"] = _doc("XP scroll 30 min")
    env.docs["b.jpg"] = _doc("XP scroll 50 min")
    env.auto.scan()
    env.auto.drain()
    u2, u1 = (c["id"] for c in env.auto.view()["commits"])
    with pytest.raises(ValueError, match="changed since"):
        env.auto.undo(u1)  # u2 wrote the timer since
    env.auto.undo(u2)
    assert _buff(env, "XP scroll")["left_s"] == 30 * 60
    env.grind.buff({"name": "XP scroll", "minutes": 5})  # a manual re-arm
    with pytest.raises(ValueError, match="changed since"):
        env.auto.undo(u1)
    assert _buff(env, "XP scroll")["left_s"] == 5 * 60


def test_buff_that_ran_out_is_neither_committed_nor_queued(env):
    _login(env, T0 - 3600)
    env.game.add("old.jpg", T0 - 600)  # 10 min ago
    env.docs["old.jpg"] = _doc("XP scroll 5 min", "Silver 1 234 567")
    env.auto.scan()
    env.auto.drain()
    assert [r["kind"] for r in env.auto.view()["review"]] == ["silver"]
    assert _buff(env, "XP scroll")["left_s"] is None


def test_review_buff_accept_after_expiry_then_fix_with_minutes_left_now(env):
    review = _queued(env, _doc("Value Pack 2 hr 75 min"))
    rid = review[0]["id"]
    env.clock.t = T0 + 4 * 3600  # the 195 minutes ran out while it waited
    with pytest.raises(ValueError, match="minutes left now"):
        env.auto.review({"id": rid, "action": "accept"})
    env.auto.review({"id": rid, "action": "fix", "value": 30})
    assert _buff(env, "Value Pack")["left_s"] == 30 * 60
    assert env.auto.view()["review"] == []


def test_threshold_setting_moves_the_gate(env):
    env.cfg["ocr.auto_commit_min"] = 0.75
    _login(env, T0 - 1000)
    env.game.add("a.jpg", T0 - 5)
    env.docs["a.jpg"] = _doc("Silver 1 234 567")  # conf 0.8
    env.auto.scan()
    env.auto.drain()
    assert env.store.get("silver")["samples"][0]["value"] == 1234567
    assert env.auto.view()["review"] == []


def test_acceptance_three_fixture_shots(env):
    """Plan 063 acceptance: 3 shots during a logged_in window commit the clean
    silver and buff fields and queue the noisy one, with no button press."""
    _login(env, T0 - 3600)
    env.game.add("one.jpg", T0 - 300)
    env.game.add("two.jpg", T0 - 200)
    env.game.add("three.jpg", T0 - 100)
    env.docs["one.jpg"] = CLEAN
    env.docs["two.jpg"] = _doc("Silver 1,300,000", "Hot Time 1 hr 20 min")
    env.docs["three.jpg"] = NOISY
    env.auto.scan()
    env.auto.drain()
    samples = env.store.get("silver")["samples"]
    assert [s["value"] for s in samples] == [1234567, 1300000]
    assert _buff(env, "XP scroll")["left_s"] == 25 * 60
    assert _buff(env, "Hot Time")["left_s"] == (80 - 3) * 60  # 200 s ago: 3 whole minutes
    review = env.auto.view()["review"]
    assert {(r["file"], r["kind"]) for r in review} == {("three.jpg", "silver"),
                                                        ("three.jpg", "buff")}
    assert _buff(env, "Value Pack")["left_s"] is None
    assert env.changes


# -- review queue ---------------------------------------------------------------

def _queued(env, *docs):
    _login(env, T0 - 3600)
    for i, d in enumerate(docs):
        env.game.add(f"q{i}.jpg", T0 - 100 + i)
        env.docs[f"q{i}.jpg"] = d
    env.auto.scan()
    env.auto.drain()
    return env.auto.view()["review"]


def test_review_accept_fix_discard(env):
    review = _queued(env, NOISY)
    silver = next(r for r in review if r["kind"] == "silver")
    buff = next(r for r in review if r["kind"] == "buff")
    env.auto.review({"id": silver["id"], "action": "fix", "value": 1234000})
    assert env.store.get("silver")["samples"][-1]["value"] == 1234000
    env.auto.review({"id": buff["id"], "action": "accept"})
    assert _buff(env, "Value Pack")["left_s"] is not None
    v = env.auto.view()
    assert v["review"] == []
    assert {c["via"] for c in v["commits"]} == {"fix", "accept"}
    with pytest.raises(ValueError):
        env.auto.review({"id": silver["id"], "action": "discard"})


def test_review_discard_and_bad_bodies(env):
    review = _queued(env, NOISY)
    rid = review[0]["id"]
    for bad in (None, {}, {"id": rid}, {"id": rid, "action": "eat"},
                {"id": rid, "action": "fix"}, {"id": rid, "action": "accept", "value": 1},
                {"id": rid, "action": "fix", "value": -1},
                {"id": rid, "action": "fix", "value": True}):
        with pytest.raises(ValueError):
            env.auto.review(bad)
    env.auto.review({"id": rid, "action": "discard"})
    assert len(env.auto.view()["review"]) == 1
    assert env.store.get("silver").get("samples", []) == []


def test_review_queue_capped_oldest_dropped(env):
    docs = [_doc(f"Silver {n} 000 000") for n in range(1, 56)]
    review = _queued(env, *docs)
    assert len(review) == ocrauto.MAX_REVIEW
    assert review[-1]["value"] == 6000000  # newest first; the five oldest dropped


def test_ocr_error_skips_the_shot(env):
    _login(env, T0 - 100)
    env.game.add("x.jpg", T0 - 5)

    def boom(path):
        raise ocr.OcrError("engine missing")
    env.reader.runner = boom
    env.auto.scan()
    assert env.auto.drain() == 1
    assert env.auto.view()["review"] == [] and env.auto.scan() == 0


# -- confidence -----------------------------------------------------------------

@pytest.mark.parametrize("text,value,lo,hi", [
    ("Silver 1,234,567", 1234567, 0.95, 1),
    ("Silver: 98,765", 98765, 0.95, 1),
    ("Silver 1.234.567", 1234567, 0.9, 0.95),
    ("Silver 1, 234 ,567", 1234567, 0.8, 0.9),
    ("Silver 1 234 567", 1234567, 0.75, 0.85),
    ("Silver 1,234.567", 1234567, 0, 0.7),
    ("Silver 1234567", 1234567, 0, 0.75),
    ("Silver 450", 450, 0.9, 1),
    ("Silver 1,23,567", 1, 0, 0.35),
    ("Silver 1,234,S67", 1234, 0, 0.35),
])
def test_silver_confidence(text, value, lo, hi):
    f = ocrauto.silver_field(_doc(text))
    assert f["value"] == value and lo <= f["conf"] <= hi, f


def test_silver_none_without_amount():
    assert ocrauto.silver_field(_doc("XP scroll 30 min")) is None
    assert ocrauto.silver_field({}) is None


def test_silver_plausibility_and_engine_agreement():
    d = _doc("Silver 1,234,567")
    assert ocrauto.silver_field(d, last=1_000_000)["conf"] >= 0.9
    assert ocrauto.silver_field(d, last=100)["conf"] < 0.9
    assert ocrauto.silver_field(d, last=900_000_000)["conf"] < 0.9
    assert ocrauto.silver_field(_doc("Silver 1 234 567"), alt=1234567)["conf"] >= 0.9
    assert ocrauto.silver_field(d, alt=1234568)["conf"] <= 0.3


@pytest.mark.parametrize("text,minutes,lo,hi", [
    ("XP scroll 30 min", 30, 1, 1),
    ("Hot Time 1 hr 20 min", 80, 1, 1),
    ("Value Pack 29d 23h", 29 * 1440 + 23 * 60, 1, 1),
    ("Value Pack 2 hr 75 min", 195, 0, 0.7),
    ("XP scroll 20 min 1 hr", 80, 0, 0.7),
    ("XP scroll 3 20 min", 20, 0, 0.85),
    ("XP scrol 30 min", 30, 0.8, 0.9),
])
def test_buff_confidence(text, minutes, lo, hi):
    fs = ocrauto.buff_fields(_doc(text))
    assert len(fs) == 1 and fs[0]["value"] == minutes and lo <= fs[0]["conf"] <= hi, fs


def test_buff_time_from_neighbour_line_is_below_default_gate():
    fs = ocrauto.buff_fields({"lines": [_ln("XP scroll", 0), _ln("30 min", 12)]})
    assert fs[0]["value"] == 30 and fs[0]["conf"] < ocrauto.AUTO_COMMIT_MIN


def test_buff_without_time_is_not_a_field():
    assert ocrauto.buff_fields(_doc("XP scroll")) == []


# -- bench: commit precision over the plan 009 synthetic amounts ------------------

def _replay_reads(seed=9):
    """(truth, OCR text) pairs: the plan 009 bench amounts (tools/ocr_bench.py
    cases(): 4-12 digits, comma grouped) passed through the error classes the
    bench and plan 016 measured - clean, comma read as dot / space, spaced
    separator, dropped comma, a truncated or garbled group, a joined neighbour
    number. Single wrong glyphs that keep a valid grouping are not detectable
    from text and are reported separately (see the plan's as-built section)."""
    rng = random.Random(seed)
    out = []
    for _ in range(288):
        digits = rng.choice((4, 5, 6, 7, 7, 8, 9, 10, 11, 12))
        n = rng.randrange(10 ** (digits - 1), 10 ** digits)
        s = f"{n:,}"
        kind = rng.choice(("clean",) * 6 + ("dot", "space", "spaced", "dropped",
                                            "truncated", "garbled", "joined"))
        if kind == "dot":
            s = s.replace(",", ".")
        elif kind == "space":
            s = s.replace(",", " ")
        elif kind == "spaced":
            s = s.replace(",", ", ", 1)
        elif kind == "dropped" and "," in s:
            i = s.index(",")
            s = s[:i] + s[i + 1:]
        elif kind == "truncated" and "," in s:
            s = s[:-2] + "," + s[-2:]
        elif kind == "garbled" and "," in s:
            s = s[:-1] + "S"
        elif kind == "joined":
            s = s.replace(",", " ") + " 100"
        out.append((n, f"Silver {s}"))
    return out


def test_bench_commit_precision_at_default_threshold():
    committed = correct = 0
    for truth, text in _replay_reads():
        f = ocrauto.silver_field(_doc(text))
        if f is not None and f["conf"] >= ocrauto.AUTO_COMMIT_MIN:
            committed += 1
            correct += f["value"] == truth
    precision = correct / committed
    recall = correct / 288
    assert precision >= 0.99, (precision, recall)
    assert recall >= 0.4, recall  # reported; the clean majority still commits


# -- settings + server wiring -----------------------------------------------------

def test_ocr_keys_fixed_not_settable():
    # Plan 080: fixed values; a config value is read only as an incident switch.
    d = settings.fixed()
    assert d["ocr.auto"] is True and d["ocr.auto_commit_min"] == 0.9 and d["ocr.daily_cap"] == 120
    for k in ("ocr.auto", "ocr.auto_commit_min", "ocr.daily_cap"):
        with pytest.raises(ValueError, match="not a settable key"):
            settings.validate({"set": {k: d[k]}})
    vals = settings.values_from({"ocr": {"daily_cap": 10, "auto_commit_min": 0.7}}, settings.FIXED)
    assert vals["ocr.daily_cap"] == 10 and vals["ocr.auto_commit_min"] == 0.9  # 0.7 out of range


def _no_network(url, timeout=None):
    raise AssertionError("no network in tests")


@pytest.fixture
def srv(tmp_path):
    shot_dir = tmp_path / "ScreenShot"
    shot_dir.mkdir()
    game = FakeGame(shot_dir)
    docs = {}
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", market_seed=[],
                          market_client=market.ArshaClient(fetch=_no_network,
                                                           cache_dir=tmp_path / "cache"),
                          profile_cfg={}, game_watch=game,
                          ocr_runner=lambda p: docs[p.name], ocr_cache_dir=tmp_path / "ocr",
                          config_path=tmp_path / "local.json")
    s.fake_game, s.fake_docs = game, docs
    t = threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    t.start()
    yield s
    s.shutdown()
    s.server_close()


def _req(s, method, path, body=None):
    c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=5)
    data = json.dumps(body).encode() if body is not None else None
    c.request(method, path, body=data, headers={"Content-Type": "application/json"})
    r = c.getresponse()
    out = json.loads(r.read())
    c.close()
    return r.status, out


def test_server_route_review_and_undo(srv):
    import time as _time
    now = _time.time()
    assert srv.game.listeners and srv.ocr_auto.on_game in srv.game.listeners
    srv.ocr_auto.on_game(None, "logged_in", now - 100)
    srv.fake_game.add("a.jpg", now - 10)
    srv.fake_game.add("b.jpg", now - 5)
    srv.fake_docs["a.jpg"] = _doc("Silver 1,234,567")
    srv.fake_docs["b.jpg"] = _doc("Silver 1 234 567")
    srv.ocr_auto.scan()
    srv.ocr_auto.drain()
    st, v = _req(srv, "GET", "/api/ocr/auto")
    assert st == 200 and v["enabled"] is True and len(v["review"]) == 1 and len(v["commits"]) == 1
    st, v = _req(srv, "POST", "/api/ocr", {"review": {"id": v["review"][0]["id"],
                                                      "action": "discard"}})
    assert st == 200 and v["review"] == []
    st, v = _req(srv, "POST", "/api/ocr", {"undo": v["commits"][0]["id"]})
    assert st == 200 and v["commits"] == []
    st, v = _req(srv, "POST", "/api/ocr", {"undo": "u999"})
    assert st == 400
