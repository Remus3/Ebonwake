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

## As-built (lane build, 2026-10-06)

`server/ew/bossdrift.py` (config / parse_table / normalize / remote_slots /
diff / DriftClient / DriftService), wired as `EWServer.drift`: GET
`/api/bosses` carries `drift` {state ok|differs|unknown, source, robots,
checked, error, n, diff[{kind, weekday, local_at, remote_weekday, remote_at,
local, remote, text}], banner}; `/api/state` sources.bossdrift {updated,
ttl_s, status, robots, banner}. Only GET `/api/bosses` may start the
once-a-day background refresh; POST and `/api/state` read the cache only.
Dashboard: `C.bossDriftBanner` -> Bosses card banner with one line per slot,
System tab line + `differs` source pill as warn. Tests:
`tests/test_bossdrift.py` (42), `app/test/bossdrift.test.js` (6).

## As-built deviations

1. Fixture is synthetic, not a saved copy of the live page.
   Decision: `tests/fixtures/bossdrift/mainTbl_na.html` (and `_moved`) are
   written in the expected `#mainTbl` shape (days as rows, times as header
   columns, `<br>`-separated names, decoy table + script) from the tracked
   table. Alternatives: fetch the live page once to trim it; skip the
   fixture. Why: the build lane has no network grant for an ad-hoc GET, and
   the parser is tolerant (day row names Mon / Monday, 24 h or 12 h headers,
   `<br>` / `,` / `&` / `+` / `/` separators); any shape it cannot read is a
   parse failure = state `unknown`, no banner, so a wrong layout guess is
   silent. One guess can alarm falsely: if `drift.tz` does not match the
   zone the live page writes, every slot reads as moved / changed (the
   banner says "verify", never edits). Reverses if: the first live run
   reports `unknown` with a `parse:` error, or a banner covering most
   slots by one constant offset - then save a trimmed live copy as the
   fixture and fit the parser / `tz` to it.
2. The `drift` block (url, robots, table_id, tz, aliases) lives in
   `world_bosses_na.json`, not in code. Decision: source + alias map in the
   data file per plan item 1; `tz` PT or UTC (UTC shifted with plan 031's US
   DST rule at fetch time). Alternatives: constants in the module. Why: one
   reviewed data file for everything schedule-related. Reverses if: a second
   public source is added (then a list of sources).
3. A move is reported as ONE "moved" entry (same bosses, same set, within
   180 min, midnight-wrapping) instead of a missing + an extra slot.
   Alternatives: raw per-slot set difference (a 1 h DST shift would read as
   2 slots). Why: the acceptance counts the moved slot once. Reverses if:
   operators find paired moves hide real changes.
4. No settings toggle (unlike `coupons.check`). Decision: on whenever the
   data file carries a valid `drift` block; removing the block turns it off.
   Why: the plan asks for none and it is one robots-gated GET a day.
   Reverses if: the operator or a plan asks for a toggle.
5. A cached result whose last refresh errored and is past its TTL reads as
   `unknown` (no stale banner). Why: plan item 3 - unreachable = unknown.

refute-rounds: 1/3 (verifier round 1 PASS; advisories: tz mismatch false
alarm documented in deviation 1; greedy move pairing keeps the count right).

Dependency guard: before writing code the lane checks that `server/ew/bosses.py` (plan 031) and `server/ew/coupons.py` (plan 014) exist. If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["031", "014"]` into its progress JSON (`ops/loop/control/progress/p072-build.json`) and exits 0.
