/* EW dashboard preload (sandboxed). Exposes exactly one call: market watchlist
   add/remove, forwarded to the main process, which validates the body and POSTs
   to the local EW server (the server refuses renderer POSTs by design).
   The overlay window has no preload. */
'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('ewMarket', {
  watch: function (body) { return ipcRenderer.invoke('ew:market-watch', body); }
});
