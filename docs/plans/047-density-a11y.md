# Plan 047 - Density and accessibility rework: content-sized cards, focus-visible, keyboard tabs, tab badges

Status: open. Deep dive + UX audit 2026-10-05 session 4 (research 0003-0005). Lane hint: `build`.

Spec gap: research 0005 candidate F10 (value 3, S) with findings M1 (cards
stretch, tabs 50-70 percent empty), M3 (no focus indication, no tab roles /
arrow keys, mouse-only rows), L4 (11 px delete targets), L5 (zero-state
noise), L8 (colour-only cues) and L9 (no tab badges, System weighted like a
daily tab).

1. `app/shared/ew.css`: `grid-auto-rows: min-content`, `align-content:
   start`, per-card `max-height` with list cards `grid-row: span 2`;
   global `:focus-visible` outline; delete buttons min 20x20 with
   `aria-label`; `ON` / clock text markers next to colour cues.
2. `app/dashboard/dashboard.js`: tabs get `role=tab` / `tabpanel` /
   `aria-controls`, roving tabindex, Left/Right and Ctrl+1..9 switching
   (dashboard window only); System moves to a right-aligned icon tab.
3. Tab badges from data already polled ("Today 3 left", "Events 1
   ending"); pure `tabBadges(snapshots)` in `ewcore.js`.
4. Zero states: counts hidden at zero; empty cards say the next action
   ("Log a grind to see silver/h"). Hot-list and Where-next rows become
   `button`s.
5. Tests: node tests for `tabBadges` and keyboard helpers; self-test 7/7
   (or 8/8 with plan 025) asserts no card scrolls on a fresh store at
   1264x761.

Acceptance: tests green; self-test at merge; gates green; verifier PASS
within 3 rounds.

ToS check: dashboard-only UI; keyboard shortcuts act inside EW's own
window, never on the game.

Depends on: none.
