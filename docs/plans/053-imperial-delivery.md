# Plan 053 - Imperial delivery planner: CP/2 boxes per type, 250 % box value, reset countdown

Status: open. Deep dive + UX audit 2026-10-05 session 4 (research 0003-0005). Lane hint: `build`.

Spec gap: research 0004 F14 (value 3, M). Imperial crafting delivery pays
250 percent of a box's market price, no tax, with daily boxes = CP / 2 per
type (cooking, alchemy), resetting at midnight server time (official wiki
133, 2026-07-17). Nothing in EW plans it.

1. `server/ew/data/imperial_boxes.json` (tracked, operator-extendable):
   box rows `{type: cooking|alchemy, name, items: [{id, qty}], source,
   verified}`; seed only rows the lane can cite; rules from wiki 133.
2. `server/ew/imperial.py`: `daily_cap(cp)` (CP from plan 042's card when
   present, else operator-typed), `box_value(box, prices)` (250 percent of
   input market price, mastery bonus operator-typed), best boxes by
   value per input silver.
3. `GET /api/imperial`; a Today-tab card with boxes left today and the
   reset countdown, ticks per box count.
4. Tests: `tests/test_imperial.py` (cap, valuation, missing price, schema).

Acceptance: tests green; gates green; verifier PASS within 3 rounds.

ToS check: cached read-only prices, operator data; no market action.

Depends on: 042.

Dependency guard: before writing code the lane checks that `lifeskill_card` exists in `server/ew/progress.py` (plan 042). If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["042"]` into its progress JSON (`ops/loop/control/progress/p053-build.json`) and exits 0. Plan 019's tick turns that clean, marked run into item state `blocked` (not `no-change`) and re-dispatches the row once every Depends-on row is `[x]`; plan 019's work-list gate normally keeps the row from being dispatched that early. If this row ran before plan 019 landed and was recorded `no-change`, 019's re-arm step makes it dispatchable again.
