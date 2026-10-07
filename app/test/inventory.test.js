'use strict';
// Plan 045: inventory / weight / storage planner + Value Pack ledger card on
// the Progress tab. Pure helpers in ewcore.js and static guards on the card
// module and the bridge. No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const APP = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(APP, p), 'utf8');

const VIEW = {
  sources: [
    { id: 'strength', name: 'Strength training', min: 0, max: 40, verified: false, owned_lt: 40,
      next_lt: null, next_cost: null, note: '', warn: '' },
    { id: 'belt', name: 'Belt', min: 0, max: 80, verified: false, owned_lt: null, next_lt: 80,
      next_cost: 320000000, note: 'boss', warn: '' },
    { id: 'Bad Id', name: 'x' }, null
  ],
  base_lt: 1000, lt_owned: 40, vp_lt: 200, lt_total: 1240,
  next_cheapest: [{ id: 'belt', name: 'Belt', next_lt: 80, next_cost: 320000000, cost_per_lt: 4000000 }],
  slots: { base: 100, used: 110, vp: 16, total: 116, free: 6 },
  warehouse: { vt: 7000, fame: true, base_vt: 5000, fame_vt: 2000, transfer_vt: 200,
    source: 'https://www.naeu.playblackdesert.com/en-us/Wiki?wikiNo=47', verified: '2025-08-06' },
  towns: [{ id: 'heidel', name: 'Heidel', used: 192, total: 192, note: 'mats', free: 0 },
    { id: 'velia', name: 'Velia', used: null, total: null, note: '', free: null }],
  town_slots: { used: 192, total: 192 },
  vp: { active: true, from: 'buff', ends: '2026-10-08T12:00:00+00:00', left_s: 259200, reminder: null },
  ledger: { sales: [{ id: 's2', at: '2026-10-05T12:00:00+00:00', price: 100000000, vp: true, fame_pct: 0,
    net: 84500000, gain: 19500000 }, { id: 's1', at: '2026-10-04T12:00:00+00:00', price: 1000000, vp: false,
    fame_pct: 0, net: 650000, gain: 0 }], count: 2, gain_total: 19500000, gain_30d: 19500000,
  vp_cost: 15000000, net_30d: 4500000 },
  error: null
};

test('validInventoryBody: exact shapes only', () => {
  const ok = [
    { set: { base_lt: 1000 } }, { set: { slots: null, fame_vt: true, vp_cost: 0 } },
    { source: { id: 'strength', lt: 40 } }, { source: { id: 'belt', next_lt: 80, next_cost: 1e9, note: '' } },
    { source: { id: 'belt', lt: null } },
    { town_add: { name: 'Heidel' } }, { town_add: { name: 'Heidel', used: 1, total: 2, note: 'x' } },
    { town_edit: { id: 'heidel-2', used: 3 } }, { town_del: 'heidel' },
    { sale: { price: 1 } }, { sale: { price: 1e13, vp: false } }, { sale_del: 's12' }
  ];
  for (const b of ok) assert.strictEqual(C.validInventoryBody(b), true, JSON.stringify(b));
  const bad = [
    null, [], {}, { nope: 1 }, { set: {} }, { set: { base_lt: -1 } }, { set: { slots: 0 } },
    { set: { fame_vt: 'y' } }, { set: { base_lt: 1, x: 1 } },
    { source: { lt: 1 } }, { source: { id: 'strength' } }, { source: { id: 'Bad', lt: 1 } },
    { source: { id: 'strength', lt: 5001 } }, { source: { id: 'strength', next_lt: 0 } },
    { source: { id: 'strength', note: 'x'.repeat(121) } },
    { town_add: {} }, { town_add: { used: 1 } }, { town_add: { name: '' } },
    { town_edit: { id: 'heidel' } }, { town_edit: { id: 'Bad Id', used: 1 } },
    { town_del: 5 }, { sale: {} }, { sale: { price: 0 } }, { sale: { price: 1.5 } },
    { sale: { price: 1, vp: 'y' } }, { sale_del: 'x1' }, { sale_del: 's0' },
    { sale: { price: 1 }, sale_del: 's1' }
  ];
  for (const b of bad) assert.strictEqual(C.validInventoryBody(b), false, JSON.stringify(b));
});

test('validPost / labels: /api/inventory is a bridge route', () => {
  assert.ok(C.POST_ROUTES.indexOf('/api/inventory') >= 0);
  assert.strictEqual(C.validPost('/api/inventory', { town_del: 'heidel' }), true);
  assert.strictEqual(C.postToast('/api/inventory', { ok: true }).text, 'Inventory saved');
});

test('form parsers: blanks clear, silver suffixes, range errors', () => {
  assert.deepStrictEqual(C.parseInvSetForm({ base_lt: ' 1000 ', slots: '', slots_used: '90', vp_cost: '15m', fame_vt: true }),
    { ok: true, body: { set: { base_lt: 1000, slots: null, slots_used: 90, vp_cost: 15000000, fame_vt: true } } });
  assert.strictEqual(C.parseInvSetForm({ slots: '900' }).ok, false);
  assert.match(C.parseInvSetForm({ base_lt: 'lots' }).error, /base lt/);
  const s = C.parseInvSourceForm({ id: 'belt', lt: '', next_lt: '80', next_cost: '320m', note: ' boss ' });
  assert.deepStrictEqual(s.body, { source: { id: 'belt', lt: null, next_lt: 80, next_cost: 320000000, note: 'boss' } });
  assert.strictEqual(C.validInventoryBody(s.body), true);
  assert.match(C.parseInvSourceForm({ id: '' }).error, /source/);
  assert.strictEqual(C.parseInvSourceForm({ id: 'belt', lt: '9999' }).ok, false);
  const t = C.parseInvTownForm({ name: ' Heidel ', used: '10', total: '192', note: '' });
  assert.deepStrictEqual(t.body, { town_add: { name: 'Heidel', used: 10, total: 192, note: '' } });
  assert.strictEqual(C.validInventoryBody(t.body), true);
  assert.match(C.parseInvTownForm({ name: 'A', used: '5', total: '4' }).error, /exceed/);
  assert.match(C.parseInvTownForm({ name: '' }).error, /town name/);
  assert.deepStrictEqual(C.parseInvSale({ price: '84.5m', vp: true }), { ok: true, body: { sale: { price: 84500000, vp: true } } });
  assert.deepStrictEqual(C.parseInvSale({ price: '1,000', vp: false }).body, { sale: { price: 1000, vp: false } });
  assert.strictEqual(C.parseInvSale({ price: '0' }).ok, false);
  assert.strictEqual(C.parseInvSale({ price: 'x' }).ok, false);
});

test('invSummaryLines: weight, slots, warehouse, VP, ledger', () => {
  const l = C.invSummaryLines(VIEW);
  assert.deepStrictEqual(l.map((x) => [x.text, x.cls]), [
    ['weight 1,240 LT (sources +40, VP +200)', 'ok'],
    ['inventory 110/116 slots, 6 free', 'warn'],
    ['market warehouse 7,000 VT, 200 VT per transfer', 'ok'],
    ['Value Pack on - 3d 0h left', 'ok'],
    ['VP +30% earned 19.5M in 30 d (19.5M total, 2 sales), net of VP cost 4.5M', 'ok']]);
  const off = Object.assign({}, VIEW, { base_lt: null, lt_total: null, vp_lt: 0, slots: { base: null },
    vp: { active: false, reminder: 'Value Pack off: +200 LT / +16 inventory / +16 storage slots unused' },
    ledger: { sales: [], count: 0 } });
  const lo = C.invSummaryLines(off);
  assert.strictEqual(lo[0].text, 'weight type base LT (sources +40)');
  assert.deepStrictEqual(lo.map((x) => x.cls), ['unknown', 'ok', 'warn']);
  assert.match(lo[2].text, /\+200 LT/);
  const settings = C.invSummaryLines(Object.assign({}, VIEW, { vp: { active: true, from: 'settings', left_s: null } }));
  assert.match(settings[3].text, /settings/);
  assert.deepStrictEqual(C.invSummaryLines(null), []);
});

test('row helpers: sources, next, towns, sales; junk dropped', () => {
  const rows = C.invSourceRows(VIEW);
  assert.strictEqual(rows.length, 2);
  assert.deepStrictEqual(rows[0], { id: 'strength', name: 'Strength training', owned: '+40 LT', next: '', note: '',
    warn: '', done: true, range: '0..40 LT (unverified)', lt: 40, next_lt: null, next_cost: null });
  assert.strictEqual(rows[1].owned, 'not set');
  assert.strictEqual(rows[1].next, 'next +80 for 320M');
  assert.deepStrictEqual(C.invNextLines(VIEW), ['Belt: +80 LT for 320M (4M/LT)']);
  assert.deepStrictEqual(C.invTownRows(VIEW), [
    { id: 'heidel', text: 'Heidel - 192/192 (0 free) - mats', full: true },
    { id: 'velia', text: 'Velia - slots not set', full: false }]);
  assert.deepStrictEqual(C.invSaleRows(VIEW).map((s) => s.text), [
    '2026-10-05 sold 100M -> 84.5M (VP +19.5M)', '2026-10-04 sold 1M -> 650K (no VP)']);
  assert.strictEqual(C.invSaleRows(VIEW, 1).length, 1);
  assert.deepStrictEqual(C.invSourceRows({ sources: 'x' }), []);
});

test('inventory.js card: Progress tab, GET + bridge POST, no HTML sinks', () => {
  const src = read('dashboard/inventory.js');
  assert.match(src, /window\.EWInventory\s*=/);
  assert.match(src, /'Inventory'/);
  assert.match(src, /getJSON\('\/api\/inventory'\)/);
  assert.match(src, /\.post\('\/api\/inventory'/);
  for (const f of ['invSummaryLines', 'invSourceRows', 'invNextLines', 'invTownRows', 'invSaleRows',
    'parseInvSetForm', 'parseInvSourceForm', 'parseInvTownForm', 'parseInvSale']) {
    assert.ok(src.indexOf('C.' + f + '(') >= 0, f);
  }
  assert.doesNotMatch(src, /innerHTML|outerHTML|insertAdjacentHTML|document\.write|method:\s*'POST'/);
  const dash = read('dashboard/dashboard.js');
  assert.match(dash, /EWInventory\.mount\(/);
  assert.match(dash, /EWInventory\.show\(/);
  const html = read('dashboard/index.html');
  const i = html.indexOf('<script src="inventory.js"></script>');
  assert.ok(i > html.indexOf('ewcore.js') && i < html.indexOf('<script src="dashboard.js"></script>'));
  assert.match(read('preload.js'), /\/api\/inventory/);
});
