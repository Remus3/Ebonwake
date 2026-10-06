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

// ---- plan 048: quick-entry parsers + local time ----

test('parseSilver: exact quick entry (1.2b, 1,234,567, 850m, 12k)', () => {
  const ok = [['1.2b', 1200000000], ['1,234,567', 1234567], ['850m', 850000000], ['12k', 12000],
    ['1.234567891B', 1234567891], ['2.95m', 2950000], ['1 b', 1000000000], ['1,500m', 1500000000],
    ['0.5k', 500], ['10t', 10000000000000], ['007', 7]];
  for (const [s, v] of ok) assert.strictEqual(C.parseSilver(s), v, s);
  // Sub-silver precision is refused, never rounded away.
  for (const bad of ['1.0000000001b', '1.2345k', '1.5', '.5m', '5.m', '1,23m', '-1m', '1bb', '1e3', 'k',
    '99999999t', '1 2m']) {
    assert.strictEqual(C.parseSilver(bad), null, bad);
  }
});

test('parseDuration: minutes from 30d, 1h30m, 90m, 45', () => {
  const ok = [['30d', 43200], ['1h30m', 90], ['90m', 90], ['45', 45], ['1h', 60], ['1d12h', 2160],
    ['1.5h', 90], ['0.5d', 720], [' 2h 15m ', 135], ['90min', 90], ['1D', 1440], ['0', 0]];
  for (const [s, v] of ok) assert.strictEqual(C.parseDuration(s), v, s);
  for (const bad of ['', ' ', 'h', '1x', '1m30h', '1h1h', '-5', '1.5', '0.01h', '1.5m', '1:30', null, undefined,
    'd30', '12345678']) {
    assert.strictEqual(C.parseDuration(bad), null, String(bad));
  }
});

test('fmtDurationShort: compact minutes, round trips through parseDuration', () => {
  const t = [[43200, '30d'], [60, '1h'], [90, '1h30m'], [45, '45m'], [1500, '1d1h'], [1441, '1d1m'], [0, '0m']];
  for (const [m, s] of t) assert.strictEqual(C.fmtDurationShort(m), s, String(m));
  for (const m of [1, 59, 61, 1439, 1440, 10079, 43199, 43200]) {
    assert.strictEqual(C.parseDuration(C.fmtDurationShort(m)), m, String(m));
  }
  for (const bad of [-1, 1.5, '60', null, NaN]) assert.strictEqual(C.fmtDurationShort(bad), '-', String(bad));
});

// Same edge instants as tests/test_bosses.py (plan 031) so the two ports cannot drift.
test('ptOffsetHours: US DST edges match server/ew/bosses.py', () => {
  const T = (y, mo, d, h, mi, s) => Date.UTC(y, mo - 1, d, h, mi || 0, s || 0);
  const cases = [
    [T(2026, 11, 1, 8, 59, 59), -7], [T(2026, 11, 1, 9, 0, 0), -8],
    [T(2027, 3, 14, 9, 59, 59), -8], [T(2027, 3, 14, 10, 0, 0), -7],
    [T(2026, 7, 1, 12), -7], [T(2026, 1, 15, 12), -8]
  ];
  for (const [ms, off] of cases) assert.strictEqual(C.ptOffsetHours(ms), off, new Date(ms).toISOString());
});

test('ptToUtc: PT wall clock -> UTC across DST, same table as test_bosses.py', () => {
  const T = (y, mo, d, h) => Date.UTC(y, mo - 1, d, h);
  assert.strictEqual(C.ptToUtc(2026, 11, 1, 0, 0), T(2026, 11, 1, 7), 'Sun 00:00 still PDT');
  assert.strictEqual(C.ptToUtc(2026, 11, 1, 10, 0), T(2026, 11, 1, 18), 'Sun 10:00 now PST');
  assert.strictEqual(C.ptToUtc(2027, 3, 14, 0, 0), T(2027, 3, 14, 8), 'Sun 00:00 still PST');
  assert.strictEqual(C.ptToUtc(2027, 3, 14, 10, 0), T(2027, 3, 14, 17), 'Sun 10:00 now PDT');
  // Repeated 01:xx in November resolves to PDT (first occurrence), as in Python.
  assert.strictEqual(C.ptToUtc(2026, 11, 1, 1, 30), T(2026, 11, 1, 8) + 30 * 60000);
  for (const bad of [[2026, 13, 1, 0, 0], [2026, 2, 30, 0, 0], [2026, 1, 1, 24, 0], [2026, 1, 1, 0, 60]]) {
    assert.strictEqual(C.ptToUtc.apply(null, bad), null, bad.join(','));
  }
});

test('parseLocalTime: zone wall time -> UTC HH:MM plus day shift', () => {
  const pdt = Date.UTC(2026, 9, 5, 12);
  const pst = Date.UTC(2026, 11, 5, 12);
  assert.deepStrictEqual(C.parseLocalTime('21:00', { zone: 'pt', now: pdt }), { utc: '04:00', shift: 1 });
  assert.deepStrictEqual(C.parseLocalTime('21:00', { zone: 'pt', now: pst }), { utc: '05:00', shift: 1 });
  assert.deepStrictEqual(C.parseLocalTime('9:30', { zone: 'pt', now: pdt }), { utc: '16:30', shift: 0 });
  assert.deepStrictEqual(C.parseLocalTime('9pm', { zone: 'pt', now: pdt }), { utc: '04:00', shift: 1 });
  assert.deepStrictEqual(C.parseLocalTime('12:15 am', { zone: 'utc', now: pdt }), { utc: '00:15', shift: 0 });
  assert.deepStrictEqual(C.parseLocalTime('0900', { zone: 'utc', now: pdt }), { utc: '09:00', shift: 0 });
  assert.deepStrictEqual(C.parseLocalTime('23:59', { zone: 'utc', now: pdt }), { utc: '23:59', shift: 0 });
  // Host local zone: whatever this machine's offset is, the round trip holds.
  const off = -new Date(pdt).getTimezoneOffset();
  const loc = C.parseLocalTime('12:00', { now: pdt });
  const back = ((12 * 60 - off) % 1440 + 1440) % 1440;
  assert.strictEqual(loc.utc, String(Math.floor(back / 60)).padStart(2, '0') + ':' + String(back % 60).padStart(2, '0'));
  for (const bad of ['24:00', '9', '9:60', '13pm', '0am', 'abc', '', null, '21:00:00']) {
    assert.strictEqual(C.parseLocalTime(bad, { zone: 'utc', now: pdt }), null, String(bad));
  }
  assert.strictEqual(C.parseLocalTime('21:00', { zone: 'mars', now: pdt }), null);
});

test('fmtLocal: UTC ISO -> zone wall time with a UTC tooltip', () => {
  assert.deepStrictEqual(C.fmtLocal('2026-10-08T14:00:00Z', { zone: 'pt' }),
    { text: '2026-10-08 07:00', title: 'UTC 14:00' });
  assert.deepStrictEqual(C.fmtLocal('2026-10-08T02:30:00Z', { zone: 'pt' }),
    { text: '2026-10-07 19:30', title: 'UTC 2026-10-08 02:30' });
  assert.deepStrictEqual(C.fmtLocal('2026-12-08T14:00:00+00:00', { zone: 'pt' }),
    { text: '2026-12-08 06:00', title: 'UTC 14:00' });
  assert.deepStrictEqual(C.fmtLocal('2026-10-08T14:00:00Z', { zone: 'utc' }),
    { text: '2026-10-08 14:00', title: 'UTC 14:00' });
  const d = new Date(Date.UTC(2026, 9, 8, 14));
  const p = (n) => String(n).padStart(2, '0');
  assert.strictEqual(C.fmtLocal('2026-10-08T14:00:00Z').text,
    d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes()));
  for (const bad of ['', 'nope', null, 5, '2026-13-01T00:00:00Z']) assert.strictEqual(C.fmtLocal(bad), null, String(bad));
});

test('parseLocalDateTime round trips fmtLocal in every zone', () => {
  for (const zone of ['utc', 'pt', 'local']) {
    for (const iso of ['2026-10-08T14:00:00Z', '2026-11-01T18:00:00Z', '2027-03-14T17:00:00Z', '2026-12-31T23:59:00Z']) {
      const text = C.fmtLocal(iso, { zone: zone }).text;
      assert.strictEqual(C.parseLocalDateTime(text, { zone: zone }), iso, zone + ' ' + iso);
    }
  }
  assert.strictEqual(C.parseLocalDateTime('2026-11-01 10:00', { zone: 'pt' }), '2026-11-01T18:00:00Z');
  assert.strictEqual(C.parseLocalDateTime('2026-11-01T00:00', { zone: 'pt' }), '2026-11-01T07:00:00Z');
  for (const bad of ['2026-02-30 10:00', '2026-10-08 24:00', '2026-10-08', 'x', null]) {
    assert.strictEqual(C.parseLocalDateTime(bad, { zone: 'utc' }), null, String(bad));
  }
});

test('hotWindowView: UTC window shown in a zone, days shifted with the start', () => {
  const pdt = Date.UTC(2026, 9, 5, 12);
  // UTC Sat(5) 04:00-06:00 = PT Fri(4) 21:00-23:00.
  assert.deepStrictEqual(C.hotWindowView({ days: [5], start: '04:00', end: '06:00' }, { zone: 'pt', now: pdt }),
    { days: [4], start: '21:00', end: '23:00', title: 'UTC ' + C.fmtDays([5]) + ' 04:00-06:00' });
  const u = C.hotWindowView({ days: [0, 6], start: '10:00', end: '12:00' }, { zone: 'utc', now: pdt });
  assert.deepStrictEqual([u.days, u.start, u.end], [[0, 6], '10:00', '12:00']);
  assert.strictEqual(C.hotWindowView({ days: 'x' }, { zone: 'utc', now: pdt }), null);
});

test('parseHotForm zone: PT / local entry stored as UTC days + times', () => {
  const pdt = Date.UTC(2026, 9, 5, 12);
  const r = C.parseHotForm({ days: ['4'], start: '21:00', end: '23:00', label: 'Hot Time', pct: '50', zone: 'pt', now: pdt });
  assert.deepStrictEqual(r, { ok: true, body: { hot_add: { days: [5], start: '04:00', end: '06:00', label: 'Hot Time', pct: 50 } } });
  // Sunday 21:00 PT wraps to Monday (0) UTC.
  const w = C.parseHotForm({ days: [6], start: '9pm', end: '11pm', label: 'a', pct: '5', zone: 'pt', now: pdt });
  assert.deepStrictEqual(w.body.hot_add.days, [0]);
  assert.strictEqual(C.validLevelingBody(w.body), true);
  // The stored window reads back as what was typed.
  const v = C.hotWindowView(r.body.hot_add, { zone: 'pt', now: pdt });
  assert.deepStrictEqual([v.days, v.start, v.end], [[4], '21:00', '23:00']);
  assert.strictEqual(C.parseHotForm({ days: [1], start: '25:00', end: '1:00', label: 'a', pct: '5', zone: 'pt', now: pdt }).ok, false);
  assert.strictEqual(C.parseHotForm({ days: [1], start: '9:00', end: '10:00', label: 'a', pct: '5', zone: 'mars' }).ok, false);
  // The UTC choice takes the same quick-entry times as local / PT.
  assert.deepStrictEqual(C.parseHotForm({ days: [1], start: '9pm', end: '9:30', label: 'a', pct: '5', zone: 'utc', now: pdt }).body.hot_add,
    { days: [1], start: '21:00', end: '09:30', label: 'a', pct: 5 });
  assert.match(C.parseGrindForm('stop', { silver: '1', trash: 'x' }).error, /3\.2k/);
});

test('parseEpochForm zone: PT / local start stored as UTC', () => {
  const r = C.parseEpochForm({ id: '', start: '2026-11-01 10:00', label: 'Lv 75', source: 'notes', zone: 'pt' });
  assert.strictEqual(r.body.epoch_add.starts_utc, '2026-11-01T18:00:00Z');
  assert.strictEqual(C.validLevelingBody(r.body), true);
  const u = C.parseEpochForm({ id: 'x', start: '2026-10-08 14:00', label: 'L', source: 's', zone: 'utc' });
  assert.strictEqual(u.body.epoch_add.starts_utc, '2026-10-08T14:00:00Z');
  const text = C.fmtLocal('2026-10-08T14:00:00Z').text;
  assert.strictEqual(C.parseEpochForm({ id: 'x', start: text, label: 'L', source: 's', zone: 'local' }).body.epoch_add.starts_utc,
    '2026-10-08T14:00:00Z');
  assert.strictEqual(C.parseEpochForm({ id: 'x', start: '2026-02-30 10:00', label: 'L', source: 's', zone: 'pt' }).ok, false);
});

test('quick entry reaches the form parsers (grind, market, shop)', () => {
  assert.deepStrictEqual(C.parseGrindForm('stop', { silver: '1.2b', trash: '3,200' }),
    { ok: true, body: { stop: { silver: 1200000000, trash: 3200 } } });
  assert.deepStrictEqual(C.parseGrindForm('log', { spot: 'g', minutes: '1h30m', silver: '850m', trash: '' }),
    { ok: true, body: { log: { spot: 'g', minutes: 90, silver: 850000000, trash: 0 } } });
  assert.deepStrictEqual(C.parseGrindForm('buff', { name: 'Value Pack', minutes: '30d' }),
    { ok: true, body: { buff: { name: 'Value Pack', minutes: 43200 } } });
  assert.strictEqual(C.parseGrindForm('buff', { name: 'Value Pack', minutes: '31d' }).ok, false);
  assert.strictEqual(C.parseGrindForm('log', { spot: 'g', minutes: '1d1m', silver: '', trash: '' }).ok, false);
  assert.strictEqual(C.parseGrindForm('loot_item', { spot: 'g', name: 'Tag', marketable: false, vendor_price: '12k' })
    .body.loot_item.vendor_price, 12000);
  assert.deepStrictEqual(C.parseWatchForm({ id: '1', sid: '0', below: '1.2b', above: '1,500m' }, 'add'),
    { ok: true, body: { add: { id: 1, sid: 0, below: 1200000000, above: 1500000000 } } });
  assert.strictEqual(C.parseWatchForm({ id: '1', sid: '0', below: '1.5' }, 'add').ok, false);
  assert.deepStrictEqual(C.parseShopForm({ silver: '1.25b', hours: '2h30m' }),
    { ok: true, body: { shop_set: { silver_on_hand: 1250000000, hours_per_day: 2.5 } } });
});

test('buffRowView: short duration, unarmed off (muted), armed first', () => {
  const t0 = Date.UTC(2026, 9, 5, 12);
  const rows = C.buffRows([
    { id: 'vp', name: 'Value Pack', left_s: null },
    { id: 'xp', name: 'XP Scroll', ends: new Date(t0 + 600000).toISOString() }
  ], t0, t0);
  assert.strictEqual(rows[0].name, 'XP Scroll');
  const on = C.buffRowView(rows[0]);
  assert.deepStrictEqual([on.armed, on.muted, on.left], [true, false, C.fmtDuration(600000)]);
  const vp = C.buffRowView(rows.filter((r) => r.name === 'Value Pack')[0]);
  assert.deepStrictEqual([vp.left, vp.muted, vp.armed, vp.minutes], ['off', true, false, '30d']);
});
