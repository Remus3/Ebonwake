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

Dependency guard: before writing code the lane checks that
`server/ew/overrides.py` (plan 079), `server/ew/ocrinfer.py` (plan 066) and
`server/ew/playsession.py` (plan 062) exist. If any is missing, the lane
changes nothing, writes `"status": "blocked", "needs": ["062", "066",
"079"]` into its progress JSON (`ops/loop/control/progress/p081-build.json`)
and exits 0.
