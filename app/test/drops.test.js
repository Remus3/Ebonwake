'use strict';
// Plan 038: drop-rate card helpers (ewcore.js), the drop POST shapes and the
// Grind tab card wiring. No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const read = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');

const ROI = {
  price_silver: 1e9, minutes: 60, per_week: 10, sale_until_utc: '2026-10-22', removed_utc: '2026-11-05',
  on_sale: true, hidden: false, rate_before: 300, rate_after: 350, gain_pct: 12.5,
  break_even_silver_h: 8e9, silver_h: 1e10, gain_silver: 1.25e9, net_silver: 2.5e8, worth: true,
  spot: 'gyfin', spot_name: 'Gyfin'
};

function body(over) {
  return {
    sessions: [],
    drops: Object.assign({
      error: null, rate_total: 350, rate_capped: 300, wasted: 50, amount_total: 50, cap_used: 300,
      over_cap: true, caps: { base_pct: 300, bypass_pct: 400, beyond_pct: 500 },
      active: [{ id: 'a', name: 'Item Collection', via: 'timer', rate_pct: 100, amount_pct: 50, bypass: 'none' },
        { id: 'n', name: 'Night time', via: 'toggle', rate_pct: 10, amount_pct: null, bypass: 'none' }],
      buffs: [{ id: 'n', name: 'Night time', rate_pct: 10, amount_pct: null, bypass: 'none', source: 'BDF',
        verified: '2026-10-05', note: null, on: true, timer: false, overridden: false },
      { id: 'e', name: 'Ecology', rate_pct: 30, amount_pct: null, bypass: 'none', source: 'BDF',
        verified: false, note: 'disputed', on: false, timer: false, overridden: true },
      { id: 'x', name: 'Node', rate_pct: 10, bypass: 'to400', source: 'BDF', verified: '2026-10-05' }, null]
    }, over || {}),
    agris_roi: ROI
  };
}

test('dropView: capped line, wasted flag, amount apart, active and toggles', () => {
  const v = C.dropView(body());
  assert.strictEqual(v.error, null);
  assert.strictEqual(v.line, '300% / 300% cap (350% stacked, 50% wasted)');
  assert.strictEqual(v.wasted, true);
  assert.strictEqual(v.amount, '+50% amount');
  assert.deepStrictEqual(v.active.map((a) => [a.name, a.text, a.via]),
    [['Item Collection', '+100% +50% amount', 'timer'], ['Night time', '+10%', 'toggle']]);
  assert.deepStrictEqual(v.toggles.map((t) => [t.id, t.on, t.unverified, t.overridden]),
    [['n', true, false, false], ['e', false, true, true], ['x', false, false, false]]);
  assert.match(v.toggles[2].title, /bypass to 400%/);
  assert.match(v.toggles[1].title, /unverified - disputed/);
  const ok = C.dropView(body({ rate_total: 120, rate_capped: 120, wasted: 0, amount_total: 0 }));
  assert.strictEqual(ok.line, '120% / 300% cap');
  assert.strictEqual(ok.wasted, false);
  assert.strictEqual(ok.amount, '');
});

test('dropView: error and missing data degrade', () => {
  assert.strictEqual(C.dropView(body({ error: 'drop buffs unreadable' })).error, 'drop buffs unreadable');
  assert.strictEqual(C.dropView({ sessions: [] }).error, 'no drop data');
  assert.strictEqual(C.dropView(null).roi, null);
});

test('agrisLine: break-even, verdict, dates; hidden once removed', () => {
  const r = C.agrisLine(ROI);
  assert.strictEqual(r.text, 'Agris 1B / 60m: +12.5% drops, break-even 8B/h');
  assert.strictEqual(r.verdict, 'worth it at 10B/h (Gyfin), net 250M');
  assert.strictEqual(r.dates, 'sale until 2026-10-22, removed 2026-11-05');
  assert.strictEqual(r.worth, true);
  const none = C.agrisLine(Object.assign({}, ROI, { silver_h: null, break_even_silver_h: null, worth: null, on_sale: false }));
  assert.match(none.text, /break-even never/);
  assert.strictEqual(none.verdict, 'log a session to compare');
  assert.match(none.dates, /^sale over/);
  assert.strictEqual(C.agrisLine(Object.assign({}, ROI, { hidden: true })), null);
  assert.strictEqual(C.agrisLine(null), null);
  assert.strictEqual(C.dropView(body()).roi.worth, true);
});

test('validGrindBody: drop_toggle and drop_override shapes', () => {
  const ok = [
    { drop_toggle: { id: 'night', on: true } }, { drop_toggle: { id: 'node-investment', on: false } },
    { drop_override: { id: 'luck', field: 'rate_pct', value: 12.5 } },
    { drop_override: { id: 'luck', field: 'rate_pct', value: null } },
    { drop_override: { id: 'luck', field: 'bypass', value: 'to400' } },
    { drop_override: { id: 'caps', field: 'base_pct', value: 320 } },
    { drop_override: { id: 'agris_scroll', field: 'removed_utc', value: '2026-11-05' } }
  ];
  for (const b of ok) assert.strictEqual(C.validGrindBody(b), true, JSON.stringify(b));
  const bad = [
    { drop_toggle: { id: 'night', on: 1 } }, { drop_toggle: { id: 'Night', on: true } }, { drop_toggle: { id: 'night' } },
    { drop_override: { id: 'luck', field: 'name', value: 'x' } }, { drop_override: { id: 'luck', field: 'rate_pct' } },
    { drop_override: { id: 'luck', field: 'rate_pct', value: -1 } }, { drop_override: { id: 'luck', field: 'rate_pct', value: 'x' } },
    { drop_override: { id: 'a_b', field: 'rate_pct', value: 1 } }, { drop_override: { id: 'luck', field: 'rate_pct', value: true } }
  ];
  for (const b of bad) assert.strictEqual(C.validGrindBody(b), false, JSON.stringify(b));
});

test('Grind tab: Drop rate card wired, writes via the bridge only', () => {
  const src = read('dashboard/grind.js');
  assert.match(src, /card\('Drop rate'\)/);
  assert.match(src, /C\.dropView\(/);
  assert.match(src, /drop_toggle:/);
  assert.match(src, /drawDrops\(\);/);
});
