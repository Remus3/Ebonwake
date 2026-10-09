# Plan 100 - Perf 2.8: governor counts runs from headless_usage.jsonl

Status: built (lane, 2026-10-09). Source: ROADMAP row 100 (MAIN 2246 sec 2.8,
perf audit).

## Problem

The loop's spawn governor (`ew_loop.spawn_block`, item f: stop at RUNS_CAP - 3)
reads only the kit's `headless_budget.json` start list. That file and the
usage log `headless_usage.jsonl` drift: a run started by another path (a
by-hand kit CLI run, a session sub-run, a budget file removed after a
"repair or remove it" refusal) is in the usage log but not in the budget, so
the governor under-counts and lets the loop spawn into the kit's hard cap,
where the kit refuses mid-dispatch instead of the loop pausing cleanly. The
usage log also carries 34 pre-kit-v8 rows with no `kind`, which the
build-vs-inbox split (FLEET item 14 e) cannot attribute.

## Design

1. New module `tools/usage_ledger.py` (stdlib only):
   - `run_starts(path, now, window)`: epoch starts of the usage rows inside
     the rolling window. A row's start is its `ts` (the kit writes it at run
     end) minus `duration_s`; a row with no parseable `ts` is skipped, a
     malformed line is skipped (the log is append-only and a torn last line
     must not stop the loop).
   - `UsageBudget(inner, usage_path)`: wraps the kit `RunBudget`. `used()` =
     max(budget-file count, usage-log count) in the window, so the governor
     counts whichever ledger saw more runs; `readable()` is the budget
     file's (fail closed stays the kit's); `cap`, `window`, `clock` (settable,
     forwarded to the inner budget), `frees_at()` and `can_start()` follow.
     The kit's own hard cap inside `spawn` is unchanged.
   - `backfill_kind(path)`: every row lacking `kind` gets `"kind": "build"`
     and `"kind_src": "backfill"` (so a reader can tell a backfilled label
     from a kit-written one). Idempotent: a file with no such row is never
     rewritten. Atomic tmp-then-replace, and only when the file's size is
     unchanged since it was read (a kit append in between skips this pass;
     the next tick retries). Returns the number of rows labelled.
2. `ew_loop.Deps.budget` is a `UsageBudget` over the kit budget and the main
   checkout's usage log, so `spawn_block`, the lane worker's re-check and the
   status file's `runs_in_window` all count from both ledgers.
3. `Tick.run()` calls `backfill_kind` on the usage log at tick start (never in
   a dry run), so the live file is fixed by the loop itself and any later
   kind-less row (a pre-v8 tool) is labelled the same way.
4. CLI: `python tools/usage_ledger.py count|backfill [--root R]` for a
   session to read the count or run the backfill by hand.

## Acceptance

- A usage log with N rows in the window and a budget file with fewer starts
  makes `spawn_block` return "runs cap" when N >= cap - HEADROOM.
- Rows outside the window, torn lines and rows without `ts` are not counted.
- `backfill_kind` labels exactly the kind-less rows, keeps every other byte
  of the other rows, and a second call returns 0 without rewriting.
- A tick backfills the usage log under its root; a dry-run tick does not.
- Tests: `tests/test_usage_ledger.py`.

## As-built deviations

1. No plan doc existed for row 100; this lane wrote it from the ROADMAP row.
   Alternatives: block on a session. Why: the row names both fixes. Reverses
   if: MAIN's 2246 text asks for something else.
2. "Counts from headless_usage.jsonl OR reconciles headless_budget.json":
   chose counting (max of both ledgers) over rewriting the kit-owned budget
   file. Alternatives: merge usage starts into `headless_budget.json` at tick
   start. Why: the budget file is the kit's (vendored, pinned) and is written
   under the kit's own lock; writing it from EW risks racing a live spawn,
   while max() needs no write and never under-counts. Reverses if: the kit
   ships usage-based counting itself (then Deps.budget goes back to the kit
   RunBudget).
3. "Backfill kind build OR exclude pre-v8 rows": chose backfill (with a
   `kind_src` marker) over exclusion. Alternatives: drop rows with kit < 8
   from the split. Why: the rows are real second-account runs that used
   budget; excluding them hides spend, and the marker keeps them separable.
   Reverses if: MAIN's insights report wants pre-v8 rows excluded - then it
   filters on `kind_src`.
4. The live usage log was not touched by this lane (the lane has no access to
   the main checkout's control files). The first loop tick after merge runs
   the backfill (design 3); `python tools/usage_ledger.py backfill` does it by
   hand. Reverses if: never - the tick path is idempotent.
5. The lane could not write its progress file `progress/p100-build.json` in
   the main checkout (write permission denied outside the worktree).
   Alternatives: write it inside the worktree. Why: item 12 puts it in the
   main checkout only; a worktree copy is read by nobody. Reverses if: the
   lane's permission set grants that one path.
