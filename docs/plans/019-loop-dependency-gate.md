# Plan 019 - Loop dependency gate: skip a ROADMAP row until its Depends-on plans are done

Status: open. Deep dive + UX audit 2026-10-05 session 4 (research 0003-0005). Lane hint: `build`. Priority row.

Spec gap: plans 020-057 (deep dive + UX audit 2026-10-05, research 0003-0005)
carry `Depends on:` lines, but `tools/ew_loop.py` `work_list()` dispatches the
first open rows to up to 3 lanes in parallel with no notion of order beyond
`(priority)`. A dependent plan can be dispatched while its dependency is still
in a lane, so it builds against code that is not on main yet.

1. Parser: `plan_depends(text) -> list[str]` in `tools/ew_loop.py` reads the
   first line matching `^Depends on:\s*(.+)$` in a plan doc and returns the
   three-digit ids it names (`none` -> `[]`; ids in prose such as
   `027 (net proceeds)` still parse; anything else ignored).
2. `work_list()`: an open ROADMAP row whose plan doc (`docs/plans/<id>-*.md`
   on main) names a dependency whose ROADMAP row is not `[x]` is moved out of
   the work list into `skipped` with reason `waits on <ids>`; it is not
   dispatched. A missing plan doc or a dependency id with no ROADMAP row is
   treated as "no dependency" (fail open for rows, so a typo never wedges
   the queue) and logged as a step line.
3. The checklist (`python tools/ew_loop.py checklist`) and
   `ops/loop/control/progress/loop.json` show waiting rows as
   `(waits on 0NN)`.
4. Blocked state. Today a clean worktree with rc 0 becomes `no-change`
   (`process()`, `tools/ew_loop.py` ~line 875), which is in `DONE_STATES`
   (~line 89) and never passes `dispatchable()` (~line 428) again. Change:
   a. In `process()`, before recording `no-change`, read the lane's progress
      JSON `ops/loop/control/progress/p<id>-build.json` from the lane
      WORKTREE first (where the lane writes it), then the main checkout.
      The kit never cleans lane worktrees (`ops/fleet_kit/fleet_lanes.py`),
      so a marker is accepted only if its `updated` timestamp parses and is
      >= `rec["dispatched"]` (the current run's dispatch time); an older
      marker is stale and ignored (step line `<id>: stale blocked marker
      ignored`). A fresh `"status": "blocked"` with a `needs` list of
      three-digit ids -> item state `blocked`, `needs` stored on the item
      record, `blocked_runs` += 1, step line `<id>: blocked on <ids>`.
   b. New state `blocked` is NOT in `DONE_STATES`. `dispatchable()` returns
      true for a `blocked` record only once every id in `rec["needs"]` (and
      every Depends-on id of its plan doc) has a `[x]` ROADMAP row AND
      `blocked_runs < MAX_ATTEMPTS`. Dispatch still increments `attempts`
      as today (`tools/ew_loop.py` ~line 1096); the separate `blocked_runs`
      counter is what bounds blocked re-dispatches. A `blocked` record
      that reaches `blocked_runs >= MAX_ATTEMPTS` becomes `gave-up` (an
      ATTENTION state, error `blocked <n> times on <ids>`), in the same
      place the refused / lost cap is applied (~line 851).
   c. Re-arm, once per record: on each tick, an item record in state
      `no-change` without a `rearmed` flag, whose ROADMAP row is still
      `[ ]` open and whose plan doc names Depends-on ids, is re-armed ONLY
      if at least one of those dependencies was still open when the record
      finished - i.e. its ROADMAP row flipped to `[x]` after
      `rec["dispatched"]`. Flip time: `merge()` (`tools/ew_loop.py`
      ~lines 976-1021) flips the row inside the `--no-ff` merge commit and
      stores no timestamp today, so `git log -S` would find the row-ADD
      commit, not the flip. This plan makes `merge()` store `merged_at`
      (ISO UTC, `iso(self.d.clock())`) on the dependency's item record at
      the moment it flips the row to `[x]`; 4c compares that `merged_at`
      with `rec["dispatched"]`. Fallback for dependencies merged before
      this plan landed (no `merged_at`): `git log -m --first-parent -1
      --format=%cI -G"^\| <dep> \|.*\[x\]" -- docs/plans/ROADMAP.md`
      on main (the first-parent merge commit that introduced the `[x]`
      line); no result -> treat the dependency as flipped before the run
      (no re-arm). Then it is set to
      `blocked` with `needs` = those ids and `rearmed: true`. If
      every dependency was already `[x]` when the record ran, the
      `no-change` was genuine and the record stays `no-change` (it gets
      `rearmed: false` so it is never reconsidered). A `no-change` record
      with no Depends-on ids is left alone (current behaviour).
   d. Checklist / `loop.json` show `blocked` rows as `(blocked on 0NN)`.
5. Tests in `tests/test_ew_loop.py`: parser cases (none, one, several,
   prose, absent line); work list with a dependency open -> skipped, done ->
   dispatched, priority row still first among ready rows; a dependency cycle
   (A on B, B on A) leaves both skipped and the tick logs `dependency cycle`;
   fresh blocked marker -> state `blocked` (not `no-change`); stale marker
   (`updated` before `rec["dispatched"]`, left over in a reused worktree)
   -> plain `no-change`; worktree marker preferred over a main-checkout
   one; `blocked` not dispatchable while a need is open, dispatchable once
   all are `[x]`; `blocked_runs` reaching `MAX_ATTEMPTS` -> `gave-up`;
   re-arm of a `no-change` record whose dependency flipped `[x]` after it
   ran (happens once: a second tick does not re-arm, `rearmed` set);
   `merge()` writes `merged_at` on the item record when it flips the row
   `[x]` (and not when the merge fails); the fallback finds a flip made
   inside a `--no-ff` merge commit (temp git repo fixture: add row on main,
   flip it on a lane branch merged with `--no-ff`, assert the returned
   time is the merge commit's, not the row-add commit's);
   deps already `[x]` before the run + `no-change` -> stays `no-change`
   across repeated ticks with zero dispatches; a `no-change` record with a
   `[x]` own row or no Depends-on ids untouched; malformed marker (no
   `needs`, bad ids, unparsable `updated`) -> plain `no-change` plus a
   step line.

Acceptance: the cases above green; `python tools/ew_loop.py tick --dry-run`
on the current ROADMAP lists plans with open dependencies as waiting and
dispatches only ready rows; a synthetic blocked lane run is re-dispatched on
the first tick after its dependency row flips to `[x]`; pytest + node gates green; verifier PASS within
3 rounds.

ToS check: repository tooling only; nothing touches the game or the web.

Depends on: none.
