/* EW Electron main process: dashboard window + overlay window.
   ToS floor: the overlay is a separate transparent click-through window. It never
   hooks, injects into or sends input to the game. Hotkeys use globalShortcut only. */
'use strict';

const { app, BrowserWindow, globalShortcut, screen } = require('electron');
const fs = require('fs');
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
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true }
  });
  dashboard.removeMenu();
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
  });
  app.on('will-quit', function () { globalShortcut.unregisterAll(); });
  app.on('window-all-closed', function () { /* overlay keeps the app alive */ });
}
