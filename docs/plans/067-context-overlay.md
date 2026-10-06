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
