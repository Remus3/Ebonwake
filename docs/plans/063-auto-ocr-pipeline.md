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

## As-built (lane build, 2026-10-06)

Verifier: refute round 1 REFUTE (3 undo / expiry defects, fixed), round 2
PASS. refute-rounds: 2/3. Host follow-up: run `tools/ocr_bench.py --live` and
score commit precision at 0.9 with `ocrauto.silver_field` (deviation 4).

Code: `server/ew/ocrauto.py` (AutoOcr, silver_field, buff_fields),
`OcrService.doc` (ocr.py), `GrindService.restore_buff` (grind.py, undo only,
not a POST op), settings keys `ocr.auto` / `ocr.auto_commit_min` /
`ocr.daily_cap`, `GET /api/ocr/auto`, `POST /api/ocr {review: {id, action,
value?}} | {undo: id}`, SSE domain `ocr`, System tab game card "Auto-OCR: N to
review" (accept / fix / discard, last 5 commits with undo), Settings group
"Screenshots (OCR)". Tests: `tests/test_ocr_auto.py`, `app/test/ocr_auto.test.js`.

### As-built deviations

1. Loot rows are not auto-extracted.
   Decision: the auto pipeline commits / queues silver and buff fields only;
   loot import stays on the plan 040 Import button.
   Alternatives: queue loot rows against the active plan 005 session's spot;
   commit them to a plan 062 session.
   Why: plan 062 (the session loot rows would commit into) is not built, and
   a queued loot row has no commit target for "accept".
   Reverses if: plan 062 lands - then rows join `buff_fields`/`silver_field`
   as a third kind with `1 - edit distance` confidence.
2. Silver samples live in a new store domain `silver` ({samples: [{id, at,
   value, source}]}, newest 500).
   Alternatives: write into a grind session's silver field.
   Why: no silver-sample domain existed; a bag/wallet reading is not a session
   result, and plausibility needs "the last committed silver".
   Reverses if: a later plan adds a silver history; migrate the samples there.
3. Engine agreement is wired (`silver_field(..., alt=)`) but the normal path
   runs one engine (the plan 009 chain) per shot, so `alt` is None there.
   Alternatives: always run Windows OCR as a second engine.
   Why: Windows OCR read 105/288 on the plan 009 bench; requiring agreement
   would cut recall to about a third and double OCR time.
   Reverses if: the live bench below shows precision < 0.99.
4. Bench: the plan 009 render bench needs PowerShell rendering + Tesseract on
   the host, which a sandboxed lane cannot reach. The CI gate is a replay
   bench (`test_bench_commit_precision_at_default_threshold`): the bench's
   288 amount draws through the error classes plan 009 / 016 measured (clean,
   dot, space, spaced separator, dropped comma, truncated / garbled group,
   joined neighbour number). Precision 1.00 by construction of the classes
   (only clean and dot-grouped reads clear 0.9), recall about 0.54 (asserted
   >= 0.4). A single wrong glyph that keeps valid grouping (4/288 live misses in
   plan 009) cannot be seen in text; at that rate live precision could be
   ~0.986. Mitigations: every auto commit is listed with undo; the
   plausibility check (10x off the last committed silver) catches magnitude
   errors.
   Alternatives: mandatory engine agreement (see 3); Tesseract per-word
   confidence (needs live calibration).
   Why: no host access from the lane; the gate must run in CI.
   Reverses if: a host run of the live bench measures commit precision < 0.99
   - then add Tesseract word confidence as a silver signal.
5. Buff names: exact (normalised) match = ratio 1.0; otherwise the plan 040
   fuzzy match (edit distance <= 0.25) against the seed buff names gives
   `1 - distance`, so a misread name never reaches the default 0.9 gate.
   A time read from a neighbour line caps at 0.85 (queued at the default).
6. Committed buff minutes are the read minutes minus whole minutes since the
   shot's mtime; a buff that ran out since the shot is neither committed nor
   queued. In review, accept applies the same rule (an expired item says so);
   fix takes minutes left NOW. Undo restores the timer's `ends` and `armed`,
   and only while the timer is still the one the commit wrote (a later commit
   or a manual re-arm / clear is never overwritten; verifier r1 findings 1-3).
7. Shots outside a logged_in window and shots over the daily cap are marked
   seen and never auto-read (the Read button still works). `ocr.daily_cap`
   accepts 0..1000 (0 = auto-read nothing).

Dependency guard: before writing code the lane checks that `server/ew/gamewatch.py` (plan 008), `server/ew/ocr.py` (plan 009) and `server/ew/grind.py` (plan 040) exist. If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["008", "009", "040"]` into its progress JSON (`ops/loop/control/progress/p063-build.json`) and exits 0.
