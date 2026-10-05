'use strict';
// Plan 009 slice B: OCR helpers (ewcore.js), the per-screenshot "Read" button
// and result panel on the System tab game card (dashboard/game.js), and the
// "use silver" / "arm buff" hand-off to the existing grind routes. Static
// guards keep the card safe-DOM and its POSTs on the bridge. No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const APP = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(APP, p), 'utf8');

test('validOcrBody: exactly {file: plain screenshot name}', () => {
  assert.strictEqual(C.validOcrBody({ file: 'ScreenShot_2026-10-05_120000.jpg' }), true);
  assert.strictEqual(C.validOcrBody({ file: 'a b (1).png' }), true);
  assert.strictEqual(C.validOcrBody({ file: 'x'.repeat(C.OCR_NAME_MAX) }), true);
  for (const bad of [
    null, undefined, 'a.jpg', [], {}, { file: '' }, { file: '   ' }, { file: 5 }, { file: null },
    { file: 'x'.repeat(C.OCR_NAME_MAX + 1) }, { file: 'a.jpg', extra: 1 }, { name: 'a.jpg' },
    { file: '../a.jpg' }, { file: 'dir/a.jpg' }, { file: 'dir\\a.jpg' }, { file: '.' }, { file: '..' },
    { file: 'C:a.jpg' }, { file: 'a\u0000.jpg' }, { file: 'a\n.jpg' }
  ]) {
    assert.strictEqual(C.validOcrBody(bad), false, JSON.stringify(bad));
  }
});

test('validPost allowlist carries /api/ocr with its own validator; preload names it', () => {
  assert.ok(C.POST_ROUTES.indexOf('/api/ocr') >= 0);
  assert.strictEqual(C.validPost('/api/ocr', { file: 'a.jpg' }), true);
  assert.strictEqual(C.validPost('/api/ocr', { start: 'gyfin' }), false);
  assert.strictEqual(C.validPost('/api/grind', { file: 'a.jpg' }), false);
  assert.strictEqual(C.validPost('/api/ocr/', { file: 'a.jpg' }), false);
  for (const r of ['/api/grind', '/api/market/watch', '/api/today', '/api/progress', '/api/events', '/api/deadeye']) {
    assert.ok(C.POST_ROUTES.indexOf(r) >= 0, r + ' kept');
  }
  assert.match(read('preload.js'), /\/api\/ocr/);
});

test('normalizeOcr: defensive over the slice A reply', () => {
  for (const bad of [null, undefined, [], 'x', 5]) assert.strictEqual(C.normalizeOcr(bad), null, String(bad));
  assert.deepStrictEqual(C.normalizeOcr({}), { text: '', silver: null, buffs: [] });
  const r = C.normalizeOcr({
    text: 'Silver 1,234,567\r\nXP scroll 29:00',
    silver: 1234567,
    buffs: [
      { name: 'XP scroll', minutes: 29 },
      { name: 'Hot Time', minutes: 0 },
      { name: 'Old Moon book', minutes: 12.6 },
      { name: '', minutes: 5 }, { name: 5, minutes: 5 }, null, 'x',
      { name: 'Value Pack', minutes: 99999 }, { name: 'Drop rate scroll' },
      { name: 'xp SCROLL', minutes: 3 }
    ]
  });
  assert.strictEqual(r.text, 'Silver 1,234,567\nXP scroll 29:00');
  assert.strictEqual(r.silver, 1234567);
  assert.deepStrictEqual(r.buffs, [
    { name: 'XP scroll', minutes: 29 },
    { name: 'Old Moon book', minutes: 13 },
    { name: 'Value Pack', minutes: 43200 }
  ], 'zero / missing minutes and junk dropped, fractions rounded, over-range clamped, one row per name');
});

test('normalizeOcr: silver only as a whole number in the grind range; text capped', () => {
  for (const bad of [-1, 1.5, '123', NaN, Infinity, 1e14, true, null]) {
    assert.strictEqual(C.normalizeOcr({ silver: bad }).silver, null, String(bad));
  }
  assert.strictEqual(C.normalizeOcr({ silver: 0 }).silver, 0);
  assert.strictEqual(C.normalizeOcr({ text: 5 }).text, '');
  assert.strictEqual(C.normalizeOcr({ text: 'x'.repeat(C.OCR_TEXT_MAX + 50) }).text.length, C.OCR_TEXT_MAX);
});

test('ocrBuffBody: a grind buff POST body that the bridge accepts', () => {
  const b = C.ocrBuffBody({ name: 'XP scroll', minutes: 29 });
  assert.deepStrictEqual(b, { buff: { name: 'XP scroll', minutes: 29 } });
  assert.strictEqual(C.validPost('/api/grind', b), true);
  for (const bad of [null, {}, { name: 'x' }, { name: '', minutes: 5 }, { name: 'x', minutes: 0 }, { name: 'x', minutes: 43201 }]) {
    assert.strictEqual(C.ocrBuffBody(bad), null, JSON.stringify(bad));
  }
});

test('ocrSilverInput: plain digits for the stop form and the clipboard', () => {
  assert.strictEqual(C.ocrSilverInput(1234567), '1234567');
  assert.strictEqual(C.ocrSilverInput(0), '0');
  assert.strictEqual(C.ocrSilverInput(1e13), '10000000000000');
  assert.strictEqual(C.parseGrindForm('stop', { silver: C.ocrSilverInput(1234567), trash: '' }).body.stop.silver, 1234567);
  for (const bad of [null, -1, 1.5, '5']) assert.strictEqual(C.ocrSilverInput(bad), null, String(bad));
});

test('ocrBuffLabel: name and minutes for the result panel', () => {
  assert.strictEqual(C.ocrBuffLabel({ name: 'XP scroll', minutes: 29 }), 'XP scroll - 29m');
  assert.strictEqual(C.ocrBuffLabel({ name: 'Value Pack', minutes: 43200 }), 'Value Pack - 30d 0h');
  assert.strictEqual(C.ocrBuffLabel({ name: 'Hot Time', minutes: 90 }), 'Hot Time - 1h 30m');
});

test('game.js: Read button, result panel, bridge-only POSTs to /api/ocr and /api/grind', () => {
  const src = read('dashboard/game.js');
  assert.doesNotMatch(src, /innerHTML|outerHTML|insertAdjacentHTML|document\.write/);
  assert.doesNotMatch(src, /method:\s*['"]POST|fetch\([^)]*POST/i, 'no direct renderer POST');
  assert.match(src, /window\.ewApi/);
  assert.match(src, /\.post\('\/api\/ocr'/);
  assert.match(src, /\.post\('\/api\/grind'/);
  assert.doesNotMatch(src, /stop:\s*\{/, 'never posts a stop: silver is only pre-filled');
  assert.match(src, /'Read'/);
  assert.match(src, /use silver/);
  assert.match(src, /arm buff/);
  assert.match(src, /createElement\('pre'\)|el\('pre'/);
  assert.match(src, /navigator\.clipboard/);
  assert.match(src, /EWGrind/);
  for (const f of ['normalizeOcr', 'ocrBuffBody', 'ocrSilverInput', 'ocrBuffLabel', 'validOcrBody', 'fmtSilver']) {
    assert.match(src, new RegExp('C\\.' + f + '\\('), f);
  }
});

test('grind.js: prefillSilver fills the stop form only while a session runs', () => {
  const src = read('dashboard/grind.js');
  assert.match(src, /prefillSilver/);
  assert.match(src, /window\.EWGrind\s*=\s*\{[^}]*prefillSilver/);
  assert.doesNotMatch(src, /innerHTML|outerHTML|insertAdjacentHTML/);
});

test('CSS styles the OCR panel as a scrolling pre', () => {
  const css = read('shared/ew.css');
  assert.match(css, /\.ew-game \.ew-ocr/);
  assert.match(css, /\.ew-ocrtext[^}]*overflow:\s*auto/);
});
