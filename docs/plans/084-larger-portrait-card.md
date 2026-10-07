# Plan 084 - Larger class portrait card: a left-rail character card in the spirit of the in-game End Game card

Status: open. Operator feedback 2026-10-07 (research 0011 section 8). Lane hint: `build`.

Spec gap: operator feedback 2026-10-07 - "the portrait in the dashboard
could be larger". Plan 082 shipped a 28 x 36 top-bar chip. The reference the
operator likes is the in-game End Game window (ESC > End Game): a tall card,
portrait about 278 x 360 (the 624 : 804 FaceTexture aspect), then "Lv.N",
class icon + character name, with energy and contribution counts over the
bottom of the image. EW redesigns the top-left identity element as a
character card sized for the compact dashboard. Zero-touch: the DEFAULT size
is already large; no click, toggle or entry is needed to see it.

## Layout audit (2026-10-07, read from app/ at HEAD)

- Window: 1280 x 800 default, min 960 x 600 (`app/main.js:48`); dashboard
  zoom `ui.scale` 0.9-1.3 (`main.js:70`), so CSS-px viewport ranges from
  about 1264 x 761 (default) down to 738 x 430 (min window at 1.3).
- Shell: `.ew-shell` column flex; `.ew-top` bar about 38 px (082 chip,
  tabs `flex: 1` scrolling sideways per 078, palette button, server pill);
  `.ew-main` flex, one active `.ew-panel` grid of `minmax(280px, 1fr)`
  auto-fill columns (`ew.css:79`); Deadeye panel `3fr / 2fr`.
- Columns today: 1264 px -> 4; 944 px (min window, zoom 1.0) -> 3;
  738 px (min window, zoom 1.3) -> 2.
- A header-height card (bar grown to about 84 px for a 64 x 82 image) costs
  46 px of height on every tab (6 percent of 761, 11 percent of 430), is
  still small, and does not read as the End Game card.
- A left rail of width W costs W + 10 px of grid width. 4 columns at 1264
  need W <= 84 (no gain worth the move). At W = 156 the default loses one
  column (4 -> 3, about 353 px each); at the 738 px floor a 156 px rail
  leaves 1 column (crushed), a 96 px rail leaves 2 (unchanged).
- Height: full card about 270 px, compact about 170 px; both fit the 430 px
  floor below the bar.

## Decision

Left rail character card, sized automatically by viewport width (CSS media
queries, so `ui.scale` is accounted for); no collapse toggle, no setting.

| Viewport (CSS px) | Rail | Image | Lines |
|---|---|---|---|
| >= 1100 (default 1280 window at zoom 0.9-1.1) | 168 px | 156 x 201 | Lv.N, class, character name; energy + CP over the image bottom |
| 600-1099 (960 window, or zoom 1.2-1.3) | 104 px | 96 x 124 | Lv.N, class; energy + CP in `title` only |
| < 600 (HTTP fallback, phone) | none | plan 082 chip 28 x 36 in the top bar, unchanged | - |

At >= 600 px the top-bar chip is hidden (the rail card is the identity and
the tabs get its width back, easing 0009 L2); below 600 px the chip returns.

Record: decision = left rail card, 156 x 201 at >= 1100 px, 96 x 124 at
600-1099, 082 chip below 600. Alternatives = (a) taller header bar with a
64 x 82 image; (b) 84 px rail that keeps 4 columns at 1280; (c) collapsible
rail with a toggle; (d) small chip + click-to-expand popover. Why = (a)
costs height on every tab and stays small; (b) 96 x 124 at most is not the
"larger" asked for at the default size; (c) and (d) put the large view
behind a click or a remembered toggle (zero-touch rule, research 0010);
the chosen rail costs one card column at the default size only, keeps 2
columns at the 738 px floor, and reuses the 082 `m` thumb (312 x 402, crisp
at 2x DPI at both sizes) with no new image work. Reverses if: the operator
reports tab content crushed at the default size, or asks for a toggle.

## Work

1. Server (`server/ew/portraits.py`, `server/ew/app.py`): `GET
   /api/portraits` gains `card: {level, level_source, energy, energy_at,
   cp, cp_src, name}` built from objects EW already has - no new input,
   no new GET to any external host, no new manual field:
   - `level`, `level_source` from `leveling.current_level()` / the plan 011
     view (plan 066 OCR or profile, whichever leveling already uses); None
     -> the line is omitted.
   - `energy` from the plan 042 Life & CP view (`progress.lifeskill_view()`,
     plan 041 profile snapshot); `"hidden"` or None -> omitted.
   - `cp` = the value the Imperial / Life & CP cards already show (plan 081
     derivation over the plan 079 ledger, else the plan 042 snapshot);
     `cp_src` names it; hidden / None -> omitted. One truth: the card never
     derives CP itself.
   - `name` = the profile snapshot's character name for the bound
     characterNo's class, runtime only; None -> omitted (never typed).
   The card block rides the existing `portraits` bus event plus the 30 s
   poll; no extra dashboard poll.
2. `app/shared/ewcore.js`: `portraitCard(view, activeCls, opts)` ->
   `{cls, src|null, alt, title, empty, pinned, lines: [{k, text}],
   over: [{k, text}]}`; class selection identical to `portraitChip` (active
   class tab, else loaded class, else Progress class); `src` uses
   `?size=m`; `lines` = `Lv.N` (if level), class name, character name (if
   known); `over` = energy, CP (each only if numeric, labelled "Energy N",
   "CP N"); `title` adds the source and local age (plan 078 `fmtLocal`) of
   each value. Empty state = `src` null, `empty` true, no `over` art; the
   level / class lines still render (text, not art).
3. `app/dashboard/index.html` + `dashboard.js`: `.ew-main` becomes
   `aside.ew-rail` (card) + the panels container. The card is a button
   (click selects the class tab, optional, as 082) with
   `aria-label "<Class> character card"`; image `object-fit: cover`,
   `loading="eager"`. Pinned (plan 083 override live): the 6 px accent dot
   on the frame corner + title text, as on the chip. Re-render only on a
   changed signature (as the chip does).
4. `app/shared/ew.css`: `.ew-rail`, `.ew-card-pic`, `.ew-card-over`
   (bottom scrim from tokens only, text `--fk-text` on a
   `--fk-surface-2`-based translucent band, legible in light and dark),
   `.ew-card-lines`; media queries per the table; `.ew-chip` hidden at
   >= 600 px. Empty frame: same box, 1 px dashed `--fk-border`, no image,
   no silhouette, no art of any class (082 rule unchanged). No hover zoom,
   no motion.
5. Tests:
   - `node --test` `app/test/portrait.test.js`: `portraitCard` with level,
     energy, CP -> lines and over in order; hidden / null energy and CP
     omitted (no "0", no "?"); no level -> no Lv line; empty class -> no
     src, frame only, never another class's image; pinned -> dot flag;
     `src` uses `size=m`.
   - `tests/test_portraits.py`: `card` block from fakes (level from
     leveling, energy hidden -> None, CP from the 081 value); no new store
     field written.
   - `app/test/portrait.test.js` static part (reads `index.html` and
     `ew.css` as text): rail present, `.ew-chip` hidden at >= 600 px,
     breakpoints 600 / 1100, no colour literal in the new rules.
6. Docs: plan 082 as-built note "top-bar chip superseded at >= 600 px by
   plan 084"; research 0011 section 8 already records the audit.

Acceptance: at the default 1280 x 800 window and zoom 1.0 the top-left of
every tab shows the Deadeye card with the 156 x 201 portrait, `Lv.N` from
the leveling view, the class name, and energy / CP over the image bottom
when the profile or OCR has them (omitted otherwise), with no click or
entry; at 960 x 600 zoom 1.3 the card is 96 x 124 and the Home tab keeps 2
card columns; below 600 px the 082 chip shows; the empty state is the same
frame with no art; light and dark themes legible; gates green; verifier
PASS within 3 rounds; one push.

ToS check: no new input. Same FaceTexture archive (082), leveling (011 /
066), profile snapshot (041 / 042) and 081 OCR values already in EW; no game
input, no new external request.

Depends on: 011, 042, 066, 078, 081, 082, 083 (pinned dot; if 083 is not
merged the dot is skipped and noted as a deviation).

Dependency guard: before writing code the lane checks that
`server/ew/portraits.py` (plan 082) and `server/ew/leveling.py` (plan 011)
exist and `app/shared/ewcore.js` exports `portraitChip`. If any is missing
the lane changes nothing, writes `"status": "blocked", "needs": ["011",
"082"]` into `ops/loop/control/progress/p084-build.json` and exits 0.

## Optional follow-up (not required by this plan)

The End Game window (ESC > End Game) is a good OCR source of the plan 066
kind: one screenshot shows level, energy and contribution points in a
fixed layout, and the character name next to the class icon. A later plan
may add an `endgame` region to `server/ew/ocrinfer.py` reading level,
energy and CP (gated at the plan 063 confidence), and bind class through
`portraits.bind_ocr` (082 A2) by matching the OCR'd character name against
the profile's character list (which carries the class) - not by reading
the class icon. Screenshot-only (operator takes it; plan 008 watcher);
no game input. Not planned now; file as a ROADMAP row when adopted.

refute-rounds: 0/3 (plan doc; the lane's verifier rounds apply to the build).
