# 0001 - Ebonwake spec

Status: ACCEPTED 2026-10-04 (adjudicated; operator standing orders apply).
Audience: one player, NA region, Steam build, Deadeye main. Single Windows box.

## 1. Goal

A companion that answers, at a glance and without alt-tabbing: what should I do
today, what is my stuff worth, am I using my buffs, and what is my build plan -
without ever touching the game client (ToS floor: `CLAUDE.md`, long form
`docs/research/0001-bdo-data-and-tos.md`).

## 2. Architecture

```
 public web (arsha.io, BDO-REST-API)        BDO client (read-only, outside it)
          |  GET, cached                        | session log tail, ScreenShot folder
          v                                     v
 +-----------------------------------------------------------+
 | EW server  127.0.0.1:8940   python stdlib, ThreadingHTTP  |
 |  /api/health /api/version /api/state /api/<tab> /events   |
 |  store: JSON files under ops/runtime/store (atomic writes)|
 +-----------------------------------------------------------+
          ^ HTTP + SSE (loopback only, Host-checked)
 +--------+------------------+   +--------------------------+
 | Electron DASHBOARD window |   | Electron OVERLAY window  |
 | compact, tabbed, 1 screen |   | transparent, click-thru, |
 | (no mile-long scroll)     |   | always-on-top, hotkey    |
 +---------------------------+   +--------------------------+
```

- One Electron main process owns both windows (`app/main.js`). The dashboard is a
  normal window; the overlay is `transparent: true, frame: false,
  alwaysOnTop: 'screen-saver', focusable: false, skipTaskbar: true` and calls
  `setIgnoreMouseEvents(true, {forward: false})`.
- Hotkeys via `globalShortcut` only: `Ctrl+Alt+E` toggle overlay, `Ctrl+Alt+D`
  show dashboard. Configurable in `config/local.json`. Never a low-level hook and
  never a key sent anywhere.
- Server binds 127.0.0.1 only and rejects a Host header that is not loopback.
- Restartable at any time (standing order 11); the app reconnects SSE with
  backoff and shows a "server offline" pill instead of stale numbers.

## 3. Data domains (one dashboard tab each; overlay shows a chosen subset)

| Tab | Content | Source | Plan |
|---|---|---|---|
| Today | daily + weekly checklist with reset countdowns (NA reset 00:00 UTC; weekly Thursday), login-event claims, dice | operator ticks; seed data | 003 |
| Market | watchlist prices, 90-day sparkline, order-book depth, alert thresholds, hot list | arsha.io v2 `na` | 002 |
| Progress | quest / main-story / season-pass tracker, level + gear score, Tuvala -> PEN path | operator input, BDO-REST-API profile | 004 |
| Grind | grind spot log (spot, minutes, silver/h, trash count), XP and drop buff timers (scrolls, Hot Time, Value Pack, Old Moon book, Kamasylve) | operator input, ScreenShot OCR (later) | 005 |
| Events | coupon tracker (code, rewards, expiry, redeemed?), events and Twitch-drop deadlines | seed from official pages, operator ticks | 006 |
| Deadeye | build notes: skill add-ons, crystals, artifacts, lightstones, enhancement plan, PvE rotation notes (text only, never executed) | operator-authored markdown | 007 |
| System | server health, data freshness per source, lane/ETA status, logs | server | 001 |

Overlay widgets (each opt-in): next reset countdown, active buff timers, one
market watch ticker, grind session timer. Overlay is glanceable text, 60-80
percent opacity, corner-anchored, never covering the screen centre.

## 4. Dashboard layout rules

- Fits 1280x800 without page scroll. Tabs across the top; each tab is a grid of
  compact cards; long lists scroll INSIDE their card. A tab that would need a page
  scroll is split, not lengthened.
- Visual tokens: `ops/fleet_kit/tokens.css` (vendored, never edited) is imported
  first; EW adds only layout variables in `app/shared/ew.css` and uses
  `--fk-*` colours (status-ok/warn/bad for freshness pills, chart-1..8 for
  sparklines). Light and dark both work via the kit's `prefers-color-scheme`.
- Every number shows its source and age (`arsha 4m ago`). Stale = muted, never
  silently old.

## 5. Store

JSON documents under `ops/runtime/store/<domain>.json`, one writer (the server),
atomic tmp+replace, schema version per file, migrations are functions in
`server/ew/store.py`. Market cache under `ops/runtime/cache/` with TTLs. Nothing
under `ops/runtime/` is tracked.

## 6. Secrets and config

No API key is needed for plan 001-003 (arsha and BDO-REST-API are keyless). Any
later key (for example an OCR or LLM provider) is an env var referenced through
`fleet_secrets` (`{"env": "EW_..."}`) in gitignored `config/local.json`.

## 7. Non-goals (ToS floor)

No memory reading, injection, packet access, client file edits, input
automation of any kind, auto-buy/sell, AFK tooling. No scraping of sites whose
owners have not allowed it.

## 8. Acceptance for the product (all plans)

- Operator can see today's checklist, 5 watched prices and active buff timers on
  the overlay while in game, with zero game-process interaction.
- Every tab renders within 1280x800 without page scroll.
- Server, dashboard and overlay survive a restart mid-session without data loss.
