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

## As-built (lane build, 2026-10-05)

- `server/ew/ocr.py`: `parse_loot(text_lines, known_names)` -> `[{name, count,
  confidence}]` via `extract_loot` (adds `unmatched`), `name_distance`
  (Levenshtein / longer length, cutoff `LOOT_MATCH_MAX` 0.25, inclusive),
  `_loot_count` (x-tagged anywhere, else leading, else trailing; plan 016 digit
  groups). `OcrService.read` takes `{"loot": {"shot", "spot"}}` beside
  `{"file"}`; names come from `GrindService.loot_names(spot)` (table + operator
  items). Same OCR cache as the plan 009 read.
- Bench: `tests/fixtures/ocr_loot/cases.json`, 20 cases, 40/40 exact rows
  (gate >= 90 percent) in `tests/test_ocr.py`.
- Dashboard: Grind loot card "Import from screenshot" (`app/dashboard/grind.js`,
  helpers `normalizeLootOcr` / `lootImportCounts` / `lootImportText` in
  `ewcore.js`, tests `app/test/ocr_loot.test.js`).

## As-built deviations

1. Response shape. Decision: `POST /api/ocr {"loot": ...}` returns `{shot, spot,
   text, rows, unmatched}`, `unmatched` = counted lines with no loot name.
   Alternatives: rows only. Why: the operator sees that a line was skipped
   (an item missing from the spot list) instead of a silent drop. Reverses if:
   the dashboard stops showing it.
2. Unread count. Decision: a matched name with no count on its line takes a
   pure-count line on its row / just below (only when the OCR gave geometry),
   else `count: null` and the dashboard leaves that input blank and names it.
   Alternatives: default 1; drop the row. Why: inventing a count would value
   loot that was never read; dropping hides a real item. Repeated names (the
   acquisition log) sum. Reverses if: BDO's loot window is measured to always
   print a count.
3. OCR-tolerant digits. Decision: inside an x-tagged count only, O/o read as 0
   and l/I as 1 ("x1O5" -> 105). Alternatives: everywhere; nowhere. Why: a
   leading/trailing plain number next to a name could otherwise eat letters.
   Reverses if: real reads show the misread outside x-tagged counts.
4. Bench is recorded OCR text, not rendered images. Decision: hand-written OCR
   line outputs with measured Tesseract misreads (l/1, O/0, rn/m, brackets,
   comma as space/dot), ASCII-only fixture; the multiplication-sign count is a
   unit test (chr(0xD7)) so the fixture stays ASCII. Alternatives: render
   images and run Tesseract in CI. Why: the plan allows recorded outputs, CI has
   no Tesseract, no game assets. Reverses if: plan 009's ocr_bench grows a loot
   render set.
5. Import source and target. Decision: the button OCRs the NEWEST screenshot of
   the plan 008 list and pre-fills the Grind loot counts, which feed both Stop +
   log and the manual Log form; fuzzy-name rows show "?" until edited; nothing
   is posted to /api/grind. Alternatives: a screenshot picker; a separate
   confirm dialog. Why: one tap after the operator's own screenshot key, and
   the existing editable inputs are the confirmation step. Reverses if: the
   operator asks to pick older shots.
6. Engine passes. Decision: unchanged; the Tesseract chain stops at the first
   pass that reads a silver amount, so a loot window (no silver) runs all three
   passes and keeps the first pass's read (cached per file). Alternatives: a
   loot-specific stop rule. Why: no measured loot render set to tune it on.
   Reverses if: the import read time is reported as too slow.

7. One item per OCR line (verifier refute round 1, minor). Decision: a line
   holding two items keeps the closest name only. Alternatives: split the line
   at each count. Why: loot windows and the acquisition log print one item per
   row; Tesseract's psm 6 row joining is already split at wide gaps by the plan
   009 TSV parser. Reverses if: a real loot read shows two items on one line.

Refute rounds 1-2 fixed: a name without a count takes a count on its own row
first (nearest x); a left-over count goes to the nearest name line above it,
only if that name has no count (each count once, by line index), so a count
printed beside or under one name never reaches the name above it.

ToS: reads only a screenshot the operator took (plan 008 listed name, never a
path); output only pre-fills dashboard inputs; nothing reaches the game.

Dependency guard: before writing code the lane checks that `loot_value` exists in `server/ew/grind.py` (plan 039). If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["039"]` into its progress JSON (`ops/loop/control/progress/p040-build.json`) and exits 0. Plan 019's tick turns that clean, marked run into item state `blocked` (not `no-change`) and re-dispatches the row once every Depends-on row is `[x]`; plan 019's work-list gate normally keeps the row from being dispatched that early. If this row ran before plan 019 landed and was recorded `no-change`, 019's re-arm step makes it dispatchable again.
