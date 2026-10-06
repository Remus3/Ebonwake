'use strict';
// Plan 040: OCR loot-window import - the {loot: {shot, spot}} body guard, the
// reply normaliser, the count pre-fill helpers (ewcore.js) and static guards on
// the Grind tab's "Import from screenshot" button. No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const read = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');

test('validOcrBody: {loot: {shot, spot}} alongside {file}', () => {
  const ok = { loot: { shot: 'ScreenShot_2026-10-05_120000.jpg', spot: 'polly-forest' } };
  assert.strictEqual(C.validOcrBody(ok), true);
  assert.strictEqual(C.validPost('/api/ocr', ok), true);
  assert.strictEqual(C.validOcrBody({ file: 'a.jpg' }), true);
  for (const bad of [
    { loot: null }, { loot: 'a.jpg' }, { loot: { shot: 'a.jpg' } }, { loot: { spot: 'x' } },
    { loot: { shot: 'a.jpg', spot: 'x', extra: 1 } }, { loot: { shot: '../a.jpg', spot: 'x' } },
    { loot: { shot: 'a.jpg', spot: 'Polly Forest' } }, { loot: { shot: 'a.jpg', spot: '' } },
    { loot: { shot: 'a.jpg', spot: 'x'.repeat(41) } }, { loot: { shot: 'a.jpg', spot: 3 } },
    { loot: { shot: 'a.jpg', spot: 'x' }, file: 'a.jpg' }
  ]) {
    assert.strictEqual(C.validOcrBody(bad), false, JSON.stringify(bad));
  }
});

test('normalizeLootOcr: defensive over the server reply', () => {
  for (const bad of [null, undefined, [], 'x', 5]) assert.strictEqual(C.normalizeLootOcr(bad), null, String(bad));
  assert.deepStrictEqual(C.normalizeLootOcr({}), { shot: '', rows: [], unmatched: [] });
  const r = C.normalizeLootOcr({
    shot: 'a.jpg',
    rows: [
      { name: 'Forest Fury', count: 1200, confidence: 1 },
      { name: 'Black Stone (Weapon)', count: null, confidence: 0.83 },
      { name: 'forest fury', count: 3, confidence: 1 },
      { name: 'Dust', count: 0, confidence: 2 }, { name: '', count: 1 }, null, 'x',
      { name: 'Big', count: 1e8, confidence: 1 }
    ],
    unmatched: ['Pirate Gold Coin x3', 5, '', 'y'.repeat(300)]
  });
  assert.deepStrictEqual(r.rows, [
    { name: 'Forest Fury', count: 1200, confidence: 1 },
    { name: 'Black Stone (Weapon)', count: null, confidence: 0.83 },
    { name: 'Dust', count: null, confidence: 0 },
    { name: 'Big', count: null, confidence: 1 }
  ]);
  assert.deepStrictEqual(r.unmatched, ['Pirate Gold Coin x3', 'y'.repeat(200)]);
});

test('lootImportCounts: counts keyed by the spot item names, fuzzy rows flagged', () => {
  const imp = C.lootImportCounts([
    { name: 'forest fury', count: 1200, confidence: 1 },
    { name: 'Black Stone (Weapon)', count: 9, confidence: 0.83 },
    { name: 'Caphras Stone', count: null, confidence: 1 },
    { name: 'Gone Item', count: 4, confidence: 1 }
  ], ['Forest Fury', 'Black Stone (Weapon)', 'Caphras Stone']);
  assert.deepStrictEqual(imp.counts, { 'Forest Fury': '1200', 'Black Stone (Weapon)': '9' });
  assert.deepStrictEqual(imp.low, ['Black Stone (Weapon)']);
  assert.strictEqual(imp.filled, 2);
  assert.deepStrictEqual(imp.missing, ['Caphras Stone', 'Gone Item']);
  const body = C.parseGrindForm('stop', { silver: '', trash: '', loot: Object.keys(imp.counts)
    .map(function (n) { return { name: n, count: imp.counts[n] }; }) }).body;
  assert.strictEqual(C.validGrindBody(body), true);
  assert.deepStrictEqual(C.lootImportCounts(null, null), { counts: {}, low: [], filled: 0, missing: [] });
});

test('lootImportText: one status line', () => {
  const imp = { counts: {}, low: ['A'], filled: 2, missing: ['B'] };
  assert.strictEqual(C.lootImportText('s.jpg', imp, ['x']),
    'filled 2 from s.jpg - check, then Stop + log; check ? rows; no count: B; 1 line(s) not on this list');
  assert.strictEqual(C.lootImportText('s.jpg', { counts: {}, low: [], filled: 0, missing: [] }, []),
    'no loot counts read from s.jpg');
});

test('grind.js: Import from screenshot posts {loot} on the bridge and never logs by itself', () => {
  const src = read('dashboard/grind.js');
  assert.match(src, /Import from screenshot/);
  assert.match(src, /window\.EWToast\.via\(b\)\.post\('\/api\/ocr', body\)/);
  assert.match(src, /\{ loot: \{ shot: shot, spot: spot \} \}/);
  for (const f of ['normalizeGame', 'validOcrBody', 'normalizeLootOcr', 'lootImportCounts', 'lootImportText']) {
    assert.match(src, new RegExp('C\\.' + f + '\\('), f);
  }
  const fn = src.slice(src.indexOf('function importShot'), src.indexOf('function addLootItem'));
  assert.ok(fn.length > 0);
  assert.doesNotMatch(fn, /send\(|\/api\/grind/, 'import only pre-fills; Stop + log stays the operator act');
  assert.doesNotMatch(src, /innerHTML|outerHTML|insertAdjacentHTML/);
});
