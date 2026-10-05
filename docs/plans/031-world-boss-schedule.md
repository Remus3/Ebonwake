# Plan 031 - World boss schedule: NA table as sourced data, DST-correct next-spawn API

Status: open. Deep dive + UX audit 2026-10-05 session 4 (research 0003-0005). Lane hint: `data`.

Spec gap: research 0003 C04 (value 4, effort M, top 4) and section 1.3.
Plan 003 has only a generic "weekly boss rewards" tick; there is no boss
timer. The official Adventurer's Guide "World Bosses" page (NA table in
Pacific Time, image uploaded 2025-12-24, page edited 2026-09-24) is the
source.

1. `server/ew/data/world_bosses_na.json` (tracked, ASCII): `{tz: "PT",
   source: <official wiki URL>, verified: "2026-09-24", note, slots:
   [{weekday: 0-6, at: "HH:MM", bosses: [...]}], rules: {despawn_min: 30,
   short_despawn: {"Quint": 15, "Muraka": 15}, garmoth_loot_per_week: 3}}`,
   transcribed by the lane from research 0003 section 1.3 (the table there
   is the transcription; the image is never OCR-scraped at runtime).
2. `server/ew/bosses.py`: stdlib-only Pacific time conversion (US rule:
   DST from the second Sunday of March 02:00 to the first Sunday of
   November 02:00 local; no `zoneinfo`, which lacks tz data on Windows
   without a package). `next_spawns(now_utc, n=3)` -> `[{bosses, at_utc,
   at_pt}]`; spawns stay on PT wall clock across DST (open question in
   research 0003, flagged `dst_assumption` in the payload).
3. Ticks: per boss per day "looted" (operator click) and Garmoth n/3 this
   week (Thursday 00:00 UTC reset, reusing plan 021 helpers if present,
   else `today.last_weekly_reset`), stored in the EW store.
4. Routes: `GET /api/bosses` (next 3 + today's remaining + ticks), `POST
   /api/bosses {"tick": {boss, day}}` / `{"untick": ...}` with validation.
5. Tests `tests/test_bosses.py`: DST edges (2026-11-01 and 2027-03-14),
   the Muraka cross-check (Thu 14:00 and Sat 17:00 PT = Thu 21:00 UTC and
   Sun 00:00 UTC in summer time), Sunday-to-Monday wrap, schema test for
   the data file, tick validation.

Acceptance: tests above green; the data file's rows match the research
table (fixture compares the count per day); gates green; verifier PASS
within 3 rounds. A follow-up row is NOT needed for the DST open question:
the payload flag is the reminder; the operator or a later deep dive
re-checks after 2026-11-01.

ToS check: sourced static data from an official robots-allowed page,
operator ticks; nothing reads the in-game boss notification.

Depends on: none.
