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

## As-built deviations (build lane, self-adjudicated 2026-10-05)

Built: `server/ew/data/grind_spots.json` (20 rows), `server/ew/spots.py`,
`GET /api/spots` in `server/ew/app.py`, ewcore helpers `spotsPath`,
`spotRecs`, `spotNeedText`, `matchSpot`, Grind tab card "Where next".
Tests: `tests/test_spots.py`, `app/test/spots.test.js`.

1. Seed rows transcribed without a live fetch.
   Decision: rows carry the community AP/DP bands as recalled from the Garmoth
   grind tracker; `verified` is the transcription date, not a live re-check;
   every row's notes say "community recommendation, verify" and the API sends
   a `disclaimer`. Alternatives: wait for a web-tool grant (blocks the plan);
   ship an empty table (card useless). Why: web tools were not granted to the
   build lane; coarse bands are what the plan asks for and are flagged.
   Reverses if: a data lane re-checks rows against the live source (robots.txt
   respected) and updates bands + `verified`.
2. Character AP is max(ap, aap) from plan 004's `gs`.
   Alternatives: `ap` only; average. Why: Deadeye fights on the higher of the
   two and community tables quote a single AP. Reverses if: the operator wants
   a specific stat, or tables start quoting AAP separately.
3. Unlock order (plan named only "next 2"). Ineligible spots whose goal tier is
   at least the best eligible tier come first (a step up, not sideways), then
   smallest AP + DP missing, fewest levels missing, higher tier, id; filled
   from lower tiers when fewer than 2 qualify. Alternatives: smallest gap only
   (suggests sideways moves). Reverses if: operator QA finds the order unhelpful.
4. `goal` defaults to `xp` when absent; a bad goal / ap / dp / level is a 400.
   When AP, DP or level is unknown (Progress unset, no what-if) the body lists
   it in `missing` and returns empty `top` / `unlocks` instead of guessing.
   A bad or missing table answers 200 with `status: "error"` and empty lists
   (the card says so) rather than failing server start.
5. "A spot click pre-fills the session log spot name": the session spot is a
   select of logged spots, so a click selects the matching logged spot
   (case-insensitive name) or, if not logged yet, fills the "new spot" field
   for one-tap Add spot. Nothing is posted. Alternative: auto-add the spot
   (a write the operator did not ask for). Reverses if: operator prefers
   auto-add.
6. Plan 005 silver/h beside the tier is matched by exact case-insensitive
   name and shown only for spots with at least one logged session
   (`logged_silver_per_h`, else null). Alternative: fuzzy match (false hits).
