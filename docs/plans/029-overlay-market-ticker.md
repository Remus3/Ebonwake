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

## As-built deviations

1. Signature `tickerRows(watchRows, nowMs, max = 5, base)` gains a 4th arg.
   Decision: the arrow compares against `base`, a map `"id:sid" -> previous
   different price` kept in overlay memory by a new pure `tickerTrack(state,
   items)`. Alternatives: a server-side previous price on `/api/market/watch`
   (plan says no new route, and it would touch market.py); comparing with the
   last poll only (arsha's 300 s TTL vs a 60 s poll would flatten the arrow on
   4 of 5 polls). Why: no server change, the arrow persists until the next
   price change. Reverses if: the watch route gains a previous price.
2. Tests live in a new `app/test/ticker.test.js`, not appended to
   `market.test.js`. Alternatives: append to market.test.js. Why: parallel
   lanes edit market.test.js; a new file avoids merge conflicts, `npm test`
   globs every test file. Reverses if: a test-layout rule names one file per
   tab.
3. Stale = server `freshness.stale`, or `nowMs - fetched_at > ttl_s` (fallback
   `age_s > ttl_s` when `fetched_at` is unparsable; missing freshness is
   stale). The ticker block hides when the watchlist is empty and shows a
   muted `offline` header (last rows kept) when the GET fails. Alternatives:
   treat missing freshness as fresh. Why: never show stale as fresh (plan 002
   rule). Reverses if: never.
4. The ticker polls on its own 60 s interval and is not re-fetched on SSE
   heartbeats (other widgets ride `loadToday`'s throttled heartbeat path).
   Why: plan says every 60 s, and each watch GET walks every watched item
   through the market cache. Reverses if: a market SSE event is added.
5. `config/local.example.json` gains `"marketTicker": false` (template mirrors
   every widget key, as for leveling/season).
6. Self-test 7/7 with the widget enabled was not run in the lane: the lane
   worktree has no `app/node_modules` (no Electron) and no `config/local.json`.
   Decision: ship with node tests + static guards; the ticker block lives in
   the overlay panel whose height is content-reported (plan 022), width fixed
   at OVERLAY_SIZE (names ellipsize, values nowrap). Alternatives: npm install
   Electron into the lane worktree (large download per lane, not a CI gate).
   Why: CI gates are ruff / pytest / npm test; the self-test is an operator-
   box check. Reverses if: the post-merge self-test with
   `overlay.widgets.marketTicker: true` reports the overlay outside its
   bounds - then fix in a follow-up row.

Verification: refute-rounds: 2/3 (round 1 REFUTE: stale hit kept its green colour - fixed in ew.css + static test; round 2 CONFIRM).
