#!/usr/bin/env python3
"""EW laned headless driver (operator standing order 10).

    ew_lane.py status                         lanes, worktrees, ETA per lane
    ew_lane.py ensure <lane>                  create the lane's worktree (idempotent)
    ew_lane.py run <lane> --prompt-file F [--writes-code] [--timeout S] [--dry-run]

Up to 3 named lanes (build, data, review). Each lane has its OWN git worktree
(`<repo parent>/ew-worktrees/<lane>`, branch `lane/<lane>`), so a CI wait on one
lane never blocks another. Each run holds ONE machine-wide slot from the shared
governor `ops/loop/slots.py` (max_slots=3) around the executor call only, and
spawns through the fleet kit's `fleet_headless.spawn` ONLY (account B proxy,
fail closed - FLEET item 10). Every run writes a progress file (FLEET item 12)
and records its duration in the ETA log (`tools/eta.py`).

Idempotent: `ensure` on an existing worktree is a no-op; a lane whose progress
file says running with a live slot is not started twice (slots refuse it).
The prompt file is read, never echoed to chat.
"""

import argparse
import importlib.util
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LANES = ("build", "data", "review")
MAX_SLOTS = 3
CODE = "EW"
# Headless -p has no prompt to approve edits; code lanes run in acceptEdits so
# file edits land in the lane worktree. Bash stays on the project allow list
# plus the gate commands below; commits and merges are done by the main session.
CODE_EXTRA = ("--permission-mode", "acceptEdits", "--allowedTools",
              "Bash(python -m pytest:*),Bash(npm test:*),Bash(node --test:*),"
              "Bash(python tools/leak_sweep.py:*)")
sys.path.insert(0, str(ROOT / "tools"))
import eta  # noqa: E402


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def kit():
    return _load("fleet_headless", "ops/fleet_kit/fleet_headless.py")


def slots():
    return _load("slots", "ops/loop/slots.py")


def check_lane(lane):
    if lane not in LANES:
        raise SystemExit(f"unknown lane {lane!r}; lanes: {', '.join(LANES)}")
    return lane


def worktree_path(lane, root=ROOT):
    return Path(root).parent / "ew-worktrees" / check_lane(lane)


def ensure_worktree(lane, root=ROOT, git=None):
    """Create the lane worktree on branch lane/<lane> from main. No-op if present."""
    path = worktree_path(lane, root)
    if (path / ".git").exists():
        return path, False
    run = git or (lambda *a: subprocess.run(["git", "-C", str(root), *a], check=True,
                                            capture_output=True))
    path.parent.mkdir(parents=True, exist_ok=True)
    run("worktree", "add", "-B", f"lane/{lane}", str(path), "main")
    return path, True


def run_lane(lane, prompt, writes_code=False, timeout=3600, dry_run=False, root=ROOT,
             spawn=None, hold=None, progress=None, record=eta.record, clock=time.time):
    """One headless run on one lane. Returns the kit usage line (or a dry-run stub)."""
    check_lane(lane)
    task = f"lane-{lane}"
    if progress is None:
        progress = kit().write_progress
    kind = f"lane-{lane}-{'code' if writes_code else 'read'}"
    eta_s = eta.estimate(kind)
    progress(root, task, 5, "waiting for slot", eta_s, "running")
    if dry_run:
        progress(root, task, 100, "dry run", 0, "done")
        return {"lane": lane, "dry_run": True, "eta": eta.fmt(eta_s)}
    wt = worktree_path(lane, root)
    if hold is None:
        hold = slots().hold
    if spawn is None:
        spawn = kit().spawn
    t0 = clock()
    ok = False
    try:
        with hold(MAX_SLOTS, repo=CODE, run_id=task, timeout=timeout):
            progress(root, task, 20, "running headless", eta_s, "running")
            line = spawn(root, CODE, prompt, note=task, writes_code=writes_code,
                         timeout=timeout, cwd=wt, stdin=True,
                         extra=CODE_EXTRA if writes_code else ())
        ok = line.get("rc") == 0 and not line.get("error")
        return line
    finally:
        record(kind, clock() - t0, ok=ok)
        progress(root, task, 100, "finished" if ok else "failed", 0,
                 "done" if ok else "failed")


def status(root=ROOT):
    rows = []
    for lane in LANES:
        wt = worktree_path(lane, root)
        e = eta.fmt(eta.estimate(f"lane-{lane}-code"))
        rows.append(f"{lane}: worktree={'yes' if (wt / '.git').exists() else 'no'} eta={e}")
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description="EW laned headless driver")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    e = sub.add_parser("ensure")
    e.add_argument("lane")
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
    if a.cmd == "ensure":
        path, made = ensure_worktree(a.lane)
        print(f"{a.lane}: {'created' if made else 'present'}")
        return 0
    prompt = Path(a.prompt_file).read_text(encoding="utf-8")
    if not a.dry_run:
        ensure_worktree(a.lane)
    line = run_lane(a.lane, prompt, a.writes_code, a.timeout, a.dry_run)
    print(f"{a.lane}: rc={line.get('rc')} error={line.get('error')}"
          if not a.dry_run else f"{a.lane}: dry-run ok")
    return 0 if a.dry_run or line.get("rc") == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
