/* EW Electron main process: dashboard window + overlay window.
   ToS floor: the overlay is a separate transparent click-through window. It never
   hooks, injects into or sends input to the game. Hotkeys use globalShortcut only. */
'use strict';

const { app, BrowserWindow, Menu, Tray, globalShortcut, ipcMain, nativeImage, screen } = require('electron');
const selftest = require('./selftest');
const childProcess = require('child_process');
const fs = require('fs');
const http = require('http');
const path = require('path');
const core = require('./shared/ewcore');
const tray = require('./shared/tray');

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
  // Opt-in widgets (spec section 3) ride in the query; the overlay has no bridge.
  const widgets = core.overlayWidgets(readConfig());
  overlay.loadFile(path.join(__dirname, 'overlay', 'index.html'), { query: core.widgetsQuery(widgets) });
  overlay.on('closed', function () { overlay = null; });
}

// Dashboard writes (plans 002, 003): the server answers no CORS preflight, so
// the dashboard cannot POST itself. Only the dashboard may ask, only allowlisted
// routes with an exact body shape pass (core.validPost), and only the local EW
// server is ever contacted.
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

ipcMain.handle('ew:post', function (event, route, body) {
  if (!dashboard || event.sender !== dashboard.webContents) return { ok: false, error: 'not allowed' };
  if (!core.validPost(route, body)) return { ok: false, error: 'invalid request' };
  return postLocal(route, body);
});

function toggleOverlay() {
  if (!overlay) createOverlay();
  if (overlay.isVisible()) overlay.hide();
  else overlay.showInactive();
}

function showDashboard() {
  if (!dashboard) createDashboard();
  if (dashboard.isMinimized()) dashboard.restore();
  dashboard.show();
  dashboard.focus();
}

// GET a local EW server route: resolves { status, server, doc } or null.
function getLocal(route) {
  const base = new URL(core.SERVER);
  return new Promise(function (resolve) {
    const req = http.get({ hostname: base.hostname, port: base.port, path: route, timeout: 1500 },
      function (res) {
        const chunks = [];
        let size = 0;
        res.on('data', function (c) { size += c.length; if (size <= 65536) chunks.push(c); });
        res.on('end', function () {
          let doc = null;
          try { doc = JSON.parse(Buffer.concat(chunks).toString('utf8')); } catch (e) { /* non-JSON */ }
          resolve({ status: res.statusCode, server: res.headers.server, doc: doc });
        });
      });
    req.on('timeout', function () { req.destroy(new Error('timeout')); });
    req.on('error', function () { resolve(null); });
  });
}

// Tray "Restart server" (plan 010): stop only the EW server that answers
// /api/version with the EW contract, wait for the port to free, then start it
// the way tools/launch.py does (--server-only). Nothing else is contacted.
let restarting = false;
async function restartServer() {
  if (restarting) return;
  restarting = true;
  try {
    const v = await getLocal('/api/version');
    const pid = v ? tray.killablePid(v.doc, v.server, process.pid) : null;
    if (pid) {
      try { process.kill(pid); } catch (e) { /* already gone */ }
      for (let i = 0; i < 40 && await getLocal('/api/health'); i++) {
        await new Promise(function (r) { setTimeout(r, 250); });
      }
    }
    const c = tray.serverStartCommand(process.env, REPO);
    const child = childProcess.spawn(c.cmd, c.args,
      { cwd: c.cwd, detached: true, stdio: 'ignore', windowsHide: true });
    child.on('error', function () { /* pythonw missing: the pill shows offline */ });
    child.unref();
  } finally {
    restarting = false;
  }
}

let trayIcon = null;
function createTray() {
  const img = nativeImage.createFromBitmap(tray.iconBitmap(16), { width: 16, height: 16, scaleFactor: 1 });
  img.addRepresentation({ buffer: tray.iconBitmap(32), width: 32, height: 32, scaleFactor: 2 });
  trayIcon = new Tray(img);
  trayIcon.setToolTip('Ebonwake');
  trayIcon.setContextMenu(Menu.buildFromTemplate(tray.menuTemplate({
    showDashboard: showDashboard, toggleOverlay: toggleOverlay,
    restartServer: restartServer, quit: function () { app.quit(); }
  })));
  trayIcon.on('click', showDashboard);
}

if (process.env.EW_SELFTEST) {
  selftest.switches().forEach(function (s) { app.commandLine.appendSwitch.apply(app.commandLine, s); });
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', showDashboard);
  app.whenReady().then(function () {
    const keys = core.hotkeys(readConfig());
    createDashboard();
    createOverlay();
    createTray();
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
  app.on('will-quit', function () {
    globalShortcut.unregisterAll();
    if (trayIcon) trayIcon.destroy();
  });
  app.on('window-all-closed', function () { /* overlay keeps the app alive */ });
}
