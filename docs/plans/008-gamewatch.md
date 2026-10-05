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
5. Screenshots list only `.jpg/.jpeg/.png/.bmp` regular files (stat only, never
   opened). Alternatives: every file. Why: the folder may hold non-image
   leftovers. Reverses if: BDO writes another image extension.
6. Poll thread is OFF unless `make_server(game_poll=True)`; `main()` turns it on
   and `server_close()` stops it. Tests inject `game_watch=` or `game_cfg=` so
   no test reads the real config, a real log or runs tasklist. `last_event`
   = {date, type, log (control chars stripped, <= 200 chars), state} of the
   last CLASSIFIED line; `/api/state` `sources.game` = {updated, status}.
   tasklist is resolved from `%SystemRoot%\System32` first (no cwd hijack).
   Reads are capped at 4 MiB per poll; a larger backlog drains over polls.
