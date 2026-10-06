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

## As-built deviations

1. Search also indexes Today items and non-coupon events (kinds `today`,
   `event`), beyond the listed items / notes / coupons / steps / spots.
   Alternatives: listed kinds only. Why: same payloads already fetched, and
   tick targets are the most-searched rows. Reverses if: noise in results.
2. Enter on a line that does not parse fills the highlighted suggestion
   instead of running it; a second Enter runs. Alternatives: run the top
   suggestion. Why: a write is never a guess (ambiguity is an error in
   `parseCommand`). Reverses if: the operator asks for one-key run.
3. Jump to row is best effort: the tab is selected, then the first element
   (or Deadeye note textarea) holding the hit text is scrolled to and
   outlined (`.ew-hit`). A market item not on the watchlist has no row, so
   only the Market tab opens. Alternatives: per-module row APIs. Why: no
   module change needed. Reverses if: a module exposes a row focus API.
4. After a successful write the palette closes and the plan 026 toast
   reports it; the affected tab refreshes on its own poll. Alternatives:
   force a re-poll. Why: no module API for it. Reverses if: stale tabs are
   reported.
5. `stop grind [silver]` logs trash 0 (no trash argument). Why: the plan
   grammar has one optional amount. Reverses if: a trash argument is wanted.
6. `dashboard.js` exposes `window.EWDash.select(id)` for `go` and search
   jumps. `start grind` refuses while a session runs and `stop grind`
   refuses when none runs (when the grind payload is loaded).
7. Rows sharing a name get a typeable unique name (`name [sid]` for an
   enhancement level, `name (id)` for a duplicate title), so every
   suggestion parses to exactly one row (verifier round 1).
8. Adjudicated after refute round 3: `arm` keeps the whole argument as the
   buff name when it equals or starts a buff name; only otherwise is a
   trailing duration split off. Alternatives: require a unit letter on arm
   durations (breaks `arm hot time 90` and the plan 048 bare-number-minutes
   rule); ship with a known issue. Why: `arm tier 2` armed Tier 2 Elixir
   for 2m instead of its default. Reverses if: a number typed after the
   start of a buff name is meant as a duration and is read as part of the
   name. refute-rounds: 3/3 (adjudicated, no round 4).

Depends on: 048.

Dependency guard: before writing code the lane checks that `parseSilver` exists in `app/shared/ewcore.js` (plan 048). If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["048"]` into its progress JSON (`ops/loop/control/progress/p050-build.json`) and exits 0. Plan 019's tick turns that clean, marked run into item state `blocked` (not `no-change`) and re-dispatches the row once every Depends-on row is `[x]`; plan 019's work-list gate normally keeps the row from being dispatched that early. If this row ran before plan 019 landed and was recorded `no-change`, 019's re-arm step makes it dispatchable again.
