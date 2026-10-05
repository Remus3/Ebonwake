'use strict';
// Plan 002 slice B: pure market helpers (ewcore.js) and static guards on the
// Market tab (market.js). No network, no Electron, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const APP = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(APP, p), 'utf8');

test('fmtSilver shapes', () => {
  assert.strictEqual(C.fmtSilver(1234567890), '1.23B');
  assert.strictEqual(C.fmtSilver(45600000), '45.6M');
  assert.strictEqual(C.fmtSilver(789000), '789K');
  assert.strictEqual(C.fmtSilver(950), '950');
  assert.strictEqual(C.fmtSilver(0), '0');
  assert.strictEqual(C.fmtSilver(1000000), '1M');
  assert.strictEqual(C.fmtSilver(1500), '1.5K');
  assert.strictEqual(C.fmtSilver(999999), '1M');
  assert.strictEqual(C.fmtSilver(999.6), '1K');
  assert.strictEqual(C.fmtSilver(2.5e12), '2.5T');
  assert.strictEqual(C.fmtSilver(-45600000), '-45.6M');
  assert.strictEqual(C.fmtSilver(null), '-');
  assert.strictEqual(C.fmtSilver(undefined), '-');
  assert.strictEqual(C.fmtSilver(NaN), '-');
  assert.strictEqual(C.fmtSilver('12'), '-');
});

test('sparkPath: empty and junk give empty string', () => {
  assert.strictEqual(C.sparkPath([], 100, 20), '');
  assert.strictEqual(C.sparkPath(null, 100, 20), '');
  assert.strictEqual(C.sparkPath([[1, 'x'], null, [2]], 100, 20), '');
});

test('sparkPath maps time to x and price to inverted y', () => {
  const d = C.sparkPath([[0, 10], [50, 20], [100, 0]], 100, 20);
  assert.strictEqual(d, 'M0,10L50,0L100,20');
});

test('sparkPath sorts by time and handles flat and single points', () => {
  assert.strictEqual(C.sparkPath([[100, 0], [0, 10], [50, 20]], 100, 20), 'M0,10L50,0L100,20');
  assert.strictEqual(C.sparkPath([[0, 5], [10, 5]], 100, 20), 'M0,10L100,10');
  assert.strictEqual(C.sparkPath([[7, 5]], 100, 20), 'M0,10L100,10');
});

test('sparkPath rounds to one decimal', () => {
  assert.strictEqual(C.sparkPath([[0, 0], [3, 1]], 10, 3), 'M0,3L10,0');
  assert.strictEqual(C.sparkPath([[0, 0], [1, 1], [3, 3]], 10, 3), 'M0,3L3.3,2L10,0');
});

test('alertFor edges match slice A', () => {
  assert.strictEqual(C.alertFor(100, 100, null), 'below');
  assert.strictEqual(C.alertFor(101, 100, null), null);
  assert.strictEqual(C.alertFor(200, null, 200), 'above');
  assert.strictEqual(C.alertFor(199, null, 200), null);
  assert.strictEqual(C.alertFor(150, 100, 200), null);
  assert.strictEqual(C.alertFor(50, 100, 200), 'below');
  assert.strictEqual(C.alertFor(null, 100, 200), null);
  assert.strictEqual(C.alertFor(100, undefined, undefined), null);
  assert.strictEqual(C.alertFor(0, 0, null), 'below');
});

test('depthBars: top-n per side, best price first, widths relative to the max', () => {
  const orders = [
    { price: 100, sellers: 2, buyers: 0 },
    { price: 90, sellers: 0, buyers: 8 },
    { price: 110, sellers: 4, buyers: 0 },
    { price: 120, sellers: 1, buyers: 0 },
    { price: 80, sellers: 0, buyers: 2 },
    { price: 95, sellers: 1, buyers: 1 }
  ];
  const d = C.depthBars(orders, 2);
  assert.deepStrictEqual(d.sell, [
    { price: 95, count: 1, w: 0.125 },
    { price: 100, count: 2, w: 0.25 }
  ]);
  assert.deepStrictEqual(d.buy, [
    { price: 95, count: 1, w: 0.125 },
    { price: 90, count: 8, w: 1 }
  ]);
});

test('depthBars: empty, junk and default n', () => {
  assert.deepStrictEqual(C.depthBars(null, 5), { sell: [], buy: [] });
  assert.deepStrictEqual(C.depthBars([null, { price: 'x', sellers: 1 }], 5), { sell: [], buy: [] });
  const many = [];
  for (let i = 1; i <= 20; i++) many.push({ price: i, sellers: 1, buyers: 1 });
  const d = C.depthBars(many);
  assert.strictEqual(d.sell.length, 5);
  assert.strictEqual(d.buy.length, 5);
  assert.strictEqual(d.sell[0].price, 1);
  assert.strictEqual(d.buy[0].price, 20);
});

test('historyStats gives min, max and last by time', () => {
  assert.deepStrictEqual(C.historyStats([[2, 50], [1, 10], [3, 30]]), { min: 10, max: 50, last: 30 });
  assert.deepStrictEqual(C.historyStats([]), { min: null, max: null, last: null });
  assert.deepStrictEqual(C.historyStats(null), { min: null, max: null, last: null });
});

test('fmtAge is compact', () => {
  assert.strictEqual(C.fmtAge(5), '5s');
  assert.strictEqual(C.fmtAge(240), '4m');
  assert.strictEqual(C.fmtAge(7200), '2h');
  assert.strictEqual(C.fmtAge(3 * 86400 + 5), '3d');
  assert.strictEqual(C.fmtAge(-3), '0s');
});

test('marketPill reads the slice A freshness contract', () => {
  assert.deepStrictEqual(C.marketPill(null), { cls: 'unknown', label: 'arsha no data', stale: true, error: null });
  assert.deepStrictEqual(
    C.marketPill({ fetched_at: null, age_s: null, ttl_s: 300, stale: true, error: 'blocked' }),
    { cls: 'unknown', label: 'arsha no data', stale: true, error: 'blocked' });
  assert.deepStrictEqual(
    C.marketPill({ fetched_at: '2026-10-04T00:00:00Z', age_s: 240, ttl_s: 300, stale: false, error: null }),
    { cls: 'ok', label: 'arsha 4m ago', stale: false, error: null });
  // stale is never shown as ok even when young
  assert.strictEqual(
    C.marketPill({ fetched_at: 'x', age_s: 10, ttl_s: 300, stale: true, error: 'HTTP 500' }).cls, 'warn');
  assert.strictEqual(
    C.marketPill({ fetched_at: 'x', age_s: 600, ttl_s: 300, stale: false, error: null }).cls, 'warn');
  assert.strictEqual(
    C.marketPill({ fetched_at: 'x', age_s: 5000, ttl_s: 300, stale: true, error: null }).cls, 'bad');
});

test('parseWatchForm validates ints and builds add/remove bodies', () => {
  assert.deepStrictEqual(C.parseWatchForm({ id: '44195', sid: '0', below: '', above: '' }, 'add'),
    { ok: true, body: { add: { id: 44195, sid: 0 } } });
  assert.deepStrictEqual(C.parseWatchForm({ id: '44195', sid: '', below: '1000000', above: '2000000' }, 'add'),
    { ok: true, body: { add: { id: 44195, sid: 0, below: 1000000, above: 2000000 } } });
  assert.deepStrictEqual(C.parseWatchForm({ id: ' 7 ', sid: '3', below: '5' }, 'remove'),
    { ok: true, body: { remove: { id: 7, sid: 3 } } });
  for (const bad of [
    { id: '', sid: '0' }, { id: '0', sid: '0' }, { id: '1.5', sid: '0' }, { id: 'abc', sid: '0' },
    { id: '1', sid: '-1' }, { id: '1', sid: '0', below: '-5' }, { id: '1', sid: '0', above: '1e3' },
    { id: '99999999999999999999', sid: '0' }
  ]) {
    const r = C.parseWatchForm(bad, 'add');
    assert.strictEqual(r.ok, false, JSON.stringify(bad));
    assert.strictEqual(typeof r.error, 'string');
  }
  assert.strictEqual(C.parseWatchForm({ id: '1', sid: '0' }, 'bogus').ok, false);
});

test('validWatchBody accepts exactly add/remove with int fields', () => {
  assert.strictEqual(C.validWatchBody({ add: { id: 1, sid: 0 } }), true);
  assert.strictEqual(C.validWatchBody({ add: { id: 1, sid: 0, below: 5, above: 9 } }), true);
  assert.strictEqual(C.validWatchBody({ add: { id: 1, sid: 0, below: null } }), true);
  assert.strictEqual(C.validWatchBody({ remove: { id: 1, sid: 2 } }), true);
  for (const bad of [
    null, 'x', [], {}, { add: { id: 1, sid: 0 }, remove: { id: 1, sid: 0 } },
    { add: { id: 1 } }, { add: { id: '1', sid: 0 } }, { add: { id: 1.5, sid: 0 } },
    { add: { id: 0, sid: 0 } }, { add: { id: 1, sid: -1 } }, { add: { id: 1, sid: 0, extra: 1 } },
    { add: { id: 1, sid: 0, below: -1 } }, { remove: { id: 1, sid: 0, below: 3 } },
    { buy: { id: 1, sid: 0 } }, { add: { id: 2 ** 53, sid: 0 } }
  ]) {
    assert.strictEqual(C.validWatchBody(bad), false, JSON.stringify(bad));
  }
  // parseWatchForm output always passes the IPC check
  const r = C.parseWatchForm({ id: '5', sid: '1', below: '3' }, 'add');
  assert.strictEqual(C.validWatchBody(r.body), true);
});

test('market POST goes through the dashboard preload only (overlay has no POST path)', () => {
  const pre = read('preload.js');
  // Plan 003 generalised the plan 002 bridge: window.ewApi.post(route, body).
  assert.match(pre, /contextBridge\.exposeInMainWorld\('ewApi'/);
  assert.match(pre, /ipcRenderer\.invoke\('ew:post'/);
  const m = read('main.js');
  assert.match(m, /ipcMain\.handle\('ew:post'/);
  assert.match(m, /core\.validPost\(/);
  assert.strictEqual(C.validPost('/api/market/watch', { add: { id: 1, sid: 0 } }), true);
  const mk = read('dashboard/market.js');
  assert.match(mk, /window\.ewApi/);
  assert.match(mk, /\.post\('\/api\/market\/watch'/);
  assert.doesNotMatch(mk, /ewMarket/);
  // Plan 022: the overlay's own preload is a one-way size report, never a POST.
  assert.equal((m.match(/preload:/g) || []).length, 2, 'dashboard + overlay size preload');
  const ov = m.slice(m.indexOf('function createOverlay'), m.indexOf('function toggleOverlay'));
  assert.doesNotMatch(ov, /path\.join\(__dirname, 'preload\.js'\)/);
  const ovPre = read('overlay/preload.js');
  assert.doesNotMatch(ovPre, /ew:post|invoke|ewApi/);
  assert.doesNotMatch(pre + m + ovPre, /arsha\.io/);
});

test('pollDue throttles to the interval', () => {
  assert.strictEqual(C.pollDue(null, 1000, 60000), true);
  assert.strictEqual(C.pollDue(0, 59999, 60000), false);
  assert.strictEqual(C.pollDue(0, 60000, 60000), true);
});

test('market.js builds DOM safely and polls no faster than 60 s', () => {
  const src = read('dashboard/market.js');
  assert.doesNotMatch(src, /innerHTML|outerHTML|insertAdjacentHTML|document\.write/);
  assert.match(src, /createElementNS\(/);
  assert.match(src, /POLL_MS\s*=\s*60000/);
  assert.match(src, /\/api\/market\/watch/);
  assert.match(src, /\/api\/market\/item/);
  assert.match(src, /\/api\/market\/hot/);
  assert.doesNotMatch(src, /arsha\.io/);
});

test('dashboard mounts the market tab and loads market.js; CSP unchanged', () => {
  const html = read('dashboard/index.html');
  assert.match(html, /connect-src http:\/\/127\.0\.0\.1:8940;/);
  const iCore = html.indexOf('ewcore.js');
  const iMarket = html.indexOf('<script src="market.js"></script>');
  const iDash = html.indexOf('dashboard.js');
  assert.ok(iCore >= 0 && iMarket > iCore && iDash > iMarket, 'script order ewcore, market, dashboard');
  assert.match(read('dashboard/dashboard.js'), /EWMarket\.mount\(/);
});

test('itemFreshness flattens the nested /api/market/item shape', () => {
  const ok = { fetched_at: '2026-10-04T00:00:00+00:00', age_s: 60, ttl_s: 300, stale: false, error: null };
  const f = C.itemFreshness({ sub: ok, history: ok, orders: Object.assign({}, ok, { stale: true, error: 'arsha code 103' }) });
  assert.strictEqual(f.age_s, 60);
  assert.strictEqual(f.stale, true);
  assert.strictEqual(f.error, 'arsha code 103');
  const g = C.itemFreshness({ sub: ok, history: ok, orders: ok });
  assert.strictEqual(g.stale, false);
  assert.strictEqual(C.marketPill(g).stale, false);
  assert.strictEqual(C.itemFreshness(ok), ok);
});

test('fmtSilver never prints -0', () => {
  assert.strictEqual(C.fmtSilver(-0.4), '0');
  assert.strictEqual(C.fmtSilver(-1500), '-1.5K');
});

// ---- plan 028: exact silver + name typeahead helpers ----

test('fmtSilverExact uses thousands separators', () => {
  assert.strictEqual(C.fmtSilverExact(1234567890), '1,234,567,890');
  assert.strictEqual(C.fmtSilverExact(100000000), '100,000,000');
  assert.strictEqual(C.fmtSilverExact(999), '999');
  assert.strictEqual(C.fmtSilverExact(1000), '1,000');
  assert.strictEqual(C.fmtSilverExact(0), '0');
  assert.strictEqual(C.fmtSilverExact(-4560000), '-4,560,000');
  assert.strictEqual(C.fmtSilverExact(-0.2), '0');
  assert.strictEqual(C.fmtSilverExact(1499.6), '1,500');
  assert.strictEqual(C.fmtSilverExact(null), '-');
  assert.strictEqual(C.fmtSilverExact(NaN), '-');
  assert.strictEqual(C.fmtSilverExact('12'), '-');
});

test('searchQuery mirrors the server validation (2-40 printable ASCII)', () => {
  assert.strictEqual(C.searchQuery(' cron '), 'cron');
  assert.strictEqual(C.searchQuery('16080'), '16080');
  assert.strictEqual(C.searchQuery('x'.repeat(40)), 'x'.repeat(40));
  [null, undefined, 12, '', 'a', ' a ', 'x'.repeat(41), 'caf' + String.fromCharCode(0xe9), 'ab\tc', 'ab\x7f']
    .forEach((q) => assert.strictEqual(C.searchQuery(q), null, JSON.stringify(q)));
  assert.strictEqual(C.SEARCH_DEBOUNCE_MS, 250);
});

test('searchPath encodes the query', () => {
  assert.strictEqual(C.searchPath('black st&x'), '/api/market/search?q=black%20st%26x');
});

test('searchRows keeps valid rows and builds labels', () => {
  const rows = C.searchRows({ items: [
    { id: 16080, sid: 0, name: 'Cron Stone' },
    { id: 11103, sid: 3, name: 'Kzarka Longbow' },
    { id: '1', sid: 0, name: 'bad id' }, { id: 2, sid: -1, name: 'bad sid' },
    { id: 3, sid: 0, name: '' }, null, 'junk'] });
  assert.deepStrictEqual(rows, [
    { id: 16080, sid: 0, name: 'Cron Stone', label: 'Cron Stone  #16080' },
    { id: 11103, sid: 3, name: 'Kzarka Longbow', label: 'Kzarka Longbow [3]  #11103' }]);
  assert.deepStrictEqual(C.searchRows(null), []);
  assert.deepStrictEqual(C.searchRows({ items: 'x' }), []);
});

test('market.js wires the debounced typeahead and exact silver in detail', () => {
  const src = read('dashboard/market.js');
  assert.match(src, /C\.searchPath\(/);
  assert.match(src, /C\.SEARCH_DEBOUNCE_MS/);
  assert.match(src, /clearTimeout\(f\.timer\)/);
  assert.match(src, /advanced: raw id \/ sid/);
  assert.match(src, /C\.fmtSilverExact\(s\[1\]\)/);
  assert.match(src, /title = C\.fmtSilverExact\(n\)/);
});
