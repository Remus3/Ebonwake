# Plan 001 - Skeleton

Status: in progress (2026-10-04). Lane: `build`.

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
