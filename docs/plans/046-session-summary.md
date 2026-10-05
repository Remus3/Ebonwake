# Plan 046 - Session-end summary: prompt to stop the grind on game exit, nightly and weekly recap

Status: open. Deep dive + UX audit 2026-10-05 session 4 (research 0003-0005). Lane hint: `build`.

Spec gap: research 0005 candidate F9 (value 4, M) and section 3: nothing
is recorded at log-out; a grind session keeps counting after the game
exits although plan 008 knows the game closed.

1. `server/ew/summary.py`: `session_summary(since, until)` from the store:
   grind sessions (silver, silver/h), XP gained (plan 011 samples), buffs
   armed, dailies ticked, events claimed; `week_summary` for the current
   week (Thursday reset).
2. Game exit hook: when the plan 008 game state goes running -> not running
   with a grind session open, the server marks `grind.pending_stop` (no
   automatic stop); `GET /api/grind` exposes it; plan 026's `gameExit`
   rule notifies.
3. Dashboard: a "Session ended" dialog on the Grind tab (stop at the exit
   time / keep running), then the summary card; Home (plan 025) shows the
   last summary when present.
4. Tests: `tests/test_summary.py` (aggregation windows, empty session,
   pending_stop set/cleared, never auto-stopped).

Acceptance: tests green; gates green; verifier PASS within 3 rounds.

ToS check: plan 008 session-log signal (running / login / disconnect only)
and EW's own store.

Depends on: 026.

Dependency guard: before writing code the lane checks that `notifyRules` exists in `app/shared/ewcore.js` (plan 026). If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["026"]` into its progress JSON (`ops/loop/control/progress/p046-build.json`) and exits 0. Plan 019's tick turns that clean, marked run into item state `blocked` (not `no-change`) and re-dispatches the row once every Depends-on row is `[x]`; plan 019's work-list gate normally keeps the row from being dispatched that early. If this row ran before plan 019 landed and was recorded `no-change`, 019's re-arm step makes it dispatchable again.
