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

## As-built deviations

Self-adjudicated in the build lane (2026-10-05); none needs the operator.

1. Exchange chance below 5 pets is `null` ("chance not sourced"), not a
   number. Alternatives: interpolate (20 percent a pet) or copy a community
   table. Why: the sourced rule only says 5 same-type pets give 100 percent
   T4; any other number would be invented. Reverses if: a dated source with
   the 2-4 pet chances is transcribed into `pets.json` `exchange`.
2. Exchange candidates are same-species pets below T4 that are not the
   Alpha, first 5 in roster order; parent tier equality is not enforced and
   T5 pets are never candidates (a trained T5 cannot be exchanged). The plan
   card shows which out pets would be destroyed. Alternatives: enforce equal
   parent tiers, or let the operator pick parents. Why: the source does not
   state a tier-equality rule; roster order is predictable and the warning
   names every parent. Reverses if: a source states the tier rule, or the
   operator asks to pick parents.
3. Goals beyond loot are data-driven (`pets.json` `goals`): detection
   (flagged-player detection), elite marking, fishing (auto-fishing),
   gathering (gathering detection AND amount), desert (desert resistance);
   a goal is covered when the out pets hold every listed skill. Alternatives:
   only the plan's three goals. Why: elite marking and desert resistance are
   the two grind-relevant skills the plan's species list already carries.
   Reverses if: the operator drops a goal (edit the data file).
4. Species `other` (no special skill, no exchange type) is added for pets
   outside the sourced list. Why: the operator must be able to type any pet;
   it never counts toward a skill goal or an exchange plan. Reverses if: the
   species list is completed from a source.
5. `fed_at` is a "fed" mark stamped by POST `feed` (feeding stays the
   operator's act in game); hunger is not simulated. The feed values (12 / 80
   / 140) are shown as reference data only. Why: no sourced hunger drain
   rate. Reverses if: a sourced drain rate is added.
6. Roster cap 40 pets, talents up to 5 names of 40 ASCII characters each.
   Why: bounded store and POST size (4 KiB guard). Reverses if: a real
   roster exceeds it.
7. GET `/api/pets?exchange=<species>` returns one species' plan (the view
   already lists every species with 2+ candidates). Why: "given pets of one
   species" from the plan, without a POST.
