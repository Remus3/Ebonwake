# Plan 023 - AP/DP bracket calculator: bonus AP, DR %, next-bracket gain on the Progress card

Status: open. Deep dive + UX audit 2026-10-05 session 4 (research 0003-0005). Lane hint: `data`. Priority row.

Spec gap: research 0003 C01 (value 5, effort S, top 1). The Progress
Character card (plan 004) stores sheet AP / AAP / DP but says nothing about
what they are worth: bonus AP brackets make 245 -> 276 sheet AP move bonus
AP 48 -> 142 (the 257-272 cliff) and DP brackets give damage-reduction rate.

1. `server/ew/data/brackets.json` (tracked, ASCII): three tables
   `ap` (sheet AP lower bound -> bonus AP; also used for AAP), `dp_dr`
   (DP -> damage-reduction %), `dp_all_dr` (DP -> all damage reduction),
   each row `{min, value}`, each table with `source`
   (BDFoundry AP/DP brackets guide URL), `verified` ("2026-08-13" page
   date) and `note`. Rows transcribed by the lane from the cited page as
   facts (numbers only); a schema test asserts monotonic `min`, no gaps,
   ASCII.
2. `server/ew/brackets.py`: `load()` merging the tracked file with operator
   overrides from the store (`brackets_override`, same shape, editable via
   `POST /api/progress {"brackets_set": {...}}` with validation);
   `lookup(table, x)` -> `{bracket_min, bracket_max, value, next_min,
   next_gain}`; `summary(gs)` -> `{ap, aap, dp}` each with the lookup and a
   `cliff` flag when the next 16 AP gain more bonus AP than the previous 16.
3. `GET /api/progress` adds `brackets` (the summary for the stored
   character gs; `null` when gs unset).
4. Dashboard: the Character card in `app/dashboard/progress.js` shows
   "AP 251 -> +57 bonus; +2 AP to 253 gives +12" and the same for AAP and DP
   (DR %); a `verify` badge when the table's `verified` is older than the
   newest XP epoch start (plan 018 pattern). Pure formatter in `ewcore.js`.
5. Tests: `tests/test_brackets.py` (lookup edges at bracket bounds, below the
   first row, above the last, override precedence, validation 400s);
   `app/test/progress.test.js` formatter cases.

Acceptance: tests above green; the known pairs from research 0003 section
1.2 (245 -> +48, 257 -> +83, 273 -> +142) pass as fixtures; gates green;
verifier PASS within 3 rounds.

ToS check: static sourced data plus operator-typed gear score.

Depends on: none.
