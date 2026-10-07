# 0007 - Autonomy deep dive 2026-10-06 (operator order: self-aware, low-input EW)

Operator order 2026-10-06: the dashboard and overlay must be far more
self-aware and need much less operator input; the operator plays the game,
not the UI. This doc: (1) a code audit of every operator-input point and the
passive signal that can replace or prefill it, (2) web research (read-only,
unauthenticated, robots.txt read first on every host), (3) a ranked design,
(4) the adopted plans 061-073.

Allowed passive signals (CLAUDE.md Game ToS floor), and ONLY these:
S1 session-log tail (running / login / disconnect, plan 008 `gamewatch`);
S2 ScreenShot folder watcher + OCR of screenshots the operator takes anyway;
S3 arsha.io v2 market GETs; S4 BDO-REST-API profiles (see 2.3: public host
now robots-disallowed); S5 public official notices and patch notes; S6 the
clock and reset math; S7 inference from data EW already stores. Never:
memory reads, injection, packet capture, client file edits, live capture of
the game window, any input to the game.

## 1. Audit - operator input today (44 points, grouped)

Pipeline facts (code, 2026-10-06):
- `server/ew/gamewatch.py` polls every 2 s; states `unconfigured`,
  `not_running`, `running`, `logged_in`, `disconnected`; changes go out on
  SSE `/events`. Listeners: `summary.on_game` (only marks
  `grind.pending_stop` on exit), `DiceClock` (sums logged-in minutes).
- Screenshot watcher lists only files newer than the watcher start, max 50.
  OCR (`server/ew/ocr.py`) runs ONLY on a button: System > screenshots
  "Read" (`app/dashboard/game.js`) and Grind "Import from screenshot"
  (plan 040). Silver and buff extraction carry no confidence score; only
  loot rows do (1 - edit distance, `LOOT_MATCH_MAX` 0.25).
- Paths: `bdo.install_dir` and `documents_dir` are hand-edited in
  `config/local.json`; gamewatch never guesses a path; neither is in the
  Settings form (plan 030).
- Overlay widgets: a static set of 8 booleans (`settings.WIDGETS`, sent by
  query string, overlay recreated on Settings save). Nothing selects widgets
  by context.
- Coupons (014) and event notices (059) are suggest-only: each needs "add".

| Area | Input today | Per session | Passive replacement |
|---|---|---|---|
| Paths (008) | type install / Documents dir in local.json | first run | S7: Windows known-folder Documents (incl. OneDrive redirect) + `Black Desert` probe; Steam `libraryfolders.vdf` (Steam's file, not the client) for the install dir -> plan 065 |
| Onboarding (051) | 6 manual steps | first run | auto-tick steps that 065 detects -> 065 |
| Profile family (004/041) | type family name | first run | none safe until S4 is self-hosted -> plan 061 |
| Grind start/stop (005/039) | Start, Stop, spot pick | 1-3 | S1 login/exit -> auto open/close; spot = last spot or OCR loot match -> plan 062 |
| Session-end prompt (046) | confirm stop on exit | 1 | S1 auto-close at last-seen logged_in -> 062 |
| OCR Read (009) | press Read per shot | 0-10 | S2 auto-OCR every new shot while logged_in -> plan 063 |
| Loot import (040) | press Import, confirm rows | 1-3 | S2 auto + confidence gate (rows already scored) -> 063 |
| Silver entry (005) | type silver before/after | 2 | S2 OCR silver deltas between shots in one session -> 063/066 |
| Buff timers (005) | type buff + minutes | 2-5 | S2 buff-bar OCR -> auto timers -> 066 |
| Level / XP % (011/018) | quick-entry typing | 2-4 | S2 OCR of the level/XP % text in shots -> 066 |
| Hot Time windows (011) | type window (UTC) | weekly | S5 official Hot Time post ("Ends Oct 8", "Combat EXP +N %") -> plan 064 |
| Events (006/059) | type, or press add | weekly | S5 auto-add high-confidence with undo -> 064 |
| Coupons (006/014) | press add, tick redeemed | weekly | S5 auto-add + expiry; "redeemed" stays a tick (in-game act) -> 064 |
| Maintenance slot (059) | settings override | rare | S5 maintenance notice UTC column -> 064 |
| Dailies / weeklies (003/021) | tick each | 5-15 | S1 first login of the day ticks login-type rows; S6 auto-reset (exists) -> plan 068 |
| Dice (056) | tick roll | 1 | S7 DiceClock already counts minutes -> "ready" auto, roll tick stays -> 068 |
| World boss looted (032) | tick | 0-3 | S2 shot taken inside a spawn window -> suggest tick -> 068 |
| Imperial / season pass (053/013) | tick counts | 1 | 013 already auto-ticks level rows; rest stays manual (no signal) |
| Overlay widgets (022/030) | Settings toggles | rare, but wrong mid-session | S1+S6+S5 context engine -> plan 067 |
| Notifications opt-in (026) | per-rule toggles | first run | context gating (quiet when game closed) -> plan 070 |
| Market watchlist / thresholds (002/029/052) | type ids + thresholds | weekly | S7 seed from shopping list / loot tables; thresholds = p20/p80 bands -> plan 071 |
| Boss schedule drift (031) | none (data file) | - | S5-like public table diff at low rate -> plan 072 |
| Gear / pets / mounts / inventory (034/043-045) | rosters typed | rare | S2 OCR of character window AP/DP only (034/023) -> 066 field; rosters stay manual |
| Secret books (060) | add / use | weekly | S2 XP % before/after a book shot pair -> 066 feeds `book_use` suggestion |
| Notes (007/057) | free text | - | stays manual by nature |

## 2. Web research (read 2026-10-06, robots.txt first on every host)

### 2.1 Official site (www.naeu.playblackdesert.com)
- robots.txt allows `/en-US/News/*` (disallows `/MyPage/`, `/CS/QNA/`,
  `/InGame/`; unchanged since 0006).
- Lists: `/en-US/News/Notice?boardType=N` (1 notices, 2 updates/patch
  notes, 3 events); rows are `li a[name=btnDetail]` carrying
  `groupContentNo`. Detail: `/en-US/News/Notice/Detail?groupContentNo=ID&
  countryType=en-US`, body in `div.contents_area`. Titles carry a "Last
  Updated" stamp and are edited in place - cache keys must include it.
- Maintenance notice: title shape "October 8 (Thu) Maintenance", a time
  table with PDT / EDT / UTC / CEST columns; the column ORDER changes with
  DST, so the parser locates the UTC header by text, never by index.
- Hot Time post: an "Ends <Mon D>" line and bonus lines such as
  "Combat EXP +1,000%" (comma inside the number).
- Events: "<date> after maintenance - <date> before maintenance" (plan
  059's parser and `maint.resolve`).
- Coupons: word-style codes (example shape DAILY24HCOUPON06) in the span
  right before `button.js-btnCopyCoupon`; plan 014's regex assumed a
  dashed 4x4 shape - widen it.
- No RSS / JSON feed found on the official host. Backup: Steam store RSS
  `store.steampowered.com/feeds/news/app/582660/` (store host robots
  re-checked at runtime by the same gate; `api.steampowered.com` stays
  banned, research 0006 s5).

### 2.2 arsha.io v2 (re-probe 2026-10-06)
- Working: wait list, hot list, search (`GetWorldMarketSearchList`),
  category list, `util/db`.
- Still blocked by Imperva (HTTP 500, JSON code 103) for tradable items:
  per-item sublist, history (`GetMarketPriceInfo`), orderbook. Keep plan
  002's backoff + cache; prefer the search endpoint for live prices of a
  batch of ids (plan 071).

### 2.3 BDO-REST-API
- The public instance moved to `api.cutepap.us`; its robots.txt is
  `Disallow: /`. The old host `bdo.hemlo.cc` is dead.
- DEFECT: `server/ew/progress.py` `DEFAULT_BASE` and
  `config/local.example.json` point at `https://api.cutepap.us/community/v1`
  and plan 041 snapshots it hourly. EW must stop calling a robots-
  disallowed host. Supported path: a self-hosted instance (Docker image
  `man90/bdo-rest-api`, default port 8001 - outside EW's 8940-8959 block,
  so it is configured, not assigned). Plan 061 gates every profile call on
  the robots verdict and degrades plans 004/041/042 to "profile source off"
  with OCR-inferred values (066) as the replacement.

### 2.4 Community tools (garmoth, bdocodex, bdolytics, BDFoundry, others)
- None exposes a documented public API. Black Desert Foundry and Grumpy
  Green are WordPress sites with `/wp-json/wp/v2/posts` (search usable for
  change detection only; EW keeps official pages as the source of truth).
- `mmotimer.com/bdo/?server=na` has a plain boss table (`#mainTbl`), usable
  for a low-frequency diff against `world_bosses_na.json` (plan 072),
  robots-gated at runtime.
- bdoplanner: robots forbids access - never used.

### 2.5 Zero-input patterns in other ToS-safe companions
- A log-tail state machine drives everything (Path of Exile `Client.txt`
  tools, Warframe AlecaFrame on `EE.log`): zone / login lines start and stop
  sessions with no button.
- OCR on screenshot FILES only while the player is logged in, with a
  confidence-gated auto-commit and a review queue for the rest
  (screenshot-file artifact scanners, as opposed to live-capture ones).
- Snapshot work runs on the logout transition (WoW tools reading
  SavedVariables after logout follow the same idea).
- Overlay widgets are shown by game state (Overwolf-style), not by toggles.
- Alerts at 15 / 5 / 1 min before an event, silent while the game is closed.
- Rejected patterns: anything that sends a key or reads the clipboard on a
  keypress in game (Awakened PoE Trade style) and packet capture (ACT) -
  both break EW's ToS floor.

## 3. Design - ranked upgrades

Score = operator inputs removed per play session (from section 1) versus
build cost (S <= 1 lane run, M 1-2, L 3+). All items: ToS-floor clean - the
only inputs are S1-S7; no game memory, injection, packets, client file
edits, live capture of the game window, or input to the game; every web GET
is unauthenticated and robots-gated.

| Rank | Plan | Upgrade | Inputs removed / session | Cost | Signals |
|---|---|---|---|---|---|
| 1 | 061 | Profile source robots gate + self-host base + graceful degrade (compliance fix) | stops an error loop; 0 inputs but a floor defect | S | S4 |
| 2 | 062 | Auto play-session: login opens, exit closes the grind / session log | 3-5 (start, stop, confirm) | S | S1 S7 |
| 3 | 063 | Auto-OCR every new screenshot while logged_in, per-field confidence, auto-commit + review queue | 2-10 (Read, Import, silver typing) | M | S2 S1 |
| 4 | 064 | Official notice auto-import: maintenance UTC, Hot Time windows, events + coupons auto-add with undo | 2-6 a week | M | S5 S6 |
| 5 | 065 | Zero-config first run: path auto-detect, onboarding self-ticks | all first-run typing | S | S7 |
| 6 | 066 | Progress inference from OCR history: level, XP %, silver, buffs, AP/DP, book use | 4-8 | M | S2 S7 |
| 7 | 067 | Context-aware overlay: widgets chosen by state (in game, AFK, maint soon, reset soon, boss soon) | toggling + wrong-widget glances | M | S1 S5 S6 |
| 8 | 068 | Auto-tick inferable checklist rows (login dailies, dice ready, boss-shot suggest) | 2-5 | S | S1 S2 S6 |
| 9 | 069 | One "What now" card (Home + overlay) ranking the next action | reading 5 tabs | M | S7 |
| 10 | 070 | Prompt hygiene: stale-prompt expiry, dedupe, game-closed quiet, 15/5/1 alert ladder | dismiss clicks | S | S1 S6 |
| 11 | 071 | Self-curating market watch: seed from plans, thresholds from price bands | weekly typing | S | S3 S7 |
| 12 | 072 | World boss schedule drift check vs a public table | DST surprises | S | S5-like public page |
| 13 | 073 | Self-health digest: per-signal liveness (log, shots, OCR, notices, market) with one-line fix hints | troubleshooting | S | S7 |

Priority (loop `(priority)`): 061 (floor defect), 062, 063, 064, 065.

Rejected / folded: AFK detection by input idle (needs a desktop input hook -
rejected; "AFK" = logged_in with no screenshot and no state change for N min,
inside 067); live capture of the game window for OCR (floor); clipboard
reading (floor in spirit - input-triggered); OCR of rosters (pets / mounts -
low value, high error); auto-tick of "redeemed coupon" (in-game act, no
signal). Dedupe vs 001-060: 041 auto level sample is profile-API-only (dead
host) - 066 replaces it from OCR; 046 prompt is superseded by 062 auto-close;
059 parser is reused by 064, not rewritten; 025 Home stays and hosts 069.

## 4. Adopted plans

061 profile-source-gate, 062 auto-play-session, 063 auto-ocr-pipeline,
064 notice-auto-import, 065 zero-config-first-run, 066 ocr-progress-
inference, 067 context-overlay, 068 auto-tick-checklists, 069 what-now-card,
070 prompt-hygiene, 071 market-auto-watch, 072 boss-schedule-drift,
073 signal-health-digest. One ROADMAP row each; 061-065 marked priority.
