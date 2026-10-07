# Plan 076 - Home consolidation: full-width What now, one Timers card, quiet line, no repeated facts

Status: open. UI/UX audit 2026-10-06 (research 0009 H2, H3, H4, M10, L1, L4). Lane hint: `build`.

Spec gap: Home (025) grew to 11 cards. Live at 1264x761 the What now card
(069) truncates its own action text ("Bulgas..."), the same boss / reset /
dailies fact appears in 3-4 cards, and 6 cards are empty states. Spec goal 1
("what should I do right now") is answered, then buried.

1. `app/shared/ewcore.js` (pure, clock injected):
   - `nowTimers(today, bosses, events, at, now, whatnow)`: one card
     "Timers" that merges daily / weekly / custom resets (today
     `nowResets`), the next 3 world bosses (`nowBosses`) and events ending
     within 48 h (`nowEnding`), sorted by due, max 6 rows. A row whose
     (source, due-minute) matches a What now action is dropped (sources
     `reset`, `boss`, `coupon`, `maint`). The Garmoth n/3 count stays only as
     the card meta, not as a row.
   - `composeNow` returns `{cards, quiet}`: cards with no rows (Buffs, Grind
     session, Level ETA, Market alerts, New coupons, Timers when empty) move
     to `quiet` as `[{title, tab}]`; What now stays even when all clear.
     Order: What now, Get started (while shown), Dailies left, Timers, then
     cards with content in the existing order, Last session last.
   - `homeCollapse` helpers for any card: `collapsedKey(tab, card)` and a
     pure `toggleCollapsed(set, key)` (localStorage list, try/catch).
2. `app/dashboard/home.js`: What now card `ew-wide` (`grid-column: 1 / -1`),
   each action one row: text never ellipsized, `why` on a muted second
   line, the whole row a button that opens its tab (no separate `open`
   button). Home tick button labelled `tick` with
   `aria-label="tick <title>"`; card `open` links `aria-label="open <tab>"`.
   The `quiet` list renders as one muted line at the bottom
   ("Quiet: buffs, grind, alerts, coupons") with each name a link.
3. `app/dashboard/progress.js` + `dashboard.js`: Mounts, Pets, Inventory and
   Life & CP cards get a header toggle (collapsed = header + one-line
   summary already computed by their modules), default collapsed, state per
   card in localStorage. Prose in Mounts moves from `ew-num` to normal text.
4. `app/overlay/overlay.js`: the Today line is hidden when daily and weekly
   totals are both 0 (`C.ovQuiet` extension).
5. `app/shared/ew.css`: `.ew-wide`, two-line What now rows, collapsed card
   style. No new colours (fleet tokens only).
6. Tests (`node --test`, `app/test/home.test.js`, `whatnow.test.js`,
   `density.test.js`):
   - Fixture: boss in 3 min + daily reset in 3 min + What now listing both
     -> Timers has neither row; Weekly reset row remains.
   - Empty store -> cards = [What now, Dailies left (if any)], quiet lists
     the others; no card has zero rows.
   - Get started still leads when What now is all-clear (069 deviation 4).
   - `toggleCollapsed` round trip; corrupt storage value -> default.
   - Overlay quiet: `daily 0/0  weekly 0/0` hidden.
   - Self-test (`app/selftest.js`): Home on a fresh store at 1264x761 has
     no vertical scroll and no element with `text-overflow` clipping inside
     the What now card.

Acceptance: at 1264x761 with the 0009 live fixture (boss + reset in 3 min,
5 dailies open, nothing running) Home shows What now (full text), Get
started, Dailies left, Timers (no repeated boss / reset row) and one Quiet
line; tests green; `python -m pytest -q` and `npm test --prefix app` green;
verifier PASS within 3 rounds; one push.

ToS check: renderer-only regrouping of data EW already GETs; no game input,
no new route, overlay unchanged except one hidden-row rule.

Depends on: 025, 047, 069.

Dependency guard: before writing code the lane checks that
`app/dashboard/home.js` (plan 025) and `server/ew/whatnow.py` (plan 069)
exist. If either is missing, the lane changes nothing, writes
`"status": "blocked", "needs": ["025", "069"]` into its progress JSON
(`ops/loop/control/progress/p076-build.json`) and exits 0.
