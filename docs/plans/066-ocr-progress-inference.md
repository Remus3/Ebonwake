# Plan 066 - Progress inference from screenshot OCR history: level, XP %, silver, buffs, AP/DP, books

Status: open. Autonomy deep dive 2026-10-06 (research 0007 section 1). Lane hint: `build`.

Spec gap: level and XP % (011 / 018) are typed by hand; plan 041's auto
level sample came from the profile API, now off (plan 061). Buff timers and
silver are typed too. Screenshots the operator takes anyway usually show the
level and XP % text, the buff bar and often the silver line.

1. OCR fields (extend the plan 063 extractor, same `{value, conf, why}`
   contract): `level` (1-75 within `levels.LEVEL_RANGE`), `xp_pct`
   (0-100, 3 decimals), `ap` / `aap` / `dp` when the character window is in
   the shot. Region hints are relative crops in a tracked data file
   `server/ew/data/ocr_regions.json` (per UI scale, `verified: false`),
   never pixel literals in code.
2. Sanity over history (S7): a level sample is accepted only if it is >=
   the last level and XP % moves forward within a session (a level-up
   resets it); a sample that breaks monotonicity goes to the review queue.
3. Feeds: accepted level / XP samples post as plan 011 samples (source
   `ocr`); buffs start plan 005 timers; silver deltas between two shots in
   one plan 062 session give a silver/h estimate; AP/DP update plan 023 /
   034 gear score with source `ocr`; an XP % jump across a plan 060 book
   the operator just added suggests a `book_use`.
4. Manual entry stays and always wins over an older OCR sample.
5. Tests: `tests/test_ocr_infer.py` - stub OCR fields feed leveling
   samples; non-monotonic sample queued; level-up reset accepted; buff
   timer started; AP/DP update with source tag; book-use suggestion.

Acceptance: a fixture series of 4 shots (Lv 61 12.5 % -> 13.1 % -> Lv 62
0.4 % -> stale 12 %) yields 3 accepted samples, 1 queued, and a level ETA
with no typing; gates green; verifier PASS within 3 rounds; one push.

ToS check: OCR of saved screenshot files only; no live capture, no game
input, no memory read, no client file.

Depends on: 063, 011, 060.

Dependency guard: before writing code the lane checks that `server/ew/leveling.py` (plan 011), `server/ew/xpbooks.py` (plan 060) and the plan 063 auto-OCR queue (domain `ocr_review` in `server/ew/ocr.py` or a new `server/ew/ocrauto.py`) exist. If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["063", "011", "060"]` into its progress JSON (`ops/loop/control/progress/p066-build.json`) and exits 0.
