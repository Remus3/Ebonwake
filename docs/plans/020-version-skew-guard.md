# Plan 020 - Server health + version-skew guard, System freshness card, honest 404 copy, / redirect

Status: open. Deep dive + UX audit 2026-10-05 session 4 (research 0003-0005). Lane hint: `build`. Priority row.

Spec gap: research 0005 (UX audit) H1, H2, M2, M12 and candidate F2 (value 5,
effort S, "the live dashboard is lying about health today"). The dashboard
pill turns `server ok` once and never re-checks; it never compares the
server's `/api/version` commit with the app's, so a server 10 h behind HEAD
showed green while `/api/spots` answered 404 and the Grind card blamed a
missing API. The System tab renders a raw JSON blob although
`/api/state.sources` carries `updated` / `ttl_s` / `status`. `GET /` serves
`index.html` with broken relative asset paths.

1. App commit: `app/main.js` reads the app's commit once at start
   (`git rev-parse --short HEAD` in the repo root, `CREATE_NO_WINDOW`-style
   hidden spawn; `unknown` on failure) and exposes it to the dashboard via
   the existing preload bridge as `ewApi.appCommit()` (read-only value, no
   new write channel).
2. Pure logic in `app/shared/ewcore.js`: `healthPill({version, appCommit,
   lastOkMs, nowMs, sseOk})` -> `{level: ok|warn|bad, text}`: `bad` when no
   OK answer for 90 s or the SSE stream errored, `warn` + `server outdated -
   restart` when both commits are known and differ, else `ok`. Tested with
   `node --test` in `app/test/ewcore.test.js`.
3. `app/dashboard/dashboard.js` polls `/api/version` every 30 s (and on SSE
   reconnect) and repaints the pill; the warn pill carries a `Restart server`
   button that calls the existing tray restart path (`app/main.js` restart
   handler) through a new allowlisted IPC `ew:restart-server`.
4. One 404 helper `notOnServer(module)` in `ewcore.js` used by every module
   (grind, spots, leveling, ...) instead of ad-hoc copy: "server is older
   than the app - restart it (tray > Restart server)".
5. System tab: replace the JSON blob with a "Data freshness" card (one pill
   per `state.sources` entry: name, age `4m ago`, status, stale when age >
   ttl_s) and a "Server" card (commit, started, pid, uptime, outdated flag)
   from `/api/version` + `/api/health`.
6. `server/ew/app.py`: `GET /` answers 302 to `/app/dashboard/index.html`
   (test in `tests/test_server.py`), no path rewrite.

Acceptance: node tests cover ok / warn (commit mismatch) / bad (no answer,
SSE error) / unknown commit -> ok; server test for the 302; every module's
404 path uses the helper (grep test in `app/test/shell.test.js` style);
self-test 7/7 at merge; gates green; verifier PASS within 3 rounds.

ToS check: local server and Electron only; restart touches EW's own server
process, never the game (CLAUDE.md standing order 11).

Depends on: none.
