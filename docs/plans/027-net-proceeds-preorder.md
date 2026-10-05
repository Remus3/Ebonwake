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
