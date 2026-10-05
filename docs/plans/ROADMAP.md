# Ebonwake roadmap

Markers: `[x]` done, `[>]` in progress, `[ ]` open. One plan = one batch = one
push. Spec: `docs/design/0001-ebonwake-spec.md`.

| # | Plan | Status |
|---|---|---|
| 001 | Skeleton: server (health/version/state, SSE), dashboard shell (tabs), overlay shell (click-through + hotkey), ETA log, lane driver, tests + CI | [x] done 2026-10-04 (desktop self-test green: 7 tabs fit 1264x761 content, overlay alpha 0 + WS_EX_TRANSPARENT, hotkeys registered) |
| 002 | Market tab: arsha.io v2 client with disk cache + TTL + backoff, watchlist, sparkline, order book, alert thresholds | [x] done 2026-10-04 (live arsha data on the desktop, self-test 7/7) |
| 003 | Today tab: daily/weekly checklist with NA reset clocks, login events, dice; overlay reset countdown | [x] done 2026-10-04 (refute 2/3 PASS; self-test 7/7) |
| 004 | Progress tab: quest / season-pass / gear tracker, BDO-REST-API profile card | [x] done 2026-10-04 (refute 2/3 PASS; profile API base verified vs upstream openapi) |
| 005 | Grind tab: grind session log, silver/h, buff timers (overlay widget) | [x] done 2026-10-04 (refute 2/3 PASS; self-test 7/7) |
| 006 | Events tab: coupon + event + Twitch-drop tracker with expiries (seed from official pages) | [x] done 2026-10-04 (refute 1/3 PASS) |
| 007 | Deadeye tab: build notes (markdown), enhancement plan | [x] done 2026-10-05 (refute 1/3 PASS; self-test 7/7) |
| 008 | Session-log tail (game running / logged in) + ScreenShot watcher | [x] done 2026-10-05 (refute 2/3 PASS; operator log check pending) |
| 009 | OCR of operator-taken screenshots (buff icons, silver) - adjudicate engine first | [x] done 2026-10-05 (refute 2/3 PASS; follow-up: Tesseract chain primary, Windows OCR fallback, synthetic bench 284/288 vs 159/288) |
| 010 | Packaging: start-on-login task, single-instance, tray | [x] done 2026-10-05 (logon task read back: exists, delay PT1M, enabled; server second instance exits 0; self-test 7/7 painted) |
| 011 | Leveling tracker: XP rate + next-level ETA, recurring Hot Time windows, XP bonus stack (Progress card + overlay widget) | [x] done 2026-10-05 (refute 1/3 PASS; self-test 7/7; OCR word-gap fix also landed, refute 2/3 PASS: 1-2 spaces after silver 66/120 -> 120/120 on renders) |
| 012 | Grind spot recommender by AP/DP/level from a sourced community table | [x] done 2026-10-05 (loop; refute 0/3 PASS) |
| 013 | Season pass tracker by objective, auto-tick level objectives | [x] done 2026-10-05 (loop; refute 0/3 PASS) |
| 014 | Coupon auto-check vs the official news page (robots.txt-gated, suggest only) | [x] done 2026-10-05 (loop; refute 0/3 PASS) |
| 015 | EW loop tick: inbox answers, lane dispatch, review + merge + push, idle deep-dive, 15-min scheduled task | [>] built in lane 2026-10-05 (install + read-back pending) |
| 016 | OCR comma-drop regression: a vanished comma must not cut a digit group (old bench 141 -> 142/144) | [x] done 2026-10-05 (loop; refute 1/3 PASS) |
| 017 | Supply-chain hardening (MAIN order Nfa7953): dependabot, SHA pins, hashed CI pip, CodeQL, read-only token, ruff gate, fuzzing ruled out | [x] done 2026-10-05 (loop; refute 1/3 PASS; Scorecard before/after blocked) |

Ranking 011-014 (adjudicated 2026-10-05, value to a new Season Deadeye who is
leveling now): 011 helps every leveling hour with zero external risk; 012
decides where those hours go; 013 tracks the pass the leveling feeds; 014
saves minutes a week and depends on the publisher's crawl policy.
Alternatives ranked lower: OCR of the XP bar (needs 011 first), market
extensions (low value while leveling).

Rules: refute rounds capped at 3 per item; every plan lands via a lane worktree
and one push; ETA log updated by every run.

## Order N715c44 - blocked items

Done in-lane: item-14 inbox rules folded into the loop tick (`tools/ew_inbox.py`,
`tools/ew_loop.py`; triage sonnet/low/bare, ack = ledger line, 6 notes a day,
HOP lines, batch notes, kind build/inbox/triage); plan 015 deviation 13. No
responder task existed, none disabled.

- DONE (kit-v8 branch, main session): kit v8 vendored into `ops/fleet_kit/` (12/12 hashes vs MAIN outbox, one commit), FLEET-COMMON block re-embedded, conformance test pinned to v8; the tick now calls the kit's `fleet_inbox` directly and `tools/ew_inbox.py` is removed (plan 015 deviation 14).

## Order Nfa7953 - blocked items

- Section 4 Scorecard before/after: BLOCKED - needs the scorecard v5.5.0 Linux binary downloaded into WSL (operator-approved download).
