# Plan 038 - Drop-buff cap calculator (300/400/500 % caps, rate vs amount) + Blessing of Agris ROI

Status: open. Deep dive + UX audit 2026-10-05 session 4 (research 0003-0005). Lane hint: `build`.

Spec gap: research 0004 F09 (value 4, S) and F10 (value 2, S). Item Drop
Rate caps at 300 percent; Arsha server, node, castle and Thriving Earth
bypass to 400; the Blessing of Agris Scroll goes beyond to 500 (research
0004 section 2: 1 B silver, 60 min, sale until the 2026-10-22 maintenance,
unused items removed 2026-11-05). Every one of these numbers is game data
and goes into the data file, not code. Drop RATE and drop
AMOUNT (Agris Fever, item collection scrolls) are separate and sources
disagree on values.

1. `server/ew/data/drop_buffs.json` (tracked): rows `{name, rate_pct?,
   amount_pct?, bypass: none|to400|to500, source, verified}` from research
   0004 section 2 (BDFoundry 2024-11-27 list, official notices for Agris
   scroll); disputed values (Agris Fever amount, ecology knowledge) carry
   `verified: false` and a note. The same file holds a `caps` row
   `{base_pct: 300, bypass_pct: 400, beyond_pct: 500, source, verified}`
   (BDFoundry 2024-11-27 / official Agris notice) and an `agris_scroll` row
   `{price_silver: 1000000000, minutes: 60, sale_until_utc: "2026-10-22",
   removed_utc: "2026-11-05", per_week: 10, source, verified}` (official
   NA patch notes, research 0002 s3 / 0004 s2). Operator overrides for
   every row (caps and scroll included) live in the store. A test parses
   `server/ew/grind.py` with stdlib `ast` and walks `ast.Constant` nodes,
   skipping docstrings (so comments, docstrings and identifiers never
   match); it fails on a numeric constant whose value equals 300, 400, 500
   or 1000000000 (whole values, any spelling: `1e9`, `1_000_000_000`) or a
   str constant matching `\b2026-10-22\b` / `\b2026-11-05\b`; `3000`,
   `1500` or `"x300"` do not match.
2. `server/ew/grind.py`: `drop_stack(active)` -> `{rate_total, rate_capped,
   wasted, amount_total, cap_used}` with caps read from the `caps` row;
   active buffs come from plan 005 buff
   timers by name match plus operator toggles for passive sources (node,
   fame, night).
3. `agris_roi(silver_h, scroll)` -> break-even silver/h using the plan 005
   spot average, with price, minutes and sale/removal dates all taken from
   the `agris_scroll` row (after overrides); the card hides the ROI line
   once `removed_utc` has passed.
4. `GET /api/grind` adds `drops` and `agris_roi`; dashboard card "Drop
   rate" on the Grind tab with a `wasted` flag when over cap.
5. Tests: `tests/test_grind.py` additions (caps, bypass order, wasted,
   amount kept separate, ROI arithmetic); data schema test.

Acceptance: tests green; gates green; verifier PASS within 3 rounds.

ToS check: sourced data and operator toggles; no game input.

Depends on: none.
