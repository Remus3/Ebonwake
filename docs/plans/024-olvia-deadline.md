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
