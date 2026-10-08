# Plan 088 - Monster Zone Info OCR: per-kill EXP and recommended level per zone, kills-to-level, cap-bound buff flag

Status: open. Deep dive 2026-10-08 (research 0015 section 1 F2, 6).
Lane hint: `build`.

Spec gap: since the 2026-10-08 patch the in-game Monster Zone Info panel
lists each zone's monsters with the Combat EXP a kill gives and shows a
recommended level before the zone name (official notes, Asia board 19899,
read 2026-10-08). The per-kill EXP cap includes XP buffs (1-5 20 % ... 56-61
0.2 %, 62-64 0.02 %, 65+ 0.01 % of a level). EW knows neither number: the
level ETA (011 / 018) uses the observed XP rate only, the recommender
(012) a coarse community `xp_tier`, and the XP-buff stack (018) adds
percentages that buy nothing once a zone's base kill already reaches the
cap. No public calculator models the new cap yet (research 0015 s4).

1. Region + parser (`server/ew/zoneinfo.py`, pure): plan 063's auto-OCR
   pipeline gets one more screenshot kind, `zone_info`, detected by the
   panel title text ("Monster Zone Info", case-insensitive, one hint list
   in `server/ew/data/ocr_regions.json` as region `zone_info`,
   `verified: false`, estimated). The parser reads `{zone_name,
   recommended_level, monsters: [{name, xp_per_kill_pct}]}`; numbers go
   through the plan 016 digit-group guard; a read below the plan 063
   confidence gate goes to the review queue, never auto-committed.
2. Zone EXP table (store, runtime): `zone_xp[<zone id>] = {recommended_level,
   monsters, read_at, level_at_read, screenshot}`; the zone id comes from a
   name match against `grind_spots.json` (exact, then plan 028's normalised
   name index); an unknown name stays a `zone_name` key with an "unmatched"
   flag. A newer read replaces an older one; a read taken at another level
   keeps `level_at_read` (EXP per kill is level-dependent).
3. Derived numbers (pure): for the operator's level (plan 066 inference),
   `cap_pct` from `xp_epochs.json` `kill_xp_cap`; `effective_pct =
   min(xp_per_kill_pct * (1 + buff_stack), cap_pct)` with the plan 018 stack;
   `cap_bound` when the unbuffed value already reaches `cap_pct`;
   `kills_to_level = ceil((100 - xp_pct) / effective_pct)`. Shown only when
   the read is from the current level band (else "re-read at Lv N").
4. Surfaces: the Grind tab spot row and the plan 012 recommender gain
   "~N kills to Lv X" and the recommended level from the panel (panel value
   wins over the community `level_min` for that spot, shown with source
   "in-game zone info <date>"); the Progress XP-buff card shows "cap-bound
   here - XP buffs add nothing" when the current session's spot is
   `cap_bound` (the plan 069 What now never suggests an XP buff then).
   GET `/api/zones/xp` -> `{zones, level, cap_pct}`.
5. Tests: `tests/test_zoneinfo.py` - synthetic panel renders (plan 009
   bench style, 2 UI scales) parse zone, recommended level and 3 monsters;
   a dropped comma / decimal point is caught by the digit guard; low
   confidence goes to the queue; cap math at Lv 55 / 62 / 66 bands;
   `cap_bound` true when base >= cap; unknown zone stays unmatched; no
   network; `node --test` for the spot-row text.

Acceptance: a synthetic Monster Zone Info screenshot dropped in the
watched folder while logged in yields a `zone_xp` row, the matching spot
row shows "~N kills to Lv X" and the in-game recommended level, and a
cap-bound fixture flips the XP-buff card line; gates green; verifier PASS
within 3 rounds; one push.

ToS check: input is only a screenshot the operator took with the game's own
key, read from the ScreenShot folder (research 0001 s4); OCR output never
becomes input to the game; no memory read, no client file, no GET.

Depends on: 063, 066.
