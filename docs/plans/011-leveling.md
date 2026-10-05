# Plan 011 - Leveling tracker: XP rate, level ETA, Hot Time windows, XP stack

Status: open. One lane (`build`). Ranked first of 011-014 (adjudicated
2026-10-05, see ROADMAP "Ranking"): the operator is leveling a new Season
Deadeye now; every leveling hour goes further with an XP rate, a next-level
ETA and "is Hot Time on, what is my XP bonus" at a glance.

Spec gap: spec section 3 lists Hot Time only as a buff name (plan 005) and
level only as a number (plan 004). Nothing tracks XP progress, rate or
recurring Hot Time windows.

1. Store `ops/runtime/store/leveling.json` (atomic writes):
   `{"samples": [{ts, level, pct}], "hot_windows": [{id, days, start, end,
   label, pct}], "milestones": [int], "next_id": int}`.
   - `samples`: operator-typed level (1-70) + XP percent (0-100, up to 3
     decimals; BDO shows e.g. 37.512 %). Newest 500 kept.
   - `hot_windows`: recurring weekly windows in UTC (`days` subset of 0-6,
     Monday=0; `start`/`end` "HH:MM", end may wrap past midnight), `pct` the
     combat XP bonus of that window (0-1000). Operator-entered from the
     official event notice; NO seeded times (stale times are worse than none,
     same rule as plan 006).
   - `milestones`: default seed [50, 56, 57, 58, 60, 61], operator-editable;
     the UI labels them "seed, verify against the current season notice".
2. Pure math module `server/ew/leveling.py`: rate in level-percent per hour
   from consecutive samples (a level rollover counts +100 pct per level), the
   median of the last 5 deltas spanning >= 2 minutes each; ETA to next level
   = (100 - pct) / rate; None with fewer than 2 usable samples or rate <= 0.
   Hot window status at `now`: active (ends_in_s) or next (starts_in_s).
   XP stack = sum of `pct` of active hot windows + active grind buffs that
   carry an `xp_pct` (plan 005 buff entries gain an optional `xp_pct`, 0-1000;
   backward compatible, absent = not counted).
3. API: `GET /api/leveling` -> `{now, level, pct, rate_pct_h|null,
   eta_next_s|null, next_milestone|null, hot: {active: [...], next|null},
   xp_stack_pct, milestones, hot_windows, samples: [last 20]}`;
   `POST /api/leveling` with exactly one of `{"sample": {level, pct}}`,
   `{"sample_del": ts}`, `{"hot_add": {days, start, end, label, pct}}`,
   `{"hot_del": id}`, `{"milestones": [...]}`. Validation like plan 004 (400
   on bad shape, existing body-size cap honoured). `/api/state` sources gains
   `leveling`. SSE event `leveling` on change, like the other tabs.
4. Dashboard: a "Leveling" card on the Progress tab (no new tab; the 1280x800
   no-scroll rule holds): level + pct quick entry (one row, Enter submits),
   rate, ETA, next milestone, Hot Time now/next with countdown, XP stack total.
   Hot window editor in a collapsed details row.
5. Overlay: opt-in widget `leveling` (WIDGETS list): one line such as
   "Lv 52 37.5% | 4.1 %/h | ETA 15h12m | HOT 1h03m +50%".
6. ToS: operator-typed data only; no game input, no client file. OCR of the
   XP bar is a possible later follow-up, not this plan.

Acceptance: gates green (pytest, node); leveling math unit-tested (rollover,
sparse samples, wrap-past-midnight windows, UTC only); self-test 7/7; verifier
PASS within 3 rounds; one push.
