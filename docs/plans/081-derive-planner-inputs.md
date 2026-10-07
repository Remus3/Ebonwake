# Plan 081 - Derive the typed planner inputs: silver, hours/day, inventory weight, CP, fame, loot counts, Hot Time ends

Status: open. Zero-touch audit 2026-10-06 (research 0010 M4-M10). Lane hint: `data`.

Spec gap: operator order 2026-10-06 - tabs update from the live game, not
from typing. Seven planner inputs are still typed and go stale silently:
silver on hand and hours / day (Deadeye shopping, `shopping.py:82`),
inventory `base_lt` / `slots` / `slots_used` / `fame_vt`
(`inventory.py:42`), Imperial CP (`imperial.js:91`, profile source off by
default since 061), market fame bonus (`market.fame_pct`), grind loot
counts per session (`grind.js:406`) and recurring Hot Time windows with no
end (`leveling.js:331`).

1. Silver on hand: latest plan 066 `silver` read (value + shot time);
   a typed value becomes a plan 079 entry superseded by the next OCR read.
2. Hours / day: 14-day median of logged-in hours per day from plan 062
   play sessions (session log); typed value superseded once 3 sessions
   exist in the window; card shows "from N sessions".
3. Inventory weight / slots, CP, fame bonus: new crop regions in
   `server/ew/data/ocr_regions.json` (inventory weight `x / y LT`, slots
   `a / b`, CP readout, market sell dialog fame bonus line); new
   `server/ew/ocrinfer.py` kinds `weight`, `slots`, `cp`, `fame` on the
   063 `{kind, name, value, conf, why}` contract, auto-committed above the
   063 confidence gate, else queued for review. Typed values are 079
   entries superseded by a committed read.
4. Grind loot counts: the open session's committed OCR loot / inventory
   reads prefill the loot form (`source: ocr`); typing only corrects.
5. Hot Time windows: a typed window must carry an end (default: the next
   maintenance end from plan 064 / `maint.py`); a 064 notice window
   overlapping it retires it (079 `retired_by: "notice"`).
6. Every derived value shows its source and age on its card (spec section
   4); a stale derived value (> 7 d) shows the muted stale marker from
   plan 077, never a silent number.
7. Tests:
   - `tests/test_ocrinfer.py`: fixtures for weight, slots, CP and fame
     lines (3-decimal and comma variants); confidence below the gate
     queues instead of committing.
   - `tests/test_shopping.py`: OCR silver supersedes typed; hours / day
     from sessions (median, 3-session minimum).
   - `tests/test_inventory.py`, `test_imperial.py`: committed read
     supersedes typed; badge cleared.
   - `tests/test_leveling.py`: typed Hot Time window without end gets the
     next maintenance end; overlapping notice window retires it.
   - `tests/test_grind.py`: loot form prefill from the session's OCR reads.

Acceptance: after one screenshot of the inventory, one of the CP readout
and one market sell dialog, plus three logged-in sessions, Deadeye
shopping, Inventory, Imperial and the net-proceeds figures show derived
values with source + age and no typed value; gates green; verifier PASS
within 3 rounds; one push.

ToS check: OCR of screenshots the operator saved and the client session
log tail only; no window capture, no game input, no memory read.

Depends on: 045, 053, 062, 063, 064, 066, 079.

## As-built deviations

Dependency guard passed (062, 066, 079 modules present). Self-adjudicated:

1. Supersede rule is "newest wins" (`server/ew/derived.py` `pick`), not a
   plan 079 store-ledger entry per input. Decision: each service keeps its
   typed value with a set-at stamp; a live read (OCR shot time, newest play
   session end) at or after it wins, typing after the last read corrects it
   until the next read. Typed inputs still in force are listed in
   `/api/overrides` as `clearable: false` rows (the plan 066 `gear.*`
   pattern). Alternatives: one 079 ledger key per input (needs a typed
   write path through `/api/settings` for service-owned values and a
   live-vs-set_at gate the ledger's resolve does not have); a live read
   always winning (typing could never correct a misread). Why: same visible
   behaviour (badge until a newer read, history-free), no cross-service
   write path. Reverses if: the operator wants typed planner inputs to
   expire by time or show in the 079 retired history.
   `market.fame_pct` does go through the 079 ledger: the newest fame read
   is its live signal (`retired_by: "ocr_fame"`) when newer than the entry.
2. Hours / day median is over PLAYED UTC dates in the 14-day window (a
   session over midnight split per date), and the 3-session minimum counts
   sessions, not dates. Alternatives: median over all 14 calendar days.
   Why: rest days would drive the median to 0 h and the afford date to
   "never". Reverses if: the operator wants calendar-day averaging. Closed
   sessions are kept in a new `playsession.history` (last 100).
3. Inventory `fame_vt` is not derived: no OCR readout says whether the
   warehouse fame VT bonus applies. Base LT is derived as the weight read's
   max minus owned sources minus VP LT (a read that makes it negative is
   ignored); base slots = slots total minus VP slots. Reverses if: a
   readout for the fame VT tier is found.
4. Bare "a / b" slot reads are only taken with a slot / inventory label on
   the line, beside the weight line, or inside the (unverified) slots
   region - a bare fraction anywhere on screen would flood the review
   queue with quest counters. CP takes the total after the slash; a single
   "Contribution Points N" is queued (0.85, left or total unknown).
5. Loot prefill: rows read from shots taken during the running grind
   session go to store domain `ocr_loot` (newest read per item replaces,
   the loot window shows running totals); rows under the 063 gate prefill
   marked "?" instead of entering the review queue (typing corrects
   either way). The loot form never overwrites a count the operator typed.
6. Hot Time: legacy windows without `until` keep working and show "no end
   set" (no invented end). Undoing a notice import (`hot_auto_del`)
   restores the typed windows that notice retired. Retired windows live in
   `leveling.hot_retired`, not the 079 ledger (see 1).
7. Tests landed in the existing files (`test_ocr_infer.py`, not a new
   `test_ocrinfer.py`) plus `tests/test_planner_inputs.py` (served-app
   acceptance) and `app/test/planner_inputs.test.js`.

refute-rounds: 0/3 (lane build; the merge verifier runs round 1).

Dependency guard: before writing code the lane checks that
`server/ew/overrides.py` (plan 079), `server/ew/ocrinfer.py` (plan 066) and
`server/ew/playsession.py` (plan 062) exist. If any is missing, the lane
changes nothing, writes `"status": "blocked", "needs": ["062", "066",
"079"]` into its progress JSON (`ops/loop/control/progress/p081-build.json`)
and exits 0.
