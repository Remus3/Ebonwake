# Ebonwake roadmap

Markers: `[x]` done, `[>]` in progress, `[ ]` open. `[>] session` = a
session carries it (the loop dispatches only `[ ]` rows). One plan = one
batch = one push. Spec: `docs/design/0001-ebonwake-spec.md`. History:
`docs/plans/ROADMAP-history.md`.

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
| 015 | EW loop tick: inbox answers, lane dispatch, review + merge + push, idle deep-dive, 15-min scheduled task | [x] done (built in lane 2026-10-05; task LaneLoop installed, read back 2026-10-09: Ready, PT15M, IgnoreNew, last result 0) |
| 016 | OCR comma-drop regression: a vanished comma must not cut a digit group (old bench 141 -> 142/144) | [x] done 2026-10-05 (loop; refute 1/3 PASS) |
| 017 | Supply-chain hardening (MAIN order Nfa7953): dependabot, SHA pins, hashed CI pip, CodeQL, read-only token, ruff gate, fuzzing ruled out | [x] done 2026-10-05 (loop; refute 1/3 PASS; Scorecard before/after blocked) |
| 018 | Level cap 75 readiness: one level range, patch epoch for XP rates, re-seeded milestones, XP buff presets | [x] done 2026-10-05 (loop; refute 0/3 PASS) |
| 019 | Loop dependency gate: skip a ROADMAP row until its Depends-on plans are done | [x] done 2026-10-05 (loop; refute 0/3 PASS) |
| 020 | Server health + version-skew guard, System freshness card, honest 404 copy, / redirect | [x] done 2026-10-05 (loop; refute 0/3 PASS) |
| 021 | Multi-cadence resets: per-item reset rule (Sunday weeklies, non-midnight dailies) | [x] done 2026-10-05 (loop; refute 0/3 PASS) |
| 022 | Overlay placement off the minimap, content-sized height, legibility | [x] done 2026-10-05 (loop; refute 0/3 PASS) |
| 023 | AP/DP bracket calculator: bonus AP, DR %, next-bracket gain on the Progress card | [x] done 2026-10-05 (loop; refute 0/3 PASS) |
| 024 | Olvia Academy deadline vs level ETA: red pill when Lv 60 lands after enrolment closes | [x] done 2026-10-05 (loop; refute 0/3 PASS) |
| 025 | Home / Now tab: one glance screen for resets, dailies left, buffs, session, ETA, alerts | [x] done 2026-10-05 (loop; refute 0/3 PASS) |
| 026 | Toasts + opt-in Windows notifications (alert hit, buff ending, Hot Time, reset, coupon, game exit) | [x] done 2026-10-05 (loop; refute 0/3 PASS) |
| 027 | Market net proceeds after tax (VP / fame) + pre-order-queue badge | [x] done 2026-10-05 (loop; refute 0/3 PASS) |
| 028 | Market item-name search (local name index) + exact silver in detail | [x] done 2026-10-05 (loop; refute 0/3 PASS) |
| 029 | Overlay market ticker: up to 5 watched prices, alert hits first | [x] done 2026-10-05 (loop; refute 0/3 PASS) |
| 030 | Settings tab + allowlisted POST /api/settings (overlay, hotkeys, profile, theme, scale, notifications) | [x] done 2026-10-05 (loop; refute 0/3 PASS) |
| 031 | World boss schedule: NA table as sourced data, DST-correct next-spawn API | [x] done 2026-10-05 (loop; refute 0/3 PASS) |
| 032 | World boss overlay widget + Today card + 5/15-min notification | [x] done 2026-10-05 (loop; refute 0/3 PASS) |
| 033 | Weekly content planner gated by level and gear (Black Shrine, Atoraxxion, Jetina, LoML, Garmoth, Edania) | [x] done 2026-10-05 (loop; refute 0/3 PASS) |
| 034 | Post-graduation gear roadmap track + graduation readiness + adventure-log seeds | [x] done 2026-10-05 (loop; refute 0/3 PASS) |
| 035 | Enhancement EV calculator: per-step chance tables, expected attempts and cost, Agris pity cap | [x] done 2026-10-05 (loop; refute 0/3 PASS) |
| 036 | Failstack bank + Agris pity tracker + cron budget | [x] done 2026-10-05 (loop; refute 0/3 PASS) |
| 037 | Shopping list from the enhancement plan: materials x arsha prices, can-afford-by | [x] done 2026-10-05 (loop; refute 0/3 PASS) |
| 038 | Drop-buff cap calculator (300/400/500 % caps, rate vs amount) + Blessing of Agris ROI | [x] done 2026-10-05 (loop; refute 0/3 PASS) |
| 039 | Loot-valued grind log: per-spot loot tables, tax-correct silver/h, sell-vs-vendor | [x] done 2026-10-05 (loop; refute 0/3 PASS) |
| 040 | OCR loot-window import: operator screenshot -> item counts for the grind log | [x] done 2026-10-05 (loop; refute 0/3 PASS) |
| 041 | Profile history: hourly BDO-REST-API snapshots, trend sparklines, auto level sample | [x] done 2026-10-05 (loop; refute 0/3 PASS) |
| 042 | Lifeskill / energy / contribution card from the profile API | [x] done 2026-10-05 (loop; refute 0/3 PASS) |
| 043 | Pet roster: 5 slots, tier, talents, special-skill coverage, exchange planner | [x] done 2026-10-05 (loop; refute 0/3 PASS) |
| 044 | Mount tracker: horses (tier, level, skills), Royal Fern Root counter, T10 breed pity calc | [x] done 2026-10-05 (loop; refute 0/3 PASS) |
| 045 | Inventory / weight / storage planner + Value Pack ledger | [x] done 2026-10-05 (loop; refute 0/3 PASS) |
| 046 | Session-end summary: prompt to stop the grind on game exit, nightly and weekly recap | [x] done 2026-10-05 (loop; refute 0/3 PASS) |
| 047 | Density and accessibility rework: content-sized cards, focus-visible, keyboard tabs, tab badges | [x] done 2026-10-05 (loop; refute 0/3 PASS) |
| 048 | Quick-entry parsers (1.2b, 30d, 90m) + local time everywhere | [x] done 2026-10-05 (loop; refute 0/3 PASS) |
| 049 | Shared SSE event bus in the dashboard, hidden-tab poll pause, keyed row updates | [x] done 2026-10-06 (loop; refute 0/3 PASS) |
| 050 | Command palette (Ctrl+K) with typed commands and cross-tab search | [x] done 2026-10-06 (loop; refute 0/3 PASS) |
| 051 | First-run checklist: profile family, log path, ScreenShot folder, overlay corner, watch items, dailies | [x] done 2026-10-06 (loop; refute 0/3 PASS) |
| 052 | Enhancement-mats price bands (p20/p50/p80) with cached-history fallback and below-p20 alert | [x] done 2026-10-06 (loop; refute 0/3 PASS) |
| 053 | Imperial delivery planner: CP/2 boxes per type, 250 % box value, reset countdown | [x] done 2026-10-06 (loop; refute 0/3 PASS) |
| 054 | Cooking / alchemy margin calculator: operator recipes, input/output prices, net after tax | [x] done 2026-10-06 (loop; refute 0/3 PASS) |
| 055 | Jetina boss-crystal planner + Caphras cost calculator | [x] done 2026-10-06 (loop; refute 0/3 PASS) |
| 056 | Black Spirit's Adventure dice timer from logged-in time (overlay) | [x] done 2026-10-06 (loop; refute 0/3 PASS) |
| 057 | Deadeye notes autosave + safe open-in-browser for source links | [x] done 2026-10-06 (loop; refute 0/3 PASS) |
| 058 | Loop resolves its own merge conflicts: re-dispatch a resolve lane | [x] done 2026-10-05 (loop; refute 0/3 PASS) |
| 059 | Official event-notice import: maintenance-relative windows, suggest-only | [x] done 2026-10-06 (loop; refute 0/3 PASS) |
| 060 | Combat Secret Book ledger: books per activity, observed XP, books-to-level | [x] done 2026-10-06 (loop; refute 0/3 PASS) |
| 061 | Profile source robots gate: public BDO-REST-API host is robots-disallowed; self-host base, graceful degrade | [x] done 2026-10-06 (session fix; refute 0/3 PASS) |
| 062 | Auto play-session: login opens, exit closes the grind log (no Start/Stop, no exit prompt) | [x] done 2026-10-06 (loop; refute 0/3 PASS) |
| 063 | Auto-OCR every new screenshot while logged in: confidence-gated auto-commit + review queue | [x] done 2026-10-06 (loop; refute 0/3 PASS) |
| 064 | Official notice auto-import: maintenance UTC, Hot Time windows, events + coupons auto-add with undo | [x] done 2026-10-06 (loop; refute 0/3 PASS) |
| 065 | Zero-config first run: Documents / Steam library path auto-detect, self-ticking onboarding | [x] done 2026-10-06 (loop; refute 0/3 PASS) |
| 066 | Progress inference from screenshot OCR: level, XP %, silver, buffs, AP/DP, book use | [x] done 2026-10-06 (loop; refute 0/3 PASS) |
| 067 | Context-aware overlay: widgets chosen by state (in game, idle, maint/reset/boss soon), pins and blocks | [x] done 2026-10-06 (loop; refute 0/3 PASS) |
| 068 | Auto-tick inferable checklist rows: login dailies, dice ready, boss-shot suggestion | [x] done 2026-10-06 (loop; refute 2/3 PASS) |
| 069 | One "What now" card on Home + overlay: next best action ranked by urgency | [x] done 2026-10-06 (loop; refute 0/3 PASS) |
| 070 | Prompt hygiene: stale-prompt expiry, dedupe, game-closed quiet, 15/5/1 alert ladder | [x] done 2026-10-06 (session merge; refute 1/3 PASS) |
| 071 | Self-curating market watch: seeded from shopping list / loot / recipes, thresholds from price bands | [x] done 2026-10-06 (loop; refute 0/3 PASS) |
| 072 | World boss schedule drift check against a public NA table (robots-gated, banner only) | [x] done 2026-10-06 (loop; refute 0/3 PASS) |
| 073 | Signal health digest: per-signal liveness with one-line fix hints | [x] done 2026-10-06 (loop; refute 0/3 PASS) |
| 074 | Before-maintenance digest: loss warnings from maintenance notices + everything ending at the next maintenance, T-24 h | [x] done 2026-10-06 (loop; refute 0/3 PASS) |
| 075 | Login-day reward tracker: qualifying login days per event, days left, at-risk alert | [x] done 2026-10-06 (loop; refute 0/3 PASS) |
| 076 | Home consolidation: full-width What now, one Timers card, quiet line for empty cards, no repeated facts | [x] done 2026-10-06 (loop; refute 1/3 PASS) |
| 077 | One status truth: onboarding, profile card and stale styling derived from the signal digest | [x] done 2026-10-06 (loop; refute 0/3 PASS) |
| 078 | Settings and formatting consistency: one control per overlay widget, human labels, local-time display (no new entry), palette button | [x] done 2026-10-06 (loop; refute 0/3 PASS) |
| 079 | Override ledger: source, set-at, expiry, superseded by live signals; override badges + digest list | [x] done 2026-10-06 (loop; refute 0/3 PASS) |
| 080 | Settings purge: delete automation kill switches, manual overlay layout, per-rule notify toggles, tunables | [x] done 2026-10-07 (session merge; refute 3/3 PASS) |
| 081 | Derive typed planner inputs from live signals: silver, hours/day, inventory weight, CP, fame, loot counts, Hot Time ends | [x] done 2026-10-06 (loop; refute 0/3 PASS) |
| 082 | Class portrait: FaceTexture archive, characterNo -> class binding from the session log, top-left class chip with empty state | [x] done 2026-10-06 (loop; refute 0/3 PASS) |
| 083 | Deadeye portrait gallery: portrait history + class-attributed screenshots, optional pick as a plan 079 override (depends on 079, 082) | [x] done 2026-10-07 (loop; refute 1/3 PASS) |
| 084 | Larger class portrait card: left-rail character card (156 x 201, 96 x 124 compact) with Lv, class, energy / CP over the image, End Game card style (depends on 082) | [x] done 2026-10-07 (loop; refute 0/3 PASS) |
| 085 | Patch-notes data verifier: hinted unverified data rows checked against official patch notes, runtime verdicts, digest row (depends on 064, 073) | [x] done 2026-10-07 (loop; refute 0/3 PASS) |
| 086 | Reward claim windows: claim-by deadlines that outlive the event, Events row, Timers, What now (depends on 064, 069) | [x] done 2026-10-07 (loop; refute 0/3 PASS) |
| 087 | Patch-notes match fidelity: Unicode fold, name aliases, hints for the official 2026-10-08 numbers (depends on 085) | [x] done 2026-10-08 (loop; refute 0/3 PASS) |
| 088 | Monster Zone Info OCR: per-kill EXP and recommended level per zone, kills-to-level, cap-bound buff flag (depends on 063, 066) | [x] done 2026-10-08 (loop; refute 0/3 PASS) |
| 091 | loop: route ORDER items needing ops/fleet_kit/, CLAUDE.md or .claude/ edits to a session (or grant that lane those paths) - lane briefs block them by construction (N94ff08) | [x] done 2026-10-08 (loop; refute 0/3 PASS) |
| 092 | Fleet kit v11 -> v13 catch-up (Nf0e1ea, N0fcd68, v13 bundle): vendor, anchored + claims hooks, gitlock / suite gate, test guard, identity hooks | [x] done 2026-10-08 (session 20) |
| 093 | loop: reply notes never reach the destination inbox (written only to moon_sync_outbox, undashed 20261008- stamps); copy each reply into the sender's inbox with the dashed stamp, re-hash, record N/M reached; test a reply lands with a matching hash | [x] done 2026-10-08 (loop; refute 0/3 PASS) |
| 095 | Event currency planner: guaranteed currency by event end vs an exchange wishlist, shortfall and buy-by warnings (depends on 064, 075, 021) | [x] done 2026-10-09 (loop; refute 0/3 PASS) |
| 096 | Perf 2.1: one authoritative gate run per tree state - verdict cache keyed by git write-tree (push skips an already-gated tree), verifier drops pytest from VERIFY_EXTRA, producers run touched tests only (MAIN 2246 sec 2) | [x] done 2026-10-09 (loop; refute 0/3 PASS) |
| 097 | Perf 2.2 rest: pytest markers slow / server / git with a fast tier for producer and verifier loops; hash-pinned xdist -n 4 at the loop gate (--dist loadfile for real-git tests), adjudicate a dev-only test dep under the stdlib-only rule; tests only (MAIN 2246 sec 2) | [x] done 2026-10-09 (loop; refute 0/3 PASS) |
| 098 | Perf 2.4: detached review / fix / merge worker (the _launch path); the tick only classifies, dispatches and reaps, target under 60 s; gate independent items in parallel worktrees (MAIN 2246 sec 2) | [x] done 2026-10-09 (loop; refute 0/3 PASS) |
| 099 | Perf 2.5: model / effort routing by item kind - opus for plan implementation, sonnet for fix rounds, data refreshes, resolve-merge and research; effort low for verifier and inbox via pick_effort; choice recorded in loop config (MAIN 2246 sec 2) | [x] done 2026-10-09 (loop; refute 0/3 PASS) |
| 100 | Perf 2.8: governor counts runs from headless_usage.jsonl (or reconciles headless_budget.json at tick start); backfill kind build on the 34 rows lacking kind or exclude pre-v8 rows (MAIN 2246 sec 2) | [x] done 2026-10-09 (loop; refute 0/3 PASS) |
| 101 | Perf 2.9: headless lanes work inline - drop the background agents share one scratchpad clause from headless GATES prompts (MAIN 2246 sec 2); plan docs/plans/101-headless-inline-gates.md | [ ] open |
| 102 | Repo review and audit repo-review-20261009 (MAIN 2246 sec 8): every file on disk in the main checkout (tracked, untracked, ignored), research 0018, reversible fixes | [x] done 2026-10-09 (session re-run from the reaped lane partial; refute 0/3) |
| 103 | Main-checkout inventory the lanes cannot read: count ops/runtime, ops/loop/control, moon_sync_inbox / outbox, logs, app/node_modules (one folder + file count); list sidecar moves into the one sidecar folder; Recycle Bin only, consumer-checked (research 0018) | [x] done 2026-10-09 (folded into 102: counts in research 0018; 33 stale progress files + 2 budget backups moved to the sidecar archive, duplicate old-drive project dir to the Recycle Bin) |
| 104 | Hand-off prune at the next /done: move the closed NOTE / OPERATOR QA bullets research 0018 lists into a dated hand-off ledger and fix the stale Status block (session 19 under SESSION 20) | [x] done 2026-10-09 (session 22 /done: closed bullets to docs/handoff-ledger.txt, Status block rewritten) |
| 105 | TEMP-1 (MAIN FIX N7416e2): pytest.ini tmp_path_retention_policy = failed, tmp_path_retention_count = 1; behavior test in tests/test_pytest_retention.py. Plan: `docs/plans/105-temp1-pytest-retention.md` | [x] done 2026-10-09 (rebuilt on main from keep ref; refute-rounds 3/3, adjudicated) |
| 106 | Sidecar consolidation, session part (research 0018 F-OUT-2/3): lane worktrees from ../ew-worktrees to the kit v14 sidecar root (<sidecar>/EW/Worktree/lane-<i>, fleet_lanes) with the loop HALTed and lanes idle; archive the superseded kit bundles v6-v13 out of moon_sync_inbox after an inbox_seen check | [>] session (main-checkout and loop-config work; not a lane item) |
| 107 | Split the two monoliths by domain, no behaviour change (research 0018 F-APP-1): app/shared/ewcore.js (7562 lines) into per-feature modules re-exported from ewcore.js, and tools/ew_loop.py (2563 lines) inbox / roadmap / merge helpers into modules; every existing test passes unchanged | [ ] open |

Ranking rationale per deep dive / audit and the closed channel-order
sections: `docs/plans/ROADMAP-history.md` (moved verbatim 2026-10-09, plan
102).

Rules: refute rounds capped at 3 per item; every plan lands via a lane worktree
and one push; ETA log updated by every run.

## Order Nfa7953 - blocked items

- Section 4 Scorecard before/after: BLOCKED - needs the scorecard v5.5.0 Linux binary downloaded into WSL (operator-approved download).
