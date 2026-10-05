# Plan 018 - Level cap 75 readiness: one level range, patch epoch, re-seeded milestones

Status: open. Sole plan of deep dive 2026-10-05 (research 0002 section 1;
the proposed 019 was dropped, see As-built deviations). One lane (`build`).

Spec gap: the Global Lab notes posted 2026-10-02 raise the max level 70 -> 75,
rescale combat XP for every level range, add a per-kill XP cap, double XP buff
values and re-ladder the main quests (Lv 56 / 60 / 61). They usually reach
NA/EU at the next maintenance (2026-10-08 or the one after). EW hard-codes
`LEVEL_RANGE = (1, 70)` three times (`server/ew/progress.py`,
`server/ew/leveling.py`, `server/ew/spots.py`), so a level 71+ entry would be
rejected (400) the day the patch lands, and plan 011 rates measured before the
patch would feed post-patch ETAs.

1. One constant: `server/ew/levels.py` holds `LEVEL_MAX` (75) and
   `LEVEL_RANGE = (1, LEVEL_MAX)`; progress, leveling and spots import it.
   A test fails if any `server/ew/*.py` assigns its own `LEVEL_RANGE`
   (same guard style as `tests/test_ports.py`). Raising the range is safe
   before the patch is live: 71-75 simply cannot be typed in game yet.
2. Patch epoch: `server/ew/data/xp_epochs.json` (tracked, data only, ASCII):
   `[{id, starts_utc, label, source, verified}]`, first row the level-cap-75
   rescale with `starts_utc` = the operator-confirmed live maintenance
   (seed: 2026-10-08 maintenance, labelled "verify against patch notes").
   `leveling.py` computes rate and ETA only from samples on or after the
   newest epoch start that is <= now; older samples stay listed but greyed
   ("pre-patch"). Epochs are editable via `POST /api/leveling
   {"epoch_add": {...}}` / `{"epoch_del": id}` with plan 011 validation.
3. Milestones: new seed [56, 60, 61, 70, 75] with labels (main questline end,
   Rebirth of Darkness, Olvia course, AP/DR bonus starts, cap). Migration:
   a store whose milestones equal the OLD seed [50, 56, 57, 58, 60, 61] is
   moved to the new seed; operator-edited lists are kept untouched.
4. XP buff presets: the Grind buff entry form offers named presets with
   `xp_pct` (Body Enhancement 100, Adventure Blessing 30, Pearl outfit set 50)
   from `server/ew/data/xp_buffs.json` (`source`, `verified` per row,
   "community/patch-note value, verify"). Existing buffs are never rewritten;
   a buff whose name matches a preset but whose `xp_pct` equals the
   pre-patch value (50 / 15 / 10) shows a "pre-patch value?" hint.
5. Level-gap note in the Grind "Where next" card (plan 012): when a spot row
   carries an optional `monster_level`, show the gap and the patch's
   out-level DR bonus (+3 per level, max +9). `grind_spots.json` rows gain an
   optional `monster_level` and `epoch` field; rows `verified` before the
   newest epoch get a "re-verify after patch" badge (no row is deleted).
6. Per-kill XP cap: shown as a one-line info row on the Leveling card for the
   current level band (data in `xp_epochs.json`, not code); EW makes no XP
   prediction from it.
7. ToS: operator-typed data and tracked data files only; no game input, no
   client file.

Acceptance: a level 75 sample accepted and 76 rejected on all three routes;
rate ignores pre-epoch samples (unit test across the epoch boundary);
milestone migration tested (old seed -> new seed, edited list kept); presets
and stale badges schema-tested; gates green (pytest, node); self-test 7/7;
verifier PASS within 3 rounds; one push.

Operator act (not code, batched into the next operator ask): claim unclaimed
character-switch XP before the 2026-10-08 maintenance (deleted then).

## As-built deviations

- Plan 019 dropped (deep dive 2026-10-05, refute round 2/3). Decision: the
  proposed "Patch-notes watcher: Deadeye / XP digest and stale-data flags"
  is not filed; 018 is the deep dive's only plan. Alternatives: keep 019 as
  filed; narrow it to the stale-data flags only. Why: the reviewer ruled it
  duplicates the existing Deadeye tab (plan 007), and the one non-duplicate
  part - "re-verify after patch" flags on sourced tables - is already carried
  by step 5 here (epoch-driven badge on `grind_spots.json` rows). Reverses
  if: the Deadeye tab is retired, or a patch lands that 018's epoch badges
  fail to flag and a watcher is shown to be the missing piece.
