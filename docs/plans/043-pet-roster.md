# Plan 043 - Pet roster: 5 slots, tier, talents, special-skill coverage, exchange planner

Status: open. Deep dive + UX audit 2026-10-05 session 4 (research 0003-0005). Lane hint: `build`.

Spec gap: research 0003 C16 (value 3, M) and section 1.4. Pets drive loot
speed and detection; the tier-5 Alpha rule and the exchange rule (5 same
type -> 100 percent T4, parents destroyed) are easy to get wrong.

1. `server/ew/data/pets.json` (tracked): species -> special skill
   (gathering detection, flagged-player detection, elite marking,
   auto-fishing, desert resistance, gathering amount, taunt), feed values
   (Cheap 12, Good 80, Organic 140), T5 Alpha rule text, each with
   `source` (BDFoundry pets guide 2025-07-25, official T5 wiki 2025-07-25)
   and `verified`.
2. `server/ew/pets.py` + store section: roster rows `{name, species, tier
   1-5, talents [..], alpha: bool, out: bool, fed_at?}`; validation: at most
   5 `out`, at most one `alpha` and only on T5.
3. Coverage check -> missing roles among the out pets (loot speed always,
   plus detection / fishing / gathering as operator-selected goals);
   exchange planner: given pets of one species, chance to T4 and "parents
   destroyed" warning.
4. Routes `GET/POST /api/pets`; card "Pets" on the Progress tab.
5. Tests: `tests/test_pets.py` (validation, coverage, planner, schema).

Acceptance: tests green; gates green; verifier PASS within 3 rounds.

ToS check: operator-typed roster, sourced static data.

Depends on: none.
