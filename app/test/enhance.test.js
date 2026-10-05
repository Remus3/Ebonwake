'use strict';
// Plan 035: pure enhancement EV helpers (ewcore.js: parseFs, enhanceSubSteps,
// enhanceFamilyGuess, enhancePath, fmtEv, evLine) and static guards on the
// Deadeye tab's EV panel. No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const read = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');

test('parseFs: whole numbers 0..999 only', () => {
  assert.strictEqual(C.parseFs('0'), 0);
  assert.strictEqual(C.parseFs(' 150 '), 150);
  assert.strictEqual(C.parseFs('999'), 999);
  assert.strictEqual(C.parseFs(42), 42);
  for (const bad of ['', '-1', '1000', '1.5', 'abc', '1e2', null, undefined, NaN, -3]) {
    assert.strictEqual(C.parseFs(bad), null, String(bad));
  }
});

test('enhanceSubSteps: levels reached in (current, target]', () => {
  assert.deepStrictEqual(C.enhanceSubSteps('+15', 'PRI'), ['PRI']);
  assert.deepStrictEqual(C.enhanceSubSteps('PRI', 'TET'), ['DUO', 'TRI', 'TET']);
  assert.deepStrictEqual(C.enhanceSubSteps('+13', '+15'), ['+14', '+15']);
  assert.deepStrictEqual(C.enhanceSubSteps('TET', 'PRI'), []);
  assert.deepStrictEqual(C.enhanceSubSteps('nope', 'PRI'), []);
});

test('enhanceFamilyGuess: family named in the item text', () => {
  const fams = ['blackstar', 'edana', 'kharazad', 'sovereign'];
  assert.strictEqual(C.enhanceFamilyGuess('Sovereign Longbow', fams), 'sovereign');
  assert.strictEqual(C.enhanceFamilyGuess('Kharazad Ring', fams), 'kharazad');
  assert.strictEqual(C.enhanceFamilyGuess('Kzarka Longbow', fams), null);
  assert.strictEqual(C.enhanceFamilyGuess(null, fams), null);
  assert.strictEqual(C.enhanceFamilyGuess('Sovereign', null), null);
});

test('enhancePath: query for one step, or null', () => {
  assert.strictEqual(C.enhancePath('sovereign', 'TET', '100', true),
    '/api/deadeye/enhance?family=sovereign&step=TET&fs=100&crons=1');
  assert.strictEqual(C.enhancePath('edana', '+15', 0, false),
    '/api/deadeye/enhance?family=edana&step=%2B15&fs=0&crons=0');
  assert.strictEqual(C.enhancePath('Bad', 'TET', 1, false), null);
  assert.strictEqual(C.enhancePath('sovereign', '', 1, false), null);
  assert.strictEqual(C.enhancePath('sovereign', 'TET', '1000', false), null);
});

test('fmtEv: numbers to display strings, missing -> "-"', () => {
  const f = C.fmtEv({
    step: 'TET', chance_pct: 10.01, approx: false, attempts_mean: 8.6066, attempts_p90: 21,
    pity_cap: 21, crons_mean: 6713.1, cost_mean_silver: 2.0139e10, cost_note: null, verified: false
  });
  assert.deepStrictEqual(f, {
    step: 'TET', chance: '10.01%', attempts: '8.6', p90: '21', pity: '21', crons: '6.71K',
    silver: '20.1B', note: '', unverified: true
  });
  const g = C.fmtEv({ step: 'PRI', chance_pct: 48.9, approx: true, attempts_mean: 1234.5, attempts_p90: 3,
    pity_cap: null, crons_mean: 0, cost_mean_silver: null, cost_note: 'no cached market price', verified: true });
  assert.strictEqual(g.chance, '~48.90%');
  assert.strictEqual(g.attempts, '1.23K');
  assert.strictEqual(g.pity, '-');
  assert.strictEqual(g.crons, '-');
  assert.strictEqual(g.silver, '-');
  assert.strictEqual(g.note, 'no cached market price');
  assert.strictEqual(g.unverified, false);
  // unverified_used wins over the row-level flag (crons off on a preview-cron row)
  assert.strictEqual(C.fmtEv({ verified: false, unverified_used: [] }).unverified, false);
  assert.strictEqual(C.fmtEv({ verified: false, unverified_used: ['crons_per_attempt'] }).unverified, true);
  const h = C.fmtEv(null);
  assert.strictEqual(h.chance, '-');
  assert.strictEqual(h.attempts, '-');
});

test('evLine: one compact line per step', () => {
  const s = C.evLine({ step: 'PEN', chance_pct: 7.5, approx: false, attempts_mean: 12.25, attempts_p90: 30,
    pity_cap: 31, crons_mean: 0, cost_mean_silver: null, verified: false });
  assert.strictEqual(s, 'PEN  7.50%  12.3 tries (p90 30, pity 31)  crons -  silver -  [unverified]');
});

test('deadeye.js EV panel: GETs the enhance route through ewcore helpers, no new HTML sink', () => {
  const src = read('dashboard/deadeye.js');
  assert.match(src, /\/api\/deadeye\/enhance/);
  for (const f of ['enhancePath', 'enhanceFamilyGuess', 'enhanceSubSteps', 'fmtEv', 'evLine', 'parseFs']) {
    assert.match(src, new RegExp('C\\.' + f + '\\('), f);
  }
  assert.strictEqual((src.match(/innerHTML/g) || []).length, 1);
  assert.match(read('shared/ew.css'), /\.ew-dev/);
});
