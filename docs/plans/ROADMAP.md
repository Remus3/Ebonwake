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
| 096 | Perf 2.1: one authoritative gate run per tree state - verdict cache keyed by git write-tree (push skips an already-gated tree), verifier drops pytest from VERIFY_EXTRA, producers run touched tests only (MAIN 2246 sec 2) | [ ] open (priority) |
| 097 | Perf 2.2 rest: pytest markers slow / server / git with a fast tier for producer and verifier loops; hash-pinned xdist -n 4 at the loop gate (--dist loadfile for real-git tests), adjudicate a dev-only test dep under the stdlib-only rule; tests only (MAIN 2246 sec 2) | [ ] open |
| 098 | Perf 2.4: detached review / fix / merge worker (the _launch path); the tick only classifies, dispatches and reaps, target under 60 s; gate independent items in parallel worktrees (MAIN 2246 sec 2) | [ ] open (priority) |
| 099 | Perf 2.5: model / effort routing by item kind - opus for plan implementation, sonnet for fix rounds, data refreshes, resolve-merge and research; effort low for verifier and inbox via pick_effort; choice recorded in loop config (MAIN 2246 sec 2) | [ ] open |
| 100 | Perf 2.8: governor counts runs from headless_usage.jsonl (or reconciles headless_budget.json at tick start); backfill kind build on the 34 rows lacking kind or exclude pre-v8 rows (MAIN 2246 sec 2) | [ ] open |
| 101 | Perf 2.9: headless lanes work inline - drop the background agents share one scratchpad clause from headless GATES prompts (MAIN 2246 sec 2) | [ ] open |

Deep dive 2026-10-09 (research 0017): one plan. 095 - the live Marni event
(10673, to 11-05) pays seals by login, 60-min play and weekly games against
a limited exchange, and the shape repeats every season. Number 094 is left
unused: the My Information EXP-table candidate was dropped at refute round
1 as a duplicate of the Progress tab (004; leveling card 011, OCR 066).
Data items (no plan): My Information absolute EXP (10678) for the 004 /
011 / 066 leveling card; Twitch drops Fri-Sun only, 12:00 UTC reset, claim
by 11-19 (006 / 086); quest anchors Lv 56 / 60 / 61; Godslayer: Force Palm
(034); Lv 75 pace target. Dropped: My Information EXP table (dup of 004),
per-level class stat card (0006), BDOVision / Fashion Show trackers.

Deep dive 2026-10-08 (research 0015): two plans. Ranking: 087 first - NA
patch notes post today and the shipped 085 hints miss the official
wording (U+2192 arrows, "Adventure's Boon"), so it would confirm nothing,
while the tracked 62-75 per-kill cap band is already wrong (62-64 is
0.02 %); 088 second - the new in-game Monster Zone Info panel is the only
per-kill EXP source and from Lv 56 the buff-inclusive cap decides spot
choice and buff spending. Data items (no plan): 14 cap bands, Lv 70-75
AP / DR +3 per level, level-gap DR max +9, "Adventure's Boon" alias, no
Bio area in My Information (066). Dropped: Asia board as a runtime source,
patch-note loss lines into 074, pre-revamp EXP tables.

Deep dive 2026-10-07 (research 0013): two plans. Ranking: 085 first -
the 2026-10-08 patch notes confirm or contradict the level-cap epoch, the
XP-buff presets and the cap bands, and 60 data rows sit `verified: false`
with no reader. 086 second - Combat Special claims close 2026-10-15 and
coupon mail 2026-10-29, later than the events' own ends, from pages plan
064 already reads. Data items (no plan): XP buff presets 100 / 30 / 50 %
(018), loot keyed by spot id (039 / 040). Dropped: kill-milestone tracker
(no kill signal), arsha WebSocket (F22 again), Black Desert+ (authenticated).

Deep dive 2026-10-06b (research 0008): two plans. 074 first (priority) -
the official 2026-10-08 maintenance notice says unclaimed Tag Characters
EXP is deleted at the update and EW showed neither that nor the events
ending at the maintenance; it reuses plan 064's Detail GETs (no new GET).
075 second - 14-day Special Login Reward (to 2026-10-28) and the four-week
Dream Horse login event need a per-event count of qualifying days from the
plan 008 / 056 logged-in signal. Data items (no plan): Lv 70+ monster AP /
DR and level-gap DR rows (018 / 023 / 012), HYPERBOOST track seed for 034
once an official stage list is read, My Info EXP OCR region for 066.
Dropped: "All Events at glance" single-page index (064 cap suffices),
Deadeye patch tracker (dup 007), Season graduation countdown (dup 024).

UI/UX audit 2026-10-06 (research 0009): 21 findings, three consolidation
plans, no new features, no dependency between them (any lane order). 076:
Home shows What now truncated, the same boss / reset fact in 3-4 cards and
6 empty cards. 077: three surfaces disagree about one signal and a Get
started link dead-ends (smallest, highest correctness value). 078 (Settings duplicate overlay controls with a manual-mode default bug,
raw identifier labels, remaining UTC entry, palette button). 047 and 050
are extended only for gaps found live. Not planned: shared snapshot store
(0009 M9), overlay base rows by context (L3).

Portrait research 2026-10-06 (research 0011, operator request session
10): the game writes the character portrait to Documents\Black
Desert\FaceTexture\<characterNo>.bmp (624 x 804); the session log names
the characterNo on each character load; class is bound per characterNo
(single-character rule today, OCR class read later). Adjudicator: FaceTexture
reads allowed like ScreenShot (short handle, never written). 082 first -
archive + binding + top-left class chip (empty frame when the class has no
portrait, never another class). 083 after 079 and 082 - Deadeye gallery
(history + attributed screenshots), optional pick as a 079 override
retired by a newer portrait.

Zero-touch audit 2026-10-06 (research 0010, operator order: self-adjusting
EW, no toggles or typed data that get forgotten): 29 findings (4 H, 10 M,
15 L). 079 first (priority) - the override ledger every other change writes
through: source + set-at + expiry on any operator value that changes
output, superseded by a live signal, badged on the card and listed in the
073 digest (fixes `market.vp` silently inflating Grind / Crafting net
proceeds). 080 and 081 depend on 079, not on each other: 080 deletes the
seven automation kill switches, the manual overlay layout, per-rule notify
opt-ins (four defaulted off) and tunables; 081 derives silver, hours/day,
inventory weight, CP, fame, loot counts from OCR / session log. 078
re-scoped: no new manual entry (local-time display only, no manual-layout
sub-group). 077 unchanged (no conflict).

Autonomy deep dive 2026-10-06 (research 0007, operator order: self-aware,
low-input EW): 13 plans 061-073 ranked by operator inputs removed per play
session vs build cost. Priority: 061 first - the shipped profile default
calls a robots-disallowed host (floor defect); then 062 auto session and
063 auto-OCR (the two biggest per-session input sinks), 064 notice
auto-import (weekly typing), 065 zero-config paths (unblocks 062/063 on a
fresh box). 066 waits on 063 (Depends on). Every plan uses only the ToS
floor's passive signals (session log, saved screenshots + OCR, arsha GETs,
robots-allowed public pages, clock, stored data). Dedupe vs 001-060: 059
parser reused by 064; 046 prompt superseded by 062 for auto sessions; 041
auto level sample replaced by 066; 025 Home hosts 069. Rejected: input-idle
AFK detection (needs an input hook), live window capture, clipboard reads,
roster OCR, auto-tick of redeemed coupons.

Deep dive 2026-10-06 (research 0006): two plans. Ranking: 059 first - pays
off every week from today, removes the biggest manual-typing chore on the
Events tab and reuses plan 014's proven robots-gated fetcher; 060 second -
high value once the operator is Lv 60+ and the post-patch Lv 62+ per-kill
cap (~0.01 %) makes books a main XP source, but books only exist from the
2026-10-15 maintenance. Rejected: Steam news feed (api.steampowered.com
robots.txt disallows `/`), level-gap helper (plan 018 steps 5-6), per-level
stat card (sheet AP already includes it). Data items (no plan): 6-49
per-kill cap bands into `xp_epochs.json`; verify the plan 018 epoch after
the 2026-10-08 patch notes.

Deep dive + UX audit 2026-10-05 session 4 (research 0003-0005): plans 019-057,
39 plans from 21 + 25 + 15 candidates and 30 UX findings. Ordering rationale:
019 first because the loop dispatches 3 lanes with no dependency order and
later plans carry `Depends on:` lines (each dependent plan also carries a
code-presence guard until 019 lands). Then the UX correctness fix that makes
the dashboard stop lying about health (020), the Sunday-reset model that 033
and 056 build on (021), the overlay off the minimap (022), and the two
progression wins with verified data and a hard date (023 AP/DP brackets, 024
Olvia enrolment closes 2026-11-05). After the priority rows: glance surfaces
(025 Home, 026 notifications), small market wins (027-029), settings (030),
world bosses (031-032), gear-gated weekly plan and roadmap (033-034), the
enhancement chain (035-037), loot economy (038-040), profile-API trackers
(041-042), rosters (043-045), then UX polish and lower-value calculators
(046-057). Dropped as duplicates of 001-018: 0003 C20 (018 milestones),
0003 C21 (007 notes), 0003 C08 (018 presets), 0004 F22 (research 0002 s5),
0004 F23 (002 hot list). Deferred (low value, L effort or blocked source):
0003 C15 next-upgrade value ranker (L; revisit once 023 + 035 + 037 land),
0003 C18 carrack, 0003 C19 war hours (unverified), 0004 F18 treasure
tracker, 0004 F19 Marni's Stone, 0004 F20 barter log, 0004 F21 registration
queue (arsha /queue Imperva-blocked), 0004 F25 worker empire (L). Merged:
0003 C07 + C11 into 034, 0004 F05 + F06 + F07 into 036, 0004 F08 + F12 into
039, 0004 F09 + F10 into 038, 0003 C13 + C14 and 0004 F24 into 055, 0005
F7/F8 into 050. Blocked lanes: a dependent plan dispatched early writes a
`blocked` marker; plan 019 adds the `blocked` item state that re-dispatches
it once its Depends-on rows are `[x]`.

Deep dive 2026-10-05 (research 0002): one plan, 018 - the Global Lab level
cap 75 / XP rescale can go live at the 2026-10-08 maintenance and would make
EW reject level 71+ and mislead the level ETA. A proposed 019 (patch-notes
watcher) was dropped in refute round 2/3 as a duplicate of the Deadeye tab
(007); see research 0002 section 7.

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

## Order Na22de8 - blocked items

Done in-lane: C: path inventory (inventory only, nothing changed) in
`docs/research/0014-c-drive-path-inventory-2026-10-07.md`; the loop writes
the ONE ANSWER (HOP 2) from it after merge.

- Live read-back of tasks `\EbonwakeOps\LaneLoop` and `\Ebonwake`: BLOCKED - schtasks / `tools/loop_task.py status` outside this lane's allow list; rows inferred from the installer code.
- Desktop shortcuts and EW env variable values: BLOCKED - Desktop folder and Env: provider outside this lane's allow list; rows inferred from code.
- Main checkout `config/local.json` and `.git/worktrees/*/gitdir`: BLOCKED - main checkout outside this lane's read grant; worktree copy read instead.

## Order N67971f - blocked items

Plan: `docs/plans/089-fleet-kit-v9-done-marker.md` (As-built deviations 1-7).

ORDER OPEN: acceptance is not met until the main session does items 1, 2, 3, 5
and 6. Merging lane-0 does not close it.

Done in-lane: `.gitignore` names `ops/loop/control/session_done.json` and
`ops/loop/control/session_done.seen` explicitly (already covered by
`ops/loop/control/*`). Lane-1 / lane-2 HEADs (44d1006, e1d71be) have no
commits outside main (read with `git log main..<sha>`).

- Item 1 vendor kit v9 + re-embed FLEET-COMMON item 15 in CLAUDE.md: BLOCKED - kit bundle sits in the main checkout inbox, outside this lane's read grant; lane brief also forbids editing CLAUDE.md and `ops/fleet_kit/`. Main session vendors all 16 files in one commit and re-pins `tests/test_fleet_kit_conformance.py` to v9.
- Item 2 /done last act `fleet_done.py mark`: BLOCKED - `.claude/commands/done.md` outside this lane's write grant; also needs item 1 first.
- Item 3 Stop hook + remove `spinnerTipsEnabled` from `.claude/settings.json`: BLOCKED - `.claude/` outside this lane's write grant; the Stop hook must wait for item 1 (a missing script exits non-zero on Stop).
- Item 5 MIG-1 worktree prune (`git worktree list` = 4 before): BLOCKED - `git -C` status of lane-1/2, `git worktree remove/prune` and Recycle Bin moves outside this lane's allow list; lane-0 is held by this lane and stays. Lane-1/2 locks read FREE, HEADs contain nothing unmerged; uncommitted state unread.
- Item 6 deliver the 2230 ANSWER to ORDER 2155: BLOCKED - outbox outside this lane's read grant; the loop carries it in the HOP 2 ANSWER.

## Order N94ff08 - closed

CLOSED 2026-10-08 by the main-checkout commit "kit v10: vendor, re-embed
FLEET-COMMON, re-pin conformance, subagent-first hook (log), /done dispatch,
emit()" on top of merge 126cfc7 (lane WIP 0bbc472 merged as a partial;
adjudicated, refute-rounds 3/3). Plan and as-built deviations:
`docs/plans/090-fleet-kit-v10-subagent-first.md`.

- Item 1 DONE: kit v10 vendored, 17/17 files match MANIFEST.json; FLEET-COMMON block re-embedded (block sha256 matches); `tests/test_fleet_kit_conformance.py` pinned to v10.
- Item 2 DONE: PreToolUse `python ops/fleet_kit/fleet_subagent_first.py` (matcher `Bash|PowerShell|Read|Edit|Write|Grep|Glob|NotebookEdit|MultiEdit`, timeout 10) in `.claude/settings.json`, wired after the kit file landed.
- Item 3 DONE: main checkout `ops/loop/control/subagent_first.mode` = `log`, written before the hook; both files gitignored. Deny target 2026-10-11 (3 clean interactive sessions).
- Item 4 DONE: `.claude/commands/done.md` DISPATCH line (whole ritual to ONE background sub-agent).
- Item 5 DONE: `tools/ew_loop.py checklist` prints via kit `fleet_checklist.emit()`; headless paths were already on `spawn()`.
- Item 6 DONE: ONE ANSWER (HOP 2) to MAIN after push.
- Routing defect filed as ROADMAP row 091.
