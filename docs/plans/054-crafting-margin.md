# Plan 054 - Cooking / alchemy margin calculator: operator recipes, input/output prices, net after tax

Status: open. Deep dive + UX audit 2026-10-05 session 4 (research 0003-0005). Lane hint: `build`.

Spec gap: research 0004 F15 (value 3, M). Whether a cooking or alchemy
batch is worth it depends on input prices, output price and the 65 / 84.5
percent collect rate; today that is arithmetic in the operator's head.

1. `server/ew/crafting.py` + store: operator-typed recipes `{name, inputs:
   [{id|name, qty, vendor_price?}], outputs: [{id, qty_avg}], procs?}`;
   validation (<= 20 inputs, positive quantities).
2. `margin(recipe, prices, vp, fame)` -> cost, gross, net (plan 027
   helper), profit per craft and per 1,000 crafts, missing prices
   flagged; optional "via imperial box" comparison when plan 053 is
   present.
3. `GET/POST /api/crafting`; card on the Market tab.
4. Tests: `tests/test_crafting.py` (margins, vendor inputs, missing price,
   validation).

Acceptance: tests green; gates green; verifier PASS within 3 rounds.

ToS check: operator recipes and cached read-only prices.

Depends on: 027.

Dependency guard: before writing code the lane checks that `net_proceeds` exists in `server/ew/market.py` (plan 027). If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["027"]` into its progress JSON (`ops/loop/control/progress/p054-build.json`) and exits 0. Plan 019's tick turns that clean, marked run into item state `blocked` (not `no-change`) and re-dispatches the row once every Depends-on row is `[x]`; plan 019's work-list gate normally keeps the row from being dispatched that early. If this row ran before plan 019 landed and was recorded `no-change`, 019's re-arm step makes it dispatchable again.
