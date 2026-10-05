import contextlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import ew_lane  # noqa: E402


def test_roster_and_cap():
    assert ew_lane.LANES == ("build", "data", "review") and ew_lane.LANE_CAP == 3
    with pytest.raises(SystemExit):
        ew_lane.check_lane("rogue")


def test_kit_worktree_layout_is_per_lane_index(tmp_path):
    fl = ew_lane.lanes()
    assert fl.LANE_CAP_MAX == 3
    paths = {fl.worktree_path(ROOT, "EW", i) for i in range(3)}
    assert len(paths) == 3
    assert all(p.parent.name == "ew-worktrees" for p in paths)


def test_seed_config_copies_missing_only(tmp_path):
    root, wt = tmp_path / "repo", tmp_path / "wt"
    (root / "config").mkdir(parents=True)
    (root / "config" / "local.json").write_text("{}")
    assert ew_lane.seed_config(wt, root) == ["config/local.json"]
    assert ew_lane.seed_config(wt, root) == []


def _fakes(tmp_path, rc=0):
    seen = {"progress": [], "spawn": [], "lane": [], "record": []}
    wt = tmp_path / "ew-worktrees" / "lane-1"

    @contextlib.contextmanager
    def lane_ctx(root, code, name, run_id, cap):
        seen["lane"].append((code, name, run_id, cap))
        yield {"worktree": wt, "index": 1}

    def spawn(root, code, prompt, **kw):
        seen["spawn"].append((code, kw))
        return {"rc": rc, "error": None}

    def progress(root, task, pct, step, eta_s, status):
        seen["progress"].append((task, pct, status))

    def record(kind, secs, ok=True):
        seen["record"].append((kind, ok))

    return seen, wt, dict(lane_ctx=lane_ctx, spawn=spawn, progress=progress, record=record)


def test_run_lane_claims_lane_one_governor_slot_at_spawn(tmp_path):
    seen, wt, f = _fakes(tmp_path)
    line = ew_lane.run_lane("data", "do x", root=tmp_path, **f)
    assert line["rc"] == 0 and line["worktree"] == str(wt)
    assert seen["lane"] == [("EW", "data", "lane-data", 3)]
    code, kw = seen["spawn"][0]
    assert code == "EW" and kw["cwd"] == wt and kw["governor"] == "queued"
    assert seen["record"] == [("lane-data-read", True)]
    assert seen["progress"][-1] == ("lane-data", 100, "done")


def test_failed_run_marks_failed(tmp_path):
    seen, _, f = _fakes(tmp_path, rc=1)
    ew_lane.run_lane("build", "x", writes_code=True, root=tmp_path, **f)
    assert seen["record"] == [("lane-build-code", False)]
    assert seen["progress"][-1][2] == "failed"


def test_dry_run_spawns_nothing(tmp_path):
    seen, _, f = _fakes(tmp_path)
    out = ew_lane.run_lane("review", "x", dry_run=True, root=tmp_path, **f)
    assert out["dry_run"] and seen["spawn"] == [] and seen["lane"] == []


def test_code_lane_runs_accept_edits_read_lane_does_not(tmp_path):
    seen, _, f = _fakes(tmp_path)
    ew_lane.run_lane("build", "do x", writes_code=True, root=tmp_path, **f)
    ew_lane.run_lane("review", "look", root=tmp_path, **f)
    extra = seen["spawn"][0][1]["extra"]
    assert extra[:2] == ("--permission-mode", "acceptEdits")
    assert "Bash(python -m pytest:*)" in extra[3] and "git" not in extra[3]
    assert seen["spawn"][1][1]["extra"] == ()
