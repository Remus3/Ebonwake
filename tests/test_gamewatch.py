"""Plan 008 slice A: session-log tail + ScreenShot watcher.

Every fixture is a synthetic UTF-16LE file written into tmp_path. No test reads
a real game log, a real config or runs the real tasklist.
"""

import http.client
import json
import os
import subprocess
import threading
import time

import pytest

from server.ew import app as ewapp
from server.ew import gamewatch, market

T0 = 1_800_000_000.0  # injected clock base (epoch seconds)


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


class Tasklist:
    """Injected process probe: returns bool, counts calls."""

    def __init__(self, listed=False):
        self.listed = listed
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.listed


def _line(log, date="2026-10-05 12:00:00", log_type="Info"):
    return json.dumps({"Date": date, "Thread": 1, "LogType": log_type, "ErrNo": 0,
                       "ErrStr": "", "Log": log}) + "\r\n"


def _write(path, text, bom=True, mode="wb", mtime=None):
    data = (b"\xff\xfe" if bom else b"") + text.encode("utf-16-le")
    with open(path, mode) as f:
        f.write(data)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def _append(path, text, mtime=None):
    with open(path, "ab") as f:
        f.write(text.encode("utf-16-le"))
    if mtime is not None:
        os.utime(path, (mtime, mtime))


@pytest.fixture()
def dirs(tmp_path):
    inst = tmp_path / "install"
    docs = tmp_path / "docs"
    (inst / "Log").mkdir(parents=True)
    (docs / "ScreenShot").mkdir(parents=True)
    return inst, docs


def _watch(dirs, clock=None, listed=False):
    inst, docs = dirs
    return gamewatch.GameWatch(install_dir=str(inst), documents_dir=str(docs),
                               clock=clock or Clock(), tasklist=Tasklist(listed))


LOGNAME = "Client_2026-10-05_120000.json"


# -- config -------------------------------------------------------------------

def test_config_bdo_reads_local_json(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "local.json").write_text(json.dumps(
        {"bdo": {"install_dir": "X1", "documents_dir": "X2"}}), encoding="utf-8")
    assert gamewatch.config_bdo(tmp_path) == {"install_dir": "X1", "documents_dir": "X2"}


@pytest.mark.parametrize("doc", [None, "[]", "{}", '{"bdo": 3}', "not json"])
def test_config_bdo_absent_is_empty(tmp_path, doc):
    if doc is not None:
        (tmp_path / "config").mkdir()
        (tmp_path / "config" / "local.json").write_text(doc, encoding="utf-8")
    assert gamewatch.config_bdo(tmp_path) == {}


def test_unconfigured_never_guesses(tmp_path):
    tl = Tasklist(True)
    w = gamewatch.GameWatch.from_config({}, clock=Clock(), tasklist=tl)
    w.poll()
    v = w.view()
    assert v["configured"] is False and v["state"] == "unconfigured"
    assert v["log_file"] is None and v["screenshots"] == []
    assert tl.calls == 0, "unconfigured watcher must not probe processes"


def test_partially_configured_install_only(dirs):
    inst, _ = dirs
    w = gamewatch.GameWatch.from_config({"install_dir": str(inst)}, clock=Clock(),
                                        tasklist=Tasklist(False))
    w.poll()
    v = w.view()
    assert v["configured"] is True and v["state"] == "not_running" and v["screenshots"] == []


@pytest.mark.parametrize("cfg", [{"install_dir": 5}, {"install_dir": ""},
                                 {"documents_dir": ["x"]}])
def test_bad_config_values_unconfigured(cfg):
    w = gamewatch.GameWatch.from_config(cfg, clock=Clock(), tasklist=Tasklist(True))
    w.poll()
    assert w.view()["state"] == "unconfigured"


# -- classifier table: one test per row ---------------------------------------

# One positive sample per row (same order as CLASSIFIERS).
ROW_SAMPLES = ("Session disconnected", "Reconnect failed (3)", "Connection lost to host",
               "Logout requested", "Client exit", "Login success", "Server select done",
               "SelectServer ok")


def test_samples_cover_every_row():
    assert len(ROW_SAMPLES) == len(gamewatch.CLASSIFIERS)


@pytest.mark.parametrize("i", range(len(gamewatch.CLASSIFIERS)))
def test_classifier_row(i):
    _, state = gamewatch.CLASSIFIERS[i]
    sample = ROW_SAMPLES[i]
    assert gamewatch.classify(sample) == state
    assert gamewatch.classify(sample.upper()) == state
    assert gamewatch.classify(f"xx {sample.lower()} yy") == state


def test_classifier_table_is_data_and_complete():
    states = {s for _, s in gamewatch.CLASSIFIERS}
    assert states == {"logged_in", "running", "disconnected"}
    assert all(p == p.lower() and p for p, _ in gamewatch.CLASSIFIERS)


@pytest.mark.parametrize("text", ["UI debug: panel opened", "", "auth host resolved",
                                  "Catalog index built", "Dialog initialized",
                                  "Load CatalogInfo table", "blogin", "texit code",
                                  "ExitGames sdk", "prologout"])
def test_unknown_lines_classify_none(text):
    assert gamewatch.classify(text) is None


def test_classifier_order_disconnect_beats_login():
    assert gamewatch.classify("login session disconnected") == "disconnected"


# -- line parser ---------------------------------------------------------------

def test_split_lines_keeps_partial_tail():
    raw = (_line("a") + _line("b") + '{"Date": "x", "Lo').encode("utf-16-le")
    lines, used = gamewatch.split_lines(raw)
    assert len(lines) == 2 and json.loads(lines[1])["Log"] == "b"
    assert raw[used:] == '{"Date": "x", "Lo'.encode("utf-16-le")


def test_split_lines_ignores_odd_aligned_newline_byte():
    # U+0A41 U+0100 encode as 41 0a 00 01: the 0a 00 at odd offset 1 is not a line end.
    odd = chr(0x0A41) + chr(0x0100)
    raw = (odd + "z\n").encode("utf-16-le")
    assert raw.find(b"\n\x00") == 1
    lines, used = gamewatch.split_lines(raw)
    assert lines == [odd + "z"] and used == len(raw)


def test_split_lines_odd_trailing_byte_waits():
    raw = "ab\n".encode("utf-16-le") + b"\x7b"
    lines, used = gamewatch.split_lines(raw)
    assert lines == ["ab"] and used == 6


def test_parse_line_shapes():
    assert gamewatch.parse_line(_line("hello").rstrip())["Log"] == "hello"
    assert gamewatch.parse_line(chr(0xFEFF) + _line("bom").rstrip())["Log"] == "bom"
    for bad in ["", "not json", "[]", '{"Date": "x"}', '{"Log": 3}']:
        assert gamewatch.parse_line(bad) is None


# -- session tail --------------------------------------------------------------

def test_no_log_not_running(dirs):
    w = _watch(dirs)
    w.poll()
    v = w.view()
    assert v["configured"] is True and v["state"] == "not_running"
    assert v["log_file"] is None and v["last_event"] is None


def test_login_then_logout_walk(dirs):
    inst, _ = dirs
    clk = Clock()
    w = _watch(dirs, clk)
    w.poll()
    assert w.view()["state"] == "not_running"
    p = inst / "Log" / LOGNAME
    _write(p, _line("Client started"), mtime=clk.t)
    w.poll()
    assert w.view()["state"] == "running" and w.view()["log_file"] == LOGNAME
    _append(p, _line("Login success", date="2026-10-05 12:01:00"), mtime=clk.t)
    w.poll()
    v = w.view()
    assert v["state"] == "logged_in"
    assert v["last_event"] == {"date": "2026-10-05 12:01:00", "type": "Info",
                               "log": "Login success", "state": "logged_in"}
    _append(p, _line("Logout requested"), mtime=clk.t)
    w.poll()
    assert w.view()["state"] == "running"
    _append(p, _line("Network disconnected"), mtime=clk.t)
    w.poll()
    assert w.view()["state"] == "disconnected"


def test_unknown_lines_change_nothing(dirs):
    inst, _ = dirs
    clk = Clock()
    w = _watch(dirs, clk)
    p = inst / "Log" / LOGNAME
    _write(p, _line("Login ok"), mtime=clk.t)
    w.poll()
    since = w.view()["since"]
    clk.t += 10
    _append(p, _line("UI debug noise") + _line("more noise"), mtime=clk.t)
    w.poll()
    v = w.view()
    assert v["state"] == "logged_in" and v["since"] == since
    assert v["last_event"]["log"] == "Login ok"


def test_partial_trailing_line_kept_for_next_poll(dirs):
    inst, _ = dirs
    clk = Clock()
    w = _watch(dirs, clk)
    p = inst / "Log" / LOGNAME
    full = _line("Login ok")
    _write(p, _line("boot") + full[:20], mtime=clk.t)
    w.poll()
    assert w.view()["state"] == "running"
    _append(p, full[20:], mtime=clk.t)
    w.poll()
    assert w.view()["state"] == "logged_in"


def test_no_bom_tolerated(dirs):
    inst, _ = dirs
    clk = Clock()
    w = _watch(dirs, clk)
    _write(inst / "Log" / LOGNAME, _line("Login ok"), bom=False, mtime=clk.t)
    w.poll()
    assert w.view()["state"] == "logged_in"


def test_offset_means_lines_not_reapplied(dirs):
    inst, _ = dirs
    clk = Clock()
    w = _watch(dirs, clk)
    p = inst / "Log" / LOGNAME
    _write(p, _line("Login ok") + _line("Logout"), mtime=clk.t)
    w.poll()
    assert w.view()["state"] == "running"
    w.poll()  # nothing new: the login line must not be replayed
    assert w.view()["state"] == "running"


def test_newest_log_by_name_stamp_and_reset_on_change(dirs):
    inst, _ = dirs
    clk = Clock()
    w = _watch(dirs, clk)
    old = inst / "Log" / "Client_2026-10-04_090000.json"
    _write(old, _line("Login ok"), mtime=clk.t)
    (inst / "Log" / "Client.log").write_bytes(b"\x00encrypted")
    (inst / "Log" / "Client_bad.json").write_bytes(b"")
    w.poll()
    assert w.view()["log_file"] == old.name and w.view()["state"] == "logged_in"
    new = inst / "Log" / "Client_2026-10-05_080000.json"
    # newer stamp but OLDER mtime: name stamp wins
    _write(new, _line("Client started"), mtime=clk.t - 5)
    w.poll()
    v = w.view()
    assert v["log_file"] == new.name
    assert v["state"] == "running", "new session file starts from offset 0, no carried login"


def test_truncated_file_restarts_at_zero(dirs):
    inst, _ = dirs
    clk = Clock()
    w = _watch(dirs, clk)
    p = inst / "Log" / LOGNAME
    _write(p, _line("boot") * 5 + _line("Logout"), mtime=clk.t)
    w.poll()
    _write(p, _line("Login ok"), mtime=clk.t)
    w.poll()
    assert w.view()["state"] == "logged_in"


def test_bad_json_lines_skipped(dirs):
    inst, _ = dirs
    clk = Clock()
    w = _watch(dirs, clk)
    _write(inst / "Log" / LOGNAME, "garbage\r\n[1,2]\r\n" + _line("Login ok"), mtime=clk.t)
    w.poll()
    assert w.view()["state"] == "logged_in"


def test_last_event_log_text_capped_and_clean(dirs):
    inst, _ = dirs
    clk = Clock()
    w = _watch(dirs, clk)
    _write(inst / "Log" / LOGNAME, _line("Login \x01" + "x" * 500), mtime=clk.t)
    w.poll()
    log = w.view()["last_event"]["log"]
    assert len(log) <= gamewatch.MAX_EVENT_TEXT and "\x01" not in log


# -- running rule ---------------------------------------------------------------

def test_stale_log_without_process_is_not_running(dirs):
    inst, _ = dirs
    clk = Clock()
    w = _watch(dirs, clk)
    _write(inst / "Log" / LOGNAME, _line("Login ok"), mtime=clk.t - 121)
    w.poll()
    assert w.view()["state"] == "not_running"


def test_recent_log_within_120s_is_running(dirs):
    inst, _ = dirs
    clk = Clock()
    w = _watch(dirs, clk)
    _write(inst / "Log" / LOGNAME, _line("boot"), mtime=clk.t - 119)
    w.poll()
    assert w.view()["state"] == "running"


def test_process_listed_keeps_logged_in_with_idle_log(dirs):
    inst, _ = dirs
    clk = Clock()
    w = _watch(dirs, clk, listed=True)
    _write(inst / "Log" / LOGNAME, _line("Login ok"), mtime=clk.t - 3600)
    w.poll()
    assert w.view()["state"] == "logged_in"


def test_process_listed_no_log_is_running(dirs):
    w = _watch(dirs, listed=True)
    w.poll()
    assert w.view()["state"] == "running"


def test_game_exit_clears_session_state(dirs):
    inst, _ = dirs
    clk = Clock()
    tl = Tasklist(True)
    inst_, docs = dirs
    w = gamewatch.GameWatch(install_dir=str(inst_), documents_dir=str(docs), clock=clk,
                            tasklist=tl)
    _write(inst / "Log" / LOGNAME, _line("Login ok"), mtime=clk.t)
    w.poll()
    assert w.view()["state"] == "logged_in"
    tl.listed = False
    clk.t += 200
    w.poll()
    assert w.view()["state"] == "not_running"
    tl.listed = True  # relaunched, new log not yet written
    w.poll()
    assert w.view()["state"] == "running"


def test_since_tracks_state_changes(dirs):
    inst, _ = dirs
    clk = Clock()
    w = _watch(dirs, clk)
    w.poll()
    s0 = w.view()["since"]
    clk.t += 30
    _write(inst / "Log" / LOGNAME, _line("Login ok"), mtime=clk.t)
    w.poll()
    s1 = w.view()["since"]
    assert s0 != s1 and s1.endswith("+00:00")


def test_seq_bumps_only_on_change(dirs):
    inst, _ = dirs
    clk = Clock()
    w = _watch(dirs, clk)
    w.poll()
    a = w.seq
    w.poll()
    assert w.seq == a
    _write(inst / "Log" / LOGNAME, _line("Login ok"), mtime=clk.t)
    w.poll()
    assert w.seq == a + 1
    assert w.wait_change(a, 0.01) == a + 1


# -- tasklist probe -------------------------------------------------------------

def test_tasklist_command_and_parse():
    seen = {}

    def run(args, **kw):
        seen["args"], seen["kw"] = args, kw
        return subprocess.CompletedProcess(
            args, 0, stdout='"BlackDesert64.exe","4242","Console","1","2,000,000 K"\r\n')

    assert gamewatch.process_listed(run=run) is True
    args = seen["args"]
    assert args[0].lower().endswith("tasklist.exe") or args[0].lower() == "tasklist"
    assert args[1:] == ["/FI", "IMAGENAME eq BlackDesert64.exe", "/FO", "CSV", "/NH"]
    assert seen["kw"].get("shell") is not True
    assert seen["kw"]["creationflags"] == gamewatch._NO_WINDOW
    assert seen["kw"]["timeout"] <= 10


@pytest.mark.parametrize("out", ["INFO: No tasks are running which match the specified "
                                 "criteria.\r\n", "", '"BlackDesert64.exe.bak","1"\r\n'])
def test_tasklist_not_listed(out):
    def run(args, **kw):
        return subprocess.CompletedProcess(args, 0, stdout=out)
    assert gamewatch.process_listed(run=run) is False


def test_tasklist_failure_is_not_listed():
    def run(args, **kw):
        raise OSError("no tasklist")
    assert gamewatch.process_listed(run=run) is False

    def slow(args, **kw):
        raise subprocess.TimeoutExpired(args, 5)
    assert gamewatch.process_listed(run=slow) is False


def test_module_never_touches_game_process():
    src = open(gamewatch.__file__, encoding="utf-8").read()
    for banned in ("OpenProcess", "ReadProcessMemory", "ctypes", "psutil", "SendInput",
                   "keybd_event", "PostMessage", "win32api", "shell=True"):
        assert banned not in src, banned


# -- screenshots ----------------------------------------------------------------

def test_screenshots_newer_than_start_newest_first(dirs):
    _, docs = dirs
    clk = Clock()
    ss = docs / "ScreenShot"
    old = ss / "old.jpg"
    old.write_bytes(b"x")
    os.utime(old, (clk.t - 10, clk.t - 10))
    w = _watch(dirs, clk)
    for i in range(3):
        p = ss / f"shot{i}.jpg"
        p.write_bytes(b"y" * (i + 1))
        os.utime(p, (clk.t + i, clk.t + i))
    (ss / "sub").mkdir()
    w.poll()
    shots = w.view()["screenshots"]
    assert [s["name"] for s in shots] == ["shot2.jpg", "shot1.jpg", "shot0.jpg"]
    assert shots[0]["size"] == 3 and shots[0]["mtime"].endswith("+00:00")
    assert set(shots[0]) == {"name", "size", "mtime"}


def test_screenshots_capped_at_50(dirs):
    _, docs = dirs
    clk = Clock()
    w = _watch(dirs, clk)
    for i in range(60):
        p = docs / "ScreenShot" / f"s{i:02d}.png"
        p.write_bytes(b"z")
        os.utime(p, (clk.t + i, clk.t + i))
    w.poll()
    shots = w.view()["screenshots"]
    assert len(shots) == gamewatch.MAX_SHOTS == 50 and shots[0]["name"] == "s59.png"


def test_screenshots_non_images_ignored(dirs):
    _, docs = dirs
    clk = Clock()
    w = _watch(dirs, clk)
    for n in ("a.txt", "b.ini", "c.JPG"):
        p = docs / "ScreenShot" / n
        p.write_bytes(b"z")
        os.utime(p, (clk.t + 1, clk.t + 1))
    w.poll()
    assert [s["name"] for s in w.view()["screenshots"]] == ["c.JPG"]


def test_screenshot_dir_missing_is_empty(tmp_path):
    inst = tmp_path / "i"
    (inst / "Log").mkdir(parents=True)
    w = gamewatch.GameWatch(install_dir=str(inst), documents_dir=str(tmp_path / "nope"),
                            clock=Clock(), tasklist=Tasklist(False))
    w.poll()
    assert w.view()["screenshots"] == []


def test_view_shape_and_no_paths(dirs):
    inst, _ = dirs
    clk = Clock()
    w = _watch(dirs, clk)
    _write(inst / "Log" / LOGNAME, _line("Login ok"), mtime=clk.t)
    w.poll()
    v = w.view()
    assert set(v) == {"state", "since", "log_file", "last_event", "screenshots", "configured"}
    text = json.dumps(v)
    assert str(inst) not in text and "\\\\" not in text
    src = w.source()
    assert src["status"] == "logged_in" and set(src) == {"updated", "status"}


# -- thread -------------------------------------------------------------------

def test_poll_thread_start_stop(dirs):
    w = _watch(dirs)
    w.start(interval=0.01)
    try:
        assert _wait(lambda: w.view()["since"] is not None)
    finally:
        w.stop()
    assert not w.running()


def test_poll_errors_never_kill_thread(dirs):
    w = _watch(dirs)
    calls = []

    def boom():
        calls.append(1)
        raise RuntimeError("probe failed")
    w.tasklist = boom
    w.start(interval=0.01)
    try:
        assert _wait(lambda: len(calls) >= 3)
        assert w.running()
    finally:
        w.stop()


def _wait(pred, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


# -- routes -------------------------------------------------------------------

def _no_network(url, timeout):
    raise AssertionError("test touched the network")


@pytest.fixture()
def gsrv(tmp_path, dirs):
    clk = Clock(time.time())
    w = _watch(dirs, clk)
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          sse_interval=0.05, market_seed=[],
                          market_client=market.ArshaClient(fetch=_no_network,
                                                           cache_dir=tmp_path / "cache"),
                          profile_cfg={}, game_watch=w)
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield s, w, clk
    s.shutdown()
    s.server_close()


def _get(s, path):
    c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=5)
    c.request("GET", path)
    r = c.getresponse()
    out = r.read()
    c.close()
    return r.status, json.loads(out)


def test_route_get_game(gsrv):
    s, w, _ = gsrv
    w.poll()
    st, doc = _get(s, "/api/game")
    assert st == 200 and doc["state"] == "not_running" and doc["configured"] is True


def test_route_state_sources_game(gsrv):
    s, w, _ = gsrv
    st, doc = _get(s, "/api/state")
    assert st == 200 and "game" in doc["sources"]
    w.poll()
    assert _get(s, "/api/state")[1]["sources"]["game"]["status"] == "not_running"


def test_route_sse_pushes_game_event_on_change(gsrv, dirs):
    s, w, clk = gsrv
    inst, _ = dirs
    w.poll()
    c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=5)
    c.request("GET", "/events")
    r = c.getresponse()
    assert b"heartbeat" in r.fp.readline()
    _write(inst / "Log" / LOGNAME, _line("Login ok"), mtime=clk.t)
    w.poll()
    got = None
    end = time.monotonic() + 3
    while time.monotonic() < end:
        line = r.fp.readline()
        if line.startswith(b"event: game"):
            data = r.fp.readline()
            assert data.startswith(b"data: ")
            got = json.loads(data[6:])
            break
    c.close()
    assert got is not None and got["state"] == "logged_in"


def test_server_poll_thread_disabled_by_default(tmp_path, dirs):
    w = _watch(dirs)
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", market_seed=[],
                          profile_cfg={}, game_watch=w)
    try:
        assert not w.running() and w.tasklist.calls == 0
    finally:
        s.server_close()


def test_server_game_poll_starts_and_close_stops(tmp_path, dirs):
    w = _watch(dirs)
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", market_seed=[],
                          profile_cfg={}, game_watch=w, game_poll=True)
    try:
        assert _wait(lambda: w.tasklist.calls >= 1) and w.running()
    finally:
        s.server_close()
    assert not w.running()


def test_server_game_cfg_injection_never_reads_real_config(tmp_path):
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", market_seed=[],
                          profile_cfg={}, game_cfg={})
    try:
        assert s.game.view()["state"] == "unconfigured"
    finally:
        s.server_close()


def test_bare_make_server_never_reads_real_config(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(gamewatch, "config_bdo", lambda root: calls.append(root) or {})
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", market_seed=[],
                          profile_cfg={})
    try:
        assert calls == [] and s.game.view()["state"] == "unconfigured"
    finally:
        s.server_close()
