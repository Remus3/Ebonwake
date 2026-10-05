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

## As-built deviations

1. App commit reaches the preload as a launch argument, not an IPC call.
   Decision: `main.js` reads `git rev-parse --short HEAD` once
   (`execFileSync`, `windowsHide`, 5 s timeout, sha-shaped or `unknown`) and
   passes `--ew-app-commit=<sha>` via `webPreferences.additionalArguments`;
   `preload.js` exposes `ewApi.appCommit()` returning that constant.
   Alternatives: `ipcRenderer.sendSync` channel; async `invoke`. Why: the plan
   asks for a read-only value and no new channel; an argument is synchronous,
   immutable and adds zero IPC surface. Reverses if: the commit must change
   without a window reload (e.g. hot app update).
2. Commits compare by prefix. The app has the short sha, the server the full
   sha; `commitsDiffer` treats one being a prefix of the other as equal, and an
   unknown / non-hex value on either side as "not different" (pill stays ok).
   Alternatives: make the server report a short sha; read the full sha in the
   app. Why: no server contract change (`/api/version` is the fleet P0-5
   shape). Reverses if: the version contract gains a short-commit field.
3. 404 copy names the module: `notOnServer(m)` returns
   `<m> API missing: server is older than the app - restart it (tray > Restart
   server)`, and accepts a route path (`/api/spots?...` -> `spots`) so grind's
   shared getJSON names `spots` correctly. Today and Market gained the 404
   branch they lacked; the OCR POST 404 uses it too. Alternatives: the bare
   sentence from the plan. Why: with several cards on a tab the operator must
   see which call failed. Reverses if: UX review wants one banner instead of
   per-card copy.
4. The dashboard shell opens its own `EventSource('/events')` (it had none):
   `onerror` sets `sseOk=false` (pill bad, "server stream lost"), `onopen`
   after an error re-polls `/api/version` (the "on SSE reconnect" trigger),
   and every heartbeat refreshes `lastOkMs`. Alternatives: piggy-back on
   `leveling.js`'s stream. Why: the shell must not depend on a tab module.
   Reverses if: a shared SSE hub module lands.
5. The 30 s poll also refreshes `/api/health` and `/api/state.sources` for the
   System cards (no tab re-render); pill and cards repaint every 5 s so ages
   and the 90 s cut-off move without a fetch. While a restart is in flight the
   pill reads `server restarting` and the button is disabled.
6. `GET /` now 302s, so `test_static_dashboard_and_tokens` fetches
   `/app/dashboard/index.html` directly; `test_root_redirects_to_dashboard`
   covers the redirect. Added a compile test (vm.Script over main, preload and
   every dashboard script) to `app/test/shell.test.js`.
7. Self-test 7/7 needs the Electron desktop; not runnable in a headless build
   lane, left to the merge step as the plan states ("at merge").
