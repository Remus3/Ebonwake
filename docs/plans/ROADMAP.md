# Ebonwake roadmap

Markers: `[x]` done, `[>]` in progress, `[ ]` open. One plan = one batch = one
push. Spec: `docs/design/0001-ebonwake-spec.md`.

| # | Plan | Status |
|---|---|---|
| 001 | Skeleton: server (health/version/state, SSE), dashboard shell (tabs), overlay shell (click-through + hotkey), ETA log, lane driver, tests + CI | [>] server + tests + CI + app shells landed; Electron run-check on the desktop open |
| 002 | Market tab: arsha.io v2 client with disk cache + TTL + backoff, watchlist, sparkline, order book, alert thresholds | [ ] |
| 003 | Today tab: daily/weekly checklist with NA reset clocks, login events, dice; overlay reset countdown | [ ] |
| 004 | Progress tab: quest / season-pass / gear tracker, BDO-REST-API profile card | [ ] |
| 005 | Grind tab: grind session log, silver/h, buff timers (overlay widget) | [ ] |
| 006 | Events tab: coupon + event + Twitch-drop tracker with expiries (seed from official pages) | [ ] |
| 007 | Deadeye tab: build notes (markdown), enhancement plan | [ ] |
| 008 | Session-log tail (game running / logged in) + ScreenShot watcher | [ ] |
| 009 | OCR of operator-taken screenshots (buff icons, silver) - adjudicate engine first | [ ] |
| 010 | Packaging: start-on-login task, single-instance, tray | [ ] |

Rules: refute rounds capped at 3 per item; every plan lands via a lane worktree
and one push; ETA log updated by every run.
