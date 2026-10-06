'use strict';
// Plan 046: session-end summary. pendingStop / pendingStopText (the Grind tab
// "Session ended" dialog), summaryRows + the Home "Last session" card, the
// gameExit notify rule over grind.pending_stop, and the grind POST shapes
// (stop at_exit, keep). Static guards on grind.js / home.js wiring. No DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const APP = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(APP, p), 'utf8');
const T0 = Date.parse('2026-10-05T12:00:00Z');
const AT = '2026-10-05T11:30:00+00:00';
const ON = { gameExit: true };

function grind(extra) {
  return Object.assign({ active: { spot: 'orc-camp', started: '2026-10-05T10:00:00+00:00' },
    spots: [{ id: 'orc-camp', name: 'Orc Camp' }], sessions: [] }, extra);
}

const PENDING = { at: AT, started: '2026-10-05T10:00:00+00:00', spot: 'orc-camp', minutes: 90 };

function win(extra) {
  return Object.assign({ since: '2026-10-05T10:00:00+00:00', until: AT, empty: false,
    grind: { sessions: 2, minutes: 90, silver: 300000000, silver_per_h: 200000000,
      spots: [{ spot: 'orc-camp', name: 'Orc Camp', sessions: 2, minutes: 90, silver: 300000000, silver_per_h: 200000000 }] },
    xp: { gained_pct: 30.55, from: { level: 62, pct: 80 }, to: { level: 63, pct: 10.55 } },
    buffs: [{ name: 'XP scroll', ends: AT }], dailies: [{ id: 'a', title: 'Attendance reward' }],
    events: [{ id: 'e1', title: 'Season pass' }] }, extra);
}

test('pendingStop: needs an active session and a sane pending row', () => {
  assert.deepStrictEqual(C.pendingStop(grind({ pending_stop: PENDING })), { at: AT, spot: 'orc-camp', minutes: 90 });
  assert.strictEqual(C.pendingStop(grind({ pending_stop: null })), null);
  assert.strictEqual(C.pendingStop(grind({ active: null, pending_stop: PENDING })), null);
  assert.strictEqual(C.pendingStop(grind({ pending_stop: Object.assign({}, PENDING, { at: 'soon' }) })), null);
  assert.strictEqual(C.pendingStop(grind({ pending_stop: Object.assign({}, PENDING, { minutes: 0 }) })), null);
  assert.strictEqual(C.pendingStop(null), null);
  assert.strictEqual(C.pendingStop(grind({ pending_stop: [] })), null);
});

test('pendingStopText names the spot, the exit age and the minutes', () => {
  const p = C.pendingStop(grind({ pending_stop: PENDING }));
  assert.strictEqual(C.pendingStopText(p, grind().spots, T0),
    'Game exited (30m 00s ago) while Orc Camp was running. Stop at the exit time (1h 30m) or keep it running?');
  assert.strictEqual(C.pendingStopText(null, [], T0), '');
});

test('summaryRows: grind, best spot, XP, buffs, dailies, events', () => {
  assert.deepStrictEqual(C.summaryRows(win()), [
    { label: 'grind', value: '2 sessions, 1h 30m' },
    { label: 'silver', value: '300M (200M/h)' },
    { label: 'best spot', value: 'Orc Camp 200M/h' },
    { label: 'XP', value: '+30.6% (now Lv 63)' },
    { label: 'buffs', value: 'XP scroll' },
    { label: 'dailies', value: '1 ticked' },
    { label: 'events', value: '1 claimed' }
  ]);
});

test('summaryRows: empty, missing or junk windows give no rows', () => {
  assert.deepStrictEqual(C.summaryRows(win({ empty: true })), []);
  assert.deepStrictEqual(C.summaryRows(null), []);
  assert.deepStrictEqual(C.summaryRows({ grind: 'x' }), []);
  const bare = C.summaryRows(win({ grind: { sessions: 0, minutes: 0, silver: 0, silver_per_h: 0, spots: [] },
    xp: null, buffs: [1, { name: 2 }], dailies: 'x', events: null }));
  assert.deepStrictEqual(bare, []);
  assert.deepStrictEqual(C.summaryRows(win({ grind: { sessions: 1, minutes: 30, silver: 5, silver_per_h: 10, spots: [] },
    xp: null, buffs: [], dailies: [], events: [] })),
  [{ label: 'grind', value: '1 session, 30m 00s' }, { label: 'silver', value: '5 (10/h)' }]);
});

test('Home: Last session card only when /api/summary has a session', () => {
  const card = (snap) => C.composeNow(snap, T0).filter((c) => c.id === 'summary');
  assert.deepStrictEqual(card({ at: {} }), []);
  assert.deepStrictEqual(card({ at: {}, summary: { session: null } }), []);
  const c = card({ at: {}, summary: { session: win() } })[0];
  assert.strictEqual(c.title, 'Last session');
  assert.strictEqual(c.tab, 'grind');
  assert.strictEqual(c.rows[0].label, 'grind');
  const e = card({ at: {}, summary: { session: win({ empty: true }) } })[0];
  assert.strictEqual(e.empty, 'no game session recorded yet');
});

test('gameExit fires from grind.pending_stop, keyed like the game transition', () => {
  const g = grind({ pending_stop: PENDING });
  const next = { game: { state: 'not_running', since: AT }, grind: g };
  const hits = C.notifyRules(null, next, T0, ON);
  assert.strictEqual(hits.length, 1);
  assert.strictEqual(hits[0].key, 'gameExit:' + Date.parse(AT));
  assert.strictEqual(hits[0].title, 'Game exited');
  // the transition path (no pending yet) yields the same key, so the ledger dedupes
  const prev = { game: { state: 'running' }, grind: grind() };
  const t = C.notifyRules(prev, { game: { state: 'not_running', since: AT }, grind: grind() }, T0, ON);
  assert.strictEqual(t[0].key, hits[0].key);
  // kept running / stopped: nothing
  assert.deepStrictEqual(C.notifyRules(null, { game: { state: 'not_running' }, grind: grind() }, T0, ON), []);
  assert.deepStrictEqual(C.notifyRules(null, next, T0, { gameExit: false }), []);
});

test('grind POST shapes: stop at_exit true, keep true', () => {
  assert.strictEqual(C.validPost('/api/grind', { stop: { silver: 1, trash: 0, at_exit: true } }), true);
  assert.strictEqual(C.validPost('/api/grind', { stop: { silver: 1, trash: 0, at_exit: false } }), false);
  assert.strictEqual(C.validPost('/api/grind', { stop: { silver: 1, trash: 0, at_exit: 1 } }), false);
  assert.strictEqual(C.validPost('/api/grind', { stop: { silver: 1, trash: 0 } }), true);
  assert.strictEqual(C.validPost('/api/grind', { stop: { trash: 0, at_exit: true } }), false);
  assert.strictEqual(C.validPost('/api/grind', { keep: true }), true);
  assert.strictEqual(C.validPost('/api/grind', { keep: false }), false);
  assert.strictEqual(C.validPost('/api/grind', { keep: 'yes' }), false);
});

test('wiring: grind.js dialog + summary card, home.js reads /api/summary', () => {
  const g = read('dashboard/grind.js');
  assert.match(g, /'Session ended'/);
  assert.match(g, /'Stop at exit time', stopAtExit/);
  assert.match(g, /'Keep running', keepRunning/);
  assert.match(g, /at_exit = true/);
  assert.match(g, /send\(\{ keep: true \}/);
  assert.match(g, /getJSON\('\/api\/summary'\)/);
  assert.match(read('dashboard/home.js'), /summary: '\/api\/summary'/);
  // never an automatic stop: the at_exit stop is only sent from the button handler
  assert.strictEqual((g.match(/at_exit/g) || []).length, 1);
});
