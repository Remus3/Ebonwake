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

## As-built deviations

Self-adjudicated in the build lane (2026-10-05); each line: decision /
alternatives / why / reverses if.

1. `leveling` gets no second, name-only event. Alt: emit both. Why: the plan
   011 `leveling` event (full view) already fires on every leveling POST via
   `LevelingService.seq`, and the overlay consumes that payload; a duplicate
   would double every re-GET. The dashboard bus passes either payload shape
   and modules re-GET on a non-view one. Reverses if: a leveling write stops
   bumping `seq`.
2. Domain event data is the JSON-encoded name (`data: "today"`), not a bare
   word. Alt: bare text. Why: every other SSE frame is JSON, so one
   `JSON.parse` path serves all. Reverses if: a non-JS consumer needs bare text.
3. The server bumps a per-domain counter (`DomainBus`) only after a 200 POST;
   each stream diffs its snapshot on the existing 0.25 s SSE tick. Alt: a
   condition variable waking streams at once. Why: the tick loop already
   exists; 0.25 s worst case is far inside the UX bar. Reverses if: a push
   needs to beat 0.25 s.
4. Hidden-tab pause covers every polled tab module (today, grind, game,
   leveling, market, progress, events, deadeye, pets, inventory, bosses), not
   only the five bus modules; Home keeps polling because its snapshots drive
   the tab badges. A domain event on a hidden tab clears `last` so the next
   `show()` re-reads. Window un-hide (`visibilitychange`) re-runs `show()`
   for the active tab. Reverses if: badges move off the Home snapshots.
5. The game card keeps a 60 s fallback poll (paused when hidden) instead of
   no poll. Alt: SSE only. Why: a dropped stream would otherwise freeze the
   card until reconnect. A stream reconnect re-emits every domain with null
   data, so all bus modules re-GET what they missed. Reverses if: never needed
   in practice (drop it then).
6. `reconcile(container, rows, key, render)`: `render(row)` is a pure builder;
   the row's JSON is its signature (unchanged -> node kept, changed -> rebuilt
   in place). Per-GET or ticking values are kept out of the signature (market
   `freshness` incl. `age_s`, plus the selection class; Today reset / event
   countdowns; grind buff `left_s` folded into `on`) and written after the
   reconcile through nodes stored on the row (refute round 1). Today's "This week" list was
   adopted too. Alt: per-module patch functions. Why: one generic rule, no
   per-field diff code. Reverses if: a row's JSON becomes too large to stringify
   per poll.
7. Gate fix outside scope: the plan 044 mount delete button in `progress.js`
   lacked the plan 047 `aria-label` (density test red at base). Two legacy
   source-pattern tests (`leveling.test.js`, `shell.test.js`) were updated to
   the bus shape.
8. Accepted minor (verifier round 1): a stream reconnect makes Today re-GET
   twice (it listens to `today` and `events`). Why accepted: it only happens
   on reconnect and costs two local GETs. Reverses if: reconnects become frequent.

9. Merge onto main after plan 048 (resolve lane, 2026-10-05): `grind.js`
   buff list conflicted (048 rewrote the full-rebuild rows to use
   `C.buffRowView`; 049 moved them to keyed `reconcile`). Decision: keep the
   049 keyed `buffRow` builder and fold 048's view into it - `C.buffRowView`
   runs in the reconcile row map (`on` = `v.armed`, i.e. `left_s > 0`;
   `minutes` = the short duration string), unarmed rows render a muted `off`,
   the minutes field takes 048's `maxLength 8` / `max 30d` title, and the
   post-reconcile clock writes `v.left`. Alt: take 048's full rebuild (loses
   049 focus/scroll keeping) or 049's block as-is (loses 048 short durations
   and muted `off`, fails the 048 source test). Why: both features survive
   and the ticking time stays out of the row signature (the bus.test.js
   signature check now matches `on: v.armed`). Reverses if:
   `buffRowView` starts returning a per-tick value used in the signature.

Verification: refute-rounds: 2/3. Round 1 REFUTE (market signature held
`freshness.age_s`; fixed). Round 2 CONFIRM. Gates at round 2: ruff clean,
pytest ~2199 passed, node 433/433.
