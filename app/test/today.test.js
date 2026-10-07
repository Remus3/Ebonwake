'use strict';
// Plan 003 slice B: pure Today helpers (ewcore.js), the general POST bridge and
// static guards on the Today tab (today.js) and overlay. No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const APP = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(APP, p), 'utf8');
const T = (s) => Date.parse(s);
const DAY = 86400000;

test('lastDailyReset: 00:00 UTC boundaries and month/year rollover', () => {
  assert.strictEqual(C.lastDailyReset(T('2026-10-04T00:00:00Z')), T('2026-10-04T00:00:00Z'));
  assert.strictEqual(C.lastDailyReset(T('2026-10-03T23:59:59Z')), T('2026-10-03T00:00:00Z'));
  assert.strictEqual(C.lastDailyReset(T('2026-10-04T13:45:10.500Z')), T('2026-10-04T00:00:00Z'));
  assert.strictEqual(C.lastDailyReset(T('2027-01-01T00:00:00Z')), T('2027-01-01T00:00:00Z'));
  assert.strictEqual(C.lastDailyReset(T('2026-12-31T23:59:59Z')), T('2026-12-31T00:00:00Z'));
  assert.strictEqual(C.lastDailyReset(T('2026-03-01T00:00:01Z')), T('2026-03-01T00:00:00Z'));
});

test('lastWeeklyReset: Thursday 00:00 UTC edges', () => {
  // 2026-10-01 is a Thursday.
  assert.strictEqual(new Date(T('2026-10-01T00:00:00Z')).getUTCDay(), 4);
  assert.strictEqual(C.lastWeeklyReset(T('2026-10-01T00:00:00Z')), T('2026-10-01T00:00:00Z'));
  assert.strictEqual(C.lastWeeklyReset(T('2026-09-30T23:59:59Z')), T('2026-09-24T00:00:00Z'));
  assert.strictEqual(C.lastWeeklyReset(T('2026-10-04T12:00:00Z')), T('2026-10-01T00:00:00Z'));
  assert.strictEqual(C.lastWeeklyReset(T('2026-10-07T23:59:59Z')), T('2026-10-01T00:00:00Z'));
  assert.strictEqual(C.lastWeeklyReset(T('2026-10-08T00:00:00Z')), T('2026-10-08T00:00:00Z'));
  // year rollover: 2027-01-01 is a Friday, last Thursday 2026-12-31
  assert.strictEqual(C.lastWeeklyReset(T('2027-01-02T05:00:00Z')), T('2026-12-31T00:00:00Z'));
  // last + 7d is always the next reset
  for (const s of ['2026-10-01T00:00:00Z', '2026-10-04T08:00:00Z', '2027-02-28T23:59:59Z']) {
    assert.strictEqual(C.lastWeeklyReset(T(s)) + 7 * DAY, C.nextWeeklyReset(T(s)));
    assert.strictEqual(C.lastDailyReset(T(s)) + DAY, C.nextDailyReset(T(s)));
  }
});

test('isDone: tick counts only since the last reset of its kind', () => {
  const now = T('2026-10-04T12:00:00Z'); // Sunday; daily reset 10-04, weekly 10-01
  assert.strictEqual(C.isDone('2026-10-04T00:00:00Z', 'daily', now), true);
  assert.strictEqual(C.isDone('2026-10-03T23:59:59Z', 'daily', now), false);
  assert.strictEqual(C.isDone('2026-10-03T23:59:59Z', 'event', now), false);
  assert.strictEqual(C.isDone('2026-10-04T01:00:00Z', 'event', now), true);
  assert.strictEqual(C.isDone('2026-10-02T10:00:00Z', 'weekly', now), true);
  assert.strictEqual(C.isDone('2026-10-01T00:00:00Z', 'weekly', now), true);
  assert.strictEqual(C.isDone('2026-09-30T23:59:59Z', 'weekly', now), false);
  assert.strictEqual(C.isDone('2026-10-04T00:00:00+00:00', 'daily', now), true);
  for (const bad of [null, undefined, '', 'yesterday', 12345]) {
    assert.strictEqual(C.isDone(bad, 'daily', now), false, String(bad));
  }
  assert.strictEqual(C.isDone('2026-10-04T01:00:00Z', 'monthly', now), false);
});

test('isDone flips across a reset without any new data', () => {
  const tick = '2026-10-04T22:00:00Z';
  assert.strictEqual(C.isDone(tick, 'daily', T('2026-10-04T23:59:59Z')), true);
  assert.strictEqual(C.isDone(tick, 'daily', T('2026-10-05T00:00:00Z')), false);
  const wk = '2026-10-07T22:00:00Z';
  assert.strictEqual(C.isDone(wk, 'weekly', T('2026-10-07T23:59:59Z')), true);
  assert.strictEqual(C.isDone(wk, 'weekly', T('2026-10-08T00:00:00Z')), false);
});

test('eventDaysLeft counts whole UTC days to the until date', () => {
  const now = T('2026-10-04T12:00:00Z');
  assert.strictEqual(C.eventDaysLeft('2026-10-04', now), 0);
  assert.strictEqual(C.eventDaysLeft('2026-10-05', now), 1);
  assert.strictEqual(C.eventDaysLeft('2026-11-04', now), 31);
  assert.strictEqual(C.eventDaysLeft('2026-10-03', now), -1);
  assert.strictEqual(C.eventDaysLeft(null, now), null);
  assert.strictEqual(C.eventDaysLeft('soon', now), null);
  assert.strictEqual(C.eventDaysLeft('2026-02-30', now), null);
  assert.strictEqual(C.fmtDaysLeft(0), 'ends today');
  assert.strictEqual(C.fmtDaysLeft(1), '1 day left');
  assert.strictEqual(C.fmtDaysLeft(5), '5 days left');
  assert.strictEqual(C.fmtDaysLeft(null), 'no end date');
  assert.strictEqual(C.fmtDaysLeft(-1), 'ended');
});

const ITEMS = [
  { id: 'attendance', title: 'Attendance reward', kind: 'daily', until: null, done: true, ticked_at: '2026-10-04T01:00:00Z' },
  { id: 'dice', title: "Black Spirit's Adventure dice", kind: 'daily', until: null, done: false, ticked_at: '2026-10-03T20:00:00Z' },
  { id: 'bs-weekly', title: 'Black Spirit weekly quests', kind: 'weekly', until: null, done: true, ticked_at: '2026-10-02T09:00:00Z' },
  { id: 'login-ev', title: 'Login event', kind: 'event', until: '2026-10-10', done: false, ticked_at: null },
  { id: 'old-ev', title: 'Old event', kind: 'event', until: '2026-10-01', done: false, ticked_at: null },
  null, { id: 'x', kind: 'bogus', title: 'junk' }
];

test('groupItems splits by kind, keeps order, derives done from ticked_at', () => {
  const g = C.groupItems(ITEMS, T('2026-10-04T12:00:00Z'));
  assert.deepStrictEqual(g.daily.items.map((i) => i.id), ['attendance', 'dice']);
  assert.deepStrictEqual(g.daily.items.map((i) => i.done), [true, false]);
  assert.strictEqual(g.daily.done, 1);
  assert.strictEqual(g.daily.total, 2);
  assert.strictEqual(g.weekly.done, 1);
  assert.strictEqual(g.weekly.total, 1);
  // expired events are dropped client-side too
  assert.deepStrictEqual(g.event.items.map((i) => i.id), ['login-ev']);
  assert.strictEqual(g.event.items[0].days_left, 6);
  assert.strictEqual(g.event.done, 0);
});

test('groupItems re-derives after the daily reset (no reload)', () => {
  const g = C.groupItems(ITEMS, T('2026-10-05T00:00:00Z'));
  assert.strictEqual(g.daily.done, 0);
  assert.strictEqual(g.weekly.done, 1); // weekly reset is Thursday
  const w = C.groupItems(ITEMS, T('2026-10-08T00:00:00Z'));
  assert.strictEqual(w.weekly.done, 0);
});

test('groupItems without now trusts the server done flag; junk input is safe', () => {
  const g = C.groupItems(ITEMS);
  assert.strictEqual(g.daily.done, 1);
  assert.deepStrictEqual(C.groupItems(null), {
    daily: { items: [], done: 0, total: 0 },
    weekly: { items: [], done: 0, total: 0 },
    event: { items: [], done: 0, total: 0 }
  });
});

test('withTick returns a copy with one item ticked/unticked (optimistic update)', () => {
  const data = { now: 'x', items: [{ id: 'a', ticked_at: null, done: false }, { id: 'b', ticked_at: 'old', done: true }] };
  const t = C.withTick(data, 'a', '2026-10-04T12:00:00Z');
  assert.strictEqual(t.items[0].ticked_at, '2026-10-04T12:00:00Z');
  assert.strictEqual(t.items[0].done, true);
  assert.strictEqual(data.items[0].ticked_at, null, 'input not mutated');
  const u = C.withTick(data, 'b', null);
  assert.strictEqual(u.items[1].ticked_at, null);
  assert.strictEqual(u.items[1].done, false);
  assert.strictEqual(u.items[0], data.items[0]);
  assert.strictEqual(C.withTick(null, 'a', null), null);
});

test('validTodayBody accepts exactly the slice A POST shapes', () => {
  const ok = [
    { tick: 'dice' }, { untick: 'black-spirit-weekly' }, { remove: 'a1' },
    { add: { title: 'Guild mission', kind: 'daily' } },
    { add: { title: 'Login event', kind: 'event', until: '2026-10-31' } },
    { add: { title: 'Boss', kind: 'weekly', until: null } },
    { move: { id: 'dice', to: 0 } }, { move: { id: 'dice', to: 3 } }
  ];
  for (const b of ok) assert.strictEqual(C.validTodayBody(b), true, JSON.stringify(b));
  const bad = [
    null, 'x', [], {}, { tick: 'dice', untick: 'dice' }, { tick: 'Dice' }, { tick: '' },
    { tick: 'a'.repeat(41) }, { tick: 5 }, { tick: 'a b' }, { bogus: 'dice' },
    { add: { title: '', kind: 'daily' } }, { add: { title: '   ', kind: 'daily' } },
    { add: { title: 'x', kind: 'monthly' } }, { add: { title: 'x' } },
    { add: { title: 'x', kind: 'event', until: '31/10/2026' } },
    { add: { title: 'x', kind: 'event', until: '2026-02-30' } },
    { add: { title: 'x', kind: 'daily', extra: 1 } }, { add: { title: 'x'.repeat(81), kind: 'daily' } },
    { add: { title: 'a\u0007b', kind: 'daily' } }, { add: 'x' },
    { move: { id: 'dice' } }, { move: { id: 'dice', to: -1 } }, { move: { id: 'dice', to: 1.5 } },
    { move: { id: 'dice', to: '1' } }, { move: { id: 'dice', to: 1, x: 1 } }
  ];
  for (const b of bad) assert.strictEqual(C.validTodayBody(b), false, JSON.stringify(b));
});

test('parseTodayForm builds an add body or an operator error', () => {
  assert.deepStrictEqual(C.parseTodayForm({ title: ' Guild mission ', kind: 'daily', until: '2026-10-31' }),
    { ok: true, body: { add: { title: 'Guild mission', kind: 'daily' } } });
  assert.deepStrictEqual(C.parseTodayForm({ title: 'Login event', kind: 'event', until: '2026-10-31' }),
    { ok: true, body: { add: { title: 'Login event', kind: 'event', until: '2026-10-31' } } });
  for (const bad of [
    { title: '', kind: 'daily' }, { title: 'x', kind: 'nope' },
    { title: 'x', kind: 'event', until: '' }, { title: 'x', kind: 'event', until: '2026-13-01' },
    { title: 'x'.repeat(81), kind: 'daily' }
  ]) {
    const r = C.parseTodayForm(bad);
    assert.strictEqual(r.ok, false, JSON.stringify(bad));
    assert.strictEqual(typeof r.error, 'string');
  }
  const r = C.parseTodayForm({ title: 'Event', kind: 'event', until: '2026-12-01' });
  assert.strictEqual(C.validTodayBody(r.body), true);
});

test('validPost: fixed route allowlist, per-route body validator', () => {
  for (const r of ['/api/market/watch', '/api/today']) assert.ok(C.POST_ROUTES.indexOf(r) >= 0, r);
  assert.strictEqual(C.validPost('/api/today', { tick: 'dice' }), true);
  assert.strictEqual(C.validPost('/api/market/watch', { add: { id: 1, sid: 0 } }), true);
  assert.strictEqual(C.validPost('/api/today', { add: { id: 1, sid: 0 } }), false);
  assert.strictEqual(C.validPost('/api/market/watch', { tick: 'dice' }), false);
  for (const r of ['/api/state', '/api/today/', '/api/today?x=1', 'http://evil/api/today', '', null, 5, 'toString', '__proto__']) {
    assert.strictEqual(C.validPost(r, { tick: 'dice' }), false, String(r));
  }
});

test('general bridge: one ew:post channel, dashboard-only, validated in main', () => {
  const pre = read('preload.js');
  assert.match(pre, /contextBridge\.exposeInMainWorld\('ewApi'/);
  assert.match(pre, /ipcRenderer\.invoke\('ew:post'/);
  const m = read('main.js');
  assert.match(m, /ipcMain\.handle\('ew:post'/);
  assert.match(m, /event\.sender !== dashboard\.webContents/);
  assert.match(m, /core\.validPost\(/);
  // Plan 022: the overlay gets only the one-way size preload, not the bridge.
  assert.equal((m.match(/preload:/g) || []).length, 2, 'dashboard + overlay size preload');
  const ov = m.slice(m.indexOf('function createOverlay'), m.indexOf('function toggleOverlay'));
  assert.match(ov, /preload: path\.join\(__dirname, 'overlay', 'preload\.js'\)/);
  assert.doesNotMatch(read('overlay/preload.js'), /ew:post|invoke|ewApi/);
});

test('today.js: safe DOM, POST via the bridge only, optimistic with revert', () => {
  const src = read('dashboard/today.js');
  assert.doesNotMatch(src, /innerHTML|outerHTML|insertAdjacentHTML|document\.write/);
  assert.match(src, /\/api\/today/);
  assert.match(src, /ewApi/);
  assert.match(src, /C\.withTick\(/);
  assert.match(src, /C\.groupItems\(/);
  assert.doesNotMatch(src, /method:\s*'POST'/, 'renderer never POSTs directly');
  assert.match(src, /window\.EWToday\s*=/);
});

test('dashboard loads and mounts today.js; CSP unchanged', () => {
  const html = read('dashboard/index.html');
  assert.match(html, /connect-src http:\/\/127\.0\.0\.1:8940;/);
  const iCore = html.indexOf('ewcore.js');
  const iToday = html.indexOf('<script src="today.js"></script>');
  const iDash = html.indexOf('dashboard.js');
  assert.ok(iCore >= 0 && iToday > iCore && iDash > iToday, 'script order ewcore, today, dashboard');
  assert.match(read('dashboard/dashboard.js'), /EWToday\.mount\(/);
});

test('overlay reads /api/today with GET only and shows daily n/m', () => {
  const src = read('overlay/overlay.js');
  assert.match(src, /\/api\/today/);
  assert.doesNotMatch(src, /POST|ewApi|ewMarket|ipcRenderer/);
  assert.match(src, /60000/);
  assert.match(read('overlay/index.html'), /id="ov-today"/);
});
