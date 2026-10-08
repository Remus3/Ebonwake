'use strict';
// Plan 088: Monster Zone Info reads on the client - the spot-row text
// ("~N kills to Lv X", in-game recommended level, re-read hint), the
// cap-bound XP-buff card line and the normalized leveling field. No network,
// no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const APP = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(APP, p), 'utf8');

test('zoneKillsText', () => {
  assert.strictEqual(C.zoneKillsText({ current: true, kills_to_level: 830, next_level: 58 }),
    '~830 kills to Lv 58');
  assert.strictEqual(C.zoneKillsText({ current: true, kills_to_level: 5000, next_level: 63 }),
    '~5,000 kills to Lv 63');
  assert.strictEqual(C.zoneKillsText({ current: false, reread_level: 57, kills_to_level: null }),
    're-read at Lv 57');
  assert.strictEqual(C.zoneKillsText({ current: true, kills_to_level: null, next_level: 58 }), '');
  assert.strictEqual(C.zoneKillsText(null), '');
  assert.strictEqual(C.zoneKillsText('x'), '');
});

test('zoneLevelText', () => {
  assert.strictEqual(C.zoneLevelText({ recommended_level: 58 }), 'in-game Lv 58');
  assert.strictEqual(C.zoneLevelText({ recommended_level: 'x' }), '');
  assert.strictEqual(C.zoneLevelText(undefined), '');
});

test('zoneCapText flips on cap_bound only', () => {
  assert.strictEqual(C.zoneCapText({ zone_name: 'Z', cap_bound: true }),
    'cap-bound here - XP buffs add nothing');
  assert.strictEqual(C.zoneCapText({ zone_name: 'Z', cap_bound: false }), '');
  assert.strictEqual(C.zoneCapText(null), '');
});

test('normalizeLeveling keeps zone_cap', () => {
  const base = { milestones: [60], hot: {} };
  assert.strictEqual(C.normalizeLeveling(base).zone_cap, null);
  const n = C.normalizeLeveling(Object.assign({}, base, {
    zone_cap: { zone_name: 'Gyfin Rhasia Temple', cap_bound: true, cap_pct: 0.02,
      source: 'in-game zone info 2026-10-08' } }));
  assert.deepStrictEqual(n.zone_cap, { zone_name: 'Gyfin Rhasia Temple', cap_bound: true,
    source: 'in-game zone info 2026-10-08' });
  assert.strictEqual(C.normalizeLeveling(Object.assign({}, base, { zone_cap: { cap_bound: true } })).zone_cap, null);
});

test('surfaces use the helpers', () => {
  const grind = read('dashboard/grind.js');
  assert.ok(grind.includes('C.zoneKillsText(r.zone_xp)'));
  assert.ok(grind.includes('C.zoneLevelText(r.zone_xp)'));
  assert.ok(read('dashboard/leveling.js').includes('C.zoneCapText(d.zone_cap)'));
});
