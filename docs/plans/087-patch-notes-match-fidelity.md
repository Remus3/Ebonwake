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

## As-built deviations (build lane, self-adjudicated 2026-10-08)

1. Fixture is `tests/fixtures/notices/patch-20261008-asia.html`, not `.txt`.
   Decision: the real U+2192 / U+2019 / U+2013 / U+00A0 glyphs are written as
   numeric character references and reach the test through
   `eventnotices._lines` (the production page parser), which decodes them.
   Alternatives: a raw UTF-8 `.txt` (breaks the ASCII-only rule and
   `tests/test_ascii_lf.py`); `\u` escapes decoded by the test. Why: FLEET
   item 8 is absolute, and the HTML path is the one real pages take. A test
   asserts the decoded text holds the real glyphs. Reverses if: the ASCII rule
   gains a fixture exemption.
2. The 14 official bands live in a sibling key `kill_xp_cap_official` on the
   epoch row, not inside `kill_xp_cap`. Decision: `levels.kill_cap_note` reads
   `kill_xp_cap` first-match, so mixing the lists would change the shown cap
   note for Lv 6-49 and 62-64 today; the plan says the new rows are read by
   nothing else. `validate_epoch` validates the official list (ids, rising
   bands, `cap_pct`, source, hint compiled) and copies neither it nor any
   hint key into the epoch view (`kill_xp_cap` rows still surface only
   `level_min / level_max / note`). The 4 old rows gained `id` (`cap-<lo>-<hi>`),
   `verified: false`, a `verify` hint and `superseded_by` (ids in the official
   list, checked). Hinted rows are keyed `xp_epochs.json#band-<lo>-<hi>` and
   `#cap-<lo>-<hi>`. Alternatives: one list with a `kind` flag. Why: zero
   behaviour change until a data pass adopts a confirmed band. Reverses if: a
   data pass folds the confirmed table into `kill_xp_cap`.
3. Old 62-75 row: contradicted by any `62 - 64 ... <n> %` (the split itself
   proves the row wrong) or a `62 - 75` band with a value other than 0.01 %.
   The other 3 old rows carry the same hint as their official twin, so they
   come out `confirmed` with the fixture.
4. `brackets.json` gains two top-level lists `level_bonus` (6 rows
   `lv-bonus-70..75`, `ap_vs_monsters` = `monster_dr` = 3 x (lv - 69)) and
   `level_gap_dr` (1 row, `dr_per_level` 3, `max_levels` 3), validated by
   `brackets.validate_level_rows` inside `load_tracked` (which still returns
   only the 3 bracket tables). The 6 level rows share one hint: the per-level
   rule tied to "each / per level" plus the printed total sequence
   `(3 / 6 / 9 / 12 / 15 / 18)` built from the stored totals (a test pins
   that), and the level rows are title-gated to the 2026-10-08 notes like the
   epoch (verifier round 1: an item line "+5 Extra AP Against Monsters" in a
   later patch must not contradict them). Reverses if: the NA notes print a
   per-level table, or a later patch changes the rule (re-hint then).
5. `pearl-outfit-set` also gained an alias (`Pearl Outfit`) and `{name}`:
   the official text writes "Pearl Outfit 4-part set effect", which the
   085 `Pearl Outfit set` hint missed; the plan requires the three presets
   to confirm. Its hint now needs `Combat EXP` between the name and the
   value (round 1: "Pearl Outfit sale ... up to 30%" contradicted it). The
   epoch contradict `maximum (?:character )?level` became
   `max(?:imum)? character level` (the official text says "max character
   level"; round 1: "max level of the Season Pass to 60" must not match);
   the epoch expect already accepts `level: 70 -> 75` once folded, so it is
   unchanged.
6. `check` also folds the notice title; `load()` validates the fold table
   (cached per path) so a broken table is a `dataverdicts` error, not a crash.
   Aliases: max 4, each 1..60 printable ASCII; the row name joins the
   alternation only when it is printable ASCII; `{name}` with neither name
   nor alias is a load error. `xp_buffs.json` was re-laid one row per line
   (values unchanged).
7. Band hints use a digit-safe boundary `(?<![0-9])(?<![0-9]\.)` on both the
   band and the value (so `0.25 %` never reads as `0.2 %`, `Lv. 41` never as
   band 1) and a gap `[^\n%]{0,30}?` that stops at the first `%`, so a
   one-line list `1-5 20 %, 6-10 18 %` checks each band against its own
   value. Book hints guard `Large` with `(?<!Extra )`, and a value followed
   by `at / for Lv <n>` with n != 66 is not the row's value (the rows hold
   the Lv 66 figure; round 1); a book gap never crosses `Lv` (round 2: "Small
   at Lv. 61: 0.5%" stays silent). Round 2: band and book hints read
   `old -> new` (`->` / `=>` / `to`): an optional old value plus arrow may
   precede the checked value, and a value followed by an arrow is never the
   checked one, so "0.2% -> 0.5%" contradicts the 0.2 % row instead of
   confirming it.
9. `patchverify.MAX_PATTERN` 200 -> 300. Decision: the round-2 band hints
   (digit-safe boundaries + old-value prefix + arrow guard; the 65+ band
   spells its open end) run to ~235 chars. Alternatives: split a hint across
   several expect patterns (all must match, so it cannot express an
   alternative), drop the digit guards (reopens round-1 / round-2 false
   verdicts). Why: the cap only bounds tracked, reviewed data. Reverses if: a
   generic hint builder replaces hand-written patterns.
10. `fold` also collapses runs of spaces / tabs to one space (line breaks
    kept): round 3 showed an NBSP before an arrow folding to a second space,
    which the one-space hint guards let through as a false confirm of the
    old value. Regression cases in
    `test_refute_r2_old_values_and_level_keyed_books`. Verifier:
    refute-rounds 3/3. Adjudicator: accept with the round-3 fix, no round 4
    (alternatives: leave WIP unmerged in `adjudicate`; round 4 forbidden by
    order 7); why: the one round-3 defect is fixed at the root with a
    regression test and plan 085 deviation 9 is the precedent. Reverses if:
    another whitespace form is shown to split a hint (file a ROADMAP row).
8. Fold table also maps U+FF5E / U+301C (fullwidth tilde, wave dash) to
   `~` so a band written `1 ~ 5` in those glyphs still matches (round 1).
   Reverses if: never; the table is data and only grows.
11. Hint tuning against the live NA page 10678 (lane H0a5846, re-run of
    H1c02d2, which ended no-change with no reason). Cause of the 36 `silent`
    rows: the Asia fixture's wording was reconstructed, the NA page differs.
    Its tables render one cell per line (`1~5\n20%`, `70\n3\n3`), which every
    `[^\n%]` band / book gap refused; books are labelled `(S)/(M)/(L)/(XL)`
    with `0.20%` / `7.50%`; the level bonus reads `Extra AP Against Monsters
    +3 and Monster Damage Reduction +3 are gained from level 70` plus a table;
    the gap DR reads `For each level you are above a monster ... +3` / `level
    difference of up to 3`; buff lines carry a comma and a non-numeric `to`
    (`talking to NPCs`); the Pearl line puts `Combat EXP` before the name.
    Decision: hints only (no data value, no code): band / cap / book gaps
    become `[^%]` (still stop at the first percent, so no neighbour can be
    borrowed); book labels and values take the NA spellings; lv-bonus and
    gap-DR expects become `(?:<Asia wording>|<NA wording>)` with the NA table
    row anchored on the table header (`<lv>\s+<ap>\s+<dr>`) and a matching
    per-row contradict; buff gaps drop `,` and only stop at a numeric `to`;
    Pearl takes both word orders. Result on 10678: 32 confirmed,
    `cap-62-75` contradicted (`62-64 0.02%` - the notes split that coarse,
    superseded row, which its own guard was written to catch), the 4
    drop_buffs rows stay silent (the page never states them). Older pages
    10599 / 10621: no verdict change. Fixture
    `tests/fixtures/notices/patch-20261008-na.html` is cut from the live
    Detail markup (one robots-allowed GET): verbatim lines with trailing
    blanks trimmed, non-ASCII as numeric character references. Alternatives:
    code-side "table join" in `fold` (changes every hint's line semantics,
    out of scope "hints only"); NA-only patterns (breaks the Asia fixture
    tests). Why: smallest change, both wordings covered, regressions pinned
    in the H0a5846 tests. Reverses if: a later NA page renders the tables
    inline again and a hint goes silent (re-tune, file a ROADMAP row).
