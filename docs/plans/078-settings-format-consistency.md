# Plan 078 - Settings and formatting consistency: one control per overlay widget, human labels, local-time entry, palette button

Status: open. UI/UX audit 2026-10-06 (research 0009 M1, M2, M3, M7, M8, L2, L5, L7). Lane hint: `build`.

Spec gap: Settings (030) grew to 11 groups and 54 inputs. The Overlay group
shows 9 `Widget: grindSession` booleans that only apply when context mode
(067) is off - and when it is off the server reads a missing boolean as off
while the renderer default is on, so the overlay blanks. Notification
toggles are raw identifiers (`marketAlert`). Plan 048 moved time entry to
local time, but three UTC fields remain. The command palette (050) is the
cheapest input saver and nothing in the UI mentions Ctrl+K.

1. `app/shared/ewcore.js`:
   - `LABELS`: human names for the 9 overlay widgets, 8 notification rules
     and the config keys onboarding hints cite (e.g. `whatNow` -> "What
     now", `bdo.documents_dir` -> "Game folders > BDO Documents folder").
     Settings and onboarding hints use it.
   - Overlay group: one row per widget, a select `auto / always / never`
     (stored values stay `auto | pin | block` under `overlay.mode.*`); the
     `overlay.widgets.*` booleans move under a collapsed "Manual layout
     (context off)" sub-group shown only while `overlay.auto` is false.
   - `zeroPill(n, noun)` -> '' at zero (wraps `countText`).
2. `server/ew/context.py:170`: manual widgets use the renderer's default-on
   semantics (`overlayWidgets`): missing key -> default for that widget.
3. Local time: Today custom reset (`app/dashboard/today.js:456-461`) and
   Settings `events.maintenance_start_utc` take local `HH:MM` through
   `C.parseLocalTime` / `C.utcClockIn` (stored UTC unchanged, UTC on
   hover); System `started` via `C.fmtLocal`.
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
   - `app/test/today_resets.test.js`: local `HH:MM` in a UTC-7 zone ->
     stored UTC clock; invalid text rejected.
   - `app/test/shell.test.js`: palette button present and opens the palette.
   - `tests/test_context.py`: auto off with no booleans stored -> the
     default-on widgets show (regression for 0009 M1).
   - Self-test: top bar fits 960 px at scale 1.3 (pill fully visible).

Acceptance: Settings Overlay group shows 9 widget rows plus anchor / display
/ scale / opacity / context / idle; no label is a code identifier; turning
context mode off keeps the default widgets; no remaining `HH:MM UTC` entry
in the dashboard; gates green; verifier PASS within 3 rounds; one push.

ToS check: dashboard-only labels and inputs; the palette button opens the
existing in-window palette (no global hotkey, nothing reaches the game).

Depends on: 030, 048, 050, 067.

Dependency guard: before writing code the lane checks that
`app/dashboard/settings.js` (plan 030), `app/dashboard/palette.js` (plan
050) and `server/ew/context.py` (plan 067) exist. If any is missing, the
lane changes nothing, writes `"status": "blocked", "needs": ["030", "050",
"067"]` into its progress JSON (`ops/loop/control/progress/p078-build.json`)
and exits 0.
