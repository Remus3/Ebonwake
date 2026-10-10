"""pytest.ini keeps tmp_path dirs only for failed tests, at most 1 base dir
(MAIN FIX N7416e2, plan 105). The behavior test runs a child pytest against a
byte copy of the repo pytest.ini with its temp root (PYTEST_DEBUG_TEMPROOT),
rootdir and confcutdir under tmp_path, so nothing outside tmp_path is touched
or scanned."""

import configparser
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INI = ROOT / "pytest.ini"

CHILD = '''
def test_pass(tmp_path):
    (tmp_path / "mark").write_text("x")

def test_fail(tmp_path):
    (tmp_path / "mark").write_text("x")
    assert False
'''


def test_tmp_path_retention_configured():
    cfg = configparser.ConfigParser()
    cfg.read(INI, encoding="utf-8")
    assert cfg.get("pytest", "tmp_path_retention_policy") == "failed"
    assert cfg.get("pytest", "tmp_path_retention_count") == "1"


def _run(work, temproot, only=None):
    # No inherited PYTEST_* (ADDOPTS, PLUGINS, ...) reaches the child.
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PYTEST_")}
    env["PYTEST_DEBUG_TEMPROOT"] = str(temproot)
    target = "test_child.py" if only is None else f"test_child.py::{only}"
    # The child reads a byte copy of the repo pytest.ini placed in work, so its
    # inifile, rootdir and confcutdir are all work. With `-c <repo ini>` the
    # confcutdir was the repo root, work was "in" it, and collection built Dir
    # nodes from the drive root down through the shared user temp dir: an
    # entry another process deleted mid-scan crashed collection (rc 2).
    return subprocess.run(
        [sys.executable, "-m", "pytest", "--rootdir", str(work),
         "--confcutdir", str(work), "-p", "no:cacheprovider", "-q", target],
        cwd=work, env=env, capture_output=True, text=True, timeout=120,
    )


def _bases(temproot):
    return sorted(p for p in temproot.glob("pytest-of-*/pytest-*")
                  if p.is_dir() and not p.name.endswith("current"))


def test_retention_behavior(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / "test_child.py").write_text(CHILD, encoding="utf-8")
    (work / "pytest.ini").write_bytes(INI.read_bytes())
    temproot = tmp_path / "temproot"
    temproot.mkdir()
    for _ in range(3):
        res = _run(work, temproot)
        assert res.returncode == 1, res.stdout + res.stderr
    bases = _bases(temproot)
    assert len(bases) == 1, bases
    kept = sorted(p.name for p in bases[0].iterdir()
                  if p.is_dir() and not p.name.endswith("current"))
    assert kept == ["test_fail0"], kept
    res = _run(work, temproot, only="test_pass")
    assert res.returncode == 0, res.stdout + res.stderr
    # A green run removes its own base dir; at most 1 older base survives and
    # no passing test's dir is ever kept.
    bases = _bases(temproot)
    assert len(bases) <= 1, bases
    assert not list(temproot.glob("pytest-of-*/pytest-*/test_pass*"))
