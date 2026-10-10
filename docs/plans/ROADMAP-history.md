# Ebonwake roadmap - history

Moved out of `docs/plans/ROADMAP.md` on 2026-10-09 by the repo review (plan
102, research 0018) so the roadmap stays the live table. The text below is
verbatim except the Order N67971f status line (marked CLOSED, original
quoted). Ranking rationale per deep dive / audit, newest first, then the
closed channel-order sections. The loop does not read this file
(`tools/ew_loop.py` reads only ROADMAP table rows and appends open order
sections to the roadmap itself).

## Ranking rationale

Deep dive 2026-10-10 (research 0020): no new plan. Nothing posted after the
10-08 patch; a Black Spirit's Scheduler mirror is a duplicate of 003 / 025 /
069; Altar of Blood and Event Horizon rows are data items (033, 012).

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

## Closed orders

## Order N715c44 - blocked items

Done in-lane: item-14 inbox rules folded into the loop tick (`tools/ew_inbox.py`,
`tools/ew_loop.py`; triage sonnet/low/bare, ack = ledger line, 6 notes a day,
HOP lines, batch notes, kind build/inbox/triage); plan 015 deviation 13. No
responder task existed, none disabled.

- DONE (kit-v8 branch, main session): kit v8 vendored into `ops/fleet_kit/` (12/12 hashes vs MAIN outbox, one commit), FLEET-COMMON block re-embedded, conformance test pinned to v8; the tick now calls the kit's `fleet_inbox` directly and `tools/ew_inbox.py` is removed (plan 015 deviation 14).

## Order Na22de8 - blocked items

Done in-lane: C: path inventory (inventory only, nothing changed) in
`docs/research/0014-c-drive-path-inventory-2026-10-07.md`; the loop writes
the ONE ANSWER (HOP 2) from it after merge.

- Live read-back of tasks `\EbonwakeOps\LaneLoop` and `\Ebonwake`: BLOCKED - schtasks / `tools/loop_task.py status` outside this lane's allow list; rows inferred from the installer code.
- Desktop shortcuts and EW env variable values: BLOCKED - Desktop folder and Env: provider outside this lane's allow list; rows inferred from code.
- Main checkout `config/local.json` and `.git/worktrees/*/gitdir`: BLOCKED - main checkout outside this lane's read grant; worktree copy read instead.

## Order N67971f - blocked items

Plan: `docs/plans/089-fleet-kit-v9-done-marker.md` (As-built deviations 1-7).

CLOSED (2026-10-09 repo review): the session did items 1, 2, 3, 5 and 6 -
kit v9 vendored in 3eb2d01 after merge ca1e51a, MIG-1 prune and the HOP 2
ANSWER recorded in EW-NEXT-SESSION.txt (session 17 / N67971f bullets). The
original line read: "ORDER OPEN: acceptance is not met until the main
session does items 1, 2, 3, 5 and 6. Merging lane-0 does not close it."

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
