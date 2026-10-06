# Plan 067 - Context-aware overlay: widgets chosen by state, no toggling

Status: open. Autonomy deep dive 2026-10-06 (research 0007 sections 1, 2.5). Lane hint: `build`.

Spec gap: overlay widgets are a static set of 8 booleans
(`settings.WIDGETS`, passed by query string, the overlay recreated on each
Settings save). Nothing picks widgets by context, so the operator either
toggles mid-session or glances at the wrong thing.

1. `server/ew/context.py` (pure, clock injected): derives one context from
   existing signals - `closed` (gamewatch not_running), `in_game`
   (logged_in), `idle` (logged_in, no screenshot and no state change for
   `idle_min`, default 20 - no input hook of any kind), `maint_soon`
   (plan 059 / 064 maintenance within 60 min), `reset_soon` (plan 021
   reset within 30 min), `boss_soon` (plan 031 spawn within 15 min),
   `hot_time` (plan 011 window active). Several can hold; a fixed priority
   orders them.
2. Rules data `server/ew/data/overlay_contexts.json` (tracked): per context
   the ordered widget list, e.g. in_game -> grindSession, grindBuff,
   leveling; boss_soon -> worldBoss first; maint_soon -> a maintenance
   countdown line; closed -> overlay hidden. Max 4 widgets shown.
3. Live switch: the server pushes SSE `overlay_context` {context, widgets};
   the overlay re-renders in place (no window recreate). Settings keeps the
   8 booleans as PINS (always shown) and BLOCKS (never shown) on top of the
   rules; `overlay.auto: true` default.
4. The hotkey still toggles visibility (Electron `globalShortcut` only).
5. Tests: `tests/test_context.py` (each context from fixture signals,
   priority, pins / blocks) and node tests for the in-place re-render.

Acceptance: a fixture timeline closed -> in_game -> boss_soon -> in_game ->
closed switches the widget list with no settings write and no overlay
recreate; gates green; verifier PASS within 3 rounds; one push.

ToS check: overlay stays a separate click-through Electron window toggled
by `globalShortcut`; context comes from the session log, clock and public
notices; no input hook, no game input, no memory read.

Depends on: 008, 022, 031, 059.

Dependency guard: before writing code the lane checks that `server/ew/gamewatch.py` (plan 008), `app/overlay/` (plan 022), `server/ew/bosses.py` (plan 031) and `server/ew/maint.py` (plan 059) exist. If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["008", "022", "031", "059"]` into its progress JSON (`ops/loop/control/progress/p067-build.json`) and exits 0.

## As-built deviations

Adjudicated by the build lane (2026-10-06); each reverses on an operator or MAIN order in writing, or as noted.

1. Pins / blocks are one tri-state setting per widget, `overlay.mode.<w>` = `auto | pin | block` (default `auto`), not a reinterpretation of the 8 `overlay.widgets.*` booleans. Alternatives: reuse the booleans as pins and add 8 block booleans (16 checkboxes, pin + block conflicts); reuse booleans as pins only (the default-on trio would eat 3 of 4 slots). Why: a two-state boolean cannot carry three states, and the existing booleans keep their meaning as the manual set when `overlay.auto` is false. Reverses if: the operator wants a compact checkbox grid instead of selects.
2. Only `not_running` is `closed`; `running`, `disconnected` and `unconfigured` are the `in_game` base. Alternatives: treat `unconfigured` as closed (overlay hidden forever without a watcher config) or as no context. Why: an unconfigured install must still get clock-driven contexts (boss / reset / maintenance). Reverses if: gamewatch becomes mandatory.
3. `closed` hides the overlay PANEL (`#ov-panel[hidden]`) inside the existing window; the window is never hidden, destroyed or recreated by a context. Alternatives: an IPC hide from the renderer (new preload channel, fights the hotkey toggle). Why: no new IPC surface, the hotkey (`globalShortcut`) keeps sole control of window visibility. Pins are hidden too while closed. Reverses if: a transparent empty window is measured costing anything.
4. Base rows (game header, daily / weekly reset, Today) are not widgets and always show in a visible context; the context picks among the 8 widgets plus the new `maintenance` countdown row.
5. Several active contexts merge: pins first, then each context's list in priority order, deduped, blocks removed, capped at `max_widgets` (4); pins are never cut. Priority: closed, maint_soon, boss_soon, reset_soon, hot_time, idle, in_game.
6. `reset_soon` uses the NA daily reset (00:00 UTC; the weekly reset coincides with a daily one). `maint_soon` also holds while a maintenance window is under way. `idle_min` is a setting, `overlay.idle_min` (5-240, default 20).
7. Saving `overlay.auto`, `overlay.idle_min` or `overlay.mode.*` no longer recreates the overlay (`settingsEffects`); the server invalidates its 5 s context cache on every settings POST and the change arrives over SSE. Other `overlay.*` keys still recreate (unchanged plan 030 behaviour).
8. New route `GET /api/overlay/context` (same payload as the SSE event) so the overlay has the context at start and after a reconnect, without the SSE stream sending an initial frame (existing SSE consumers read the heartbeat first).
9. Merge resolve (2026-10-06, plan 063 landed on main first): `server/ew/app.py` imports both `context` / `maint` (067) and `ocrauto` (063); `server/ew/settings.py` SPEC carries both the 063 `ocr.auto*` / `ocr.daily_cap` keys and the 067 `overlay.auto` / `overlay.idle_min` keys. Alternatives: none viable - each side's keys and imports are disjoint and each feature needs its own. Why: union keeps both features; ruff, pytest and node tests green on the merged tree. Reverses if: never (mechanical).
