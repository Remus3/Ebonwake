# Plan 068 - Auto-tick inferable checklist rows: login dailies, dice ready, boss-shot suggest

Status: built (lane, awaiting merge). Autonomy deep dive 2026-10-06 (research 0007 section 1). Lane hint: `build`.

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

As-built (lane build, 2026-10-06): `server/ew/autotick.py` (`AutoTick`,
`boss_suggestions`), rule field + `by` / `blocked` state in
`server/ew/today.py`, `checklist.auto` in `server/ew/settings.py`, wiring in
`server/ew/app.py` (GameWatch listener + poller; GET /api/today `ready`, GET and
POST /api/bosses `suggested`), dashboard `today.js` (auto pill + undo, ready
pill), `bosses.js` ("probably done - tick?"), ewcore helpers, Settings group
"Checklist". Tests: `tests/test_autotick.py`, `app/test/autotick.test.js`.

## As-built deviations

1. Decision: a third rule `ready_minutes:N` (mark ready, never tick); the seed
   dice row carries `ready_minutes:60`, attendance carries `login`.
   Alternatives: `logged_minutes:N` on the dice row (would auto-tick the roll);
   hard-coding the dice row in code. Why: item 1 says the dice is "shown as
   ready, the roll itself stays a tick" and "rules are data, not code"; only a
   distinct rule satisfies both. Reverses if: the operator wants the dice row
   auto-ticked (change its seed rule to `logged_minutes:60`).
2. Decision: `login` also ticks on a poll (at most every 30 s) that sees
   logged_in after the row's reset, not only on the login transition.
   Alternatives: transition only. Why: a session that spans the reset would
   never tick though the game grants attendance to online players; a login
   before the reset still does not carry over (tested). Reverses if: the
   operator check shows attendance needs a fresh login.
3. Decision: any operator untick of a row that has an auto rule blocks auto
   ticks until the row's next reset (not only the undo of an auto tick).
   Alternatives: block only when the tick was `by: auto`. Why: an operator who
   unticks a manual tick would otherwise see it re-ticked by the next poll;
   same intent ("I say it is not done"). Reverses if: never - an operator
   tick still works on a blocked row.
4. Decision: minute rules read the plan 056 dice clock (logged-in minutes since
   the dice reset, 05:00 UTC by the seed) and count only when that reset is at
   or after the row's own last reset. Alternatives: a second per-row minute
   clock. Why: one clock, no double counting; the guard stops a midnight row
   inheriting yesterday's minutes (00:00-05:00 shows not ready). Known limit:
   a minute row whose own reset is later in the day than 05:00 UTC never
   ticks; `add` does not refuse it. Reverses if: a row needs minutes on a
   reset the dice clock does not share.
5. Decision: boss suggestions live in store domain `autotick` and are merged
   into GET / POST /api/bosses as `suggested: {PT day: [boss]}` (looted ones
   dropped); the setting gates them too. Alternatives: a field on the
   `bosses` domain. Why: BossService stays the operator-tick store; a
   suggestion is never a loot tick. Shots come from the plan 008 watcher list
   (since EW start, newest 50). Reverses if: never.
6. Decision: `tests/test_today.py::test_view_shape` now pins the plan 003 shape
   on `barter-run` (no rule) instead of the first row (attendance now has a
   rule and the three plan 068 keys). Why: the shape of rule-less rows is
   unchanged. Reverses if: never.
7. Merge resolve onto main after plans 065 / 067 (both sides kept in all four
   conflicts). Decision: Settings group order ends `Game folders` (065) then
   `Checklist` (068), in `ewcore.js` SETTINGS_GROUPS and
   `app/test/settings.test.js`; `settings.py` SPEC keeps 065 `bdo.*` + 067
   `overlay.auto` / `overlay.idle_min` then 068 `checklist.auto`; `app.py`
   imports the union (`autotick`, `context`, `detect`, `maint`) and GET
   /api/bosses serves `self.server.bosses_view()` (068 `suggested`) with 067's
   `/api/overlay/context` route kept after it. Alternatives: 068's group before
   065's; main's plain `bosses.view()` on GET. Why: plan-number order matches
   every earlier group; plain `bosses.view()` would drop the boss suggestion
   that GET must carry (POST already used `bosses_view`). Reverses if: never.

refute-rounds: 1/3 (verifier r1 FAIL: premature round claim, merge paths
unstaged; both fixed in the producer answer).

Dependency guard: before writing code the lane checks that `server/ew/today.py` (plan 021), `server/ew/gamewatch.py` with `DiceClock` (plan 056) and `server/ew/bosses.py` (plan 032) exist. If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["021", "056", "032"]` into its progress JSON (`ops/loop/control/progress/p068-build.json`) and exits 0.
