# Plan 048 - Quick-entry parsers (1.2b, 30d, 90m) + local time everywhere

Status: open. Deep dive + UX audit 2026-10-05 session 4 (research 0003-0005). Lane hint: `build`.

Spec gap: research 0005 candidate F13 (value 3, S) with M5 (exact silver
inputs), M6 (Hot Time / epochs in UTC, events in local, deadeye in UTC) and
M10 (buff minutes raw, `43200` for a Value Pack).

1. `app/shared/ewcore.js` parsers: `parseSilver("1.2b" | "1,234,567" |
   "850m" | "12k")`, `parseDuration("30d" | "1h30m" | "90m" | "45")`,
   `parseLocalTime("21:00")` -> UTC for storage; formatters
   `fmtDurationShort` (`30d`, `1h`) and `fmtLocal(utcIso)` with
   `title="UTC hh:mm"`.
2. Every silver, duration and time input in `grind.js`, `leveling.js`,
   `events.js`, `deadeye.js`, `market.js` uses the parsers; stored values
   stay UTC / integers (no store migration).
3. A "paste PT" helper on the Hot Time and epoch forms converts a Pacific
   time (patch-note style) to UTC. Plan 031 owns the US DST rule (Python,
   `server/ew/bosses.py`); this plan adds `ptToUtc` in `ewcore.js` as a
   JS port of that rule and tests it against the same edge dates as
   `tests/test_bosses.py` (2026-11-01, 2027-03-14) so the two cannot
   drift.
4. Buff rows: durations shown `30d` / `1h`, unarmed `off` muted, armed rows
   first.
5. Tests: `app/test/ewcore.test.js` parser and formatter tables (invalid
   inputs rejected, round trips, DST edges).

Acceptance: node tests green; existing module tests green; gates green;
verifier PASS within 3 rounds.

ToS check: input parsing in EW's own dashboard only.

Depends on: 031.

Dependency guard: before writing code the lane checks that `server/ew/bosses.py` exists (plan 031). If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["031"]` into its progress JSON (`ops/loop/control/progress/p048-build.json`) and exits 0. Plan 019's tick turns that clean, marked run into item state `blocked` (not `no-change`) and re-dispatches the row once every Depends-on row is `[x]`; plan 019's work-list gate normally keeps the row from being dispatched that early. If this row ran before plan 019 landed and was recorded `no-change`, 019's re-arm step makes it dispatchable again.
