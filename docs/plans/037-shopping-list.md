# Plan 037 - Shopping list from the enhancement plan: materials x arsha prices, can-afford-by

Status: open. Deep dive + UX audit 2026-10-05 session 4 (research 0003-0005). Lane hint: `build`.

Spec gap: research 0004 F04 (value 4, effort M) and research 0005 F15
(enhancement helper links plan steps to market prices). Today the operator
adds up black stones, Essence of Dawn, crons and caphras by hand.

1. `server/ew/shopping.py`: from plan 007 open steps x plan 035 expected
   attempts x per-attempt materials -> totals per item id; priced with the
   plan 002 cache (exact silver), `null` with `missing price` when absent;
   `can_afford_by` = (total - silver on hand) / plan 005 average silver/h
   (operator hours per day setting, default 3).
2. `GET /api/deadeye/shopping` -> `{lines: [{id, name, qty, unit, total,
   preorder}], total, can_afford_by}`; `silver_on_hand` and `hours_per_day`
   via `POST /api/deadeye`.
3. Dashboard: "Shopping list" card on the Deadeye tab with a "watch" button
   per line that adds the id to the plan 002 watchlist (existing POST),
   names via plan 028's index.
4. Tests: `tests/test_shopping.py` (totals, missing price, afford date with
   zero rate -> null); node formatter tests.

Acceptance: tests green; gates green; verifier PASS within 3 rounds.

ToS check: read-only cached prices and operator data; the "watch" button
only edits EW's own watchlist, never a market order.

Depends on: 035, 028.

## As-built deviations

Self-adjudicated by the build lane (2026-10-05).

1. Step -> rate rows: plan 007 steps carry free item text, no gear family or
   FS. Per-step settings live in store domain `shopping` (`steps: {id:
   {family?, fs?, crons?}}`), set through POST `/api/deadeye` op `shop_step`
   (the EV panel's new "Use in list" button sends its family / FS / crons).
   Without one the family is guessed from the item text (same rule as the
   EV panel), FS 0, crons off. Alternative: add the fields to the plan 007
   step schema. Why: keeps plan 007's validated step shape and its bridge
   guard unchanged. Reverses if: the operator wants FS edited in the step row.
2. Quantities: per level of (current, target] at constant FS, row
   `materials` qty x `attempts_mean` (pity-truncated) and crons x the
   non-guaranteed attempts; summed per item id over every open step, then
   rounded up to whole items (`qty`; the float is `expected`). A failure's
   downgrade re-climb is not counted (as plan 035). Levels without a row or
   chance data are listed in `steps[].levels[].note`, never guessed.
3. Materials are only what rate rows list: the tracked table seeds none
   (plan 035 deviation 3), so out of the box only crons (when on) appear;
   black stones / Essence of Dawn / caphras appear once an operator
   `rate_set` override lists `materials`. Alternative: seed unverified
   quantities. Why: invented numbers are worse than an empty line. Reverses
   if: a verified per-step material source is found.
4. Body extras beyond the plan's `{lines, total, can_afford_by}`: line
   `expected`, `watched`, `note` (`missing price`); top-level
   `priced_total`, `missing_prices`, `afford {need, silver_per_h,
   hours_per_day, per_day, days, reason}`, `steps`, `families`,
   `settings`. `total` is `null` while any line is unpriced (a partial sum
   would understate the date); `can_afford_by` is then `null` too.
5. Average silver/h = minute-weighted over plan 005 spots (floored); none
   logged or 0 -> `can_afford_by: null` (reason given). Date = today (UTC,
   from the server clock) + ceil(need / (silver/h x hours/day)) days; need
   <= 0 -> today. `silver_on_hand` int 0..1e15, `hours_per_day` (0, 24],
   default 3, via POST `/api/deadeye` op `shop_set`.
6. Prices and pre-order state come from `MarketService.cached_quote`
   (sublist cache peek, never a fetch); names from the plan 028 index via
   `NameIndex.name` (seed > util/db > cache, never a fetch). The "watch"
   button posts the existing plan 002 `{add: {id, sid: 0}}`.
7. Verifier round 1/3 PASS (pytest 1782/0, node 353/0, leak sweep clean).
   Its one crash edge (a `rate_set` chance so small the mean attempts
   overflow) is closed: that level is noted `chance too small to total` and
   adds nothing. refute-rounds: 1/3.
8. Merge onto main after plan 036 (resolve lane, 2026-10-05): union of both
   sides in `server/ew/app.py`, `app/dashboard/deadeye.js` and
   `app/shared/ewcore.js`. Decision: keep plan 036's `DeadeyeService(...,
   rates=self.enhance.rows)` construction and add `ShoppingService` right
   after it; the POST `/api/deadeye` error text lists both op sets; the
   Deadeye tab mounts Stacks then Shopping cards and `draw()` calls both;
   the shopping block reuses plan 036's `GEAR_FAMILY_RE` instead of
   redeclaring it (same regex; a second `const` is a SyntaxError).
   Alternatives: put Shopping before Stacks, or keep a renamed duplicate
   regex. Why: merge order matches plan numbers; one regex keeps the family
   rule in one place. Reverses if: the operator wants the card order swapped.
   Gates after the merge: ruff clean, pytest green, node 365/0.

Dependency guard: before writing code the lane checks that `server/ew/enhance.py` exists (plan 035); `server/ew/itemnames.py` exists (plan 028). If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["035", "028"]` into its progress JSON (`ops/loop/control/progress/p037-build.json`) and exits 0. Plan 019's tick turns that clean, marked run into item state `blocked` (not `no-change`) and re-dispatches the row once every Depends-on row is `[x]`; plan 019's work-list gate normally keeps the row from being dispatched that early. If this row ran before plan 019 landed and was recorded `no-change`, 019's re-arm step makes it dispatchable again.
