# Plan 012 - Grind spot recommender by AP/DP and level

Status: open. Ranked second (adjudicated 2026-10-05).

Spec gap: the Grind tab logs spots the operator already chose; nothing says
where a Season Deadeye with AP x / DP y at level z should grind next.

1. Data file `server/ew/data/grind_spots.json` (tracked, data only, ASCII):
   `[{id, name, region, ap_min, dp_min, level_min, xp_tier 1-5,
   silver_tier 1-5, notes, source, verified}]`. Seeded from public community
   guides, coarse bands, labelled "community recommendation, verify". No row
   without `source` and `verified` (date).
2. `server/ew/spots.py`: rank spots for {ap, dp, level, goal: xp|silver}:
   eligible when ap >= ap_min, dp >= dp_min, level >= level_min; score = goal
   tier, tie-break by smallest non-negative AP headroom; also the next 2
   "unlock" spots with the AP/DP still missing.
3. Character AP/DP/level come from plan 004's character (`/api/progress`);
   query overrides allow what-if.
4. `GET /api/spots?goal=xp|silver[&ap=&dp=&level=]`; Grind tab card "Where
   next": top 3 + 2 unlocks; a spot click pre-fills the session log spot name.
   Plan 005's own per-spot silver/h shows beside the community tier when the
   operator has logged that spot.

Acceptance: ranking unit-tested on a fixture table; data file schema-tested;
gates green; verifier PASS within 3 rounds; one push.
