'use strict';
// Plan 024: Olvia Academy deadline vs level ETA - pill formatter, line text,
// overlay alert, the deadline_set / deadline_del bridge shapes and static
// guards on the Leveling card, Events tab and overlay. No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const APP = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(APP, p), 'utf8');

const ROW = {
  id: 'olvia-class-3', label: 'Olvia', needs_level: 60, enrol_by_utc: '2026-11-05T00:00:00+00:00',
  state: 'on_track', reach_utc: '2026-10-29T08:00:00+00:00', margin_h: 160, verified: true
};
const row = (o) => Object.assign({}, ROW, o);

test('deadlinePill: one class per state', () => {
  assert.deepStrictEqual(C.deadlinePill(row({ state: 'done' })), { text: 'done', cls: 'ok' });
  assert.deepStrictEqual(C.deadlinePill(row({})), { text: 'on track', cls: 'ok' });
  assert.deepStrictEqual(C.deadlinePill(row({ state: 'tight' })), { text: 'tight', cls: 'warn' });
  assert.deepStrictEqual(C.deadlinePill(row({ state: 'late' })), { text: 'late', cls: 'bad' });
  assert.deepStrictEqual(C.deadlinePill(row({ state: 'unknown' })), { text: 'no rate', cls: 'unknown' });
  for (const bad of [null, {}, row({ state: 'bogus' }), row({ needs_level: 99 }), row({ id: 5 })]) {
    assert.deepStrictEqual(C.deadlinePill(bad), { text: '-', cls: 'unknown' });
  }
});

test('deadlineLine: plan wording per state', () => {
  assert.strictEqual(C.deadlineLine(row({})), 'Olvia: Lv 60 by Nov 5 - you reach 60 ~Oct 29 (on track)');
  assert.strictEqual(C.deadlineLine(row({ state: 'late', reach_utc: '2026-11-09T00:00:00Z' })),
    'Olvia: Lv 60 by Nov 5 - you reach 60 ~Nov 9 (late)');
  assert.strictEqual(C.deadlineLine(row({ state: 'late', reach_utc: null })), 'Olvia: Lv 60 by Nov 5 - enrolment closed (late)');
  assert.strictEqual(C.deadlineLine(row({ state: 'unknown', reach_utc: null })), 'Olvia: Lv 60 by Nov 5 - log XP for an ETA (no rate)');
  assert.strictEqual(C.deadlineLine(row({ state: 'done', reach_utc: null })), 'Olvia: Lv 60 by Nov 5 - Lv 60 reached (done)');
  assert.strictEqual(C.deadlineLine(row({ verified: false })), 'Olvia: Lv 60 by Nov 5 (verify date) - you reach 60 ~Oct 29 (on track)');
  assert.strictEqual(C.deadlineLine(null), '');
});

test('fmtMonthDay: UTC date, junk is ?', () => {
  assert.strictEqual(C.fmtMonthDay('2026-11-05T00:00:00+00:00'), 'Nov 5');
  assert.strictEqual(C.fmtMonthDay('2026-11-04T23:00:00-02:00'), 'Nov 5');
  assert.strictEqual(C.fmtMonthDay('x'), '?');
  assert.strictEqual(C.fmtMonthDay(null), '?');
});

test('deadlineAlert: only tight or late, late first', () => {
  assert.strictEqual(C.deadlineAlert([row({}), row({ state: 'done' }), row({ state: 'unknown' })]), null);
  assert.strictEqual(C.deadlineAlert(null), null);
  assert.deepStrictEqual(C.deadlineAlert([row({ state: 'tight' })]), { text: 'Olvia Lv 60 tight', cls: 'warn' });
  assert.deepStrictEqual(C.deadlineAlert([row({ state: 'tight' }), row({ id: 'b', label: 'B', needs_level: 61, state: 'late' })]),
    { text: 'B Lv 61 late', cls: 'bad' });
});

test('normalizeLeveling keeps valid deadline rows only', () => {
  const n = C.normalizeLeveling({ milestones: [], deadlines: [row({}), row({ state: 'x' }), 5] });
  assert.strictEqual(n.deadlines.length, 1);
  assert.strictEqual(n.deadlines[0].id, 'olvia-class-3');
  assert.deepStrictEqual(C.normalizeLeveling({ milestones: [] }).deadlines, []);
});

test('validLevelingBody: deadline_set / deadline_del shapes', () => {
  const D = {
    id: 'olvia-class-3', label: 'Olvia', needs_level: 60, enrol_by_utc: '2026-11-05T00:00:00Z',
    quests_by_utc: null, source: 'official notice', verified: true
  };
  const ok = [{ deadline_set: D }, { deadline_set: Object.assign({}, D, { quests_by_utc: '2026-11-11T00:00:00Z' }) },
    { deadline_set: Object.assign({}, D, { note: '' }) }, { deadline_set: Object.assign({}, D, { note: 'checked' }) },
    { deadline_del: 'olvia-class-3' }];
  for (const b of ok) assert.strictEqual(C.validLevelingBody(b), true, JSON.stringify(b));
  const bad = [{ deadline_del: 'Bad Id' }, { deadline_del: 5 }, { deadline_set: {} },
    { deadline_set: Object.assign({}, D, { needs_level: 0 }) }, { deadline_set: Object.assign({}, D, { needs_level: 76 }) },
    { deadline_set: Object.assign({}, D, { enrol_by_utc: '2026-11-05' }) },
    { deadline_set: Object.assign({}, D, { quests_by_utc: 'x' }) }, { deadline_set: Object.assign({}, D, { label: '' }) },
    { deadline_set: Object.assign({}, D, { verified: 1 }) }, { deadline_set: Object.assign({}, D, { x: 1 }) },
    { deadline_set: Object.assign({}, D, { note: 5 }) }];
  for (const b of bad) assert.strictEqual(C.validLevelingBody(b), false, JSON.stringify(b));
  const missing = Object.assign({}, D);
  delete missing.quests_by_utc;
  assert.strictEqual(C.validLevelingBody({ deadline_set: missing }), false);
});

test('static: card, events tab and overlay wire the deadline helpers', () => {
  assert.match(read('dashboard/leveling.js'), /C\.deadlineLine\(/);
  assert.match(read('dashboard/leveling.js'), /C\.deadlinePill\(/);
  assert.match(read('dashboard/events.js'), /S\.data\.deadlines/);
  assert.match(read('overlay/overlay.js'), /C\.deadlineAlert\(/);
  assert.match(read('overlay/index.html'), /id="ov-deadline"[^>]*hidden/);
});
