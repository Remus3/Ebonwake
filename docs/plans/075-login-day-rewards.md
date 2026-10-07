# Plan 075 - Login-day reward tracker: qualifying days per event, days left, at-risk alert

Status: open. Deep dive 2026-10-06b (research 0008 section 4). Lane hint: `build`.

Spec gap: high-value rewards hang on a COUNT of qualifying login days
inside a window: "[Donghwa's Gift] Special Login Reward" (10658) pays 14
reward days (6,000 Cron Stones total) between 2026-10-02 and 2026-10-28
23:59 UTC, missed days are lost, and a weekend second reward needs 1 h
played; the Dream Horse event (10659, from 2026-10-08) needs four weeks of
logins; the September "60-Min Login" (10619) needed 60 min a day. Plan 068
ticks TODAY's `login` / `logged_minutes:N` rows only; no part of EW counts
qualifying days per event, knows the days left, or warns before the
operator can no longer finish.

1. Login-day history (`server/ew/logindays.py`): from plan 008 GameWatch
   `logged_in` intervals (the same listener feed plan 056 `DiceClock`
   uses), accumulate logged-in minutes per UTC calendar date in store
   domain `logindays` `{dates: {"YYYY-MM-DD": minutes}}`; an interval
   crossing 00:00 UTC is split; keep the last 120 days; restart-safe like
   056 (an orphan open interval closes at its last seen poll). On first
   run it backfills once from plan 062's stored play sessions; days with no
   stored session are unknown and the row
   says "counting since <date>"; a one-click "I logged in that day" fills a
   past date inside the window (operator data).
2. Rule on an event item (plan 006 store, optional field `login_rule`):
   `{days_needed: 1..60, min_minutes: 0..600, weekend_minutes?: 0..600}`.
   Days are UTC calendar dates: every login event read in research 0008
   (10619, 10658) resets at 00:00 UTC, so no per-rule reset field
   (reverses if a notice states another reset time). Validated like other
   plan 006 fields; absent = not tracked. Plan 064 import SUGGESTS a rule from detail text
   matching tracked patterns in `server/ew/data/login_rule_patterns.json`
   ("log in for N minutes", "N days", "four weeks") - shown as "track
   logins? (N days)" with one click; never auto-applied (the counts in
   notices are worded loosely).
3. Progress math (pure): `credited` = qualifying dates in `[starts, ends]`
   (minutes >= `min_minutes`, at least 1 minute when 0), capped at
   `days_needed`; `days_left` = calendar days from today (inclusive) to
   `ends`; `today_done`; `at_risk` when `days_needed - credited >=
   days_left` and today is not yet credited; `lost` when `days_needed -
   credited > days_left`; weekend bonus count shown separately when
   `weekend_minutes` is set.
4. Surfaces: Events tab row "Login days 6/14 - 22 days left" with an
   at-risk / lost pill; Today minutes bar "38/60 min today" for minute
   rules while logged in; one What now (069) candidate "Log in today for
   <title>" when `at_risk` (source `deadline`, due at the day's end); plan
   070 ladder rules apply (quiet while the game is closed except one
   at-risk toast per day, opt-in plan 026).
5. Tests: `tests/test_logindays.py` - interval split at 00:00 UTC, orphan
   close, 120-day trim; credited with and without `min_minutes`; window
   edges; at_risk / lost boundaries (14 needed, 6 credited, 8 and 7 days
   left); weekend bonus count; rule validation; suggestion patterns on a
   trimmed 10658 / 10619 fixture (no network). Node formatter test.

Acceptance: a fixture with logins on 6 distinct UTC dates since
2026-10-02 and a clock at 2026-10-21T10:00Z (8 days left, today not
credited) shows 10658 at "6/14, 8 days left, at risk" and a What now row;
the same with today credited shows no row; a 60-min rule credits only
dates with >= 60 logged-in minutes; gates green; verifier PASS within 3
rounds; one push.

ToS check: plan 008 session-log state (running / login / disconnect) and
the clock only; notice text from plan 064's existing robots-allowed GETs;
no game input, no memory read, no client file, no reward claim (the
operator logs in and claims).

Depends on: 008, 056, 062, 064, 069.

Dependency guard: before writing code the lane checks that `server/ew/gamewatch.py` (plan 008), the plan 056 dice clock, `server/ew/playsession.py` (plan 062), `server/ew/eventnotices.py` with plan 064 auto-import and `server/ew/whatnow.py` (plan 069) exist. If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["008", "056", "062", "064", "069"]` into its progress JSON (`ops/loop/control/progress/p075-build.json`) and exits 0.

## As-built deviations

Adjudicated in-lane (build lane, 2026-10-06); refute-rounds recorded at merge.

1. Backfill sources. Decision: the one-time backfill reads the stored plan 062
   play window (`playsession.last`), plan 046 `summary.last_game` and the plan
   005 grind sessions (`started` + `minutes`). Alternatives: plan 062 only (it
   stores just the LAST window, so the backfill would hold at most one day).
   Why: grind sessions are the only multi-day record of time in game EW keeps.
   Reverses if: a manual grind log is shown to credit a day the operator was
   not logged in (then backfill from auto sessions only).
2. Mark semantics. Decision: "I logged in that day" (`POST /api/events
   {"login_mark": {date, on}}`) credits that date for every rule, minute rules
   included; the weekend bonus count ignores marks. A mark is any past UTC date
   within the 120-day history, not tied to one event's window. Alternatives:
   ask for minutes per mark; per-event marks. Why: one click, operator data
   wins, and a date credited for one event is credited for all. Reverses if: a
   minute rule is seen over-credited from marks.
3. Lost when today is already credited. Decision: `lost` compares the missing
   days with the FUTURE days (`days_left - 1` once today is credited); with
   today not credited it is exactly the plan's `needed - credited > days_left`.
   `at_risk` is false once `lost` (the pill shows "lost"; no What now row).
   Alternatives: the literal formula (once today is credited it reports a
   lost event as reachable for one extra day). Why: correct on both sides of
   today's login. Reverses if: a notice credits today's login differently.
4. Suggestion surfacing. Decision: the notice parse stores a `login`
   suggestion per cached Detail read (pre-075 cache entries read None until the
   next refresh); `/api/events` `login_days.suggest` lists Events items without
   a rule whose url is that notice, with "days needed" filled from the event
   window when the notice gives minutes only (10619). Suggestion rows on the
   not-yet-added "Suggested events" candidates were not added. Alternatives:
   suggest on candidates too. Why: a rule needs a stored item to attach to; the
   one click is an `edit` of that item. Reverses if: the operator asks to track
   logins straight from a candidate.
5. At-risk toast. Decision: new plan 026 rule `loginRisk` (default off, setting
   `notify.loginRisk`), keyed `loginRisk:<id>:<UTC date>` so it fires once a
   day, and added to plan 070's quiet `allow` list so it is the one toast while
   the game is closed. Alternatives: a ladder timer. Why: the risk is a
   day-level state, not a countdown. Reverses if: the operator finds the
   closed-game toast noisy (drop it from `allow`).
6. Today minutes bar. Decision: a text line "38/60 min today" under the row
   (shown while logged in, or once minutes count today), not a graphic bar.
   Why: matches the compact Events rows. Reverses if: the UI audit asks for a bar.
