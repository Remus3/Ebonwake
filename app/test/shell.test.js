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
