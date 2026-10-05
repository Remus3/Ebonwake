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

## As-built deviations

Self-adjudicated in the build lane (operator standing order 6); each is
decision / alternatives / why / reverses if.

1. Home does not fetch `/api/progress`. Alternatives: fetch it and use the
   profile level as a fallback. Why: no listed card needs it (level and ETA
   come from `/api/leveling`); `composeNow` accepts and ignores the key, so a
   later card costs no API change. Reverses if: a progress-sourced card
   (e.g. season pass) is added to Home.
2. "Dailies left" lists `daily` items only. Alternatives: daily + weekly in
   one card. Why: plan text says dailies; weeklies stay on Today, keeping the
   card short enough for 1264x761. Reverses if: the operator asks for
   weeklies on Home.
3. Grind session silver/h is the spot's logged average (`spots[].silver_per_h`).
   Alternatives: none - a running session has no silver until it is logged.
   Reverses if: plan 005 gains live loot entry during a session.
4. M8: the Today add form drops the `event` kind and its `until (event)`
   field; the server still accepts `event` adds and existing `event` items
   stay listed and tickable. The Events card adds Events-tab items ending at
   or before the next weekly reset (Thursday 00:00 UTC), read-only, via the
   new pure `C.eventsThisWeek`. Alternatives: keep the kind in the form with
   a hint. Why: a form that offers `event` is exactly the "second copy"
   invitation M8 names. Reverses if: the Today event kind is retired server
   side (then drop the legacy rows too) or the operator wants the form back.
5. Missing payloads: a 404 drops the card silently (System tab already flags
   an old server); other fetch errors keep the last data and show one error
   line on the Resets card. Alternatives: an error card per source. Why: one
   glance screen, no noise. Reverses if: QA finds stale data unnoticed.
6. Fit at 1264x761 is asserted in `app/test/selftest.test.js` with fake
   windows at that size; the real Electron self-test runs at merge (the
   lane has no display run). Home is 8 cards in the existing auto-fill grid
   (4 x 2 at that width), lists scroll inside cards. Reverses if: the merge
   self-test reports `fits: false` for Home.
7. "Default tab" means Home is first and opens when no tab is remembered;
   a saved `ew.tab` (localStorage) still restores the operator's last tab.
   Alternatives: force Home on every launch. Why: restoring the last tab is
   existing plan 001 behaviour the operator relies on. Reverses if: the
   operator wants Home on every launch (drop the saved-tab branch).
