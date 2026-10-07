'use strict';
// Plan 029: overlay market ticker - pure helpers (ewcore.js) and static guards
// on the overlay (opt-in widget, GET /api/market/watch only, no POST).
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const APP = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(APP, p), 'utf8');

const NOW = Date.parse('2026-10-05T12:00:00Z');
const fresh = (ageS) => ({
  fetched_at: new Date(NOW - ageS * 1000).toISOString(), age_s: ageS, ttl_s: 300, stale: false, error: null,
});
const item = (id, price, extra) => Object.assign({
  id: id, sid: 0, name: 'Item ' + id, price: price, below: null, above: null, alert: null,
  net: null, preorder: null, freshness: fresh(10),
}, extra || {});

test('tickerRows: alert hits first, then operator order', () => {
  const rows = C.tickerRows([
    item(1, 100), item(2, 200, { above: 150, alert: 'above' }), item(3, 300),
    item(4, 50, { below: 60, alert: 'below' }),
  ], NOW);
  assert.deepStrictEqual(rows.map((r) => r.id), [2, 4, 1, 3]);
  assert.strictEqual(rows[0].alert, 'above');
  assert.strictEqual(rows[1].alert, 'below');
  assert.strictEqual(rows[2].alert, null);
});

test('tickerRows: alert re-derived from thresholds when the server sent none', () => {
  const rows = C.tickerRows([item(1, 100), item(2, 40, { below: 50, alert: undefined })], NOW);
  assert.deepStrictEqual(rows.map((r) => [r.id, r.alert]), [[2, 'below'], [1, null]]);
});

test('tickerRows: at most 5 by default, max overrides, hits never dropped for plain rows', () => {
  const list = [];
  for (let i = 1; i <= 8; i++) list.push(item(i, i * 10));
  list.push(item(9, 5, { below: 6, alert: 'below' }));
  const rows = C.tickerRows(list, NOW);
  assert.strictEqual(rows.length, 5);
  assert.deepStrictEqual(rows.map((r) => r.id), [9, 1, 2, 3, 4]);
  assert.strictEqual(C.tickerRows(list, NOW, 2).length, 2);
  assert.strictEqual(C.tickerRows(list, NOW, 0).length, 5);
  assert.strictEqual(C.tickerRows(list, NOW, 'x').length, 5);
});

test('tickerRows: short price, name fallback, junk rows skipped', () => {
  const rows = C.tickerRows([null, 'x', [], item(7, 1234567890, { name: '' }), item(8, null)], NOW);
  assert.strictEqual(rows.length, 2);
  assert.strictEqual(rows[0].name, '#7');
  assert.strictEqual(rows[0].price, '1.23B');
  assert.strictEqual(rows[1].price, '-');
  assert.deepStrictEqual(C.tickerRows(null, NOW), []);
  assert.deepStrictEqual(C.tickerRows({ items: [] }, NOW), []);
});

test('tickerRows: net when present, null when missing or junk', () => {
  const rows = C.tickerRows([
    item(1, 100000000, { net: 84500000 }), item(2, 100), item(3, 100, { net: 'x' }),
  ], NOW);
  assert.strictEqual(rows[0].net, '84.5M');
  assert.strictEqual(rows[1].net, null);
  assert.strictEqual(rows[2].net, null);
});

test('tickerRows: pre-order badge from the server state', () => {
  const rows = C.tickerRows([item(1, 100, { preorder: 'capped' }), item(2, 100), item(3, 100, { preorder: 'bogus' })], NOW);
  assert.strictEqual(rows[0].badge.label, 'pre-order');
  assert.strictEqual(rows[0].badge.cls, 'preorder');
  assert.strictEqual(rows[1].badge, null);
  assert.strictEqual(rows[2].badge, null);
});

test('tickerRows: stale once the source age passes its TTL at nowMs', () => {
  const it = item(1, 100, { freshness: fresh(200) });
  assert.strictEqual(C.tickerRows([it], NOW)[0].stale, false);
  // 200 s old at fetch, 101 s later the age passes the 300 s TTL.
  assert.strictEqual(C.tickerRows([it], NOW + 101000)[0].stale, true);
  assert.strictEqual(C.tickerRows([item(2, 1, { freshness: Object.assign(fresh(1), { stale: true }) })], NOW)[0].stale, true);
  assert.strictEqual(C.tickerRows([item(3, 1, { freshness: null })], NOW)[0].stale, true);
  assert.strictEqual(C.tickerRows([item(4, 1, { freshness: { fetched_at: null, age_s: null, ttl_s: 300 } })], NOW)[0].stale, true);
  // fetched_at unparsable: fall back to the server's age_s.
  assert.strictEqual(C.tickerRows([item(5, 1, { freshness: { fetched_at: 'x', age_s: 400, ttl_s: 300 } })], NOW)[0].stale, true);
  assert.strictEqual(C.tickerRows([item(6, 1, { freshness: { fetched_at: 'x', age_s: 10, ttl_s: 300 } })], NOW)[0].stale, false);
  const r = C.tickerRows([item(1, 100, { freshness: fresh(400) })], NOW)[0];
  assert.match(r.cls, /ew-stale/);
});

test('tickerRows: arrow vs the previous price', () => {
  const list = [item(1, 110), item(2, 90), item(3, 100), item(4, 100)];
  const rows = C.tickerRows(list, NOW, 5, { '1:0': 100, '2:0': 100, '3:0': 100 });
  assert.deepStrictEqual(rows.map((r) => r.arrow), ['up', 'down', null, null]);
  assert.notStrictEqual(rows[0].arrowText, rows[1].arrowText);
  assert.strictEqual(rows[2].arrowText, '');
  assert.deepStrictEqual(C.tickerRows(list, NOW).map((r) => r.arrow), [null, null, null, null]);
});

test('tickerTrack: base keeps the last different price until the next change', () => {
  let st = C.tickerTrack(null, [item(1, 100)]);
  assert.deepStrictEqual(st, { last: { '1:0': 100 }, base: {} });
  st = C.tickerTrack(st, [item(1, 100)]);
  assert.deepStrictEqual(st.base, {});
  st = C.tickerTrack(st, [item(1, 120)]);
  assert.deepStrictEqual(st, { last: { '1:0': 120 }, base: { '1:0': 100 } });
  st = C.tickerTrack(st, [item(1, 120)]);
  assert.deepStrictEqual(st.base, { '1:0': 100 });
  st = C.tickerTrack(st, [item(1, 90), item(2, null)]);
  assert.deepStrictEqual(st, { last: { '1:0': 90 }, base: { '1:0': 120 } });
  // Removed items drop out of both maps.
  st = C.tickerTrack(st, []);
  assert.deepStrictEqual(st, { last: {}, base: {} });
});

test('marketTicker widget: opt-in, default off, query round-trip, template off', () => {
  assert.strictEqual(C.overlayWidgets({}).marketTicker, false);
  assert.strictEqual(C.overlayWidgets({ overlay: { widgets: { marketTicker: true } } }).marketTicker, true);
  assert.strictEqual(C.overlayWidgets({ overlay: { widgets: { marketTicker: 1 } } }).marketTicker, false);
  assert.strictEqual(C.widgetsFromQuery('').marketTicker, false);
  const q = new URLSearchParams(C.widgetsQuery({ marketTicker: true })).toString();
  assert.strictEqual(C.widgetsFromQuery('?' + q).marketTicker, true);
  const ex = JSON.parse(fs.readFileSync(path.join(APP, '..', 'config', 'local.example.json'), 'utf8'));
  assert.ok(!('widgets' in ex.overlay), 'plan 080: no manual overlay layout in the example');
});

test('overlay: ticker from GET /api/market/watch every 60 s, opt-in row, no POST', () => {
  const src = read('overlay/overlay.js');
  assert.match(src, /getJSON\('\/api\/market\/watch'\)/);
  assert.match(src, /C\.tickerRows\(/);
  assert.match(src, /C\.tickerTrack\(/);
  assert.match(src, /W\.marketTicker/);
  assert.match(src, /TICKER_MS = 60000/);
  assert.doesNotMatch(src, /method:\s*'POST'|ewApi|innerHTML/);
  const html = read('overlay/index.html');
  assert.match(html, /id="ov-ticker-row" hidden/);
  assert.match(html, /id="ov-ticker"/);
});

test('css: a stale ticker row mutes its hit colour and arrows (declared after them)', () => {
  const css = read('shared/ew.css');
  const hit = css.indexOf('.ew-ov-val.ew-tick-hit {');
  const mute = css.indexOf('.ew-ov-row .ew-ov-val.ew-stale, .ew-ov-row .ew-stale .ew-tick-up, .ew-ov-row .ew-stale .ew-tick-down {');
  assert.ok(hit >= 0 && mute > hit && mute > css.indexOf('.ew-tick-down {'));
  assert.match(css.slice(mute), /^[^}]*color: var\(--fk-text-muted\)/);
});
