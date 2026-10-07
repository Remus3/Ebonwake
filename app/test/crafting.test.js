'use strict';
// Plan 054: cooking / alchemy margin card on the Market tab. Pure helpers in
// ewcore.js and static guards on the card module and the bridge. No network,
// no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const APP = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(APP, p), 'utf8');

const RECIPE = {
  name: 'Beer', kind: 'cooking',
  inputs: [{ id: 1, qty: 5 }, { name: 'Leavening Agent', qty: 2, vendor_price: 20 }],
  outputs: [{ id: 9, qty_avg: 2.5 }]
};

test('validCraftingBody: exact shapes only', () => {
  const ok = [
    { add: RECIPE }, { add: Object.assign({}, RECIPE, { procs: [] }) },
    { add: Object.assign({}, RECIPE, { kind: 'alchemy', procs: [{ id: 7, qty_avg: 0.1, name: 'Rare' }] }) },
    { edit: Object.assign({ id: 'beer-2' }, RECIPE) }, { delete: 'beer' },
    { add: { name: 'X', inputs: [{ id: 1, qty: 1, vendor_price: null }], outputs: [{ id: 2, qty_avg: 1000 }] } }
  ];
  for (const b of ok) assert.strictEqual(C.validPost('/api/crafting', b), true, JSON.stringify(b));
  const many = [];
  for (let i = 0; i < 21; i++) many.push({ id: 1, qty: 1 });
  const bad = [
    null, {}, { nope: 1 }, { add: RECIPE, delete: 'beer' }, { delete: 'Bad Id' }, { delete: 5 },
    { edit: RECIPE }, { add: Object.assign({ id: 'x' }, RECIPE) },
    { add: Object.assign({}, RECIPE, { name: '' }) },
    { add: Object.assign({}, RECIPE, { kind: 'fishing' }) },
    { add: Object.assign({}, RECIPE, { inputs: [] }) },
    { add: Object.assign({}, RECIPE, { inputs: many }) },
    { add: Object.assign({}, RECIPE, { inputs: [{ id: 1, qty: 0 }] }) },
    { add: Object.assign({}, RECIPE, { inputs: [{ id: 1, qty: 1.5 }] }) },
    { add: Object.assign({}, RECIPE, { inputs: [{ qty: 1 }] }) },
    { add: Object.assign({}, RECIPE, { inputs: [{ id: 1, qty: 1, vendor_price: -1 }] }) },
    { add: Object.assign({}, RECIPE, { outputs: [{ id: 9, qty_avg: 0 }] }) },
    { add: Object.assign({}, RECIPE, { outputs: [{ name: 'Beer', qty_avg: 1 }] }) },
    { add: Object.assign({}, RECIPE, { extra: 1 }) }
  ];
  for (const b of bad) assert.strictEqual(C.validPost('/api/crafting', b), false, JSON.stringify(b));
  assert.ok(C.POST_ROUTES.indexOf('/api/crafting') >= 0);
  assert.strictEqual(C.postToast('/api/crafting', { ok: true }).text, 'Crafting saved');
});

test('parseCraftLines: inputs and outputs', () => {
  assert.deepStrictEqual(C.parseCraftLines('5 #9001\n\n 2 Leavening Agent @1.5k \n3x Flour @20', 'input'),
    { ok: true, lines: [{ qty: 5, id: 9001 }, { qty: 2, name: 'Leavening Agent', vendor_price: 1500 },
      { qty: 3, name: 'Flour', vendor_price: 20 }] });
  assert.deepStrictEqual(C.parseCraftLines('2.5 #9213 Beer\n0.1 #9214', 'output'),
    { ok: true, lines: [{ id: 9213, qty_avg: 2.5, name: 'Beer' }, { id: 9214, qty_avg: 0.1 }] });
  for (const t of ['0 #1', 'five #1', '1 Flour @abc', '10000 #1']) {
    assert.strictEqual(C.parseCraftLines(t, 'input').ok, false, t);
  }
  for (const t of ['1 Beer', '0 #1', '#1 2']) assert.strictEqual(C.parseCraftLines(t, 'output').ok, false, t);
  assert.deepStrictEqual(C.parseCraftLines('', 'input'), { ok: true, lines: [] });
});

test('parseCraftForm: add, edit, errors, and craftFormOf round-trips', () => {
  const f = { name: ' Beer ', kind: 'cooking', inputs: '5 #1\n2 Leavening Agent @20', outputs: '2.5 #9', procs: '' };
  const add = C.parseCraftForm(f);
  assert.deepStrictEqual(add, { ok: true, body: { add: {
    name: 'Beer', kind: 'cooking', inputs: [{ qty: 5, id: 1 }, { qty: 2, name: 'Leavening Agent', vendor_price: 20 }],
    outputs: [{ id: 9, qty_avg: 2.5 }], procs: [] } } });
  assert.ok(C.validPost('/api/crafting', add.body));
  const edit = C.parseCraftForm(f, 'beer');
  assert.strictEqual(edit.body.edit.id, 'beer');
  assert.ok(C.validPost('/api/crafting', edit.body));
  assert.strictEqual(C.parseCraftForm(Object.assign({}, f, { name: '' })).ok, false);
  assert.strictEqual(C.parseCraftForm(Object.assign({}, f, { inputs: '' })).ok, false);
  assert.strictEqual(C.parseCraftForm(Object.assign({}, f, { outputs: '' })).ok, false);
  assert.match(C.parseCraftForm(Object.assign({}, f, { inputs: 'junk' })).error, /input line 1/);
  const stored = Object.assign({ id: 'beer' }, add.body.add);
  const back = C.parseCraftForm(C.craftFormOf(stored), 'beer');
  assert.deepStrictEqual(back.body.edit, Object.assign({}, add.body.add, { id: 'beer' }));
  assert.deepStrictEqual(C.craftFormOf(null), { name: '', kind: 'cooking', inputs: '', outputs: '', procs: '' });
});

test('craftSummary formats server numbers and flags missing prices', () => {
  const s = C.craftSummary({ margin: { cost: 540, gross: 2500, net: 1625, profit: 1085, profit_1000: 1085000,
    margin_pct: 200.9, complete: true, missing: [] } });
  assert.deepStrictEqual(s, { cost: '540', net: '1.63K', profit: C.fmtSilver(1085), per1000: C.fmtSilver(1085000), pct: '200.9%',
    loss: false, missing: '' });
  const m = C.craftSummary({ margin: { cost: 40, gross: 0, net: 0, profit: null, profit_1000: null,
    margin_pct: null, complete: false, missing: [{ side: 'input', id: 1, name: null },
      { side: 'output', id: null, name: 'Flour' }] } });
  assert.strictEqual(m.profit, '-');
  assert.strictEqual(m.loss, true);
  assert.strictEqual(m.missing, 'no price: input #1, output Flour');
  assert.strictEqual(C.craftSummary({ margin: { cost: 10, net: 5, profit: -5, profit_1000: -5000, complete: true,
    missing: [] } }).loss, true);
  assert.strictEqual(C.craftSummary(null).profit, '-');
});

test('crafting.js card: Market tab, GET + bridge POST, no HTML sinks', () => {
  const src = read('dashboard/crafting.js');
  assert.match(src, /window\.EWCrafting\s*=/);
  assert.match(src, /getJSON\('\/api\/crafting'\)/);
  assert.match(src, /\.post\('\/api\/crafting'/);
  for (const f of ['parseCraftForm', 'craftFormOf', 'craftSummary']) assert.ok(src.indexOf('C.' + f + '(') >= 0, f);
  assert.doesNotMatch(src, /innerHTML|outerHTML|insertAdjacentHTML|document\.write|method:\s*'POST'/);
  const dash = read('dashboard/dashboard.js');
  assert.match(dash, /EWCrafting\.mount\(/);
  assert.match(dash, /EWCrafting\.show\(/);
  const html = read('dashboard/index.html');
  const i = html.indexOf('<script src="crafting.js"></script>');
  assert.ok(i > html.indexOf('ewcore.js') && i < html.indexOf('<script src="dashboard.js"></script>'));
  assert.match(read('preload.js'), /\/api\/crafting/);
  assert.ok(read('overlay/overlay.js').indexOf('/api/crafting') < 0);
});
