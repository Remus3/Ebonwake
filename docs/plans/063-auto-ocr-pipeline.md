# Plan 063 - Auto-OCR every new screenshot: confidence-gated auto-commit + review queue

Status: open. Autonomy deep dive 2026-10-06 (research 0007 sections 1, 2.5). Lane hint: `build`.

Spec gap: OCR (009) runs only when the operator presses Read (System) or
Import (Grind, 040). Silver and buff results carry no confidence, so nothing
can be committed safely without a human. Screenshots the operator takes
anyway are the richest passive signal EW has.

1. Trigger: the plan 008 screenshot watcher enqueues every NEW file whose
   mtime falls while the gamewatch state is `logged_in` (or within 60 s
   after); one worker thread OCRs the queue (plan 009 engine chain, existing
   cache). Shots taken while the game is closed are listed, never OCRed.
   One OCR at a time, at most `ocr.daily_cap` (default 120) a day.
2. Confidence: every extracted field returns `{value, conf 0..1, why}`.
   Silver: digit-group validity, separator consistency, agreement between
   engines when both ran, plausibility vs the last committed silver.
   Buffs: name match ratio against the buff data names x time-parse
   validity. Loot rows reuse `1 - edit distance` (plan 040).
3. Commit gate: fields with `conf >= ocr.auto_commit_min` (default 0.9)
   are committed to their domain (silver sample, buff timer, loot rows to
   the open plan 062 session when present) with source `ocr:<file>` and an
   undo id; the rest go to a review queue (store domain `ocr_review`, max
   50, oldest dropped) shown as one System card "N to review" with accept /
   fix / discard per field.
4. The Read and Import buttons stay (re-run on demand) but are no longer
   needed on the normal path.
5. Settings (030 allowlist): `ocr.auto: true`, `ocr.auto_commit_min`
   0.75-0.99, `ocr.daily_cap`.
6. Tests: `tests/test_ocr_auto.py` with stub engines - a shot while
   logged_in is enqueued, while closed it is not; high-conf silver
   committed with undo, low-conf queued; cap respected; undo removes the
   sample. Bench: commit precision on the plan 009 synthetic bench >= 0.99
   at the default threshold (recall reported).

Acceptance: 3 fixture shots dropped during a fixture logged_in window
commit the clean silver and buff fields and queue the noisy one, with no
button press; gates green; verifier PASS within 3 rounds; one push.

ToS check: OCR of image files the operator saved with the game's own
screenshot key, read from the ScreenShot folder after they are written; no
live capture of the game window, no game input, no memory read, no client
file edit.

Depends on: 008, 009, 040.

Dependency guard: before writing code the lane checks that `server/ew/gamewatch.py` (plan 008), `server/ew/ocr.py` (plan 009) and `server/ew/grind.py` (plan 040) exist. If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["008", "009", "040"]` into its progress JSON (`ops/loop/control/progress/p063-build.json`) and exits 0.
