# Plan 025 - Home / Now tab: one glance screen for resets, dailies left, buffs, session, ETA, alerts

Status: open. Deep dive + UX audit 2026-10-05 session 4 (research 0003-0005). Lane hint: `build`.

Spec gap: research 0005 candidate F1 (value 5, effort M) and M8 (events
duplicated between Today and Events). Spec goal 1 ("what should I do right
now") is spread across Today, Grind, Progress and Events; the operator
alt-tabs and clicks a tab for each.

1. New dashboard module `app/dashboard/home.js`, tab id `home`, first and
   default tab (`/api/state` tab list in `server/ew/app.py` gains `home`
   at index 0; existing tab ids unchanged).
2. Pure composer `composeNow(snapshots, nowMs)` in `app/shared/ewcore.js`
   taking the existing GET payloads (`/api/today`, `/api/grind`,
   `/api/leveling`, `/api/progress`, `/api/events`, `/api/market/watch`)
   and returning ordered cards: next resets (daily / weekly, plus any plan
   021 custom rule), dailies left BY NAME, active buffs with countdowns,
   running grind session with silver/h, level ETA + next Hot Time, market
   alert hits, events and coupons ending within 48 h, new coupon
   suggestions (plan 014). Missing payloads (404 on an old server) drop
   their card, never the screen.
3. Read-only: Home writes nothing except one-click ticks of dailies, which
   reuse the existing `POST /api/today` tick route.
4. M8 fix: the Today tab's own `event` kind add path stays for existing
   items, but its Events card shows Events-tab items ending this week
   (read-only) instead of inviting a second copy.
5. Tests: `app/test/home.test.js` - composer ordering, empty states, a
   missing payload, 48 h window edges; `app/test/selftest.test.js` tab
   count updated (8 tabs) and the self-test fits 1264x761.

Acceptance: node tests green; self-test paints Home first with 8 tabs at
merge; no new server route; gates green; verifier PASS within 3 rounds.

ToS check: composition of existing local GETs; no game input.

Depends on: none.
