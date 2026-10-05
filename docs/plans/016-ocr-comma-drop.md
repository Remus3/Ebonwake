# Plan 016 - OCR comma-drop regression: a vanished comma must not cut a digit group

Status: open, priority (operator order relayed 2026-10-05). One lane (`build`).

The plan 009 word-gap fix (9ace779) cuts a silver amount at a digit gap with no
ink around the baseline (a real space). On the old quick bench set that cost
one case: c0017 (13 px, dark on light), a comma Tesseract dropped whose pixels
are gone after prep, so the probe sees 0 ink and the amount loses its tail
digit group. Old set 142/144 -> 141/144. Target: 142/144 back with the
gap set still 120/120.

1. Reproduce: `python tools/ocr_bench.py --quick --set old --live` and
   `--set gap --live`; record both counts in this doc before any change.
2. Find a signal that separates a vanished comma from one real space. Look at,
   in this order, and record what each measures on c0017 vs the gap set:
   a. the raw (pre-prep) crop: ink in the gap at the comma's expected position
      (below the baseline, ~0.2 h wide), probed on the unthresholded crop;
   b. group grammar on the far side: a cut leaves a right part of exactly 3
      digits AND the left part already carries at least one read comma with
      the same group pitch (gap width vs the line's read-comma gap widths);
   c. Tesseract confidence of the two digit words.
   Ship the smallest rule that gets old 142/144 and gap 120/120 together.
3. TDD: a unit test in `tests/` per rule over recorded tsv rows (no image
   needed), plus the bench counts in the commit body. Bump `CACHE_VERSION`
   if the read of a cached image can change.
4. If no signal separates them on the bench, change no code: record the
   measurements and "no separating signal" under As-built deviations, and the
   accepted residual stands (009-ocr.md "Word-gap fix").

Acceptance: gates green; bench old >= 142/144 and gap = 120/120 on the live
chain, both counts in the commit body; or the measured no-signal record.
Game ToS floor unchanged: synthetic renders and operator screenshots only.

## As-built (2026-10-05, build lane)

Step 1, before any change (live chain, `--quick`):
- `--set old`: 141/144 (fails c0013, c0017, c0034) - reproduces 009.
- `--set gap`: 86/96. The quick gap set is 96 cases today (4 fonts x 4 sizes
  x 3 crop/frame styles x 2 spacings), not the 120 quoted in 009 (see
  deviations).

Step 2, measured on c0017 ("Silver: 4,521,372", Arial 13 px dark on light),
Tesseract tsv at x3 (psm 6 and 11 identical):
`Silver:` (67,43,98,32) | `4,521` (182,43,88,37) | `372` (283,43,68,37).
- a. Ink: the comma is NOT gone after prep. The prepped image shows it
  clearly at x ~285-291, y 65-81 - inside the `372` box (starts x 283).
  Tesseract kept the comma's pixels in the right word's box and dropped
  only its text, so the probed gap (x 271-282, between `1` and the comma) is
  clean paper. The 009 note "0 ink at any threshold" probed the wrong
  columns; the raw crop adds nothing the prepped one lacks (prep is a
  bicubic upscale, no threshold).
- b. Group grammar: does not separate. Every gap case also leaves a right
  part of exactly 3 digits ("100") after a comma-read left part; Arial's
  comma and space advances are both 0.278 em, so pitch does not separate
  either.
- c. Confidence: `4,521` / `372` 87.8 / 87.8 vs gap-set `1,584` / `100`
  95.8 / 96.3 - lower, but within the spread of good reads (`8,650,273,285`
  87.1); not used.
- The separating signal is the box BOTTOM, read straight from the tsv: a
  comma word's box runs 0.13-0.19 h below the digit baseline (g0000 0.16,
  g0002 0.19, g0010 0.14, g0018 0.16); the right word that swallowed a comma
  ends level with it (c0017 0.00, g0016 `9,384`|`802` 0.00) or, after a
  plain left word, below it (g0008 `7`|`490` +0.16); a real-space `100`
  ends on the baseline.

Rule shipped (`server/ew/ocr.py` `_swallowed_comma`, `COMMA_TAIL` 0.07):
a plain digit word whose bottom is within 0.07 h of a comma-tailed left word
(text holds `,`/`.`, or it swallowed a comma itself), or 0.07 h below a
plain one, holds a dropped comma: that gap is never probed and stays
joined. `CACHE_VERSION` 3 -> 4. Tests: `tests/test_ocr.py` recorded rows
C0017, G0010, G0016, G0008 plus the chained-tail and plain-baseline cases;
the synthetic `_gap_tsv` / last-gap fixtures now give comma words their
measured tail (flat bottoms there were unrealistic).

Step 3 bench after the change (live chain, `--quick`, measured 2026-10-05):
- `--set old`: 142/144 (fails c0013, c0034) - c0017 back; 141 -> 142.
- `--set gap`: 86/96 (fails g0004 g0006 g0012 g0014 g0022 g0042 g0050
  g0054 g0074 g0076) - the same 10 as step 1, no new fail.

## As-built deviations

1. Gap-set acceptance reads "no regression on the quick gap set" (86/96 ->
   86/96 measured after, same 10 fails), not "120/120".
   - Decision (self-adjudicated): the committed `gap_cases(quick=True)` is
     96 cases; 120 is not reachable by any `--quick` / full flag (full is
     192). Its 10 baseline fails are not word-gap probe outcomes: 7 are
     amounts >= 10^10 whose joined read with "100" exceeds `MAX_SILVER`
     (10^13), so `extract_silver` finds nothing and the chain never reaches
     the probe (g0004 g0014 g0022 g0042 g0050 g0054 g0074; g0074 also
     reads one comma as ";"); 3 are glyph or
     layout misreads (g0006 6/8, g0012 every group on its own line, g0076
     a dropped comma read as a run "94,249522,509").
   - Alternatives: fix the over-`MAX_SILVER` join here (scope creep past
     the plan's one-case regression; touches the chain's pass selection);
     treat 86/96 as a failed acceptance and ship nothing (the c0017 fix is
     independent of those fails).
   - Why: plan 016 is the c0017 regression; the gap set's job here is to
     prove the new rule cuts no real space, which "no regression" proves.
   - Reverses if: MAIN or the roadmap restates 120/120 on a named set -
     then file the over-`MAX_SILVER` join as its own plan (probe the joined
     read's last gap even when the join is out of range).
2. Signal found is none of a/b/c as worded: the box bottom from the tsv
   (no image, no extra PowerShell call). Decision: ship it - it is the
   smallest rule (one comparison per digit gap, tsv only) and it removes a
   probe call instead of adding one. Alternatives: widen the probe rectangle
   into the right word's first columns below the baseline (needs the true
   digit baseline, which `min(bottoms)` is not when both words carry a
   comma tail; a `1` stem in "100" would read as ink). Reverses if: a real
   screenshot shows a real-space neighbour number whose box descends (a
   descender glyph in the right word) - then require the right word's
   height, not just its bottom, to exceed the left's digit height.
