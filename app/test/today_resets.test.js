'use strict';
// Plan 021: per-item reset rules in ewcore.js (same rule as server today.py)
// and static guards on the Today tab. No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const T = (s) => Date.parse(s);
const DAY = 86400000;
const SUNDAY = { every: 'week', weekday: 6, at: '00:00' };
const FIVE_AM = { every: 'day', at: '05:00' };

test('lastResetOf: Sunday weekly across Saturday 23:59 -> Sunday 00:00', () => {
  assert.strictEqual(new Date(T('2026-10-04T00:00:00Z')).getUTCDay(), 0); // Sunday
  assert.strictEqual(C.lastResetOf(SUNDAY, T('2026-10-03T23:59:59Z')), T('2026-09-27T00:00:00Z'));
  assert.strictEqual(C.lastResetOf(SUNDAY, T('2026-10-04T00:00:00Z')), T('2026-10-04T00:00:00Z'));
  assert.strictEqual(C.lastResetOf(SUNDAY, T('2026-10-08T12:00:00Z')), T('2026-10-04T00:00:00Z'));
  assert.strictEqual(C.lastResetOf(SUNDAY, T('2027-01-02T10:00:00Z')), T('2026-12-27T00:00:00Z'));
  assert.strictEqual(C.nextResetOf(SUNDAY, T('2026-10-04T00:00:00Z')), T('2026-10-11T00:00:00Z'));
});

test('lastResetOf: 05:00 daily across 04:59 -> 05:00', () => {
  assert.strictEqual(C.lastResetOf(FIVE_AM, T('2026-10-04T04:59:59Z')), T('2026-10-03T05:00:00Z'));
  assert.strictEqual(C.lastResetOf(FIVE_AM, T('2026-10-04T05:00:00Z')), T('2026-10-04T05:00:00Z'));
  assert.strictEqual(C.lastResetOf(FIVE_AM, T('2026-11-01T00:30:00Z')), T('2026-10-31T05:00:00Z'));
  assert.strictEqual(C.nextResetOf(FIVE_AM, T('2026-10-04T04:59:59Z')), T('2026-10-04T05:00:00Z'));
  const wed = { every: 'week', weekday: 2, at: '10:30' };
  assert.strictEqual(C.lastResetOf(wed, T('2026-10-07T10:29:00Z')), T('2026-09-30T10:30:00Z'));
  assert.strictEqual(C.lastResetOf(wed, T('2026-10-07T10:30:00Z')), T('2026-10-07T10:30:00Z'));
});

test('default rules reproduce the plan 003 clocks', () => {
  for (const s of ['2026-10-01T00:00:00Z', '2026-09-30T23:59:59Z', '2027-01-02T05:00:00Z']) {
    assert.strictEqual(C.lastResetOf({ every: 'day', at: '00:00' }, T(s)), C.lastDailyReset(T(s)));
    assert.strictEqual(C.lastResetOf({ every: 'week', weekday: 3, at: '00:00' }, T(s)), C.lastWeeklyReset(T(s)));
  }
});

test('validResetRule / itemRule mirror server validation', () => {
  assert.deepStrictEqual(C.validResetRule({ every: 'day' }), { every: 'day', at: '00:00' });
  assert.deepStrictEqual(C.validResetRule(SUNDAY), SUNDAY);
  for (const bad of [null, 'day', [], { every: 'month' }, { every: 'week', at: '00:00' },
    { every: 'week', weekday: 7 }, { every: 'week', weekday: -1 }, { every: 'week', weekday: 1.5 },
    { every: 'day', weekday: 1 }, { every: 'day', at: '24:00' }, { every: 'day', at: '5:00' },
    { every: 'day', at: '05:60' }, { every: 'day', at: '05:00', tz: 'x' }]) {
    assert.strictEqual(C.validResetRule(bad), null, JSON.stringify(bad));
  }
  assert.deepStrictEqual(C.itemRule('weekly', SUNDAY), SUNDAY);
  assert.strictEqual(C.itemRule('daily', SUNDAY), null);
  assert.strictEqual(C.itemRule('weekly', FIVE_AM), null);
  assert.strictEqual(C.itemRule('event', FIVE_AM), null);
});

test('fmtResetRule and fmtResetCountdown', () => {
  assert.strictEqual(C.fmtResetRule(SUNDAY), 'Sun 00:00 UTC');
  assert.strictEqual(C.fmtResetRule({ every: 'week', weekday: 0, at: '18:15' }), 'Mon 18:15 UTC');
  assert.strictEqual(C.fmtResetRule(FIVE_AM), '05:00 UTC');
  assert.strictEqual(C.fmtResetRule({ every: 'nope' }), '');
  assert.strictEqual(C.fmtResetCountdown(SUNDAY, T('2026-10-01T20:00:00Z')), 'resets Sun 00:00 UTC in 2d 4h');
  assert.strictEqual(C.fmtResetCountdown(FIVE_AM, T('2026-10-04T04:30:00Z')), 'resets 05:00 UTC in 30m 00s');
  assert.strictEqual(C.fmtResetCountdown(null, 0), '');
});

test('isDone / groupItems honour an item rule; legacy items unchanged', () => {
  const items = [
    { id: 'shrine', kind: 'weekly', reset: SUNDAY, ticked_at: '2026-10-03T12:00:00Z' },
    { id: 'boss', kind: 'weekly', ticked_at: '2026-10-03T12:00:00Z' },
    { id: 'dice', kind: 'daily', reset: FIVE_AM, ticked_at: '2026-10-04T01:00:00Z' },
    { id: 'barter', kind: 'daily', ticked_at: '2026-10-04T01:00:00Z' },
    { id: 'bad', kind: 'daily', reset: SUNDAY, ticked_at: '2026-10-04T01:00:00Z' }
  ];
  let g = C.groupItems(items, T('2026-10-04T04:59:59Z'));
  assert.deepStrictEqual(g.weekly.items.map((i) => i.done), [false, true]);
  assert.deepStrictEqual(g.daily.items.map((i) => i.done), [true, true, true]);
  assert.deepStrictEqual(g.weekly.items[0].reset, SUNDAY);
  assert.strictEqual(g.daily.items[2].reset, null, 'mismatched rule ignored');
  g = C.groupItems(items, T('2026-10-04T05:00:00Z'));
  assert.deepStrictEqual(g.daily.items.map((i) => i.done), [false, true, true]);
  assert.strictEqual(C.isDone('2026-10-03T23:59:59Z', 'weekly', T('2026-10-04T00:00:00Z'), SUNDAY), false);
  assert.strictEqual(C.isDone('2026-10-04T00:00:00Z', 'weekly', T('2026-10-04T00:00:01Z'), SUNDAY), true);
});

test('resetKey changes when a custom item resets, not before', () => {
  const items = [{ id: 'dice', kind: 'daily', reset: FIVE_AM }];
  assert.strictEqual(C.resetKey(items, T('2026-10-04T01:00:00Z')), C.resetKey(items, T('2026-10-04T04:59:59Z')));
  assert.notStrictEqual(C.resetKey(items, T('2026-10-04T04:59:59Z')), C.resetKey(items, T('2026-10-04T05:00:00Z')));
  assert.strictEqual(C.resetKey([], T('2026-10-04T04:59:59Z')), C.resetKey([], T('2026-10-04T05:00:00Z')));
  assert.strictEqual(C.resetKey(null, 0), C.resetKey([], 0));
  assert.ok(C.resetKey([], T('2026-10-04T05:00:00Z')).split(':').length === 2);
  assert.ok(DAY > 0);
});

test('resetPresets labels unverified rows and drops junk; presetForm fills the form', () => {
  const p = C.resetPresets({ reset_presets: [
    { name: 'Black Shrine (5/week)', kind: 'weekly', reset: SUNDAY, verified: true, source: 's' },
    { name: "Black Spirit's Adventure dice", kind: 'daily', reset: FIVE_AM, verified: false, source: 's' },
    { name: 'junk', kind: 'daily', reset: SUNDAY }, null, { name: '', kind: 'daily', reset: FIVE_AM }
  ] });
  assert.deepStrictEqual(p.map((x) => x.label), ['Black Shrine (5/week) - Sun 00:00 UTC',
    "Black Spirit's Adventure dice - 05:00 UTC (verify)"]);
  assert.deepStrictEqual(C.resetPresets(null), []);
  const f = C.presetForm(p[0]);
  assert.deepStrictEqual(f, { title: 'Black Shrine (5/week)', kind: 'weekly', until: '', reset_weekday: '6', reset_at: '00:00' });
  const r = C.parseTodayForm(f);
  assert.deepStrictEqual(r, { ok: true, body: { add: { title: 'Black Shrine (5/week)', kind: 'weekly', reset: SUNDAY } } });
  assert.strictEqual(C.validTodayBody(r.body), true);
  const d = C.parseTodayForm(C.presetForm(p[1]));
  assert.deepStrictEqual(d.body.add.reset, FIVE_AM);
});

test('parseTodayForm custom reset row', () => {
  assert.deepStrictEqual(C.parseTodayForm({ title: 'X', kind: 'daily', reset_weekday: '', reset_at: '' }),
    { ok: true, body: { add: { title: 'X', kind: 'daily' } } });
  assert.deepStrictEqual(C.parseTodayForm({ title: 'X', kind: 'weekly', reset_at: '12:00' }).body.add.reset,
    { every: 'week', weekday: 3, at: '12:00' });
  assert.deepStrictEqual(C.parseTodayForm({ title: 'X', kind: 'weekly', reset_weekday: '6' }).body.add.reset, SUNDAY);
  for (const bad of [
    { title: 'X', kind: 'event', until: '2026-12-01', reset_at: '05:00' },
    { title: 'X', kind: 'daily', reset_weekday: '6' },
    { title: 'X', kind: 'daily', reset_at: '25:00' },
    { title: 'X', kind: 'weekly', reset_weekday: '9' },
    { title: 'X', kind: 'weekly', reset_weekday: 'x' }
  ]) {
    const r = C.parseTodayForm(bad);
    assert.strictEqual(r.ok, false, JSON.stringify(bad));
    assert.strictEqual(typeof r.error, 'string');
  }
});

test('validTodayBody: add with reset', () => {
  assert.strictEqual(C.validTodayBody({ add: { title: 'x', kind: 'weekly', reset: SUNDAY } }), true);
  assert.strictEqual(C.validTodayBody({ add: { title: 'x', kind: 'daily', reset: FIVE_AM } }), true);
  assert.strictEqual(C.validTodayBody({ add: { title: 'x', kind: 'daily', reset: null } }), true);
  assert.strictEqual(C.validTodayBody({ add: { title: 'x', kind: 'daily', reset: SUNDAY } }), false);
  assert.strictEqual(C.validTodayBody({ add: { title: 'x', kind: 'event', until: '2026-12-01', reset: FIVE_AM } }), false);
  assert.strictEqual(C.validTodayBody({ add: { title: 'x', kind: 'weekly', reset: { every: 'week', weekday: 7 } } }), false);
});

test('Today tab renders item countdowns and the custom reset row', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'dashboard', 'today.js'), 'utf8');
  assert.match(src, /C\.fmtResetCountdown\(/);
  assert.match(src, /C\.resetKey\(/);
  assert.match(src, /C\.resetPresets\(/);
  assert.match(src, /reset_weekday/);
  assert.match(src, /reset_at/);
  assert.doesNotMatch(src, /innerHTML/);
});
