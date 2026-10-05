# Plan 008 - Session-log tail + ScreenShot watcher

Status: open. Lanes: `build` (slice A), `data` (slice B).

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
