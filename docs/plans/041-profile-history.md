# Plan 041 - Profile history: hourly BDO-REST-API snapshots, trend sparklines, auto level sample

Status: open. Deep dive + UX audit 2026-10-05 session 4 (research 0003-0005). Lane hint: `build`.

Spec gap: research 0003 C09 (value 4, M). Plan 004 shows one profile
snapshot; plan 011 level samples are typed; the profile API also exposes
`contributionPoints`, `energy`, `specLevels`, `mastery`, `combatFame`,
`lifeFame`, `gs`, `privacy` (openapi read 2026-10-05) that EW drops.

1. `server/ew/progress.py`: after each successful profile refresh (existing
   cache, at most hourly; never an extra call) append a snapshot
   `{at, level?, gs?, energy?, contribution?, combat_fame?, life_fame?,
   spec_levels?}` per character to `ops/runtime/profile_history.jsonl`
   (atomic append via tmp + replace of a rolling file, 90 days kept).
   Fields hidden by profile privacy are absent, never zero.
2. Auto level marker (display only): when the main character's profile
   level rises, add a plan 011 sample with `source: "profile"` and `pct:
   null` (XP percent unknown; the profile can lag up to the 1 h cache, so
   the timestamp is approximate). Changes in `server/ew/leveling.py`:
   a. `_clean_sample()` (~line 214) today drops `pct: null` and strips
      every other key; it must keep a marker `{ts, level, pct: None,
      source: "profile"}` (only that exact source value may carry a null
      pct; any other null-pct entry is still dropped, and typed samples
      keep their current shape without a `source` key).
   b. `_points()` / `rate_pct_h()` skip markers, so they never feed the
      rate or ETA.
   c. `view()` (~line 350) takes `pct`, rate and ETA from the LAST TYPED
      sample, and `level` = max(last typed level, last marker level); it
      adds `level_source: "typed"|"profile"` and `pct: null` when the
      marker level is higher than the last typed level (XP percent of that
      level unknown).
   d. Markers are written only by the server (profile refresh path), never
      posted: the IPC body validator `validLevelingBody` in
      `app/shared/ewcore.js` (~line 1472) stays strict, and a test asserts
      `{"sample": {"level": 60, "pct": null}}` is still rejected (so the
      dashboard cannot forge a marker). `sample_del` by `ts` deletes a
      marker like any sample.
   e. Render path: pure formatter `fmtLevelLine(d)` in `ewcore.js`
      returns `Lv N P%` for typed data and `Lv N (profile)` when `pct` is
      null and `level_source` is `"profile"`. `app/dashboard/leveling.js`
      (~line 114) uses it instead of `'Lv ' + d.level + '  ' + d.pct +
      '%'` (which would print `null%`); the sample list marks markers
      `(profile)`. The overlay path `C.levelingLine` (`ewcore.js` ~line
      1655, called from `app/overlay/overlay.js` ~line 99) today returns
      `no XP sample yet` whenever `pct` is null; it must build its first
      part through `fmtLevelLine` so a marker-only level shows `Lv N
      (profile)` plus rate / ETA / HOT from the typed samples, and still
      return `no XP sample yet` only when `level` is null;
      `normalizeLeveling` must pass `level_source` through (and accept `pct: null` when it is
      `"profile"`).
   Typed samples are untouched. Operator can disable with
   `profile.auto_level` (default on).
3. `GET /api/progress/history?field=&days=` -> series for sparklines.
4. Dashboard: sparklines for level, gs, energy, CP on the profile card.
5. Tests: `tests/test_progress.py` additions with a recorded profile
   fixture (`tests/fixtures`): snapshot written once per refresh, privacy
   gaps, rotation, auto marker rules. `tests/test_leveling.py`: a marker
   survives `_clean_sample` with `source` kept, a null-pct entry without
   `source: "profile"` is still dropped, a marker between two typed
   samples leaves `rate_pct_h` and the ETA unchanged, `view()` with a
   higher marker level returns that level with `pct: null` and
   `level_source: "profile"` while rate/ETA come from typed samples.
   `app/test/leveling.test.js`: `fmtLevelLine` never prints `null`;
   `levelingLine` with a profile-marker body (`level: 61, pct: null,
   level_source: "profile"`, typed rate present) returns a line starting
   `Lv 61 (profile)` and containing the rate, not `no XP sample yet`;
   `levelingLine` with `level: null` still returns `no XP sample yet`;
   `validLevelingBody` rejects a null-pct sample.

Acceptance: tests green; no additional HTTP request beyond plan 004's
cadence (test counts fetches); gates green; verifier PASS within 3 rounds.

ToS check: keyless public BDO-REST-API GET at the existing cadence.

Depends on: none.
