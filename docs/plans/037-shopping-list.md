# Plan 037 - Shopping list from the enhancement plan: materials x arsha prices, can-afford-by

Status: open. Deep dive + UX audit 2026-10-05 session 4 (research 0003-0005). Lane hint: `build`.

Spec gap: research 0004 F04 (value 4, effort M) and research 0005 F15
(enhancement helper links plan steps to market prices). Today the operator
adds up black stones, Essence of Dawn, crons and caphras by hand.

1. `server/ew/shopping.py`: from plan 007 open steps x plan 035 expected
   attempts x per-attempt materials -> totals per item id; priced with the
   plan 002 cache (exact silver), `null` with `missing price` when absent;
   `can_afford_by` = (total - silver on hand) / plan 005 average silver/h
   (operator hours per day setting, default 3).
2. `GET /api/deadeye/shopping` -> `{lines: [{id, name, qty, unit, total,
   preorder}], total, can_afford_by}`; `silver_on_hand` and `hours_per_day`
   via `POST /api/deadeye`.
3. Dashboard: "Shopping list" card on the Deadeye tab with a "watch" button
   per line that adds the id to the plan 002 watchlist (existing POST),
   names via plan 028's index.
4. Tests: `tests/test_shopping.py` (totals, missing price, afford date with
   zero rate -> null); node formatter tests.

Acceptance: tests green; gates green; verifier PASS within 3 rounds.

ToS check: read-only cached prices and operator data; the "watch" button
only edits EW's own watchlist, never a market order.

Depends on: 035, 028.

Dependency guard: before writing code the lane checks that `server/ew/enhance.py` exists (plan 035); `server/ew/itemnames.py` exists (plan 028). If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["035", "028"]` into its progress JSON (`ops/loop/control/progress/p037-build.json`) and exits 0. Plan 019's tick turns that clean, marked run into item state `blocked` (not `no-change`) and re-dispatches the row once every Depends-on row is `[x]`; plan 019's work-list gate normally keeps the row from being dispatched that early. If this row ran before plan 019 landed and was recorded `no-change`, 019's re-arm step makes it dispatchable again.
