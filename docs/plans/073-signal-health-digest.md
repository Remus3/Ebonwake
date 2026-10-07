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

## As-built deviations

Self-adjudicated by the build lane (2026-10-06); each reverses on the stated
trigger. Verifier: PASS at refute round 3/3 (round 1: market empty vs
blocked order, block scope, stale last line; round 2: OCR error with the game
closed was `bad`, now `off` with the error in the detail).

1. Boss drift row ships as `off` / `not_built`. Decision: plan 072 is not
   built, so `app.py` wires `boss_drift: lambda: None`; `signals._boss_drift`
   already maps a `{status: ok|drift|error, checked_at}` provider.
   Alternatives: omit the row; block on 072. Why: the row shape is fixed now
   and 072 only swaps the lambda. Reverses if: 072 lands (wire its provider).
2. Session-log thresholds are data: warn 300 s, bad 600 s, in
   `signal_hints.json`. `bad` needs `logged_in`; `running` / `disconnected`
   (loading screens, launcher) cap at `warn`. Silence counts from the later of
   the last line and the last state change; a new log file clears the last
   line (verifier round 1). Alternatives: one threshold for
   every active state. Why: the acceptance names logged_in, and a launcher can
   sit silent without a fault. Reverses if: a real session shows the client
   log silent over 10 min while logged in (raise `bad_s`, no code change).
3. Added a `watcher_stalled` reason (not in the plan): no plan 008 poll for
   60 s is `bad` whatever the game state. Why: a dead poll thread is the one
   failure that makes every other row look like expected silence.
   Reverses if: the poll interval grows past 60 s (raise `stall_s`).
4. Missing ScreenShot folder and an unconfigured game folder are `warn`, not
   `bad`. Why: the client creates ScreenShot on the first shot, and plan 065
   may still detect the install. A configured install with no Log folder is
   `bad`. Reverses if: the operator reports a missed broken path.
5. Market "Imperva block count" = backoff entries of WATCHED items that are
   not "being fetched" (every upstream failure, Imperva 103 included).
   Alternatives: parse the error text for 103 / Imperva; count every cache
   key. Why: error strings vary by transport, and an unwatched key is never
   re-fetched, so its block would never clear. `last ok` is the newest watched
   sublist `fetched_at`. An empty watchlist is `off` even with a leftover
   block (verifier round 1). Reverses if: a non-Imperva failure needs a
   different hint.
6. The System card keeps the plan 020 freshness pills as the fallback when
   `/api/signals` is missing (server older than the app). Home's pill is a
   button above the cards that opens System. Reverses if: plan 020 pills are
   retired fleet-wide.
7. Inputs added for the digest, all local and read-only: `GameWatch.health()`
   (last complete log line clock time, folder `is_dir`, last poll),
   `AutoOcr.health()` (last good read, last failure, queue), and
   `MarketService.health()`. Notices read the persisted attempt record;
   profile reads plan 061's `source()` + off reason. No fetch, poll or game
   access is added.

Dependency guard: before writing code the lane checks that `server/ew/gamewatch.py` (plan 008) and the plan 020 freshness card (`app/dashboard/game.js` or `server/ew/app.py` freshness handler) exist. If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["020", "008"]` into its progress JSON (`ops/loop/control/progress/p073-build.json`) and exits 0.
