/* EW Electron main process: dashboard window + overlay window.
   ToS floor: the overlay is a separate transparent click-through window. It never
   hooks, injects into or sends input to the game. Hotkeys use globalShortcut only. */
'use strict';

const { app, BrowserWindow, Menu, Notification, Tray, dialog, globalShortcut, ipcMain, nativeImage, screen, shell } = require('electron');
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

// Plan 020: the app's own commit, read once at start (hidden spawn, no console),
// handed to the dashboard preload as a launch argument; 'unknown' on failure.
function readAppCommit() {
  try {
    const out = childProcess.execFileSync('git', ['rev-parse', '--short', 'HEAD'],
      { cwd: REPO, encoding: 'utf8', timeout: 5000, windowsHide: true, stdio: ['ignore', 'pipe', 'ignore'] });
    const sha = out.trim();
    return /^[0-9a-f]{4,40}$/.test(sha) ? sha : 'unknown';
  } catch (e) {
    return 'unknown';
  }
}
const APP_COMMIT = readAppCommit();

let dashboard = null;
let overlay = null;
let NOTIFY_SILENT = true;
let notifyShown = 0;

function createDashboard() {
  const cfg = readConfig();
  NOTIFY_SILENT = core.notifySilent(cfg);
  dashboard = new BrowserWindow({
    width: 1280, height: 800, minWidth: 960, minHeight: 600,
    title: 'Ebonwake', backgroundColor: '#0f1216', show: true,
    webPreferences: {
      contextIsolation: true, nodeIntegration: false, sandbox: true,
      preload: path.join(__dirname, 'preload.js'),
      additionalArguments: ['--ew-app-commit=' + APP_COMMIT, '--ew-notify=' + core.notifyArg(core.notifyPrefs(cfg))]
    }
  });
  dashboard.removeMenu();
  // The dashboard (and its preload bridge) never leaves its own file page.
  dashboard.webContents.on('will-navigate', function (e) { e.preventDefault(); });
  dashboard.webContents.setWindowOpenHandler(function () { return { action: 'deny' }; });
  // Plan 057: the Deadeye tab blocks unload while a note is unsaved (its
  // draft is already in localStorage); Electron would cancel silently, so ask.
  dashboard.webContents.on('will-prevent-unload', function (e) {
    const choice = dialog.showMessageBoxSync(dashboard, {
      type: 'question', buttons: ['Leave', 'Stay'], defaultId: 1, cancelId: 1, noLink: true,
      title: 'Ebonwake', message: 'Unsaved Deadeye notes.',
      detail: 'The draft is kept locally and offered back next time. Leave anyway?'
    });
    if (choice === 0) e.preventDefault();
  });
  // Plan 030: settings ui.scale is the dashboard zoom (0.9-1.3).
  dashboard.webContents.on('did-finish-load', function () {
    if (dashboard) dashboard.webContents.setZoomFactor(core.uiScale(readConfig()));
  });
  dashboard.loadFile(path.join(__dirname, 'dashboard', 'index.html'));
  dashboard.on('closed', function () { dashboard = null; });
}

// Plan 022: placement from config overlay.{anchor, display, scale} (default
// middle-left of the primary display, off BDO's top-right minimap); height
// follows the overlay page's content (ew:overlay-size).
const OVERLAY_MARGIN = 16;
let overlayCfg = core.overlayConfig({});

function overlayWorkArea() {
  const all = screen.getAllDisplays();
  const d = overlayCfg.display !== null && all[overlayCfg.display] ? all[overlayCfg.display]
    : screen.getPrimaryDisplay();
  return d.workArea;
}

function overlayPlacement(height) {
  const size = { width: Math.round(core.OVERLAY_SIZE.width * overlayCfg.scale), height: height };
  return core.overlayBounds(overlayWorkArea(), overlayCfg.anchor, size, OVERLAY_MARGIN);
}

function createOverlay() {
  const cfg = readConfig();
  overlayCfg = core.overlayConfig(cfg);
  const b = overlayPlacement(core.OVERLAY_SIZE.height);
  overlay = new BrowserWindow({
    x: b.x, y: b.y, width: b.width, height: b.height, useContentSize: true,
    transparent: true, frame: false, resizable: false, movable: false,
    focusable: false, skipTaskbar: true, hasShadow: false, show: false,
    alwaysOnTop: true,
    webPreferences: {
      contextIsolation: true, nodeIntegration: false, sandbox: true,
      preload: path.join(__dirname, 'overlay', 'preload.js')
    }
  });
  overlay.setAlwaysOnTop(true, 'screen-saver');
  overlay.setIgnoreMouseEvents(true, { forward: false });
  overlay.setVisibleOnAllWorkspaces(true);
  overlay.webContents.on('will-navigate', function (e) { e.preventDefault(); });
  overlay.webContents.setWindowOpenHandler(function () { return { action: 'deny' }; });
  // Opt-in widgets (spec section 3) and scale / opacity ride in the query.
  const query = Object.assign(core.widgetsQuery(core.overlayWidgets(cfg)), core.overlayStyleQuery(overlayCfg));
  overlay.loadFile(path.join(__dirname, 'overlay', 'index.html'), { query: query });
  const win = overlay;
  overlay.on('closed', function () { if (overlay === win) overlay = null; });
}

// Plan 030: a settings save recreates the overlay from the new config (same
// visibility) - no app restart. No payload: main re-reads config/local.json.
function reloadOverlay() {
  const wasVisible = !!overlay && overlay.isVisible();
  if (overlay) overlay.destroy();
  overlay = null;
  createOverlay();
  if (wasVisible) overlay.showInactive();
}

// One-way, number only, from the overlay page alone: clamp 60-600 px and
// re-place so bottom / middle anchors stay anchored.
ipcMain.on('ew:overlay-size', function (event, px) {
  if (!overlay || event.sender !== overlay.webContents) return;
  const h = core.overlayHeight(px);
  if (h === null) return;
  const b = overlayPlacement(h);
  const cur = overlay.getContentBounds();
  if (cur.x === b.x && cur.y === b.y && cur.width === b.width && cur.height === b.height) return;
  overlay.setContentBounds(b);
});

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

// Hotkeys (globalShortcut only). A settings save re-registers the changed
// ones; a combination another app holds is reported and the old one kept.
let keys = core.hotkeys({});
const HOTKEY_ACTIONS = { toggleOverlay: toggleOverlay, showDashboard: showDashboard };

function reloadHotkeys() {
  const next = core.hotkeys(readConfig());
  const names = Object.keys(HOTKEY_ACTIONS).filter(function (n) { return next[n] !== keys[n]; });
  names.forEach(function (n) { globalShortcut.unregister(keys[n]); });
  const conflicts = [];
  names.forEach(function (n) {
    let ok = false;
    try { ok = globalShortcut.register(next[n], HOTKEY_ACTIONS[n]); } catch (e) { ok = false; }
    if (ok) {
      keys[n] = next[n];
    } else {
      conflicts.push(next[n]);
      try { globalShortcut.register(keys[n], HOTKEY_ACTIONS[n]); } catch (e) { /* stays unbound */ }
    }
  });
  return conflicts;
}

ipcMain.handle('ew:reload-overlay', function (event) {
  if (!dashboard || event.sender !== dashboard.webContents) return { ok: false, error: 'not allowed' };
  reloadOverlay();
  return { ok: true };
});

ipcMain.handle('ew:reload-shell', function (event) {
  if (!dashboard || event.sender !== dashboard.webContents) return { ok: false, error: 'not allowed' };
  const conflicts = reloadHotkeys();
  dashboard.webContents.setZoomFactor(core.uiScale(readConfig()));
  return { ok: conflicts.length === 0, conflicts: conflicts };
});

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

// Plan 020: the dashboard's outdated-server pill reuses the tray restart path.
ipcMain.handle('ew:restart-server', async function (event) {
  if (!dashboard || event.sender !== dashboard.webContents) return { ok: false, error: 'not allowed' };
  await restartServer();
  return { ok: true };
});

// Plan 026: an OS notification for a dashboard rule hit. Only the dashboard may
// ask, exactly {title, body} (ASCII-printable, 64 / 200 chars), at most 6 a
// minute; no actions, a click only brings the dashboard forward.
const notifyLimit = core.rateLimiter(core.NOTIFY_RATE.max, core.NOTIFY_RATE.windowMs);
ipcMain.handle('ew:notify', function (event, n) {
  if (!dashboard || event.sender !== dashboard.webContents) return { ok: false, error: 'not allowed' };
  if (!core.validNotify(n)) return { ok: false, error: 'invalid request' };
  if (!Notification.isSupported()) return { ok: false, error: 'notifications not supported' };
  if (!notifyLimit.allow(Date.now())) return { ok: false, error: 'rate limited' };
  const note = new Notification({ title: n.title, body: n.body, silent: NOTIFY_SILENT });
  note.on('click', showDashboard);
  note.show();
  notifyShown++;
  return { ok: true };
});

// Plan 057: a source link opens in the operator's browser. Only the dashboard
// may ask, exactly {url}, https on core.EXTERNAL_HOSTS only, at most 6 a
// minute; the normalized href is what opens. The dashboard never navigates.
const openLimit = core.rateLimiter(core.OPEN_RATE.max, core.OPEN_RATE.windowMs);
ipcMain.handle('ew:open-external', async function (event, body) {
  if (!dashboard || event.sender !== dashboard.webContents) return { ok: false, error: 'not allowed' };
  if (!core.validOpenExternal(body)) return { ok: false, error: 'link not allowed' };
  if (!openLimit.allow(Date.now())) return { ok: false, error: 'rate limited' };
  try {
    await shell.openExternal(core.externalUrl(body.url));
  } catch (e) {
    return { ok: false, error: 'could not open the browser' };
  }
  return { ok: true };
});

if (process.env.EW_SELFTEST) {
  selftest.switches().forEach(function (s) { app.commandLine.appendSwitch.apply(app.commandLine, s); });
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', showDashboard);
  app.whenReady().then(function () {
    keys = core.hotkeys(readConfig());
    createDashboard();
    createOverlay();
    createTray();
    globalShortcut.register(keys.toggleOverlay, toggleOverlay);
    globalShortcut.register(keys.showDashboard, showDashboard);
    if (process.env.EW_SELFTEST) {
      selftest.run({
        app: app, dashboard: dashboard, overlay: overlay, keys: keys,
        globalShortcut: globalShortcut, toggleOverlay: toggleOverlay,
        out: process.env.EW_SELFTEST,
        freshStore: process.env.EW_SELFTEST_FRESH === '1',
        notifyShown: function () { return notifyShown; },
        overlayPlace: { workArea: overlayWorkArea(), defaultAnchor: overlayCfg.anchor === core.overlayConfig({}).anchor }
      });
    }
  });
  app.on('will-quit', function () {
    globalShortcut.unregisterAll();
    if (trayIcon) trayIcon.destroy();
  });
  app.on('window-all-closed', function () { /* overlay keeps the app alive */ });
}
