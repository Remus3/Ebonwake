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
