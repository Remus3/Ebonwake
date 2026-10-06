'use strict';
// Plan 056: Black Spirit's Adventure dice from logged-in time. Pure formatters
// in ewcore.js over GET /api/today `dice`, plus static guards on the overlay,
// the Today row and the widget switch. No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const APP = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(APP, p), 'utf8');
const NOW = Date.parse('2026-10-06T10:18:00Z');
const MIN = 60000;

function dice(o) {
  return Object.assign({ earned: 1, max: 3, next_at_min: 30, eta_utc: '2026-10-06T10:30:00+00:00',
    played_min: 18, logged_in: true, next_reset: '2026-10-07T05:00:00+00:00', verified: false }, o);
}

test('diceLine: logged in counts down locally from eta_utc', () => {
  assert.strictEqual(C.diceLine(dice(), NOW), 'die 2/3 in 12m');
  assert.strictEqual(C.diceLine(dice(), NOW + 11 * MIN + 30000), 'die 2/3 in 1m');
  assert.strictEqual(C.diceLine(dice(), NOW + 12 * MIN), 'die 2/3 ready');
  assert.strictEqual(C.diceLine(dice({ earned: 2, next_at_min: 60, eta_utc: '2026-10-06T11:30:00+00:00' }), NOW),
    'die 3/3 in 1h12m');
});

test('diceLine: logged out, before login, all earned, bad bodies', () => {
  assert.strictEqual(C.diceLine(dice({ eta_utc: null, logged_in: false, played_min: 20 }), NOW),
    'die 2/3 after 10m play');
  assert.strictEqual(C.diceLine(dice({ earned: 0, next_at_min: 0, eta_utc: null, played_min: 0 }), NOW),
    'dice 0/3 - log in');
  assert.strictEqual(C.diceLine(dice({ earned: 3, next_at_min: null, eta_utc: null }), NOW), 'dice 3/3');
  [null, undefined, {}, [], dice({ earned: 4 }), dice({ max: 0 }), dice({ earned: -1 }),
    dice({ next_at_min: '30' }), dice({ earned: 1.5 })].forEach((d) => {
    assert.strictEqual(C.diceLine(d, NOW), '-', JSON.stringify(d));
    assert.ok(C.ovQuiet(C.diceLine(d, NOW)));
  });
});

test('diceSuggest: only the open dice row, only once a die is earned', () => {
  const it = { id: 'black-spirits-adventure-dice', title: "Black Spirit's Adventure dice", done: false };
  assert.strictEqual(C.diceSuggest(it, dice()), '1/3 earned - roll, then tick');
  assert.strictEqual(C.diceSuggest(Object.assign({}, it, { done: true }), dice()), '');
  assert.strictEqual(C.diceSuggest(it, dice({ earned: 0, next_at_min: 0, eta_utc: null })), '');
  assert.strictEqual(C.diceSuggest(it, null), '');
  assert.strictEqual(C.diceSuggest({ id: 'barter-run', title: 'Barter run', done: false }, dice()), '');
  assert.ok(C.isDiceItem({ id: 'x-2', title: ' black spirits adventure dice ' }));
  assert.ok(!C.isDiceItem(null));
});

test('dice widget: opt-in, default off, query round-trip, settings + example config', () => {
  assert.strictEqual(C.widgetsQuery({}).dice, '0');
  assert.strictEqual(C.overlayWidgets({}).dice, false);
  assert.strictEqual(C.overlayWidgets({ overlay: { widgets: { dice: true } } }).dice, true);
  assert.strictEqual(C.widgetsFromQuery('').dice, false);
  const q = new URLSearchParams(C.widgetsQuery({ dice: true })).toString();
  assert.strictEqual(C.widgetsFromQuery('?' + q).dice, true);
  const ex = JSON.parse(fs.readFileSync(path.join(APP, '..', 'config', 'local.example.json'), 'utf8'));
  assert.strictEqual(ex.overlay.widgets.dice, false);
  assert.match(fs.readFileSync(path.join(APP, '..', 'server', 'ew', 'settings.py'), 'utf8'), /"dice": False/);
});

test('overlay: dice from GET /api/today, opt-in row, no POST; Today never auto-ticks', () => {
  const src = read('overlay/overlay.js');
  assert.match(src, /C\.diceLine\(today\.dice/);
  assert.match(src, /W\.dice/);
  assert.doesNotMatch(src, /method:\s*'POST'|ewApi|innerHTML/);
  assert.match(read('overlay/index.html'), /id="ov-dice-row" hidden/);
  const t = read('dashboard/today.js');
  assert.match(t, /C\.diceSuggest\(it, S\.data\.dice\)/);
  // The suggestion never calls toggle / send on its own.
  const block = t.slice(t.indexOf('if (r.dice)'), t.indexOf('const n = row(it, extra);'));
  assert.doesNotMatch(block, /toggle|send\(|ewApi/);
});
