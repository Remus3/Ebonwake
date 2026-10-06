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

## As-built deviations

1. Buffs "armed" in a window = timers with `ends` after the window start and
   `armed` before its end. Decision: `GrindService.buff` now stamps `armed`
   (cleared with the timer); a timer armed before plan 046 has no stamp and
   counts on its end alone. Alternatives: `ends` only (refute r1: a long
   buff armed after a past window was listed in it); a full arm history.
   Why: one field, exact for every new timer. Reverses if: a plan adds an
   arm-time history to the grind domain.
2. Events "claimed" needs a time: `events.done` now stamps `done_at` (kept
   on edit, dropped on un-done, re-done keeps the first stamp). Items marked
   done before plan 046 have no stamp and never appear in a summary.
   Alternatives: count every done item (wrong window); a separate claim log
   domain. Why: smallest change, one field on the existing row. Reverses if:
   an events history / ledger plan supersedes it.
3. The exit hook is a `GameWatch.listeners` list (called outside the lock,
   failures swallowed) feeding a new `SummaryService` that records the play
   window in store domain `summary` (`last_game {since, until}`) and calls
   `GrindService.mark_pending_stop`. `GET /api/summary` serves
   `{session, day, week, pending_stop}`; "day" (the nightly recap) is since
   the last 00:00 UTC daily reset, "week" since Thursday 00:00 UTC.
   Alternatives: derive exit from the grind view (no transition memory);
   fold the summary into `GET /api/grind` (couples grind to every domain).
   Why: plan 008 already owns the transition; one read-only route keeps the
   grind body lean. Reverses if: a plan moves game events to a bus.
4. Grind writes: `{"stop": {..., "at_exit": true}}` ends the session at the
   pending exit time; `{"keep": true}` dismisses the pending stop. A pending
   stop is bound to the session's `started` and dropped once that session
   ends or is replaced, or when the game comes back up (down -> up: the
   operator relaunched and kept going; refute r1), so the next exit raises
   a fresh pending time. Two marks without a relaunch keep the first.
   Alternatives: a separate `stop_at_exit` op. Why: reuses the stop form,
   loot and validation unchanged. Reverses if: never (shape only).
5. The "Session ended" dialog is an inline `role="alertdialog"` box in the
   Grind Session card (no modal window), plus a Summary card (last game
   session / today / this week). The gameExit notify rule fires from
   `grind.pending_stop`, keyed by the exit time in ms like the existing
   transition path, so the ledger dedupes the two. Alternatives: a native
   modal (blocks the dashboard, not testable without a DOM). Why: the tab is
   compact and the box shows on every redraw until answered. Reverses if:
   operator QA asks for a modal.
6. Merge into main (plan 044 mounts landed first): `server/ew/app.py`
   conflicted in the docstring route list, the module import list and the
   GET router. Decision: union - both `mounts` and `summary` imported, both
   listed, `/api/mounts` then `/api/summary` GET routes; mounts keeps its
   POST route, summary stays GET-only. Alternatives: take either side (drops
   a shipped feature). Why: the hunks are purely additive and independent.
   Reverses if: never (merge mechanics only).
