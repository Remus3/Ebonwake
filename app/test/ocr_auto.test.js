'use strict';
// Plan 063: auto-OCR review card helpers (ewcore.js) and the System card wiring
// in dashboard/game.js. No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const read = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');

test('validOcrBody: review accept / discard / fix and undo shapes', () => {
  for (const ok of [
    { review: { id: 'r1', action: 'accept' } }, { review: { id: 'r12', action: 'discard' } },
    { review: { id: 'r3', action: 'fix', value: 1234567 } }, { undo: 'u4' }
  ]) {
    assert.strictEqual(C.validOcrBody(ok), true, JSON.stringify(ok));
    assert.strictEqual(C.validPost('/api/ocr', ok), true, JSON.stringify(ok));
  }
  for (const bad of [
    { review: null }, { review: { id: 'r1' } }, { review: { id: 'x1', action: 'accept' } },
    { review: { id: 'r1', action: 'eat' } }, { review: { id: 'r1', action: 'accept', value: 1 } },
    { review: { id: 'r1', action: 'fix' } }, { review: { id: 'r1', action: 'fix', value: -1 } },
    { review: { id: 'r1', action: 'fix', value: 1.5 } }, { review: { id: 'r1', action: 'fix', value: '5' } },
    { undo: 'r1' }, { undo: 5 }, { undo: 'u1', review: { id: 'r1', action: 'accept' } }
  ]) {
    assert.strictEqual(C.validOcrBody(bad), false, JSON.stringify(bad));
  }
});

test('normalizeOcrAuto: defensive over GET /api/ocr/auto', () => {
  for (const bad of [null, undefined, [], 'x', 5]) assert.strictEqual(C.normalizeOcrAuto(bad), null);
  const a = C.normalizeOcrAuto({
    enabled: true, daily_cap: 120, today: 3, pending: 1,
    review: [
      { id: 'r2', file: 'a.jpg', kind: 'silver', name: 'silver', value: 1234567, conf: 0.8, why: 'groups split by spaces' },
      { id: 'bad', kind: 'silver', value: 1 }, { id: 'r3', kind: 'loot', value: 1 }, null,
      { id: 'r4', file: 'b.jpg', kind: 'buff', name: 'Value Pack', value: 195, conf: 2 }
    ],
    commits: [{ id: 'u1', file: 'c.jpg', kind: 'buff', name: 'XP scroll', value: 30, via: 'auto' }]
  });
  assert.deepStrictEqual(a.review.map((r) => r.id), ['r2', 'r4']);
  assert.strictEqual(a.review[1].conf, null);
  assert.strictEqual(a.commits[0].via, 'auto');
  assert.strictEqual(C.ocrAutoHead(a), '2 to review - 3/120 read today');
  assert.strictEqual(C.ocrAutoHead(C.normalizeOcrAuto({ enabled: false })), 'nothing to review - 0 read today (auto off)');
  assert.strictEqual(C.ocrAutoLabel(a.review[0]), 'silver 1,234,567 (80%)');
  assert.strictEqual(C.ocrAutoLabel(a.commits[0]), 'XP scroll 30m');
});

test('ocrFixBody: typed digits -> a valid fix body', () => {
  assert.deepStrictEqual(C.ocrFixBody('r1', 'silver', '1,234,567'),
    { review: { id: 'r1', action: 'fix', value: 1234567 } });
  assert.deepStrictEqual(C.ocrFixBody('r1', 'buff', ' 45 '), { review: { id: 'r1', action: 'fix', value: 45 } });
  for (const [k, t] of [['silver', ''], ['silver', 'abc'], ['buff', '0'], ['buff', '43201'], ['silver', '-5']]) {
    assert.strictEqual(C.ocrFixBody('r1', k, t), null, k + ' ' + t);
  }
});

test('game.js: auto-OCR card reads /api/ocr/auto, posts over the bridge, listens to ocr', () => {
  const src = read('dashboard/game.js');
  assert.match(src, /'\/api\/ocr\/auto'/);
  assert.match(src, /\.on\('ocr'/);
  for (const w of ['accept', 'fix', 'discard', 'undo']) assert.match(src, new RegExp("'" + w + "'"), w);
  for (const f of ['normalizeOcrAuto', 'ocrAutoLabel', 'ocrAutoHead', 'ocrFixBody']) {
    assert.match(src, new RegExp('C\\.' + f + '\\('), f);
  }
  assert.doesNotMatch(src, /innerHTML|outerHTML|insertAdjacentHTML|document\.write/);
  assert.match(read('dashboard/dashboard.js'), /'ocr'\]/);
});
