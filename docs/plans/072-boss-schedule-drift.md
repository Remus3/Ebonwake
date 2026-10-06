# Plan 072 - World boss schedule drift check against a public table

Status: open. Autonomy deep dive 2026-10-06 (research 0007 section 2.4). Lane hint: `data`.

Spec gap: plan 031's NA table (`server/ew/data/world_bosses_na.json`) is
hand-maintained; NA times after the 2026-11-01 DST change are unconfirmed
and a silent drift makes every boss alert wrong.

1. `server/ew/bossdrift.py`: at most once a day, robots-gated (plan 014
   `robots_verdict`), one GET of the public NA boss table
   (`mmotimer.com/bdo/?server=na`, table `#mainTbl`); parse day x time x
   boss names; normalize names through an alias map in the data file.
2. Diff against the local table: matching rows = ok; any difference ->
   System card + Bosses card banner "schedule differs from <source> on
   <n> slots - verify" with the diff, never an auto-edit of the tracked
   data file (data changes stay plan-reviewed).
3. Unreachable / disallowed / parse failure = state `unknown`, no banner.
4. Tests: `tests/test_bossdrift.py` with a saved trimmed fixture; robots
   off = no GET; diff of a moved slot; alias normalization; no network.

Acceptance: a fixture with one slot moved by 1 h after DST shows exactly
that slot in the banner; gates green; verifier PASS within 3 rounds; one
push.

ToS check: one unauthenticated, robots-allowed GET a day of a public page;
no game input, no memory read, no client file.

Depends on: 031, 014.

Dependency guard: before writing code the lane checks that `server/ew/bosses.py` (plan 031) and `server/ew/coupons.py` (plan 014) exist. If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["031", "014"]` into its progress JSON (`ops/loop/control/progress/p072-build.json`) and exits 0.
