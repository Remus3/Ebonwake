# Plan 085 - Patch-notes data verifier: check every unverified data row against the official patch notes

Status: open. Deep dive 2026-10-07 (research 0013 sections 1, 7). Lane hint: `data`.

Spec gap: `server/ew/data/` holds 60 rows marked `"verified": false`
across 18 files (grep at HEAD 2026-10-07: enhance_rates 19, weight_sources
9, loot_tables 6, xp_books 4, caphras 4, drop_buffs 4, tracks 6, others 1
each). Confirming them is a manual hand-off item that has rolled over two
sessions ("after the 2026-10-08 patch notes, flip plan 018 epoch verified,
add cap bands"). Plan 064 already lists the Updates board (boardType 2), so
EW sees "[Updates] Patch Notes - <date>" appear and reads none of it. The
2026-10-08 notes will confirm or contradict the level-cap epoch and the
XP-buff presets (Body Enhancement 100 %, Adventure Blessing 30 %, Pearl
Outfit 50 % on Global Lab, research 0013 s1).

1. Hints as data: an unverified row may carry an optional `verify` object
   `{"title": "<regex on the notice title>", "expect": ["<regex>", ...],
   "contradict": ["<regex>", ...]}` (ASCII, compiled at load, max 4 each).
   Seed hints for the rows research 0013 / 0008 name: the `xp_epochs.json`
   cap-75 epoch, the three `xp_buffs.json` presets, the `drop_buffs.json`
   unverified rows. Other rows get hints in later data passes; a row with
   no hint is listed as `unchecked`, never guessed.
2. Reader (`server/ew/patchverify.py`, pure): input = one patch-notes
   Detail page text (fetched by plan 064's fetcher inside its existing
   5-Detail-per-run cap; a title matching `^\[Updates\] Patch Notes` is
   queued first on its board) + every hinted row. Per row: `confirmed`
   (all `expect` match, no `contradict`), `contradicted` (any `contradict`
   matches) or `silent`. Keep at most one 200-char ASCII evidence line per
   verdict plus `notice_no`, `url`, `checked_at`.
3. Verdicts live in runtime state (`ops/runtime/data_verdicts.json`,
   atomic write), keyed `<file>#<row id>`; tracked data files are NEVER
   edited (the plan 072 rule: data changes stay plan-reviewed). Loaders
   that already read `verified` (018 epoch, xp buffs, drop buffs) treat a
   `confirmed` verdict as verified and show `verified by patch notes
   <date>` in the source tooltip; a `contradicted` row keeps its value but
   shows a `check` badge.
4. Surfaces: plan 073 signal digest gains row `data` - `ok` (no
   contradiction), `warn` "<n> data rows contradicted by patch notes
   <date> - see System" with the evidence lines on the System card; GET
   `/api/data/verdicts` -> `{verdicts, unchecked, last_notice}`. The
   hand-off writer (loop tick) lists contradicted rows as data items so a
   lane edits the tracked file.
5. Tests: `tests/test_patchverify.py` - a trimmed fixture
   (`tests/fixtures/notices/patch-20261008.html`, reconstructed from
   research 0013 s1 text) confirms the epoch and flags a seeded stale
   preset (Adventure Blessing 15 %) as contradicted; a row without hint is
   `unchecked`; bad regex in a hint is rejected at load (schema test over
   every data file); verdict file round-trip; tracked data byte-identical
   after a run; no network.

Acceptance: with the fixture, GET /api/data/verdicts shows the epoch
`confirmed` and the stale preset `contradicted`, the digest `data` row is
`warn` with that one line, the Progress leveling card's epoch shows "verified by patch
notes 2026-10-08"; gates green; verifier PASS within 3 rounds; one push.

ToS check: official public patch-notes pages on the robots-allowed host
plan 064 already reads, inside its GET cap; unauthenticated; no game
input, no memory read, no client file. EW never edits tracked data on its
own.

Depends on: 064, 073.
