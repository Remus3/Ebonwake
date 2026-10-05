/* EW dashboard preload (sandboxed). Exposes exactly one call: a POST to the
   local EW server, forwarded to the main process, which checks the route
   against a fixed allowlist, validates the body and POSTs (the server refuses
   renderer POSTs by design). Routes: /api/market/watch, /api/today,
   /api/progress.
   The overlay window has no preload. */
'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('ewApi', {
  post: function (route, body) { return ipcRenderer.invoke('ew:post', route, body); }
});
