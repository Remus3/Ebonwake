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
- Buff list. GET returns only armed buffs, so the Buffs card merges them with
  client defaults (`ewcore.BUFF_DEFAULTS`, the plan's buff kinds, minutes
  capped at 1440) by case-insensitive name. Session / buff ids are accepted as
  a short string or a whole number by `validGrindBody`.
- `app/test/progress.test.js` route check loosened from an exact list to
  membership (the exact list now lives in `grind.test.js`).

## Gates

pytest + node --test + leak sweep; verifier per merged plan, refute rounds
capped at 3; one push.
