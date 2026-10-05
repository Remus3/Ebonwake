'use strict';
const test = require('node:test');
const assert = require('node:assert');
const C = require('../shared/ewcore');

test('daily reset is next 00:00 UTC', () => {
  const now = Date.UTC(2026, 9, 4, 23, 30);
  assert.strictEqual(C.nextDailyReset(now), Date.UTC(2026, 9, 5));
});

test('weekly reset is next Thursday 00:00 UTC, never today', () => {
  const sun = Date.UTC(2026, 9, 4, 12); // 2026-10-04 is a Sunday
  assert.strictEqual(new Date(sun).getUTCDay(), 0);
  assert.strictEqual(C.nextWeeklyReset(sun), Date.UTC(2026, 9, 8));
  const thu = Date.UTC(2026, 9, 8, 0, 0, 1);
  assert.strictEqual(C.nextWeeklyReset(thu), Date.UTC(2026, 9, 15));
});

test('fmtDuration shapes', () => {
  assert.strictEqual(C.fmtDuration(65 * 1000), '1m 05s');
  assert.strictEqual(C.fmtDuration(3 * 3600 * 1000 + 7 * 60 * 1000), '3h 07m');
  assert.strictEqual(C.fmtDuration(50 * 3600 * 1000), '2d 2h');
  assert.strictEqual(C.fmtDuration(-5), '0m 00s');
});

test('freshness classes', () => {
  assert.strictEqual(C.freshness(null, 0, 1).cls, 'unknown');
  assert.strictEqual(C.freshness(1000, 1500, 1000).cls, 'ok');
  assert.strictEqual(C.freshness(0, 2500, 1000).cls, 'warn');
  assert.strictEqual(C.freshness(0, 5000, 1000).cls, 'bad');
});

test('normalizeTabs drops junk and falls back to System', () => {
  assert.deepStrictEqual(C.normalizeTabs(null).map((t) => t.id), ['system']);
  const s = { tabs: [{ id: 'market', title: 'Market' }, { id: 'Bad Id', title: 'x' }, null] };
  assert.deepStrictEqual(C.normalizeTabs(s).map((t) => t.id), ['market']);
});

test('backoff is exponential and capped', () => {
  assert.strictEqual(C.backoffMs(0, 1000, 30000), 1000);
  assert.strictEqual(C.backoffMs(3, 1000, 30000), 8000);
  assert.strictEqual(C.backoffMs(20, 1000, 30000), 30000);
});

test('hotkeys accept only valid accelerators', () => {
  assert.deepStrictEqual(C.hotkeys({}), C.DEFAULT_HOTKEYS);
  const hk = C.hotkeys({ hotkeys: { toggleOverlay: 'Control+Shift+O', showDashboard: 'rm -rf' } });
  assert.strictEqual(hk.toggleOverlay, 'Control+Shift+O');
  assert.strictEqual(hk.showDashboard, C.DEFAULT_HOTKEYS.showDashboard);
  assert.strictEqual(C.validAccelerator('E'), false);
  assert.strictEqual(C.validAccelerator('Alt+F12'), true);
});

test('overlay main process stays click-through and hook-free', () => {
  const fs = require('fs');
  const path = require('path');
  const src = fs.readFileSync(path.join(__dirname, '..', 'main.js'), 'utf8');
  assert.match(src, /setIgnoreMouseEvents\(true/);
  assert.match(src, /focusable: false/);
  assert.match(src, /transparent: true/);
  for (const banned of ['robotjs', 'nut-js', 'iohook', 'uiohook', 'sendInput', 'keybd_event', 'ffi-napi', 'koffi']) {
    assert.ok(!src.includes(banned), 'banned input/hook API in main.js: ' + banned);
  }
});
