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
