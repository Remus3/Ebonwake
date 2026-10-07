'use strict';
// Plan 085: patch-notes verdicts on tracked data rows. patchNote / epochTitle /
// xpPresets / dropView check badge / signalRows lines (ewcore.js), plus static
// guards on the leveling, grind and System wiring. No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const APP = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(APP, p), 'utf8');

test('patchNote: confirmed, contradicted, silent / junk', () => {
  assert.deepStrictEqual(C.patchNote({ verdict: 'confirmed', date: '2026-10-08', evidence: 'x' }),
    { check: false, text: 'verified by patch notes 2026-10-08' });
  const c = C.patchNote({ verdict: 'contradicted', date: '2026-10-08', evidence: 'from 15% to 30%' });
  assert.strictEqual(c.check, true);
  assert.strictEqual(c.text, 'check - patch notes 2026-10-08 disagree: "from 15% to 30%"');
  assert.strictEqual(C.patchNote({ verdict: 'silent' }), null);
  assert.strictEqual(C.patchNote(null), null);
  assert.strictEqual(C.patchNote('confirmed'), null);
});

test('epoch brief carries the verdict into the source tooltip', () => {
  const ep = { source: 'Global Lab', patch: C.patchNote({ verdict: 'confirmed', date: '2026-10-08' }) };
  assert.strictEqual(C.epochTitle(ep), 'source: Global Lab\nverified by patch notes 2026-10-08');
  assert.strictEqual(C.epochTitle({ source: '', patch: null }), '');
  assert.strictEqual(C.epochTitle(null), '');
});

test('xp presets: check flag and tooltip line', () => {
  const p = C.xpPresets({ xp_presets: [
    { name: 'Adventure Blessing', xp_pct: 30, notes: 'n', source: 's', verified: '2026-10-05',
      patch: { verdict: 'contradicted', date: '2026-10-08', evidence: 'e' } },
    { name: 'Body Enhancement', xp_pct: 100, notes: 'n', source: 's', verified: '2026-10-08',
      patch: { verdict: 'confirmed', date: '2026-10-08' } },
    { name: 'Pearl outfit set', xp_pct: 50, notes: 'n', source: 's', verified: '2026-10-05' }] });
  assert.deepStrictEqual(p.map((x) => x.check), [true, false, false]);
  assert.match(p[0].title, /check - patch notes 2026-10-08 disagree/);
  assert.match(p[1].title, /verified by patch notes 2026-10-08/);
});

test('drop toggles: check flag on a contradicted row', () => {
  const v = C.dropView({ drops: { rate_capped: 0, cap_used: 300, active: [], buffs: [
    { id: 'eco', name: 'Ecology knowledge', bypass: 'none', rate_pct: 30, verified: false, source: 's',
      patch: { verdict: 'contradicted', date: '2026-10-08', evidence: 'to 20%' } },
    { id: 'luck', name: 'Luck', bypass: 'none', rate_pct: 12.5, verified: '2026-10-05', source: 's' }] } });
  assert.deepStrictEqual(v.toggles.map((t) => t.check), [true, false]);
  assert.match(v.toggles[0].title, /disagree: "to 20%"/);
});

test('signalRows passes evidence lines, junk dropped', () => {
  const rows = C.signalRows({ rows: [
    { id: 'data', name: 'Data rows', level: 'warn', detail: '1 data row contradicted by patch notes 2026-10-08 - see System',
      lines: ['xp_buffs.json#adventure-blessing: from 15% to 30%', 3, ''] },
    { id: 'market', name: 'Market', level: 'ok' }] });
  assert.deepStrictEqual(rows[0].lines, ['xp_buffs.json#adventure-blessing: from 15% to 30%']);
  assert.deepStrictEqual(rows[1].lines, []);
});

test('wiring: leveling tooltip, grind check badge, System evidence lines', () => {
  assert.match(read('dashboard/leveling.js'), /C\.epochTitle\(/);
  assert.match(read('dashboard/leveling.js'), /'check'/);
  assert.match(read('dashboard/grind.js'), /t\.check \? ' check'/);
  assert.match(read('dashboard/grind.js'), /p\.check \? ' \(check\)'/);
  assert.match(read('dashboard/dashboard.js'), /r\.lines\.forEach/);
});
