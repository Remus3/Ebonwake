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

## As-built deviations

Self-adjudicated in the build lane (no operator wait, CLAUDE.md order 6).

1. Overlay preload invariant. Decision: item 3's one-way IPC needs a
   renderer channel, so the overlay gets its own sandboxed
   `app/overlay/preload.js` exposing only `ewOverlay.reportSize(number)`
   (`ipcRenderer.send`, no invoke, no reply); three older tests that asserted
   "exactly one preload / overlay has none" (grind, market, today) now assert
   two preloads and that the overlay one carries no `ew:post` / invoke /
   `ewApi`. Main checks `event.sender === overlay.webContents`; the overlay
   also denies navigation and window.open like the dashboard.
   Alternatives: Electron `enablePreferredSizeMode` + `preferred-size-changed`
   (no preload at all) or main polling `executeJavaScript`. Why: the plan
   names the IPC; preferred-size height semantics at a fixed width are not
   documented tightly enough to bet the clip-free guarantee on; polling
   wastes work. Reverses if: preferred-size mode is measured to report the
   laid-out height at the window width - then drop the preload and restore
   the one-preload tests.
2. Measurement trigger. Decision: a `ResizeObserver` on the panel calls the
   report, and only a changed body height is sent. Alternatives: measure
   after every draw (once a second). Why: same result, no per-second IPC.
   Reverses if: a render path is found that changes height without resizing
   the panel.
3. Height measure and rounding. Decision: the page reports the fractional
   `document.body.getBoundingClientRect().height` (not `scrollHeight`, an
   integer that can round down at 13 px x scale) and `overlayHeight` rounds
   UP (ceil) before the 60-600 clamp. Why: a fractional last line must never
   clip (verifier round 1). Reverses if: never (round-down can only clip).
4. Width follows scale. Decision: window width = round(340 x scale), so a
   scaled font does not wrap or clip. The plan was silent. Reverses if: the
   operator wants a fixed width with scale (config key then).
5. Quiet rows. Decision: `ovQuiet` treats `''`, `-`, `?`, `none`, `idle` as
   nothing-to-say (initial unknown states included); `offline` always shows.
   Leveling / Season rows now read `Leveling | <line>` and `Season | <line>`,
   their placeholders shortened to `-` / `offline`. The Server row and a red
   header dot (before "Game") show only when the server is not ok.
   Reverses if: the operator wants placeholders visible while loading.
6. Value column width. Decision: values are `flex: 0 0 auto` with
   `min-width: 11ch`, right-aligned, tabular figures - a fixed 11ch column
   for countdowns and short values that grows (never shrinks or wraps) for
   longer ones (`daily 3/5  weekly 1/4` is ~21ch); free-text values
   (Grind spot, Leveling, Season) may shrink with an ellipsis. Alternatives:
   a hard `width`, which would clip the Today line. Why: no jitter, no clip.
   Reverses if: the Today line is shortened below 11ch.
7. Self-test minimap check. Decision: `selftest.run` takes
   `overlayPlace: {workArea, defaultAnchor}` from main; `insideWorkArea` is
   always required, `clearOfMinimap` only when the configured anchor equals
   the default (`ml`, explicit or implied; `null` otherwise). The live
   Electron self-test (7/7 + minimap) is run at merge per the acceptance
   line, not in this lane (a second app instance would quit on the
   single-instance lock while the operator's EW runs).
