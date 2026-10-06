'use strict';
// Plan 055: pure calculator helpers (ewcore.js: calcQuery, fmtCalc) and static
// guards on the Deadeye tab's Calculators card. No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const read = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');

test('calcQuery crystal: strings -> GET path, per_level optional', () => {
  assert.deepStrictEqual(C.calcQuery('crystal', { on_hand: '1,200', levels: '3' }),
    { ok: true, path: '/api/deadeye/calc?kind=crystal&on_hand=1200&levels=3' });
  assert.deepStrictEqual(C.calcQuery('crystal', { on_hand: '', levels: '1', per_level: '60' }),
    { ok: true, path: '/api/deadeye/calc?kind=crystal&on_hand=0&levels=1&per_level=60' });
  assert.strictEqual(C.calcQuery('crystal', { levels: '0' }).ok, false);
  assert.strictEqual(C.calcQuery('crystal', { levels: '21' }).ok, false);
  assert.strictEqual(C.calcQuery('crystal', { on_hand: '-1', levels: '2' }).ok, false);
  assert.strictEqual(C.calcQuery('crystal', { levels: '2', per_level: 'x' }).ok, false);
});

test('calcQuery caphras: slot, range, grade and quick-entry price', () => {
  assert.deepStrictEqual(C.calcQuery('caphras', { slot: 'boss_main_pen', from: '0', to: '10', price: '2.1m' }),
    { ok: true, path: '/api/deadeye/calc?kind=caphras&slot=boss_main_pen&from=0&to=10&price=2100000' });
  assert.deepStrictEqual(C.calcQuery('caphras', { slot: 'boss_main_pen', to: '20', grade: 'blackstar' }),
    { ok: true, path: '/api/deadeye/calc?kind=caphras&slot=boss_main_pen&from=0&to=20&grade=blackstar' });
  assert.strictEqual(C.calcQuery('caphras', { slot: 'boss_main_pen', from: '10', to: '5' }).ok, false);
  assert.strictEqual(C.calcQuery('caphras', { slot: 'boss_main_pen', to: '21' }).ok, false);
  assert.strictEqual(C.calcQuery('caphras', { slot: 'x&y=1', to: '5' }).ok, false);
  assert.strictEqual(C.calcQuery('caphras', { slot: 'boss_main_pen', to: '5', price: 'lots' }).ok, false);
  assert.strictEqual(C.calcQuery('nope', {}).ok, false);
});

test('fmtCalc: crystal band, covered and exact', () => {
  const band = C.fmtCalc({ kind: 'crystal', needed: { min: 180, max: 360 }, short: { min: 80, max: 260 },
    weeks: { min: 1, max: 2 }, exact: false, weekly: 155, auras_per_week: 2, reset: 'Thursday' });
  assert.strictEqual(band.main, '1-2 wk of Jetina exchanges');
  assert.strictEqual(band.sub, 'need 180-360, short 80-260; 155/wk, 2 auras, resets Thursday (60-120/level band)');
  const cov = C.fmtCalc({ kind: 'crystal', weeks: { min: 0, max: 0 }, exact: true });
  assert.strictEqual(cov.main, 'covered by crystals on hand');
  assert.deepStrictEqual(C.fmtCalc(null), { main: '-', sub: '' });
});

test('fmtCalc: caphras stones, silver and flags', () => {
  const r = C.fmtCalc({ kind: 'caphras', from: 0, to: 10, stones: 8895, silver: 17790000000,
    approx: false, verified: true, price_source: 'cache' });
  assert.strictEqual(r.main, '8,895 stones, 17.8B silver');
  assert.strictEqual(r.sub, 'C0 -> C10 (cached price)');
  const a = C.fmtCalc({ kind: 'caphras', from: 5, to: 15, stones: 19149, silver: null,
    approx: true, verified: false, price_source: null });
  assert.strictEqual(a.main, '~19,149 stones, no price (watch the stone or type one)');
  assert.strictEqual(a.sub, 'C5 -> C15 (prorated inside a range, unverified data)');
});

test('Deadeye tab mounts a Calculators card on the calc GET only', () => {
  const src = read('dashboard/deadeye.js');
  assert.match(src, /card\('Calculators'/);
  assert.match(src, /C\.calcQuery\(/);
  assert.match(src, /\/api\/deadeye\/calc/);
  assert.doesNotMatch(src, /post\('\/api\/deadeye\/calc/);
});
