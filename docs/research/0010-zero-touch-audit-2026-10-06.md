# 0010 - Zero-touch audit: every operator input and toggle (2026-10-06)

Status: research, read-only audit. No app code changed.
Trigger: operator order 2026-10-06 (session 10): "EW must be self-adjusting
and update the tabs from the live game. Operator attention is precious -
toggling things and entering information is NOT wanted, because a changed
setting gets forgotten and silently affects the output."
Scope: every input, select, textarea and checkbox in `app/dashboard/*.js`,
the Settings allowlist `server/ew/settings.py` SPEC (54 keys), every key in
`config/local.example.json`, the POST routes behind them, and the stores
that persist the typed values. Line numbers are against HEAD 36172f2.

Method:
1. grep for `el('input' | 'select' | 'textarea')` per tab module (100 sites
   in 16 files), for `.post('/api/...` (23 call sites, 16 routes) and for
   config / settings reads in `server/ew/*.py`.
2. For each input: what it changes, whether it persists, whether the value
   changes output elsewhere, and which allowed live signal can provide it:
   session log tail (008, 062), auto-OCR of screenshots (063) and its
   inference (066: level, XP %, silver, AP / AAP / DP, buffs, book use),
   official notice auto-import (064), arsha.io market GETs (071), the
   context engine (067), the clock, and data EW already stores.
3. Classified as DERIVE (a live signal provides it), DEFAULT (a fixed
   sensible value, control deleted), KEEP-RECORD (operator content or a
   time-stamped record, not an override) or OVERRIDE (kept, but under the
   rule in section 3).

## 1. Headline

- 29 findings: 4 High, 10 Medium, 15 Low.
- The dangerous class is the sticky setting that silently changes numbers
  on other tabs. Worst case: `market.vp` (Settings) is read straight into
  every net-proceeds figure on Grind loot value, Crafting margins and the
  sell-or-vendor hints (`server/ew/app.py:767`), while Inventory already
  prefers an armed "Value Pack" buff timer (`inventory.py:246-260`). Two
  tabs can disagree, and a VP that lapsed months ago keeps inflating every
  silver figure by 30 percent with nothing on screen saying so.
- Seven automation kill switches (`ocr.auto`, `play.auto_session`,
  `notices.auto_add`, `events.notice_check`, `coupons.check`,
  `checklist.auto`, `overlay.auto`) are one click each in Settings. Any one
  left off silently turns a self-adjusting tab back into a manual one.
- Four notification rules the operator plausibly wants (`bossSoon`,
  `hotTime`, `resetSoon`, `newCoupon`) default OFF, so zero config means
  no boss / reset alerts - the opposite of the order.
- Good news: the provenance pattern already exists in two places. Gear AP /
  DP carry `gs_src = {source: typed|ocr, at}` and OCR supersedes typed
  (`progress.py:1169-1206`); leveling samples carry `source` (typed / ocr /
  marker). Plan 079 generalises that pattern instead of inventing one.

## 2. Findings

Columns: ID, where, what the operator types / flips today, live source that
can replace it, zero-touch replacement, plan.

### High

| ID | Where | Today | Live source | Replacement | Plan |
|---|---|---|---|---|---|
| H1 | Settings `market.vp`; `app.py:767`; `inventory.py:246` | Boolean, no expiry; drives net proceeds on Grind, Crafting, sell-or-vendor, Inventory fallback | 066 buff OCR / plan 005 "Value Pack" timer (30 d) | DERIVE from the VP timer only; a typed VP becomes an override that expires at set_at + 30 d; one resolver for every tab | 079 |
| H2 | Settings Overlay `overlay.widgets.*` (9 booleans) + `overlay.auto` | Manual layout used when context is off; server reads missing as off (0009 M1) | 067 context engine | DELETE manual layout; context always on; pins / blocks are overrides (M2) | 080 |
| H3 | Settings kill switches: `ocr.auto`, `play.auto_session`, `notices.auto_add`, `events.notice_check`, `coupons.check`, `checklist.auto`, `overlay.auto` | One click turns automation off forever, no badge | n/a (policy) | DELETE from Settings; per-item undo already exists (064, 068, 063 review queue). Config-only incident switch kept, shown as an override, expires after 24 h | 080 |
| H4 | Settings `notify.*` (8 rule toggles, 4 off by default) | Opt-in per rule; zero config = no boss / reset / Hot Time alerts | 070 ladder + game-closed quiet, 067 context | DEFAULT: one fixed policy (all rules on while logged in, 070 quiet while closed); per-rule toggles deleted; one "mute until" override (expires, max 24 h) | 080 |

### Medium

| ID | Where | Today | Live source | Replacement | Plan |
|---|---|---|---|---|---|
| M1 | Settings `events.maintenance_start_utc` | Typed UTC start overrides the tracked slot for every week without a notice, no expiry | 064 maintenance notices (beat the slot per date) | OVERRIDE: expires at the end of the next maintenance it applied to; superseded the moment a notice for that date lands | 079 |
| M2 | Settings `overlay.mode.*` pin / block | A block hides a widget forever | 067 context | OVERRIDE: badge on the overlay row + digest list; expires after 7 d unless re-set | 079 |
| M3 | Settings `bdo.install_dir`, `bdo.documents_dir` | Typed path beats auto-detect forever | 065 detection | OVERRIDE: superseded when the typed path stops existing and detection finds one; badge "using typed folder" | 079 |
| M4 | Settings `market.fame_pct` | Typed 0-1.5 percent; changes every net figure | OCR of the market sell dialog (fame bonus line) when a screenshot shows it | DERIVE when read; typed value is an override (no expiry, fame only rises) but always badged with set_at | 081 |
| M5 | Deadeye shopping `silver_on_hand` (`deadeye.js:545`, `shopping.py:82`) | Typed silver; goes stale within a session | 066 silver OCR | DERIVE latest OCR silver (with its age); typed value superseded by the next OCR read | 081 |
| M6 | Deadeye shopping `hours_per_day` (`deadeye.js:551`) | Typed hours; drives every "days to afford" ETA | 062 play sessions (session log) | DERIVE 14-day median logged-in hours / day; typed value superseded once 3 sessions exist | 081 |
| M7 | Inventory plan `base_lt`, `slots`, `slots_used`, `fame_vt` (`inventory.py:42`) | Typed; `slots_used` stale after every grind | OCR of the inventory window weight / slot readout (new region in `data/ocr_regions.json`) | DERIVE from OCR when present; typed value badged with age | 081 |
| M8 | Leveling Hot Time windows (`leveling.js:331-360`) | Typed recurring weekday windows, no end date | 064 notice Hot Time windows (auto, sourced) | OVERRIDE: a typed window needs an end (default next maintenance); a notice window covering it supersedes | 081 |
| M9 | Grind loot counts (`grind.js:406`) | Typed per item per session | 063 auto-OCR loot / inventory screenshots via the review queue | DERIVE prefill from the session's OCR reads; typing only corrects | 081 |
| M10 | Imperial CP (`imperial.js:91`, "blank = from profile") | Typed CP; profile source is off by default (061) | OCR of the CP readout (066 contract, new region) | DERIVE from OCR; typed value is an override superseded by OCR | 081 |

### Low

| ID | Where | Today | Replacement | Plan |
|---|---|---|---|---|
| L1 | Settings tunables `ocr.auto_commit_min`, `ocr.daily_cap`, `play.grace_s`, `overlay.idle_min`, `notify.ladder_min`, `notify.quiet_closed` | DEFAULT; delete from Settings (config file only, badged if changed) | 080 |
| L2 | Config `ocr.engine`, `ocr.tesseract` | DEFAULT auto; delete `engine` from the example; tesseract path auto-detected | 080 |
| L3 | Settings `ui.theme` (default `dark`) | DERIVE: default `system` (follow OS) | 080 |
| L4 | Settings `ui.scale` | DEFAULT 1.0; kept, badged when not 1.0 | 080 |
| L5 | Settings overlay `anchor`, `display`, `scale`, `opacity` | KEEP as placement (does not change any number); fixed defaults | - |
| L6 | Settings `hotkeys.*` | KEEP (input binding, not output) | - |
| L7 | `profile.family`, `profile.base_url`; example config base `127.0.0.1:8001` vs SPEC default `""` | KEEP identity; align example with SPEC (blank = off) | 080 |
| L8 | Config `market_watch` list | Superseded by 071 auto-watch; delete from the example | 080 |
| L9 | Leveling typed level / XP sample | KEEP-RECORD; already superseded by 066 OCR samples | - |
| L10 | Progress gear AP / AAP / DP typed | Already compliant (`gs_src`); reference pattern for 079 | 079 |
| L11 | Today custom reset `HH:MM UTC` (`today.js:457-461`) | KEEP-RECORD; show local time, no new entry control (078 re-scoped) | 078 |
| L12 | Deadeye enhance planner fs / crons / family per step | KEEP as labelled planner assumptions (family already has set / guess source) | - |
| L13 | Grind manual session form, buff minutes / xp % | KEEP-RECORD; 062 auto session and 066 buffs are primary | - |
| L14 | Content records: pets, crafting recipes, mounts, Deadeye notes, events add / done, progress steps, T10 mats, books add / use, epochs, milestones | KEEP-RECORD (operator-owned content, time-stamped; no hidden effect) | - |
| L15 | Market manual watch thresholds | KEEP-RECORD; 071 thresholds are the default, a typed one is badged | 079 |

## 3. Rule: no forgotten overrides

Any operator-set value that changes output elsewhere is an OVERRIDE and
obeys all of these. Content records (L14) and placement (L5, L6) are not.

1. Zero config is the product. Every key has a derived value or a fixed
   default that produces correct output with no Settings visit.
2. Resolution order, one resolver per key: live signal (fresh, inside its
   confidence gate) > unexpired override > default. A live signal arriving
   SUPERSEDES the override: the override is retired (not deleted - kept in
   the ledger with `retired_by`) and the card shows the live value.
3. Every override is stored with `{value, source: typed|config, set_at,
   expires_at | null, reason}`. `expires_at` is required unless the key is
   on a short no-expiry list (fame bonus, folders, identity) - and those are
   still badged.
4. Visible where it acts: every card whose output an active override
   changes shows an `override` badge (title: key label, value, set-at local
   time, expiry, "clear" action). The 073 signal digest gets an Overrides
   section listing every active override; Home's status line counts them.
5. Expiry defaults: VP 30 d, pin / block 7 d, mute 24 h max, incident kill
   switch 24 h, maintenance start until that maintenance ends, Hot Time
   window until the next maintenance, typed folder until it stops existing.
6. Config-file values are overrides too: a key in `config/local.json` that
   differs from its default is listed with `source: config`, set_at = the
   file's mtime when first seen.

## 4. Plans

| Plan | Title | Covers | Effort |
|---|---|---|---|
| 079 (priority) | Override ledger: source, set-at, expiry, supersede-by-live-signal, badges and digest list | H1, M1, M2, M3, L10, L15 + the rule | M |
| 080 | Settings purge: delete kill switches, manual overlay layout, per-rule notify toggles and tunables; OS theme | H2, H3, H4, L1, L2, L3, L4, L7, L8 | M |
| 081 | Derive the typed planner inputs: silver, hours / day, inventory weight / slots, CP, fame, loot counts, Hot Time ends | M4-M10 | M-L |

Order: 079 first - 080 and 081 both write through its ledger. 080 and 081
are independent of each other.

Effect on open plans (lanes are building 074-076; untouched):
- 078 re-scoped: no new manual entry. Dropped: local-time ENTRY for the
  Today custom reset and `events.maintenance_start_utc` (the field goes
  away in 080, the value comes from 064 notices, and the reset shows its
  stored UTC in local time), and the "Manual layout (context off)"
  sub-group (080 deletes manual layout). Kept: human labels (079 badges and
  the override list reuse `LABELS`), one row per overlay widget, the
  context.py default-on fix (cheap regression guard until 080 lands), zero
  pills, palette button, tab strip.
- 077: no conflict - it already derives onboarding, profile and stale
  styling from the 073 digest and adds no control. Its "Documents not set"
  Settings link remains the one sanctioned typed fallback (M3 badge applies).

## 5. ToS check

Every replacement reads only the floor's allowed inputs: session log tail
(running / login / disconnect), the ScreenShot folder + OCR of screenshots
the operator saved, arsha.io GETs, robots-allowed official notices, the
clock, and EW's own store. New OCR regions (inventory weight, CP, fame
bonus) crop saved screenshots only. Nothing reads game memory or sends
input to the game window.

## 6. Self-critique

Round 1: deleting the kill switches removes the operator's way to stop a
misbehaving automation - answered: per-item undo already exists (064 auto
add, 068 auto-tick, 063 review queue) and an incident switch survives in
config, shown and expiring after 24 h. Round 2: defaulting all notification
rules on could be noisy - answered: 070's dedupe, expiry and game-closed
quiet already govern volume; a 24 h mute remains. Round 3 not needed.
refute-rounds: 2/3.
