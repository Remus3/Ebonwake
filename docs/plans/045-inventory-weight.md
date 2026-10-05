# Plan 045 - Inventory / weight / storage planner + Value Pack ledger

Status: open. Deep dive + UX audit 2026-10-05 session 4 (research 0003-0005). Lane hint: `build`.

Spec gap: research 0004 F17 (value 2, S) and F16 (value 2, S). Weight
sources (training, pearl, belt, jewelry set, alchemy stone, crystals),
slots and per-town storage are tracked nowhere; the Value Pack timer exists
in plan 005 but not what it is worth.

1. `server/ew/data/weight_sources.json` (tracked): candidate LT sources
   with ranges and `verified: false` where research 0004 section 3 marks
   them unverified; warehouse rule 5,000 VT + 2,000 with fame (official
   wiki 47, verified 2025-08-06).
2. `server/ew/inventory.py` + store: operator checklist of owned LT sources
   (value typed), inventory slots, per-town storage notes; totals and
   "next cheapest +LT" from the operator's own notes.
3. VP ledger: VP expiry (reads the plan 005 Value Pack buff when present),
   silver gained by the +30 percent on sales the operator logs (plan 027
   net helper), "+200 LT / +16 slots" reminder when VP is off.
4. Routes `GET/POST /api/inventory`; card on the Progress tab.
5. Tests: `tests/test_inventory.py` (totals, validation, VP ledger maths).

Acceptance: tests green; gates green; verifier PASS within 3 rounds.

ToS check: operator-typed data only.

Depends on: 027.

Dependency guard: before writing code the lane checks that `net_proceeds` exists in `server/ew/market.py` (plan 027). If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["027"]` into its progress JSON (`ops/loop/control/progress/p045-build.json`) and exits 0. Plan 019's tick turns that clean, marked run into item state `blocked` (not `no-change`) and re-dispatches the row once every Depends-on row is `[x]`; plan 019's work-list gate normally keeps the row from being dispatched that early. If this row ran before plan 019 landed and was recorded `no-change`, 019's re-arm step makes it dispatchable again.
