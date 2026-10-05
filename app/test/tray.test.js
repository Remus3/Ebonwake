'use strict';
// Plan 010: tray menu, tray icon bitmap, server-restart helpers (pure, no
// Electron) and static guards on how main.js wires them.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const T = require('../shared/tray');

const APP = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(APP, p), 'utf8');
const EW_DOC = { commit: 'a'.repeat(40), started: '2026-10-05T00:00:00Z', pid: 4242,
  config_hash: 'b'.repeat(12), schema: 1 };

test('menu template: Show dashboard / Toggle overlay / Restart server / Quit, in order', () => {
  const hits = [];
  const acts = {
    showDashboard: () => hits.push('show'), toggleOverlay: () => hits.push('toggle'),
    restartServer: () => hits.push('restart'), quit: () => hits.push('quit')
  };
  const m = T.menuTemplate(acts);
  const items = m.filter((i) => i.type !== 'separator');
  assert.deepStrictEqual(items.map((i) => i.label),
    ['Show dashboard', 'Toggle overlay', 'Restart server', 'Quit']);
  items.forEach((i) => i.click());
  assert.deepStrictEqual(hits, ['show', 'toggle', 'restart', 'quit']);
  assert.strictEqual(m[m.length - 1].label, 'Quit');
  assert.strictEqual(m[m.length - 2].type, 'separator');
});

test('menu template rejects missing actions', () => {
  assert.throws(() => T.menuTemplate({ showDashboard() {} }));
});

test('icon bitmap: BGRA, square, transparent corners, opaque ember ring', () => {
  for (const size of [16, 32]) {
    const b = T.iconBitmap(size);
    assert.strictEqual(b.length, size * size * 4);
    const px = (x, y) => Array.from(b.subarray((y * size + x) * 4, (y * size + x) * 4 + 4));
    assert.strictEqual(px(0, 0)[3], 0, 'corner transparent');
    const c = Math.floor(size / 2);
    assert.strictEqual(px(c, c)[3], 255, 'core opaque');
    const ring = px(c + Math.round(size * 0.28), c);
    assert.deepStrictEqual(ring, [T.EMBER[2], T.EMBER[1], T.EMBER[0], 255], 'ember ring, BGRA order');
  }
  assert.deepStrictEqual(T.iconBitmap(16), T.iconBitmap(16), 'deterministic');
});

test('isEwVersion needs the exact fleet contract and the Ebonwake banner', () => {
  assert.ok(T.isEwVersion(EW_DOC, 'Ebonwake '));
  assert.ok(!T.isEwVersion(EW_DOC, 'nginx'));
  assert.ok(!T.isEwVersion(EW_DOC, undefined));
  assert.ok(!T.isEwVersion(Object.assign({}, EW_DOC, { extra: 1 }), 'Ebonwake '));
  assert.ok(!T.isEwVersion(Object.assign({}, EW_DOC, { schema: 2 }), 'Ebonwake '));
  assert.ok(!T.isEwVersion(null, 'Ebonwake '));
});

test('killablePid: only an EW server pid, never ourselves or nonsense', () => {
  assert.strictEqual(T.killablePid(EW_DOC, 'Ebonwake ', 1), 4242);
  assert.strictEqual(T.killablePid(EW_DOC, 'nginx', 1), null);
  assert.strictEqual(T.killablePid(EW_DOC, 'Ebonwake ', 4242), null);
  for (const pid of [0, -3, 1.5, '4242', null]) {
    assert.strictEqual(T.killablePid(Object.assign({}, EW_DOC, { pid }), 'Ebonwake ', 1), null);
  }
});

test('serverStartCommand runs tools/launch.py --server-only with the launcher python', () => {
  const repo = path.join('r', 'ew');
  const a = T.serverStartCommand({ EW_PYTHONW: path.join('py', 'pythonw.exe') }, repo);
  assert.strictEqual(a.cmd, path.join('py', 'pythonw.exe'));
  assert.deepStrictEqual(a.args, [path.join(repo, 'tools', 'launch.py'), '--server-only']);
  assert.strictEqual(a.cwd, repo);
  assert.strictEqual(T.serverStartCommand({}, repo).cmd, 'pythonw.exe');
});

test('main.js wires the tray from the pure module and keeps single instance', () => {
  const m = read('main.js');
  assert.match(m, /require\('\.\/shared\/tray'\)/);
  assert.match(m, /new Tray\(/);
  assert.match(m, /Menu\.buildFromTemplate\(tray\.menuTemplate\(/);
  assert.match(m, /nativeImage\.createFromBitmap\(tray\.iconBitmap\(16\)/);
  assert.match(m, /requestSingleInstanceLock\(\)/);
  assert.match(m, /app\.on\('second-instance',\s*showDashboard\)/);
  assert.match(m, /killablePid\(/);
  // Restart server never contacts anything but the local EW server.
  assert.doesNotMatch(m, /sendInputEvent|uiohook|robotjs|SetWindowsHookEx/);
});

test('showDashboard restores a minimized window before focusing', () => {
  const m = read('main.js');
  const fn = m.slice(m.indexOf('function showDashboard'));
  assert.match(fn.slice(0, fn.indexOf('\n}')), /isMinimized\(\)\)\s*dashboard\.restore\(\)/);
});

test('launch.py hands its pythonw to Electron for Restart server', () => {
  const src = fs.readFileSync(path.join(APP, '..', 'tools', 'launch.py'), 'utf8');
  assert.match(src, /EW_PYTHONW/);
});
