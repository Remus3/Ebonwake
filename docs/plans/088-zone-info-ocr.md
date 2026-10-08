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

## As-built (2026-10-08, lane build)

Code: `server/ew/zoneinfo.py` (title detection, parser, digit guard
`pct_guard`, zone-name match, cap / kill math, `ZoneXpService` over store
domain `zone_xp`), `server/ew/data/ocr_regions.json` (region `zone_info` +
`titles.zone_info` hint list, unverified), hooks in `ocrauto.py` (kind
`zone_xp`, `zones=` service; commit / undo / review accept), `leveling.py`
(`xp_state`, `kill_caps`, `level_at`, view field `zone_cap`), `spots.py`
(`zones` hook: panel level replaces `level_min`, row `zone_xp`),
`whatnow.py` (`from_grind` skips XP buffs on `zone_cap_bound`), `app.py`
(wiring + GET `/api/zones/xp`), client `ewcore.js` (`zoneKillsText`,
`zoneLevelText`, `zoneCapText`, `normalizeLeveling.zone_cap`),
`dashboard/grind.js` (recommender rows), `dashboard/leveling.js` (cap-bound
line). Tests: `tests/test_zoneinfo.py`, `app/test/zoneinfo.test.js`.

## As-built deviations

1. `cap_pct` is parsed from each `kill_xp_cap` band's note ("~2% of the
   level"), or a numeric `cap_pct` when a band carries one; the narrowest band
   holding the level wins and a band with `superseded_by` is skipped.
   Decision: no edit to `xp_epochs.json`. Alternatives: add a numeric
   `cap_pct` to every band now; hard-code the official table. Why: plan 087
   (data lane, in flight) owns the band rows (14 official bands, old 62-75
   row superseded); two lanes editing the same rows collide. With today's
   tracked 4 bands Lv 62-64 reads 0.01 % (the known-wrong band) until 087
   lands; the narrowest-band rule then picks 0.02 % with no code change.
   Reverses if: 087 adds a numeric field under another name.
2. "Current level band". Decision: a read counts for the operator's level
   when it was taken in the same `kill_xp_cap` band (else at the same level
   when no band covers both); otherwise the row shows "re-read at Lv N" and
   no kill count. Alternatives: same level only; any level. Why: the plan
   says "current level band"; same-level-only would blank every row after
   each level-up, any-level would quote a stale % across a cap change.
   Reverses if: real reads show per-kill % drifting inside one band enough
   to mislead the kill count (then same level only).
3. Zone figures. Decision: `effective_pct` / `kills_to_level` use the mean of
   the listed monsters' effective EXP; the zone is `cap_bound` only when every
   listed monster's unbuffed kill reaches the cap. Alternatives: best
   monster; first monster; any monster cap-bound. Why: a zone is ground as a
   mixed pack; "XP buffs add nothing" must hold for every kill there.
   Reverses if: the operator wants a per-monster pick.
4. Percent reads only, digit guard. Decision: only "<name> <n>%" rows are
   read (`xp_per_kill_pct`); one `.` = 0.95, one `,` = 0.85, a whole percent
   = 0.9; a leading zero without a separator (dropped point or comma), over
   100 %, or several separators trip the guard (0.3 -> review); accepting a
   guarded value outside 0 < pct <= 100 is refused (discard it). A bare value
   cell ("Combat EXP 0.0450%") is never a monster; its name cell claims it.
   Alternatives: also read absolute EXP numbers and convert by a level EXP
   table; drop guarded rows silently. Why: the plan's field is a percent and
   EW has no post-revamp EXP-per-level table (research 0015 s6 F); review
   keeps a doubtful read visible at no risk. Reverses if: a real panel shot
   shows absolute EXP numbers.
5. Review actions. Decision: a zone read is accept / discard only (no `fix`),
   like `book_use`. Alternatives: a fix form for the nested table. Why: the
   value is a zone plus up to 20 monster rows; a JSON fix box is error-prone
   and a re-shot is one key press. Reverses if: fixing one monster row is
   asked for.
6. Zone-name match. Decision: exact, then the plan 009 normalised name
   (`ocr._norm`), then the closest normalised name within edit distance 0.2.
   Alternatives: plan 028's index (as the plan says); exact only. Why: the
   plan 028 index is the market item index and holds no zone names; exact
   only would leave an OCR slip unmatched. Reverses if: a zone alias table is
   added.
7. Ordering, level at read, undo. Decision: a read whose shot is older than
   the stored row is not committed (it waits in review, "a newer read ... is
   stored"); `level_at_read` is the level of the samples at or before the
   shot (+1 s, so a level read from the same shot counts), None when no
   sample is that old (the row then says "re-read"); undo restores the
   previous row only while the row is still the one that commit wrote; past
   MAX_ZONES (200) the oldest other reads are pruned, never the one just
   written. Alternatives: newest commit wins regardless of shot time; stamp
   the current level. Why: an old shot read late must not overwrite a newer
   one, and a later level would mark an old read current. Reverses if: the
   review queue fills with late old shots.
8. Surfaces. Decision: the "Grind tab spot row" is the plan 012 recommender
   row in the Grind tab; the cap-bound line sits on the Leveling card (where
   the XP stack pill lives); the kill count uses the operator's real level
   even under a what-if level query; What now treats a buff as an XP buff
   when it has xp_pct > 0, its name says XP / EXP / experience / combat /
   Hot Time (the seeded "XP scroll" and "Hot Time" have no xp_pct), or it is
   named like a plan 018 XP preset, and also drops its "Hot Time - grind
   now" nudge while cap-bound. Alternatives: also the operator's
   own spot list; a new card. Why: the operator's spot list carries no level
   or zone data; the what-if level has no XP % to count from. Reverses if:
   the operator's spot list gains a zone link.
9. Region box. Decision: region `zone_info` is recorded (unverified) but not
   used; the panel title alone gates the read. Alternatives: tie-break or
   crop by the box. Why: the box is an estimate and the title is a sharper
   signal; a wrong crop would lose good reads (plan 066 deviation 1).
   Reverses if: the box is measured and set `verified: true`.
10. Level reads on zone shots (verifier round 3 minor, adjudicated: fix).
   Decision: a shot holding the zone panel title gives no plan 066 level /
   XP % read. Alternatives: keep the read and rely on the review gate; drop
   only a level paired with a monster row. Why: "Lv. 58 <zone>" over a
   "0.120%" monster row pairs as a fake level sample (0.85, committed if the
   operator lowers the gate); the HUD level is read from any other shot.
   Reverses if: zone shots turn out to be the main source of level reads.
11. Re-run port (2026-10-08, lane-1). Decision: this re-run from drive E:
   ports the salvage ref `refs/ew/salvage/088` (the first run's complete
   output, verifier rounds already spent there) instead of rebuilding.
   Alternatives: rebuild from scratch; cherry-pick in the loop. Why: the
   salvage base differs from HEAD only in the hand-off, ROADMAP and a plan
   rename, so the port is byte-equal; a rebuild would spend a lane for the
   same result. Reverses if: the gates fail on the ported files.

Status note: the Status line stays `open` until the loop merges (plan 066
precedent); ROADMAP is flipped by the loop.
