'use strict';
// Plan 037: pure shopping-list helpers (ewcore.js: parseShopForm, fmtShopLine,
// fmtShopping, shopWatchBody, the shop_set / shop_step bridge guard) and
// static guards on the Deadeye tab's Shopping list card. No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const read = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');

test('fmtShopLine: numbers to display strings, missing -> "-"', () => {
  assert.deepStrictEqual(C.fmtShopLine({
    id: 16080, name: 'Cron Stone', qty: 6714, expected: 6713.1, unit: 3000000, total: 20142000000,
    preorder: 'capped', watched: false, note: null
  }), { name: 'Cron Stone', qty: '6.71K', unit: '3M', total: '20.1B', preorder: 'pre-order (capped)', note: '' });
  const m = C.fmtShopLine({ id: 200, name: null, qty: 12, unit: null, total: null, preorder: null, note: 'missing price' });
  assert.deepStrictEqual(m, { name: '#200', qty: '12', unit: '-', total: '-', preorder: '', note: 'missing price' });
  assert.strictEqual(C.fmtShopLine({ preorder: 'no_stock' }).preorder, 'pre-order (no stock)');
  assert.strictEqual(C.fmtShopLine(null).name, '-');
});

test('fmtShopping: total and afford line', () => {
  const ok = C.fmtShopping({
    total: 60008600, priced_total: 60008600, missing_prices: [], can_afford_by: '2026-09-23',
    afford: { need: 60000000, silver_per_h: 10000000, hours_per_day: 3, per_day: 30000000, days: 2, reason: null }
  });
  assert.deepStrictEqual(ok, { total: '60M', afford: 'can afford by 2026-09-23 (2 d at 10M/h x 3 h/day)' });
  const covered = C.fmtShopping({ total: 5, missing_prices: [], can_afford_by: '2026-09-21', afford: { days: 0, need: 0 } });
  assert.strictEqual(covered.afford, 'covered by silver on hand');
  const miss = C.fmtShopping({ total: null, priced_total: 8000, missing_prices: [200], can_afford_by: null,
    afford: { reason: 'missing price' } });
  assert.deepStrictEqual(miss, { total: '>= 8K (1 unpriced)', afford: 'can afford by: - (missing price)' });
  const zero = C.fmtShopping({ total: 10, missing_prices: [], can_afford_by: null, afford: { reason: 'no grind silver/h logged' } });
  assert.strictEqual(zero.afford, 'can afford by: - (no grind silver/h logged)');
  assert.deepStrictEqual(C.fmtShopping(null), { total: '-', afford: 'can afford by: -' });
});

test('parseShopForm: silver and hours strings -> shop_set body', () => {
  assert.deepStrictEqual(C.parseShopForm({ silver: '1,250,000,000', hours: '2.5' }),
    { ok: true, body: { shop_set: { silver_on_hand: 1250000000, hours_per_day: 2.5 } } });
  assert.deepStrictEqual(C.parseShopForm({ silver: '', hours: '4' }), { ok: true, body: { shop_set: { hours_per_day: 4 } } });
  assert.deepStrictEqual(C.parseShopForm({ silver: '0' }), { ok: true, body: { shop_set: { silver_on_hand: 0 } } });
  for (const bad of [{}, { silver: '-1' }, { silver: '1.5' }, { silver: '1e9' }, { silver: '99999999999999999' },
    { hours: '0' }, { hours: '25' }, { hours: 'abc' }, { hours: '-2' }]) {
    assert.strictEqual(C.parseShopForm(bad).ok, false, JSON.stringify(bad));
  }
  assert.strictEqual(C.parseShopForm(null).ok, false);
});

test('shopWatchBody: plan 002 watch add for the line id, sid 0', () => {
  const b = C.shopWatchBody(16080);
  assert.deepStrictEqual(b, { add: { id: 16080, sid: 0 } });
  assert.strictEqual(C.validPost('/api/market/watch', b), true);
  for (const bad of [0, -1, 1.5, '16080', null]) assert.strictEqual(C.shopWatchBody(bad), null);
});

test('validDeadeyeBody: shop_set / shop_step exact shapes', () => {
  const good = [
    { shop_set: { silver_on_hand: 0 } }, { shop_set: { hours_per_day: 1.5 } },
    { shop_set: { silver_on_hand: 5, hours_per_day: 24 } },
    { shop_step: { id: 'd1', fs: 0 } }, { shop_step: { id: 'd2', family: 'sovereign', fs: 999, crons: true } },
    { shop_step: { id: 'd3', family: null } }
  ];
  for (const b of good) assert.strictEqual(C.validDeadeyeBody(b), true, JSON.stringify(b));
  const bad = [
    { shop_set: {} }, { shop_set: { silver_on_hand: -1 } }, { shop_set: { silver_on_hand: 1.5 } },
    { shop_set: { silver_on_hand: 1e16 } }, { shop_set: { hours_per_day: 0 } }, { shop_set: { hours_per_day: 25 } },
    { shop_set: { hours_per_day: '3' } }, { shop_set: { x: 1 } }, { shop_set: 3 },
    { shop_step: { id: 'd1' } }, { shop_step: { id: 'x1', fs: 1 } }, { shop_step: { id: 'd1', fs: 1000 } },
    { shop_step: { id: 'd1', fs: -1 } }, { shop_step: { id: 'd1', crons: 1 } }, { shop_step: { id: 'd1', family: 'Bad' } },
    { shop_step: { id: 'd1', family: 3 } }, { shop_step: { id: 'd1', x: 1 } }
  ];
  for (const b of bad) assert.strictEqual(C.validDeadeyeBody(b), false, JSON.stringify(b));
  assert.strictEqual(C.validPost('/api/deadeye', { shop_step: { id: 'd1', crons: false } }), true);
});

test('deadeye.js shopping card: GETs the shopping route, watch via existing POST, no new HTML sink', () => {
  const src = read('dashboard/deadeye.js');
  assert.match(src, /\/api\/deadeye\/shopping/);
  assert.match(src, /'Shopping list'/);
  assert.match(src, /post\('\/api\/market\/watch'/);
  for (const f of ['fmtShopLine', 'fmtShopping', 'parseShopForm', 'shopWatchBody']) {
    assert.match(src, new RegExp('C\\.' + f + '\\('), f);
  }
  assert.match(src, /shop_step:/);
  assert.strictEqual((src.match(/innerHTML/g) || []).length, 1);
  // never a market order: no buy / sell / register route anywhere in the card
  assert.doesNotMatch(src, /\/(buy|sell|register)/i);
});
