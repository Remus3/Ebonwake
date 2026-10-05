'use strict';
// Plan 033: weekly content planner formatters in ewcore.js and static guards
// on the Today tab. No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const NOW = Date.parse('2026-10-05T12:00:00Z');

function row(id, state, extra) {
  return Object.assign({ id: id, name: id.toUpperCase(), per_week: 1, done: 0,
    min_level: 60, ap: 250, dp: 300, next_reset: '2026-10-08T00:00:00+00:00',
    note: 'n', source: 'https://example.invalid/', verified: '2026-07-02',
    gate: { state: state, needs: {}, unknown: [], bracket: null } }, extra || {});
}

test('fmtWeeklyGate: gaps with bracket hint', () => {
  const r = row('atx', 'locked', { gate: { state: 'locked', needs: { level: 2, ap: 10 }, unknown: ['dp'],
    bracket: { ap: 240, to_next: 5, next_gain: 8 } } });
  assert.strictEqual(C.fmtWeeklyGate(r),
    'needs Lv +2, +10 AP (+5 AP crosses a bracket: +8 bonus AP); set DP on Progress');
});

test('fmtWeeklyGate: unsourced vs unset unknowns, eligible empty', () => {
  const r = row('j', 'unknown', { ap: null, dp: null,
    gate: { state: 'unknown', needs: {}, unknown: ['level', 'ap', 'dp'], bracket: null } });
  assert.strictEqual(C.fmtWeeklyGate(r), 'set level on Progress; AP/DP gate not sourced');
  assert.strictEqual(C.fmtWeeklyGate(row('bs', 'eligible')), '');
  assert.strictEqual(C.fmtWeeklyGate(null), '');
  assert.strictEqual(C.fmtWeeklyGate({ gate: 'x' }), '');
});

test('fmtWeeklyGate: bracket hint without a known gain', () => {
  const r = row('e', 'locked', { gate: { state: 'locked', needs: { ap: 40 }, unknown: [],
    bracket: { ap: 310, to_next: 6, next_gain: null } } });
  assert.strictEqual(C.fmtWeeklyGate(r), 'needs +40 AP (+6 AP crosses a bracket)');
});

test('weeklyPlan: eligible first, then unknown, then locked; data order kept', () => {
  const d = { weekly_plan: { error: null, rows: [
    row('a', 'locked'), row('b', 'unknown'), row('c', 'eligible', { per_week: 5, done: 2 }),
    row('d', 'locked'), row('e', 'eligible')] } };
  const p = C.weeklyPlan(d, NOW);
  assert.deepStrictEqual(p.rows.map((r) => r.id), ['c', 'e', 'b', 'a', 'd']);
  assert.strictEqual(p.eligible, 2);
  assert.strictEqual(p.total, 5);
  assert.strictEqual(p.rows[0].ticks, '2/5');
  assert.strictEqual(p.rows[0].full, false);
  assert.ok(p.rows[0].title.indexOf('verified 2026-07-02') >= 0);
});

test('weeklyPlan: cap, passed reset zeroes the count, junk dropped', () => {
  const d = { weekly_plan: { rows: [
    row('full', 'eligible', { per_week: 3, done: 9 }),
    row('old', 'eligible', { done: 1, next_reset: '2026-10-05T00:00:00+00:00' }),
    null, { id: 5 }, row('nopw', 'eligible', { per_week: 0 }),
    row('weird', 'toString', { gate: { state: 'toString', needs: {}, unknown: ['constructor'] } })] } };
  const p = C.weeklyPlan(d, NOW);
  const by = {};
  p.rows.forEach((r) => { by[r.id] = r; });
  assert.deepStrictEqual(Object.keys(by).sort(), ['full', 'old', 'weird']);
  assert.strictEqual(by.full.ticks, '3/3');
  assert.strictEqual(by.full.full, true);
  assert.strictEqual(by.old.done, 0);
  assert.strictEqual(by.weird.state, 'unknown');
  assert.strictEqual(by.weird.gap, '');
});

test('weeklyPlan: missing plan and server error', () => {
  assert.deepStrictEqual(C.weeklyPlan(null, NOW), { rows: [], eligible: 0, total: 0, error: null });
  assert.strictEqual(C.weeklyPlan({ weekly_plan: { rows: [], error: 'bad file' } }, NOW).error, 'bad file');
});

test('validTodayBody: weekly_tick / weekly_untick take a slug id', () => {
  assert.strictEqual(C.validTodayBody({ weekly_tick: 'black-shrine' }), true);
  assert.strictEqual(C.validTodayBody({ weekly_untick: 'garmoth' }), true);
  assert.strictEqual(C.validTodayBody({ weekly_tick: 'Bad Id' }), false);
  assert.strictEqual(C.validTodayBody({ weekly_tick: 5 }), false);
  assert.strictEqual(C.validTodayBody({ weekly_tick: 'a', tick: 'b' }), false);
});

test('Today tab: This week card, writes only through the bridge, no innerHTML', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'dashboard', 'today.js'), 'utf8');
  assert.ok(src.indexOf("listCard('This week'") >= 0);
  assert.ok(src.indexOf('C.weeklyPlan(') >= 0);
  assert.ok(src.indexOf('weekly_tick') >= 0 && src.indexOf('weekly_untick') >= 0);
  assert.ok(!/innerHTML/.test(src));
});
