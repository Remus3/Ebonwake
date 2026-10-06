# Plan 039 - Loot-valued grind log: per-spot loot tables, tax-correct silver/h, sell-vs-vendor

Status: open. Deep dive + UX audit 2026-10-05 session 4 (research 0003-0005). Lane hint: `data`.

Spec gap: research 0004 F08 (value 5, effort M, top of the economy list) and
F12 (sell-vs-vendor, value 3, S; "one loot data file feeds both"). Plan 005
logs silver earned typed by hand; plan 012 has only a coarse silver tier.

1. `server/ew/data/loot_tables.json` (tracked): per spot id (plan 012 ids)
   `{items: [{id?, name, vendor_price?, marketable: bool}], source,
   verified}`; seed only spots with a citable source; every value
   `verified: false` unless the lane read it from a cited page today.
   Operator-added items and vendor prices live in the store.
2. `server/ew/grind.py`: a session may carry `loot: [{name|id, count}]`;
   `loot_value(loot, prices, vp, fame)` -> trash at vendor price (no tax),
   marketable at plan 027 net (65 / 84.5 percent), unknown price ->
   flagged; silver/h for the session from that value when present, else the
   typed silver (plan 005 unchanged).
3. `sell_or_vendor(item)` -> `vendor | market | either` with the
   difference; "trash pile worth X" summary for the session.
4. Dashboard: the Grind stop form gets a loot list (item select from the
   spot's table, count) and shows valued silver/h; per-item sell/vendor
   hint.
5. Tests: `tests/test_grind.py` additions (valuation, tax paths, missing
   price, sell-vs-vendor), data schema test.

Acceptance: tests green; a session logged with loot only (no typed silver)
shows a silver/h; gates green; verifier PASS within 3 rounds.

ToS check: operator-typed counts, sourced data, cached read-only prices.

Depends on: 027.

## As-built deviations

1. Seed is Black Stone (Weapon) 16001 / (Armor) 16002 on six plan 012 spots,
   all `verified: false`; no spot-specific trash seeded.
   Alternatives: seed trash names and vendor prices from memory; leave the file
   empty. Why: the lane had no web grant to read a cited page today, and the
   plan forbids unsourced values; trash + vendor price are operator-added in the
   Grind tab (`loot_item`), which the plan already puts in the store.
   Reverses if: a lane reads a spot page (e.g. bdocodex) and seeds trash rows
   with that day's date in `verified`.
2. The file is `{"_doc", "spots": {spot_id: {items, source, verified}}}` (one
   wrapper key for the doc string). A grind spot maps to a table by its own id,
   else by plan 012 name match (`Polly's Forest` -> `polly-forest`), because
   plan 005 spots are operator-named slugs, not plan 012 ids.
   Reverses if: plan 005 spots adopt plan 012 ids.
3. Loot is priced ONCE when the session is logged (stop / log), stored in the
   session (`loot`, `loot_value`); GET /api/grind stays offline. Live prices
   and the per-item sell/vendor hint come from a new GET
   `/api/grind/loot?spot=<id>` (cached arsha sublist, plan 027 vp / fame).
   Alternatives: re-price every session on each GET (network on every poll,
   history drifts with today's price). Why: the silver a session earned is
   what it was worth that day; GET /api/grind is read by Home, Spots and
   Leveling and must not block on the market. Reverses if: the operator wants
   history re-valued at current prices.
4. `sell_or_vendor(item, price, vp, fame)` takes the price and tax settings as
   arguments (plan text: `sell_or_vendor(item)`), keeping it pure.
   A marketable item without a market price is valued 0 and listed in
   `unknown` (flagged), even if a vendor price is known - per plan item 2.
5. Operator item ops: POST /api/grind `loot_item` {spot, name, marketable, id?,
   vendor_price?} (upsert by name, operator wins over the table) and
   `loot_forget` {spot, name}. A non-marketable item must carry a vendor price.

Dependency guard: before writing code the lane checks that `net_proceeds` exists in `server/ew/market.py` (plan 027). If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["027"]` into its progress JSON (`ops/loop/control/progress/p039-build.json`) and exits 0. Plan 019's tick turns that clean, marked run into item state `blocked` (not `no-change`) and re-dispatches the row once every Depends-on row is `[x]`; plan 019's work-list gate normally keeps the row from being dispatched that early. If this row ran before plan 019 landed and was recorded `no-change`, 019's re-arm step makes it dispatchable again.
