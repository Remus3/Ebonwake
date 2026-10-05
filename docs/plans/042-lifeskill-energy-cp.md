# Plan 042 - Lifeskill / energy / contribution card from the profile API

Status: open. Deep dive + UX audit 2026-10-05 session 4 (research 0003-0005). Lane hint: `build`.

Spec gap: research 0003 C10 (value 3, S) plus the life-skill fields named
in the same row of section 2 (`specLevels`, `mastery`, `energy`,
`contributionPoints`). Weekly CP quests open at 220-349 CP (Thursday reset),
byproducts exchange for CP XP (BDFoundry CP guide 2026-07-01).

1. `server/ew/data/cp_milestones.json` (tracked, sourced): CP thresholds
   with labels (weekly CP quests 220-349, worker-empire comfort 250+) and
   `verified`.
2. `server/ew/progress.py`: `lifeskill_card(snapshot)` -> per life skill
   name + rank text (as the API returns it), energy, CP with next milestone
   and gap; fields missing under privacy show `hidden`.
3. `GET /api/progress` adds `lifeskill`; dashboard card "Life & CP" on the
   Progress tab with the trend from plan 041.
4. Tests: card builder on recorded fixtures (public, private, partial);
   data schema test; node formatter tests.

Acceptance: tests green; gates green; verifier PASS within 3 rounds.

ToS check: keyless public BDO-REST-API data already fetched by plan 004.

Depends on: 041.

Dependency guard: before writing code the lane checks that `profile_history` appears in `server/ew/progress.py` (plan 041). If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["041"]` into its progress JSON (`ops/loop/control/progress/p042-build.json`) and exits 0. Plan 019's tick turns that clean, marked run into item state `blocked` (not `no-change`) and re-dispatches the row once every Depends-on row is `[x]`; plan 019's work-list gate normally keeps the row from being dispatched that early. If this row ran before plan 019 landed and was recorded `no-change`, 019's re-arm step makes it dispatchable again.
