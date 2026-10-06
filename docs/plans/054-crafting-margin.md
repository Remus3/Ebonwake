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

## As-built deviations (build lane, self-adjudicated 2026-10-06)

1. No "via imperial box" comparison. Plan 053 (imperial delivery) is still an
   open row and has no module to call, so the optional comparison has nothing
   to compare against. Alternatives: stub a box value here (duplicates 053's
   rules before they exist). Reverses if: plan 053 lands; it then adds an
   `imperial` field to each recipe row of `GET /api/crafting`.
2. Prices come only from the plan 002 sublist cache (`cached_price`, stale or
   not), never a fetch on GET, as plans 035 / 037 do. A market id the operator
   has never looked up is listed under `missing` with the hint "watch the item
   on the Market tab"; profit stays null rather than guessed. Alternatives: a
   live sublist fetch per id on every GET (up to 40 arsha calls per recipe).
   Reverses if: a shared cache-warming job for non-watched ids lands.
3. Sale side uses `net_proceeds(unit price)` per unit times the average yield
   (exact fraction, floored once per total), so per 1,000 crafts is exact and
   not 1,000 x a floored per-craft value. A vendor price on an input wins over
   the market price for that line (it is what the operator pays the NPC).
   `procs` are extra sale lines `{id, qty_avg}` (expected proc yield per
   craft). Recipe `kind` is `cooking` (default) or `alchemy`. Limits: 20
   inputs, 10 outputs, 10 procs, 50 recipes, qty 1..9999, qty_avg (0, 1000].
   Alternatives: per-stack net (needs a stack size the operator does not know).
   Reverses if: the tax rule in `market_rules.json` stops being per-unit linear.
4. Inputs are typed as text lines (`5 #9001`, `2 Leavening Agent @20`;
   outputs `2.5 #9213 Beer`) rather than per-line form rows, to fit 20 inputs
   in one compact card. Alternatives: item-search rows (plan 028) per input
   (much larger card). Reverses if: operator QA asks for search per line.
