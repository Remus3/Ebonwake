'use strict';
// Plan 060: Combat Secret Book helpers (ewcore.js), the /api/leveling book ops
// guard and static guards on the Leveling card. No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const APP = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(APP, p), 'utf8');

const per = (pct, flag) => ({ pct: pct, observed: !flag, uses: flag ? 0 : 1, flag: flag || null });
const BOOKS = {
  available: true, min_level: 60, error: null,
  owned: { small: 0, medium: 0, large: 3, xl: 0 },
  per_book: { small: per(0.2, 'Lv 66 value, verify'), medium: per(1, 'Lv 66 value, verify'),
    large: per(7.5, 'Lv 66 value, verify'), xl: per(15, 'Lv 66 value, verify') },
  flagged: true, owned_pct: 22.5, to_next: { small: 300, medium: 60, large: 8, xl: 4 },
  weekly: { rows: [], logged: [], pct_week: 1 }, pct_week: 1, books_pct_h: 0.005952,
  rate_with_books_pct_h: 2.006, eta_next_with_books_s: 107678,
  sizes: [{ id: 'small', name: 'Small' }, { id: 'large', name: 'Large' }, { id: 'huge', name: 'x' }],
  activities: [{ activity: 'black-shrine', name: 'Black Shrine' }, { activity: 'Bad', name: 'x' }],
  used: [{ index: 0, size: 'large', gain: 7.5, level: 66 }, { index: 'x', size: 'large', gain: 1 }]
};

test('validLevelingBody: book ops', () => {
  const ok = [
    { book_add: { size: 'large', n: 3 } }, { book_add: { size: 'xl', n: -1, activity: 'war-of-roses' } },
    { book_add: { size: 'small', n: 99 } },
    { book_use: { size: 'large', pct_before: 40, pct_after: 47.5 } },
    { book_use: { size: 'small', pct_before: 99.9, pct_after: 0.1 } },
    { book_del: 0 }, { book_del: 12 }
  ];
  for (const b of ok) assert.strictEqual(C.validLevelingBody(b), true, JSON.stringify(b));
  const bad = [
    { book_add: { size: 'huge', n: 1 } }, { book_add: { size: 'small', n: 0 } },
    { book_add: { size: 'small', n: 100 } }, { book_add: { size: 'small', n: 1.5 } },
    { book_add: { size: 'small' } }, { book_add: { size: 'small', n: 1, x: 1 } },
    { book_add: { size: 'small', n: 1, activity: 'Bad Id' } }, { book_add: null },
    { book_use: { size: 'large', pct_before: 40, pct_after: 40 } },
    { book_use: { size: 'large', pct_before: 40, pct_after: 101 } },
    { book_use: { size: 'large', pct_before: 40 } },
    { book_use: { size: 'large', pct_before: 40, pct_after: 41, x: 1 } },
    { book_del: -1 }, { book_del: 1.5 }, { book_del: '0' }, { book_del: true }
  ];
  for (const b of bad) assert.strictEqual(C.validLevelingBody(b), false, JSON.stringify(b));
});

test('normalizeBooks keeps a good body and drops junk', () => {
  const n = C.normalizeBooks(BOOKS);
  assert.strictEqual(n.available, true);
  assert.deepStrictEqual(n.owned, { small: 0, medium: 0, large: 3, xl: 0 });
  assert.strictEqual(n.owned_pct, 22.5);
  assert.strictEqual(n.to_next.large, 8);
  assert.deepStrictEqual(n.sizes.map((s) => s.id), ['small', 'large']);
  assert.deepStrictEqual(n.activities.map((a) => a.activity), ['black-shrine']);
  assert.strictEqual(n.used.length, 1);
  assert.strictEqual(C.normalizeBooks(null), null);
  assert.strictEqual(C.normalizeBooks({}), null);
  const junk = C.normalizeBooks({ available: true, owned: { large: -2 }, to_next: null });
  assert.strictEqual(junk.owned.large, 0);
  assert.strictEqual(junk.to_next, null);
  // a normalized body normalizes to itself
  assert.deepStrictEqual(C.normalizeBooks(n), n);
});

test('normalizeLeveling carries books; null on a pre-060 server', () => {
  assert.strictEqual(C.normalizeLeveling({ milestones: [] }).books, null);
  assert.strictEqual(C.normalizeLeveling({ milestones: [], books: BOOKS }).books.owned_pct, 22.5);
});

test('booksLine: acceptance fixture, observed, below Lv 60', () => {
  assert.strictEqual(C.booksLine(BOOKS), 'Books: +22.5 % owned, +1 %/week expected (Lv 66 values, verify)');
  const obs = Object.assign({}, BOOKS, { per_book: { small: per(0.2), medium: per(1), large: per(6.5), xl: per(15) },
    owned_pct: 19.5, pct_week: 1.25 });
  assert.strictEqual(C.booksLine(obs), 'Books: +19.5 % owned, +1.25 %/week expected');
  const low = { available: false, min_level: 60, error: null,
    deadline: { label: 'Olvia Academy', needs_level: 60, enrol_by_utc: '2026-11-05T00:00:00+00:00' } };
  assert.strictEqual(C.booksLine(low), 'books from Lv 60 - Olvia Academy Lv 60 by Nov 5');
  assert.strictEqual(C.booksLine({ available: false, min_level: 60, deadline: null }), 'books from Lv 60');
  assert.strictEqual(C.booksLine({ available: false, error: 'bad file' }), 'book table: bad file');
  assert.strictEqual(C.booksLine(null), '');
});

test('booksToNextText: largest first, blank without a pct', () => {
  assert.strictEqual(C.booksToNextText(BOOKS), 'to next: 4 XL / 8 L / 60 M / 300 S');
  assert.strictEqual(C.booksToNextText(Object.assign({}, BOOKS, { to_next: null })), '');
  assert.strictEqual(C.booksToNextText({ available: false }), '');
});

test('parseBookForm: add and use', () => {
  assert.deepStrictEqual(C.parseBookForm({ op: 'add', size: 'large', n: '3' }),
    { ok: true, body: { book_add: { size: 'large', n: 3 } } });
  assert.deepStrictEqual(C.parseBookForm({ op: 'add', size: 'medium', n: ' 2 ', activity: 'guild-boss' }),
    { ok: true, body: { book_add: { size: 'medium', n: 2, activity: 'guild-boss' } } });
  assert.deepStrictEqual(C.parseBookForm({ op: 'add', size: 'small', n: '-1', activity: '' }),
    { ok: true, body: { book_add: { size: 'small', n: -1 } } });
  assert.deepStrictEqual(C.parseBookForm({ op: 'use', size: 'large', before: '40', after: '47,5%' }),
    { ok: true, body: { book_use: { size: 'large', pct_before: 40, pct_after: 47.5 } } });
  for (const f of [{ op: 'add', size: 'huge', n: '1' }, { op: 'add', size: 'small', n: '0' },
    { op: 'add', size: 'small', n: '100' }, { op: 'add', size: 'small', n: 'x' },
    { op: 'add', size: 'small', n: '1', activity: 'Bad Id' },
    { op: 'use', size: 'large', before: '40', after: '40' },
    { op: 'use', size: 'large', before: '40', after: '101' }, null]) {
    assert.strictEqual(C.parseBookForm(f).ok, false, JSON.stringify(f));
  }
  // every parsed body passes the IPC guard
  const r = C.parseBookForm({ op: 'add', size: 'xl', n: '1', activity: 'war-of-roses' });
  assert.strictEqual(C.validLevelingBody(r.body), true);
});

test('leveling card renders the books line, with-books ETA and book editor', () => {
  const src = read('dashboard/leveling.js');
  assert.match(src, /C\.booksLine\(/);
  assert.match(src, /C\.booksToNextText\(/);
  assert.match(src, /C\.parseBookForm\(/);
  assert.match(src, /eta_next_with_books_s/);
  assert.match(src, /book_del/);
  assert.doesNotMatch(src, /innerHTML|outerHTML|insertAdjacentHTML|document\.write/);
});
