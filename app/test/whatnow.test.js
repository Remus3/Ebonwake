'use strict';
// Plan 069: What now. The server ranks; ewcore only validates the actions,
// counts `due` down, builds the Home card (on top) and the overlay line.
// Static guards on the overlay + Home + settings wiring. No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const APP = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(APP, p), 'utf8');
const T0 = Date.parse('2026-10-06T18:00:00Z');
const M = 60 * 1000;
const iso = (ms) => new Date(ms).toISOString().replace('.000Z', '+00:00');

function view() {
  return {
    now: iso(T0),
    top: { text: 'Kzarka spawns', why: 'world boss, 11:08 PT', due: iso(T0 + 8 * M), source: 'boss' },
    next: [
      { text: 'Finish 2 dailies before reset', why: 'Barter, Pet feed', due: iso(T0 + 25 * M), source: 'reset' },
      { text: 'Review 2 OCR reads', why: 'System tab review queue', due: null, source: 'ocr' }
    ],
    empty: false, empty_text: 'All clear - play', errors: []
  };
}

test('whatNowActions: top then next, junk dropped, tab per source', () => {
  const v = view();
  v.next.push({ text: 'extra', why: '', due: null, source: 'boss' });
  const a = C.whatNowActions(v);
  assert.deepStrictEqual(a.map((x) => x.source), ['boss', 'reset', 'ocr']);
  assert.deepStrictEqual(a.map((x) => x.tab), ['today', 'today', 'system']);
  assert.strictEqual(a[0].due, T0 + 8 * M);
  assert.strictEqual(a[2].due, null);
  assert.deepStrictEqual(C.whatNowActions({ top: { text: 'x', source: 'nope' }, next: [] }), []);
  assert.deepStrictEqual(C.whatNowActions({ top: { text: '', source: 'boss' }, next: 'x' }), []);
  assert.deepStrictEqual(C.whatNowActions(null), []);
});

test('whatNowLeft: counts down locally, now once due, blank undated', () => {
  const [boss, , ocr] = C.whatNowActions(view());
  assert.strictEqual(C.whatNowLeft(boss, T0), 'in 8m');
  assert.strictEqual(C.whatNowLeft(boss, T0 + 7 * M), 'in 1m');
  assert.strictEqual(C.whatNowLeft(boss, T0 + 9 * M), 'now');
  assert.strictEqual(C.whatNowLeft(ocr, T0), '');
});

test('whatNowLine: one overlay line; all clear; - without data', () => {
  assert.strictEqual(C.whatNowLine(view(), T0), 'Kzarka spawns - in 8m');
  const v = view();
  v.top = v.next[1];
  assert.strictEqual(C.whatNowLine(v, T0), 'Review 2 OCR reads');
  assert.strictEqual(C.whatNowLine({ top: null, next: [], empty: true }, T0), 'All clear - play');
  assert.strictEqual(C.whatNowLine(null, T0), '-');
});

test('composeNow: What now card leads Home, top row highlighted with an open link', () => {
  const cards = C.composeNow({ at: {}, whatnow: view() }, T0).cards;
  assert.strictEqual(cards[0].id, 'whatnow');
  const c = cards[0];
  assert.strictEqual(c.title, 'What now');
  assert.strictEqual(c.tab, 'today');
  assert.deepStrictEqual(c.rows.map((r) => r.label),
    ['Kzarka spawns', 'Finish 2 dailies before reset', 'Review 2 OCR reads']);
  assert.strictEqual(c.rows[0].cls, 'warn');
  assert.strictEqual(c.rows[1].cls, '');
  assert.strictEqual(c.rows[0].value, 'in 8m');
  assert.deepStrictEqual(c.rows[2].go, { tab: 'system' });
});

test('composeNow: all clear keeps the card; a first-run card leads over it', () => {
  const clear = { top: null, next: [], empty: true, empty_text: 'All clear - play', errors: [] };
  const out = C.composeNow({ at: {}, whatnow: clear }, T0);
  const cards = out.cards;
  assert.strictEqual(cards[0].id, 'whatnow');
  assert.strictEqual(cards[0].empty, 'All clear - play');
  assert.ok(out.quiet.every((q) => q.title !== 'What now'), 'What now never goes quiet');
  const ob = { show: true, done: 0, total: 1, steps: [{ id: 'bdo', title: 'Point EW at BDO', done: false,
    link: { tab: 'system' } }] };
  const ids = C.composeNow({ at: {}, whatnow: clear, onboarding: ob }, T0).cards.map((x) => x.id);
  assert.deepStrictEqual(ids.slice(0, 2), ['onboarding', 'whatnow']);
  const busy = C.composeNow({ at: {}, whatnow: view(), onboarding: ob }, T0).cards.map((x) => x.id);
  assert.deepStrictEqual(busy.slice(0, 2), ['whatnow', 'onboarding']);
  assert.ok(C.composeNow({ at: {} }, T0).cards.every((x) => x.id !== 'whatnow'), '404 drops the card');
});

test('plan 076: What now rows carry the action source + due for the Timers dedupe', () => {
  const c = C.composeNow({ at: {}, whatnow: view() }, T0).cards[0];
  assert.strictEqual(c.wide, true);
  assert.strictEqual(c.rows[0].source, 'boss');
  assert.strictEqual(c.rows[0].due, T0 + 8 * M);
  assert.strictEqual(c.rows[2].due, null);
});

test('overlay: whatNow widget default on, opt-out via config', () => {
  assert.strictEqual(C.overlayWidgets({}).whatNow, true);
  assert.strictEqual(C.overlayWidgets({ overlay: { widgets: { whatNow: false } } }).whatNow, false);
  assert.strictEqual(C.widgetsFromQuery('?whatNow=0').whatNow, false);
});

test('wiring: overlay row, SSE whatnow listener, Home source, dashboard domain', () => {
  const html = read('overlay/index.html');
  assert.match(html, /id="ov-whatnow-row" hidden/);
  const ov = read('overlay/overlay.js');
  assert.match(ov, /addEventListener\('whatnow'/);
  assert.match(ov, /\/api\/whatnow/);
  assert.doesNotMatch(ov, /sendInputEvent|robotjs|keybd_event/);
  assert.match(read('dashboard/home.js'), /whatnow: '\/api\/whatnow'/);
  assert.match(read('dashboard/dashboard.js'), /'events', 'whatnow', 'ocr'\]/);
});
