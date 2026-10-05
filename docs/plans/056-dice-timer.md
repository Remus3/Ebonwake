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
