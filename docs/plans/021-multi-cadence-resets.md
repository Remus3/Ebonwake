# Plan 021 - Multi-cadence resets: per-item reset rule (Sunday weeklies, non-midnight dailies)

Status: open. Deep dive + UX audit 2026-10-05 session 4 (research 0003-0005). Lane hint: `build`. Priority row.

Spec gap: research 0003 C05 (value 4, effort S) and section 1.6. Plan 003
models only `daily` (00:00 UTC) and `weekly` (Thursday 00:00 UTC) in
`server/ew/today.py` (`KINDS`, `THURSDAY`, `last_weekly_reset`). Black Shrine
solo (5/week) and the Altar of Blood weekly reset Sunday 00:00 UTC (verified
BDFoundry 2026-07-10 / official patch notes 2026-09-10); the Black Spirit's
Adventure dice may reset 05:00 UTC (unverified, 2023 source). Those items
tick and clear on the wrong day today. Prerequisite for plans 033 and 056.

1. Data model: a Today item may carry `reset: {"every": "day"|"week",
   "weekday": 0-6 (Mon=0, week only), "at": "HH:MM" (UTC)}`. `kind` stays
   (`daily` / `weekly` / `event`) for display and migration; an item without
   `reset` behaves exactly as today (daily 00:00, weekly Thursday 00:00).
   No store migration rewrites existing items.
2. `server/ew/today.py`: `last_reset(rule, now)` / `next_reset(rule, now)`
   generalise `last_daily_reset` / `last_weekly_reset` (both kept as thin
   wrappers so plan 003 tests stay green). The view groups items by their
   own next reset and returns `next_reset` per item.
3. Validation on add/edit (`POST /api/today`): weekday 0-6, `at` 00:00-23:59,
   `every` in the two values; errors 400 with plan 003 wording.
4. Seeds: `server/ew/data/reset_rules.json` (tracked, ASCII) with
   `{name, reset, source, verified}` rows for "Black Shrine (5/week)" and
   "Altar of Blood weekly" (Sunday 00:00, verified) and "Black Spirit's
   Adventure dice" (day 05:00, `verified: false`, UI shows "(verify)").
   The Today add form offers these as presets; nothing is auto-added to an
   existing store.
5. Dashboard (`app/dashboard/today.js`): an item with a non-default rule
   shows its own countdown ("resets Sun 00:00 UTC in 2d 4h"); the add form
   gets an optional "custom reset" row. Pure helpers in `ewcore.js`
   (`fmtResetRule`) with node tests.

Acceptance: unit tests across Saturday 23:59 -> Sunday 00:00 for a Sunday
item, 04:59 -> 05:00 for a 05:00 daily, unchanged behaviour for legacy items
(existing `tests/test_today.py` untouched and green), validation 400s; node
tests for the formatter; gates green; verifier PASS within 3 rounds.

ToS check: operator-typed checklist data and a tracked seed file only.

Depends on: none.

## As-built deviations

Built by the `build` lane 2026-10-05. Tests: `tests/test_today_resets.py`,
`app/test/today_resets.test.js`; `tests/test_today.py` untouched.

1. Legacy items get no new view keys.
   Decision: `reset` and `next_reset` appear only on items that carry their own
   rule; legacy items keep the exact plan 003 key set and use the top-level
   `daily_reset` / `weekly_reset`. "Groups by its own next reset" is realised as
   per-item done-state and countdown; list grouping stays by `kind`.
   Alternatives: `next_reset` on every item; regroup the view by reset instant.
   Why: `tests/test_today.py::test_view_shape` pins the item key set and the
   plan requires that file untouched and green; regrouping would break the
   daily / weekly / events cards and the overlay n/m.
   Reverses if: plan 003's shape test is retired.
2. No edit op. Rules are set on `add` only; `POST /api/today` has no edit op
   (plan 003 has tick / untick / add / remove / move).
   Alternatives: a new `set_reset` op. Why: smallest change; remove + re-add
   with a preset covers it. Reverses if: plan 033 / 056 need in-place edits.
3. Rule must match kind. `daily` takes `every: day`, `weekly` takes
   `every: week`, `event` takes no rule (400). A stored rule that is malformed
   or mismatched degrades to the kind default instead of failing the read.
   `at` defaults to `00:00`; `weekday` is required for `week` and refused for
   `day`. Alternatives: let a weekly item carry a daily rule. Why: `kind` still
   decides the card, so a mismatch would show a daily item in the Weekly card.
   Reverses if: a card-by-rule layout replaces cards-by-kind.
4. Presets reach the dashboard as `reset_presets` on `GET /api/today`
   (`[{name, kind, reset, source, verified}]`, `kind` derived from `every`);
   an unreadable seed file yields `[]` there while `today.load_presets()`
   raises (a test pins the tracked file). The form fills title / kind / custom
   reset row from a preset; unverified rows are labelled "(verify)".
   Alternatives: a separate route. Why: one fetch, no new route or IPC guard.
   Reverses if: presets grow past a few dozen rows.
5. The seed checklist's existing "Black Spirit's Adventure dice" item stays a
   plain midnight daily (no store migration, per item 1); the 05:00 preset is
   offered for operators who want it. Reverses if: the 05:00 reset is verified.

refute-rounds: recorded by the merge.
