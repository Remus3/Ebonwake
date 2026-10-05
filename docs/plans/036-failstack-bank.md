# Plan 036 - Failstack bank + Agris pity tracker + cron budget

Status: open. Deep dive + UX audit 2026-10-05 session 4 (research 0003-0005). Lane hint: `build`.

Spec gap: research 0004 F05, F06, F07 (values 3/3/2, effort S each, merged).
Stored failstacks (Advice of Valks, saved stacks, Valks' Cry), Agris
Essence pity stacks per gear category and step, and crons on hand vs
crons needed are kept in the operator's head.

1. Store sections in `server/ew/deadeye.py`: `fs_bank` (rows `{kind:
   advice|saved|cry, value, count}`), `agris` (rows `{family, step,
   stacks}`), `crons` (`{owned, weekly_income}`); validated POST actions on
   `/api/deadeye` (`fs_add`, `fs_use`, `agris_set`, `crons_set`).
2. Advice: for the next plan 007 step, suggest the stored FS closest to the
   plan 035 soft cap without exceeding it; Agris shows "guaranteed in N
   fails" from the plan 035 threshold table; cron budget shows owned vs
   expected crons for planned steps (plan 035 `crons_per_attempt` x
   expected attempts) and weeks to cover the gap.
3. Dashboard: a compact "Stacks" card on the Deadeye tab.
4. Tests: `tests/test_deadeye.py` additions (validation, suggestion picks,
   N-fails arithmetic, cron gap weeks); node formatter tests.

Acceptance: tests green; gates green; verifier PASS within 3 rounds.

ToS check: operator-typed inventory of stacks; no game or market action.

Depends on: 035.

Dependency guard: before writing code the lane checks that `server/ew/enhance.py` exists (plan 035). If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["035"]` into its progress JSON (`ops/loop/control/progress/p036-build.json`) and exits 0. Plan 019's tick turns that clean, marked run into item state `blocked` (not `no-change`) and re-dispatches the row once every Depends-on row is `[x]`; plan 019's work-list gate normally keeps the row from being dispatched that early. If this row ran before plan 019 landed and was recorded `no-change`, 019's re-arm step makes it dispatchable again.
