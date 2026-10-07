/* EW overlay preload (sandboxed, plan 022). Exposes one thing: a one-way
   report of the overlay page's content height (a number) so main can size the
   window to its content. No other channel, no reply, nothing reaches the game. */
'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('ewOverlay', {
  reportSize: function (px) {
    if (typeof px === 'number' && isFinite(px)) ipcRenderer.send('ew:overlay-size', px);
  }
});
