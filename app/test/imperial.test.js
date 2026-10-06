'use strict';
// Plan 053: Imperial delivery card helpers (ewcore.js) over GET /api/imperial,
// the /api/imperial bridge guard, and static guards on the Today module.
// No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const APP = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(APP, p), 'utf8');
const NOW = Date.parse('2026-10-05T22:30:00Z');

function view(o) {
  return Object.assign({
    day: '2026-10-05', reset: { next_utc: '2026-10-06T00:00:00+00:00', left_s: 5400 },
    cp: { value: 301, source: 'profile', typed: null },
    types: [
      { type: 'cooking', cap: 150, delivered: 40, left: 110, done: false, mastery_pct: 20 },
      { type: 'alchemy', cap: 150, delivered: 150, left: 0, done: true, mastery_pct: 0 }
    ],
    boxes: [
      { id: 'cooking-a', type: 'cooking', name: 'A', payout: 50000, cost: 20000, ratio: 2.5, missing: [] },
      { id: 'cooking-b', type: 'cooking', name: 'B', payout: 1250000, cost: 400000, ratio: 3.125, missing: [] },
      { id: 'alchemy-c', type: 'alchemy', name: 'C', payout: null, cost: null, ratio: null, missing: [5961, 5962] }
    ],
    best: { cooking: ['cooking-b', 'cooking-a'], alchemy: [] },
    data_error: null
  }, o || {});
}

test('imperialRows: left / cap per type, done at zero, mastery carried', () => {
  const r = C.imperialRows(view());
  assert.deepStrictEqual(r.map((x) => [x.label, x.text, x.done, x.mastery]), [
    ['Cooking', '110 / 150 left', false, 20], ['Alchemy', '0 / 150 left', true, 0]]);
});

test('imperialRows: unknown CP asks for it; junk rows dropped', () => {
  const v = view({ types: [{ type: 'cooking', cap: null, delivered: 3 }, { type: 'fishing', delivered: 1 }, null, { type: 'alchemy' }] });
  const r = C.imperialRows(v);
  assert.strictEqual(r.length, 1);
  assert.strictEqual(r[0].text, '3 delivered (set CP for the cap)');
  assert.strictEqual(r[0].left, null);
  assert.strictEqual(r[0].done, false);
  assert.deepStrictEqual(C.imperialRows(null), []);
});

test('imperialReset: live countdown from next_utc; empty without it', () => {
  assert.strictEqual(C.imperialReset(view(), NOW), 'resets in 1h 30m');
  assert.strictEqual(C.imperialReset(view(), Date.parse('2026-10-05T23:59:30Z')), 'resets in 0m 30s');
  assert.strictEqual(C.imperialReset(view({ reset: null }), NOW), '');
  assert.strictEqual(C.imperialReset(view({ reset: { next_utc: 'soon' } }), NOW), '');
});

test('imperialCpText: profile vs typed vs unknown', () => {
  assert.strictEqual(C.imperialCpText(view()), 'CP 301');
  assert.strictEqual(C.imperialCpText(view({ cp: { value: 80, source: 'operator', typed: 80 } })), 'CP 80 (typed)');
  assert.strictEqual(C.imperialCpText(view({ cp: { value: null } })), 'CP ?');
});

test('imperialBest: server order, payout for cost, unpriced skipped', () => {
  assert.deepStrictEqual(C.imperialBest(view(), 'cooking').map((b) => [b.name, b.text]), [
    ['B', '1.25M for 400K (x3.125)'], ['A', '50K for 20K (x2.5)']]);
  assert.deepStrictEqual(C.imperialBest(view({ best: { alchemy: ['alchemy-c'] } }), 'alchemy'), []);
  assert.deepStrictEqual(C.imperialBest(null, 'cooking'), []);
  assert.deepStrictEqual(C.imperialUnpriced(view()), ['C: no cached price for 5961, 5962']);
});

test('parseImperialBox / parseImperialCp: form text -> exact bodies', () => {
  const p = C.parseImperialBox({ type: 'cooking', name: ' Beer box ', items: '9213 x 10, 9214*2' });
  assert.deepStrictEqual(p, { ok: true, body: { box_add: { type: 'cooking', name: 'Beer box',
    items: [{ id: 9213, qty: 10 }, { id: 9214, qty: 2 }] } } });
  assert.strictEqual(C.validPost('/api/imperial', p.body), true);
  [{ type: 'fishing', name: 'x', items: '1 x 1' }, { type: 'cooking', name: '', items: '1 x 1' },
    { type: 'cooking', name: 'x', items: '' }, { type: 'cooking', name: 'x', items: '1 x 0' },
    { type: 'cooking', name: 'x', items: '1 x 1, 1 x 2' }, { type: 'cooking', name: 'x', items: 'beer x 1' }]
    .forEach((f) => assert.strictEqual(C.parseImperialBox(f).ok, false, JSON.stringify(f)));
  assert.deepStrictEqual(C.parseImperialCp(' 1,200 '), { ok: true, body: { cp: 1200 } });
  assert.deepStrictEqual(C.parseImperialCp(''), { ok: true, body: { cp: null } });
  assert.strictEqual(C.parseImperialCp('-1').ok, false);
  assert.strictEqual(C.parseImperialCp('10001').ok, false);
});

test('validPost: /api/imperial exact bodies only', () => {
  assert.ok(C.POST_ROUTES.indexOf('/api/imperial') >= 0);
  const ok = [{ deliver: { type: 'cooking', add: 1 } }, { deliver: { type: 'alchemy', add: -10 } },
    { cp: 300 }, { cp: null }, { mastery: { type: 'cooking', pct: 12.5 } },
    { box_add: { type: 'alchemy', name: 'X', items: [{ id: 1, qty: 1 }] } }, { box_del: 'cooking-beer-box' }];
  for (const b of ok) assert.strictEqual(C.validPost('/api/imperial', b), true, JSON.stringify(b));
  const bad = [{}, { deliver: { type: 'cooking', add: 0 } }, { deliver: { type: 'cooking', add: 1.5 } },
    { deliver: { type: 'x', add: 1 } }, { deliver: { type: 'cooking' } }, { cp: -1 }, { cp: 10001 }, { cp: '3' },
    { mastery: { type: 'cooking', pct: 501 } }, { mastery: { type: 'cooking' } },
    { box_add: { type: 'cooking', name: 'X', items: [] } },
    { box_add: { type: 'cooking', name: 'X', items: [{ id: 1, qty: 1, x: 1 }] } },
    { box_del: 'beer' }, { cp: 1, box_del: 'cooking-a' }];
  for (const b of bad) assert.strictEqual(C.validPost('/api/imperial', b), false, JSON.stringify(b));
  assert.strictEqual(C.postToast('/api/imperial', { ok: true }).text, 'Imperial delivery saved');
});

test('Today card: imperial.js mounted by today.js, posts via the bridge, safe DOM', () => {
  const src = read('dashboard/imperial.js');
  assert.match(src, /window\.EWImperial\s*=/);
  assert.match(src, /getJSON\('\/api\/imperial'\)/);
  assert.match(src, /\.post\('\/api\/imperial'/);
  for (const f of ['imperialRows', 'imperialReset', 'imperialBest', 'parseImperialBox', 'parseImperialCp']) {
    assert.ok(src.indexOf('C.' + f + '(') >= 0, f);
  }
  assert.doesNotMatch(src, /innerHTML|outerHTML|insertAdjacentHTML|document\.write|method:\s*'POST'/);
  assert.match(read('dashboard/today.js'), /EWImperial\.mount\(/);
  const html = read('dashboard/index.html');
  const i = html.indexOf('<script src="imperial.js"></script>');
  assert.ok(i > html.indexOf('ewcore.js') && i < html.indexOf('<script src="today.js"></script>'));
  assert.match(read('preload.js'), /\/api\/imperial/);
  assert.ok(read('overlay/overlay.js').indexOf('/api/imperial') < 0);
});
