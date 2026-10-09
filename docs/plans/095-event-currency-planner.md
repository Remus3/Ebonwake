# Plan 095 - Event currency planner: guaranteed currency by event end vs an exchange wishlist, shortfall and buy-by warnings

Status: open. Deep dive 2026-10-09 (research 0017 section 2 F2, 6).
Lane hint: `build`.

Spec gap: BDO pays most seasonal events in a currency spent at an exchange
with per-family limits. The live NA event "[EP. 1] Evil Stirs at Marni's
Playground" (10673, read 2026-10-09, to 11-05 before maintenance) pays
Seals of Purification: 20 for a daily login + 20 for 60 minutes played,
three once-per-family entry quests (10 each), two weekly games (40 each,
weekly reset Thursday 00:00 UTC), random field drops; a 29-row exchange
(80 seals for Valks +250 x1, ...), two of its buffs removed at the 11-12
maintenance. EW imports the event window (064), counts login days (075),
knows weekly resets (021) and claim deadlines (086), but nothing totals what
the operator is sure to earn by the end against what they want to buy, so
a short-by-the-end wishlist is noticed only at the exchange.

1. Currency rule data (`server/ew/data/event_currency.json`, tracked,
   sourced, `verified: false` until an official page matches): per event
   number `{currency, ends, sources: [{id, kind: daily_login | daily_minutes
   | weekly | once | drop, amount, min_minutes?, reset_rule?, window?}],
   exchange: [{item, cost, limit, removed_at?}]}`. Seeded with event 10673
   from its Detail page text (URL + read date per row). A suggestion path:
   plan 064's notice parser offers a rule when a Detail page carries
   "<N> <currency>" source lines and a cost table (suggest-only, never
   auto-committed; the 064 undo applies).
2. Earn projection (`server/ew/eventcurrency.py`, pure): `earned_so_far`
   from 075 `logindays` (a date counts for `daily_login` when minutes >= 1,
   for `daily_minutes` when minutes >= `min_minutes`), weekly rows ticked
   in the Today checklist (plan 021 Thursday 00:00 UTC rule, plan 068
   auto-tick never guesses a weekly game - operator tick only), once rows
   ticked once; `guaranteed_by_end` = earned + remaining days x daily
   amounts + remaining weekly windows x weekly amounts + unticked once rows.
   Drops are never projected (unknown rate); an optional operator-typed
   "balance now" (plan 079 override, expires at event end) replaces
   `earned_so_far` when set.
3. Wishlist (store domain `event_wishlist`): `{event, item, qty}` within
   `limit`; `cost_total`; `shortfall = max(0, cost_total -
   guaranteed_by_end)` shown as "N short - from drops"; a wishlist item with
   `removed_at` before the event end gets "use before <date>" (plan 074
   digest line at T-24 h).
4. Surfaces: Events tab row per currency event: "Seals: 120 earned, 640
   guaranteed by 11-05, wishlist 590 - covered" (or "N short"); weekly
   game rows appear in the Today checklist for the event window only;
   What now (069) suggests "Play 60 min today (+20 seals)" only when the
   day's daily_minutes row is unmet and the wishlist is short. GET
   `/api/events/currency` -> `{events: [{event, currency, earned,
   guaranteed, wishlist_cost, shortfall, ends}]}`.
5. Tests: `tests/test_eventcurrency.py` - schema validation of the seed;
   projection on a fixed clock (mid-event, last day, after end -> hidden);
   daily_minutes counts only >= 60-minute dates; weekly windows across a
   Thursday 00:00 UTC boundary; once rows counted once; drops never
   projected; balance override replaces earned and expires at the end;
   limit enforced on wishlist qty; removed_at warning; no network.
   `node --test` for the Events row text.

Acceptance: with the seeded 10673 rule, a fixture of 3 logged days (one
under 60 min), one weekly game ticked and a 2-item wishlist, the Events
row shows the hand-computed earned / guaranteed / shortfall; the weekly
game rows show only inside the event window; gates green; verifier PASS
within 3 rounds; one push.

ToS check: inputs are the robots-allowed official event Detail page plan
064 already GETs (no new host, no new GET), the plan 075 logged-in minutes
from the session log, and operator ticks / typed wishlist rows. No memory
read, no client file, no packet, no input to the game window, no
authenticated call; nothing is redeemed or bought by EW.

Depends on: 064, 075, 021.
