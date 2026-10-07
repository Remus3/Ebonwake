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

// Plan 077: one status truth - the profile row of /api/signals drives the pill.
function sigProfile(level, hint) {
  return { rows: [{ id: 'profile', name: 'Profile', level: level, age_s: 21600,
    reason: level === 'off' ? 'no_base' : 'error', detail: 'x', hint: hint }] };
}

test('profilePill: signal off -> muted unknown, label and text from the hint', () => {
  const hint = 'profile off: public API disallows robots - Settings > Profile > Self-hosted profile API base';
  const stale = { data: { family: 'F' }, status: 'stale',
    freshness: { fetched_at: 'x', age_s: 6 * 3600, ttl_s: 3600, stale: true, error: null } };
  assert.strictEqual(C.profilePill(stale).cls, 'bad');  // the live red pill without the digest
  const p = C.profilePill(stale, sigProfile('off', hint));
  assert.strictEqual(p.cls, 'unknown');
  assert.strictEqual(p.off, true);
  assert.strictEqual(p.label, 'profile off: public API disallows robots');
  assert.strictEqual(p.text, hint);
  assert.strictEqual(C.profilePill(null, sigProfile('off', null)).off, true);
});

test('profilePill: a real fetch error stays bad when the signal is not off', () => {
  const err = { data: {}, status: 'error',
    freshness: { fetched_at: 'x', age_s: 4 * 3600, ttl_s: 3600, stale: true, error: 'HTTP 503' } };
  assert.strictEqual(C.profilePill(err, sigProfile('bad', 'profile fetch failing')).cls, 'bad');
  assert.strictEqual(C.profilePill(err, null).cls, 'bad');
  assert.strictEqual(C.profilePill(err, { rows: 'junk' }).cls, 'bad');
});

test('progress.js: profile and Life & CP cards read /api/signals through profilePill', () => {
  const src = read('dashboard/progress.js');
  assert.match(src, /getJSON\('\/api\/signals'\)/);
  assert.strictEqual((src.match(/C\.profilePill\([^)]*S\.signals\)/g) || []).length, 2);
  assert.doesNotMatch(src, /C\.profilePill\(S\.data\.profile\)\./);
});
