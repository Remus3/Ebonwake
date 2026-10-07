# Plan 013 - Season pass tracker by objective

Status: built in lane (2026-10-05), verifier PASS; refute-rounds: 2/3. Ranked
third (adjudicated 2026-10-05).

Spec gap: plan 004 seeds the season pass as coarse level steps. A leveling
Season player works the pass objective by objective (reach level N, enhance
Tuvala gear to +X, finish quest Y) and wants "next unclaimed reward".

1. Track kind `season` gains objectives: `{id, title, kind: level|gear|quest|
   other, target?, reward, done_at?, claimed_at?}`. Level objectives auto-tick
   from plan 011 level samples or the plan 004 character level.
2. Seed = structure only (titles + kinds), editable, labelled "seed, verify
   against the in-game pass"; the operator edits and ticks.
3. Progress tab card: n/N, next 3 open objectives, "done but unclaimed"
   highlighted (claiming stays the operator's act in game).
4. Overlay opt-in line: "Pass 23/40 - next: Lv 50 (2 lv)".

Acceptance: migration from the plan 004 coarse steps keeps done marks; gates
green; verifier PASS within 3 rounds; one push.

## As built (2026-10-05) - contract

- Store: a `season` track is `{id, title, kind: "season", seed: bool,
  objectives: [{id, title, kind, target, reward, done_at, claimed_at}]}` (no
  `steps`). Other kinds unchanged. `target`: level 1-70 (required), gear null or
  1-20 (16-20 = PRI DUO TRI TET PEN), quest/other null. `reward` 0-80 chars.
- GET `/api/progress` season tracks add `seed, objectives[] (+ done, claimed,
  auto, gap), claimed, unclaimed: [ids], next: [<=3 open], level` and keep a
  `steps` mirror so `done/total/pct` and plan 004 clients still work. Top-level
  `season` = summary of the first season track or null.
- POST `/api/progress` adds `{"claim": {track, objective, claimed}}`,
  `{"obj_add": {track, title, kind, target?, reward?}}`, `{"obj_edit": {track,
  objective, title?|kind?|target?|reward?}}`, `{"obj_del": {track,
  objective}}`. Ticking stays `{"step": {track, step, done}}`.
- Overlay widget `season` (opt-in) -> `seasonLine`, e.g. `Pass 23/40 - next:
  Lv 50 (2 lv) | claim 2`.

## As-built deviations (adjudicated in-lane, 2026-10-05)

1. Level source = max(newest plan 011 sample level, plan 004 character level);
   auto-tick STAMPS `done_at` (persisted on the next read or write), it is not
   derived per read. Alt: derive done from level each read. Why: a deleted or
   corrected sample must not silently untick a reward the operator saw. Reverses
   if: QA wants a wrong sample's ticks undone automatically.
2. Unticking a level objective whose target is already reached is refused (400,
   "fix the level to untick"); the dashboard disables that checkbox. Alt: allow
   it and let auto-tick re-stamp on the next read (checkbox flips back). Why: no
   flicker, one obvious fix path. Reverses if: operator wants a manual override
   flag.
3. Migration: a season track whose steps are exactly the plan 004 coarse seed
   (`Level 10..50, Graduate`) is replaced by the new objective seed, done marks
   carried by level target (Graduate by id), `seed: true`; any other season
   step list migrates 1:1 ("Level N"/"Lv N"/"Reach Lv N" -> level N, else
   other), `seed: false`. Persisted once at service start. Alt: always 1:1 (the
   operator would never see the richer seed). Why: the operator's store holds
   the untouched plan 004 seed. Reverses if: never expected.
4. Seed = 24 objectives (12 level, 7 gear, 4 quest, Graduate), titles only,
   rewards blank. `seed` stays true through ticks/claims and goes false on any
   objective add/edit/delete; while true the card shows "seed, verify against
   the in-game pass". Alt: no seed. Why: plan item 2. Reverses if: the operator
   supplies the real pass list - replace `SEASON_SEED`.
5. Claim is a separate mark (`claimed_at`) that needs `done`; unticking clears
   it; a stored claim without done is dropped on load. Claiming in game stays
   the operator's act (ToS floor). Reverses if: never.
6. The overlay season line GETs `/api/progress` (plan 004 had the overlay never
   touch that route); `app/test/progress.test.js` now asserts no POST / bridge
   and GET-only uses instead. It refreshes on the 60 s cadence and on each SSE
   `leveling` event (a new sample can auto-tick). Alt: a separate
   `/api/season` route. Why: one source of truth, no new route. Reverses if:
   the profile background refresh triggered by that GET is ever a problem -
   then add `?refresh=0`.
7. (refute r1) Objectives are edited in place from the card ("edit" per row ->
   inline form -> `obj_edit` with all four fields), keeping done/claim marks
   and list position; while the form is open, background redraws skip the
   track cards so typing is not clobbered, and any other row action (tick,
   claim, delete, add) abandons the open edit. A kind change without a target
   drops a target the new kind cannot take (to level still needs one). Alt:
   delete + re-add. Why: that lost done/claim marks. Reverses if: never.
8. `test_route_get_progress` (plan 004) now expects the extra top-level
   `season` key; grind widget defaults test lists `season: false`. Contract
   extensions only.
