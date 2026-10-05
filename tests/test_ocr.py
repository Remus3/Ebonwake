"""Plan 009 slice A: OCR of operator-taken screenshots.

Extractors run on synthetic OCR line lists; the PowerShell runner is always
faked. No test reads the real ScreenShot folder or config/local.json, and no
test runs OCR (the ps1 is only PARSED, by powershell.exe 5.1, on Windows).
"""

import http.client
import json
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from server.ew import app as ewapp
from server.ew import gamewatch, grind, market, ocr

ROOT = Path(__file__).resolve().parents[1]
PS1 = ROOT / "tools" / "ocr.ps1"
T0 = 1_800_000_000.0


def L(text, x=0, y=0, w=100, h=20):
    return {"text": text, "x": x, "y": y, "w": w, "h": h}


# -- silver extractor ----------------------------------------------------------

def test_silver_same_line():
    assert ocr.extract_silver([L("Silver 1,234,567,890")]) == 1234567890


def test_silver_number_before_word_same_line():
    assert ocr.extract_silver([L("12,345,678 Silver")]) == 12345678


def test_silver_same_row_neighbour():
    lines = [L("Silver", x=10, y=100), L("98,765,432", x=200, y=102),
             L("4,000", x=200, y=300)]
    assert ocr.extract_silver(lines) == 98765432


def test_silver_line_below():
    lines = [L("Silver", x=10, y=100, h=20), L("5,500,000", x=12, y=124, h=20)]
    assert ocr.extract_silver(lines) == 5500000


def test_silver_dot_grouping_and_spaces():
    assert ocr.extract_silver([L("Silver: 1.234.567")]) == 1234567
    assert ocr.extract_silver([L("Silver 1, 234, 567")]) == 1234567


def test_silver_case_insensitive():
    assert ocr.extract_silver([L("SILVER 2,000")]) == 2000


def test_silver_absent_is_none():
    assert ocr.extract_silver([]) is None
    assert ocr.extract_silver([L("Weight 1,234")]) is None
    assert ocr.extract_silver([L("Silver")]) is None


def test_silver_ignores_far_numbers():
    lines = [L("Silver", x=10, y=100, h=20), L("777", x=10, y=600, h=20)]
    assert ocr.extract_silver(lines) is None


def test_silver_over_cap_rejected():
    assert ocr.extract_silver([L("Silver 99,999,999,999,999,999")]) is None


def test_silver_bad_shapes_tolerated():
    assert ocr.extract_silver([{"text": None}, "junk", L("Silver 3,000")]) == 3000


# -- duration + buff extractor -------------------------------------------------

@pytest.mark.parametrize("text,minutes", [
    ("59 min", 59), ("59min", 59), ("1 hr 20 min", 80), ("1h 20m", 80),
    ("2 hours", 120), ("29 days 23 hours", 29 * 1440 + 23 * 60), ("29d 23h", 29 * 1440 + 23 * 60),
    ("30 sec", 1), ("1 min 30 sec", 2), ("Remaining Time: 45 minutes", 45),
])
def test_duration_minutes(text, minutes):
    assert ocr.duration_minutes(text) == minutes


@pytest.mark.parametrize("text", ["", "no time here", "12:34", "Level 61", "1,234"])
def test_duration_none(text):
    assert ocr.duration_minutes(text) is None


def test_duration_capped():
    assert ocr.duration_minutes("999 days") == grind.MAX_BUFF_MINUTES


def test_buffs_match_seed_names_with_minutes():
    lines = [L("Value Pack 29 days 23 hours", y=10), L("XP scroll 59 min", y=40),
             L("Inventory", y=70)]
    assert ocr.extract_buffs(lines) == [
        {"name": "Value Pack", "minutes": 29 * 1440 + 23 * 60},
        {"name": "XP scroll", "minutes": 59},
    ]


def test_buffs_case_and_spacing_insensitive():
    out = ocr.extract_buffs([L("drop-rate  SCROLL: 1h 5m")])
    assert out == [{"name": "Drop rate scroll", "minutes": 65}]


def test_buffs_minutes_from_same_row_or_line_below():
    lines = [L("Hot Time", x=10, y=100), L("25 min", x=300, y=101),
             L("Kamasylve blessing", x=10, y=200, h=20), L("6 days", x=12, y=224, h=20)]
    assert ocr.extract_buffs(lines) == [
        {"name": "Hot Time", "minutes": 25},
        {"name": "Kamasylve blessing", "minutes": 6 * 1440},
    ]


def test_buffs_without_time_have_none_minutes():
    assert ocr.extract_buffs([L("Old Moon book")]) == [{"name": "Old Moon book", "minutes": None}]


def test_buffs_deduped_first_wins_and_unknown_ignored():
    lines = [L("XP scroll 10 min", y=0), L("XP scroll 50 min", y=300), L("Mystery buff 9 min")]
    assert ocr.extract_buffs(lines) == [{"name": "XP scroll", "minutes": 10}]


def test_buffs_custom_names_and_word_bounds():
    assert ocr.extract_buffs([L("Hot Timer 5 min")]) == []
    assert ocr.extract_buffs([L("Elion 5 min")], names=("Elion",)) == [
        {"name": "Elion", "minutes": 5}]


def test_buff_names_cover_every_seed():
    for name in grind.SEED_BUFFS:
        assert ocr.extract_buffs([L(f"{name} 3 min")]) == [{"name": name, "minutes": 3}]


# -- runner output parsing -------------------------------------------------------

def test_parse_output_ok_and_sanitised():
    out = json.dumps({"text": "a\nb", "lines": [
        {"text": "a", "x": 1, "y": 2, "w": 3, "h": 4},
        {"text": "b\x01", "x": 1.6, "y": "bad", "w": 3, "h": 4},
        "junk"]})
    doc = ocr.parse_output(out)
    assert doc["text"] == "a\nb"
    assert doc["lines"][0] == {"text": "a", "x": 1, "y": 2, "w": 3, "h": 4}
    assert doc["lines"][1]["text"] == "b" and doc["lines"][1]["y"] == 0
    assert len(doc["lines"]) == 2


@pytest.mark.parametrize("out", ["", "not json", "[]", json.dumps({"error": "boom"}),
                                 json.dumps({"text": 3, "lines": []})])
def test_parse_output_bad_raises(out):
    with pytest.raises(ocr.OcrError):
        ocr.parse_output(out)


class Proc:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


class FakeRun:
    def __init__(self, proc=None, exc=None):
        self.proc, self.exc, self.calls = proc, exc, []

    def __call__(self, args, **kw):
        self.calls.append((args, kw))
        if self.exc:
            raise self.exc
        return self.proc


def test_run_ocr_command_shape(tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(b"x")
    run = FakeRun(Proc(json.dumps({"text": "Silver 5", "lines": [L("Silver 5")]})))
    doc = ocr.run_ocr(img, run=run, timeout=7)
    args, kw = run.calls[0]
    assert args[0].lower().endswith("powershell.exe") or args[0] == "powershell"
    assert args[1:7] == ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                         "-File", str(ocr.PS1)]
    assert args[7] == str(img)
    assert kw["timeout"] == 7 and kw["capture_output"] is True
    if sys.platform == "win32":
        assert kw["creationflags"] == subprocess.CREATE_NO_WINDOW
    assert doc["text"] == "Silver 5"


@pytest.mark.parametrize("run", [
    FakeRun(Proc(json.dumps({"error": "no engine"}), returncode=1)),
    FakeRun(Proc("garbage")),
    FakeRun(exc=subprocess.TimeoutExpired("ps", 1)),
    FakeRun(exc=OSError("missing")),
])
def test_run_ocr_failures_raise(tmp_path, run):
    with pytest.raises(ocr.OcrError):
        ocr.run_ocr(tmp_path / "a.png", run=run)


def test_run_ocr_error_message_surfaced(tmp_path):
    run = FakeRun(Proc(json.dumps({"error": "no engine"}), returncode=1))
    with pytest.raises(ocr.OcrError, match="no engine"):
        ocr.run_ocr(tmp_path / "a.png", run=run)


# -- ps1 static checks -----------------------------------------------------------

def test_ps1_ascii_lf_and_pattern():
    raw = PS1.read_bytes()
    assert b"\r" not in raw and all(b < 128 for b in raw)
    text = raw.decode("ascii")
    for needle in ("Windows.Media.Ocr", "BitmapDecoder", "StorageFile",
                   "TryCreateFromUserProfileLanguages", "RecognizeAsync",
                   "WindowsRuntimeSystemExtensions", "AsTask", "ConvertTo-Json"):
        assert needle in text


@pytest.mark.skipif(sys.platform != "win32", reason="powershell.exe 5.1 is Windows only")
@pytest.mark.parametrize("PS1", [PS1, ROOT / "tools" / "ocr_prep.ps1",
                                 ROOT / "tools" / "ocr_bench.ps1",
                                 ROOT / "tools" / "ocr_ink.ps1"], ids=lambda p: p.name)
def test_ps1_parses_in_windows_powershell(PS1):
    exe = ocr.powershell_exe()
    if not Path(exe).is_file() and not shutil.which(exe):
        pytest.skip("powershell.exe not found")
    cmd = ("$t=$null; $e=$null; [void][System.Management.Automation.Language.Parser]::ParseFile("
           f"'{PS1}', [ref]$t, [ref]$e); $e.Count")
    out = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-Command", cmd],
                         capture_output=True, text=True, timeout=60,
                         creationflags=subprocess.CREATE_NO_WINDOW)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "0", out.stdout + out.stderr


# -- service + cache ----------------------------------------------------------------

class Tasklist:
    def __call__(self):
        return False


@pytest.fixture()
def watch(tmp_path):
    inst, docs = tmp_path / "install", tmp_path / "docs"
    (inst / "Log").mkdir(parents=True)
    (docs / "ScreenShot").mkdir(parents=True)
    w = gamewatch.GameWatch(install_dir=str(inst), documents_dir=str(docs),
                            clock=lambda: T0, tasklist=Tasklist())
    w.started = 0  # every synthetic shot counts as taken this session
    return w


def _shot(watch, name="2026_10_05_12_00_00.jpg", data=b"img"):
    p = watch.shot_dir / name
    p.write_bytes(data)
    watch.poll()
    return name


OCR_DOC = {"text": "Silver 1,500,000\nXP scroll 30 min",
           "lines": [L("Silver 1,500,000", y=0), L("XP scroll 30 min", y=100)]}


def test_service_reads_listed_file_and_caches(watch, tmp_path):
    name = _shot(watch)
    calls = []

    def runner(path):
        calls.append(Path(path))
        return OCR_DOC

    svc = ocr.OcrService(watch, tmp_path / "ocr", runner=runner)
    out = svc.read({"file": name})
    assert out == {"text": OCR_DOC["text"], "silver": 1500000,
                   "buffs": [{"name": "XP scroll", "minutes": 30}]}
    assert calls == [watch.shot_dir / name]
    assert svc.read({"file": name}) == out
    assert len(calls) == 1  # second read from the cache
    assert len(list((tmp_path / "ocr").glob("*.json"))) == 1
    assert not list((tmp_path / "ocr").glob("*.tmp"))
    svc2 = ocr.OcrService(watch, tmp_path / "ocr", runner=runner)
    assert svc2.read({"file": name}) == out and len(calls) == 1  # survives restart


def test_service_cache_invalidated_when_file_changes(watch, tmp_path):
    name = _shot(watch)
    calls = []

    def runner(path):
        calls.append(path)
        return OCR_DOC

    svc = ocr.OcrService(watch, tmp_path / "ocr", runner=runner)
    svc.read({"file": name})
    _shot(watch, name, data=b"different-size")
    svc.read({"file": name})
    assert len(calls) == 2


@pytest.mark.parametrize("body", [{}, {"file": 3}, {"file": "nope.jpg"},
                                  {"file": "../x.jpg"}, {"file": "a.jpg", "extra": 1},
                                  {"file": ""}])
def test_service_rejects_unlisted(watch, tmp_path, body):
    _shot(watch, "a.jpg")

    def runner(path):
        raise AssertionError("runner must not run")

    svc = ocr.OcrService(watch, tmp_path / "ocr", runner=runner)
    with pytest.raises(ValueError):
        svc.read(body)


def test_service_unconfigured_rejects(tmp_path):
    w = gamewatch.GameWatch.from_config({}, clock=lambda: T0, tasklist=Tasklist())
    svc = ocr.OcrService(w, tmp_path / "ocr", runner=lambda p: OCR_DOC)
    with pytest.raises(ValueError):
        svc.read({"file": "a.jpg"})


def test_service_failure_not_cached(watch, tmp_path):
    name = _shot(watch)
    n = [0]

    def runner(path):
        n[0] += 1
        if n[0] == 1:
            raise ocr.OcrError("boom")
        return OCR_DOC

    svc = ocr.OcrService(watch, tmp_path / "ocr", runner=runner)
    with pytest.raises(ocr.OcrError):
        svc.read({"file": name})
    assert svc.read({"file": name})["silver"] == 1500000


def test_service_never_applies_results(watch, tmp_path):
    name = _shot(watch)
    store_root = tmp_path / "store"
    svc = ocr.OcrService(watch, tmp_path / "ocr", runner=lambda p: OCR_DOC)
    svc.read({"file": name})
    assert not store_root.exists()


# -- HTTP route ---------------------------------------------------------------------

def _no_network(url, timeout):
    raise AssertionError("test touched the network")


@pytest.fixture()
def srv(tmp_path, watch):
    calls = []

    def runner(path):
        calls.append(path)
        return OCR_DOC

    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          sse_interval=0.05, market_seed=[],
                          market_client=market.ArshaClient(fetch=_no_network,
                                                           cache_dir=tmp_path / "cache"),
                          profile_cfg={}, game_watch=watch, ocr_runner=runner,
                          ocr_cache_dir=tmp_path / "ocr")
    s.ocr_calls = calls
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield s
    s.shutdown()
    s.server_close()


def _post(s, body, path="/api/ocr"):
    c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=5)
    c.request("POST", path, body=json.dumps(body).encode(),
              headers={"Content-Type": "application/json"})
    r = c.getresponse()
    out = r.read()
    c.close()
    return r.status, json.loads(out)


def test_route_reads_listed_shot(srv, watch, tmp_path):
    name = _shot(watch)
    st, doc = _post(srv, {"file": name})
    assert st == 200
    assert doc == {"text": OCR_DOC["text"], "silver": 1500000,
                   "buffs": [{"name": "XP scroll", "minutes": 30}]}
    # nothing applied: grind state untouched by OCR
    grind_doc = srv.grind.view()
    assert grind_doc["active"] is None and grind_doc["sessions"] == []


def test_route_unlisted_is_400(srv, watch):
    _shot(watch)
    st, doc = _post(srv, {"file": "other.jpg"})
    assert st == 400 and "error" in doc
    assert srv.ocr_calls == []


def test_route_ocr_failure_is_502(tmp_path, watch):
    def runner(path):
        raise ocr.OcrError("engine missing")

    s = ewapp.make_server(port=0, store_root=tmp_path / "store", market_seed=[],
                          market_client=market.ArshaClient(fetch=_no_network,
                                                           cache_dir=tmp_path / "cache"),
                          profile_cfg={}, game_watch=watch, ocr_runner=runner,
                          ocr_cache_dir=tmp_path / "ocr")
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    try:
        name = _shot(watch)
        st, doc = _post(s, {"file": name})
        assert st == 502 and doc == {"error": "ocr failed: engine missing"}
    finally:
        s.shutdown()
        s.server_close()


def test_default_server_ocr_cache_not_in_repo_runtime_under_tests(tmp_path):
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", market_seed=[],
                          market_client=market.ArshaClient(fetch=_no_network,
                                                           cache_dir=tmp_path / "cache"),
                          profile_cfg={})
    try:
        assert tmp_path in Path(s.ocr.cache_dir).parents
    finally:
        s.server_close()


def _ln(text, y=10):
    return {"text": text, "x": 0, "y": y, "w": 100, "h": 10}


def test_two_buffs_on_one_line_each_get_their_own_time():
    out = ocr.extract_buffs([_ln("XP scroll 29 min Value Pack 3 d 4 h")])
    assert out == [{"name": "XP scroll", "minutes": 29},
                   {"name": "Value Pack", "minutes": 3 * 1440 + 240}]
    out = ocr.extract_buffs([_ln("Hot Time XP scroll 10 min")])
    assert [b["name"] for b in out] == ["Hot Time", "XP scroll"]
    assert out[1]["minutes"] == 10 and out[0]["minutes"] is None


def test_unwritable_cache_still_returns_result(watch, tmp_path):
    name = _shot(watch)
    blocker = tmp_path / "ocr"
    blocker.write_text("not a dir")
    svc = ocr.OcrService(watch, blocker, runner=lambda p: OCR_DOC)
    assert svc.read({"file": name})["silver"] == 1500000


# -- hand-off 08caa5c edge cases ---------------------------------------------------

def test_repeated_buff_name_takes_the_time_after_the_repeat():
    got = ocr.extract_buffs([L("XP scroll XP scroll 20 min")])
    assert got == [{"name": "XP scroll", "minutes": 20}]


def test_same_start_keeps_the_longer_name():
    names = ("Value Pack", "Value Pack Plus")
    got = ocr.extract_buffs([L("Value Pack Plus 3 h")], names=names)
    assert got == [{"name": "Value Pack Plus", "minutes": 180}]
    got = ocr.extract_buffs([L("Value Pack Plus 3 h")], names=names[::-1])
    assert got == [{"name": "Value Pack Plus", "minutes": 180}]


# -- Tesseract runner (follow-up: engine benchmark) --------------------------------

TSV_HEAD = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"


def _tsv_row(line, word, x, y, w, h, text, conf="91.5"):
    return f"5\t1\t1\t1\t{line}\t{word}\t{x}\t{y}\t{w}\t{h}\t{conf}\t{text}\n"


def test_parse_tsv_groups_words_and_unscales():
    out = TSV_HEAD + "1\t1\t0\t0\t0\t0\t0\t0\t600\t300\t-1\t\n" \
        + _tsv_row(1, 1, 30, 60, 90, 30, "Silver") \
        + _tsv_row(1, 2, 130, 60, 230, 36, "1,234,567") \
        + _tsv_row(2, 1, 30, 150, 60, 30, "XP") \
        + _tsv_row(2, 2, 99, 150, 9, 30, " ", conf="-1")
    doc = ocr.parse_tsv(out, scale=3)
    assert doc["text"] == "Silver 1,234,567\nXP"
    assert doc["lines"][0] == {"text": "Silver 1,234,567", "x": 10, "y": 20, "w": 110, "h": 12}
    assert ocr.extract_silver(doc["lines"]) == 1234567


def test_parse_tsv_garbage_is_empty():
    assert ocr.parse_tsv("") == {"text": "", "lines": []}
    assert ocr.parse_tsv(TSV_HEAD + "5\tx\n5\t1\t1\t1\t1\t1\ta\tb\tc\td\te\tf\n")["lines"] == []


def test_run_tesseract_command_shape(tmp_path):
    seen = {}

    def fake(args, **kw):
        seen["args"], seen["kw"] = args, kw
        return subprocess.CompletedProcess(args, 0, TSV_HEAD + _tsv_row(1, 1, 3, 3, 3, 3, "Hi"), "")

    doc = ocr.run_tesseract(tmp_path / "a.png", exe="tess.exe", psm=11, scale=1, run=fake)
    assert doc["text"] == "Hi"
    assert seen["args"] == ["tess.exe", str(tmp_path / "a.png"), "stdout", "--psm", "11",
                            "-l", "eng", "tsv"]
    assert seen["kw"]["timeout"] == ocr.OCR_TIMEOUT_S


def test_run_tesseract_failures_raise(tmp_path):
    def bad(args, **kw):
        return subprocess.CompletedProcess(args, 1, "", "boom")

    def boom(args, **kw):
        raise OSError("nope")
    for run in (bad, boom):
        with pytest.raises(ocr.OcrError):
            ocr.run_tesseract(tmp_path / "a.png", exe="t.exe", run=run)


def test_tesseract_exe_from_config_then_absent(tmp_path, monkeypatch):
    exe = tmp_path / "tesseract.exe"
    exe.write_bytes(b"")
    cfg = tmp_path / "local.json"
    cfg.write_text(json.dumps({"ocr": {"tesseract": str(exe)}}), encoding="utf-8")
    assert ocr.tesseract_exe(cfg) == str(exe)
    monkeypatch.setattr(ocr.shutil, "which", lambda n: None)
    for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        monkeypatch.setenv(var, str(tmp_path / "none"))
    cfg.write_text("{}", encoding="utf-8")
    assert ocr.tesseract_exe(cfg) is None


# -- engine chain (Tesseract preprocessed passes, Windows OCR fallback) ------------

def _fake_chain(reads, prep_scale=3.0, prep_rc=0, tess_rc=0):
    """A subprocess.run fake: ocr_prep.ps1 -> {out, scale}; tesseract -> TSV of the
    next read in `reads`; ocr.ps1 -> a Windows OCR doc. Records every call."""
    calls = []
    it = iter(reads)

    def run(args, **kw):
        calls.append(args)
        if "-File" in args and args[args.index("-File") + 1].endswith("ocr_prep.ps1"):
            sc = min(prep_scale, float(args[args.index("-Scale") + 1]))
            out = json.dumps({"out": args[args.index("-Out") + 1], "scale": sc})
            return subprocess.CompletedProcess(args, prep_rc, out if not prep_rc else
                                               '{"error": "bad image"}', "")
        if "-File" in args:
            return subprocess.CompletedProcess(args, 0, json.dumps(
                {"text": "Silver 7", "lines": [L("Silver 7")]}), "")
        text = next(it)
        return subprocess.CompletedProcess(args, tess_rc, TSV_HEAD + _tsv_row(
            1, 1, 0, 0, 30, 30, text), "")
    return run, calls


def test_chain_stops_at_first_pass_with_silver(tmp_path):
    run, calls = _fake_chain(["Silver", "Silver 1,234,567", "never"])
    doc = ocr.run_tesseract_chain(tmp_path / "a.png", tmp_path / "w", "t.exe", run=run)
    assert doc["text"] == "Silver 1,234,567"
    kinds = [("prep" if "-File" in c else c[4]) for c in calls]
    assert kinds == ["prep", "6", "11"]  # one prep for both x3 passes
    assert list((tmp_path / "w").iterdir()) == []  # temp preps removed


def test_chain_without_silver_keeps_first_read(tmp_path):
    run, calls = _fake_chain(["XP scroll 20 min", "junk", "junk"])
    doc = ocr.run_tesseract_chain(tmp_path / "a.png", tmp_path / "w", "t.exe", run=run)
    assert doc["text"] == "XP scroll 20 min"
    assert [c for c in calls if "-File" in c][1][-3] == "1.0"  # x1 pass re-preps


def test_chain_maps_boxes_by_applied_scale(tmp_path):
    run, _ = _fake_chain(["Silver 5"], prep_scale=2.0)
    doc = ocr.run_tesseract_chain(tmp_path / "a.png", tmp_path / "w", "t.exe", run=run)
    assert doc["lines"][0]["h"] == 15  # 30 px / applied 2.0, not / requested 3.0


def test_auto_falls_back_to_windows_on_tesseract_failure(tmp_path):
    run, calls = _fake_chain(["x"], prep_rc=1)
    doc = ocr.run_auto(tmp_path / "a.png", tmp_path / "w", run=run, engine="auto", exe="t.exe")
    assert doc["text"] == "Silver 7" and calls[-1][calls[-1].index("-File") + 1] == str(ocr.PS1)
    with pytest.raises(ocr.OcrError):
        ocr.run_auto(tmp_path / "a.png", tmp_path / "w", run=run, engine="tesseract",
                     exe="t.exe")


def test_auto_engine_choice(tmp_path, monkeypatch):
    run, calls = _fake_chain(["Silver 9"])
    assert ocr.run_auto(tmp_path / "a.png", tmp_path / "w", run=run, engine="windows",
                        exe="t.exe")["text"] == "Silver 7"
    assert ocr.run_auto(tmp_path / "a.png", tmp_path / "w", run=run, engine="auto",
                        exe="t.exe")["text"] == "Silver 9"
    monkeypatch.setattr(ocr, "tesseract_exe", lambda: None)
    assert ocr.run_auto(tmp_path / "a.png", tmp_path / "w", run=run,
                        engine="auto")["text"] == "Silver 7"
    with pytest.raises(ocr.OcrError):
        ocr.run_auto(tmp_path / "a.png", tmp_path / "w", run=run, engine="tesseract")


def test_ocr_engine_config(tmp_path):
    cfg = tmp_path / "local.json"
    assert ocr.ocr_engine(cfg) == "auto"
    for val, want in (("windows", "windows"), ("tesseract", "tesseract"), ("bogus", "auto")):
        cfg.write_text(json.dumps({"ocr": {"engine": val}}), encoding="utf-8")
        assert ocr.ocr_engine(cfg) == want


@pytest.mark.parametrize("text", ["Silver 1 234 567", "Silver 1,234 567",
                                  "Silver 1 ,234,567", "Silver: 1, 234, 567"])
def test_silver_tesseract_separator_forms(text):
    assert ocr.extract_silver([L(text)]) == 1234567


def test_bench_cases_and_scoring():
    sys.path.insert(0, str(ROOT / "tools"))
    import ocr_bench
    cs = ocr_bench.cases(quick=True)
    assert len(cs) == len({c["id"] for c in cs}) and any(c["want"] == 1234567 for c in cs)
    c = next(c for c in cs if c["kind"] == "silver")
    assert ocr_bench.score(c, {"lines": [L(c["text"])]})
    assert not ocr_bench.score(c, {"lines": [L("Silver 1")]})
    b = next(c for c in cs if c["kind"] == "bare")
    assert ocr_bench.score(b, {"lines": [L(b["text"])]})


def test_parse_tsv_splits_wide_gaps_so_row_numbers_never_merge():
    # verifier round 1: psm 6 put "100" on the silver line -> 1234567100
    out = TSV_HEAD + _tsv_row(1, 1, 0, 0, 60, 15, "Silver") \
        + _tsv_row(1, 2, 66, 0, 70, 15, "1,234,567") \
        + _tsv_row(1, 3, 160, 0, 25, 15, "100")
    doc = ocr.parse_tsv(out)
    assert [ln["text"] for ln in doc["lines"]] == ["Silver 1,234,567", "100"]
    assert ocr.extract_silver(doc["lines"]) == 1234567


def test_parse_tsv_keeps_comma_read_as_space_together():
    out = TSV_HEAD + _tsv_row(1, 1, 0, 0, 60, 15, "Silver") \
        + _tsv_row(1, 2, 66, 0, 38, 15, "1,234") \
        + _tsv_row(1, 3, 109, 0, 25, 15, "567")
    assert ocr.extract_silver(ocr.parse_tsv(out)["lines"]) == 1234567


# -- word-gap fix: digit|digit boundaries + ink probe -------------------------------

def _gap_tsv(gap, h2=30, y2=0, h1=30):
    """"Silver 1,234,567 100" at x3-like sizes; `gap` px between amount and 100."""
    return TSV_HEAD + _tsv_row(1, 1, 0, 0, 120, 30, "Silver") \
        + _tsv_row(1, 2, 132, 0, 200, h1, "1,234,567") \
        + _tsv_row(1, 3, 332 + gap, y2, 54, h2, "100")


def test_parse_tsv_two_spaces_split_digit_words():
    doc = ocr.parse_tsv(_gap_tsv(gap=20))  # 0.67 h: two spaces
    assert [ln["text"] for ln in doc["lines"]] == ["Silver 1,234,567", "100"]
    assert ocr.extract_silver(doc["lines"]) == 1234567


def test_parse_tsv_dropped_comma_gap_045_kept_together():
    doc = ocr.parse_tsv(_gap_tsv(gap=13))  # 0.43 h: a comma Tesseract dropped
    assert [ln["text"] for ln in doc["lines"]] == ["Silver 1,234,567 100"]


def test_parse_tsv_non_digit_boundary_keeps_wide_split():
    out = TSV_HEAD + _tsv_row(1, 1, 0, 0, 120, 30, "Silver") \
        + _tsv_row(1, 2, 140, 0, 200, 30, "1,234,567")  # 0.67 h after a word: kept
    assert ocr.parse_tsv(out)["lines"][0]["text"] == "Silver 1,234,567"


@pytest.mark.parametrize("kw", [{"h2": 22}, {"y2": 9}], ids=["height", "bottom"])
def test_parse_tsv_height_or_bottom_mismatch_splits(kw):
    doc = ocr.parse_tsv(_gap_tsv(gap=8, **kw))
    assert [ln["text"] for ln in doc["lines"]] == ["Silver 1,234,567", "100"]


def test_parse_tsv_bounds_rect_for_probe():
    doc, bounds = ocr._parse_tsv(_gap_tsv(gap=13), scale=2)
    assert len(bounds) == 1
    b = bounds[0]
    assert doc["lines"][0]["text"][b["at"]] == " " and b["line"] == 0
    assert b["at"] == len("Silver 1,234,567")
    # x: prev right + 1 .. next left - 1; y: baseline 30 - 0.15 h .. + 0.40 h
    assert b["rect"] == (333, 26, 11, 16)
    assert b["cut"] == (166, 172)  # original px (scale 2)
    _, none = ocr._parse_tsv(_gap_tsv(gap=5))  # under PROBE_GAP: never probed
    assert none == []


class FakeProbe:
    def __init__(self, ink=None, exc=None):
        self.ink, self.exc, self.calls = ink, exc, []

    def __call__(self, image, rects):
        self.calls.append((image, list(rects)))
        if self.exc:
            raise self.exc
        return self.ink


def _one_space():
    return ocr._parse_tsv(_gap_tsv(gap=13))


def test_probe_no_ink_is_a_space_and_cuts():
    doc, bounds = _one_space()
    probe = FakeProbe([0.0])
    out = ocr.split_spaced_amount(doc, bounds, "prep.png", probe)
    assert len(probe.calls) == 1 and probe.calls[0] == ("prep.png", [bounds[0]["rect"]])
    assert [ln["text"] for ln in out["lines"]] == ["Silver 1,234,567", "100"]
    assert out["lines"][1]["x"] == 345 and out["lines"][0]["w"] == 332
    assert ocr.extract_silver(out["lines"]) == 1234567
    assert ocr._sanitise(out) == out  # still the public {text, lines} shape


def test_probe_ink_is_a_dropped_comma_and_joins():
    doc, bounds = _one_space()
    out = ocr.split_spaced_amount(doc, bounds, "prep.png", FakeProbe([0.2]))
    assert out == doc and ocr.extract_silver(out["lines"]) == 1234567100


@pytest.mark.parametrize("probe", [FakeProbe(exc=ocr.OcrError("ps failed")),
                                   FakeProbe(exc=OSError("gone")), FakeProbe(ink=[])],
                         ids=["ocrerror", "oserror", "short"])
def test_probe_failure_falls_back_to_join(probe):
    doc, bounds = _one_space()
    assert ocr.split_spaced_amount(doc, bounds, "prep.png", probe) == doc


def test_probe_only_when_amount_spans_the_gap():
    out = TSV_HEAD + _tsv_row(1, 1, 0, 0, 54, 30, "100") \
        + _tsv_row(1, 2, 67, 0, 54, 30, "200") \
        + _tsv_row(2, 1, 0, 100, 120, 30, "Silver") \
        + _tsv_row(2, 2, 132, 100, 200, 30, "1,234,567")
    doc, bounds = ocr._parse_tsv(out)
    assert len(bounds) == 1  # "100 200" is probe-able, but not the silver amount
    probe = FakeProbe([0.0])
    assert ocr.split_spaced_amount(doc, bounds, "p.png", probe) == doc
    assert probe.calls == []


def test_ink_probe_command_and_parse(tmp_path):
    seen = {}

    def fake(args, **kw):
        seen["args"], seen["kw"] = args, kw
        return subprocess.CompletedProcess(args, 0, '{"ink":[0.0,0.25]}', "")

    got = ocr.ink_probe(tmp_path / "p.png", [(1, 2, 3, 4), (5, 6, 7, 8)], run=fake)
    assert got == [0.0, 0.25]
    a = seen["args"]
    assert a[a.index("-File") + 1] == str(ocr.INK_PS1)
    assert a[a.index("-Rects") + 1] == "1,2,3,4;5,6,7,8"
    if sys.platform == "win32":
        assert seen["kw"]["creationflags"] == subprocess.CREATE_NO_WINDOW


@pytest.mark.parametrize("stdout,rc", [('{"ink":0.5}', 0), ('{"error":"x"}', 1),
                                       ('{"ink":[0.1]}', 0), ("junk", 0),
                                       ('{"ink":[2.0,0]}', 0)])
def test_ink_probe_bad_output(stdout, rc):
    def fake(args, **kw):
        return subprocess.CompletedProcess(args, rc, stdout, "")
    with pytest.raises(ocr.OcrError):
        ocr.ink_probe("p.png", [(1, 2, 3, 4), (5, 6, 7, 8)], run=fake)


def test_ink_probe_single_rect_scalar_accepted():
    def fake(args, **kw):
        return subprocess.CompletedProcess(args, 0, '{"ink":0.5}', "")
    assert ocr.ink_probe("p.png", [(1, 2, 3, 4)], run=fake) == [0.5]


def test_chain_probes_prepped_image_and_cuts(tmp_path):
    tsv = _gap_tsv(gap=13)
    seen = []

    def run(args, **kw):
        if "-File" in args:
            out = json.dumps({"out": args[args.index("-Out") + 1], "scale": 3.0})
            return subprocess.CompletedProcess(args, 0, out, "")
        return subprocess.CompletedProcess(args, 0, tsv, "")

    def probe(image, rects):
        seen.append((Path(image).parent, Path(image).name.startswith("prep-"), len(rects)))
        return [0.0]

    doc = ocr.run_tesseract_chain(tmp_path / "a.png", tmp_path / "w", "t.exe", run=run,
                                  probe=probe)
    assert ocr.extract_silver(doc["lines"]) == 1234567
    assert seen == [(tmp_path / "w", True, 1)]  # the prepped image, one call


def test_cache_version_bumped_for_gap_fix():
    assert ocr.CACHE_VERSION == "3"


def test_ink_ps1_ascii_lf():
    raw = ocr.INK_PS1.read_bytes()
    assert b"\r" not in raw and all(b < 128 for b in raw)
    assert b"GetBrightness" in raw and b"ConvertTo-Json" in raw


def test_bench_gap_cases():
    sys.path.insert(0, str(ROOT / "tools"))
    import ocr_bench
    gs = ocr_bench.gap_cases(quick=True)
    assert {c["kind"] for c in gs} == {"gap1", "gap2"}
    c = next(c for c in gs if c["kind"] == "gap1")
    assert c["text"].endswith(" 100") and not c["text"].endswith("  100")
    assert ocr_bench.score(c, {"lines": [L(c["text"][:-4]), L("100", x=400)]})
    assert not ocr_bench.score(c, {"lines": [L(c["text"])]})


def test_probe_never_cuts_before_a_grouped_tail():
    # bench c0004: "21 187,096,269" (comma invisible after prep, no ink) must
    # stay one number - a grouped tail is the rest of the amount.
    out = TSV_HEAD + _tsv_row(1, 1, 0, 0, 120, 30, "Silver:") \
        + _tsv_row(1, 2, 132, 0, 40, 30, "21") \
        + _tsv_row(1, 3, 182, 0, 200, 30, "187,096,269")
    doc, bounds = ocr._parse_tsv(out)
    assert len(bounds) == 1
    probe = FakeProbe([0.0])
    assert ocr.split_spaced_amount(doc, bounds, "p.png", probe) == doc
    assert probe.calls == [] and ocr.extract_silver(doc["lines"]) == 21187096269
