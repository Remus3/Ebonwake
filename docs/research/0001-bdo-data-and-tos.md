# 0001 - BDO data surface and ToS floor (research, 2026-10-04)

Scope: what a companion app may read, and what it must never do, for Black Desert
Online on the NA/EU publisher (Steam build). Sanitized: install locations are
written as `<steam library>` and `%USERPROFILE%`; nothing here names a machine.
All endpoints below were probed read-only on 2026-10-04 unless marked untested.

## 1. Market data (read-only)

### arsha.io v2 (community cached wrapper) - PRIMARY

GET, JSON, region in the path (`na`, `eu`, `sea`, `mena`, `kr`, `ru`, `jp`, `th`,
`tw`, `sa`, `console_*`). Docs: https://documenter.getpostman.com/view/4028519/2s9Y5YRhp4

| Purpose | Endpoint |
|---|---|
| price, stock, trades, last sold | `https://api.arsha.io/v2/na/GetWorldMarketSubList?id=<item>&lang=en` (alias `/v2/na/item?id=`) |
| ~90 day daily price history (epoch ms keys) | `https://api.arsha.io/v2/na/GetMarketPriceInfo?id=<item>&sid=<enh>&lang=en` |
| order book | `https://api.arsha.io/v2/na/GetBiddingInfoList?id=<item>&sid=<enh>&lang=en` |
| hot list | `https://api.arsha.io/v2/na/GetWorldMarketHotList?lang=en` |
| item name / grade | `https://api.arsha.io/util/db?id=<item>&lang=en` |
| untested | `GetWorldMarketList` (category), `GetWorldMarketSearchList`, `GetWorldMarketWaitList` (registration queue) |

EW policy: cache every response on disk with a TTL (default 10 min, history 6 h),
send a descriptive User-Agent, back off on 429/5xx, never poll faster than once a
minute per item.

### Official Central Market backend - FALLBACK ONLY

Undocumented POST JSON with a pipe/dash-delimited result string; the hot list is
a compressed binary blob. Use arsha unless arsha is down. Authenticated web-market
actions (buy, sell, register; anything needing a verification token) are account
automation and are FORBIDDEN.

## 2. Community profiles

BDO-REST-API (scrapes the official adventurer and guild pages; NA/EU/SA/KR; ~180
minute cache; ~512 req/min/IP). Cold cache answers "being fetched, retry later".
Docs: https://man90es.github.io/BDO-REST-API/ - EW uses it for the family /
character profile card only, at most once per hour.

## 3. Static game data

bdocodex / bdolytics / garmoth: no documented public API found (UNVERIFIED);
scraping would need their owners' permission - EW does not scrape them. Offline
client-file decoders exist; reading client files while the game is closed is not
memory reading, but it is a grey area under the ToS "tampering with game data"
language. NOT adopted by any current plan; a future plan must adjudicate it and
keep it offline, read-only, game closed.

## 4. Local files (observed)

- `<steam library>\Black Desert Online\Log\Client_YYYY-MM-DD_HHMMSS.json` -
  UTF-16LE, one JSON object per line with keys `Date`, `Thread`, `LogType`,
  `ErrNo`, `ErrStr`, `Log`. Session events only (login, server select, auth host,
  logout, reconnect, UI debug). NO xp, loot, silver, chat or zone. EW tails the
  newest file for a game-running / logged-in / disconnected signal only.
- `Log\Client.log` is encrypted; startup / launcher logs are diagnostics only.
- `%USERPROFILE%\Documents\Black Desert\GameOption.txt` - key=value options
  (window mode, screenshot format). Read-only, used to warn when the game is in
  exclusive fullscreen (the overlay needs borderless/windowed).
- Screenshots: PrtSc writes to `%USERPROFILE%\Documents\Black Desert\ScreenShot\`
  (created on first screenshot). A folder watcher plus OCR on these files is the
  cleanest legal "game state" feed: the operator chooses when to capture.
- `Documents\Black Desert\FaceTexture\<characterNo>.bmp` - the in-game
  character portrait (plan 082; adjudicator ruling 2026-10-06, research 0011
  s3): an allowed READ-ONLY input like ScreenShot - listed, read once through a
  short-lived read-only handle, copied to `ops/runtime/portraits/`, never
  written, renamed, deleted or touched; the characterNo comes only from the
  session-log string; nothing under UserCache, Customization or the install
  dir is opened; portrait data feeds no input path.

## 5. Anti-cheat and ToS

- NA/EU anti-cheat is XIGNCODE3 (since 2024-01-17; previously EasyAntiCheat).
  It runs from launch to exit and reports "unauthorized programs". Community
  reports (not official) say it can flag peripheral utilities, dev tools and
  overlays.
- Operational Policy section 5: unauthorized programs or macros, bot-like
  patterns, or anti-cheat detection lead to permanent bans (possibly IP/HW).
  Sources: https://www.naeu.playblackdesert.com/en-US/Policy?policyNo=77 and
  https://www.naeu.playblackdesert.com/en-US/Policy?policyNo=76
- BANNED (EW never does any of these): memory read/write, DLL injection, D3D
  hooking overlays, packet sniffing or modification, client file modification,
  ANY input automation (one input = one action; all macros, including
  "convenience" ones), screen-reading bots that then send input, AFK automation.
- There is no official overlay whitelist. Lowest-risk design, adopted by EW:
  - a separate Electron window: always-on-top, transparent, click-through
    (`setIgnoreMouseEvents(true)`), focus-less; BDO in borderless/windowed;
  - toggle via Electron `globalShortcut` only - no low-level keyboard hooks, no
    key sending;
  - data only from the web APIs above, the session-log tail, the ScreenShot
    watcher and OCR of operator-taken captures; results are never turned into
    input;
  - test any new overlay behaviour on an alt account first.

## 6. Player-facing reference (for the checklist / coupon / event trackers)

Seeded as DATA, not code, in `server/ew/seed/` by later plans; each entry carries
its source URL and an expiry so stale entries drop off automatically:
season character vs season server, Hot Time windows, Steam DLC redemption,
official coupon list (https://www.naeu.playblackdesert.com/en-US/News/Detail?groupContentNo=5676),
new-adventurer login gifts, Challenges (Y), Black Spirit's Adventure dice, Twitch
drops, Olvia Academy. Codes not on the official list are UNVERIFIED and are shown
as such.
