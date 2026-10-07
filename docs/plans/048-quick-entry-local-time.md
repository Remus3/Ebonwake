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

## As-built

Dependency guard: `server/ew/bosses.py` present (plan 031), so the lane ran.

`app/shared/ewcore.js` gained `parseDuration` (whole minutes), `fmtDurationShort`,
`ptOffsetHours` / `ptToUtc` (port of `bosses.pt_offset_hours` / `pt_to_utc`),
`parseLocalTime`, `parseLocalDateTime`, `fmtLocal`, `hotWindowView`,
`buffRowView` and `ZONES`; `parseSilver` was rewritten to exact decimal maths.
`parseGrindForm` (silver, trash, vendor price, minutes), `parseWatchForm`
(below / above), `parseShopForm` (silver, hours), `parseHotForm` and
`parseEpochForm` (optional `zone`) use them. Tests: `app/test/ewcore.test.js`
(14 new tables, DST edges 2026-11-01 / 2027-03-14 identical to
`tests/test_bosses.py`; also green under TZ=Asia/Kolkata, Pacific/Chatham,
America/Los_Angeles). Verifier: round 1 refuted (Hot Time UTC zone refused
`9pm`; trash error text), both fixed; round 2 PASS. refute-rounds: 2/3.

## As-built deviations

1. The "paste PT" helper is a zone picker (local / PT / UTC, default local)
   on the Hot Time and epoch forms, not a separate paste box.
   Alternatives: a second "PT" input that converts into the main field; a
   paste-and-parse of a whole patch-note line. Why: one control covers local
   entry, PT patch-note entry and the old UTC entry with the same parser, and
   the stored value reads back in whichever zone is picked. Reverses if: the
   operator wants free-text patch-note lines parsed (dates + times + "(PT)").
2. Recurring Hot Time windows convert with the zone offset at entry time
   (`now`); the stored UTC window does not follow a later DST change of the
   entry zone. Alternatives: store the zone with the window (server schema
   change, plan says no store migration). Why: no migration; BDO announces
   Hot Time per event, so windows are re-entered across a DST change anyway.
   The weekday moves with the start time when it crosses UTC midnight.
   Reverses if: windows gain a stored zone server-side.
3. `parseSilver` now refuses sub-silver precision (`1.0000000001b`) instead
   of rounding, accepts a `t` suffix and comma groups before a suffix
   (`1,500m`). Why: exact inputs (research 0005 M5). Reverses if: never;
   rounding silently changed typed prices.
4. `parseShopForm` hours accept a duration (`2h30m`) besides a decimal; a
   bare number stays hours there (not minutes), so `parseDuration` is used
   only when the text carries a unit. Why: h/day is a rate, `2.5` already
   meant hours. Reverses if: the hours field is removed.
5. `today.js` (reset time `HH:MM UTC`) is out of the plan's five modules and
   untouched. Reverses if: a plan adds it.
6. Fixed a pre-existing red gate on base `b1a8cd9`: the plan 044 mount
   remove button in `app/dashboard/progress.js` lacked the aria-label that
   plan 047's `density.test.js` requires (one line). Why: the lane's gates
   must be green and the fix is the test's own contract. Reverses if: never.
7. `app/test/grind.test.js` "buff minutes input fits 43200" now pins the
   new field contract (maxLength 8, "max 30d", `buffRowView`) instead of
   maxLength 5 / "1-43200". Why: plan item 4 changes the field.
