'use strict';
// Plan 079: override ledger badges. overrideBadge / overrideBadges /
// overrideRows / overridePill (ewcore.js) turn /api/overrides (and the
// signals digest `overrides` section) into card-header badges, Signal health
// rows and the Home `N overrides` pill. Static guards on the card wiring.
// No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const APP = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(APP, p), 'utf8');

const VP = { key: 'market.vp', label: 'Value Pack (typed)', value: true, source: 'typed',
  set_at: '2026-10-06T12:00:00+00:00', expires_at: '2026-11-05T12:00:00+00:00',
  expires_in_s: 30 * 86400, reason: 'typed in Settings', cards: ['grind', 'crafting', 'inventory'],
  clearable: true };
const GEAR = { key: 'gear.ap', label: 'Gear AP (typed)', value: 280, source: 'typed',
  set_at: '2026-10-01T00:00:00+00:00', expires_at: null, expires_in_s: null,
  reason: 'the next OCR read supersedes it', cards: ['progress'], clearable: false };

test('overrideBadge: text, class and a title naming value, source, set-at and expiry', () => {
  const b = C.overrideBadge(VP);
  assert.strictEqual(b.cls, 'ew-ovr');
  assert.strictEqual(b.text, 'override');
  assert.match(b.title, /^Value Pack \(typed\) = on \(typed, set 2026-10-06\); expires in /);
  assert.strictEqual(b.key, 'market.vp');
  assert.strictEqual(b.clearable, true);
  const cfg = C.overrideBadge(Object.assign({}, VP, { source: 'config' }));
  assert.match(cfg.title, /from config\/local\.json/);
  const g = C.overrideBadge(GEAR);
  assert.match(g.title, /= 280 .*; the next OCR read supersedes it$/);
  assert.strictEqual(g.clearable, false);
  const w = C.overrideBadge({ key: 'market.watch.5.0', value: { below: 100, above: null } });
  assert.match(w.title, /^market\.watch\.5\.0 = below 100 /);
  assert.strictEqual(C.overrideBadge(null), null);
  assert.strictEqual(C.overrideBadge({ value: 1 }), null);
});

test('overrideBadges: only the cards a policy lists; none when the list is empty', () => {
  const doc = { count: 2, items: [VP, GEAR, 'junk'] };
  assert.strictEqual(C.overrideBadges(doc, 'grind').length, 1);
  assert.strictEqual(C.overrideBadges(doc, 'inventory')[0].key, 'market.vp');
  assert.strictEqual(C.overrideBadges(doc, 'progress')[0].key, 'gear.ap');
  assert.deepStrictEqual(C.overrideBadges(doc, 'events'), []);
  assert.deepStrictEqual(C.overrideBadges({ count: 0, items: [] }, 'grind'), []);
  assert.deepStrictEqual(C.overrideBadges(null, 'grind'), []);
});

test('overrideRows + overridePill: Signal health rows and the Home status line', () => {
  const rows = C.overrideRows({ items: [VP, GEAR] });
  assert.deepStrictEqual(rows.map((r) => r.text), ['Value Pack (typed): on', 'Gear AP (typed): 280']);
  assert.deepStrictEqual(rows.map((r) => r.clearable), [true, false]);
  assert.strictEqual(C.overridePill({ rows: [], overrides: { count: 0, items: [] } }), null);
  assert.strictEqual(C.overridePill({ rows: [] }), null);
  assert.strictEqual(C.overridePill(null), null);
  const one = C.overridePill({ overrides: { count: 1, items: [VP] } });
  assert.strictEqual(one.text, '1 override');
  assert.strictEqual(one.tab, 'system');
  assert.strictEqual(C.overridePill({ overrides: { items: [VP, GEAR] } }).text, '2 overrides');
});

test('validPost: /api/settings accepts {clear: allowlisted key} only', () => {
  assert.strictEqual(C.validPost('/api/settings', { clear: 'market.vp' }), true);
  assert.strictEqual(C.validPost('/api/settings', { clear: 'nope.key' }), false);
  assert.strictEqual(C.validPost('/api/settings', { clear: 5 }), false);
  assert.strictEqual(C.validPost('/api/settings', { clear: 'market.vp', set: {} }), false);
});

test('wiring: affected cards mount badges; Home and System read the digest section', () => {
  const idx = read('dashboard/index.html');
  assert.ok(idx.indexOf('overrides.js') > idx.indexOf('ewcore.js'));
  assert.ok(idx.indexOf('overrides.js') < idx.indexOf('grind.js'));
  for (const [file, card] of [['grind', 'grind'], ['crafting', 'crafting'], ['inventory', 'inventory'],
    ['market', 'market'], ['events', 'events']]) {
    assert.match(read('dashboard/' + file + '.js'), new RegExp("EWOverrides\\.mount\\(.*, '" + card + "'\\)"), file);
  }
  const ov = read('dashboard/overrides.js');
  assert.match(ov, /\/api\/overrides/);
  assert.match(ov, /C\.overrideBadges\(/);
  assert.match(ov, /\{ clear: key \}/);
  assert.match(read('dashboard/home.js'), /C\.overridePill\(S\.snap\.signals\)/);
  assert.match(read('dashboard/dashboard.js'), /C\.overrideRows\(H\.signals\.overrides\)/);
  assert.ok(read('overlay/overlay.js').indexOf('/api/overrides') < 0); // dashboard only
});
