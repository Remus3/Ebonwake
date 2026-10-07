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

## As-built deviations (build lane, self-adjudicated 2026-10-07)

1. Hints may sit on any data row, not only `verified: false` ones. Decision:
   the three `xp_buffs.json` presets the plan names already carry a date
   (`verified: "2026-10-05"`, from the BDFoundry summary, a secondary
   source), so a hint is accepted on any dict holding `verified`; only
   `verified: false` rows without one are listed `unchecked`. Alternatives:
   flip the presets to `false` first. Why: the plan seeds exactly those rows;
   the primary source should still check them. Reverses if: a hint on a
   verified row ever produces noise.
2. Hints compile case-insensitive (the `re.IGNORECASE` flag, no inline
   `(?i)`). Why: patch-notes casing drifts ("Pearl Outfit set" vs data
   "Pearl outfit set"). Reverses if: a hint needs case.
3. The page text is kept, not only the verdicts. Decision: plan 064's
   fetcher stores the visible text of every `[Updates] Patch Notes` Detail
   page it reads in `eventnotices_patchnotes.json` (newest 4, 300 kB cap);
   `dataverdicts` re-checks them on read and persists verdicts in
   `ops/runtime/data_verdicts.json`. A patch-notes page cached before this
   plan (no text) is read once more inside the same 5-GET cap. Alternatives:
   verify inside the fetch and keep verdicts only. Why: verdicts follow later
   hint edits without a new GET, and the fetcher stays free of data rules.
   Reverses if: the cache size matters.
4. Fetch order: maintenance, then patch notes, then Hot Time, then list
   order (one shared order, not "first on its board"). Why: the 5-GET budget
   is shared across boards; maintenance stays most time-critical. Reverses
   if: the budget is raised.
5. Verdict merge across pages: oldest page first; a newer non-silent verdict
   replaces an older one, a silent page never erases a verdict. Each verdict
   carries the row's fingerprint (sha1 of the tracked row incl. its hint);
   when a lane edits the row the old verdict is dropped on the next read.
   `checked_at` = the page's fetch time (deterministic, so a re-read writes
   nothing). Reverses if: a patch reverts a value and the older verdict
   should win.
6. "Treat confirmed as verified": the epoch view's `verified` becomes true,
   presets / drop rows show the patch-notes date; every surfaced row gains a
   `patch` field `{verdict, date, evidence, url}` (null when silent / none).
   An operator-overridden drop row or an operator epoch replacing a tracked
   one carries no verdict (the value is no longer the tracked one).
   Tooltip text: "verified by patch notes <date>"; contradicted rows show a
   `check` badge (epoch line `[check]`, epoch list, preset option, drop row).
7. Digest row `data` reasons: `contradicted` (warn) and `error` (warn: a
   broken hint table pauses verdicts rather than crashing the server).
   Every digest row gained a `lines` list (empty except `data`). Detail is
   singular for one row ("1 data row contradicted ..."). With no patch
   notes read yet the row is `ok` with "no patch notes read yet".
8. "Hand-off writer lists contradicted rows": the loop tick does not write
   the hand-off file, so `tools/ew_loop.py data_items()` reads
   `ops/runtime/data_verdicts.json` in the main checkout and adds one
   hand-off-kind work item per contradicted row (stable id `D<sha1>`), worked
   by a lane like any hand-off bullet. The quoted evidence (<= 200 ASCII
   chars of official page text, double quotes swapped) is framed as data.
   Alternatives: write bullets into `EW-NEXT-SESSION.txt` from the server.
   Why: only /done writes the hand-off; the work list is where the loop
   acts. Reverses if: MAIN asks for hand-off bullets instead.
9. Hint style (verifier rounds 1-2): every contradict gap (and every gap
    before the row's keyword) in a seeded hint is a tempered token `(?:(?!\bto\b|->|=>)[^\n;,]){0,N}`, so a match never runs
    past the first `to` / `->` or into the next item of a `,` / `;` list; a
    second value on the same line can no longer contradict a correct row.
    Expect gaps stop at `;` / `,` too (round 3: no false confirm from the
    next item). Regression cases in `tests/test_patchverify.py`. Verifier:
    refute-rounds 3/3; round 3 found no defect in the round-2 fix, its two
    false-confirm notes were applied without a round 4 (CLAUDE.md order 7).
    Reverses if: real patch notes write one change across a comma.
10. Fetcher re-read guard (verifier round 1): a listed patch-notes page with
    no kept text is re-read only when it would land in the newest-4 window,
    so older pages never eat the 5-GET budget; `dataverdicts` reads the page
    cache at most every 30 s (one view looks up ~25 rows).
11. Loaders that accept a hint: `levels.validate_epoch` (tracked rows),
    `levels.validate_preset`, `grind._check_drop_row`; each compiles the hint
    so a bad regex fails its own loader too. Other data files get the key
    when their rows are first hinted. Reverses if: a generic schema layer
    lands.
