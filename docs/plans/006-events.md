# Plan 006 - Events tab

Status: done (2026-10-04); refute rounds 1/3 PASS. Lanes: `build` (slice A), `data` (slice B).

## Goal

Track coupon codes (code, rewards, expiry, redeemed?), in-game events and
Twitch-drop campaigns with deadlines, with live countdowns and an "ending soon"
overlay row. All operator input; nothing is read from the game.

## Adjudicated before the lanes (2026-10-04)

- Seeding "from official pages". Decision: the server ships NO pre-filled codes
  or events; the seed is a fixed `SOURCES` list of official page links
  (BDO NA/EU news, Twitch drops campaigns) shown in a Sources card, and the
  operator types entries. Alternatives: scrape the official news pages (HTML
  that changes without notice, an unauthenticated crawl of the publisher's site,
  and codes copied today are wrong tomorrow); ship hand-copied codes (stale and
  unverifiable at commit time). Why: correctness over convenience; a stale code
  in the tracker is worse than none. Reverses if: the publisher exposes a
  stable machine-readable events/coupon feed.

## Slice A - server (lane `build`)

Files: `server/ew/events.py`, `server/ew/app.py` (GET + POST_ROUTES + state
source), `tests/test_events.py`.

1. Store domain `events`: `{"items": [{id, kind, title, code, rewards, starts,
   ends, url, done}], "next_id": int}`. `kind` in `coupon|event|drop`. Ids are
   `e<N>` from the never-reused counter. Seed: empty items.
2. `SOURCES` constant: list of `{name, url}` (official BDO NA/EU news page,
   Twitch drops campaigns page). https only.
3. `GET /api/events` -> `{now, sources, counts: {coupon, event, drop} (open,
   i.e. not done and not expired), items}`; each item adds `left_s` (seconds to
   `ends`, null when no `ends`), `status` (`done` if done, else `expired` if
   ends <= now, else `upcoming` if starts > now, else `active`) and `soon`
   (not done, not expired, left_s <= 172800). Order: open items by ends
   ascending (no `ends` last), then done, then expired (newest ends first).
4. `POST /api/events`, exactly one op per body:
   `{"add": {kind, title, code?, rewards?, starts?, ends?, url?}}` -> item;
   `{"edit": {id, ...any of title/code/rewards/starts/ends/url}}` (null clears
   an optional field); `{"done": {id, done: bool}}`; `{"delete": id}`;
   `{"purge_expired": true}` (removes expired items, done or not; returns the
   count). Limits: title 1-80 chars; code only for coupons, required for
   coupons, `^[A-Za-z0-9-]{4,40}$`, stored uppercase, duplicate code refused
   (case-insensitive); rewards 0-200 chars; url `https://` only, <= 300 chars;
   starts/ends ISO 8601 with offset or `Z`, or `YYYY-MM-DD` (end of that day
   23:59:59 UTC for `ends`, 00:00 UTC for `starts`), stored as UTC ISO;
   ends within 2 years of now, starts <= ends. Max 300 items. Unknown id ->
   ValueError (400). Same validation/guard style as `grind.py`.
5. `/api/state` `sources.events` (count of open items, soonest `ends`).
6. Thread-safe like `GrindService` (lock around read-modify-write).

## Slice B - dashboard + overlay (lane `data`)

Files: `app/dashboard/events.js`, `dashboard.js`, `index.html`,
`app/overlay/overlay.js`, `app/shared/ewcore.js` (`fmtLeft`, `eventRows`,
`validEventsBody` + allowlist route `/api/events`), `app/shared/ew.css`,
`app/test/events.test.js`, `config/local.example.json` (widget key).

Cards: Add (kind select; code field only for coupons; title, rewards, ends via
`datetime-local` converted from local time to UTC ISO, optional url), Coupons
(code in mono, copy button via `navigator.clipboard`, redeemed toggle, expiry
countdown, delete with two-click confirm), Events and drops (countdown,
`soon` highlighted, done toggle), Sources (official links as plain text +
copy; no navigation inside the dashboard), and a "Purge expired" button with
two-click confirm. Overlay: widget `eventsSoon` (default on, only a literal
false turns it off, same mechanism as plan 005) shows the soonest `soon`
item: title + left. Fits 1280x800; lists scroll inside their card.

## Acceptance

- pytest, node --test and leak sweep green; ASCII + LF.
- GET/POST contract above holds (tests cover every op, every limit, status and
  order rules, and the clock-injected countdown).
- Dashboard self-test: 7/7 tabs fit; Events tab renders with an empty store.
- Verifier PASS within 3 refute rounds; one push; CI green.

## Gates

pytest + node --test + leak sweep; verifier per merged plan, refute rounds
capped at 3; one push.

### Slice A deviations (adjudicated in-lane, 2026-10-04)

- Write responses. Decision: every POST op returns the full GET body (as
  `grind.py` does); `add` also carries `item`, `purge_expired` also carries
  `purged` (the count). Alternatives: return the bare item / bare count. Why: the
  dashboard re-renders from one shape after any op. Reverses if: slice B needs a
  bare item.
- Time window. Decision: "within 2 years of now" is read as |t - now| <= 730
  days, applied to both `starts` and `ends`; past dates inside the window are
  accepted (an item can be logged after it ended). Alternatives: ends must be in
  the future; window on ends only. Why: back-filling is harmless, and an
  unbounded `starts` would let starts <= ends be satisfied by nonsense. Reverses
  if: the operator wants past `ends` refused.
- Date/time grammar. Decision: `YYYY-MM-DD` or
  `YYYY-MM-DDTHH:MM[:SS[.ffffff]]` plus `Z` or `+HH:MM`, regex-gated before
  `fromisoformat` (which in 3.11 accepts many more shapes). Second fractions are
  dropped on store. Alternatives: raw `fromisoformat`. Why: one grammar the JS
  side can mirror. Reverses if: slice B must send another shape.
- `left_s` clamps at 0 once expired (status says `expired`). Alternatives:
  negative seconds. Why: a countdown never shows negative time. Reverses if:
  slice B wants "expired N ago".
- `kind` is fixed at add; `edit` refuses `kind` and `done` keys (done has its own
  op). Changing kind = delete + re-add. Why: a kind change would re-trigger the
  coupon-code rules mid-edit. Reverses if: the dashboard needs a kind switch.
- `edit` with only `id` is a valid no-op; `code: null` on a coupon is refused
  (code is required for coupons); any non-null `code` on event/drop is refused.
- Empty or whitespace `rewards` is stored as null. Title/rewards refuse control
  characters (as grind names do). URL must be ASCII, `https://` + a non-empty
  host, no whitespace, <= 300 chars.
- Order ties broken by id number; done items sorted like open items (ends asc,
  no `ends` last). An item that is done and past its `ends` has status `done`
  (done wins) but is still removed by `purge_expired`.
- `soon` applies to `upcoming` items too (not done, not expired, left_s <= 48 h).
- `/api/state` `sources.events` = `{updated, status: "ok", open, soonest}`
  (`soonest` = earliest `ends` among open items, or null).
- SOURCES = NA/EU news page, NA/EU notice board, Twitch drops campaigns; all
  https.

### Slice B deviations (adjudicated in-lane, 2026-10-04)

1. Extra pure helpers. Decision: besides `fmtLeft` / `eventRows` /
   `validEventsBody`, ewcore exports `soonestEvent` (overlay), `localToUtcIso`
   (datetime-local -> `YYYY-MM-DDTHH:MM:SSZ`, no millis) and `parseEventForm`.
   Alternatives: inline the logic in events.js / overlay.js (untestable without
   a DOM). Why: mirrors `soonestBuff` / `parseGrindForm`; node --test covers it.
   Reverses if: ewcore is split per tab.
2. Countdown source. Decision: `eventRows` recomputes `left_s`, `status`,
   `soon` and the order locally from `ends` (date-only `ends` = 23:59:59 UTC,
   date-only `starts` = 00:00 UTC), falling back to server `left_s` minus time
   since fetch when `ends` does not parse. Alternatives: show server values
   until the next 60 s poll. Why: an item flips to expired / soon on the second,
   same as the plan 005 buff clocks. Reverses if: the server and client rules
   are found to disagree (then the server value wins).
3. IPC guard strictness. Decision: `validEventsBody` checks shapes and every
   limit the client can know (kind, title 1-80, coupon code regex and
   coupon-only on add, rewards <= 200, https url <= 300, date formats, starts <=
   ends). On `add` optional fields must be absent (not null); on `edit` null
   clears rewards/starts/ends/url but never `code` or `title`. The 2-year
   window, duplicate codes and code-on-non-coupon edits are left to the server
   (it knows the stored kind and the clock). Alternatives: mirror every server
   rule (needs server state). Why: the main-process guard must not need
   server data. Reverses if: slice A accepts null on add (then relax add).
4. UI placement. Decision: "Purge expired" (two-click) sits in the Add card;
   events/drops rows also get the two-click delete; the Add form has no
   `starts` field (the plan's card lists none; the API still takes it).
   Every successful POST re-polls GET (op replies are an item or a count; the
   purge count is shown when the reply carries `purged` or `count`).
   Alternatives: purge in the Coupons header. Why: one action area, keeps rows
   compact at 1280x800. Reverses if: operator QA asks otherwise.
5. Upcoming items may be `soon`. Decision: follows the server rule literally
   (not done, not expired, left_s <= 48 h), so an upcoming item ending within
   48 h is highlighted and can lead the overlay. Why: the rule in slice A says
   so. Reverses if: slice A excludes upcoming from soon.
6. grind.test.js exact-equality assertions on `overlayWidgets` and
   `POST_ROUTES` were widened for the new `eventsSoon` key and `/api/events`
   route (same intent, new members). Overlay row label is the item title with
   ellipsis; label `Ending soon` + `none` when nothing is soon.
