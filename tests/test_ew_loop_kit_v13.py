"""Kit v11-v13 wiring in tools/ew_loop.py: triage kwargs against the REAL
vendored fleet_inbox signature (adjudication D2: keep ref 930fe43 called it
with no argument), whole-suite gate routing, and the git lock on commit/push."""

import contextlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import ew_lane  # noqa: E402
import ew_loop  # noqa: E402


def test_triage_kwargs_use_real_kit_signature_bare():
    fi = ew_lane._load("fleet_inbox", "ops/fleet_kit/fleet_inbox.py")
    kw = ew_loop.triage_spawn_kwargs(fi)
    assert kw["bare"] is True and kw["floors_in_hooks"] is False
    assert kw["model"] == "sonnet" and kw["effort"] == "low"


def test_whole_suite_detection():
    assert ew_loop._whole_suite(["py", "-m", "pytest", "-q"])
    assert not ew_loop._whole_suite(["py", "-m", "pytest", "-q", "tests/test_x.py"])
    assert not ew_loop._whole_suite(["py", "-m", "pytest", "tests/test_x.py::t"])
    assert not ew_loop._whole_suite(["npm", "test", "--prefix", "app"])
    assert not ew_loop._whole_suite(["py", "-m", "ruff", "check", "tools"])


def test_gate_argv_routes_whole_pytest_through_suite_gate():
    argv = ew_loop._gate_argv("python -m pytest -q", xdist=False)  # xdist: test_tiers.py
    i = argv.index("--")
    assert argv[1].replace("\\", "/").endswith("ops/fleet_kit/fleet_suite_gate.py")
    assert argv[2:5] == ["run", "--owner", ew_loop._owner()]
    assert argv[i + 1:][1:] == ["-m", "pytest", "-q"]
    assert ew_loop._gate_argv("python -m ruff check server tools tests")[1:] == \
        ["-m", "ruff", "check", "server", "tools", "tests"]


def test_git_commit_and_push_take_the_lock_other_verbs_do_not(monkeypatch):
    calls, taken = [], []

    @contextlib.contextmanager
    def lock(cwd, owner, verb):
        taken.append((owner, verb))
        yield cwd

    monkeypatch.setattr(ew_loop, "_git_run",
                        lambda a, c, i=None: calls.append(a) or
                        subprocess.CompletedProcess(a, 0, "", ""))
    ew_loop._git(["status"], ".", lock=lock)
    ew_loop._git(["commit", "-q", "-F", "-"], ".", input="m", lock=lock)
    ew_loop._git(["push", "origin", "main"], ".", lock=lock)
    assert [v for _, v in taken] == ["commit", "push"]
    assert len(calls) == 3


def test_git_lock_failure_is_a_failed_call_not_a_crash(monkeypatch):
    @contextlib.contextmanager
    def lock(cwd, owner, verb):
        raise RuntimeError("held")
        yield  # pragma: no cover

    monkeypatch.setattr(ew_loop, "_git_run", lambda *a, **k: 1 / 0)
    r = ew_loop._git(["commit", "-m", "x"], ".", lock=lock)
    assert r.returncode == 3 and "git lock" in r.stderr
