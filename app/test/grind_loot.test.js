'use strict';
// Plan 039: loot-valued grind log - body guards, form parsing and the
// sell-vs-vendor / trash-pile text helpers (ewcore.js), plus static guards on
// the Grind tab. No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const read = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');

test('validGrindBody: stop / log take an optional loot list', () => {
  const ok = [
    { stop: { silver: 0, trash: 0, loot: [{ name: 'Trash', count: 1 }] } },
    { stop: { silver: 0, trash: 0, loot: [] } },
    { stop: { silver: 0, trash: 0, loot: [{ id: 16001, count: 1e7 }, { name: 'X', count: 2 }] } },
    { log: { spot: 'a', minutes: 60, silver: 0, trash: 0, loot: [{ name: 'X', count: 3 }] } }
  ];
  for (const b of ok) assert.strictEqual(C.validGrindBody(b), true, JSON.stringify(b));
  const many = [];
  for (let i = 0; i < 51; i++) many.push({ name: 'x' + i, count: 1 });
  const bad = [
    { stop: { silver: 0, trash: 0, loot: 'x' } },
    { stop: { silver: 0, trash: 0, loot: [{ name: 'X', count: 0 }] } },
    { stop: { silver: 0, trash: 0, loot: [{ name: 'X', count: 1e7 + 1 }] } },
    { stop: { silver: 0, trash: 0, loot: [{ name: 'X', id: 1, count: 1 }] } },
    { stop: { silver: 0, trash: 0, loot: [{ name: '', count: 1 }] } },
    { stop: { silver: 0, trash: 0, loot: [{ id: 0, count: 1 }] } },
    { stop: { silver: 0, trash: 0, loot: [{ count: 1 }] } },
    { stop: { silver: 0, trash: 0, loot: many } }
  ];
  for (const b of bad) assert.strictEqual(C.validGrindBody(b), false, JSON.stringify(b));
});

test('validGrindBody: loot_item / loot_forget shapes', () => {
  const ok = [
    { loot_item: { spot: 'a', name: 'Rag', marketable: false, vendor_price: 5 } },
    { loot_item: { spot: 'a', name: 'Stone', marketable: true, id: 16001 } },
    { loot_item: { spot: 'a', name: 'Stone', marketable: true, id: 16001, vendor_price: 0 } },
    { loot_forget: { spot: 'a', name: 'Rag' } }
  ];
  for (const b of ok) assert.strictEqual(C.validGrindBody(b), true, JSON.stringify(b));
  const bad = [
    { loot_item: { spot: 'a', name: 'Rag', marketable: false } },
    { loot_item: { spot: 'a', name: 'Rag', marketable: 1, vendor_price: 5 } },
    { loot_item: { spot: 'a', name: 'Rag', marketable: true, id: -1 } },
    { loot_item: { spot: 'a', name: 'Rag', marketable: true, x: 1 } },
    { loot_item: { name: 'Rag', marketable: true } },
    { loot_forget: { spot: 'a' } }, { loot_forget: 'a' }
  ];
  for (const b of bad) assert.strictEqual(C.validGrindBody(b), false, JSON.stringify(b));
});

test('parseGrindForm: loot rows -> loot list; blank counts skipped', () => {
  const r = C.parseGrindForm('stop', { silver: '', trash: '', loot: [
    { name: 'Trash Pile', count: ' 2000 ' }, { name: 'Stone', count: '' }, { name: 'Gem', count: '0' }] });
  assert.deepStrictEqual(r, { ok: true, body: { stop: { silver: 0, trash: 0,
    loot: [{ name: 'Trash Pile', count: 2000 }] } } });
  assert.deepStrictEqual(C.parseGrindForm('stop', { silver: '5', trash: '', loot: [] }),
    { ok: true, body: { stop: { silver: 5, trash: 0 } } });
  assert.strictEqual(C.parseGrindForm('stop', { loot: [{ name: 'X', count: '1.5' }] }).ok, false);
  const l = C.parseGrindForm('log', { spot: 'a', minutes: '60', loot: [{ name: 'X', count: '3' }] });
  assert.deepStrictEqual(l.body.log.loot, [{ name: 'X', count: 3 }]);
  for (const b of [r.body, l.body]) assert.strictEqual(C.validGrindBody(b), true);
});

test('parseGrindForm: loot_item from strings', () => {
  assert.deepStrictEqual(C.parseGrindForm('loot_item', { spot: 'a', name: ' Rag ', vendor_price: '120', marketable: false, id: '' }),
    { ok: true, body: { loot_item: { spot: 'a', name: 'Rag', marketable: false, vendor_price: 120 } } });
  assert.deepStrictEqual(C.parseGrindForm('loot_item', { spot: 'a', name: 'Stone', vendor_price: '', marketable: true, id: '16001' }),
    { ok: true, body: { loot_item: { spot: 'a', name: 'Stone', marketable: true, id: 16001 } } });
  assert.strictEqual(C.parseGrindForm('loot_item', { spot: 'a', name: 'Rag', vendor_price: '', marketable: false }).ok, false);
  assert.strictEqual(C.parseGrindForm('loot_item', { spot: 'a', name: '', marketable: true }).ok, false);
  assert.strictEqual(C.parseGrindForm('loot_item', { spot: 'a', name: 'S', marketable: true, id: 'x' }).ok, false);
});

test('lootHintText / sessionSilver / trashPileText', () => {
  assert.strictEqual(C.lootHintText({ choice: 'vendor', diff: 55 }), 'vendor +55/u');
  assert.strictEqual(C.lootHintText({ choice: 'market', diff: 1500000 }), 'market +1.5M/u');
  assert.strictEqual(C.lootHintText({ choice: 'market', diff: null }), 'market');
  assert.strictEqual(C.lootHintText({ choice: 'either', diff: 0 }), 'either');
  assert.strictEqual(C.lootHintText({ choice: 'unknown' }), 'no price');
  assert.strictEqual(C.lootHintText(null), '');
  assert.strictEqual(C.sessionSilver({ silver: 7, valued_silver: 900 }), 900);
  assert.strictEqual(C.sessionSilver({ silver: 7 }), 7);
  assert.strictEqual(C.trashPileText({ loot_value: { trash: 2000000, unknown: [] } }), 'trash pile worth 2M');
  assert.strictEqual(C.trashPileText({ loot_value: { trash: 0, unknown: ['A'] } }), '1 item unpriced');
  assert.strictEqual(C.trashPileText({ loot_value: { trash: 5, unknown: ['A', 'B'] } }), 'trash pile worth 5, 2 items unpriced');
  assert.strictEqual(C.trashPileText({ loot_value: null }), '');
});

test('Grind tab reads /api/grind/loot and builds nodes with DOM APIs', () => {
  const src = read('dashboard/grind.js');
  assert.ok(src.includes('/api/grind/loot?spot='));
  assert.ok(src.includes('encodeURIComponent'));
  assert.ok(src.includes('C.lootHintText'));
  assert.ok(src.includes('C.sessionSilver'));
  assert.ok(!/innerHTML|insertAdjacentHTML|outerHTML/.test(src));
});
