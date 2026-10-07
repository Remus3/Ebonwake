# Plan 027 - Market net proceeds after tax (VP / fame) + pre-order-queue badge

Status: open. Deep dive + UX audit 2026-10-05 session 4 (research 0003-0005). Lane hint: `build`.

Spec gap: research 0004 F01 (value 4, S), F02 (value 3, S) and research
0005 L6. Watchlist prices are shown gross; the seller collects 65 percent
(35 percent tax), 84.5 percent with a Value Pack (+30 percent of the
65), plus a Family Fame bonus whose steps are unverified (official wiki 47,
2025-08-06). Items with `currentStock == 0` or `lastSoldPrice == priceMax`
are pre-order queues (Essence of Dawn pinned at its 100 M cap on
2026-10-05); the Hot list shows `stock 0` without context.

1. `server/ew/market.py`: `net_proceeds(price, vp, fame_pct)` (integer
   silver, floor) and `preorder_state(item)` -> `capped` (last sold ==
   priceMax), `no_stock`, or `null`. Constants in
   `server/ew/data/market_rules.json` (tracked): `tax` 0.35, `vp_bonus`
   0.30, `pearl_tax_exempt` note, `fame_steps` with `verified: false`,
   `source` = official wiki 47 URL, `verified` 2025-08-06.
2. Settings: `market.vp` (bool) and `market.fame_pct` (0-1.5, operator
   typed) in `config/local.json` (example updated); read by the server
   with defaults false / 0.
3. `GET /api/market/watch` rows gain `net` and `preorder`; `/api/market/hot`
   rows gain `preorder`.
4. Dashboard `app/dashboard/market.js`: a `net` column (short form, exact
   on hover), a pair calculator "buy at X, sell at Y -> profit after tax"
   in the item detail, a `pre-order` badge with a tooltip ("listing fills
   only from pre-orders at max; >= 20 B items fill at random") replacing
   bare `stock 0` (L6).
5. Tests: `tests/test_market.py` (net for VP off/on, fame, rounding; badge
   states), `app/test/market.test.js` (column + badge formatting).

Acceptance: 100 M gross -> 65,000,000 / 84,500,000 net fixtures; badge
fixtures from a recorded `/item` payload in `tests/fixtures`; gates green;
verifier PASS within 3 rounds.

ToS check: arsha.io v2 read-only GET data already cached by plan 002 plus
operator settings; no buy/sell action.

Depends on: none.

## As-built deviations (build lane, self-adjudicated 2026-10-05)

1. Badge fixture is shape-faithful, not byte-recorded. Decision:
   `tests/fixtures/market/hot_preorder.json` uses the arsha v2 hot/sublist
   field set with values matching the 2026-10-05 state the plan names
   (Essence of Dawn pinned at 100 M, stock 0). Alternatives: one read-only
   GET to arsha from the lane; copying a cached body from the main checkout's
   `ops/runtime/cache/market/`. Why: the lane sandbox has no network grant and
   no read access outside its worktree; the rule under test only reads
   `currentStock`, `lastSoldPrice` and `priceMax`, whose names the plan 002
   fixtures already pin. Reverses if: a session with network access records a
   live body - drop it in under the same file name.
2. `market_rules.json` key layout. Decision: `verified` is the top-level
   source date ("2025-08-06"); the unverified fame steps live under
   `fame_steps: {"verified": false, "steps": [...]}`. Alternatives: a bare
   `fame_steps` list plus `fame_verified`. Why: the plan names both a
   `verified` date and `fame_steps` with `verified: false`; nesting keeps both
   names literal. Reverses if: a shared rules-file schema lands - adopt it.
3. Net formula and rounding. Decision: net = floor(price x (1 - tax) x
   (1 + vp_bonus [VP] + fame_pct / 100)), every rate taken to basis points
   and multiplied as integers (Python int, JS BigInt) so a 1 T sale floors
   exactly on both sides. Basis points round half up as floor(x + 0.5) on
   the same double in both languages (Python `round` is half-even and split
   from JS `Math.round` at 12.5 bp - verifier round 1 finding). Alternatives:
   float maths; fame applied to gross; fame only in 0.5 steps.
   Why: matches the plan's 65,000,000 / 84,500,000 fixtures and the
   community formula (bonuses add on the after-tax amount); floats drift by
   1 silver at 1e11+. Reverses if: the official wiki shows fame applied
   differently.
4. Fame setting validation. Decision: `market.fame_pct` outside 0-1.5, a
   bool or a non-number falls back to 0 (a non-bool `vp` to false), not
   clamped. Alternatives: clamp to 1.5. Why: a typo like 15 should not
   silently become the max bonus; the default under-reports profit, the safe
   side. Reverses if: the operator asks for clamping.
5. `/api/market/watch` also carries `tax` {vp, fame_pct, tax, vp_bonus,
   fame_verified}; the dashboard calculator mirrors the server rule in
   `ewcore.js netProceeds` with those rates. Alternatives: a new
   `/api/market/net?buy=&sell=` route. Why: no extra route or round trip per
   keystroke; one formula pinned by the same fixtures in both test suites.
   Reverses if: the rules grow beyond flat rates.
6. Watch rows also get the pre-order badge (beside the alert badge), not
   only the Hot list. Why: a watched capped item has the same fill caveat.
   Reverses if: QA finds it noisy.
