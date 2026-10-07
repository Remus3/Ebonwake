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

## As-built deviations

Guard: `app/dashboard/home.js` and `server/ew/whatnow.py` both present.

1. Empty store shows What now + Timers, not What now + Dailies.
   - Decision: Timers always keeps the daily and weekly reset rows. On an
     empty store, Home is therefore [What now, Timers] plus the Quiet line.
     The plan's empty-store test was changed to match.
   - Alternatives: (a) drop the reset rows while Today has no items; (b) treat
     a Timers card that has only reset rows as quiet.
   - Why: the reset clock is always news. It is also the only countdown Home
     shows on a fresh store, and the acceptance fixture expects a Timers card.
   - Reverses if: the operator wants Timers on the Quiet line when a store is
     fresh.
2. Every card except What now goes quiet when it has no rows.
   - Decision: this includes Dailies left and Last session, not only the six
     cards the plan lists. Quiet names are short and lowercase: `dailies`,
     `timers`, `buffs`, `grind`, `level ETA`, `alerts`, `coupons`,
     `last session`.
   - Alternatives: keep an empty Dailies or Last session card that shows a
     message.
   - Why: the plan's own test says "no card has zero rows", and step 2 shows
     the line as "Quiet: buffs, grind, alerts, coupons".
   - Reverses if: the operator asks for an "all dailies done" card.
3. Collapse storage holds the cards the operator expanded.
   - Decision: localStorage key `ew.cards.open` lists expanded cards. Missing
     or corrupt storage therefore means every card starts collapsed.
     `parseCollapsed` and `isCollapsed` were added next to `collapsedKey` and
     `toggleCollapsed`. The modules mark their cards with `data-collapse`, and
     one generic `collapsibles()` in `dashboard.js` adds the header toggle
     (`aria-expanded`).
   - Collapsed view: the header plus the module's existing summary. That is
     the header pill or subtitle: T10 n/m, n/max out, VP on/off, or the
     character name.
   - Alternatives: (a) store the collapsed set, so default collapsed has to be
     special-cased; (b) write a toggle into each of the 4 modules.
   - Why: the default holds with no special case, and the toggle code exists
     in one place.
   - Reverses if: a card needs a different default.
4. `overlay.js` is unchanged.
   - Decision: `C.ovQuiet` now also matches `daily 0/0  weekly 0/0`. The
     existing `showRow` hides that row through it.
   - Alternatives: a special case inside `drawToday`.
   - Why: one rule, and it is already tested where the other quiet rows are.
   - Reverses if: never (the behaviour is the same either way).
5. The self-test fails on What now clipping for every store.
   - Decision: `wnClip` is any element inside `[data-card="whatnow"]` that is
     ellipsized and overflowing. It fails the self-test whether or not the
     store is fresh. "No vertical scroll" is the existing `fits` check.
   - Alternatives: fail only on a fresh store, like the card-scroll check.
   - Why: What now text is never ellipsized by design, so any clip is a defect.
   - Reverses if: a lived-in store is measured clipping legitimately.
6. Kept as they were:
   - Onboarding step rows keep their own `open` button. Only What now rows
     became whole-row buttons.
   - Boss rows in Timers carry the note `world boss`.
   - Events ending that are not coupons get source `event`, which What now
     never lists, so they are never deduped.
   - Why: the plan scopes the row-button change to What now.
   - Reverses if: the operator asks for the same row style on Get started.
7. Gates were not run in this lane.
   - Every shell command (`python`, `node`, `git`) needed interactive approval
     in this headless lane. The tests were written first and the code was
     reasoned against them, but `npm test --prefix app`, `python -m pytest -q`
     and ruff were not run here.
   - Decision: hand back unmerged; the loop's verifier runs the gates before
     merge.
   - Alternatives: wait for approval, which the operator standing orders
     forbid.
   - Reverses if: the verifier finds a red gate. Fix it in the next lane run.
