/* EW Electron main process: dashboard window + overlay window.
   ToS floor: the overlay is a separate transparent click-through window. It never
   hooks, injects into or sends input to the game. Hotkeys use globalShortcut only. */
'use strict';

const { app, BrowserWindow, globalShortcut, ipcMain, screen } = require('electron');
const selftest = require('./selftest');
const fs = require('fs');
const http = require('http');
const path = require('path');
const core = require('./shared/ewcore');

const REPO = path.resolve(__dirname, '..');

function readConfig() {
  try {
    return JSON.parse(fs.readFileSync(path.join(REPO, 'config', 'local.json'), 'utf8'));
  } catch (e) {
    return {};
  }
}

let dashboard = null;
let overlay = null;

function createDashboard() {
  dashboard = new BrowserWindow({
    width: 1280, height: 800, minWidth: 960, minHeight: 600,
    title: 'Ebonwake', backgroundColor: '#0f1216', show: true,
    webPreferences: {
      contextIsolation: true, nodeIntegration: false, sandbox: true,
      preload: path.join(__dirname, 'preload.js')
    }
  });
  dashboard.removeMenu();
  // The dashboard (and its preload bridge) never leaves its own file page.
  dashboard.webContents.on('will-navigate', function (e) { e.preventDefault(); });
  dashboard.webContents.setWindowOpenHandler(function () { return { action: 'deny' }; });
  dashboard.loadFile(path.join(__dirname, 'dashboard', 'index.html'));
  dashboard.on('closed', function () { dashboard = null; });
}

function createOverlay() {
  const wa = screen.getPrimaryDisplay().workArea;
  const w = 340;
  const h = 220;
  overlay = new BrowserWindow({
    x: wa.x + wa.width - w - 16, y: wa.y + 16, width: w, height: h,
    transparent: true, frame: false, resizable: false, movable: false,
    focusable: false, skipTaskbar: true, hasShadow: false, show: false,
    alwaysOnTop: true,
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true }
  });
  overlay.setAlwaysOnTop(true, 'screen-saver');
  overlay.setIgnoreMouseEvents(true, { forward: false });
  overlay.setVisibleOnAllWorkspaces(true);
  overlay.loadFile(path.join(__dirname, 'overlay', 'index.html'));
  overlay.on('closed', function () { overlay = null; });
}

// Market watchlist writes (plan 002): the server answers no CORS preflight, so
// the dashboard cannot POST itself. Only the dashboard may ask, only the exact
// add/remove shape passes, and only the local EW server is ever contacted.
function postLocal(route, body) {
  const base = new URL(core.SERVER);
  const data = Buffer.from(JSON.stringify(body), 'utf8');
  return new Promise(function (resolve) {
    const req = http.request({
      hostname: base.hostname, port: base.port, path: route, method: 'POST', timeout: 10000,
      headers: { 'Content-Type': 'application/json', 'Content-Length': data.length }
    }, function (res) {
      const chunks = [];
      let size = 0;
      res.on('data', function (c) { size += c.length; if (size <= 65536) chunks.push(c); });
      res.on('end', function () {
        let parsed = null;
        try { parsed = JSON.parse(Buffer.concat(chunks).toString('utf8')); } catch (e) { /* non-JSON */ }
        const ok = res.statusCode >= 200 && res.statusCode < 300;
        resolve(ok ? { ok: true, status: res.statusCode, data: parsed }
          : { ok: false, status: res.statusCode,
            error: (parsed && typeof parsed.error === 'string' ? parsed.error : 'HTTP ' + res.statusCode) });
      });
    });
    req.on('timeout', function () { req.destroy(new Error('timeout')); });
    req.on('error', function (e) { resolve({ ok: false, error: 'server offline (' + e.message + ')' }); });
    req.end(data);
  });
}

ipcMain.handle('ew:market-watch', function (event, body) {
  if (!dashboard || event.sender !== dashboard.webContents) return { ok: false, error: 'not allowed' };
  if (!core.validWatchBody(body)) return { ok: false, error: 'invalid request' };
  return postLocal('/api/market/watch', body);
});

function toggleOverlay() {
  if (!overlay) createOverlay();
  if (overlay.isVisible()) overlay.hide();
  else overlay.showInactive();
}

function showDashboard() {
  if (!dashboard) createDashboard();
  dashboard.show();
  dashboard.focus();
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', showDashboard);
  app.whenReady().then(function () {
    const keys = core.hotkeys(readConfig());
    createDashboard();
    createOverlay();
    globalShortcut.register(keys.toggleOverlay, toggleOverlay);
    globalShortcut.register(keys.showDashboard, showDashboard);
    if (process.env.EW_SELFTEST) {
      selftest.run({
        app: app, dashboard: dashboard, overlay: overlay, keys: keys,
        globalShortcut: globalShortcut, toggleOverlay: toggleOverlay,
        out: process.env.EW_SELFTEST
      });
    }
  });
  app.on('will-quit', function () { globalShortcut.unregisterAll(); });
  app.on('window-all-closed', function () { /* overlay keeps the app alive */ });
}
