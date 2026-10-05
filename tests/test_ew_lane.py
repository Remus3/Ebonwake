import contextlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import ew_lane  # noqa: E402


def test_three_named_lanes_each_own_worktree(tmp_path):
    assert ew_lane.LANES == ("build", "data", "review") and ew_lane.MAX_SLOTS == 3
    paths = {ew_lane.worktree_path(n, tmp_path / "repo") for n in ew_lane.LANES}
    assert len(paths) == 3
    with pytest.raises(SystemExit):
        ew_lane.worktree_path("rogue", tmp_path)


def test_ensure_worktree_idempotent(tmp_path):
    calls = []
    root = tmp_path / "repo"
    root.mkdir()
    path, made = ew_lane.ensure_worktree("build", root, git=lambda *a: calls.append(a))
    assert made and calls[0][:3] == ("worktree", "add", "-B")
    (path / ".git").parent.mkdir(parents=True, exist_ok=True)
    (path / ".git").write_text("gitdir: x")
    _, made2 = ew_lane.ensure_worktree("build", root, git=lambda *a: calls.append(a))
    assert not made2 and len(calls) == 1


def _fakes(rc=0):
    seen = {"progress": [], "spawn": [], "hold": [], "record": []}

    @contextlib.contextmanager
    def hold(n, **kw):
        seen["hold"].append((n, kw))
        yield "slot0"

    def spawn(root, code, prompt, **kw):
        seen["spawn"].append((code, kw))
        return {"rc": rc, "error": None}

    def progress(root, task, pct, step, eta_s, status):
        seen["progress"].append((task, pct, status))

    def record(kind, secs, ok=True):
        seen["record"].append((kind, ok))

    return seen, dict(hold=hold, spawn=spawn, progress=progress, record=record)


def test_run_lane_holds_slot_spawns_via_kit_records_eta(tmp_path):
    seen, f = _fakes()
    line = ew_lane.run_lane("data", "do x", root=tmp_path, **f)
    assert line["rc"] == 0
    assert seen["hold"][0][0] == 3 and seen["hold"][0][1]["repo"] == "EW"
    code, kw = seen["spawn"][0]
    assert code == "EW" and kw["cwd"] == ew_lane.worktree_path("data", tmp_path)
    assert seen["record"] == [("lane-data-read", True)]
    assert seen["progress"][-1] == ("lane-data", 100, "done")


def test_failed_run_marks_failed(tmp_path):
    seen, f = _fakes(rc=1)
    ew_lane.run_lane("build", "x", writes_code=True, root=tmp_path, **f)
    assert seen["record"] == [("lane-build-code", False)]
    assert seen["progress"][-1][2] == "failed"


def test_dry_run_spawns_nothing(tmp_path):
    seen, f = _fakes()
    out = ew_lane.run_lane("review", "x", dry_run=True, root=tmp_path, **f)
    assert out["dry_run"] and seen["spawn"] == [] and seen["hold"] == []
