# Plan 003 - Today tab

Status: open. Lanes: `build` (slice A), `data` (slice B).

## Goal

A daily + weekly checklist that resets itself on the NA clocks, login-event
claims with end dates, and the Black Spirit's Adventure dice, plus a reset
countdown on the overlay. All data is operator ticks + a small seed; nothing is
read from the game.

## Rules

- NA daily reset 00:00 UTC; weekly reset Thursday 00:00 UTC (spec section 3,
  `ewcore.nextDailyReset/nextWeeklyReset`). Server and client use the same rule.
- A tick is a timestamp, never a boolean. Item done = `ticked_at >= last reset
  of its kind`. No cron, no rollover job: reset is derived, so a server that was
  down over a reset is still correct.
- Kinds: `daily`, `weekly`, `event` (one-shot per reset window like daily, but
  with `until` = ISO date after which it is hidden/archived).

## Slice A - server (lane `build`)

Files: `server/ew/today.py` (new), `server/ew/app.py` (routes), `tests/test_today.py`.

1. `last_daily_reset(now)`, `last_weekly_reset(now)` (Thursday 00:00 UTC), pure,
   tested on boundaries (exactly 00:00:00, one second before, Thursday edge,
   year/month rollover).
2. Store domain `today`: `{"items": [{id, title, kind, until?, order}],
   "ticks": {"<id>": "<iso>"}}`. Seed when empty: daily = attendance reward,
   Black Spirit's Adventure dice, daily Challenges (Y), barter run, guild
   mission; weekly = Black Spirit weekly quests, weekly boss rewards, Pearl Shop
   weekly free item. `id` = slug, `^[a-z0-9-]{1,40}$`.
3. `GET /api/today` -> `{now, daily_reset, weekly_reset (next, iso), items:
   [{id, title, kind, until, done, ticked_at}]}`; expired events excluded.
4. `POST /api/today` JSON, same guards as plan 002 POST (loopback Host,
   application/json, <= 4 KiB): `{"tick": id}`, `{"untick": id}`,
   `{"add": {title, kind, until?}}` (id derived from title, de-duplicated),
   `{"remove": id}`, `{"move": {id, to}}`. Returns the GET body.
5. `/api/state` `sources.today = {updated, status: "ok"}`.

Acceptance: pytest green: reset boundaries, derived done-state across a reset,
event expiry, add/remove/move/validation, POST guards.

## Slice B - dashboard + overlay (lane `data`)

Files: `app/dashboard/today.js` (new), `app/dashboard/dashboard.js` (mount),
`app/dashboard/index.html`, `app/overlay/overlay.js`, `app/overlay/index.html`,
`app/shared/ewcore.js` (pure helpers), `app/shared/ew.css`,
`app/test/today.test.js` (new).

1. Pure helpers: `lastDailyReset(now)`, `lastWeeklyReset(now)`,
   `isDone(tickIso, kind, now)`, `groupItems(items)` -> `{daily, weekly, event}`
   with done counts.
2. Today tab cards: Daily (checkbox list, `n/m done`, countdown), Weekly (same),
   Events (title + days left), Add item (title, kind, until). Click toggles via
   POST; optimistic update, reverted on error. Lists scroll inside cards; the
   tab still fits 1280x800.
3. Countdowns tick every second locally; when one hits zero the list re-derives
   done-state without a reload.
4. Overlay: next reset line plus `daily n/m` from `/api/today`, refreshed every
   60 s and on SSE heartbeat; offline -> last value muted.

Acceptance: `npm test --prefix app` green; self-test 7/7 fit.

### Slice B implementation notes (decisions)

- Bridge generalised. Decision: `window.ewMarket.watch` is replaced by
  `window.ewApi.post(route, body)` over one IPC channel `ew:post`; main checks
  sender = dashboard and `ewcore.validPost(route, body)` (fixed allowlist
  `/api/market/watch` -> `validWatchBody`, `/api/today` -> `validTodayBody`);
  market.js migrated. Alternatives: keep `ewMarket` beside a second channel
  (two surfaces to guard). Why: one guarded channel, one allowlist. Reverses
  if: a route needs a non-JSON or streamed body.
- `move.to` is taken as a whole-number index >= 0 (the contract does not type
  it); the client validator accepts it but the tab has no move control yet.
  Reverses if: slice A defines `to` differently - update `validTodayBody`.
- Client-side limits slice A may be looser than: title 1-80 chars, no control
  characters; the add form requires `until` for `event` and drops it for
  daily/weekly. Remove is a two-click confirm (3 s window).
- Overlay shows `daily n/m  weekly n/m`, re-derived locally each second;
  heartbeat refreshes are throttled to one per 10 s (heartbeat is 15 s today).
  Offline or 404 -> last value muted (`offline` before any data).

## Gates

pytest + node --test + leak sweep; verifier per lane, refute rounds capped at
3; one merge batch, one push.
