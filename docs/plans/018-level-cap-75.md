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

Build lane 2026-10-05 (self-adjudicated; each reverses if the stated trigger
fires).

1. Epoch storage. Decision: tracked rows stay in `xp_epochs.json`; the
   store keeps only operator overrides (`epochs_added`, `epochs_deleted`)
   and the view merges them. `epoch_add` with an existing id corrects that
   row (its `kill_xp_cap` kept); `epoch_del` of a tracked id is a
   tombstone. Alternatives: copy the file into the store on first run;
   make the file read-only and allow only new ids. Why: a future tracked
   epoch reaches existing stores without a migration, and the operator can
   still re-date the seed to the confirmed maintenance. Reverses if: a
   tracked epoch must be force-pushed over an operator correction.
2. `verified` differs by file. Decision: epoch `verified` is a bool
   (operator-confirmed live date); preset `verified` is a transcription
   date like `grind_spots.json`. Alternatives: dates everywhere. Why: an
   epoch is either confirmed or not; a preset value is "as of" a day.
   Reverses if: the dashboard needs "confirmed on" for epochs.
3. Seed start `2026-10-08T00:00:00+00:00`, `verified: false`. The NA
   maintenance end time is not published yet; the card says "(verify
   date)" and the editor re-dates by id. Reverses if: patch notes land
   (then the operator or a lane re-dates it and sets verified).
4. Per-kill XP cap. Decision: `kill_xp_cap` bands on the tracked epoch row
   (research 0002: 1-5 ~20%, 50-55 ~2%, 56-61 ~0.2%, 62-75 ~0.01%); shown
   only while that epoch is active and the level is in a band (6-49 not
   published, so no row). Operator-added epochs carry no caps. Reverses
   if: per-level values are published.
5. Presets UI. Decision: one compact "arm preset" row (select + minutes)
   at the foot of the Buffs card, arming `{name, minutes, xp_pct}`;
   the pre-patch hint is computed server-side (`buffs[].xp_hint`) and
   shown beside the buff. Alternatives: three extra buff rows. Why: the
   Grind tab must fit 1280x800. Reverses if: the operator wants presets
   as permanent rows.
6. Re-verify badge only once an epoch has STARTED (before the patch the
   rows are still right); a row whose `epoch` equals the active epoch id is
   never stale; a row verified on the epoch's start date counts as
   post-patch. Reverses if: same-day transcriptions prove pre-patch.
7. No `monster_level` values seeded: no per-spot monster levels are
   published yet (the patched game UI will show them). Fields are
   supported and schema-tested; the gap note appears per row once a level
   is typed into the table. `MONSTER_LEVEL_RANGE` = 1..99. Reverses if: a
   sourced monster-level table appears (next deep dive).
8. Pearl outfit set 10 -> 50 kept as the research note states (not a
   doubling); the row says "verify". Reverses if: patch notes differ.
9. Presets and hint follow the epoch (refute round 1, minor 1): until an
    XP epoch has started, GET /api/grind offers each preset at its
    `pre_patch_xp_pct` (`patched: false`) and no "pre-patch value?" hint
    shows, so the XP stack is not inflated before the patch. Reverses if:
    a preset row needs its own epoch id (a later patch changes one buff).
10. Kill caps come from the newest started epoch that CARRIES caps (refute
    round 1, minor 2), so a later operator epoch does not hide them.
    Reverses if: a later tracked epoch publishes new caps (it then wins).
11. Self-test 7/7 not run inside the headless lane (needs the live
    Electron app); `app/test/selftest.test.js` stays green. The merge
    session runs it.

- Plan 019 dropped (deep dive 2026-10-05, refute round 2/3). Decision: the
  proposed "Patch-notes watcher: Deadeye / XP digest and stale-data flags"
  is not filed; 018 is the deep dive's only plan. Alternatives: keep 019 as
  filed; narrow it to the stale-data flags only. Why: the reviewer ruled it
  duplicates the existing Deadeye tab (plan 007), and the one non-duplicate
  part - "re-verify after patch" flags on sourced tables - is already carried
  by step 5 here (epoch-driven badge on `grind_spots.json` rows). Reverses
  if: the Deadeye tab is retired, or a patch lands that 018's epoch badges
  fail to flag and a watcher is shown to be the missing piece.
