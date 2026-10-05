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
