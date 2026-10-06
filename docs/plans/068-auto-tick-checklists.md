# Plan 068 - Auto-tick inferable checklist rows: login dailies, dice ready, boss-shot suggest

Status: open. Autonomy deep dive 2026-10-06 (research 0007 section 1). Lane hint: `build`.

Spec gap: every Today (003 / 021) row, the dice roll (056) and world boss
looted (032) are ticked by hand, though some are fully determined by
signals EW already has.

1. Row rule field: Today checklist rows gain an optional `auto` key in their
   seed data: `login` (ticked on the first `logged_in` after the row's
   reset - e.g. login rewards / attendance), `logged_minutes:N` (ticked
   when plan 056 `DiceClock` minutes since reset reach N - e.g. dice
   "ready", shown as ready, the roll itself stays a tick), or none.
   Rules are data, not code.
2. Auto ticks carry `by: "auto"` and render with a small "auto" mark and an
   undo; an undone auto tick is not re-ticked until the next reset.
3. Boss suggestion: a screenshot (plan 008 watcher) taken within a plan 031
   spawn window +- 20 min while logged_in marks that boss row
   "probably done - tick?" (one click), never auto-ticked.
4. Settings (030): `checklist.auto: true`.
5. Tests: `tests/test_autotick.py` - login after reset ticks once; login
   before reset does not carry over; minutes threshold; undo blocks
   re-tick until reset; boss suggestion window edges.

Acceptance: a fixture day with one login ticks the login rows once, marks
dice ready at the threshold and suggests the boss row for a shot at spawn
+5 min; gates green; verifier PASS within 3 rounds; one push.

ToS check: session-log state, screenshot file times and the clock only; no
game input, no memory read, no client file.

Depends on: 021, 056, 032.

Dependency guard: before writing code the lane checks that `server/ew/today.py` (plan 021), `server/ew/gamewatch.py` with `DiceClock` (plan 056) and `server/ew/bosses.py` (plan 032) exist. If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["021", "056", "032"]` into its progress JSON (`ops/loop/control/progress/p068-build.json`) and exits 0.
