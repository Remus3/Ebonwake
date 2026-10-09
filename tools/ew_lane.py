#!/usr/bin/env python3
"""EW laned headless driver (operator standing order 10, fleet kit v8).

    ew_lane.py status
    ew_lane.py run <lane> --prompt-file F [--writes-code] [--timeout S] [--dry-run]

Roster lanes: build, data, review. Up to LANE_CAP (3) run at once. Each run goes
through the kit's `fleet_lanes.run_lane` (repo lane lock + its OWN clean git
worktree `<repo parent>/ew-worktrees/lane-<i>`, detached at main's HEAD) and
spawns through `fleet_headless.spawn` ONLY (account B proxy, fail closed - FLEET
item 10) with exactly ONE governor slot taken AT the call (`governor="queued"`).
No slot is held around git. Every run writes a progress file (FLEET item 12)
and records its duration in the ETA log (`tools/eta.py`).

Lanes never commit. After a code run the main session commits in the printed
worktree (detached HEAD) and merges that commit into main with --no-ff. A lane
worktree left dirty is refused by the kit on the next claim (never cleaned), so
commit or hand-resolve it first. Gitignored config the gates need
(config/leak_needles.json, config/local.json) is copied into the worktree when
missing. The prompt file is read, never echoed to chat.
"""

import argparse
import importlib.util
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LANES = ("build", "data", "review")
LANE_CAP = 3  # v6 LANE_CAP_MAX; no measured reason for less (ADR 0007)
CODE = "EW"
SEED_CONFIG = ("config/leak_needles.json", "config/local.json")
# Headless -p has no prompt to approve edits; code lanes run in acceptEdits so
# file edits land in the lane worktree. Bash stays on the project allow list
# plus the gate commands below; commits and merges are done by the main session.
CODE_EXTRA = ("--permission-mode", "acceptEdits", "--allowedTools",
              "Bash(python -m pytest:*),Bash(npm test:*),Bash(node --test:*),"
              "Bash(python tools/leak_sweep.py:*),Bash(python -m ruff:*),"
              "Bash(python tools/ocr_bench.py:*),Bash(python tools/ew_tests.py:*)")
sys.path.insert(0, str(ROOT / "tools"))
import eta  # noqa: E402


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, mod)
    spec.loader.exec_module(mod)
    return mod


def kit():
    return _load("fleet_headless", "ops/fleet_kit/fleet_headless.py")


def lanes():
    return _load("fleet_lanes", "ops/fleet_kit/fleet_lanes.py")


def check_lane(lane):
    if lane not in LANES:
        raise SystemExit(f"unknown lane {lane!r}; lanes: {', '.join(LANES)}")
    return lane


def seed_config(worktree, root=ROOT):
    """Copy gitignored gate config into a lane worktree when missing. Idempotent."""
    copied = []
    for rel in SEED_CONFIG:
        src, dst = Path(root) / rel, Path(worktree) / rel
        if src.is_file() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            copied.append(rel)
    return copied


def progress_task(index):
    """FLEET item 13 d: a lane's progress file is progress/lane-<i>.json, i = its
    lane-lock index (kit `fleet_checklist.lane_task`), so the widget reads one
    named file per live lane. The lane name rides in the step text."""
    return _load("fleet_checklist", "ops/fleet_kit/fleet_checklist.py").lane_task(index)


def run_lane(lane, prompt, writes_code=False, timeout=3600, dry_run=False, root=ROOT,
             spawn=None, lane_ctx=None, progress=None, record=eta.record,
             clock=time.time):
    """One headless run on one lane. Returns the kit usage line plus "worktree".
    Progress goes to the MAIN checkout's progress/lane-<i>.json once the lane
    index is claimed; a dry run or a refused claim has no index and writes none."""
    check_lane(lane)
    run_id = f"lane-{lane}"
    if progress is None:
        progress = kit().write_progress
    kind = f"lane-{lane}-{'code' if writes_code else 'read'}"
    eta_s = eta.estimate(kind)
    if dry_run:
        return {"lane": lane, "dry_run": True, "eta": eta.fmt(eta_s)}
    if lane_ctx is None:
        lane_ctx = lanes().run_lane
    if spawn is None:
        spawn = kit().spawn
    main = lanes().main_tree(root)
    task = None
    t0 = clock()
    ok = False
    try:
        with lane_ctx(root, CODE, lane, run_id, cap=LANE_CAP) as claim:
            task = progress_task(claim["index"])
            wt = Path(claim["worktree"])
            seed_config(wt, root)
            progress(main, task, 20, f"{lane}: running headless in {wt.name}", eta_s,
                     "running")
            line = spawn(root, CODE, prompt, note=run_id, writes_code=writes_code,
                         timeout=timeout, cwd=wt, stdin=True,
                         extra=CODE_EXTRA if writes_code else (),
                         governor="queued", governor_timeout=timeout, kind="build")
        line = dict(line, worktree=str(wt))
        ok = line.get("rc") == 0 and not line.get("error")
        return line
    finally:
        record(kind, clock() - t0, ok=ok)
        if task is not None:
            progress(main, task, 100, f"{lane}: {'finished' if ok else 'failed'}", 0,
                     "done" if ok else "failed")


def status(root=ROOT):
    rows = []
    for row in lanes().repo_lane_state(root, LANE_CAP):
        rows.append(" ".join(f"{k}={v}" for k, v in sorted(row.items())))
    for lane in LANES:
        rows.append(f"{lane}: eta={eta.fmt(eta.estimate(f'lane-{lane}-code'))}")
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description="EW laned headless driver")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    r = sub.add_parser("run")
    r.add_argument("lane")
    r.add_argument("--prompt-file", required=True)
    r.add_argument("--writes-code", action="store_true")
    r.add_argument("--timeout", type=int, default=3600)
    r.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "status":
        print("\n".join(status()))
        return 0
    prompt = Path(a.prompt_file).read_text(encoding="utf-8")
    line = run_lane(a.lane, prompt, a.writes_code, a.timeout, a.dry_run)
    if a.dry_run:
        print(f"{a.lane}: dry-run ok")
        return 0
    print(f"{a.lane}: rc={line.get('rc')} error={line.get('error')} "
          f"worktree={line.get('worktree')}")
    return 0 if line.get("rc") == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
