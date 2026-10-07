# 0003 - Deep dive 2026-10-05: progression domains (research only)

Scope: progression for a new-ish Season Deadeye on NA (questing, milestones,
events, pets, mounts, resets) and the public data behind it, read-only and
unauthenticated. Output: facts with provenance plus feature candidates for EW,
each checked against the Game ToS floor (CLAUDE.md) and against plans 001-018.

Provenance tags:
- `verified <source> <date>`: read on 2026-10-05 from that page; the date is
  the page's own "last updated / last edited" stamp.
- `unverified`: from a search summary, an old page (pre-2026), or memory of the
  game; must be re-checked before it ships as data.

robots.txt read 2026-10-05 (generic `User-agent: *`):
- naeu.playblackdesert.com: disallows only `/MyPage/`, `/CS/QNA/`, `/InGame/`
  (the `/Wiki` and `/News/` pages used here are allowed).
- blackdesertfoundry.com: disallows only `/wp-admin/` and two upload temp dirs.
- garmoth.com: allows `/` except admin/auth/settings/chat/market favourites/
  `/character-stats/` etc. The world-boss page answered HTTP 403 to a fetch, so
  nothing here is taken from garmoth.
- veliainn.com: `Allow: /`, but the boss page is client-rendered (no data in
  the HTML); nothing taken.
- bdocodex.com: `Allow: /`.
- bdolytics.com: no robots.txt (SPA shell returned); treated as "no
  permission", not fetched.
- mmotimer.com: robots.txt carries content-signal reservations (EU DSM art. 4);
  not fetched.

## 1. Facts by domain

### 1.1 Questing and the season chain

- Season path: create Season character -> main questline (Balenos, Serendia,
  Calpheon, Mediah) -> enhance Tuvala -> finish Season Pass -> graduate ->
  Olvia Academy -> free Kharazad (Alustin) -> Emma Bartali log (OCT Kharazad)
  -> Sovereign weapons -> Igor Bartali log (+6 AP/DP family-wide, permanent)
  -> Black Shrine solo (free Slumbering Origin armor) -> Hammer Challenge.
  verified BDFoundry new-player guide 2026-08-11
  (https://www.blackdesertfoundry.com/new-player-guide/).
- Season Pass: 24 levelling objectives (Lv 5 -> 60) + 1 Awakening/Succession
  objective; PEN (V) Tuvala weapon/armor/sub/awakening boxes; Time-filled
  Fragments 1,000 at Lv 25 and 1,500 at Lv 55; Season Adventure Tokens 50 at
  Lv 38 and 100 at Lv 58; accessory support boxes at Lv 53 and 59.
  verified BDFoundry season pass guide 2026-07-30
  (https://www.blackdesertfoundry.com/season-pass-guide/).
  Note: the Global Lab XP / quest re-ladder (research 0002 section 1) can move
  the level at which objectives fall; plan 013 seed is "verify in game".
- Main questline re-ladder after the level-75 patch: Calpheon / Morning Light
  / Mountain of Eternal Winter each reach Lv 56; Rebirth of Darkness 56 -> 60;
  Olvia combat course 60 -> 61. Already captured: research 0002, plan 018.
- Awakening and Succession both unlock at Lv 56 ("[Class] New Power" quest
  from the Black Spirit, Main tab); skill presets let a character swap at a
  Skill Instructor (10 min cooldown). verified BDFoundry 2023-01-31 (old)
  (https://www.blackdesertfoundry.com/class-awakening-succession-guide/).
- Deadeye specifically: Succession (Revolvers / Shotgun) and Ascension
  (weapon "Lil' Devil"); no Awakening. verified official Adventurer's Guide
  "Deadeye" 2025-07-04 (https://www.naeu.playblackdesert.com/en-us/Wiki?wikiNo=436).
  So the season pass "Awakening or Succession" objective is the Succession or
  Ascension questline for a Deadeye (which one counts: unverified).
- Fughar's Secret Adventure Journal: Lv 55+, 3 books x 5 chapters; chapter
  rewards are Fughar's Secret Books (contribution XP); book finales give +2 LT
  weight / +1 inventory slot / gift box. Steps include 777 kills at Kratuga
  (250 AP / 310 DP advised), 700 amity with an NPC, a 1 billion silver "show",
  and 55 Calpheon NPC knowledge topics. verified BDFoundry 2025-07-25
  (https://www.blackdesertfoundry.com/fughars-adventure-journal/).
- Contribution points: almost every quest gives contribution XP; weekly CP
  quests open at 220-349 CP (Thursday reset); life-skill byproducts exchange
  for 900 CP XP each; workshops 5-10 CP, Nesser/Kaia rental gear 50 CP.
  verified BDFoundry CP guide 2026-07-01
  (https://www.blackdesertfoundry.com/contribution-points-guide/).
- Black Spirit's Scheduler (in game, F8, family-wide) was added 2026-09-10 to
  track daily/weekly quests. verified official patch notes 2026-09-10
  (https://www.naeu.playblackdesert.com/en-US/News/Detail?groupContentNo=10577).
  EW cannot read it (client UI); it is the in-game twin of the Today tab.
- Treasure Codex (F5) added 2026-09-23 (research 0002 section 2).
- "Path of Reverie": no such content found in official pages or search on
  2026-10-05. unverified / probably a misremembered name; not modelled.
- Hidden quests, knowledge and amity: no 2026 source read this round;
  BDFoundry has knowledge/energy and rare-hunting-knowledge guides
  (https://www.blackdesertfoundry.com/knowledge-energy-guide/). unverified.

### 1.2 Gear milestones

- AP brackets (sheet AP -> bonus AP), excerpt: 100-139 +5, 140-169 +10,
  170-183 +15, 184-208 +20, 209-234 +30, 235-244 +40, 245-248 +48,
  249-252 +57, 253-256 +69, 257-260 +83, 261-264 +101, 265-268 +122,
  269-272 +137, 273-276 +142, then +6..+7 per 4 AP to 309-315 +200, slower
  above, 449+ +297. Main-hand AP and awakening/ascension AP bracket
  separately. DP: damage-reduction-rate bracket 203-210 1% ... 401+ 30%
  (about +1% per 7 DP), plus an "all damage reduction" bracket 253-255 +2 ...
  531+ +101. Full tables (66 + 30 + 68 rows) are on the page.
  verified BDFoundry AP/DP brackets 2026-08-13
  (https://www.blackdesertfoundry.com/ap-and-dp-brackets-guide/).
  Biggest step for a leveling player: 245 -> 276 sheet AP moves bonus AP
  48 -> 142 (the 257-272 band is the steep part).
- Hammer Challenge AP milestones: 20 steps 340 -> 385 AP (same page).
- Season graduation: finish and claim the whole pass, have full PEN Tuvala,
  spend spare Time-filled Fragments first; auto-graduation after 180 days
  inactive; rewards include a Boss Gear Exchange Coupon, 100 Cron Stones,
  Advice of Valks (+60), Old Moon book 7 d, 5 Blessed Message Scrolls.
  verified BDFoundry graduation guide 2026-07-30
  (https://www.blackdesertfoundry.com/season-graduation-and-early-graduation/).
- Olvia Academy: Lv 60+; rewards TET and PEN Blackstar weapon (choose main /
  awakening / sub), PEN boss armor pieces, Essence of Dawn x60, 6,000 Cron
  Stones. DEADLINE: Class 3 enrolment 2026-07-30 .. 2026-11-05, quest
  acceptance until 2026-11-11. verified BDFoundry Olvia guide 2026-07-30
  (https://www.blackdesertfoundry.com/olvia-academy-guide/). Official dates
  not read: re-check the official notice before seeding.
- Jetina PEN boss path: Lv 60+, "[Weekly] Imperfect Beings" (Thursday 00:00
  UTC): 2 Latent Boss Aura -> 155 Concentrated Boss Crystal; 60-120 crystals
  per reform level, roughly 2-4 weeks per weapon piece. verified BDFoundry
  2026-07-02 (https://www.blackdesertfoundry.com/jetina-pen-boss-gear-guide/).
- Kharazad: free PEN via Alustin (10 Essence of Dawn + 50 Sharp Black Crystal
  Shard per piece, once per family); Emma Bartali log gives 2 OCT pieces;
  enhancement costs Essence of Dawn 1/2/3/4/6 (PRI..PEN), 8-15 above, DEC uses
  Dawn Black Stones; no break on fail. verified BDFoundry 2026-08-10
  (https://www.blackdesertfoundry.com/kharazad-accessories-guide/).
- Sub-weapon: Kutum = PvE (monster damage), Nouver = PvP; Sovereign sub =
  PEN Blackstar sub + PEN Kutum (Caphras 20) + PEN Nouver (Caphras 20).
  unverified (search summaries of garmoth / orbit-games; official update
  history https://www.naeu.playblackdesert.com/en-US/Adventure/History?_groupMasterNo=9098
  not read).
- Caphras: only worth it on PEN gear; boss main-hand Lv 20 = 29,403 stones
  (297 at Lv 1 -> 2,670 at Lv 20); not usable on Blackstar. verified BDFoundry
  2024-02-14 (old; numbers may have changed)
  (https://www.blackdesertfoundry.com/caphras-stones/).
- Crystals: 15 equipped, up to 10 presets, break only in the applied preset on
  death to monsters / negative karma; Ancient Magic Crystals unlimited.
  verified BDFoundry 2026-08-10
  (https://www.blackdesertfoundry.com/crystal-sockets-guide/).
- Artifacts / lightstones: 2 artifacts x 2 stones; leveling combos "Prayer for
  Victory" (Combat XP +75%, Skill XP +15%, Monster AP +4) and "Progress"
  (Combat XP +225%, Skill XP +40%); extraction tool 10 M silver. verified
  BDFoundry 2026-07-07
  (https://www.blackdesertfoundry.com/artifacts-and-lightstones-guide/).
  Values predate the 2026-10-08 XP revamp (research 0002): re-verify.

### 1.3 Events, bosses, weekly content

- NA world boss table (PT, official image uploaded 2025-12-24, page edited
  2026-09-24): verified official Adventurer's Guide "World Bosses"
  (https://www.naeu.playblackdesert.com/en-US/Wiki?wikiNo=83):

| PT | Mon | Tue | Wed | Thu | Fri | Sat | Sun |
|---|---|---|---|---|---|---|---|
| 00:00 | Golden Pig King, Kzarka | Sangoon, Nouver | Golden Pig King, Kutum | Bulgasal, Karanda | Uturi, Kzarka | Bulgasal, Nouver | Sangoon, Offin |
| 10:00 | Uturi, Nouver | Golden Pig King, Kutum | Bulgasal, Nouver | Sangoon, Kzarka | Bulgasal, Karanda | Uturi, Kzarka | Golden Pig King, Kutum |
| 12:00 | Garmoth | Garmoth | Garmoth | Garmoth | Garmoth | Garmoth | Garmoth |
| 14:00 | - | - | - | Quint, Muraka | Sangoon, Kutum | Black Shadow (field boss) | Vell |
| 17:00 | Sangoon, Karanda | Bulgasal, Kzarka | Vell | Uturi, Offin | Golden Pig King, Nouver | Quint, Muraka | Garmoth |
| 20:15 | Golden Pig King, Kutum | Sangoon, Nouver | Sangoon, Karanda | Bulgasal, Kutum | Uturi, Kzarka | - | Uturi, Karanda |
| 21:15 | Garmoth | Garmoth | Garmoth | Garmoth | Garmoth | - | Garmoth |
| 22:15 | Bulgasal, Offin | Uturi, Karanda | Uturi, Kzarka | Golden Pig King, Nouver | Sangoon, Karanda | Golden Pig King, Kutum | Bulgasal, Nouver |

  Cross-check: the official Known Issues notice gives NA Muraka as Thu 21:00
  UTC and Sun 00:00 UTC = Thu 14:00 / Sat 17:00 PDT, matching the table.
  OPEN QUESTION (unverified): whether spawns stay on PT wall-clock after the
  2026-11-01 DST change (likely yes; the table is labelled "PT").
- Boss rules (same official page): world bosses vanish after 30 min (Quint /
  Muraka 15 min); HP shared across channels; no XP loss or crystal break on
  death; Garmoth loot 3 times a week, weekly quest resets Thursday 00:00 UTC;
  Land of the Morning Light boss region quests are weekly (Thursday 00:00
  UTC), one kill of each of the four types covers the week; in-game
  notification can be set at 5/15/30 min (F5 > Boss Notification).
- Loot per character per boss: one reward per day (BDFoundry 2024-02-09, old;
  unverified for 2026).
- Black Shrine solo boss rush: 5 clears a week per family, reset Sunday 00:00
  UTC, rewards Sunday 00:10 UTC; Calamity 1-10; 90% of damage from aura type,
  10% from AP; first clears give family-wide permanent stats. verified
  BDFoundry 2026-07-10
  (https://www.blackdesertfoundry.com/boss-rush-black-shrine-guide/).
- Altar of Blood: Illusions 22-24 added and Abyssal Illusion removed
  2026-09-10; weekly reward window quoted as starting Sunday 00:00. verified
  official patch notes 2026-09-10 (groupContentNo=10577).
- Atoraxxion (Vahmalkea): Lv 60+, party of 5, ~250 Kutum AP / 300 DP (normal),
  ~280 / 340 Elvia; weekly, Thursday 00:00 UTC; solo story mode gives only
  first-clear rewards. verified BDFoundry 2026-07-02
  (https://www.blackdesertfoundry.com/atoraxxion-dungeon-guide/).
- Edania weekly bosses: 1 per week, Thursday 00:00 UTC, 350-420 AP and
  427-505 DP: out of reach for a season character. verified BDFoundry
  2026-08-24 (https://www.blackdesertfoundry.com/edania-weekly-bosses-guide/).
- Node War Sun-Fri 18:00 PT, Conquest War Sat 20:00 PT. unverified (search
  summary); node-war revamp with Edania / Ulukita territories landed
  2026-04-30, verified official notice 2026-04-27
  (https://www.naeu.playblackdesert.com/en-US/News/Detail?groupContentNo=9996).
- Hot Time, attendance, coupons, Twitch drops: covered by plans 006, 011, 014
  and research 0002 section 3 (October login rewards end 2026-10-31 23:59
  UTC).
- Black Spirit's Adventure dice: up to 3 basic dice a day, one at login, more
  after 30 and 60 minutes of play; "3/3 limit resets at 05:00 UTC" (sic).
  unverified (BDFoundry 2023-11-29, old;
  https://www.blackdesertfoundry.com/black-spirits-adventure-guide/). Plan 003
  assumes every daily resets at 00:00 UTC; the dice may not.
- Arena of Arsha, guild missions, life-skill events, fishing contest: guides
  exist (BDFoundry arena-of-arsha-guide, weekly-fishing-contest-guide) but are
  low value to a solo leveling Deadeye; not read this round.

### 1.4 Pets

- Tiers 1-5; T4 loots faster and holds more hunger; T5 training at Obi Bellen
  (Old Wisdom Tree) with no prerequisite quest; a T5 pet can be appointed
  Alpha: other checked-out pets loot 15% faster and the Alpha's talent gains
  one level; special skills are not boosted; a trained T5 can no longer be
  exchanged. verified official Adventurer's Guide "Tier 5 Pet Training"
  2025-07-25 (https://www.naeu.playblackdesert.com/en-us/Wiki?wikiNo=264)
  and search summary of the same.
- Up to 5 pets out; special skills by species: gathering detection (cat,
  ferret, duck), flagged-player detection (dog, lamb, dragon), elite marking
  (birds), auto-fishing speed (penguin, otter, dragon), desert resistance
  (fox), gathering amount (hedgehog, llama), taunt (panda). Feed: Cheap 12,
  Good 80, Organic 140 hunger. Exchange destroys both parents, same type only
  (Wizard Gosphy excepted); up to 5 pets for a 100% T4. verified BDFoundry
  pets guide 2025-07-25 (https://www.blackdesertfoundry.com/pets-guide/).

### 1.5 Mounts and ships

- Tier 10 "Mythical" horses: Arduanatt, Dine, Doom. Breed from a male +
  female T9 of the same type, both Lv 30, with a Mythical Censer; 3% base,
  +0.2% per failure; Royal Fern Root x100 (Wapra / Liana daily-weekly, the
  time-gated bottleneck), Flower of Oblivion x100 (Imperial horse delivery
  of T6+), Mythical Feather x10. verified BDFoundry 2026-07-03
  (https://www.blackdesertfoundry.com/dreamy-horses-tier-10/).
  Dream horses (Krogdalo, Voltarion, free T9 Vipiko's dream horse) have own
  guides (krogdalo-horse-and-donkey-gear-guide, voltarion-dream-horse-guide,
  free-t9-horse-vipikos-dreaming-horse); not read. A 10th-anniversary free
  Mythical horse is mentioned on the T10 page (date unverified).
- Camel: free quest at Sand Grain Bazaar (Lv 54+), max Lv 20, faster in
  desert. Mini elephant: Shakatu's Villa quest, Lv 55+, 30-40 M silver,
  1,200 LT, 16 slots. verified BDFoundry 2025-03-26
  (https://www.blackdesertfoundry.com/elephants-and-camels-guide/).
- Ships: Sailboat -> Improved -> Caravel -> Carrack (Balance / Advance) or
  Frigate -> Improved -> Galleass -> Carrack (Volante / Valor). Advance
  (barter) needs +10 green parts x4 then Moon Vein Flax Fabric 180,
  Deep Tide-Dyed Timber Square 144, Brilliant Rock Salt Ingot 35, Tear of the
  Ocean 42, Brilliant Pearl Shard 35 for the blue upgrade; sources are
  dailies, Crow Coins, Oquilla coins, Lv 5 barters, sea monsters. verified
  BDFoundry 2026-09-07 (https://www.blackdesertfoundry.com/epheria-carrack-guide/).

### 1.6 Resets observed (UTC)

| Content | Reset | Source |
|---|---|---|
| Dailies (EW plan 003) | daily 00:00 | plan 003 / research 0001 |
| Black Spirit's Adventure dice | daily 05:00 (?) | unverified, BDFoundry 2023 |
| World boss loot | per character per day | unverified, BDFoundry 2024 |
| Weekly quests, Garmoth 3/wk, LoML bosses, Atoraxxion, Edania, Jetina, CP weeklies | Thursday 00:00 | verified official wiki 2026-09-24 + BDFoundry 2026 |
| Black Shrine solo (5/wk), Altar of Blood weekly | Sunday 00:00 | verified BDFoundry 2026-07-10 / official 2026-09-10 |

Plan 003 models only `daily` (00:00) and `weekly` (Thursday 00:00): the Sunday
cadence and any non-midnight daily are missing.

### 1.7 Not found / out of scope

- "Black Spirit's Rage" (the rage-bar skill) and Garmoth / Kzarka summon
  scrolls: no 2026 source read; nothing to track from outside the client.
  unverified.
- "Path of Reverie": see 1.1.

## 2. Public data sources: what EW may ingest

| Source | What | Access | EW stance |
|---|---|---|---|
| official NA/EU `/Wiki`, `/News/` | boss table (image), resets, rules, event dates | robots-allowed GET | OK: one GET per page at most daily, seed as reviewed data with URL + date; the boss table is an IMAGE, so it is transcribed by a lane, never OCR-scraped live |
| BDO-REST-API (`https://api.cutepap.us/community/v1`, openapi `servers[0]` read 2026-10-05) | adventurer profile: `characters`, `contributionPoints`, `energy`, `specLevels`, `mastery`, `combatFame`, `lifeFame`, `gs`, `privacy` | keyless GET, ~180 min cache | OK (plan 004 uses it, hourly). New fields beyond the card are unused today. Profile privacy can hide fields. |
| arsha.io v2 | prices for Caphras, Memory Fragment, Black Stone, Essence of Dawn, Concentrated Boss Crystal | keyless GET | OK (plan 002 rules); read-only, never buy/sell |
| BDFoundry | brackets, guides, dates | robots-allowed; no API | OK as a CITED source for hand-reviewed seed data; never a live scrape |
| garmoth.com | boss timer, grind tracker | robots allow most pages, but 403 to fetch; no public API | do not ingest |
| veliainn.com | boss timer, market docs | robots allow; boss page is client-rendered | do not ingest (no documented boss API) |
| bdocodex.com | item / quest DB | robots allow; no API documented | manual citation only (research 0001) |
| bdolytics.com | item DB | no robots.txt | do not ingest |

ToS: every source above is outside the client; nothing reads game memory,
sends input, or calls an authenticated market action.

## 3. Feature candidates

Columns: V = value to the operator 1-5 (a Season Deadeye leveling now), E =
effort S/M/L, DUP = overlap with plans 001-018 (DUP = mostly covered; part =
extends an existing plan; new = no overlap). ToS column: every candidate is
display-only from public GETs, operator input, or the plan 008 session-log
signal; none sends input, reads memory, or calls an authenticated market
action.

| # | Candidate | Kind | V | E | Data source | ToS | Overlap |
|---|---|---|---|---|---|---|---|
| C01 | AP/DP bracket calculator: sheet AP / AAP / DP -> bonus AP, DR %, all-DR, and "next bracket in N AP gives +M"; marks the 257-272 cliff | calculator (Progress card) | 5 | S | BDFoundry tables 2026-08-13 shipped as `data/brackets.json` with source + verified | ok (static data, operator gs from plan 004) | new; reads plan 004 gs |
| C02 | Olvia Academy deadline vs level ETA: "Lv 60 needed by 2026-11-05 (enrol) / 11-11 (quests); at current rate you reach 60 on <date>" with a red pill if late | timer + alarm (Events + overlay) | 5 | S | event dates (re-check official notice), plan 011 rate/ETA | ok | part: 006 holds deadlines, 011 the ETA; new is the cross-link |
| C03 | Post-graduation gear roadmap: ordered, level/AP-gated track Tuvala PEN -> graduate -> Olvia Blackstar TET/PEN -> Alustin Kharazad -> Emma OCT -> Jetina weekly -> Sovereign -> Igor +6 AP/DP -> Black Shrine Origin armor -> Hammer Challenge | tracker / recommender (Progress) | 5 | M | BDFoundry new-player 2026-08-11 + linked guides | ok | part: 004 has a Tuvala->PEN track only; seed a second track |
| C04 | World boss timer: next 3 NA spawns with countdown, DST-correct from the PT table, per-boss "looted today" tick, Garmoth n/3 this week | timer + overlay widget | 4 | M | official wiki table (transcribed, URL + edit date); rules from same page | ok (no in-game notification read) | new (003 has only a generic "weekly boss rewards" tick) |
| C05 | Multi-cadence resets: per-item reset rule (daily at HH:MM, weekly on weekday HH:MM) so Black Shrine / Altar of Blood (Sunday) and dice (05:00?) reset correctly | engine change (Today) | 4 | S | 1.6 table | ok | part: extends 003 (daily + Thursday only) |
| C06 | Weekly content planner gated by gear: Black Shrine 5/wk, Atoraxxion, Jetina, LoML bosses, Garmoth, Edania; each shows "eligible / needs +N AP" from plan 004 gs and level | checklist + recommender (Today) | 4 | M | BDFoundry 2026 pages, official wiki | ok | part: 003 weekly items; new gating |
| C07 | Graduation readiness checklist: pass fully claimed, every Tuvala slot PEN, Time-filled Fragments spent, Succession/Ascension quest done, auto-graduate at 180 d idle noted | checklist (Progress) | 4 | S | BDFoundry graduation 2026-07-30 | ok | DUP-part: 004 Tuvala track + 013 pass; add as 013 seed rows, not a new plan |
| C08 | Leveling lightstone combo hint: shows "Progress" (+225% combat XP) / "Prayer for Victory" in the XP stack card with a re-verify flag after the 10-08 revamp | recommender (Progress) | 3 | S | BDFoundry 2026-07-07 | ok | DUP-part: 011/018 XP buff presets; fold into 018 presets |
| C09 | Profile history: hourly BDO-REST-API snapshots of level, gs, energy, contributionPoints, specLevels, combatFame -> trend sparklines and auto level sample for plan 011 / auto-tick plan 013 | tracker | 4 | M | BDO-REST-API openapi (read 2026-10-05) | ok (keyless GET, <= hourly, existing cache) | part: 004 card shows one snapshot; 011 samples are typed |
| C10 | Contribution points card: CP from profile, milestones (220-349 weekly CP quests, 250+ worker net), daily CP quest regions list | tracker (Progress) | 3 | S | BDO-REST-API `contributionPoints`, BDFoundry CP 2026-07-01 | ok | part: 004 profile card |
| C11 | Adventure-log tracker seeds: Fughar (3x5), Igor Bartali (+6 AP/DP), Emma Bartali, with per-step gates (e.g. Kratuga 250 AP / 310 DP) | checklist (Progress tracks) | 3 | S | BDFoundry 2025-07-25 + new-player guide | ok | DUP-part: 004 custom tracks exist; seed data only |
| C12 | Dice timer: next Black Spirit's Adventure die at login, +30, +60 min of logged-in time | timer (overlay) | 3 | S | plan 008 logged-in signal; rule unverified (2023) | ok (session log only) | part: 003 has a daily dice tick |
| C13 | Jetina / boss-crystal planner: crystals on hand + 155/week vs 60-120 per reform level -> weeks to PEN per piece | calculator | 3 | S | BDFoundry 2026-07-02 | ok | part: 007 enhancement plan is text only |
| C14 | Caphras cost calculator: stones to target level per slot x arsha price -> silver; "not on Blackstar" guard | calculator (Deadeye) | 2 | M | BDFoundry 2024 (re-verify) + arsha v2 GET | ok (read-only GET) | new; reuses 002 client |
| C15 | Next-upgrade value ranker: for each planned enhancement, AP gained incl. bracket bonus per silver (arsha prices) | recommender | 4 | L | C01 tables + arsha + operator gear | ok | part: 007 plan + 002 market |
| C16 | Pet roster: 5 slots, tier, talent, special skill, hunger fed-at; coverage check (loot, detection, fishing, gathering); T5 Alpha note; exchange planner (5 same-type -> 100% T4, parents destroyed) | tracker + planner (new card) | 3 | M | BDFoundry pets 2025-07-25, official T5 wiki 2025-07-25 | ok (operator input) | new |
| C17 | Horse progression tracker: tier, level, skills; Royal Fern Root time-gate counter; T10 breed pity (3% + 0.2%/fail) calculator | tracker + calculator | 2 | M | BDFoundry 2026-07-03 | ok | new |
| C18 | Carrack material checklist: Advance/Valor counts (180/144/35/42/35), daily sources, Tidal Black Stone counts | tracker | 2 | M | BDFoundry 2026-09-07 | ok | new |
| C19 | War-hours hint: Node War Sun-Fri 18:00 PT, Conquest Sat 20:00 PT on the reset clock (PvP-dense hours) | timer | 1 | S | unverified search summary; official wiki 56/344 to read first | ok | new |
| C20 | Questline ladder card after the level-75 patch (Calpheon/MoL/MoEW -> 56, Rebirth of Darkness -> 60, Olvia -> 61) | tracker | 3 | S | Global Lab 2026-10-02 | ok | DUP: plan 018 milestones |
| C21 | Ascension vs Succession note for Deadeye in the season pass objective | note (Deadeye tab) | 2 | S | official Deadeye wiki 2025-07-04 | ok | DUP: plan 007 notes are free text |

Count: 21 candidates; 2 are DUP (C20, C21), 3 DUP-part that should fold into
an existing plan as seed data (C07, C08, C11), the rest new or extending an
existing plan.

### 3.1 Top 5 by value (tie-break: smaller effort, fewer unverified facts)

1. C01 AP/DP bracket calculator (5, S) - verified 2026 data, no network,
   turns the plan 004 gs number into "what my next AP is worth".
2. C02 Olvia Academy deadline vs level ETA (5, S) - a hard 2026-11-05 cut-off
   for free TET/PEN Blackstar; plan 011 already has the ETA. Official date
   must be read before seeding.
3. C03 Post-graduation gear roadmap (5, M) - the whole free-gear path in one
   ordered, gated track.
4. C04 World boss timer + overlay (4, M) - verified official table; daily
   value once at Lv 60 and geared for loot.
5. C06 Weekly content planner gated by gear (4, M), paired with C05
   multi-cadence resets (4, S) which it needs for the Sunday cadence.

### 3.2 Notes for whoever plans these

- Data shipping rule (research 0001 section 6): every seeded row carries
  `source` URL and `verified` date; unverified rows are labelled in the UI.
- C04 transcribes an image: a lane writes `data/world_bosses_na.json` by hand
  from the official image, cites the page edit date, and a test asserts the
  Muraka cross-check (Thu 14:00 / Sat 17:00 PT). Re-check after the
  2026-11-01 DST switch.
- C05 is a prerequisite for C06 and C12; it changes the plan 003 data model
  (`kind` -> `reset: {every: day|week, weekday?, at: "HH:MM"}`), with a
  migration keeping `daily`/`weekly` items unchanged.
- C02 / C08 depend on the 2026-10-08 maintenance (plan 018 epoch); values
  read before it may move.
- The in-game Black Spirit's Scheduler (2026-09-10) overlaps the Today tab
  for in-game use; EW's edge is the overlay, the phone-glance dashboard and
  cross-domain links (deadline vs ETA, gear vs content gates), not a second
  checklist.
