# Plan 029 - Overlay market ticker: up to 5 watched prices, alert hits first

Status: open. Deep dive + UX audit 2026-10-05 session 4 (research 0003-0005). Lane hint: `build`.

Spec gap: research 0005 H3 and candidate F5 part 2. Spec section 3 lists
"one market watch ticker" on the overlay and section 8 acceptance requires
"5 watched prices ... on the overlay"; `app/overlay/overlay.js` and
`WIDGETS` in `app/shared/ewcore.js` have none.

1. New opt-in widget `marketTicker` in `WIDGETS` (`ewcore.js`), default off,
   enabled through `overlay.widgets.marketTicker` (config; plan 030
   Settings later).
2. Pure `tickerRows(watchRows, nowMs, max=5)`: alert hits first, then by
   operator order; each row `name, short price, arrow vs previous, net
   (plan 027) when present, pre-order badge`, muted with `stale` when the
   source age > TTL.
3. `app/overlay/overlay.js` renders it from `GET /api/market/watch` every
   60 s (no new route); fits the content-sized overlay from plan 022.
4. Tests: `app/test/market.test.js` / `overlay` pure helpers: ordering,
   max 5, stale, missing net.

Acceptance: node tests green; self-test 7/7 with the widget enabled shows
the ticker inside the overlay bounds; gates green; verifier PASS within 3
rounds.

ToS check: overlay stays a click-through `globalShortcut`-toggled window;
data from the existing read-only market cache.

Depends on: 022, 027.

Dependency guard: before writing code the lane checks that `overlayBounds` exists in `app/shared/ewcore.js` (plan 022); `net_proceeds` exists in `server/ew/market.py` (plan 027). If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["022", "027"]` into its progress JSON (`ops/loop/control/progress/p029-build.json`) and exits 0. Plan 019's tick turns that clean, marked run into item state `blocked` (not `no-change`) and re-dispatches the row once every Depends-on row is `[x]`; plan 019's work-list gate normally keeps the row from being dispatched that early. If this row ran before plan 019 landed and was recorded `no-change`, 019's re-arm step makes it dispatchable again.
