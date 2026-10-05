# Plan 008 - Session-log tail + ScreenShot watcher

Status: done (2026-10-05); refute rounds 2/3 PASS. Lanes: `build` (slice A), `data` (slice B).

## Goal

A read-only "game state" signal (not running / running / logged in /
disconnected) from the BDO client's own session log, and a list of new
operator-taken screenshots, both shown on the System tab and in the overlay
header. Nothing is ever sent to the game; nothing reads game memory; the
client's files are opened read-only with sharing so the game is never blocked.
Source facts: `docs/research/0001-bdo-data-and-tos.md` section 4.

## Slice A - server (lane `build`)

Files: `server/ew/gamewatch.py`, `server/ew/app.py`, `tests/test_gamewatch.py`
(fixtures are synthetic UTF-16LE files written by the tests; never a real log).

1. Paths from `config/local.json` `bdo.install_dir` / `bdo.documents_dir`
   (absent -> watcher reports `unconfigured`, never guesses a path). Log dir =
   `<install_dir>/Log`, pattern `Client_YYYY-MM-DD_HHMMSS.json`; screenshot dir
   = `<documents_dir>/ScreenShot`.
2. Session tail: pick the newest log by name stamp, read from the last
   offset (stored in memory; on file change start at 0), decode UTF-16LE
   (BOM tolerant, partial trailing line kept for next poll), one JSON object
   per line with keys `Date`, `LogType`, `Log`. Classify with a small ordered
   table of case-insensitive substrings in `Log` (login / server-select ->
   `logged_in`, logout / exit -> `running`, disconnect / reconnect failure ->
   `disconnected`); unknown lines change nothing. The table is DATA in the
   module with a test per row; the tests pin the parser, and the real
   substrings are confirmed on this host by the operator check below.
3. `running` = a log file modified within the last 120 s OR a process named
   `BlackDesert64.exe` listed by `tasklist /FI` (CREATE_NO_WINDOW; process
   LIST only - never opened, never a handle). State `not_running` otherwise.
4. Screenshot watcher: poll the dir every 5 s, list files newer than server
   start (name, size, mtime), newest 50, never opened in this plan (OCR is
   009).
5. One polling thread (5 s), injected clock/fs/tasklist for tests.
   `GET /api/game` -> `{state, since, log_file (name only), last_event,
   screenshots: [...], configured}`; `/api/state` `sources.game`; SSE pushes a
   `game` event on state change.

## Slice B - dashboard + overlay (lane `data`)

Files: `app/dashboard/dashboard.js` (System tab card), `app/overlay/overlay.js`
(header dot: grey not running, amber running, green logged in, red
disconnected), `app/shared/ewcore.js` (`gameStateLabel`), `app/shared/ew.css`,
`app/test/game.test.js`. System card: state, since, last event, recent
screenshots (name + time), "unconfigured" hint naming the config keys.

## Operator check (carried to hand-off when the lane lands)

Launch BDO normally, log in, log out: the System card must walk
not running -> running -> logged in -> running. If a step is missed, the
classifier table needs the observed `Log` substring (data change only).

## Acceptance

pytest, node --test, leak sweep green; contract covered by synthetic-file
tests; self-test 7/7; verifier PASS within 3 refute rounds; one push; CI green.

### Slice A deviations (adjudicated in-lane, 2026-10-05)

1. SSE `game` event is a NAMED event (`event: game` + `data: <GET /api/game
   body>`), sent instead of the next heartbeat when the state changes.
   Alternatives: a `data:` message with `"type": "game"`. Why: the overlay's
   `onmessage` treats every default message as a heartbeat and reloads Today;
   a named event leaves it untouched and slice B subscribes with
   `addEventListener('game', ...)`. Reverses if: slice B needs one stream type.
2. `configured` = `bdo.install_dir` is a non-empty string. Without it the state
   is `unconfigured` and tasklist is never run; a `documents_dir` alone still
   lists screenshots. Alternatives: require both keys; probe the process even
   unconfigured. Why: the session signal needs the log dir; no path is ever
   guessed. Reverses if: the operator wants a process-only signal unconfigured.
3. Session state resets to plain `running` when a new log file appears, when
   the file shrinks below the stored offset, and when the game goes
   `not_running` (so a relaunch never shows a stale `logged_in`). Alternatives:
   carry the last classified state across sessions. Why: each launch writes a
   new log; a carried login would be wrong. Reverses if: the client is seen to
   reuse one log across launches.
4. Classifier substrings beyond the spec's words: `connection lost`, `log out`,
   `log in`, `selectserver`; disconnect rows are first so they win. Data only,
   to be confirmed by the operator check.
   Revised after refute round 1: patterns are word-bounded regexes (plain
   substrings read "catalog index" / "CatalogInfo" as a login); each row has a
   positive sample and the unknown-line list carries the near misses.
5. Screenshots list only `.jpg/.jpeg/.png/.bmp` regular files (stat only, never
   opened). Alternatives: every file. Why: the folder may hold non-image
   leftovers. Reverses if: BDO writes another image extension.
6. Poll thread is OFF unless `make_server(game_poll=True)`; `main()` turns it on
   and `server_close()` stops it. Only `main()` passes the real `bdo` config;
   a bare `make_server` uses `{}` (refute round 1). Tests inject `game_watch=` or `game_cfg=` so
   no test reads the real config, a real log or runs tasklist. `last_event`
   = {date, type, log (control chars stripped, <= 200 chars), state} of the
   last CLASSIFIED line; `/api/state` `sources.game` = {updated, status}.
   tasklist is resolved from `%SystemRoot%\System32` first (no cwd hijack).
   Reads are capped at 4 MiB per poll; a larger backlog drains over polls.

### Slice B deviations (adjudicated in-lane, 2026-10-05)

1. Game card lives in a new `app/dashboard/game.js` (`window.EWGame`), mounted
   from `dashboard.js` on the System tab, not inline in `dashboard.js`.
   Alternatives: inline card in `dashboard.js`. Why: mirrors every other tab
   module (events.js etc.) and keeps the shell small. Reverses if: the System
   tab gets its own module that should own the card.
2. Time fields (`since`, screenshot `mtime`) accepted as epoch seconds (number
   below 1e12), epoch millis, or ISO string (`C.gameTimeMs`); `last_event`
   accepted as a string or a `{Date, LogType, Log}` object (the `Log` text is
   shown, control chars collapsed, capped at 200). Alternatives: pin one shape.
   Why: slice A was built in parallel and the contract does not fix the type.
   Reverses if: slice A pins a shape - then drop the other branches.
3. `configured: false` forces the `unconfigured` view whatever `state` says;
   an unknown `state` string shows grey "unknown". The unconfigured hint names
   `bdo.install_dir`, `bdo.documents_dir` and `config/local.json`.
4. Overlay "header": the overlay had no header row, so the game dot is a new
   first row (`#ov-game-row`), always shown (no widget toggle). Refreshed on
   the existing 60 s / heartbeat cadence and re-fetched (GET) on each SSE
   `game` event; the event payload is not parsed. Alternatives: a fourth
   `overlay.widgets` toggle; parsing the SSE payload. Why: the plan calls it a
   header, not a widget; a GET keeps one parse path. Reverses if: the operator
   wants it hideable - add `gameState` to `WIDGETS`.
5. Dot colours: not running = muted text tone (`off`), running = warn,
   logged in = ok, disconnected = bad; unconfigured / unknown / offline =
   status-unknown. Dashboard shows the newest 10 screenshots (+N more) of the
   up-to-50 the server returns; card polls every 10 s, "since" ticks each second.

## Hotfix 2026-10-05: exit lag (operator QA)

Operator closed BDO to the desktop; the System tab kept "running" for minutes
with last event "terminating app: ExitInstance". Cause: `poll()` treated a log
written in the last `RECENT_LOG_S` (120 s) as proof of life before asking the
process list, and "ExitInstance" classified as `running` via the `exit` row.
Fix: a definite tasklist answer wins (not listed => not_running at once); the
log-recency fallback runs only when tasklist fails (`process_listed` now
returns None on failure / nonzero exit). "terminating app" and "ExitInstance"
are a terminal classifier state `exited` (shown as not_running even while the
process lingers). Server poll 5 s -> 2 s (tasklist measured 0.06 s), dashboard
game card poll 10 s -> 2 s. Measured with a real-thread harness (fake process
probe, real log file): exit -> not_running lag BEFORE 124.9 s (log grace + 5 s poll),
AFTER 1.6 s server-side (worst case dashboard ~4 s; target 5 s).
Alternatives: shorten RECENT_LOG_S only (still log-driven, rejected); a WMI
process-exit event (new dependency surface, rejected). Reverses if: tasklist
proves flaky on the operator's box (fallback already covers outright failure).
Operator QA recorded: Ctrl+Alt+E overlay toggle PASS; profile.family SET.
