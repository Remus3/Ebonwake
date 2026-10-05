# Plan 058 - Loop resolves its own merge conflicts: re-dispatch a resolve lane

Status: open. Session 5 (2026-10-05). Lane hint: `build`. Priority row.
Depends on: none

Spec gap: three lanes run in parallel from the same main, so any two that
touch the same file (overlay, `app/shared/ewcore.js`) conflict at merge time.
`Tick.merge()` (`tools/ew_loop.py`) then runs `git merge --abort` and parks the
item in `merge-conflict`, an ATTENTION state that waits for a person. Session 5
hit this twice in one hour (024 on the overlay files, 025 on the ewcore export
list); both were resolved by hand with the same recipe (merges a01964e,
b8e0099). The recipe is mechanical enough for a lane.

1. Keep the commit. On a conflict, before the abort, record the lane commit
   sha on the item (already `rec["commit"]`) and create the ref
   `refs/ew/keep/<id>` pointing at it in the main checkout, so a re-claimed
   lane worktree can never orphan it. Delete the ref after a merge.
2. Resolve lane. A `merge-conflict` record with `resolve_runs <
   MAX_ATTEMPTS` becomes dispatchable as kind `resolve`: the prompt tells the
   lane to `git merge --no-ff refs/ew/keep/<id>` onto current main inside its
   own clean worktree (detached at main), resolve keeping BOTH sides'
   features, record decision / alternatives / why in an `As-built deviations`
   section of the plan doc, run the ci gates, and leave the result as a
   commit in the worktree (no push, no ROADMAP edit, as for build lanes).
   `resolve_runs` += 1 per dispatch.
3. Merge. The resulting worktree commit goes through the normal
   `process()` -> gates -> verifier -> `merge()` path (refute rounds still
   capped at 3), with the merge message `merge <lane>: <label>` and the
   ROADMAP flip as today. A second conflict on the resolve merge re-enters
   step 2; after `MAX_ATTEMPTS` resolve runs the item stays in
   `merge-conflict` for a session's adjudicator.
4. Ordering. Prefer dispatching a `resolve` item before new plan rows, so a
   conflicting change is folded in before more lanes build on stale main.
5. Checklist / progress: `merge-conflict` rows show `(resolving, run n/2)`
   while a resolve lane is in flight.

Tests (`tests/test_ew_loop.py`, existing fakes): conflict -> keep ref
created + state merge-conflict; next tick dispatches kind resolve with the
ref in its prompt; resolve commit merges and deletes the ref; resolve_runs
cap leaves merge-conflict; a ROADMAP-only conflict still uses the existing
row-level resolver and never dispatches a resolve lane.

Reverses if: lane conflict rate drops to near zero (e.g. lanes rebased per
dispatch) or resolve lanes are measured merging broken features past the
gates.
