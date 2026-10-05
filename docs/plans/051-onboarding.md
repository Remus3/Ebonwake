# Plan 051 - First-run checklist: profile family, log path, ScreenShot folder, overlay corner, watch items, dailies

Status: open. Deep dive + UX audit 2026-10-05 session 4 (research 0003-0005). Lane hint: `build`.

Spec gap: research 0005 candidate F12 (value 3, S). Empty states say "Set
profile.family in config/local.json"; a fresh install has no guided path.

1. `server/ew/onboarding.py`: `status(root, store)` -> ordered steps with
   `done` computed from real state: profile family set, BDO documents dir
   found (plan 008), ScreenShot folder readable (plan 008), overlay anchor
   chosen (plan 022 key present), >= 3 market watch items, >= 1 daily,
   leveling sample present.
2. `GET /api/onboarding`; each step links to the Settings field (plan 030)
   or the relevant card; "dismiss" stored in the EW store.
3. Dashboard: a first-run card on Home (plan 025) or Today, hidden once all
   steps are done or dismissed.
4. Tests: `tests/test_onboarding.py` (each step's detection with temp dirs
   and stores, dismiss).

Acceptance: tests green; gates green; verifier PASS within 3 rounds.

ToS check: reads EW's own config and the plan 008 folders (existence only).

Depends on: 030.

Dependency guard: before writing code the lane checks that `server/ew/settings.py` exists (plan 030). If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["030"]` into its progress JSON (`ops/loop/control/progress/p051-build.json`) and exits 0. Plan 019's tick turns that clean, marked run into item state `blocked` (not `no-change`) and re-dispatches the row once every Depends-on row is `[x]`; plan 019's work-list gate normally keeps the row from being dispatched that early. If this row ran before plan 019 landed and was recorded `no-change`, 019's re-arm step makes it dispatchable again.
