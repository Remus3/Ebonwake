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

## As-built deviations

1. Card-scroll self-test gate is opt-in. Decision: `app/selftest.js` always
   reports `cardsScroll` / `cardsFit` per tab, and fails the run on a
   scrolling card only when `EW_SELFTEST_FRESH=1` (`freshStore`).
   Alternatives: always fail on a scrolling card; report only. Why: lists
   legitimately scroll inside their card once the operator's store fills
   up, so an unconditional gate would fail every lived-in self-test; the
   plan's assertion is about a fresh store. Reverses if: the merge
   self-test gets its own throwaway store, then the gate can be made
   unconditional.
2. Tab badges read Home's snapshots (`EWHome.snapshots()`), repainted every
   5 s by the shell. Alternatives: a shell-level poll of /api/today and
   /api/events; every module publishing into a bus. Why: Home already polls
   both every 60 s from mount whatever tab shows ("data already polled"),
   so no new requests. A tick on the Today tab shows in the badge within
   one Home poll (<= 60 s). Reverses if: badge lag is reported as
   confusing, then today.js / events.js publish their fresh bodies too.
3. Badges: Today counts dailies left only (not weeklies); Events counts
   open items with `soon` (<= 48 h, same rule as Home's "Ending" card).
   Why: matches the plan's examples and the existing Home cards.
4. Ctrl+1..9 map to the visual tab order after System moves last (9 tabs
   today, so Ctrl+9 = System). Left/Right/Home/End act only while focus is
   on the strip; Ctrl+digit acts anywhere in the dashboard window
   (Ctrl+0 is left to Electron's zoom reset). Overlay untouched.
5. Zero states touched: Home dailies meta (no "0/0 done"), Home session
   and market-alert empty text, Today list count pill hidden at 0/0, Grind
   session log empty text. Other empty notes already named their next
   action and were left as they were.
6. Text cues beside colour (L8): "ON " before an active buff name, a
   stopwatch glyph (CSS escape, ASCII source) before a soon event's time
   and Home warn values, "! " before Home bad values.
7. Where-next rows were already `<button>`s (plan 012); only the Market
   hot-list rows changed from `div` to `button` (`.ew-rowbtn` reset).
   Watchlist rows stay `role=button` divs with tabindex (they already were).
