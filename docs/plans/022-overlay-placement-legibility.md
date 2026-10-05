# Plan 022 - Overlay placement off the minimap, content-sized height, legibility

Status: open. Deep dive + UX audit 2026-10-05 session 4 (research 0003-0005). Lane hint: `build`. Priority row.

Spec gap: research 0005 H4, H6, L1, L2, L3 and parts of candidates F6 / F11.
The overlay is fixed top-right (`app/main.js:46-50`, 340x220, primary
display) - on top of BDO's minimap and buff tray; 12 px muted text over a 72
percent panel loses contrast over bright scenes; the fixed 220 px height
clips silently when one more widget is added (plan 029 adds the market
ticker); empty rows (`Buff none`, `Server ok`) waste space.

1. Config (gitignored `config/local.json`, documented in
   `config/local.example.json`): `overlay.anchor` = `tl|tr|bl|br|ml|mr` or
   `{"x": int, "y": int}` (work-area relative), `overlay.display` = index
   (default primary), `overlay.scale` 0.8-1.6 (default 1.0),
   `overlay.opacity` 0.5-0.95 (default 0.85). Default anchor `ml`
   (middle-left), not `tr`.
2. Pure placement in `app/shared/ewcore.js`: `overlayBounds(workArea,
   anchor, size, margin)` -> `{x, y, width, height}` clamped inside the
   work area; `overlayConfig(cfg)` validates and defaults the four keys.
   Node tests for every anchor, clamping and bad input.
3. Content-sized height: the overlay page measures `document.body
   .scrollHeight` after each render and reports it through a one-way IPC
   `ew:overlay-size` (number only; main clamps 60-600 px and calls
   `setContentSize`). The overlay window stays `focusable: false`,
   `setIgnoreMouseEvents(true)` and `globalShortcut`-toggled - unchanged.
4. Legibility (`app/shared/ew.css` overlay section): base 13 px times
   `--ew-overlay-scale`, `text-shadow: 0 0 2px #000, 0 1px 2px #000`, panel
   alpha from `overlay.opacity`, labels at `--fk-text` (weight, not colour,
   for hierarchy), `font-variant-numeric: tabular-nums` on values and
   countdowns, fixed-width value column.
5. Quiet rows (`app/overlay/overlay.js`): rows with nothing to say are
   hidden; the server row shows only when the server is not ok (red dot in
   the header); Leveling and Season rows get the same label/value layout as
   the rest.
6. Self-test (`app/selftest.js`): assert the overlay bounds lie inside the
   chosen display's work area and do not intersect the top-right 360x300
   minimap zone when the anchor is the default.

Acceptance: node tests for placement/config/row-hiding pure functions;
self-test 7/7 plus the new minimap assertion at merge; overlay still
click-through (`WS_EX_TRANSPARENT` read back as in plan 001); gates green;
verifier PASS within 3 rounds.

ToS check: separate transparent click-through Electron window toggled by
`globalShortcut` only (CLAUDE.md overlay rule); no input to the game.

Depends on: none.
