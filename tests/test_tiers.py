"""Plan 097: test tiers (slow / server / git), the fast tier, the tier guard,
xdist at the loop gate and the tools/ew_tests.py fast-tier wrapper."""

import configparser
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
import ew_loop  # noqa: E402
import ew_tests  # noqa: E402
import ew_tiers  # noqa: E402


# ---------------------------------------------------------------- markers

def test_pytest_ini_registers_the_tiers_strictly():
    cp = configparser.ConfigParser()
    cp.read(ROOT / "pytest.ini", encoding="utf-8")
    names = {line.split(":", 1)[0].strip() for line in cp["pytest"]["markers"].splitlines()
             if line.strip()}
    assert {"slow", "server", "git"} <= names
    assert "--strict-markers" in cp["pytest"]["addopts"].split()


def test_fast_markexpr_drops_slow_and_git_only():
    assert ew_tiers.FAST_EXCLUDES == ("slow", "git")
    assert ew_tiers.FAST_MARKEXPR == "not slow and not git"


@pytest.mark.parametrize("args,want", [
    (["git", "status"], True),
    (["/opt/Git/cmd/git.exe", "init"], True),
    ("git -C x log", True),
    (b"git status", True),
    ([Path("/usr/bin/git"), "init"], True),
    (["python", "-m", "pytest"], False),
    (["gitk"], False),
    ("", False),
    ([], False),
    (None, False),
])
def test_is_git(args, want):
    assert ew_tiers.is_git(args) is want


def test_problems_name_the_missing_marker():
    assert ew_tiers.problems(set(), saw_git=False, saw_bind=False) == []
    p = ew_tiers.problems(set(), saw_git=True, saw_bind=True)
    assert any("git" in x for x in p) and any("server" in x for x in p)
    assert ew_tiers.problems({"git", "server"}, saw_git=True, saw_bind=True) == []


def test_slow_warning_only_for_unmarked_long_calls():
    assert ew_tiers.slow_warning(set(), ew_tiers.SLOW_WARN_S + 1)
    assert not ew_tiers.slow_warning({"slow"}, ew_tiers.SLOW_WARN_S + 1)
    assert not ew_tiers.slow_warning(set(), ew_tiers.SLOW_WARN_S - 1)


# ------------------------------------------------- guard + --fast, end to end

INNER = '''
import socket
import subprocess

import pytest


def _git():
    subprocess.run(["git", "--version"], capture_output=True, check=True)


def _hidden_bind():
    # the source scan cannot see this bind; only the audit guard can
    s = socket.socket()
    try:
        getattr(s, "bi" + "nd")(("127.0.0.1", 0))
    finally:
        s.close()


def _helper():
    _visible_bind()


def _visible_bind():
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
    finally:
        s.close()


@pytest.fixture()
def srv():
    _visible_bind()
    yield


def test_plain():
    assert True


def test_git_unmarked():
    _git()


@pytest.mark.git
def test_git_marked():
    _git()


def test_bind_unmarked():
    _hidden_bind()


@pytest.mark.server
def test_bind_marked():
    _hidden_bind()


def test_bind_auto_by_helper():
    _helper()


def test_bind_auto_by_fixture(srv):
    pass


@pytest.mark.slow
def test_slow_marked():
    assert True
'''


def _inner_world(tmp_path):
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\naddopts = -p no:cacheprovider --strict-markers\n"
        "markers =\n    slow: s\n    server: v\n    git: g\n", encoding="utf-8")
    (tmp_path / "conftest.py").write_text(
        "import sys\nsys.path.insert(0, %r)\nimport ew_tiers\new_tiers.install(globals())\n"
        % str(ROOT / "tests"), encoding="utf-8")
    (tmp_path / "test_inner.py").write_text(INNER, encoding="utf-8")


def _inner(tmp_path, *extra):
    r = subprocess.run([sys.executable, "-m", "pytest", "-rA", "-p", "no:xdist",
                        "test_inner.py", *extra], cwd=str(tmp_path),
                       capture_output=True, text=True, timeout=120)
    return r.stdout + r.stderr


def _outcome(out, name):
    for line in out.splitlines():
        if line.endswith("test_inner.py::" + name) or ("test_inner.py::" + name + " ") in line:
            return line.split()[0]
    return None


def test_guard_fails_unmarked_git_and_bind_and_passes_marked(tmp_path):
    _inner_world(tmp_path)
    out = _inner(tmp_path)
    assert _outcome(out, "test_plain") == "PASSED", out
    assert _outcome(out, "test_git_marked") == "PASSED", out
    assert _outcome(out, "test_bind_marked") == "PASSED", out
    assert _outcome(out, "test_slow_marked") == "PASSED", out
    assert _outcome(out, "test_git_unmarked") == "FAILED", out
    assert _outcome(out, "test_bind_unmarked") == "FAILED", out
    assert "@pytest.mark.git" in out and "@pytest.mark.server" in out
    assert _outcome(out, "test_bind_auto_by_helper") == "PASSED", out
    assert _outcome(out, "test_bind_auto_by_fixture") == "PASSED", out


def test_source_scan_auto_marks_server_tests(tmp_path):
    _inner_world(tmp_path)
    out = _inner(tmp_path, "-m", "server")
    for name in ("test_bind_marked", "test_bind_auto_by_helper", "test_bind_auto_by_fixture"):
        assert _outcome(out, name) == "PASSED", out
    for name in ("test_plain", "test_bind_unmarked", "test_git_marked"):
        assert _outcome(out, name) is None, out


def test_fast_deselects_slow_and_git_and_keeps_server(tmp_path):
    _inner_world(tmp_path)
    out = _inner(tmp_path, "--fast")
    assert _outcome(out, "test_git_marked") is None, out
    assert _outcome(out, "test_slow_marked") is None, out
    assert _outcome(out, "test_bind_marked") == "PASSED", out
    assert _outcome(out, "test_plain") == "PASSED", out
    assert "2 deselected" in out, out


# ---------------------------------------------------------------- loop gate

def test_gate_adds_xdist_loadfile_when_present():
    argv = ew_loop._gate_argv("python -m pytest -q", xdist=True)
    i = argv.index("--")
    assert "fleet_suite_gate.py" in argv[1]
    assert argv[i + 2:] == ["-m", "pytest", "-q", "-n", "4", "--dist", "loadfile"]


def test_gate_runs_serially_without_xdist():
    argv = ew_loop._gate_argv("python -m pytest -q", xdist=False)
    assert argv[argv.index("--") + 2:] == ["-m", "pytest", "-q"]


def test_gate_leaves_an_explicit_worker_count_alone():
    for cmd in ("python -m pytest -q -n 2", "python -m pytest -q -n2",
                "python -m pytest -q --numprocesses=2", "python -m pytest -q -p no:xdist"):
        argv = ew_loop._gate_argv(cmd, xdist=True)
        assert argv[argv.index("--") + 2:] == cmd.split()[1:], cmd


def test_gate_never_adds_xdist_to_a_targeted_run_or_other_tools():
    assert ew_loop._gate_argv("python -m pytest -q tests/test_x.py", xdist=True)[1:] == \
        ["-m", "pytest", "-q", "tests/test_x.py"]
    assert "-n" not in ew_loop._gate_argv("python -m ruff check server", xdist=True)


def test_xdist_detection_is_an_import_probe(monkeypatch):
    import importlib.util
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert ew_loop._xdist_available() is False
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    assert ew_loop._xdist_available() is True


# ------------------------------------------------------- fast-tier wrapper

def test_ew_tests_fast_argv_goes_through_the_suite_gate():
    argv = ew_tests.fast_argv("s1.main", [], xdist=False)
    i = argv.index("--")
    assert argv[1].replace("\\", "/").endswith("ops/fleet_kit/fleet_suite_gate.py")
    assert argv[2:i] == ["run", "--owner", "s1.main"]
    assert argv[i + 2:] == ["-m", "pytest", "-q", "--fast"]


def test_ew_tests_fast_argv_adds_xdist_and_extra_args():
    argv = ew_tests.fast_argv("s1.main", ["-x"], xdist=True)
    tail = argv[argv.index("--") + 2:]
    assert tail == ["-m", "pytest", "-q", "--fast", "-n", "4", "--dist", "loadfile", "-x"]


def test_ew_tests_main_passes_the_exit_code_through():
    seen = []
    rc = ew_tests.main(["fast", "--owner", "s1.main"], run=lambda a: seen.append(a) or 5)
    assert rc == 5 and seen and "--fast" in seen[0]


def test_ew_tests_main_requires_an_owner(capsys):
    with pytest.raises(SystemExit) as e:
        ew_tests.main(["fast"], run=lambda a: 0)
    assert e.value.code == 2


# ---------------------------------------------------------------- prompts

def test_producers_are_told_the_fast_tier_and_may_run_it():
    g = ew_loop.GATES.format(task="p012-build")
    assert "python tools/ew_tests.py fast" in g
    assert "fleet_suite_gate.py run" not in g
    import ew_lane
    assert "Bash(python tools/ew_tests.py:*)" in ",".join(ew_lane.CODE_EXTRA)
