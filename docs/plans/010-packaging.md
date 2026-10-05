# Plan 010 - Packaging: start on login, single instance, tray

Status: open. One lane (`build`).

1. Single instance: Electron `app.requestSingleInstanceLock()`; a second launch
   focuses the dashboard. Server: a second `python -m server.ew` sees 8940 bound
   by an EW server (`/api/version` answers) and exits 0.
2. Tray: Electron `Tray` with Show dashboard / Toggle overlay / Restart server /
   Quit. Icon from `app/assets/`.
3. Start on login: `tools/logon_task.py install|remove|status` registers a
   per-user Task Scheduler logon task (schtasks, delay 60 s per the machine's
   staggered-startup practice) running `tools/launch.py` with pythonw.exe.
   Idempotent; status reads back the task. Install is run by the main session
   and read back; no admin rights.
4. Self-test of an occluded dashboard: capture with `webContents.capturePage`
   after `showInactive()` + one paint, so unattended screenshots are real
   (carried hand-off item).

Acceptance: gates green; logon task read back; self-test 7/7; one push.

## As-built deviations (adjudicated in-lane, 2026-10-05)

1. Tray icon is drawn at run time, not loaded from `app/assets/`.
   Decision: `app/shared/tray.js` `iconBitmap(size)` renders a 16x16 and a
   32x32 (@2x) BGRA bitmap (dark rounded tile, ember ring, dark core) that
   main.js hands to `nativeImage.createFromBitmap` + `addRepresentation`.
   Alternatives: committed PNGs from a stdlib generator under `tools/`; a
   base64 data URL in JS. Why: no binary blob in the repo to drift from its
   generator, the pixels are unit-tested (`app/test/tray.test.js`), and the
   lane's sandbox would not run a new generator script. Reverses if: a
   packaged build (installer, .ico for the exe) needs real icon files - then
   add the generator and commit its output.
2. Server single-instance identity = fleet P0-5 version keys AND the
   `Server: Ebonwake` banner. Decision: `server/ew/single.py` `probe()` answers
   "ew" only when both match; "none" on refused/timeout (bind), "foreign" on any
   other answer (bind and let the OS refuse). Alternatives: version keys only.
   Why: the version contract is fleet-wide, so a sibling's server on a stray
   port would otherwise look like EW. Reverses if: the fleet contract gains an
   app-name key - then match on that instead of the banner.
3. "Restart server" kills only the pid that `/api/version` reports, and only
   when that answer passes the same EW check (`tray.killablePid`, never our own
   pid), then waits for `/api/health` to stop answering (max 10 s) and spawns
   `tools/launch.py --server-only` detached with the pythonw that launch.py put
   in `EW_PYTHONW` (PATH `pythonw.exe` otherwise). Alternatives: track the
   child Electron started (Electron never starts the server at launch, so
   there is none); `taskkill` by port. Why: the version doc already names the
   pid and proves it is EW. Reverses if: the server gains a local
   `/api/shutdown` route - then ask it to exit instead of killing.
4. Logon task XML is written as UTF-16 LE with BOM to a temp file under
   `ops/runtime/` and removed after `/Create`; status decodes UTF-16 or
   UTF-8/ANSI output and reads command, arguments, workdir, delay, enabled
   (the user id is deliberately left out of the printed JSON so it never lands
   in chat). `remove` is idempotent (`removed: false` when absent).
   Alternatives: `/SC ONLOGON /DELAY` flags without XML (cannot set
   LeastPrivilege + WorkingDirectory together). Reverses if: schtasks rejects
   the XML on another Windows build - then fall back to flags plus a `cd` wrapper.
5. Self-test paint wait = double `requestAnimationFrame` via
   `executeJavaScript`, raced against a 3 s timeout (`painted: false` is
   reported and fails `ok`, never hangs), after `showInactive()` and
   `setBackgroundThrottling(false)`; under `EW_SELFTEST` main.js also applies
   `selftest.switches()` (disable-backgrounding-occluded-windows,
   disable-renderer-backgrounding, disable-features=CalculateNativeWinOcclusion)
   so a covered window keeps painting. Alternatives: the webContents 'paint'
   event (offscreen rendering only). Why: rAF resolves only after a real
   frame. Reverses if: the live self-test still returns blank captures with
   `painted: true` - then switch the capture to offscreen rendering.
6. Electron single instance already existed (plan 001); plan 010 added the
   minimized-window restore in `showDashboard` and static guards.
