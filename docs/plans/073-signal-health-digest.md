# Plan 073 - Signal health digest: per-signal liveness with one-line fix hints

Status: open. Autonomy deep dive 2026-10-06 (research 0007 section 3). Lane hint: `build`.

Spec gap: automation (062-072) only removes operator work if its inputs are
alive. Today a dead session log path, a silent screenshot watcher, an OCR
engine failure or a blocked notice fetch shows up as "nothing happens", and
the operator has to debug the UI.

1. `server/ew/signals.py`: one liveness row per passive signal - session
   log (last line age vs game state), screenshot watcher (folder exists,
   last event), OCR (last run, last error, queue depth), official notices
   (last ok fetch, robots verdict), market (last ok, Imperva block count),
   profile (plan 061 state), boss drift (072). Each row: `ok | warn | off |
   bad`, age, and one fix hint from tracked
   `server/ew/data/signal_hints.json` (e.g. "game folder not found - see
   Settings > paths").
2. Expected-silence aware: a signal that is quiet because the game is
   closed or a feature is off is `off`, never `bad`.
3. System tab: the plan 020 freshness card becomes this digest; Home shows
   one pill only when any row is `bad`.
4. Tests: `tests/test_signals.py` - each row from fixture timestamps,
   expected-silence cases, hint lookup.

Acceptance: a fixture with the game logged in but no log line for 10 min
shows the session-log row `bad` with its hint, and the same fixture with
the game closed shows `off`; gates green; verifier PASS within 3 rounds;
one push.

ToS check: local state only; no game input, no memory read, no client
file.

Depends on: 020, 008.

Dependency guard: before writing code the lane checks that `server/ew/gamewatch.py` (plan 008) and the plan 020 freshness card (`app/dashboard/game.js` or `server/ew/app.py` freshness handler) exist. If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["020", "008"]` into its progress JSON (`ops/loop/control/progress/p073-build.json`) and exits 0.
