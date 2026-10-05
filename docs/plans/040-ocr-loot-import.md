# Plan 040 - OCR loot-window import: operator screenshot -> item counts for the grind log

Status: open. Deep dive + UX audit 2026-10-05 session 4 (research 0003-0005). Lane hint: `build`.

Spec gap: research 0004 F11 (value 4, M). Typing loot counts after each
session is the next friction once plan 039 lands; plan 009 already OCRs
operator-taken screenshots (Tesseract chain + Windows OCR fallback).

1. `server/ew/ocr.py`: `parse_loot(text_lines, known_names)` -> `[{name,
   count, confidence}]` matching OCR lines against the spot's loot-table
   names (plan 039) with a normalised edit distance <= 0.25 and a trailing
   or leading count (`x123`, `123`), reusing plan 016 digit-group rules.
2. Route: `POST /api/ocr {"loot": {"shot": <name from the plan 008
   ScreenShot list>, "spot": id}}` -> parsed rows; the operator confirms in
   the Grind stop form before anything is stored (no auto-write).
3. Synthetic bench: `tests/fixtures/ocr_loot/` rendered text images or
   recorded OCR outputs (no game assets); `tests/test_ocr.py` cases for
   matching, counts with commas, unknown names, low confidence.
4. Dashboard: "Import from screenshot" button in the Grind stop form, rows
   prefilled, editable.

Acceptance: bench >= 90 percent exact rows on the synthetic set; tests
green; gates green; verifier PASS within 3 rounds.

ToS check: screenshots the operator takes with the game's own key, read
from the ScreenShot folder (CLAUDE.md allowed input); OCR never leads to
input.

Depends on: 039.

Dependency guard: before writing code the lane checks that `loot_value` exists in `server/ew/grind.py` (plan 039). If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["039"]` into its progress JSON (`ops/loop/control/progress/p040-build.json`) and exits 0. Plan 019's tick turns that clean, marked run into item state `blocked` (not `no-change`) and re-dispatches the row once every Depends-on row is `[x]`; plan 019's work-list gate normally keeps the row from being dispatched that early. If this row ran before plan 019 landed and was recorded `no-change`, 019's re-arm step makes it dispatchable again.
