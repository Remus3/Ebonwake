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

### Slice A as built (decisions where the contract was silent)

- Store doc gains `"updated": "<iso>"` (last write) so `sources.today.updated`
  is the time of the last change, not a guess. Alternatives: max tick time
  (misses add/remove/move), server start time (meaningless). Reverses if: a
  second writer of the domain appears that cannot maintain it.
- `until` is optional on every kind; expiry is `UTC date > until` (the until
  day itself is still shown). An event without `until` never expires.
  Reverses if: slice B needs `until` mandatory for events.
- `move.to` is a 0-based index into the full list, clamped to the end;
  negative / non-int is 400. Orders are renumbered 0..n-1 on every write.
- Limits: title 1..80 chars after strip, no control chars; at most 200 items;
  unknown fields in `add` are 400. Seeded once (only when `items` is absent),
  so an emptied list stays empty.
- Corrupt store doc degrades (bad items / ticks dropped on read), never 500.
- POST guard is shared: `Handler.POST_ROUTES` maps path -> handler taking the
  parsed JSON object; ValueError -> 400. Market and today both use it.

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

## Gates

pytest + node --test + leak sweep; verifier per lane, refute rounds capped at
3; one merge batch, one push.
