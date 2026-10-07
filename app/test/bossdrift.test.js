'use strict';
// Plan 072: boss schedule drift banner on the Bosses card and the System tab.
// Pure formatter in ewcore.js over GET /api/bosses `drift` and /api/state
// sources.bossdrift, plus static guards on bosses.js / dashboard.js. No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const APP = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(APP, p), 'utf8');

const MOVED = {
  state: 'differs', source: 'https://mmotimer.com/bdo/?server=na', robots: 'allow', n: 1,
  banner: 'schedule differs from mmotimer.com on 1 slot - verify',
  diff: [{ kind: 'moved', weekday: 1, local_at: '17:00', remote_weekday: 1, remote_at: '18:00',
    local: ['Bulgasal', 'Kzarka'], remote: ['Bulgasal', 'Kzarka'],
    text: 'Tue 17:00 -> 18:00: Bulgasal + Kzarka' }]
};

test('bossDriftBanner: differs -> banner text + one line per diff entry', () => {
  assert.deepStrictEqual(C.bossDriftBanner(MOVED), {
    text: 'schedule differs from mmotimer.com on 1 slot - verify',
    lines: ['Tue 17:00 -> 18:00: Bulgasal + Kzarka']
  });
});

test('bossDriftBanner: ok / unknown / missing / malformed -> null (no banner)', () => {
  assert.strictEqual(C.bossDriftBanner(Object.assign({}, MOVED, { state: 'ok' })), null);
  assert.strictEqual(C.bossDriftBanner(Object.assign({}, MOVED, { state: 'unknown' })), null);
  assert.strictEqual(C.bossDriftBanner(null), null);
  assert.strictEqual(C.bossDriftBanner('x'), null);
  assert.strictEqual(C.bossDriftBanner({ state: 'differs', banner: 7 }), null);
});

test('bossDriftBanner: caps lines and drops non-text entries', () => {
  const diff = [];
  for (let i = 0; i < 20; i++) diff.push({ text: 'slot ' + i });
  diff.push({ text: 5 }, null);
  const b = C.bossDriftBanner(Object.assign({}, MOVED, { diff: diff }));
  assert.strictEqual(b.lines.length, C.BOSS_DRIFT_MAX_LINES);
  assert.strictEqual(b.lines[0], 'slot 0');
});

test('bossDriftBanner: state source row (System) carries only banner text', () => {
  const b = C.bossDriftBanner({ status: 'differs', banner: MOVED.banner });
  assert.deepStrictEqual(b, { text: MOVED.banner, lines: [] });
});

test('sourceFreshness: bossdrift differs is a warn pill', () => {
  const rows = C.sourceFreshness({ bossdrift: { status: 'differs', updated: new Date(0).toISOString(),
    ttl_s: 1e12 } }, 1000);
  assert.strictEqual(rows[0].cls, 'warn');
});

test('bosses.js renders the drift banner via DOM APIs; System tab shows it too', () => {
  const b = read('dashboard/bosses.js');
  assert.match(b, /C\.bossDriftBanner\(/);
  assert.doesNotMatch(b, /innerHTML/);
  const d = read('dashboard/dashboard.js');
  assert.match(d, /C\.bossDriftBanner\(/);
  assert.doesNotMatch(d, /innerHTML/);
});
