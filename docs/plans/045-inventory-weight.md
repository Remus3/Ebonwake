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

## As-built deviations

1. Sales ledger lives in the `inventory` store domain.
   - Decision: the operator logs each collected sale (`{"sale": {price, vp?}}`)
     on the Inventory card; the VP gain is `net_proceeds(price, True, fame) -
     net_proceeds(price, False, fame)`, fame stored on the sale at log time.
   - Alternatives: read a plan 027 sales log (none exists: 027 shipped the
     `net_proceeds` helper and settings, not a log); derive sales from grind
     loot values (those are estimates, not collected sales).
   - Why: the plan says "sales the operator logs"; no log existed to read.
   - Reverses if: a shared sales log lands; the ledger then reads it instead.
2. VP state source order: an armed plan 005 buff named "Value Pack"
   (case-insensitive) gives `ends` / `left_s`; else plan 030 `market.vp`
   true means "on, expiry unknown"; else off with the "+200 LT / +16 slots"
   reminder.
   - Alternatives: buff timer only (an operator with VP but no timer armed
     would see a false reminder).
   - Why: never nag an operator who told Settings they have VP.
   - Reverses if: the operator wants the timer to be the only signal.
3. Weight ranges are warnings, not validation: an owned LT outside a
   candidate range is stored and flagged "(unverified)", because research
   0004 marks every range unverified. Hard bounds only: 0..5000 per source,
   base 0..20000. Reverses if: ranges are re-verified against an official
   source (flip `verified` per row; validation can then tighten).
4. Slots and LT are typed WITHOUT the Value Pack; the server adds
   +200 LT / +16 slots while VP is on (VP numbers unverified, from
   `value_pack` in the data file). Reverses if: the operator prefers typing
   the in-game total.
5. "What VP is worth": optional operator-typed VP cost in silver; the card
   shows the last-30-day gain net of that cost. Not in the plan text; added
   because a gain without a cost does not answer "worth it". Reverses if:
   judged noise.
6. Warehouse VT: operator toggle `fame_vt` adds the +2,000 VT (the wiki rule
   does not state the fame threshold).
