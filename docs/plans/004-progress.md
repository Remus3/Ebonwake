# Plan 004 - Progress tab

Status: open. Lanes: `build` (slice A), `data` (slice B).

## Goal

Track the operator's own progression: main-story / quest milestones, season
pass, level and gear score, the Tuvala -> PEN gear path, plus a family /
character profile card from BDO-REST-API (read-only, at most hourly).

## Data

- Operator-entered, Store domain `progress`: `{"character": {name?, cls:
  "Deadeye", level, gs: {ap, aap, dp}}, "tracks": [{id, title, kind:
  "quest"|"season"|"gear", steps: [{id, title, done_at?}]}]}`. Seed tracks:
  main story (chapters as steps), season pass (levels as coarse steps), Tuvala
  -> PEN gear path (weapon, awakening, armor x4, accessories x5 as steps).
- Profile card: BDO-REST-API (docs `https://man90es.github.io/BDO-REST-API/`),
  region NA, family name from gitignored `config/local.json`
  (`profile.family`, never tracked; a family name identifies a person). Base URL
  from config with the documented default; the implementer verifies the path
  shape against the docs at build time. Cache TTL 3600 s, backoff as plan 002
  (reuse its cache/backoff helper - factor it into `server/ew/httpcache.py`
  if 002 did not already). "Being fetched, retry later" = transient.

## Slice A - server (lane `build`)

Files: `server/ew/progress.py`, `server/ew/httpcache.py` (if needed),
`server/ew/app.py`, `tests/test_progress.py`.

1. `GET /api/progress` -> `{character, tracks: [{..., done, total, pct}],
   profile: {data, freshness}}`.
2. `POST /api/progress` (plan 002 POST guards): `{"character": {...}}`
   (validated ranges: level 1-70, ap/aap/dp 0-999), `{"step": {track, step,
   done: bool}}`, `{"add_track": {title, kind, steps: [titles]}}`,
   `{"remove_track": id}`.
3. Profile fetch through the injectable cached client; no profile configured ->
   `profile: null` with `status: "none"`.
4. `/api/state` `sources.profile`.

Acceptance: pytest green (validation edges, step toggles, pct math, profile
cache/backoff/none, POST guards).

## Slice B - dashboard (lane `data`)

Files: `app/dashboard/progress.js`, `dashboard.js`, `index.html`,
`app/shared/ewcore.js` (`trackPct`, `gsTotal(ap, aap, dp)` = (ap+aap)/2+dp),
`app/shared/ew.css`, `app/test/progress.test.js`.

Cards: Character (level, AP/AAP/DP, GS, editable inline), Profile card (with
freshness pill), one card per track (progress bar + scrollable step list,
click toggles), Add track. Fits 1280x800.

Acceptance: node tests green; self-test 7/7 fit.

Slice B notes (contract reading, no adjudication needed):
- `trackPct` recomputes from `steps[].done_at` (optimistic toggles); pct is
  rounded but capped at 99 until every step is done. The client shows its own
  pct, not the server's.
- Bridge guard ids (`track`, `step`, `remove_track`): `^[a-z0-9_-]{1,40}$`;
  `add_track.steps` 0-200 titles (UI requires >= 1). Character POST sends
  `{level, gs: {ap, aap, dp}}`; `name`/`cls` are accepted by the guard but not
  sent by the UI.
- No profile = `profile: null` OR `profile.status == "none"`; both render the
  "no family configured" card. Freshness uses the plan 002 shape
  `{fetched_at, age_s, ttl_s, stale, error}`, default TTL 3600 s.
- A POST answer that is not a full GET body triggers a re-read; GET 404 shows
  "progress API not on this server yet".
- Overlay unchanged (read-only GET; nothing in this slice's file list).
