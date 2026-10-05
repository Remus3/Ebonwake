# Plan 049 - Shared SSE event bus in the dashboard, hidden-tab poll pause, keyed row updates

Status: open. Deep dive + UX audit 2026-10-05 session 4 (research 0003-0005). Lane hint: `review`.

Spec gap: research 0005 candidate F14 (value 2, M) and M4: every poll
redraws whole lists (`textContent = ''`), dropping focus and scroll; all
tabs poll every 60 s while hidden; System polls the game card every 2 s
although the server already pushes a `game` SSE event.

1. `app/dashboard/dashboard.js`: one `EventSource` on the existing `/events`
   SSE endpoint (today opened separately by `leveling.js`), shared by modules through `bus.on(domain, fn)`;
   modules (`today`, `grind`, `game`, `leveling`, `market`) refresh on
   their domain event and on `show()`; hidden tabs pause their timers.
2. Server (`server/ew/app.py`): emit per-domain SSE events after successful
   POSTs (`today`, `grind`, `leveling`, `market`, `progress`, `events`) in
   addition to the existing ones; payload is the domain name only (clients
   re-GET).
3. Keyed reconciliation helper `reconcile(container, rows, key, render)` in
   `ewcore.js`; adopted by the market watchlist, Today lists and buff list.
4. Remove the 2 s game poll in `app/dashboard/game.js` in favour of SSE.
5. Tests: node tests for `reconcile` (insert, move, remove, focus kept on
   an unchanged row via a fake DOM) and the bus; `tests/test_server.py`
   asserts the domain events after a POST.

Acceptance: tests green; self-test at merge; gates green; verifier PASS
within 3 rounds.

ToS check: local server and dashboard only.

Depends on: none.
