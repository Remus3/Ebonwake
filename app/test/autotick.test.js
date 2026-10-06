'use strict';
// Plan 068: Today auto marks (auto / ready / undo), boss-shot suggestion helper
// and static guards on today.js / bosses.js. No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const APP = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(APP, p), 'utf8');

test('todayAutoMark: only a done row ticked by auto', () => {
  assert.strictEqual(C.todayAutoMark({ id: 'a', done: true, by: 'auto' }), true);
  assert.strictEqual(C.todayAutoMark({ id: 'a', done: false, by: 'auto' }), false);
  assert.strictEqual(C.todayAutoMark({ id: 'a', done: true, by: null }), false);
  assert.strictEqual(C.todayAutoMark({ id: 'a', done: true }), false);
  assert.strictEqual(C.todayAutoMark(null), false);
});

test('todayReady: ready and still open', () => {
  assert.strictEqual(C.todayReady({ ready: true, done: false }), true);
  assert.strictEqual(C.todayReady({ ready: true, done: true }), false);
  assert.strictEqual(C.todayReady({ ready: false, done: false }), false);
  assert.strictEqual(C.todayReady({ ready: 'yes' }), false);
  assert.strictEqual(C.todayReady(undefined), false);
});

test('todayAutoTitle: one line per rule kind, junk -> empty', () => {
  assert.match(C.todayAutoTitle({ auto: 'login' }), /first login after reset/);
  assert.match(C.todayAutoTitle({ auto: 'logged_minutes:30' }), /auto-ticks after 30 /);
  assert.match(C.todayAutoTitle({ auto: 'ready_minutes:60' }), /ready after 60 /);
  assert.strictEqual(C.todayAutoTitle({ auto: 'boom' }), '');
  assert.strictEqual(C.todayAutoTitle({}), '');
});

test('withTick: an optimistic toggle clears by:auto, rows without by keep their shape', () => {
  const data = { items: [{ id: 'a', ticked_at: '2026-10-04T00:01:00Z', done: true, by: 'auto' },
    { id: 'b', ticked_at: null, done: false }] };
  const u = C.withTick(data, 'a', null);
  assert.deepStrictEqual(u.items[0], { id: 'a', ticked_at: null, done: false, by: null });
  const t = C.withTick(data, 'b', '2026-10-04T12:00:00Z');
  assert.deepStrictEqual(Object.keys(t.items[1]).sort(), ['done', 'id', 'ticked_at']);
  assert.strictEqual(data.items[0].by, 'auto'); // input untouched
});

test('bossSuggested: suggested and not looted only', () => {
  const v = { suggested: { '2026-10-06': ['Kzarka', 'Nouver'] }, looted: { '2026-10-06': ['Nouver'] } };
  assert.strictEqual(C.bossSuggested(v, '2026-10-06', 'Kzarka'), true);
  assert.strictEqual(C.bossSuggested(v, '2026-10-06', 'Nouver'), false);
  assert.strictEqual(C.bossSuggested(v, '2026-10-05', 'Kzarka'), false);
  assert.strictEqual(C.bossSuggested({ looted: {} }, '2026-10-06', 'Kzarka'), false);
  assert.strictEqual(C.bossSuggested(null, '2026-10-06', 'Kzarka'), false);
});

test('settings: checklist.auto is a bool field in the Checklist group', () => {
  const g = C.SETTINGS_GROUPS.filter((x) => x.id === 'checklist')[0];
  assert.ok(g);
  assert.deepStrictEqual(g.fields.map((f) => [f.key, f.type]), [['checklist.auto', 'bool']]);
});

test('today.js: auto pill + undo via the normal untick; bosses.js: one-click suggestion', () => {
  const t = read('dashboard/today.js');
  assert.match(t, /C\.todayAutoMark\(/);
  assert.match(t, /C\.todayReady\(/);
  assert.match(t, /'undo'/);
  const b = read('dashboard/bosses.js');
  assert.match(b, /C\.bossSuggested\(/);
  assert.match(b, /probably done - tick\?/);
  for (const src of [t, b]) assert.doesNotMatch(src, /innerHTML/);
});
