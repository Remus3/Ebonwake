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

## As-built deviations

1. Seed box list is empty. Decision: `boxes: []` in the tracked file, plus
   operator boxes in the store (POST `box_add` / `box_del`, Today card form).
   Alternatives: seed recipes from fan sites or memory; seed nothing and offer
   no way to add boxes. Why: the lane could not cite any box contents (no web
   access in the lane; research 0004 cites only the rules), and the plan says
   seed only citable rows; without an operator path the card would value
   nothing. Reverses if: a sourced box list lands in the data file (rows then
   shadow-protect against operator duplicates).
2. Payout on base price, input cost on last sold price. Decision: payout =
   payout_pct x sum(qty x basePrice) x (1 + mastery / 100), floored; cost =
   sum(qty x lastSoldPrice); each falls back to the other when one is absent.
   Alternatives: one price for both (makes "value per input silver" a
   constant 2.5 x mastery and the ranking meaningless). Why: the NPC values
   the box at the item's market base price while buying the inputs costs the
   going rate. Reverses if: wiki 133 or an official note states the payout
   follows the last sold price.
3. POST `/api/imperial` added (deliver / cp / mastery / box_add / box_del).
   The plan lists only the GET; "ticks per box count" and "operator-typed" CP
   and mastery need a write path. Ticks are +/- deltas clamped to 0..cap (or
   10000 while CP is unknown) so a double click never overshoots.
4. Reset = NA daily reset, 00:00 UTC (`reset` rule in the data file, read by
   `today.last_reset`). "Midnight server time" in wiki 133 maps to the NA
   server's 00:00 UTC daily reset used by plan 003. Reverses if: an official
   source puts the imperial reset at another hour (edit the rule in the file).
5. `MarketService.cached_prices` added (base + last from the sublist cache,
   never fetches); imperial reads no live prices.

Dependency guard: before writing code the lane checks that `lifeskill_card` exists in `server/ew/progress.py` (plan 042). If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["042"]` into its progress JSON (`ops/loop/control/progress/p053-build.json`) and exits 0. Plan 019's tick turns that clean, marked run into item state `blocked` (not `no-change`) and re-dispatches the row once every Depends-on row is `[x]`; plan 019's work-list gate normally keeps the row from being dispatched that early. If this row ran before plan 019 landed and was recorded `no-change`, 019's re-arm step makes it dispatchable again.
