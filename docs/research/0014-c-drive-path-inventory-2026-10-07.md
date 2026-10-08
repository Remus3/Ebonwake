# 0014 - C: path inventory before the move to E: (order Na22de8)

2026-10-07. Inventory only: nothing was edited, moved or deleted. Source of
the order: MAIN ORDER note 2026-10-07 21:55 (HOP 1). The loop writes the ONE
ANSWER (HOP 2) from this file after merge.

Directory names are abbreviated: `<EW>` = this repo's checkout root,
`<WT>` = the lane worktree parent (`<repo parent>/ew-worktrees`),
`<Steam>` / `<profile>` = third-party locations EW does not own.

Method: a drive-rooted / env-path regex over every tracked file (ripgrep, the
same shape as `tools/leak_sweep.py` STRUCTURAL `drive-path`), then the
gitignored per-host files (`config/local.json`, `_scratch/`, `ops/runtime/`,
`ops/loop/control/`), `git worktree list`, the worktree `.git` pointer files,
and the code that installs scheduled tasks and shortcuts. No venv exists.

## 1. Rows

    ops/loop/slots.py:40  Path(r"C:\ProgramData\lw-loop\slots")  HARDCODED
    ops/fleet_kit/fleet_lanes.py:277  env ProgramData -> <ProgramData>/lw-loop/slots  DERIVED
    server/ew/detect.py:6  %USERPROFILE%\\Documents + Black Desert (registry/env at run time)  DERIVED
    tools/ew_lane.py:9  <repo parent>/ew-worktrees/lane-<i>  DERIVED
    tools/loop_task.py:155  root / "tools" / "ew_loop.py" ; workdir=str(root)  DERIVED
    tools/logon_task.py:137  root / "tools" / "launch.py" ; workdir=str(root)  DERIVED
    tools/install_hooks.py:16  core.hooksPath .githooks (relative)  DERIVED
    tools/leak_sweep.py:43  ^[A-Za-z]:[\\/]+(ProgramData|...) (regex, not a path)  DOC
    tests/test_leak_sweep.py:36  "C" + ":" + "\\" + "ProgramData\\thing" (test fixture)  DOC
    CLAUDE.md:178  ../ew-worktrees/lane-<i>  DOC
    docs/adr/0007-fleet-kit-v6-lanes.md:12  ../ew-worktrees/<lane name>  DOC
    docs/adr/0007-fleet-kit-v6-lanes.md:13  ../ew-worktrees/lane-<i>  DOC
    docs/plans/001-skeleton.md:21  ../ew-worktrees/<lane>  DOC
    docs/plans/065-zero-config-first-run.md:12  %USERPROFILE% + Documents  DOC
    config/local.json:9  "C:/<Steam>/steamapps/common/Black Desert Online"  EXTERNAL
    config/local.json:10  "C:/<profile>/Documents/Black Desert"  EXTERNAL
    <WT>/lane-0/.git:1  gitdir: C:/<EW>/.git/worktrees/lane-0  HARDCODED
    <WT>/lane-1/.git:1  gitdir: C:/<EW>/.git/worktrees/lane-1  HARDCODED
    <WT>/lane-2/.git:1  gitdir: C:/<EW>/.git/worktrees/lane-2  HARDCODED
    .git/worktrees/lane-{0,1,2}/gitdir  C:/<WT>/lane-N/.git (git admin back-pointer, inferred)  HARDCODED
    task \EbonwakeOps\LaneLoop  Arguments "C:\<EW>\tools\ew_loop.py" tick ; WorkingDirectory C:\<EW>  EXTERNAL
    task \Ebonwake (logon launcher)  Arguments "C:\<EW>\tools\launch.py" ; WorkingDirectory C:\<EW>  EXTERNAL
    both tasks  Command <python dir>\pythonw.exe (beside sys.executable at install)  EXTERNAL
    Desktop shortcut "Ebonwake"  pythonw C:\<EW>\tools\launch.py  EXTERNAL
    Desktop shortcut to EW-NEXT-SESSION.txt  C:\<EW>\EW-NEXT-SESSION.txt  EXTERNAL

Notes on rows:

- `ops/loop/slots.py:40` is the machine-wide governor default under
  ProgramData, not a repo path. It stays valid if ProgramData stays on C:; it
  is a byte-pinned shared file, so any change is MAIN's to ship.
- `config/local.json` rows are the worktree copy (the main checkout's copy is
  the same template shape; not read from this lane). Both point at the GAME
  and the user's Documents, which do not move with the repos; no change
  unless Steam or Documents move.
- The two task rows and two shortcut rows are what the install code writes
  (`tools/loop_task.py`, `tools/logon_task.py`, `tools/launch.py` docstring);
  a live read-back was not possible from this lane (see blocked items).
  After the move: `python tools/loop_task.py install` and
  `python tools/logon_task.py install` from the new root re-point both tasks
  (both idempotent, `/F`); re-create the two shortcuts.
- Worktree pointers: `git worktree repair` from the new main checkout, or
  `git worktree prune` and let the kit recreate lanes (`fleet_lanes` prunes
  then `worktree add --detach` when a lane path is missing).
- Runtime logs under `ops/runtime/` and `ops/loop/control/*.jsonl` may quote
  absolute paths in past output; they are history, not config, and move with
  the repo unchanged.

## 2. Count per class

    HARDCODED  5  (slots.py default 1; worktree .git pointers 3; admin back-pointers 1 row covering 3 files)
    DERIVED    6
    DOC        7
    EXTERNAL   7

## 3. C: worktrees and scratch dirs EW owns

    C:\<WT>\lane-0  kit lane worktree (this lane)  needed until this lane ends; recreated by the kit on E:
    C:\<WT>\lane-1  kit lane worktree, FREE        not needed after the move; prune, kit recreates
    C:\<WT>\lane-2  kit lane worktree, FREE        not needed after the move; prune, kit recreates
    <EW>/_scratch, <EW>/ops/runtime  gitignored scratch inside the repo  move with the repo; contents disposable

No venv and no pyvenv.cfg exist; no interpreter path is pinned in config.
EW owns no registry value (it only READS Steam and Shell Folders keys). EW's
env variables hold a URL (`CLAUDE_HEADLESS_BASE_URL`) and secret references,
none expected to hold a path; a live read-back was blocked (below).

## 4. For the runbook (batched for MAIN)

1. Halt the loop first: create `ops/loop/control/HALT`; wait for no RUNNING
   lane lock (`ops/loop/control/lanes/*.lock`).
2. Move; then `git worktree prune` (or `repair`) in the new main checkout.
3. Re-run both task installers from the new root; re-create the two shortcuts.
4. Remove HALT; the next tick recreates lane worktrees under the new parent.
