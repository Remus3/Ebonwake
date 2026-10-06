'use strict';
// Plan 066: level / gear / book-use rows and silver/h in the auto-OCR card
// helpers (ewcore.js). No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

test('normalizeOcrAuto: level, gear and book_use rows; bad shapes dropped', () => {
  const a = C.normalizeOcrAuto({
    review: [
      { id: 'r1', kind: 'level', name: 'level', value: { level: 61, pct: 12 }, conf: 0.8 },
      { id: 'r2', kind: 'gear', name: 'ap', value: 296, conf: 0.85 },
      { id: 'r3', kind: 'book_use', name: 'large', value: { size: 'large', pct_before: 10, pct_after: 17.6 }, conf: 0.99 },
      { id: 'r4', kind: 'level', value: { level: 99, pct: 1 } },
      { id: 'r5', kind: 'level', value: { level: 61, pct: 1.2345 } },
      { id: 'r6', kind: 'level', value: 61 },
      { id: 'r7', kind: 'book_use', value: { size: 'large', pct_before: 10 } }
    ],
    commits: [{ id: 'u1', kind: 'level', name: 'level', value: { level: 62, pct: 0.4 }, via: 'auto' }],
    silver_h: { session: 'p2', per_h: 10000000, span_s: 1800, n: 2 }
  });
  assert.deepStrictEqual(a.review.map((r) => r.id), ['r1', 'r2', 'r3']);
  assert.strictEqual(C.ocrAutoLabel(a.review[0]), 'Lv 61 12% (80%)');
  assert.strictEqual(C.ocrAutoLabel(a.review[1]), 'AP 296 (85%)');
  assert.strictEqual(C.ocrAutoLabel(a.review[2]), 'large book used? 10% -> 17.6% (99%)');
  assert.strictEqual(C.ocrAutoLabel(a.commits[0]), 'Lv 62 0.4%');
  assert.strictEqual(C.ocrSilverH(a), 'silver/h this session: 10,000,000 (2 shots over 30m)');
  assert.strictEqual(C.ocrSilverH(C.normalizeOcrAuto({ silver_h: { per_h: 1, span_s: 0, n: 2 } })), '');
  assert.strictEqual(C.ocrSilverH(C.normalizeOcrAuto({})), '');
});

test('ocrFixBody: level and gear fixes; a book suggestion has no fix', () => {
  assert.deepStrictEqual(C.ocrFixBody('r1', 'level', '61 12.345'),
    { review: { id: 'r1', action: 'fix', value: { level: 61, pct: 12.345 } } });
  assert.deepStrictEqual(C.ocrFixBody('r1', 'level', 'Lv 62 0.4%'),
    { review: { id: 'r1', action: 'fix', value: { level: 62, pct: 0.4 } } });
  for (const t of ['61', '99 1', '61 101', '61 1.2345', 'abc', '']) {
    assert.strictEqual(C.ocrFixBody('r1', 'level', t), null, t);
  }
  assert.deepStrictEqual(C.ocrFixBody('r2', 'gear', '296'), { review: { id: 'r2', action: 'fix', value: 296 } });
  assert.strictEqual(C.ocrFixBody('r2', 'gear', '1000'), null);
  assert.strictEqual(C.ocrFixBody('r3', 'book_use', '1'), null);
  assert.strictEqual(C.ocrFixHint('book_use'), '');
  assert.ok(C.ocrFixHint('level') && C.ocrFixHint('gear'));
  assert.strictEqual(C.validOcrBody({ review: { id: 'r1', action: 'fix', value: { level: 61, pct: 5, x: 1 } } }), false);
});

test('normalizeLeveling passes level_source ocr', () => {
  const d = C.normalizeLeveling({
    now: '2026-10-05T12:00:00+00:00', level: 61, pct: 12.5, level_source: 'ocr', rate_pct_h: 1,
    eta_next_s: 1000, next_milestone: 70, xp_stack_pct: 0, xp_parts: [], hot: { active: [], next: null },
    milestones: [70], milestones_seed: false, hot_windows: [], samples: []
  });
  assert.strictEqual(d.level_source, 'ocr');
});

test('game.js: fix input only where the kind has a fix hint; silver/h line', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'dashboard', 'game.js'), 'utf8');
  assert.match(src, /C\.ocrFixHint\(/);
  assert.match(src, /C\.ocrSilverH\(/);
});
