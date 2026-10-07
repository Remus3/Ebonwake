'use strict';
// Plan 041: profile history sparklines (ewcore.js historyRows) and the Progress
// tab trend rows (progress.js). No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const read = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');

const BODY = {
  days: 30, character: 'Shooty',
  series: {
    level: { points: [{ at: '2026-10-01T00:00:00+00:00', v: 61 }, { at: '2026-10-02T00:00:00+00:00', v: 62 }], first: 61, last: 62, delta: 1 },
    gs: { points: [{ at: '2026-10-01T00:00:00+00:00', v: 640 }], first: 640, last: 640, delta: 0 },
    energy: { points: [], first: null, last: null, delta: null },
    contribution: { points: [{ at: 'junk', v: 5 }, { at: '2026-10-01T00:00:00+00:00', v: 'x' }, { at: '2026-10-02T00:00:00+00:00', v: 312 }], first: 312, last: 312, delta: 0 }
  }
};

test('PROFILE_HISTORY_PATH asks for the four card fields', () => {
  assert.match(C.PROFILE_HISTORY_PATH, /^\/api\/progress\/history\?field=level,gs,energy,contribution&days=\d+$/);
});

test('historyRows: label, [ms, v] points, last and signed delta; junk dropped', () => {
  const rows = C.historyRows(BODY);
  assert.deepStrictEqual(rows.map(function (r) { return r.label; }), ['Level', 'GS', 'Energy', 'CP']);
  assert.deepStrictEqual(rows[0].points, [[Date.parse('2026-10-01T00:00:00Z'), 61], [Date.parse('2026-10-02T00:00:00Z'), 62]]);
  assert.strictEqual(rows[0].text, '62 (+1)');
  assert.strictEqual(rows[1].text, '640');
  assert.strictEqual(rows[2].text, '-');
  assert.deepStrictEqual(rows[2].points, []);
  assert.deepStrictEqual(rows[3].points, [[Date.parse('2026-10-02T00:00:00Z'), 312]]);
  const down = C.historyRows({ series: { gs: { points: [{ at: '2026-10-01T00:00:00+00:00', v: 650 }, { at: '2026-10-02T00:00:00+00:00', v: 640 }], last: 640, delta: -10 } } });
  assert.strictEqual(down[1].text, '640 (-10)');
  assert.deepStrictEqual(C.historyRows(null).map(function (r) { return r.text; }), ['-', '-', '-', '-']);
  assert.deepStrictEqual(C.historyRows({ series: 'x' })[0].points, []);
});

test('progress.js: trend rows from /api/progress/history with SVG sparklines, safe DOM', () => {
  const src = read('dashboard/progress.js');
  assert.match(src, /C\.PROFILE_HISTORY_PATH/);
  assert.match(src, /C\.(historyRows|profileTrends)\(/); // plan 042 splits rows per card
  assert.match(src, /C\.sparkPath\(/);
  assert.match(src, /createElementNS/);
  assert.doesNotMatch(src, /innerHTML|outerHTML|insertAdjacentHTML|document\.write/);
});
