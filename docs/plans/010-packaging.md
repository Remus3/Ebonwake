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
