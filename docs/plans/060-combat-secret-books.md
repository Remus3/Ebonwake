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

Self-adjudicated by the build lane (2026-10-06); each: decision /
alternatives / why / reverses if.

1. Ledger store domain. Decision: own store domain `xpbooks`
   (`server/ew/xpbooks.py` `XpBooksService`), injected into
   `LevelingService(books=...)`. Alternatives: a `books` key inside the
   `leveling` doc. Why: `LevelingService._load` rebuilds the doc from known
   keys, so an extra key would be dropped by every other leveling write.
   Reverses if: the leveling doc gets a pass-through for unknown keys.
2. Ledger shape. Decision: `{owned, used, added}`; `added` logs each
   `book_add` {at, size, n, activity} (capped 200) and a use spent from the
   owned count carries `from_owned`. Alternatives: only `{owned, used}`. Why:
   the weekly expectation needs when / where books came from, and
   `book_del` (undo a use) must give back only a book that was really spent.
   Reverses if: book sources are tracked elsewhere.
3. Weekly expectation. Decision: a source with a plan 033 `weekly_row`
   (Black Shrine) expects `per_week x n` books (row capacity) and shows this
   period's ticked `done x n`; other activities count the books the operator
   added with that activity over the trailing 7 days. `WeeklyService.counts()`
   is new (ticks only, no character / bracket feed). Alternatives: expected =
   ticked count only (reads 0 every reset). Why: an expectation that resets
   to zero each week makes the "with books" ETA jump. Reverses if: the
   operator wants done-only.
4. Data schema additions. Decision: sources carry `name` and `weekly_row`;
   single-outcome activities (Black Shrine, Guild Boss, Altar, ranked PvP)
   put the reward in `win` with `loss: null`; the file has top-level
   `source`, `read`, `note`; `pct_at` keys load as int levels. Nearest-level
   ties pick the lower level. Alternatives: a separate `reward` key. Why:
   one schema for every source, deterministic fallback. Reverses if: a
   source with three outcomes appears.
5. Gain across a level. Decision: `pct_after < pct_before` counts as a
   level-up (`100 - before + after`), recorded at the level the book was read
   at. Alternatives: reject. Why: XL books (15 %) cross a level often.
   Reverses if: book XP is shown to carry over differently.
6. Corrections. Decision: `book_add` takes n in -99..99 (non-zero; negative
   fixes a typo, never below 0); `book_use` with none owned is still
   recorded (owned stays 0). `books_to_next` returns a count per size.
   Why: the operator types counts from memory. Reverses if: never needed.
7. Below Lv 60. Decision: the card shows "books from Lv 60" (+ the open
   Lv 60 deadline, e.g. Olvia Academy) whenever the level is under 60 or
   unknown, not just once. Alternatives: a dismissable one-time hint. Why:
   no dismiss state to store; the line is one muted row. Reverses if: the
   operator asks for it gone.
