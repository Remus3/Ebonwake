'use strict';
// Static guards for the Electron shell (no Electron needed). The desktop
// self-test (EW_SELFTEST=<out.json>, app/selftest.js) checks the same live.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const APP = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(APP, p), 'utf8');

test('overlay html root is transparent (html bg would paint opaque)', () => {
  assert.match(read('overlay/index.html'), /<html[^>]*class="ew-overlay"/);
  assert.match(read('shared/ew.css'), /html\.ew-overlay,\s*html\.ew-overlay body\s*\{\s*background:\s*transparent/);
});

test('overlay window is transparent, click-through, focus-less, globalShortcut only', () => {
  const m = read('main.js');
  assert.match(m, /transparent:\s*true/);
  assert.match(m, /focusable:\s*false/);
  assert.match(m, /setIgnoreMouseEvents\(true/);
  assert.match(m, /globalShortcut\.register\(keys\.toggleOverlay/);
  assert.doesNotMatch(m, /uiohook|iohook|robotjs|nut-js|SetWindowsHookEx|sendInputEvent/);
});

// ---- Plan 020 ----

test('renderer and main scripts compile (no Electron needed)', () => {
  const vm = require('vm');
  const files = ['main.js', 'preload.js'].concat(
    fs.readdirSync(path.join(APP, 'dashboard')).filter((f) => f.endsWith('.js')).map((f) => 'dashboard/' + f));
  for (const f of files) assert.doesNotThrow(() => new vm.Script(read(f), { filename: f }), f);
});

test('every dashboard module answers a 404 with C.notOnServer, no ad-hoc copy', () => {
  const dir = path.join(APP, 'dashboard');
  const mods = fs.readdirSync(dir).filter((f) => f.endsWith('.js'));
  assert.ok(mods.length >= 9);
  for (const f of mods) {
    const src = read('dashboard/' + f);
    assert.doesNotMatch(src, /not on this server/, f + ' carries ad-hoc 404 copy');
    const checks = src.match(/status === 404[^\n]*/g) || [];
    for (const line of checks) assert.match(line, /C\.notOnServer\(/, f + ': ' + line);
    // Every module-level getJSON that throws on !r.ok maps 404 first.
    if (/function getJSON[\s\S]*?if \(!r\.ok\)/.test(src) && f !== 'dashboard.js') {
      assert.ok(checks.length >= 1, f + ' getJSON has no 404 branch');
    }
  }
});

test('dashboard re-checks /api/version on a timer and on SSE reconnect', () => {
  const d = read('dashboard/dashboard.js');
  assert.match(d, /getJSON\('\/api\/version'\)/);
  assert.match(d, /setInterval\(pollVersion, C\.VERSION_POLL_MS\)/);
  assert.match(d, /onopen[\s\S]*?if \(reconnect\) pollVersion\(\)/);
  assert.match(d, /C\.healthPill\(/);
  assert.doesNotMatch(d, /JSON\.stringify\(/, 'System tab renders cards, not a JSON blob');
  assert.match(d, /C\.sourceFreshness\(/);
  assert.match(d, /C\.serverRows\(/);
});

test('appCommit is read-only; restart is one allowlisted, sender-checked IPC', () => {
  const pre = read('preload.js');
  const m = read('main.js');
  assert.match(pre, /appCommit: function \(\) \{ return APP_COMMIT; \}/);
  assert.match(pre, /ipcRenderer\.invoke\('ew:restart-server'\)/);
  assert.strictEqual((pre.match(/ipcRenderer\.\w+\(/g) || []).length, 2, 'post + restart only');
  assert.match(m, /rev-parse', '--short', 'HEAD'/);
  assert.match(m, /windowsHide: true/);
  assert.match(m, /additionalArguments: \['--ew-app-commit=' \+ APP_COMMIT\]/);
  const h = m.slice(m.indexOf("ipcMain.handle('ew:restart-server'"));
  assert.match(h, /event\.sender !== dashboard\.webContents/);
  assert.match(h, /await restartServer\(\)/);
  assert.match(read('dashboard/index.html'), /id="server-restart"[^>]*hidden/);
});
