# Plan 001 - Skeleton

Status: done (2026-10-04). Lane: `build`.

## Goal

A runnable, tested shell for every moving part, so plans 002+ only add data.

## Slices

1. Server (`server/ew/`): stdlib `ThreadingHTTPServer` on `ports.SERVER` (8940),
   loopback bind + Host check. Routes: `GET /api/health`, `GET /api/version`
   (fleet P0-5 contract: commit read once at bind, started, pid, config_hash,
   schema 1; no path/user/host in the body), `GET /api/state` (tab list + data
   freshness), `GET /events` (SSE heartbeat every 15 s), `GET /` and `/app/*`
   static assets for a browser fallback. Store helper with atomic writes.
2. ETA log (`tools/eta.py`): `record <kind> <seconds>` appends to
   `ops/loop/control/timings.jsonl`; `estimate <kind>` prints the median of the
   last 5 runs (or a default) as `[~Ns]` / `[~Nm]` / `[~Nh]`.
3. Lane driver (`tools/ew_lane.py`): up to 3 named lanes (`build`, `data`,
   `review`), each in its own worktree `../ew-worktrees/<lane>` on branch
   `lane/<lane>`, each run holding one slot from `ops/loop/slots.py`
   (`max_slots=3`), spawning through `fleet_headless.spawn` only, writing a
   progress file and recording its duration in the ETA log. Dry-run mode for
   tests.
4. Electron shell (`app/`): `main.js` with the dashboard window (tabs from
   `/api/state`) and the overlay window (transparent, click-through, focus-less,
   `globalShortcut` toggle). Pure logic in `app/shared/*.js` tested with
   `node --test`. Electron itself is a devDependency, not needed for tests.
5. Gates: pytest (kit conformance, ascii/lf, ports, server routes, eta, lane,
   leak sweep), `node --test`, leak sweep pre-commit + pre-push hooks, CI on
   ubuntu (python 3.11 + 3.14, node 20).

## Acceptance

- `python -m pytest -q` green, `npm test --prefix app` green, CI green.
- `python -m server.ew` serves `/api/health` -> `{"ok": true}` on 8940.
- `npx electron app` (after `npm install --prefix app`) shows the tabbed
  dashboard; `Ctrl+Alt+E` toggles a click-through overlay. (Desktop run-check;
  not CI.)

## Desktop run-check (2026-10-04)

`EW_SELFTEST=<out.json>` (optional `EW_SELFTEST_STAY=1`) runs `app/selftest.js`
inside the real app: clicks every tab via `executeJavaScript`, measures page
overflow, captures both windows, checks overlay corner alpha, focusability and
`globalShortcut.isRegistered`, then toggles the overlay through the same
handler the hotkey calls. It drives only EW's own windows.

Result: 7/7 tabs switch and fit (content 1264x761, scroll == client), both
hotkeys registered, toggle flips visibility, overlay corner alpha 0, native
ex-style `0x8080028` = NOACTIVATE | LAYERED | TRANSPARENT | TOPMOST
(click-through). Defect found and fixed: `html` painted `--fk-surface`, making
the overlay opaque; the root now carries `class="ew-overlay"` with a
transparent background (guarded by `app/test/shell.test.js`).

A physical Ctrl+Alt+E press was not sent this session (BDO running, desktop
input interrupted); registration + handler toggle are verified instead. The
operator's in-game check stays in the hand-off.

Launcher: `tools/launch.py` (server if unhealthy, then Electron, no console);
the Desktop shortcut "Ebonwake" runs it with pythonw.
