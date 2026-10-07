# Plan 078 - Settings and formatting consistency: one control per overlay widget, human labels, local-time display, palette button

Status: open. UI/UX audit 2026-10-06 (research 0009 M1, M2, M3, M7, M8, L2, L5, L7). Lane hint: `build`.

Re-scoped 2026-10-06 by research 0010 (zero-touch audit, operator order: no
new manual controls). Dropped: local-time ENTRY fields and the "Manual
layout (context off)" sub-group - plan 080 deletes manual layout and
`events.maintenance_start_utc` from Settings (the value comes from 064
notices, plan 079 ledger). Kept: labels, one row per widget, the context.py
default fix, local-time DISPLAY, zero pills, palette button, tab strip.

Spec gap: Settings (030) grew to 11 groups and 54 inputs. The Overlay group
shows 9 `Widget: grindSession` booleans that only apply when context mode
(067) is off - and when it is off the server reads a missing boolean as off
while the renderer default is on, so the overlay blanks. Notification
toggles are raw identifiers (`marketAlert`). Plan 048 moved time entry to
local time, but three UTC fields remain. The command palette (050) is the
cheapest input saver and nothing in the UI mentions Ctrl+K. This plan adds
no new input field (research 0010).

1. `app/shared/ewcore.js`:
   - `LABELS`: human names for the 9 overlay widgets, 8 notification rules
     and the config keys onboarding hints cite (e.g. `whatNow` -> "What
     now", `bdo.documents_dir` -> "Game folders > BDO Documents folder").
     Settings and onboarding hints use it.
   - Overlay group: one row per widget, a select `auto / always / never`
     (stored values stay `auto | pin | block` under `overlay.mode.*`); the
     `overlay.widgets.*` booleans are no longer rendered (no manual-layout
     sub-group; plan 080 removes the keys).
   - `zeroPill(n, noun)` -> '' at zero (wraps `countText`).
2. `server/ew/context.py:170`: manual widgets use the renderer's default-on
   semantics (`overlayWidgets`): missing key -> default for that widget.
3. Local time DISPLAY only (no new entry control): Today custom reset rows
   and the effective maintenance start render through `C.fmtLocal` (stored
   UTC unchanged, UTC on hover); System `started` via `C.fmtLocal`. The
   existing `HH:MM UTC` entry fields are left as they are (080 removes the
   maintenance one; the custom reset field is operator content).
4. Zero pills: Grind `0 sessions`, Events `0 open` / `0 items` use
   `zeroPill` (`app/dashboard/grind.js:346`, `events.js:186,343`).
5. Shell (`app/dashboard/index.html`, `dashboard.js`, `ew.css`): a `Ctrl+K`
   button in the top bar opens the palette (title lists the verbs); tab
   strip scrolls horizontally instead of clipping, badges `nowrap`, so the
   960 px minimum window at `ui.scale` 1.3 shows the server pill; add the
   viewport meta. Events Add card moves last. Palette jump outline renamed
   `.ew-jump` (duplicate `.ew-hit`).
6. Tests:
   - `node --test` `app/test/settings.test.js`: every Settings field label
     is free of camelCase identifiers; overlay group has exactly one row
     per widget in auto mode; mode values round-trip.
   - `app/test/today_resets.test.js`: a stored UTC reset clock renders as
     local `HH:MM` in a UTC-7 zone, UTC in the title.
   - `app/test/shell.test.js`: palette button present and opens the palette.
   - `tests/test_context.py`: auto off with no booleans stored -> the
     default-on widgets show (regression for 0009 M1).
   - Self-test: top bar fits 960 px at scale 1.3 (pill fully visible).

Acceptance: Settings Overlay group shows 9 widget rows plus anchor / display
/ scale / opacity / context / idle; no label is a code identifier; turning
context mode off keeps the default widgets; no new input field added;
stored UTC clocks display in local time; gates green; verifier PASS within
3 rounds; one push.

ToS check: dashboard-only labels and inputs; the palette button opens the
existing in-window palette (no global hotkey, nothing reaches the game).

Depends on: 030, 048, 050, 067.

## As-built deviations

1. Manual booleans kept as hidden fields. Decision: `overlay.widgets.*`
   stay in `SETTINGS_GROUPS` with `hidden: true` (allowlisted, validated,
   human labels) and `C.settingsRows(g)` drops them from the form.
   Alternatives: delete the fields client-side (breaks the client / server
   allowlist parity test and the bridge would refuse a key the server still
   accepts). Why: plan 080 owns removing the keys on both sides at once.
   Reverses if: plan 080 lands (it deletes the hidden fields).
2. Overlay group order: the 9 widget rows lead, then anchor / display /
   scale / opacity / context / idle. Alternatives: rows after the overlay
   settings (the old order). Why: the acceptance lists the widget rows
   first and they are the most-changed control. Reverses if: operator QA
   asks for the old order.
3. `zeroPill(n, one, many)` does not call `countText`; it shares its zero
   state ('' at zero / junk) but prints 'n noun', not 'done/total'.
   Alternatives: `countText(n, n, noun)` ('3/3 sessions', wrong shape).
   Why: the pill is a count, not a ratio. Empty pills are hidden by CSS
   `.ew-pill:empty`. Reverses if: never (shape fix only).
4. Effective maintenance start in local time: shown beside the Settings
   `HH:MM UTC` field only when a start is stored (`C.settingLocalNote`,
   via `utcClockIn` - it is a clock, not a timestamp, so `fmtLocal` does
   not apply). Blank shows the existing 'default blank' note. Alternatives:
   mirror the server's default slot (07:00 UTC) in the client (a second copy
   that drifts) or add the slot to GET /api/settings (server scope growth
   for a field plan 080 deletes). Reverses if: plan 080 moves the slot to
   a read-only display, which should use `C.settingLocalNote`'s shape.
5. Custom reset rows: `C.resetCountdownView` shows the zone's weekday and
   HH:MM of the NEXT reset instant (DST-correct), UTC rule on hover; the
   old `fmtResetCountdown` stays as the fallback and for preset labels
   (presets feed the existing UTC entry fields). Reverses if: the reset
   entry moves to local time.
6. Onboarding hints: the server keeps citing config keys; the client maps
   them through `C.labelHint` (LABELS) in `onboardingRows`. Alternatives:
   edit `server/ew/onboarding.py` strings. Why: one label table, and the
   `link.field` keys stay machine-readable. Reverses if: the server grows a
   label table of its own.
7. Self-test top bar: `selftest.js` resizes the dashboard to 960 px content
   width and zoom 1.3, measures the server pill and top-bar overflow, then
   restores size and zoom; `res.ok` fails on a clipped pill. Reverses if:
   the minimum window or `UI_SCALE` max changes (update `TOPBAR_SIZE`).
8. The notification group has 10 rules (plan 075 added loginRisk, 074
   maintLoss), not 8; all 10 have labels.

Dependency guard: before writing code the lane checks that
`app/dashboard/settings.js` (plan 030), `app/dashboard/palette.js` (plan
050) and `server/ew/context.py` (plan 067) exist. If any is missing, the
lane changes nothing, writes `"status": "blocked", "needs": ["030", "050",
"067"]` into its progress JSON (`ops/loop/control/progress/p078-build.json`)
and exits 0.
