# Plan 050 - Command palette (Ctrl+K) with typed commands and cross-tab search

Status: open. Deep dive + UX audit 2026-10-05 session 4 (research 0003-0005). Lane hint: `build`.

Spec gap: research 0005 candidates F7 (value 4, M) and F8 (value 2, S
after F7). Section 3 counts 30-45 clicks an evening, mostly to tick, arm
or log; a keyboard path cuts that to a few typed commands in EW's own
window.

1. Pure parser `parseCommand(text, catalog)` in `app/shared/ewcore.js`:
   `tick <daily>`, `arm <buff> [30m]`, `start grind <spot>`, `stop grind
   [1.2b]`, `log xp <level> <pct>`, `watch <item name>`, `go <tab>`; fuzzy
   match against names from current payloads; returns `{route, body}` for
   an existing POST or `{tab}`; never a new write route.
2. Palette UI `app/dashboard/palette.js`: Ctrl+K in the dashboard window
   only (not a `globalShortcut`), suggestions as you type, Enter runs,
   result via toast (plan 026 if present, else inline).
3. Search mode (`/` prefix): items (plan 028 index if present), notes,
   coupons, enhancement steps, spots; selecting jumps to the tab and row.
4. Tests: `app/test/palette.test.js` (each command, ambiguity, bad input,
   uses plan 048 parsers for silver and durations).

Acceptance: tests green; every command maps to an existing POST route
(test asserts the route list); gates green; verifier PASS within 3 rounds.

ToS check: keystrokes go to EW's dashboard window only, never to the game;
no global hotkey added.

Depends on: 048.

Dependency guard: before writing code the lane checks that `parseSilver` exists in `app/shared/ewcore.js` (plan 048). If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["048"]` into its progress JSON (`ops/loop/control/progress/p050-build.json`) and exits 0. Plan 019's tick turns that clean, marked run into item state `blocked` (not `no-change`) and re-dispatches the row once every Depends-on row is `[x]`; plan 019's work-list gate normally keeps the row from being dispatched that early. If this row ran before plan 019 landed and was recorded `no-change`, 019's re-arm step makes it dispatchable again.
