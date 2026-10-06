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

// ---- plan 052: price bands + below-p20 alert ----

test('alertFor mirrors the server: thresholds win, below_p20 strict and opt-in', () => {
  const b = { p20: 100, p50: 120, p80: 140 };
  assert.strictEqual(C.alertFor(99, null, null, b, true), 'below_p20');
  assert.strictEqual(C.alertFor(100, null, null, b, true), null);
  assert.strictEqual(C.alertFor(99, null, null, b, false), null);
  assert.strictEqual(C.alertFor(99, null, null, null, true), null);
  assert.strictEqual(C.alertFor(50, 60, null, b, true), 'below');
  assert.strictEqual(C.alertFor(50, null, 40, b, true), 'above');
  assert.strictEqual(C.alertFor(null, null, null, b, true), null);
});

test('watchAlert takes the server kind, else derives it', () => {
  assert.strictEqual(C.watchAlert({ alert: 'below_p20', price: 999 }), 'below_p20');
  assert.strictEqual(C.watchAlert({ price: 5, bands: { p20: 9 }, p20: true }), 'below_p20');
  assert.strictEqual(C.watchAlert({ alert: 'bogus', price: 5, below: 6 }), 'below');
  assert.strictEqual(C.watchAlert(null), null);
});

test('autoWatchBadge marks plan 071 auto rows and names the band', () => {
  assert.strictEqual(C.autoWatchBadge({ id: 1, below: 5 }), null);
  assert.strictEqual(C.autoWatchBadge(null), null);
  const b = C.autoWatchBadge({ auto: true, threshold: 'auto band', auto_band: { below: 1000, above: 25000 } });
  assert.strictEqual(b.text, 'auto band');
  assert.match(b.title, /below 1,000 \/ above 25,000/);
  assert.strictEqual(C.autoWatchBadge({ auto: true, threshold: null, auto_band: null }).text, 'auto');
});

test('bandStrip places p20/p50/p80 and the price on one scale', () => {
  const b = { p20: 100, p50: 150, p80: 200, n: 30, basis: 'history', age_s: 7200 };
  const s = C.bandStrip(b, 50);
  assert.deepStrictEqual([s.p20, s.p50, s.p80, s.price], [1 / 3, 2 / 3, 1, 0]);
  assert.strictEqual(s.zone, 'low');
  assert.strictEqual(s.text, 'p20 100  p50 150  p80 200');
  assert.strictEqual(s.source, '90d history, n=30, 2h old');
  assert.strictEqual(C.bandStrip(b, 150).zone, 'mid');
  assert.strictEqual(C.bandStrip(b, 250).zone, 'high');
  const none = C.bandStrip(Object.assign({}, b, { basis: 'samples' }), null);
  assert.strictEqual(none.price, null);
  assert.strictEqual(none.zone, null);
  assert.match(none.source, /^own samples/);
  const flat = C.bandStrip({ p20: 7, p50: 7, p80: 7 }, 7);
  assert.deepStrictEqual([flat.p20, flat.price], [0.5, 0.5]);
  assert.strictEqual(C.bandStrip(null, 5), null);
  assert.strictEqual(C.bandStrip({ p20: 1, p50: 2 }, 5), null);
});

test('p20 opt-in: form -> body -> IPC check', () => {
  const on = C.parseWatchForm({ id: '5', sid: '0', p20: true }, 'add');
  assert.deepStrictEqual(on.body, { add: { id: 5, sid: 0, p20: true } });
  assert.strictEqual(C.validWatchBody(on.body), true);
  assert.deepStrictEqual(C.parseWatchForm({ id: '5', sid: '0', p20: false }, 'add').body, { add: { id: 5, sid: 0 } });
  assert.strictEqual(C.validWatchBody({ add: { id: 5, sid: 0, p20: 'yes' } }), false);
  assert.strictEqual(C.validWatchBody({ remove: { id: 5, sid: 0, p20: true } }), false);
});

test('market tab draws the band strip and the p20 checkbox', () => {
  const src = read('dashboard/market.js');
  assert.match(src, /C\.bandStrip\(/);
  assert.match(src, /f\.p20\.checked/);
  assert.match(src, /delete rest\.bands\.age_s/);
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

// ---- plan 027: net proceeds after tax + pre-order badge ----

const HOT = JSON.parse(fs.readFileSync(
  path.join(__dirname, '..', '..', 'tests', 'fixtures', 'market', 'hot_preorder.json'), 'utf8'));

test('netProceeds matches the server fixtures (floor, basis points)', () => {
  const off = { vp: false, fame_pct: 0 };
  const on = { vp: true, fame_pct: 0 };
  assert.strictEqual(C.netProceeds(100000000, off), 65000000);
  assert.strictEqual(C.netProceeds(100000000, on), 84500000);
  assert.strictEqual(C.netProceeds(100000000, { vp: true, fame_pct: 1.5 }), 85475000);
  assert.strictEqual(C.netProceeds(100000000, { vp: false, fame_pct: 0.5 }), 65325000);
  assert.strictEqual(C.netProceeds(210, off), 136);
  assert.strictEqual(C.netProceeds(1, on), 0);
  assert.strictEqual(C.netProceeds(999999999999, { vp: true, fame_pct: 1.5 }), 854749999999);
  // half basis points: same values as tests/test_market.py (half up, same double)
  assert.strictEqual(C.netProceeds(100000000, { vp: false, fame_pct: 0.125 }), 65084500);
  assert.strictEqual(C.netProceeds(100000000, { vp: false, fame_pct: 0.005 }), 65006500);
  assert.strictEqual(C.netProceeds(100000000, { vp: false, fame_pct: 0.145 }), 65091000);
  // server-sent rates win over the defaults
  assert.strictEqual(C.netProceeds(100, { vp: false, fame_pct: 0, tax: 0.5 }), 50);
  assert.strictEqual(C.netProceeds(100000000), 65000000);
  for (const bad of [null, -1, 1.5, '100', NaN, Infinity]) {
    assert.strictEqual(C.netProceeds(bad, on), null, String(bad));
  }
  assert.strictEqual(C.netProceeds(100, { vp: false, fame_pct: 9 }), null);
});

test('fmtSilverExact groups digits for the hover title', () => {
  assert.strictEqual(C.fmtSilverExact(84500000), '84,500,000');
  assert.strictEqual(C.fmtSilverExact(950), '950');
  assert.strictEqual(C.fmtSilverExact(-1234567), '-1,234,567');
  assert.strictEqual(C.fmtSilverExact(null), '-');
  assert.strictEqual(C.fmtSilverExact(1.5), '2'); // one formatter since the 027+028 merge: rounds
});

test('parseSilver reads digits, commas and k/m/b suffixes', () => {
  assert.strictEqual(C.parseSilver('84,500,000'), 84500000);
  assert.strictEqual(C.parseSilver(' 100m '), 100000000);
  assert.strictEqual(C.parseSilver('1.5B'), 1500000000);
  assert.strictEqual(C.parseSilver('2.95M'), 2950000);
  assert.strictEqual(C.parseSilver('750k'), 750000);
  assert.strictEqual(C.parseSilver('0'), 0);
  for (const bad of ['', '  ', 'abc', '-5', '1.5', '1e9', '1..5m', 'm', null, undefined, '1,5,0']) {
    assert.strictEqual(C.parseSilver(bad), null, String(bad));
  }
});

test('pairProfit: buy X, sell Y -> profit after tax', () => {
  const on = { vp: true, fame_pct: 0 };
  assert.deepStrictEqual(C.pairProfit('80m', '100m', on),
    { ok: true, buy: 80000000, sell: 100000000, net: 84500000, profit: 4500000 });
  assert.deepStrictEqual(C.pairProfit('70m', '100m', { vp: false, fame_pct: 0 }),
    { ok: true, buy: 70000000, sell: 100000000, net: 65000000, profit: -5000000 });
  assert.strictEqual(C.pairProfit('', '100m', on).ok, false);
  assert.strictEqual(typeof C.pairProfit('x', '100m', on).error, 'string');
  assert.strictEqual(C.pairProfit('1m', 'zz', on).ok, false);
});

test('preorderBadge: states from the recorded hot fixture', () => {
  const b = HOT.map(function (x) { return C.preorderBadge(C.preorderState(x)); });
  assert.strictEqual(b[0].label, 'pre-order');
  assert.match(b[0].title, /last sold at the max price/);
  assert.match(b[0].title, /listing fills only from pre-orders at max; >= 20 B items fill at random/);
  assert.strictEqual(b[1].label, 'pre-order');
  assert.match(b[1].title, /no stock listed/);
  assert.strictEqual(b[2], null);
  assert.strictEqual(C.preorderBadge(null), null);
  assert.strictEqual(C.preorderBadge('bogus'), null);
  // server-sent preorder wins; local fallback mirrors market.preorder_state
  assert.deepStrictEqual(HOT.map(C.preorderState), ['capped', 'no_stock', null]);
  assert.strictEqual(C.preorderState({ currentStock: 0, preorder: null }), null);
  assert.strictEqual(C.preorderState(null), null);
});

test('market.js shows net column, pair calculator and pre-order badge (L6)', () => {
  const src = read('dashboard/market.js');
  assert.match(src, /C\.netProceeds\(|it\.net/);
  assert.match(src, /C\.fmtSilverExact\(/);
  assert.match(src, /C\.pairProfit\(/);
  assert.match(src, /C\.preorderBadge\(/);
  // bare "stock 0" is never printed without the badge path
  assert.match(src, /preorderBadge[\s\S]*'stock '/);
  assert.doesNotMatch(src, /\.post\('\/api\/market\/(buy|sell|register)/);
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
