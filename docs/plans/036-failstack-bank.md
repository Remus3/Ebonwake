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

Dependency guard (checked 2026-10-05: `server/ew/enhance.py` present, plan 035 merged): before writing code the lane checks that `server/ew/enhance.py` exists (plan 035). If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["035"]` into its progress JSON (`ops/loop/control/progress/p036-build.json`) and exits 0. Plan 019's tick turns that clean, marked run into item state `blocked` (not `no-change`) and re-dispatches the row once every Depends-on row is `[x]`; plan 019's work-list gate normally keeps the row from being dispatched that early. If this row ran before plan 019 landed and was recorded `no-change`, 019's re-arm step makes it dispatchable again.

## As-built deviations

Self-adjudicated by the build lane (2026-10-05).

1. Plan 007 steps carry no gear family, so the family is guessed from the
   item text with the plan 035 rule (first table family, underscores as
   spaces, found in the lower-cased item); the server mirrors
   `ewcore.enhanceFamilyGuess`. The "next step" is the first open (not done)
   plan step, and its attempt level is the first level in (current, target]
   that has a rate row for the family, so an accessory at +0 lands on PRI.
   Alternative: a `family` field on each step plus an editor. Why: no schema
   or UI change, and the EV panel already relies on the same guess.
   Reverses if: two families can match one item name, or the operator asks
   for an explicit picker.
2. Stack advice and the budget live in `GET /api/deadeye` as a `stacks`
   block (`fs_bank`, `agris` with `threshold` / `fails_to_guarantee`,
   `crons`, `advice`, `budget`); every POST op returns the same body. The
   rate rows come from the enhance service (operator overrides included).
   Alternative: a separate route. Why: one poll feeds the whole tab.
   Reverses if: the block grows heavy enough to slow the 60 s poll.
3. Validation details: FS `value` 1..999, `count` 1..999 per (kind, value)
   row (adds merge, a use that would go below 0 is refused, a row at 0 is
   removed), at most 60 bank rows and 60 Agris rows; `agris_set` takes any
   family / plan 035 step (threshold `null` when the table has none) and
   `stacks: 0` clears the row; `crons_set` is partial (`owned` and/or
   `weekly_income`, at least one). Ties in the FS suggestion prefer kind
   order advice, saved, cry. A bank where every stack is above the soft cap
   suggests nothing (reason given), per "without exceeding it".
   Reverses if: the operator wants the nearest-above stack offered too.
4. Cron budget: for every open step, each level with `crons_per_attempt` is
   costed at the row's soft-cap FS (exact table chance) as
   `crons_per_attempt x crons_attempts` from plan 035 `attempts()` (the
   guaranteed attempt spends no crons), with the Agris threshold reduced by
   the pity stacks already recorded for that family / level. Levels with a
   cron count but no chance data at the soft cap (e.g. Kharazad DUO / TRI)
   are listed as `unknown`, not counted. `weeks` = ceil(gap / weekly
   income), 0 when covered, `null` when there is a gap and no income.
   Alternative: cost at the suggested bank FS. Why: the soft cap is the
   recommended stack and keeps the budget independent of today's bank.
   Reverses if: the operator wants budget at the stacks actually stored.
5. Bridge validator (`validDeadeyeBody`) accepts the four new ops with exact
   shapes so the dashboard can write through the preload; `rate_set` /
   `rate_del` stay loopback-only (plan 035 deviation 5).

refute-rounds: 1/3 (verifier CONFIRM round 1: ruff clean, pytest 1777/0,
node 353/0, leak sweep clean).
