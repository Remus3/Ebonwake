# Plan 060 - Combat Secret Book ledger: books per activity, observed XP, books-to-level

Status: open. Deep dive 2026-10-06 (research 0006 section 1). Lane hint: `build`.

Spec gap: from the 2026-10-15 maintenance (retroactive from 2026-10-08)
Combat Secret Books give combat XP for Black Shrine, Guild Boss, Altar of
Blood, node / conquest / Roses wars, Red Battlefield, Guild League and
Solare. They need Lv 60, are family-bound and are worth (at Lv 66) Small
0.2 %, Medium 1 %, Large 7.5 %, Extra Large 15 % of a level. With the
post-patch per-kill cap at ~0.01 % from Lv 62 they become a primary XP
source, but EW's level ETA (plan 011 / 018) only knows grinding rate.

1. `server/ew/data/xp_books.json` (tracked, ASCII): sizes `{id: small |
   medium | large | xl, pct_at: {"66": 0.2 ...}, min_level: 60, source,
   verified: false}` and sources `{activity, win: {size, n}, loss: {size,
   n}}` from research 0006 with the BDFoundry URL and date. Values are
   data, never code.
2. `server/ew/xpbooks.py`: ledger `{owned: {size: n}, used: [{at, size,
   level, pct_before, pct_after}]}` in the store. `observed_pct(size,
   level)` = median gain of used books of that size at that level (needs
   one use); falls back to `pct_at` at the nearest level with a
   "Lv 66 value, verify" flag. `books_to_next(level, pct)` and
   `pct_from_owned(level)`.
3. Weekly expectation: rows of plan 033 `weekly_content.json` that map to a
   book source (Black Shrine -> small x1 per run) give "expected books this
   week" from the plan 033 tick counts; other activities are operator-ticked
   on the ledger ("+1 medium: Guild Boss").
4. Leveling card (plan 011): one line "Books: +X % owned, +Y %/week
   expected" and a second ETA "with books" = grind rate plus weekly book
   rate as %/h over the week; hidden below Lv 60 (shows "books from Lv 60"
   once, with the Olvia deadline from plan 024 when set).
5. API: GET `/api/leveling` adds `books`; POST `/api/leveling`
   `{"book_add": {size, n, activity?}}`, `{"book_use": {size, pct_before,
   pct_after}}`, `{"book_del": index}`, validated like plan 011.
6. Tests: `tests/test_xpbooks.py` (observed median, fallback flag,
   books_to_next, weekly expectation from 033 ticks, Lv < 60 hidden, data
   schema); node formatter tests.

Acceptance: a Lv 66 fixture at 40 % with 3 large owned shows +22.5 % owned
and books_to_next = 8 large (60 % / 7.5 %) (fallback values, flagged); an observed use
overrides the fallback; gates green; verifier PASS within 3 rounds; one
push.

ToS check: operator-typed data and tracked data files only; no game input,
no memory read, no client file.

Depends on: 011, 018, 033.

Dependency guard: before writing code the lane checks that `server/ew/leveling.py` (plan 011), `server/ew/data/xp_epochs.json` (plan 018) and `server/ew/weekly.py` (plan 033) exist. If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["011", "018", "033"]` into its progress JSON (`ops/loop/control/progress/p060-build.json`) and exits 0.

## As-built deviations
