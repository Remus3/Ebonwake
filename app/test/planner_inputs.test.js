'use strict';
// Plan 081: derived planner inputs - source + age text, stale marker, the
// inventory derived lines, Imperial CP from a screenshot, the typed Hot Time
// window end and the grind loot prefill. No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const APP = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(APP, p), 'utf8');
const NOW = Date.parse('2026-10-06T12:00:00Z');

test('derivedText: source and age, stale says so', () => {
  assert.deepStrictEqual(C.derivedText({ source: 'ocr:inv.jpg', age_s: 600, stale: false }),
    { text: 'from screenshot, 10m ago', stale: false });
  assert.deepStrictEqual(C.derivedText({ source: 'sessions:3', sessions: 3, age_s: 7200 }),
    { text: 'from 3 play sessions, 2h ago', stale: false });
  assert.deepStrictEqual(C.derivedText({ source: 'typed', age_s: 99 }), { text: 'typed', stale: false });
  assert.deepStrictEqual(C.derivedText({ source: 'operator' }), { text: 'typed', stale: false });
  assert.deepStrictEqual(C.derivedText({ source: 'ocr', age_s: 8 * 86400, stale: true }),
    { text: 'from screenshot, 8d ago - stale', stale: true });
  assert.deepStrictEqual(C.derivedText(null), { text: '', stale: false });
});

test('invDerivedLines: base LT, slots, weight now and fame with their sources', () => {
  const v = {
    base_lt: 1260, slots: { base: 176, used: 48 },
    inputs: {
      base_lt: { source: 'ocr:inv.jpg', age_s: 300, stale: false },
      slots: { source: 'ocr:inv.jpg', age_s: 300, stale: false },
      slots_used: { source: 'typed', age_s: 10, stale: false }
    },
    weight_now: { used: 812.35, max: 1560, source: 'ocr:inv.jpg', age_s: 300, stale: false },
    fame: { value: 1.25, source: 'ocr', age_s: 8 * 86400, stale: true }
  };
  const lines = C.invDerivedLines(v);
  assert.deepStrictEqual(lines.map((l) => l.text), [
    'base LT 1260 (from screenshot, 5m ago)', 'slots 176 (from screenshot, 5m ago)',
    'slots used 48 (typed)', 'weight 812.35 / 1560 LT (from screenshot, 5m ago)',
    'fame bonus 1.25% (from screenshot, 8d ago - stale)']);
  assert.strictEqual(lines[4].stale, true);
  assert.deepStrictEqual(C.invDerivedLines(null), []);
  assert.deepStrictEqual(C.invDerivedLines({ inputs: { base_lt: { source: 'default' } }, base_lt: null }), []);
});

test('imperialCpText: a screenshot CP shows its source and age', () => {
  assert.strictEqual(C.imperialCpText({ cp: { value: 312, source: 'ocr', age_s: 60, stale: false } }),
    'CP 312 (from screenshot, 1m ago)');
  assert.strictEqual(C.imperialCpText({ cp: { value: 80, source: 'operator', typed: 80 } }), 'CP 80 (typed)');
});

test('hotUntilText: until, ended and pre-081 rows', () => {
  const opts = { zone: 'utc', now: NOW };
  assert.deepStrictEqual(C.hotUntilText({ until: '2026-10-13T12:00:00+00:00', ended: false }, opts),
    { text: 'until 10-13 12:00', ended: false });
  assert.deepStrictEqual(C.hotUntilText({ until: '2026-10-01T12:00:00+00:00', ended: true }, opts),
    { text: 'ended 10-01 12:00', ended: true });
  assert.deepStrictEqual(C.hotUntilText({ until: null }, opts), { text: 'no end set', ended: false });
});

test('lootPrefill: fills untyped boxes, marks low, never overwrites typing', () => {
  const p = { source: 'ocr', at: '2026-10-06T11:50:00+00:00', items: [
    { name: 'Shard', count: 30, low: false }, { name: 'Crystal', count: 2, low: true },
    { name: 'Typed', count: 9 }, { name: 'Bad', count: 0 }] };
  const r = C.lootPrefill(p, {}, { Typed: true }, NOW);
  assert.deepStrictEqual(r.counts, { Shard: '30', Crystal: '2' });
  assert.deepStrictEqual(r.low, ['Crystal']);
  assert.strictEqual(r.text, '2 counts from screenshots this session, 10m ago - type to correct');
  assert.deepStrictEqual(C.lootPrefill(null, {}, {}), { counts: {}, low: [], text: '' });
});

test('cards wire the plan 081 helpers', () => {
  assert.match(read('dashboard/deadeye.js'), /C\.derivedText\(p\[1\]\)/);
  assert.match(read('dashboard/inventory.js'), /C\.invDerivedLines\(d\)/);
  assert.match(read('dashboard/leveling.js'), /C\.hotUntilText\(w/);
  assert.match(read('dashboard/grind.js'), /C\.lootPrefill\(p, S\.lootCounts, S\.lootTyped\)/);
  assert.match(read('dashboard/imperial.js'), /ew-stale/);
});
