'use strict';
// Plan 044: Mounts card helpers (ewcore.js) over GET /api/mounts, the
// /api/mounts bridge guard, and static guards on the Progress module.
// No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const APP = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(APP, p), 'utf8');

function view(o) {
  return Object.assign({
    mounts: [{ id: 'snow', name: 'Snow', kind: 'horse', tier: 9, level: 27, gender: 'female', skills: ['Drift'] }],
    materials: [{ key: 'royal_fern_root', name: 'Royal Fern Root', have: 63, need: 100, done: false }],
    fern: { have: 63, need: 100, left: 37, per_day: 4, days: 10 },
    failures: 2,
    odds: { next_pct: 3.4, expected: 24.1, by: { 50: 19, 90: 46, 99: 70 }, guaranteed_in: 484 }
  }, o || {});
}

test('mountsFernLine: have/need fern roots and days at the typed rate', () => {
  assert.strictEqual(C.mountsFernLine(view()), 'materials 63/100 fern roots, ~10 days at 4/day');
  assert.strictEqual(C.mountsFernLine(view({ fern: { have: 99, need: 100, left: 1, per_day: 2.5, days: 1 } })),
    'materials 99/100 fern roots, ~1 day at 2.5/day');
  assert.strictEqual(C.mountsFernLine(view({ fern: { have: 63, need: 100, left: 37, per_day: null, days: null } })),
    'materials 63/100 fern roots, type a daily rate for days to go');
  assert.strictEqual(C.mountsFernLine(view({ fern: { have: 120, need: 100, left: 0, per_day: null, days: 0 } })),
    'materials 120/100 fern roots, done');
  assert.strictEqual(C.mountsFernLine(null), '');
  assert.strictEqual(C.mountsFernLine({ fern: null }), '');
});

test('mountsOddsLine: next chance, attempts to 50/90/99 percent, certain-by', () => {
  assert.strictEqual(C.mountsOddsLine(view()), 'next try 3.4% | 50% by 19, 90% by 46, 99% by 70 | sure by 484');
  assert.strictEqual(C.mountsOddsLine(view({ odds: { next_pct: 3, by: {}, guaranteed_in: null } })), 'next try 3%');
  assert.strictEqual(C.mountsOddsLine({ odds: null }), '');
});

test('mountLabel: name, kind, tier, level, gender', () => {
  assert.strictEqual(C.mountLabel(view().mounts[0]), 'Snow - horse T9 Lv 27 F');
  assert.strictEqual(C.mountLabel({ name: 'Hump', kind: 'camel', tier: null, level: 20, gender: null }), 'Hump - camel Lv 20');
  assert.strictEqual(C.mountLabel(null), '');
});

test('parseMountForm: strings -> {add}, blanks become defaults', () => {
  assert.deepStrictEqual(C.parseMountForm({ name: ' Snow ', kind: 'horse', tier: '9', level: '27', gender: 'female',
    skills: 'Drift, Instant Accel,, ' }),
  { ok: true, body: { add: { name: 'Snow', kind: 'horse', tier: 9, level: 27, gender: 'female', skills: ['Drift', 'Instant Accel'] } } });
  assert.deepStrictEqual(C.parseMountForm({ name: 'Hump', kind: 'camel', tier: '', level: '', gender: '', skills: '' }),
    { ok: true, body: { add: { name: 'Hump', kind: 'camel' } } });
  for (const bad of [{ name: '', kind: 'horse' }, { name: 'A', kind: 'dragon' }, { name: 'A', kind: 'horse', tier: '11' },
    { name: 'A', kind: 'horse', level: '31' }, { name: 'A', kind: 'horse', level: '2.5' }, { name: 'A', kind: 'horse', gender: 'x' }]) {
    assert.strictEqual(C.parseMountForm(bad).ok, false, JSON.stringify(bad));
  }
  // Every parsed body passes the bridge guard.
  assert.strictEqual(C.validPost('/api/mounts', C.parseMountForm({ name: 'A', kind: 'donkey', level: '3' }).body), true);
});

test('parseMaterialsForm and parseFernRate', () => {
  assert.deepStrictEqual(C.parseMaterialsForm({ royal_fern_root: '63', censer: '', mythical_feather: '10' }),
    { ok: true, body: { materials: { royal_fern_root: 63, mythical_feather: 10 } } });
  assert.strictEqual(C.parseMaterialsForm({ royal_fern_root: '-1' }).ok, false);
  assert.strictEqual(C.parseMaterialsForm({ royal_fern_root: '' }).ok, false);
  assert.deepStrictEqual(C.parseFernRate(' 2.5 '), { ok: true, body: { fern_rate: 2.5 } });
  assert.deepStrictEqual(C.parseFernRate(''), { ok: true, body: { fern_rate: null } });
  for (const bad of ['0', '101', 'abc', '-1', '1.234']) assert.strictEqual(C.parseFernRate(bad).ok, false, bad);
});

test('validPost: /api/mounts exact bodies only', () => {
  assert.ok(C.POST_ROUTES.indexOf('/api/mounts') >= 0);
  const ok = [
    { add: { name: 'Snow', kind: 'horse', tier: 9, level: 30, gender: 'male', skills: ['Drift'] } },
    { add: { name: 'Hump', kind: 'camel', tier: null, gender: null } },
    { edit: { id: 'snow', level: 30 } }, { delete: 'snow-2' },
    { materials: { royal_fern_root: 63 } }, { fern_rate: 2.5 }, { fern_rate: null },
    { failures: 0 }, { failures: 1000 }
  ];
  for (const b of ok) assert.strictEqual(C.validPost('/api/mounts', b), true, JSON.stringify(b));
  const bad = [
    {}, { add: { name: 'A' } }, { add: { name: 'A', kind: 'horse', colour: 'grey' } },
    { add: { name: 'A', kind: 'horse', level: 31 } }, { add: { name: 'A', kind: 'horse', tier: 0 } },
    { add: { name: 'A', kind: 'horse', skills: 'Drift' } }, { edit: { id: 'snow' } },
    { edit: { id: 'Bad Id', level: 3 } }, { delete: 3 }, { materials: {} },
    { materials: { royal_fern_root: -1 } }, { materials: { 'Bad Key': 1 } }, { fern_rate: 0 },
    { fern_rate: '2' }, { failures: 1001 }, { failures: 1.5 }, { failures: 1, fern_rate: 2 }, { tick: 'x' }
  ];
  for (const b of bad) assert.strictEqual(C.validPost('/api/mounts', b), false, JSON.stringify(b));
  assert.strictEqual(C.validPost('/api/mounts/', { failures: 1 }), false);
  assert.strictEqual(C.postToast('/api/mounts', { ok: true }).text, 'Mounts saved');
});

test('Progress module: Mounts card reads GET and posts /api/mounts through the bridge only', () => {
  const src = read('dashboard/progress.js');
  assert.ok(src.indexOf("getJSON('/api/mounts')") >= 0);
  assert.ok(src.indexOf("post('/api/mounts'") >= 0);
  assert.ok(src.indexOf('innerHTML') < 0);
  assert.ok(read('preload.js').indexOf('/api/mounts') >= 0);
  assert.ok(read('overlay/overlay.js').indexOf('/api/mounts') < 0);
});
