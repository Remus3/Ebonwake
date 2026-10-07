# 0004 - Deep dive: items and economy (2026-10-05, research only)

Scope: enhancement, loot, inventory/storage, economy and community data
sources for a NA Steam Deadeye, read-only and unauthenticated. Every page was
read with a single GET (or a search-engine preview) on 2026-10-05; robots.txt
was read first for every host fetched directly (section 6). Nothing here was
built; candidates are in section 7.

Tags: "verified <source> <date>" = read on that page, page date given where
the page has one. "unverified" = search preview, fan site without a date, or
recollection not re-read today. Numbers from community guides are community
data, not publisher data, even when "verified" (it means "the page says so").

## 1. Enhancement

### 1.1 Failstacks (FS) and caps

- Every unsafe enhancement keeps at least a 10 % failure chance (90 % hard
  cap). Soft caps: armour/weapons ~70 %, Gold/Blue accessories PRI 70 %
  (18 FS), DUO 50 % (40), TRI 40 % (44), TET 30 % (110). Verified
  grumpygreen.cricket/bdo-failstack-chart (page updated 2024-09-15; old, the
  shape still matches 2026 guides).
- Blackstar weapon FS bands (search preview of the same chart family):
  +8/+9 10-14, +10 15-25, +11 25-30, +12 30-35, +13 40-50, +14/+15 50-60,
  PRI 30-40, DUO 40-60, TRI 60-85, TET 110-150, PEN 200+ "use crons".
  Unverified (preview only).
- Post-graduation rate tables, verified https://bdogearguide.com/ (updated
  2026-07-30):

  | Gear | Step | Base % | Soft-cap FS | Max % at soft cap |
  |---|---|---|---|---|
  | Kharazad acc. | +0 -> PRI | 16.30 | 40 | 72.37 |
  | Kharazad acc. | TRI -> TET | 2.89 | 130 | 40.46 |
  | Kharazad acc. | TET -> PEN | 1.91 | 170 | 34.38 |
  | Kharazad acc. | OCT -> NOV | 0.32 | 280 | 9.28 |
  | Sovereign wpn | +0 -> PRI | 8.55 | 60 | 59.85 |
  | Sovereign wpn | TRI -> TET | 0.91 | 100 | 10.01 |
  | Sovereign wpn | TET -> PEN | 0.47 | 150 | 7.50 |
  | Sovereign wpn | NOV -> DEC | 0.02 | 300 | 0.75 |
  | Edana defense | PRI / TET / PEN / NOV | n/a | 120 / 210 / 240 / 320 | 68.90 / 24.20 / 14.87 / 2.32 |

  Pattern (unverified as a formula): chance = base * (1 + 0.1 * FS) up to the
  soft cap, then +base*0.02 per FS beyond it. The table values fit the first
  part (16.30 * 4.0 ~ 65, not 72.37, so the curve is NOT exactly linear for
  Kharazad PRI) - an EW calculator must use per-step tables, never a formula.
- Blackstar TET -> PEN: 3.20 % at 150 FS, 3.40 % at 160 FS. Unverified
  (search preview).

### 1.2 Pity: Agris Essence (Ancient Anvil)

Verified https://www.blackdesertfoundry.com/agris-essence-enhancing-guide/
(updated 2025-12-02). Each failure (also with crons or J's Hammer) adds one
stack for that gear category + level; reaching the threshold guarantees the
next attempt without consuming FS; any success clears the stacks.
Thresholds: Sovereign 3 / 5 / 10 / 20 / 30 / 35 (PRI..HEX); Blackstar
TRI->TET 12, TET->PEN 20; Kharazad 3 / 5 / 7 / 8 / 10 (PRI..PEN).

### 1.3 Cron stones

- Cron Stone item id 16080 (verified arsha util/db 2026-10-05). Crons stop
  the downgrade on failure.
- Crons per attempt (bdogearguide 2026-07-30, plus search preview of
  garmoth.com/cron-stones-cost, which answered 403 to the fetch):
  Sovereign PRI->DUO 320, ->TRI 560, ->TET 780, ->PEN 970, ->HEX 1,350,
  ->SEP 1,550, ->OCT 2,250, ->NOV 2,760, ->DEC 3,920. Kharazad PRI 120, DUO
  280, TRI 540, TET 840, PEN 1,090, HEX 1,480, SEP 1,880, OCT 2,850, NOV
  3,650. Blackstar shoes DUO 190, TRI 570, TET 980. Kharazad/Sovereign
  ranges verified (bdogearguide); per-step lists unverified (preview).
- Expected crons to TET: Kharazad 1,335, Sovereign 7,792, Edana 3,884.
  Unverified (search preview of bdogearguide; not on the fetched summary).
- Cron price in silver (vendor) not re-checked today: unverified.

### 1.4 Advice of Valks, Valks' Cry

- Advice of Valks stores a FS value as an item usable by any family
  character; Blacksmith's Secret Book saves your current stack. Valks' Cry /
  Fairy's Blessing: up to 10 combined, +1 FS each. Unverified
  (grumpygreen/altarofgaming via preview).
- 2026 sources of large Advices: BEYONDTHEJOURNEY coupon (+450/+350/+300/
  +250 sets) and Emma Bartali's Journal (+180 x2, +210 x2). Verified
  bdogearguide 2026-07-30. Coupon validity today unverified (EW rule:
  official coupon list only, research 0001 s6).
- Kharazad uses Essence of Dawn per attempt (1 at PRI .. 15 at NOV; DEC uses
  a Dawn Black Stone) and does not shatter. Unverified (preview). Essence of
  Dawn id 820979, last sold 100,000,000 = its priceMax (pinned at cap),
  stock 16,190 - verified arsha 2026-10-05.
- Progression order advice: armour (weekly-gated) -> accessories -> weapons
  last (most cron-heavy). Verified bdogearguide 2026-07-30.

### 1.5 Caphras

Verified https://www.naeu.playblackdesert.com/en-US/Wiki?wikiNo=146
(official, read 2026-10-05): boss-grade and green-grade gear at TRI+, 20
levels, only up to level 10 needed for the exchange to better gear;
extraction keeps 10 % (success) / 50 % (failure), full extraction ~95 % at
100,000 silver per stone. PEN boss main-hand: C1-C10 = 8,895 stones,
C11-C20 = 29,403 more (verified preview of mmosumo + official ranges 297 ..
1,480 per level at PEN). Caphras Stone id 721003, ~0.84 M last sold
(verified arsha 2026-10-05). Blackstar/Kharazad/Sovereign caphras: not
found; unverified whether they can be caphras'd at all.

## 2. Loot

- Item Drop Rate cap 300 %; Arsha server (+50), node investment (+10),
  castle (+50), Blessing of the Thriving Earth (+20) bypass it up to a 400 %
  hard cap. Verified BDFoundry item-drop-rate-buff-list (updated
  2024-11-27). Buff list (same page): Item Collection scrolls +100 % rate
  (Advanced also +100 % amount; normal +50 % amount, per grumpygreen
  preview), Supreme Old Moon +50 %, Glorious Battlefield +100 %, Tent
  Adventurer's Luck +10..50 % (10..50 M silver), Kamasylve +20 %, Girin's
  Tear +10 %, ecology knowledge up to +30 % (+20 % per other guides;
  conflict, unverified), Luck up to +12.5 %, Family Fame 7,000+ +10 %, night
  +10 %, guild +2 / +10 %, field boss +10 % 5 h, Old Moon Treaty token +5 %.
- Blessing of Agris Scroll (+50 % drop rate BEYOND the cap, to 500 %), 1 B
  silver, 10/week, 60 min, sale until 2026-10-22 maintenance, unused items
  removed 2026-11-05. Verified official Asia notice boardNo=19844
  (2026-09-23) and NA patch notes groupContentNo=10621 (research 0002 s3).
- Agris Fever: +100 % junk-loot amount, +150 % after the first five
  Margahan chapters (grumpygreen/altarofgaming preview, unverified); BDFoundry
  calls it +50 % Item Drop AMOUNT, separate from drop rate (verified page,
  2024-11-27). The two pages disagree; an EW model must keep "rate" and
  "amount" as separate inputs and let the operator type the value.
- Ecology knowledge drop buff does not apply to junk loot or party drops.
  Unverified (grumpygreen preview).
- Boss loot, world/field boss, summon scrolls, Erethea's Limbo, Dark Rift,
  Pit of the Undying ignore drop buffs. Verified BDFoundry 2024-11-27.
- Grind spot silver/h: grumpygreen interactive table, ~70 spots, "millions
  per hour with blue loot scroll", assumes everything sold on the market
  incl. estimated values for unsellables; page updated 2024-08-24 (stale:
  predates Edania and the 2026-10 XP revamp). Top: Tungrad Ruins 1,137 M
  (320 AP / 410 DP), Dehkia Tunkuta 2P 1,134 M, Thornwood Dehkia 1,104 M.
  Verified page. Live community loot data lives in garmoth grind-tracker and
  bdoloottracker / bdogt (session submissions); none offers an open API
  (section 6).
- Hermesia Inner Castle lost top-tier status 2026-09-23 (fewer Primordial
  Pigments / Refined Essences, more Origin shards). Verified Asia notice
  19844; NA equivalent in research 0002 s2.
- Treasure items: Treasure Codex (F5 / Adventure menu) since 2026-09-23
  shows each treasure's materials, source zones and first-craft date.
  Verified Asia notice 19844 + research 0002. Ancient Relic Crystal Shard:
  five shards arranged in a + shape (fandom, unverified).
- Marni's Stone: Lv 57+, fills from last-hit kills of listed monsters, turn
  in to Wacky Toshi (Sand Grain Bazaar, Ibellab, Valencia, Shakatu) for a
  level-scaled combat XP amount. Unverified (dulfy 2018 / Steam guide); the
  2026-10 XP revamp (research 0002 s1) may change its value.

## 3. Inventory and storage

- Value Pack: +16 inventory, +16 storage, +30 % combat/skill/life/mount XP,
  distant node investment, +200 LT, +30 % silver on market collect (not
  pearl items), unlimited Merv's Palette / beauty salon, +50 barter refresh,
  -10 % barter parley. Unverified (fandom preview; official notice
  groupContentNo=422 not re-read).
- Weight: strength training up to +40 LT, Weight Training skill up to +150
  LT, pearl weight +50..+250 LT, belts (Basilisk +80), Root Treant jewelry
  set +100, life alchemy stones +15..+120, crystals +20..+50; observed max
  ~3,500 LT. Unverified (fan guides, undated).
- Maids move up to 100 LT per use between inventory and town storage (and
  silver). Unverified (BDFoundry preview).
- Warehouse (market): 5,000 VT base, +2,000 VT with Family Fame; single
  transfer limit 200 VT. Verified official wiki wikiNo=47 (updated
  2025-08-06).
- Town storage slots per town, Family inventory size: not re-checked today
  (unverified; operator-typed data in any feature).

## 4. Economy

### 4.1 Central Market rules (verified official wiki wikiNo=47, updated 2025-08-06)

- Tax 35 % (30 % market + 5 % territory): seller collects 65 %. Value Pack
  adds 30 % of that at collect: 84.5 %. Pearl Shop items are 30 % tax-exempt
  (not event/box pearl items).
- Family Fame raises collected silver "depending on fame"; the step values
  (commonly quoted +0.5 / +1.0 / +1.5 % at 1,000 / 4,000 / 7,000 fame) are
  unverified.
- Listings wait 15 minutes before they are buyable (registration queue).
  Items >= 20 B silver: one listing per item name + enhancement at a time,
  and pre-orders at the same max price are matched at random; below that,
  pre-orders fill first-come by highest price. One pearl-item pre-order per
  player. Cancelled pre-orders refund fully. A failed listing returns to the
  warehouse after ~1 minute.
- Market dynamics observed today (verified arsha 2026-10-05): Essence of
  Dawn last sold at its priceMax (100 M) - a cap-pinned item where only
  pre-orders at max get fills; Memory Fragment stock 0 (2.22 M last sold);
  Black Stone 147 k base, 43,764 in stock; Caphras 885 k base. "Stock 0" and
  "last sold == priceMax" are the machine-readable signals of a pre-order
  queue.

### 4.2 Lifeskill / delivery / barter

- Imperial crafting delivery: daily boxes = contribution points / 2,
  separately for cooking and alchemy; NPC pays 250 % of the box's market
  price, no tax, mastery adds more; resets at midnight server time;
  professional 1+ to package. Verified official wiki wikiNo=133 (updated
  2026-07-17). Territory/server caps were removed (official update history
  groupMasterNo=2095, 2021). "50 M+/day for a newer player" - unverified
  (fan guide).
- Barter: Margoria routes pay crow coins (175-325 per barter at some
  wrecks) or level-5 goods (~10-15 M). Global Lab 2026-04-10 standardised
  crow-coin parley to 21,650 (from 29,430-58,180). Unverified (search
  preview of BDFoundry / Orbit Games Global Lab pages); ship QoL 2026-09-23
  verified (Asia notice 19844).
- Workers / nodes: optimisers exist (bdoworker.com, Thell/bdo-empire on
  GitHub, no licence; xanthics/bdo_node_manager MIT but archived 2020);
  node map lineage from somethinglovely.net (unreachable today).
- Silver/h comparisons beyond grumpygreen's 2024 table: only fan articles
  ("2026" titles, AP brackets, e.g. Swamp Fogan 270 AP, Dehkia Crescent 310
  AP) - unverified.

## 5. arsha.io v2 - full surface (verified source + live probe 2026-10-05)

Source: https://github.com/guy0090/api.arsha.io `V2Controller.java`,
`UtilityController.java`, `StatusController.java`, `CacheProperties.java`
(default cache TTL 30 min; repo has NO licence file; last push 2026-07-24).
All GET endpoints also accept POST; EW uses GET only. Region in the path.

| Endpoint (alias) | Params | Live 2026-10-05 |
|---|---|---|
| `/v2/{r}/GetWorldMarketSubList` (`/item`) | `id` (set, repeatable), `lang` | 200 - {name,id,sid,minEnhance,maxEnhance,basePrice,currentStock,totalTrades,priceMin,priceMax,lastSoldPrice,lastSoldTime (epoch s)} |
| `/v2/{r}/GetMarketPriceInfo` (`/history`) | `id` list, `sid` list | 500 code 103 (Imperva) |
| `/v2/{r}/GetBiddingInfoList` (`/orders`) | `id`, `sid` | 500 code 103 |
| `/v2/{r}/GetWorldMarketWaitList` (`/queue`) | `lang` | 500 code 103 |
| `/v2/{r}/GetWorldMarketHotList` (`/hot`) | `lang` | 200 - adds priceChangeDirection (1 up, 2 down), priceChangeValue |
| `/v2/{r}/GetWorldMarketList` (`/category`) | `mainCategory`, `subCategory` | 500 code 103 |
| `/v2/{r}/GetWorldMarketSearchList` (`/search`) | `ids` | 500 code 103 |
| `/v2/{r}/market` | - (every main category; heavy) | not probed (too heavy) |
| `/v2/{r}/pearlItems` | - (category 55) | 500 code 103 |
| `/util/db` | `id` (max 100), `lang` (2 chars) | 200 - {id,name,grade} |
| `/status/scraped-times` | - | 200 - item-db scrape time per language (en 2026-10-02T01:00Z) |
| `/status/cache` | - | 200 - Redis memory stats (not useful) |
| `wss://api.arsha.io/events` | - | cache-expiry events (research 0002 s5) |

No published rate limit or ToS; robots.txt answers 500 (none). EW policy
from research 0001 (cache, UA, backoff, >= 60 s per item) stands. Imperva
blocks are intermittent upstream failures, not a ban on EW.

## 6. Community tools and datasets (robots.txt read 2026-10-05)

| Source | API / data | robots / ToS | Use in EW |
|---|---|---|---|
| arsha.io | public JSON (s5) | no robots, no licence | primary (plans 002 ...) |
| BDO-REST-API (man90es) | profiles, MIT, last push 2026-09-01 | `bdo.hemlo.cc` unreachable from the probe host today (HTTP 000) | plan 004; re-check only |
| Velia Inn | dev docs of raw publisher market API | robots `Allow: /`; docs page is JS-rendered, content not fetchable | fallback docs only |
| bdocodex.com | item pages, no documented API | robots `Allow: /` | link-out only (no scraping without permission, research 0001 s3) |
| bdolytics.com | now served by questlog.gg (Nuxt SPA, auth.js) | robots.txt returns the SPA HTML (no file) | link-out only |
| garmoth.com | grind tracker, cron cost, crates; no public API | robots allows most, disallows `/market/search/`, `/grind-tracker/custom-prices`, `/character-stats/`; fetch answered 403 | link-out only |
| grumpygreen.cricket | WordPress guides, interactive tables | robots disallows only `/wp-admin/`; no licence | cite as source for seeded tables (facts, not copied prose) |
| blackdesertfoundry.com | WordPress guides | robots disallows only wp-admin/tmp | cite as source |
| bdogearguide.com | rate / cron tables | robots `Allow: /`; "unofficial fan-made" | cite as source |
| bdogt.com / bdoloottracker.com | grind trackers, live prices | bdogt disallows `/api/`; bdoloottracker `Allow: /` | link-out only |
| bdoworker.com | worker empire | disallows `/api/` | link-out only |
| somethinglovely.net, mmotrack.com | node map / tracker | unreachable from probe host (HTTP 000) | none |
| official NA/EU site | wiki, news, update history | disallows only `/MyPage/`, `/CS/QNA/`, `/InGame/` | rules source (wiki 47, 133, 146) |

Rule carried forward: tables seeded into EW are typed from the cited page as
facts (numbers), each row with `source` + `verified` date, never bulk-copied
and never fetched by EW at runtime from a site without an API.

## 7. Feature candidates

ToS check for every row: data comes from arsha GET, the operator, a tracked
data file, or OCR of operator-taken screenshots; nothing reads memory, sends
input to the game, or calls an authenticated market action. Rows that would
break that were not listed. Value 1-5 is for this operator NOW (Season
Deadeye, leveling, grinding) with the later gear phase in mind. Overlap:
"DUP" = already covered; "ext NNN" = extends that plan, not a duplicate.

| ID | Feature | Value | Effort | Data source | Overlap |
|---|---|---|---|---|---|
| F01 | Net-proceeds column: every watchlist price shown as collected silver (65 % / 84.5 % with VP toggle / +fame %), and "profit after tax" for a buy-low/sell-high pair | 4 | S | arsha `/item`; VP + fame from operator settings | ext 002 |
| F02 | Pre-order-queue badge: flag watched items with `currentStock == 0` or `lastSoldPrice == priceMax` ("pre-order at cap, random fill if >= 20 B"); overlay ticker shows it | 3 | S | arsha `/item` | ext 002 (alerts are price thresholds only) |
| F03 | Enhancement EV calculator: per step, chance at FS (table lookup), expected attempts, expected material + cron silver, cron vs no-cron, Agris pity cap on attempts | 4 | M | tracked `enhance_rates.json` seeded from bdogearguide/BDFoundry (sourced rows) + arsha mat prices | ext 007 (plan has steps, no math) |
| F04 | Shopping list from the enhancement plan: 007 rows -> total black stones / EoD / crons / caphras at current arsha prices, "can afford by" with logged silver/h | 4 | M | 007 store + F03 table + arsha + 005 averages | ext 007 / 005 |
| F05 | Failstack bank: Advices of Valks (value, count), saved stacks, Valks' Cry count; suggests which stored FS fits the next 007 step's soft cap | 3 | S | operator input + F03 table | ext 007 |
| F06 | Agris Essence pity tracker: stacks per gear category + level, "guaranteed in N fails" | 3 | S | operator input (OCR later) + thresholds table | new |
| F07 | Cron budget: crons owned vs expected crons for planned steps, weekly cron income from login/events | 2 | S | operator input + F03 | ext 007 |
| F08 | Loot-valued grind log: per-spot loot table (trash item, vendor price; market items via arsha) so a session logs item counts and EW computes tax-correct silver/h (trash = vendor, no tax; market = 65/84.5 %) | 5 | M | tracked `loot_tables.json` (operator/community, sourced) + arsha | ext 005 (silver typed by hand today), 012 (silver_tier coarse) |
| F09 | Drop-buff stack calculator: active buffs summed against the 300 % / 400 % / 500 % caps, rate vs amount kept apart, "wasted" flag when over cap | 4 | S | 005 buff timers + tracked buff table (BDFoundry-sourced) | ext 005 |
| F10 | Blessing of Agris ROI line: at the operator's measured silver/h for a spot, is +50 % for 60 min worth 1 B; shows deadline 2026-10-22 / removal 2026-11-05 | 2 | S | 005 averages + constants | ext 005; event itself DUP 006 |
| F11 | OCR loot-window import: parse an operator-taken screenshot of the loot/inventory window into item counts for F08 | 4 | M | 009 OCR chain + ScreenShot watcher (008) | ext 009 / 008 |
| F12 | Sell-vs-vendor advisor: for items that are both vendorable and marketable, compare vendor price vs market net; also "trash pile worth X" | 3 | S | arsha + vendor prices in F08 table | new (feeds F08) |
| F13 | Enhancement-mats price bands: 90-day percentile (p20 / p50 / p80) for Black Stone, Memory Fragment, Caphras, Essence of Dawn, Concentrated Black Gem; "below p20" alert | 3 | S | arsha `/history` (often Imperva-blocked: cache + stale display) | ext 002 (sparkline shown, no stats) |
| F14 | Imperial delivery planner: CP/2 cap per type, 250 % box value, reset countdown, Today-tab tick, best box by arsha input prices | 3 | M | arsha + operator CP + tracked box recipes | ext 003 (checklist row) |
| F15 | Cooking / alchemy margin calculator: operator-typed recipes, arsha input and output prices, net after tax or via imperial box | 3 | M | arsha + operator recipes | new |
| F16 | Value Pack ledger: VP expiry countdown, silver gained by the +30 % on logged sales, +200 LT / +16 slots reminder | 2 | S | operator input + F01 sales | 005 buff timer for VP = DUP; ROI part new |
| F17 | Weight / storage planner: LT sources checklist (skill, pearl, belt, jewelry, alchemy stone, crystals), slots, per-town storage notes | 2 | S | operator input | ext 004 (Progress gear) |
| F18 | Treasure tracker mirroring the Treasure Codex: pieces owned per treasure, source zone, linked to F08 spots | 2 | S | operator input | ext 004 |
| F19 | Marni's Stone tracker: stones carried, filled, turn-in reminder near Toshi towns | 1 | S | operator input | ext 011 (XP sources) |
| F20 | Barter / Margoria log: parley spent, crow coins, level-5 goods; weekly refresh | 2 | M | operator input | new |
| F21 | Registration-queue watch (`/queue`): show watched items currently in the 15-min listing queue | 2 | S | arsha `/queue` (blocked today) | ext 002 (listed as untested in research 0001) |
| F22 | arsha WebSocket refresh instead of TTL polling | 2 | S | `wss://api.arsha.io/events` | DUP research 0002 s5 (recorded, not planned) |
| F23 | Hot-list movers on overlay | 2 | S | arsha `/hot` | DUP 002 (hot list card) |
| F24 | Caphras calculator: stones per level for boss/green gear, silver to C10/C20 | 1 | S | official wiki 146 ranges + arsha 721003 | new (low: season/Tuvala gear path) |
| F25 | Worker empire import (bdo-empire JSON) + node income view | 2 | L | operator-exported JSON | new |

Top by value: F08 (5), then F01, F03, F04, F09, F11 (4). Suggested first
plan if adopted: F08 + F12 (one loot data file feeds both), then F01 + F02
(small Market extensions), then F03 + F04 (gear phase, after the operator
leaves Tuvala).

## 8. Open questions (for a planner, not the operator)

- Agris Fever "amount" value (+50 % vs +100/150 %): sources disagree; keep it
  operator-typed.
- Family Fame market bonus steps: not on the official wiki; operator-typed
  percentage until verified.
- arsha `/history` and `/orders` are Imperva-blocked more often than `/item`;
  features needing history must degrade to the last cached series.
- Whether Blackstar / Sovereign / Kharazad accept caphras: unverified.
