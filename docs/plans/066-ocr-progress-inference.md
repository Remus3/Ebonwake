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

## As-built (2026-10-06, lane build)

Code: `server/ew/ocrinfer.py` (extractors, S7 sanity, book suggestion,
silver/h), `server/ew/data/ocr_regions.json`, hooks in `ocrauto.py`
(`leveling` / `progress` / `play` injected; new review / commit kinds `level`,
`gear`, `book_use`), `leveling.py` (`ocr_sample`, `ocr_sample_del`, samples
tagged `source: "ocr"`, `level_source: "ocr"`), `progress.py` (`gs_src`
per stat, `gs_ocr`, `gs_restore`), `xpbooks.py` (`recent`, `used_index`),
dashboard card rows in `app/shared/ewcore.js` + `app/dashboard/game.js`.
Tests: `tests/test_ocr_infer.py`, `app/test/ocr_infer.test.js`.

## As-built deviations

1. Region hints are a tie-break, not a crop. Decision: the OCR still reads
   the whole frame; when the frame size is known (PNG / JPEG / BMP header of
   the saved shot) a read inside its region wins over another read of the same
   field; nothing is penalised. Alternatives: crop to the region before OCR;
   penalise reads outside it. Why: every row is `verified: false` (estimated
   layout); a wrong crop or penalty would lose good reads. UI scale is fixed
   at 100 (no setting yet). Reverses if: a region row is measured and set
   `verified: true` - then outside reads may be penalised / cropped.
2. Monotonicity is checked against the whole history, not only within one
   plan 062 session. Decision: a lower level than the known one (profile
   markers included) or a lower XP % at the same level is held for review in
   any session. Alternatives: only within a session. Why: XP never drops
   across sessions except by death penalty, which is rare and reviewable;
   the session-only rule would let a stale shot from yesterday through.
   Reverses if: death-penalty drops make the review queue noisy.
3. A level sample needs the level and the XP % in the same shot (same line,
   or a neighbour line at lower confidence). A bare percent is not used.
   Alternatives: pair a bare % with the current level. Why: a bare % is
   ambiguous across a level-up. Reverses if: real HUD shots show the % apart
   from the level.
4. Manual wins: an OCR read whose shot is older than (or in the same second
   as) a typed sample is held for review; a typed sample in the same second
   replaces an OCR one; a gs stat typed after the shot holds the OCR stat for
   review (accepting it from review is the operator's call and wins).
   Alternatives: drop such reads silently. Why: review keeps the read
   visible at no risk. Reverses if: the queue fills with stale reads.
5. Book use: same level only, shots at most 1 h apart, jump minus the grind
   gain at the prior rate within 35 percent of one book of a size added in
   the last 24 h and still owned; always a review item, never auto-committed.
   Alternatives: auto-commit; also across a level-up. Why: a wrong book use
   corrupts the observed book value (plan 060 median). Reverses if: the
   suggestion is measured right often enough to auto-commit.
6. Silver/h uses the first and last OCR silver sample inside the open play
   session (else the last closed one), at least 60 s apart; shown in the
   auto-OCR card. Alternatives: a median of pairwise deltas. Why: two shots
   is the plan's own case. Reverses if: spending mid-session skews it.
7. Known limits (verifier round 1 minors, adjudicated as accepted): two
   shots in the same second keep one OCR level sample (the later read), so
   undoing the earlier commit removes it; an accepted book use is recorded at
   the level current at accept time (plan 060 `book_use` contract). A gs stat
   from an older shot never overwrites a newer read (fixed in round 1).
   Reverses if: either case is seen in real use.
8. Buff timers needed no new code: plan 063 already arms them from the same
   shot; plan 066 only asserts it alongside a level read.

Dependency guard: before writing code the lane checks that `server/ew/leveling.py` (plan 011), `server/ew/xpbooks.py` (plan 060) and the plan 063 auto-OCR queue (domain `ocr_review` in `server/ew/ocr.py` or a new `server/ew/ocrauto.py`) exist. If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["063", "011", "060"]` into its progress JSON (`ops/loop/control/progress/p066-build.json`) and exits 0.
