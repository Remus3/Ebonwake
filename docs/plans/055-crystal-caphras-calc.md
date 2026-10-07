# Plan 055 - Jetina boss-crystal planner + Caphras cost calculator

Status: open. Deep dive + UX audit 2026-10-05 session 4 (research 0003-0005). Lane hint: `data`.

Spec gap: research 0003 C13 (value 3, S) and C14 / research 0004 F24
(values 2 / 1). Jetina weekly gives 155 Concentrated Boss Crystals for 2
Latent Boss Auras (Thursday reset), 60-120 crystals per reform level
(BDFoundry 2026-07-02). Caphras: 20 levels on boss / green gear at TRI+,
PEN boss main-hand C1-C10 = 8,895 stones, C11-C20 = 29,403 more (official
wiki 146 ranges + preview), Caphras Stone id 721003; not usable on
Blackstar.

1. `server/ew/data/boss_crystal.json` and `caphras.json` (tracked):
   per-level costs with `source` / `verified` (`false` for preview-only
   rows); `allowed_grades` for caphras with Blackstar / Kharazad / Sovereign
   marked `unverified`.
2. `server/ew/deadeye.py` calculators: `weeks_to_reform(crystals_on_hand,
   target_levels)`; `caphras_cost(slot, from, to, price)` -> stones and
   silver (price from the plan 002 cache for 721003); guard "not on
   Blackstar" from the data file.
3. Routes under `GET /api/deadeye/calc?kind=crystal|caphras&...`; a small
   "Calculators" card on the Deadeye tab.
4. Tests: `tests/test_deadeye.py` additions (week arithmetic, caphras sums
   match 8,895 / 29,403 fixtures, guard, schema).

Acceptance: tests green; gates green; verifier PASS within 3 rounds.

ToS check: sourced static data, operator counts, cached read-only price.

Depends on: none.

## As-built deviations

1. Range rows, not per-level rows (both data files).
   Decision: `caphras.json` stores per-range stone totals (C0-C10 8,895
   verified; C10-C20 29,403 `verified: false`, preview only);
   `boss_crystal.json` stores the 60-120 per-level band, not a per-level list.
   `caphras_cost` is exact when it crosses whole ranges and prorates linearly
   inside a range, flagged `approx: true`; `weeks_to_reform` returns a
   `{min, max}` band, exact when the operator types `per_level`.
   Alternatives: invent a per-level split; refuse any endpoint inside a range.
   Why: the cited sources publish only ranges / a band and the lane has no web
   access; inventing numbers breaks the sourced-data rule, refusing hides a
   useful estimate. Reverses if: a sourced per-level table lands (add a
   `levels` list per slot; the range validator stays).
2. Grade guard is data-driven and also covers Kharazad / Sovereign.
   Decision: `allowed_grades` is `{grade: {allowed, verified, source}}`;
   boss / green allowed (verified), blackstar / kharazad / sovereign
   `allowed: false, verified: false`. The calc takes an optional `grade` that
   overrides the slot's own; a not-allowed grade is a 400 ("Caphras is not
   usable on Blackstar gear"). Alternatives: guard Blackstar only; allow the
   unverified grades. Why: the plan marks all three unverified; refusing is the
   safe default for a cost tool. Reverses if: a source shows Caphras on
   Kharazad / Sovereign (flip `allowed`).
3. Only one slot (`boss_main_pen`) ships. Why: it is the only sourced cost
   series. Reverses if: more sourced ranges are added (data-only change).
4. Calculators live on `DeadeyeService.calc` (injected `price(item_id)` =
   `market.cached_price`, never fetches); `GET /api/deadeye/calc` with no
   `kind` returns the tables the card needs. A typed `price` overrides the
   cache (`price_source` operator / cache / null).
