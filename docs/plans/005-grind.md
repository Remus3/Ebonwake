# Plan 005 - Grind tab

Status: open. Lanes: `build` (slice A), `data` (slice B).

## Goal

A grind session log (spot, minutes, silver earned, trash count -> silver/h),
per-spot averages, and buff timers (XP/drop scrolls, Hot Time, Value Pack, Old
Moon book, Kamasylve blessing) with an overlay widget. All operator input;
nothing is read from the game (OCR is plan 009).

## Slice A - server (lane `build`)

Files: `server/ew/grind.py`, `server/ew/app.py` (POST_ROUTES), `tests/test_grind.py`.

1. Store domain `grind`: `{"spots": [{id, name}], "sessions": [{id, spot,
   started, minutes, silver, trash}], "active": {spot, started}|null,
   "buffs": [{id, name, ends}]}`. Seed spots empty; seed buff names (no timers).
2. `GET /api/grind` -> `{active (with elapsed_s), sessions (newest first, last
   200), spots: [{id, name, sessions, minutes, silver_per_h (avg, int)}],
   buffs: [{id, name, ends, left_s}] (expired excluded)}`.
3. `POST /api/grind`: `{"start": spot}`, `{"stop": {silver, trash}}` (minutes
   from active.started, min 1), `{"log": {spot, minutes, silver, trash}}`
   (manual), `{"delete": session_id}`, `{"add_spot": name}`,
   `{"buff": {name, minutes}}` (ends = now + minutes; re-arming replaces),
   `{"clear_buff": id}`. Limits: minutes 1-1440, silver 0-10^13, trash
   0-10^6, names 1-60 chars.
4. silver/h = silver * 60 / minutes, integer; spot average weights by minutes.
5. `/api/state` `sources.grind`.

### Slice A deviations (adjudicated in-lane, 2026-10-04)

- Expired buffs. Decision: every buff stays listed; an expired or cleared one
  reports `ends: null, left_s: null` (unarmed), and `clear_buff` disarms rather
  than deletes. Alternatives: drop expired entries from the list. Why: seeded
  names have no timer, and dropping a buff once it expires would make the
  slice B "one-tap arm" card lose it. Reverses if: slice B wants a separate
  `names` list and armed-only `buffs`.
- Buff minutes. Decision: 1-43200 (30 days); sessions stay 1-1440. Alternatives:
  1-1440 for buffs too. Why: Value Pack and Kamasylve blessing run for days.
  Reverses if: buffs are limited to in-session scrolls.
- Ids and identifiers. `start`/`log` take a spot id (slug of the name, e.g.
  `oluns-valley`); session ids are `s<N>` from a stored never-reused counter;
  `add_spot` refuses a duplicate name (case-insensitive); `buff` re-arms by
  name (case-insensitive) or adds a new name. Stop clamps minutes to 1..1440
  (floor of elapsed). Stored sessions are capped at 2000 (oldest dropped);
  spot averages cover all stored sessions. GET also carries `now` (as
  `/api/today` does). Spots with no sessions report `silver_per_h: 0`.

## Slice B - dashboard + overlay (lane `data`)

Files: `app/dashboard/grind.js`, `dashboard.js`, `index.html`,
`app/overlay/overlay.js`, `app/shared/ewcore.js` (`silverPerHour`,
`fmtElapsed`, `validGrindBody` + allowlist route `/api/grind`),
`app/shared/ew.css`, `app/test/grind.test.js`.

Cards: Session (spot picker, start/stop with a live elapsed clock, stop form
silver + trash), Log (recent sessions, scroll in card, delete with two-click
confirm), Spots (avg silver/h, sorted), Buffs (one-tap arm with minutes, live
countdowns). Overlay: active session elapsed + soonest-ending buff (opt-in
widgets per spec section 3; default on). Fits 1280x800.

### Slice B deviations (adjudicated 2026-10-04)

- Widget opt-in. Decision: `config/local.json` `overlay.widgets`
  `{grindSession, grindBuff}` (only a literal false turns one off; default on);
  `app/main.js` reads it and passes it to the overlay as a `loadFile` query
  (`ewcore.overlayWidgets` / `widgetsQuery` / `widgetsFromQuery`). Alternatives:
  always-on rows (ignores spec section 3); a preload on the overlay (breaks the
  no-bridge overlay floor). Why: the only config path that keeps the overlay
  bridge-less. Touches `app/main.js` and `config/local.example.json`, outside
  the slice file list. Reverses if: an overlay settings UI lands.
- Spot reference. Decision: `start` and `log.spot` send the spot `id` (falling
  back to `name` when a spot has no id); sessions/active resolve `spot` through
  `spots[].id` for display and fall back to the raw value, so a server that
  stores names also renders. Alternatives: send names. Why: `spots` carries
  ids and sessions reference a spot. Reverses if: slice A keys by name - then
  `spotRef` in `grind.js` returns `s.name`.
- Buff list. GET returns every stored buff (armed or not, `ends: null` when
  unarmed or expired), so `ewcore.buffRows` lists ALL server buffs (armed
  soonest first, then the rest in server order, server ids kept) and appends
  only the `ewcore.BUFF_DEFAULTS` whose names (case-insensitive) are not
  already listed. `BUFF_DEFAULTS` names are exactly the server `SEED_BUFFS`
  (pinned by `app/test/grind.test.js`); buff minutes use
  `ewcore.BUFF_MINUTES` = 1-43200 like the server (sessions stay 1-1440).
  `validGrindBody` ids match the server: `delete` `^s[0-9]{1,9}$`,
  `clear_buff` `^[a-z0-9-]{1,40}$`. (Revised 2026-10-04 after the plan 005
  verifier: the first cut capped buffs at 1440, dropped unarmed server buffs
  and used client-only default names.)
- `app/test/progress.test.js` route check loosened from an exact list to
  membership (the exact list now lives in `grind.test.js`).

## Gates

pytest + node --test + leak sweep; verifier per merged plan, refute rounds
capped at 3; one push.
