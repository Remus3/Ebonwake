# Plan 098 - Loop: detached review / fix / merge worker, tick under 60 s

Status: built (lane, 2026-10-09). Source: ROADMAP row 098 (MAIN 2246 sec 2,
perf audit item 2.4).

## Problem

`Tick.finished()` ran `process()` inline for every `ran` / `committed` item
while holding `ops/loop/control/loop.lock`: the CI gates (a whole gated suite,
which can wait up to an hour for a machine suite-gate slot), the verifier
spawn, up to three fix spawns (each up to `lane_timeout_s`), the commit and
the merge into main. `Tick.push()` ran the gates on main again before a push.
One tick could therefore hold the loop lock for hours; every later tick found
the lock busy and did nothing - no inbox, no dispatch, no reaping. Items that
finished together were reviewed one after the other, never in parallel.

## Design

1. `ew_loop.py review <ID>` is a new detached worker (same `_launch` path as
   `lane <ID>`: pythonw, no window, detached, job breakaway with fallback). It
   runs the existing `Tick.process(rec)` for one `ran` / `committed` item -
   gates in that item's own lane worktree, verifier, fix rounds (capped at 3),
   commit, merge - and exits. Several items' workers run at once, so
   independent items are gated in parallel in their own worktrees (bounded by
   the kit's machine suite-gate slots and governor).
2. `ew_loop.py push` is a new detached worker: `Tick.push()` (main clean and on
   main, ahead of origin, gates green on main, leak sweep, push) under the
   merge lock.
3. The tick only classifies (inbox), dispatches and reaps. For a `ran` /
   `committed` item `finished()` now:
   a. a record with `review_launch` and a live `worker_pid` (or none yet, and
      launched under `REVIEW_START_S` = 300 s ago): in flight, left alone;
   b. a record with `review_launch` whose worker is gone (dead pid, or never
      started within `REVIEW_START_S`): a crashed review. `review_crashes` is
      counted; under `MAX_ATTEMPTS` the review is relaunched; at the cap a
      `ran` item becomes `failed-dirty` (the existing `retry_refused` path
      keeps its work as an unmerged WIP commit for a session) and a
      `committed` item is kept under `refs/ew/keep/<id>` as `merge-refused`;
   c. otherwise, a `ran` item while spawning is blocked (HALT, backoff, runs
      cap) waits; else the tick writes `review_launch` on the record (before
      the launch, never after it, so it never overwrites the worker's own
      writes) and launches the worker. A launch exception clears the marker.
4. The worker owns its record while it runs: it refuses when the record is
   not `ran` / `committed` or another live worker's pid is on it, writes its
   own `worker_pid` first, and on exit (always, `finally`) clears
   `worker_pid` / `review_launch`, keeps its last 10 log lines in
   `review_log` and records the run duration under ETA kind `loop-review`.
5. Merges serialize on a new lock `ops/loop/control/merge.lock` (kit
   `fleet_watch.watch_lock`, pid-owned, stale break). `Tick.merge()` takes it
   itself, whoever calls it (review worker, push worker, or the tick's own
   `retry_refused` salvage path); a busy lock defers the merge (`committed`
   stays, the next tick launches a worker that merges).
6. Push: the tick checks `push_needed()` (branch main, clean, ahead of origin:
   git only, no gates) and launches one push worker unless one is alive
   (`ops/loop/control/push_worker.json` {pid, launched}; the worker writes its
   pid and removes the file on exit; a launch older than `REVIEW_START_S` with
   no pid is stale). `--no-push` and `--dry-run` launch nothing.
7. The checklist shows a `ran` / `committed` item with a review in flight as
   `reviewing`. `loop.json` gains `tick_s` (this tick's wall time), so the
   under-60-s target is visible on every fire.

## Acceptance

- A tick with a `ran` item calls no gate and no spawn; it launches exactly one
  review worker and writes `review_launch` on the record.
- Two `ran` items in one tick launch two review workers (parallel gating).
- A second tick while a worker is alive launches nothing; a dead worker is
  relaunched and counted; at `MAX_ATTEMPTS` crashes the item is parked
  (`failed-dirty` / `merge-refused`).
- The review worker runs gates / verifier / commit / merge for its item,
  clears its markers on exit and refuses an item another live worker holds.
- A busy merge lock defers the merge; the item stays `committed`.
- A tick whose main is ahead launches one push worker; none with `--no-push`.
- Existing loop behaviour is unchanged when the workers run (the test harness
  runs them inline).

## As-built deviations

- D1. The plan doc did not exist when the lane started (ROADMAP row only);
  the lane wrote it from the row and MAIN 2246 sec 2 as summarized there.
  Alternatives: block on a missing spec. Why: rule 6, never wait. Reverses
  if: MAIN's note specifies a different worker split.
- D2. Push moved to its own detached worker instead of riding the review
  worker. Alternatives: push at the end of each review worker (session
  commits would then never be pushed by the loop); keep the push inline (the
  gates on main are a whole suite, the main cost the plan removes). Why: keeps
  the old "push whatever main is ahead by" behaviour with a git-only check in
  the tick. Reverses if: plan 096's verdict cache makes gating main cheap
  enough to run inline.
- D3. A worker that raises keeps its pid on the record (counted as a crash
  by the next tick) instead of clearing it. Alternatives: clear and relaunch
  forever. Why: bounds a repeating error by MAX_ATTEMPTS. Reverses if: never.
- D4. Tests run both workers inline through the test `deps()` helper, so
  the existing end-to-end loop tests still check gates / verifier / merge
  through a tick; the blocked-marker tests read the worker's steps from the
  record's `review_log`. Reverses if: never (test harness only).
- D5. The lane could not write its progress file into the main checkout
  (this lane's sandbox denies any write outside its worktree). Reverses if:
  the lane permission set allows the progress path.
- D6. Merge onto main after plan 096 (resolve lane, 3 conflict hunks in
  tools/ew_loop.py). (a) Module docstring: kept 098's detached push worker
  text plus 096's one-gate-per-tree paragraph, "push" now "the push worker".
  (b) Kept 096's tree_key + GateCache and 098's `_launch(..., cmd=)`
  signature (096 had not changed `_launch`). (c) `Tick.push` (run by the
  push worker) gates main through 096's `self.gate(main)` instead of 098's
  raw `self.d.gates(main)`, so a merge whose verdict was carried to main is
  pushed without a second whole suite; `launch_push` stays git-only.
  Alternatives: raw gates in the push worker (re-runs a suite 096 already
  proved green). Why: both plans aim at one gate run per tree state.
  Reverses if: the cache is measured serving a stale green to a push.
- D7. GateCache was "written only under the tick's loop lock"; with 098 the
  review workers and the push worker write it concurrently. Kept the plain
  atomic read-modify-write and documented it: a race can only drop an entry
  (re-gate), never record a wrong verdict. Alternatives: a cache file lock
  (more lock plumbing for a cache). Reverses if: lost entries are seen
  causing measurable re-gating.
- D8. Two plan 096 tests (tests/test_ew_loop_gate_cache.py: push skips an
  already gated tree; merged plan pushed on the lane verdict) asserted
  "cached green" in the tick's own log. With 098 the push runs in the push
  worker, whose steps land in push_last.json; the asserts now read that log
  (helper `push_log`). Behaviour unchanged: one gate run, push still goes.
  Alternatives: copy the push worker's log into the tick doc (the worker is
  detached in production, so the tick never has it). Reverses if: never.
