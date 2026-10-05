'use strict';
// Plan 012: grind spot recommender helpers (ewcore.js) and static guards on the
// Grind tab "Where next" card (grind.js). No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const read = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');

test('spotsPath: goal plus optional what-if, blank = Progress character', () => {
  assert.deepStrictEqual(C.spotsPath('xp', {}), { ok: true, path: '/api/spots?goal=xp' });
  assert.deepStrictEqual(C.spotsPath('silver', { ap: ' 250 ', dp: '', level: '58' }),
    { ok: true, path: '/api/spots?goal=silver&ap=250&level=58' });
  assert.deepStrictEqual(C.spotsPath('xp', { ap: '0', dp: '999', level: '70' }),
    { ok: true, path: '/api/spots?goal=xp&ap=0&dp=999&level=70' });
  assert.deepStrictEqual(C.SPOT_GOALS, ['xp', 'silver']);
  for (const bad of [['fun', {}], ['xp', { ap: '1000' }], ['xp', { dp: '-1' }], ['xp', { level: '0' }],
    ['xp', { level: '76' }], ['xp', { ap: '2.5' }], ['xp', { ap: 'x' }], [undefined, {}]]) {
    const r = C.spotsPath(bad[0], bad[1]);
    assert.strictEqual(r.ok, false, JSON.stringify(bad));
    assert.strictEqual(typeof r.error, 'string');
  }
  assert.strictEqual(C.spotsPath('xp', null).ok, true);
});

test('spotNeedText lists only what is still missing', () => {
  assert.strictEqual(C.spotNeedText({ need_ap: 50, need_dp: 40, need_level: 2 }), '+50 AP +40 DP +2 lvl');
  assert.strictEqual(C.spotNeedText({ need_ap: 0, need_dp: 15, need_level: 0 }), '+15 DP');
  assert.strictEqual(C.spotNeedText({}), '');
  assert.strictEqual(C.spotNeedText(null), '');
});

test('spotRecs: junk rows dropped, missing and error carried', () => {
  const d = {
    top: [{ id: 'a', name: 'A' }, null, { id: 'b' }, 'x', { id: 'c', name: '' }],
    unlocks: [{ id: 'u', name: 'U', need_ap: 5 }],
    missing: ['ap', 3], error: null
  };
  const r = C.spotRecs(d);
  assert.deepStrictEqual(r.top.map((x) => x.id), ['a']);
  assert.deepStrictEqual(r.unlocks.map((x) => x.id), ['u']);
  assert.deepStrictEqual(r.missing, ['ap']);
  assert.strictEqual(r.error, null);
  assert.deepStrictEqual(C.spotRecs(null), { top: [], unlocks: [], missing: [], error: null });
  assert.strictEqual(C.spotRecs({ error: 'table unreadable' }).error, 'table unreadable');
});

test('matchSpot finds a logged grind spot by name, case-insensitive', () => {
  const spots = [{ id: 'sausan', name: 'Sausan Garrison' }, { id: 'gy', name: 'Gyfin Rhasia Temple' }, null];
  assert.strictEqual(C.matchSpot(spots, 'sausan garrison'), 'sausan');
  assert.strictEqual(C.matchSpot(spots, ' Gyfin Rhasia Temple '), 'gy');
  assert.strictEqual(C.matchSpot(spots, 'Tunkuta'), null);
  assert.strictEqual(C.matchSpot(null, 'x'), null);
  assert.strictEqual(C.matchSpot(spots, null), null);
});

test('grind.js: Where next card, GET /api/spots, click pre-fills the spot, safe DOM', () => {
  const src = read('dashboard/grind.js');
  assert.match(src, /Where next/);
  assert.match(src, /C\.spotsPath\(/);
  assert.match(src, /C\.spotRecs\(/);
  assert.match(src, /C\.spotNeedText\(/);
  assert.match(src, /C\.matchSpot\(/);
  assert.match(src, /logged_silver_per_h/);
  assert.match(src, /verify/);
  assert.doesNotMatch(src, /innerHTML|outerHTML|insertAdjacentHTML|document\.write/);
  assert.doesNotMatch(src, /method:\s*'POST'/);
});

test('/api/spots is GET only: not in the POST bridge allowlist', () => {
  assert.strictEqual(C.POST_ROUTES.indexOf('/api/spots'), -1);
  assert.strictEqual(C.validPost('/api/spots', { goal: 'xp' }), false);
});

test('spot table on the server carries source + verified on every row', () => {
  const rows = JSON.parse(fs.readFileSync(path.join(__dirname, '..', '..', 'server', 'ew', 'data', 'grind_spots.json'), 'utf8'));
  assert.ok(rows.length >= 10);
  for (const r of rows) {
    assert.ok(typeof r.source === 'string' && r.source.trim(), r.id);
    assert.match(r.verified, /^\d{4}-\d{2}-\d{2}$/, r.id);
    assert.strictEqual(C.matchSpot([{ id: 'x', name: r.name }], r.name.toUpperCase()), 'x');
  }
});
