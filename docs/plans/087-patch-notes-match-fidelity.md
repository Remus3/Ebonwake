# Plan 087 - Patch-notes match fidelity: Unicode fold, name aliases, hints for the official 2026-10-08 numbers

Status: open. Deep dive 2026-10-08 (research 0015 section 1 F1, 6, 7).
Lane hint: `data`.

Spec gap: plan 085's hints accept only ASCII `to` / `->` / `=>` and run on
the raw page text; `patchverify.check_row` folds nothing (only the evidence
line is ASCII-squashed). The official 2026-10-08 notes (Asia board 19899,
read 2026-10-08; NA posts today) write "max character level: 70 U+2192
75" and "Adventure's Boon" where EW data says "Adventure Blessing". So the
cap-75 epoch and the boon preset come out `silent`, not `confirmed`. The
tracked `kill_xp_cap` has 4 of 14 bands, and its 62-75 band (0.01 %) is
wrong: the official text gives 62-64 0.02 %, 65+ 0.01 %. No hint covers the
bands, the Lv 70+ AP / DR bonus or the level-gap DR.

1. Fold before match (`server/ew/patchverify.py`, pure): `fold(text)`
   maps U+2192 / U+21D2 / U+2794 / U+25BA / U+27A1 to `->`, U+2013 / U+2014
   / U+2212 to `-`, U+00A0 / U+2009 / U+202F to a space, U+2018 / U+2019 /
   U+201C / U+201D to `'` / `"`, U+FF05 to `%`, U+00D7 to `x`; any other
   non-ASCII char becomes a space. `check` and `check_row` match on
   `fold(text)`; the evidence line comes from the folded text. The fold
   table is data (`server/ew/data/patch_fold.json`, ASCII: code points as
   `"U+2192"` keys), loaded and validated once.
2. Name aliases: a hint may carry `"aliases": ["Adventure's Boon", ...]`
   (max 4, ASCII); the token `{name}` in an expect / contradict pattern
   expands to `(?:<row name>|<alias>...)` (each `re.escape`d) at compile
   time. Seed the alias on the `adventure-blessing` preset; existing hints
   without `{name}` compile unchanged.
3. Hints for the official numbers (tracked data, no value edits): the
   `cap75-xp-rescale` epoch expect regex also accepts `level: 70 -> 75`;
   one hint per `kill_xp_cap` band, with the official 14-band table seeded
   as `verified: false` rows next to the current 4 (the 4 old rows gain a
   `superseded_by` hint list, so the 62-75 row is `contradicted` by "62 -
   64 ... 0.02 %"); `brackets.json` gains `level_bonus` rows Lv 70-75 (+3
   AP vs monsters / +3 monster DR per level, `verified: false`, Asia source)
   and one `level_gap_dr` row (+3 per level above, max 3 levels); the 4
   `xp_books.json` size rows get hints ("S 0.2 / M 1 / L 7.5 / XL 15 %").
   Loaders that read these rows (`levels.validate_epoch` for nested bands,
   `brackets`, `xpbooks`) compile the hint, per plan 085 deviation 11.
   New rows are read by nothing else in this plan (023 / 012 use them in a
   later data pass; the AP calc does not change until a band is
   `confirmed`).
4. Tests: `tests/test_patchverify.py` - a fold fixture
   (`tests/fixtures/notices/patch-20261008-asia.txt`, reconstructed from
   research 0015 s1 with the real U+2192 / curly apostrophe) confirms the
   epoch, the three buff presets, 14 bands and 4 books; the old 62-75 band
   is `contradicted`; ASCII-only text still matches as before (085
   regression cases unchanged); a fold table with a non-`U+` key or a
   non-ASCII target is rejected at load; `{name}` with no alias compiles to
   the bare name; tracked data byte-identical after a run; no network.

Acceptance: with the fixture, GET /api/data/verdicts shows the epoch, the
three presets, the 14 new bands and the 4 book sizes `confirmed`, the old
62-75 band `contradicted` (digest `data` row `warn`, one evidence line),
and the loop's `data_items()` raises one work item for that band; gates
green; verifier PASS within 3 rounds; one push.

ToS check: no new GET; text comes from the official patch-notes pages plan
064 already reads on the robots-allowed NA host. The Asia page is cited as
research only, never fetched by EW at runtime. EW never edits tracked data
on its own.

Depends on: 085.
