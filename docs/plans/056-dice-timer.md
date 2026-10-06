# Plan 056 - Black Spirit's Adventure dice timer from logged-in time (overlay)

Status: open. Deep dive + UX audit 2026-10-05 session 4 (research 0003-0005). Lane hint: `build`.

Spec gap: research 0003 C12 (value 3, S): up to 3 basic dice a day, one at
login and more after 30 and 60 minutes of play; "3/3 limit resets at 05:00
UTC" (BDFoundry 2023, unverified). Plan 003 has only a daily dice tick.

1. `server/ew/data/reset_rules.json` (plan 021) row for the dice gains
   `grants_at_min: [0, 30, 60]` and stays `verified: false`.
2. `server/ew/gamewatch.py` consumer: accumulate logged-in minutes since
   the dice reset from the plan 008 login / disconnect signal (minutes are
   wall-clock while logged in; restarts of EW resume from the store).
   `dice_status(now)` -> `{earned, next_at_min, eta_utc}`.
3. `GET /api/today` adds `dice`; overlay widget `dice` (opt-in) shows
   "die 2/3 in 12m"; the Today dice tick auto-suggests (never auto-ticks).
4. Tests: `tests/test_today.py` / `tests/test_gamewatch.py` additions
   (accumulation across disconnects, reset at the rule time, EW restart).

Acceptance: tests green; gates green; verifier PASS within 3 rounds.

ToS check: plan 008 session-log signal only (running / login / disconnect);
no game input, no memory read.

Depends on: 021.

Dependency guard: before writing code the lane checks that `server/ew/data/reset_rules.json` exists (plan 021). If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["021"]` into its progress JSON (`ops/loop/control/progress/p056-build.json`) and exits 0. Plan 019's tick turns that clean, marked run into item state `blocked` (not `no-change`) and re-dispatches the row once every Depends-on row is `[x]`; plan 019's work-list gate normally keeps the row from being dispatched that early. If this row ran before plan 019 landed and was recorded `no-change`, 019's re-arm step makes it dispatchable again.

## As-built deviations

1. Orphan session on EW restart. Decision: an open login stored by a previous
   EW run is resumed when the first poll still reads `logged_in`; any other
   first state closes it at `seen`, the last time EW saw it logged in
   (`status()` refreshes `seen` at most once a minute; every game event sets
   it). Alternatives: close at the restart time (counts EW downtime with the
   game closed, so a die could read ready when it is not); drop the open
   segment (under-counts a whole session). Why: under-counts by at most the
   gap since the last read, never over-counts. Reverses if: the session log
   gains a reliable logout timestamp that EW can replay on start.
2. Store domain `dice` (`DiceClock` in `gamewatch.py`, wired as a
   GameWatch listener like plan 046) rather than state on GameWatch itself.
   Alternatives: fields inside GameWatch. Why: GameWatch has no store and its
   tests stay unchanged. Reverses if: GameWatch gains persistence.
3. `dice` block carries extra fields beyond `{earned, next_at_min, eta_utc}`:
   `max`, `played_min`, `logged_in`, `next_reset`, `verified` (the overlay
   needs `max` / `played_min` for the logged-out line). Additive only.
4. `grants_at_min` is an OPTIONAL preset field (`PRESET_OPTIONAL`), validated
   as 1-10 strictly ascending ints 0..1440; a missing or malformed dice row
   falls back to 05:00 / [0, 30, 60] (`today.dice_preset`). A session that
   spans the reset counts only minutes after it, and being logged in across
   the reset counts as the login die.
5. Today suggestion: the open dice row (id `black-spirits-adventure-dice` or a
   matching title) shows an `n/3 earned - roll, then tick` pill; it never
   ticks. Widget key `dice` (opt-in, default off) added to ewcore WIDGETS,
   `settings.py` WIDGETS and `config/local.example.json`. The overlay re-GETs
   /api/today on each SSE `game` event when the widget is on.
6. Python tests for presets / route landed in `tests/test_today_resets.py`
   (the plan 021 file) rather than `tests/test_today.py`; node tests in
   `app/test/dice.test.js`.
