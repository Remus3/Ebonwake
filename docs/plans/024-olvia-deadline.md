# Plan 024 - Olvia Academy deadline vs level ETA: red pill when Lv 60 lands after enrolment closes

Status: open. Deep dive + UX audit 2026-10-05 session 4 (research 0003-0005). Lane hint: `build`. Priority row.

Spec gap: research 0003 C02 (value 5, effort S, top 2). Olvia Academy
(Lv 60+) gives a TET/PEN Blackstar weapon, PEN boss armour, 60 Essence of
Dawn and 6,000 Cron Stones; Class 3 enrolment closes 2026-11-05 and quest
acceptance 2026-11-11 (BDFoundry 2026-07-30; official notice not yet read).
Plan 006 stores deadlines and plan 011 computes the level ETA, but nothing
links them, so a leveling Season Deadeye can miss a hard cut-off.

1. `server/ew/data/deadlines.json` (tracked, ASCII): rows `{id, label,
   needs_level, enrol_by_utc, quests_by_utc, source, verified}`; seed
   `olvia-class-3` (needs 60, enrol 2026-11-05, quests 2026-11-11, source
   BDFoundry Olvia guide, `verified: false` with note "re-check the
   official notice"). Operator overrides via `POST /api/leveling
   {"deadline_set": {...}}` / `{"deadline_del": id}` stored like plan 018
   epochs (tracked rows + operator overrides, tombstones).
2. `server/ew/leveling.py`: `deadline_status(deadline, level_now, eta)` ->
   `{state: done|on_track|tight|late|unknown, reach_utc, margin_h}`:
   `done` when level >= needs_level; `late` when the plan 011 ETA to
   `needs_level` is after `enrol_by_utc`; `tight` when margin < 72 h;
   `unknown` without a rate. The ETA to an arbitrary target level reuses the
   plan 011/018 rate (post-epoch samples only).
3. `GET /api/leveling` adds `deadlines: [...]`; the Leveling card shows
   "Olvia: Lv 60 by Nov 5 - you reach 60 ~Oct 29 (on track)" with a pill;
   the overlay `leveling` widget shows the pill only when tight or late.
4. Events tab (plan 006) lists the deadline read-only in "Ending soon"
   when within 14 days (no duplicate store entry).

Acceptance: `tests/test_leveling.py` cases for each state including across
the 018 epoch boundary and no-rate; validation tests for overrides; node
tests for the pill formatter; gates green; verifier PASS within 3 rounds.

ToS check: operator-typed XP samples and tracked deadline data only.

Depends on: none.

## As-built deviations

Adjudicated in-lane (2026-10-05); none waits on the operator.

1. `deadline_status(deadline, level_now, eta, now)` takes `now` as a 4th
   argument. Alternatives: return a relative reach offset; read a global
   clock. Why: the module is pure with an injected clock (plan 011), and
   `reach_utc` / `margin_h` need an anchor. Reverses if: a caller needs the
   3-argument form.
2. `late` also covers "enrolment already closed, level not reached" even with
   no rate (otherwise it would read `unknown` after the cut-off). Reverses if:
   the operator wants closed deadlines hidden instead.
3. Seed times are 00:00 UTC on the published day (the earliest reading of
   "closes Nov 5"), recorded in an optional `note` field the row schema gains
   (`{..., note}`, printable ASCII, <= 200). Alternatives: guess the NA
   maintenance hour. Why: an early cut-off can only make the pill
   conservative. Reverses if: the official notice gives an hour (operator
   `deadline_set` the row, or edit the tracked file).
4. Overrides are API-level (`POST /api/leveling {deadline_set|deadline_del}`,
   also accepted by the dashboard preload validator); no editor form on the
   Leveling card. Alternatives: a fourth details-row form. Why: one seeded
   row, the card must fit 1280x800, and the plan names the POST only.
   Reverses if: the operator wants to edit deadlines in the dashboard.
5. The Events tab gets the rows server-joined in `GET /api/events`
   `deadlines` (EventsService takes a `deadlines` provider); they also count
   toward the "N ending soon" pill. Alternatives: a second fetch of
   `/api/leveling` from the Events tab. Why: one poll per tab, one window
   rule (14 days, not done, not past) on the server. Reverses if: never.
6. A deadline row leaves `GET /api/leveling` `deadlines` once its final
   cut-off (`quests_by_utc`, else `enrol_by_utc`) has passed; it can still be
   `deadline_del`-eted. Why: deviation 2 alone kept a red overlay pill
   forever (verifier r1 minor 5). Reverses if: the operator wants a history.
7. `margin_h` is rounded to 0.1 h before classifying, so the state always
   agrees with the shown margin (72.0 is never tight, 0.0 never late;
   verifier r1 minor 3).

Refute record: verifier round 1/3 PASS (no majors). Minors 3 and 5 fixed
above; left as-is, with reasons: (1) the Events-tab deadline countdown
refreshes per poll, not per second - minute-level drift on a 14-day window;
(2) a new XP sample reaches the Events tab on its next 60 s poll, not by SSE;
(4) `deadline_error` is not shown on the card, matching `epoch_error`
(both are a follow-up together); (6) dates are UTC days by design (the
published cut-off is a UTC day; the early 00:00 reading is in the row note).
refute-rounds: 1/3
