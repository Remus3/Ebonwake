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

### Slice A as built (2026-10-04) - contract for slice B

- `GET /api/progress` -> `{character: {name|null, cls, level|null, gs: {ap, aap,
  dp} (each int|null)}, tracks: [{id, title, kind, steps: [{id, title, done,
  done_at|null}], done, total, pct}], profile: {data, freshness, status}}`.
  `profile.data` = `{family, region, guild|null, characters: [{name, cls|null,
  level|null, main}]}`; `status` = ok|stale|error|none; unconfigured ->
  `{data: null, freshness: null, status: "none"}`.
- POST bodies: `{"character": {name?, cls?, level?, gs?: {ap?, aap?, dp?}}}`
  (partial merge, `name: ""` clears), `{"step": {track, step, done}}`,
  `{"add_track": {title, kind, steps}}`, `{"remove_track": id}`; each returns
  the GET body.
- Config: `config/local.json` `profile: {family, base_url?}` (base must be
  https, else the default).

Deviations (self-adjudicated in lane, minor; decision / alternatives / why /
reverses if):
1. Upstream path verified by the main session against the project's
   openapi.json (servers[0] = `https://api.cutepap.us/community`; path
   `/v1/adventurer/search`, `query` exact match, `searchType=familyName`,
   `region` in EU|KR|NA|SA; probe answered 202 = being fetched):
   `GET <base>/adventurer/search?query=<family>&searchType=familyName&region=NA`,
   default base `https://api.cutepap.us/community/v1`, one call per hour (search only, no
   second profile call). Alt: block the slice on a docs fetch. Why: base is
   configurable and the path lives in one method (`ProfileClient.url`).
   Reverses if: the docs show another path - change that method only.
2. `pct` = floor(100 * done / total), 0 when total is 0, so 100 only when
   every step is done. Alt: round. Why: no premature 100; slice B's
   `trackPct` must floor too. Reverses if: operator prefers rounding.
3. Gear seed has 13 steps (main, sub, awakening, 4 armor, necklace, 2 rings,
   2 earrings, belt) instead of the spec's 11. Alt: spec list. Why: the game
   has a sub-weapon and 6 accessory slots. Reverses if: operator says so.
4. "Being fetched" (HTTP 202, or a JSON message mentioning fetch/later) =
   `httpcache.Pending`: flat 60 s retry, backoff n not grown. Alt: normal
   exponential backoff. Why: the upstream scrape finishes in about a minute.
5. `profileTarget` (opaque account-level id) is dropped before caching or
   serving; the cache file name is a hash, never the family name. Why: ToS /
   leak floor. Reverses if: never.
6. `/api/state` `sources.profile` reads cache + backoff only (`peek`), never
   fetches; `none` = unconfigured or nothing fetched yet.

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
