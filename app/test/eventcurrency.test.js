'use strict';
// Plan 095: event currency planner - pure formatters over the server's
// `currency` block and Today `event_rows`, the POST bodies, and static guards
// on the Events / Today tabs. No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const read = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');

const row = (o) => Object.assign({ event: '10673', currency: 'Seals of Purification', unit: 'seals',
  ends: '2026-11-05T07:00:00+00:00', earned: 140, earned_src: 'log', guaranteed: 970,
  wishlist_cost: 240, shortfall: 0, verified: false,
  wishlist: [{ item: 'Mythical Censer', qty: 2, cost: 80, limit: 2, subtotal: 160, use_before: null }],
  exchange: [{ item: 'Mythical Censer', cost: 80, limit: 2 }] }, o);

test('currencyText: covered / short / no wishlist / typed (mirrors server label)', () => {
  assert.strictEqual(C.currencyText(row()),
    'Seals: 140 earned, 970 guaranteed by 11-05, wishlist 240 - covered');
  assert.strictEqual(C.currencyText(row({ earned: 20, guaranteed: 150, shortfall: 90 })),
    'Seals: 20 earned, 150 guaranteed by 11-05, wishlist 240 - 90 short - from drops');
  assert.strictEqual(C.currencyText(row({ wishlist_cost: 0, earned: 100 })),
    'Seals: 100 earned, 970 guaranteed by 11-05');
  assert.strictEqual(C.currencyText(row({ wishlist_cost: 0, earned: 300, earned_src: 'typed', guaranteed: 1170 })),
    'Seals: 300 in hand (typed), 1170 guaranteed by 11-05');
});

test('currencyPill', () => {
  assert.deepStrictEqual(C.currencyPill(row()), { cls: 'ok', text: 'covered' });
  assert.deepStrictEqual(C.currencyPill(row({ shortfall: 90 })), { cls: 'warn', text: '90 short' });
  assert.deepStrictEqual(C.currencyPill(row({ wishlist_cost: 0 })), { cls: 'unknown', text: 'no wishlist' });
});

test('currencyRows: bad rows and bad wishlist / exchange rows dropped', () => {
  const rows = C.currencyRows({ events: [row(), row({ event: 10673 }), row({ earned: -1 }), null,
    row({ unit: '' }), row({ wishlist: [{ item: 'x', qty: 0, subtotal: 0 }], exchange: [{ item: 'y', cost: 0, limit: 1 }] })] });
  assert.strictEqual(rows.length, 2);
  assert.deepStrictEqual(rows[1].wishlist, []);
  assert.deepStrictEqual(rows[1].exchange, []);
  assert.deepStrictEqual(C.currencyRows(null), []);
});

test('currencyWishText: buy-by line', () => {
  assert.strictEqual(C.currencyWishText(row().wishlist[0]), 'Mythical Censer x2 (160)');
  assert.strictEqual(C.currencyWishText({ item: 'Buff A', qty: 1, subtotal: 10, use_before: 'use before 11-12' }),
    'Buff A x1 (10) - use before 11-12');
});

test('currencyWishBody: 0..limit, strings parsed', () => {
  const x = { item: 'Mythical Censer', cost: 80, limit: 2 };
  assert.deepStrictEqual(C.currencyWishBody('10673', x, '2'), { wish: { event: '10673', item: 'Mythical Censer', qty: 2 } });
  assert.deepStrictEqual(C.currencyWishBody('10673', x, 0), { wish: { event: '10673', item: 'Mythical Censer', qty: 0 } });
  assert.strictEqual(C.currencyWishBody('10673', x, '3'), null);
  assert.strictEqual(C.currencyWishBody('10673', x, '-1'), null);
  assert.strictEqual(C.currencyWishBody('10673', null, 1), null);
});

test('currencyBalanceBody: empty clears, junk refused', () => {
  assert.deepStrictEqual(C.currencyBalanceBody('10673', '300'), { currency_balance: { event: '10673', value: 300 } });
  assert.deepStrictEqual(C.currencyBalanceBody('10673', ''), { currency_balance: { event: '10673', value: null } });
  assert.strictEqual(C.currencyBalanceBody('10673', '1.5'), null);
  assert.strictEqual(C.currencyBalanceBody('10673', 'x'), null);
});

test('eventGameRows + eventTickBody', () => {
  const rows = C.eventGameRows({ event_rows: [
    { key: '10673:game1', event: '10673', source: 'game1', name: 'Weekly event game 1', kind: 'weekly',
      amount: 40, unit: 'seals', done: true, next_reset: '2026-10-22T00:00:00+00:00' },
    { key: '10673:quest1', name: 'Entry quest 1 (once per family)', kind: 'once', amount: 10, unit: 'seals',
      done: false, next_reset: null },
    { key: '10673:login', name: 'x', kind: 'daily_login', amount: 20, unit: 'seals' },
    { key: 'bad key', name: 'x', kind: 'once', amount: 1, unit: 'seals' }] });
  assert.deepStrictEqual(rows.map((r) => [r.key, r.done, r.text]), [
    ['10673:game1', true, '+40 seals / week'], ['10673:quest1', false, '+10 seals (once)']]);
  assert.strictEqual(rows[0].reset, Date.parse('2026-10-22T00:00:00Z'));
  assert.strictEqual(rows[1].reset, null);
  assert.deepStrictEqual(C.eventGameRows({}), []);
  assert.deepStrictEqual(C.eventTickBody('10673:game1', true), { event_tick: '10673:game1' });
  assert.deepStrictEqual(C.eventTickBody('10673:game1', false), { event_untick: '10673:game1' });
});

test('static: Events tab draws the currency card, Today ticks event rows', () => {
  const ev = read('dashboard/events.js');
  assert.ok(ev.includes("card('Event currency')") && ev.includes('C.currencyText('));
  assert.ok(ev.includes('C.currencyWishBody(') && ev.includes('C.currencyBalanceBody('));
  const td = read('dashboard/today.js');
  assert.ok(td.includes('C.eventGameRows(') && td.includes('C.eventTickBody('));
});
