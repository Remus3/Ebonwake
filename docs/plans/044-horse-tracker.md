# Plan 044 - Mount tracker: horses (tier, level, skills), Royal Fern Root counter, T10 breed pity calc

Status: open. Deep dive + UX audit 2026-10-05 session 4 (research 0003-0005). Lane hint: `build`.

Spec gap: research 0003 C17 (value 2, M) and section 1.5. Tier 10 breeding
needs two T9 Lv 30 parents of the same type, a Mythical Censer, 100 Royal
Fern Roots (time-gated), 100 Flowers of Oblivion and 10 Mythical Feathers;
success 3 percent base, +0.2 percent per failure (BDFoundry 2026-07-03).
Camel / mini elephant are quest unlocks.

1. `server/ew/data/mounts.json` (tracked, sourced): T10 recipe, pity rule,
   camel and elephant unlock notes, `verified` per row.
2. `server/ew/mounts.py` + store: mounts `{name, kind, tier, level, gender,
   skills [..]}`; material counters `{royal_fern_root, flower_of_oblivion,
   mythical_feather, censer}`; `breed_odds(failures)` and
   `attempts_for(p_target)` with the +0.2 per failure pity.
3. Routes `GET/POST /api/mounts`; card "Mounts" on the Progress tab with
   "materials 63/100 fern roots, ~N days at the daily rate typed".
4. Tests: `tests/test_mounts.py` (validation, pity math vs brute force,
   schema).

Acceptance: tests green; gates green; verifier PASS within 3 rounds.

ToS check: operator-typed data, sourced static data.

Depends on: none.
