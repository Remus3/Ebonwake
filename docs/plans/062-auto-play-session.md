# Plan 062 - Auto play-session: login opens, exit closes the grind log

Status: open. Autonomy deep dive 2026-10-06 (research 0007 sections 1, 2.5). Lane hint: `build`.

Spec gap: grind sessions (005 / 039) are started and stopped by hand and
plan 046 only marks `grind.pending_stop` on game exit, then asks. The plan
008 state machine already knows `logged_in` / `disconnected` /
`not_running` every 2 s; a log-tail state machine should drive the session
with no button.

1. `server/ew/playsession.py` (a GameWatch listener, like `DiceClock`):
   `logged_in` opens a play session `{id, start, spot, auto: true}`;
   `not_running` or `disconnected` held for `grace_s` (default 120)
   closes it at the last moment seen logged in. A relaunch inside the grace
   continues the same session. An EW restart mid-session resumes it (same
   rule as `DiceClock`: resume only when the first poll still reads
   logged_in).
2. Grind binding: an auto session opens a plan 005 grind session with the
   last used spot (or "unspecified"); plan 040 / 063 loot matches may set
   the spot later. Closing it runs the plan 046 summary at once; the
   pending-stop prompt is retired for auto sessions (manual sessions keep
   it).
3. Manual control stays as an override: Start / Stop still work; a manual
   Stop during a logged-in run suppresses auto-open until the next login.
4. Settings (030 allowlist): `auto_session: true` default, `grace_s`
   60-600.
5. API / SSE: GET `/api/grind` gains `session.auto`; SSE event
   `play_session` {state: open|closed, id}.
6. Tests: `tests/test_playsession.py` - state sequences (login, disconnect
   inside grace, exit, relaunch, restart resume, manual stop suppression),
   clock injected; node test for the session pill.

Acceptance: a fixture sequence not_running -> logged_in (10:00) ->
not_running (11:30) yields one closed session 10:00-11:30 with a summary
and no prompt; gates green; verifier PASS within 3 rounds; one push.

ToS check: reads only the plan 008 session-log state and the clock; no game
input, no memory read, no client file edit.

Depends on: 008, 046.

Dependency guard: before writing code the lane checks that `server/ew/gamewatch.py` (plan 008) and `server/ew/summary.py` (plan 046) exist. If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["008", "046"]` into its progress JSON (`ops/loop/control/progress/p062-build.json`) and exits 0.
