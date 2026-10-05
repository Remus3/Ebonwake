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
