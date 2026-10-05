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

// ---- Plan 020: health pill, version skew, 404 copy, freshness card ----

const SHA = '7f297f7a1b2c3d4e5f60718293a4b5c6d7e8f901';
const NOW = Date.UTC(2026, 9, 5, 12);

test('healthPill: ok when answered recently and commits match (short vs full sha)', () => {
  const p = C.healthPill({ version: { commit: SHA }, appCommit: '7f297f7', lastOkMs: NOW - 1000, nowMs: NOW, sseOk: true });
  assert.deepStrictEqual(p, { level: 'ok', text: 'server ok' });
});

test('healthPill: warn when both commits known and differ', () => {
  const p = C.healthPill({ version: { commit: SHA }, appCommit: 'b0b782a', lastOkMs: NOW, nowMs: NOW, sseOk: true });
  assert.strictEqual(p.level, 'warn');
  assert.strictEqual(p.text, 'server outdated - restart');
});

test('healthPill: bad when no OK answer for 90 s or never', () => {
  const base = { version: { commit: SHA }, appCommit: '7f297f7', nowMs: NOW, sseOk: true };
  assert.strictEqual(C.healthPill(Object.assign({ lastOkMs: NOW - 90000 }, base)).level, 'ok');
  assert.strictEqual(C.healthPill(Object.assign({ lastOkMs: NOW - 90001 }, base)).level, 'bad');
  assert.strictEqual(C.healthPill(Object.assign({ lastOkMs: null }, base)).level, 'bad');
  assert.strictEqual(C.healthPill(null).level, 'bad');
});

test('healthPill: bad when the SSE stream errored, even with a mismatch', () => {
  const p = C.healthPill({ version: { commit: SHA }, appCommit: 'b0b782a', lastOkMs: NOW, nowMs: NOW, sseOk: false });
  assert.strictEqual(p.level, 'bad');
});

test('healthPill: unknown commit on either side -> ok', () => {
  for (const [sc, ac] of [[null, '7f297f7'], [SHA, 'unknown'], [SHA, ''], [undefined, undefined]]) {
    const p = C.healthPill({ version: { commit: sc }, appCommit: ac, lastOkMs: NOW, nowMs: NOW, sseOk: true });
    assert.strictEqual(p.level, 'ok', String(sc) + ' / ' + String(ac));
  }
  assert.strictEqual(C.healthPill({ version: null, appCommit: '7f297f7', lastOkMs: NOW, nowMs: NOW }).level, 'ok');
});

test('commitsDiffer: prefix-equal, case-insensitive, unknown never differs', () => {
  assert.strictEqual(C.commitsDiffer(SHA, '7F297F7'), false);
  assert.strictEqual(C.commitsDiffer('7f297f7', SHA), false);
  assert.strictEqual(C.commitsDiffer(SHA, 'b0b782a'), true);
  assert.strictEqual(C.commitsDiffer('unknown', 'b0b782a'), false);
});

test('notOnServer: one restart line, module named, path accepted', () => {
  const s = C.notOnServer('grind');
  assert.match(s, /^grind API missing: server is older than the app - restart it \(tray > Restart server\)$/);
  assert.match(C.notOnServer('/api/spots?goal=xp'), /^spots API missing: /);
  assert.match(C.notOnServer(), /^this API missing: /);
});

test('sourceFreshness: one pill per source with age, status, stale vs ttl_s', () => {
  const rows = C.sourceFreshness({
    market: { updated: new Date(NOW - 4 * 60000).toISOString(), ttl_s: 600, status: 'ok' },
    profile: { updated: new Date(NOW - 3 * 3600000).toISOString(), ttl_s: 3600, status: 'ok' },
    coupons: { updated: null, ttl_s: 3600, status: 'none' },
    deadeye: { done: 3, total: 10 },
    game: { updated: new Date(NOW).toISOString(), status: 'error' }
  }, NOW);
  assert.deepStrictEqual(rows.map((r) => r.name), ['coupons', 'deadeye', 'game', 'market', 'profile']);
  const by = Object.fromEntries(rows.map((r) => [r.name, r]));
  assert.deepStrictEqual(by.market, { name: 'market', age: '4m ago', status: 'ok', stale: false, cls: 'ok' });
  assert.strictEqual(by.profile.stale, true);
  assert.strictEqual(by.profile.cls, 'warn');
  assert.strictEqual(by.coupons.age, 'never');
  assert.strictEqual(by.coupons.cls, 'unknown');
  assert.strictEqual(by.deadeye.status, '3/10');
  assert.strictEqual(by.game.cls, 'bad');
  assert.deepStrictEqual(C.sourceFreshness(null, NOW), []);
});

test('serverRows: commit, started, pid, uptime, outdated flag', () => {
  const v = { commit: SHA, started: new Date(NOW - 2 * 3600000 - 5 * 60000).toISOString(), pid: 4242 };
  const rows = Object.fromEntries(C.serverRows(v, { ok: true }, 'b0b782a', NOW));
  assert.strictEqual(rows.status, 'ok');
  assert.strictEqual(rows['server commit'], '7f297f7');
  assert.strictEqual(rows['app commit'], 'b0b782a');
  assert.match(rows.outdated, /^yes/);
  assert.strictEqual(rows.uptime, '2h 05m');
  assert.strictEqual(rows.pid, '4242');
  const none = Object.fromEntries(C.serverRows(null, null, 'unknown', NOW));
  assert.strictEqual(none.status, 'no answer');
  assert.strictEqual(none.outdated, 'no');
  assert.strictEqual(none['server commit'], 'unknown');
});
