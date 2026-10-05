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

## As-built deviations (adjudicated in-lane, 2026-10-05)

1. SSE `leveling` is a new mechanism, not "like the other tabs". Only `game`
   had a named SSE event; no tab did. Decision: `LevelingService.seq` bumps on
   every write; the `/events` loop now waits on the game condition in
   `SSE_TICK_S` (0.25 s) slices and also emits `event: leveling` (full GET
   body) when `seq` moved; heartbeats still go out only after a quiet
   `sse_interval`. Alternatives: a shared server-wide Condition every service
   notifies (touches gamewatch, a kit-free module but plan 008 territory);
   leveling-only polling with no SSE. Why: smallest change, gamewatch
   untouched, a dashboard save shows on the overlay within ~0.25 s. Reverses
   if: a third service needs push - then move to one shared notifier.
2. Overlay widget `leveling` defaults OFF (only a literal `true` turns it on),
   unlike the other widgets (default on). Decision: `WIDGETS_OPT_IN` in
   ewcore; `config/local.example.json` lists `"leveling": false`.
   Alternatives: default on like the rest. Why: spec says "opt-in"; the
   overlay window is a fixed 340x220 and the line is long, so it is added
   only by an operator who wants it. Reverses if: the operator enables it and
   asks for it on by default.
3. XP stack sums armed grind buffs through the server: `LevelingService`
   takes a `buffs` callable (the grind view's buff list); the GET body adds
   `xp_parts` (name + pct of each counted source) and `milestones_seed`
   (true while milestones equal the seed, drives the "seed, verify" label).
   Alternatives: client-side sum in the dashboard from two GETs. Why: one
   source of truth, the overlay needs one GET. Reverses if: never expected.
4. Grind buff `xp_pct` re-arm rule: a re-arm without `xp_pct` keeps the stored
   value; a clear keeps it too (one-tap re-arm). The Grind tab buff row gains
   a small "xp%" input (blank = keep / none). Alternatives: re-arm without it
   clears the value. Why: the operator sets an XP scroll's bonus once.
   Reverses if: the operator wants per-arm entry.
5. Sample identity is its `ts` (second precision); a second sample in the
   same second replaces the first. Rate deltas: walking newest to oldest, each
   delta pairs a sample with the newest sample at least 120 s older (closer
   ones are skipped); median of up to 5 such deltas, None if <= 0. A long
   offline gap counts as one slow delta (the median damps it). Reverses if:
   operator QA shows ETAs dragged down by overnight gaps - then cap a delta's
   span or drop gaps over N hours.
6. Validation details beyond the spec: hot window `start == end` is refused
   (zero or 24 h is ambiguous); at most 30 windows, label 1-40 chars,
   milestones at most 20 unique levels 1-70; `sample_del` takes the exact ts
   string the GET returned. Reverses if: a real event needs a 24 h window -
   then add an explicit all-day flag.
