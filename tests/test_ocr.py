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
def test_ps1_parses_in_windows_powershell():
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
