/* EW dashboard preload (sandboxed). Exposes: a POST to the local EW server,
   forwarded to the main process, which checks the route against a fixed
   allowlist, validates the body and POSTs (the server refuses renderer POSTs by
   design). Routes: /api/market/watch, /api/today, /api/progress, /api/grind,
   /api/events, /api/deadeye, /api/ocr, /api/leveling. Plan 020 adds the app's
   commit (a read-only launch argument) and a restart of EW's own server (the
   tray "Restart server" path; it never touches the game).
   The overlay window has its own one-way preload (overlay/preload.js). */
'use strict';

const { contextBridge, ipcRenderer } = require('electron');

const COMMIT_ARG = '--ew-app-commit=';
const commitArg = process.argv.filter(function (a) { return a.indexOf(COMMIT_ARG) === 0; })[0];
const APP_COMMIT = commitArg ? commitArg.slice(COMMIT_ARG.length) : 'unknown';

contextBridge.exposeInMainWorld('ewApi', {
  post: function (route, body) { return ipcRenderer.invoke('ew:post', route, body); },
  appCommit: function () { return APP_COMMIT; },
  restartServer: function () { return ipcRenderer.invoke('ew:restart-server'); }
});
