'use strict';
// Plan 073: signal health digest. signalRows / signalPill (ewcore.js) turn
// /api/signals into the System card rows and the Home pill. Static guards on
// the dashboard + home wiring. No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const APP = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(APP, p), 'utf8');

function doc(over) {
  const rows = [
    { id: 'session_log', name: 'Session log', level: 'ok', age_s: 30, reason: null, detail: 'game logged_in', hint: null },
    { id: 'screenshots', name: 'Screenshot watcher', level: 'off', age_s: null, reason: 'game_closed', detail: 'game closed', hint: 'game closed - no screenshots expected' },
    { id: 'market', name: 'Market', level: 'warn', age_s: 7200, reason: 'blocked', detail: '2 endpoint(s) blocked', hint: 'arsha.io blocked' }
  ];
  return { rows: rows.map((r) => Object.assign({}, r, (over || {})[r.id])), bad: 0, worst: 'warn' };
}

test('signalRows: one row per signal, off renders muted, age + hint carried', () => {
  const rows = C.signalRows(doc());
  assert.deepStrictEqual(rows.map((r) => r.id), ['session_log', 'screenshots', 'market']);
  assert.deepStrictEqual(rows.map((r) => r.cls), ['ok', 'unknown', 'warn']);
  assert.strictEqual(rows[0].text, 'Session log ok - 30s ago');
  assert.strictEqual(rows[1].text, 'Screenshot watcher off');
  assert.strictEqual(rows[2].text, 'Market warn - 2h ago');
  assert.strictEqual(rows[2].hint, 'arsha.io blocked');
  assert.strictEqual(rows[2].detail, '2 endpoint(s) blocked');
});

test('signalRows: junk in, nothing thrown', () => {
  assert.deepStrictEqual(C.signalRows(null), []);
  assert.deepStrictEqual(C.signalRows({ rows: 'x' }), []);
  const r = C.signalRows({ rows: [null, { id: 'x', level: 'weird' }] });
  assert.strictEqual(r.length, 1);
  assert.strictEqual(r[0].level, 'warn');
  assert.strictEqual(r[0].name, 'x');
});

test('signalPill: null unless a row is bad; names the bad signal and its hint', () => {
  assert.strictEqual(C.signalPill(doc()), null);
  assert.strictEqual(C.signalPill(null), null);
  const one = C.signalPill(doc({ session_log: { level: 'bad', hint: 'check Settings > paths' } }));
  assert.deepStrictEqual(one, { cls: 'bad', tab: 'system', text: 'Session log: not working',
    title: 'Session log: check Settings > paths' });
  const two = C.signalPill(doc({ session_log: { level: 'bad' }, market: { level: 'bad' } }));
  assert.strictEqual(two.text, '2 signals not working');
});

test('System card renders the digest, falls back to freshness pills on an old server', () => {
  const d = read('dashboard/dashboard.js');
  assert.match(d, /card\('Signal health', fr\)/);
  assert.match(d, /getJSON\('\/api\/signals'\)/);
  assert.match(d, /C\.signalRows\(H\.signals\)/);
  assert.match(d, /C\.sourceFreshness\(/);
});

test('Home reads /api/signals and shows the pill only via C.signalPill', () => {
  const h = read('dashboard/home.js');
  assert.match(h, /signals: '\/api\/signals'/);
  assert.match(h, /C\.signalPill\(S\.snap\.signals\)/);
  assert.match(h, /openTab\(pill\.tab\)/);
});
