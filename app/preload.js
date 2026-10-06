/* EW dashboard preload (sandboxed). Exposes: a POST to the local EW server,
   forwarded to the main process, which checks the route against a fixed
   allowlist, validates the body and POSTs (the server refuses renderer POSTs by
   design). Routes: /api/market/watch, /api/today, /api/progress, /api/grind,
   /api/events, /api/deadeye, /api/ocr, /api/leveling, /api/bosses (plan 032),
   /api/pets (plan 043), /api/inventory (plan 045), /api/mounts (plan 044),
   /api/onboarding (plan 051), /api/crafting (plan 054), /api/imperial
   (plan 053).
   Plan 020 adds the app's
   commit (a read-only launch argument) and a restart of EW's own server (the
   tray "Restart server" path; it never touches the game). Plan 026 adds an
   OS notification ({title, body}; main validates and rate-limits) and the
   enabled notify rule names (a read-only launch argument). Plan 030 adds
   /api/settings and two payload-free reloads after a settings save: the
   overlay window, and the hotkeys + dashboard zoom (main re-reads the config).
   Plan 057 adds {url} open-in-browser for allowlisted https source links
   (main re-checks core.externalUrl; the dashboard itself never navigates).
   The overlay window has its own one-way preload (overlay/preload.js). */
'use strict';

const { contextBridge, ipcRenderer } = require('electron');

function launchArg(prefix) {
  const a = process.argv.filter(function (x) { return x.indexOf(prefix) === 0; })[0];
  return a === undefined ? null : a.slice(prefix.length);
}

const APP_COMMIT = launchArg('--ew-app-commit=') || 'unknown';
const notifyArg = launchArg('--ew-notify=');
const NOTIFY_ARG = notifyArg !== null && /^[A-Za-z,]*$/.test(notifyArg) ? notifyArg : null;

contextBridge.exposeInMainWorld('ewApi', {
  post: function (route, body) { return ipcRenderer.invoke('ew:post', route, body); },
  appCommit: function () { return APP_COMMIT; },
  restartServer: function () { return ipcRenderer.invoke('ew:restart-server'); },
  notify: function (n) { return ipcRenderer.invoke('ew:notify', n); },
  notifyPrefs: function () { return NOTIFY_ARG; },
  reloadOverlay: function () { return ipcRenderer.invoke('ew:reload-overlay'); },
  reloadShell: function () { return ipcRenderer.invoke('ew:reload-shell'); },
  openExternal: function (url) { return ipcRenderer.invoke('ew:open-external', { url: url }); }
});
