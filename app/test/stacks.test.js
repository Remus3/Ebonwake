'use strict';
// Plan 036: failstack bank, Agris pity and cron budget helpers (ewcore.js:
// validDeadeyeBody stacks ops, parseFsForm, parseAgrisForm, parseCronsForm,
// fmtStacks) and static guards on the Deadeye tab's Stacks card. No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const read = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');

test('validDeadeyeBody: stacks ops exact shapes', () => {
  const ok = [
    { fs_add: { kind: 'advice', value: 50 } },
    { fs_add: { kind: 'cry', value: 999, count: 999 } },
    { fs_use: { kind: 'saved', value: 1, count: 1 } },
    { agris_set: { family: 'sovereign', step: 'TET', stacks: 0 } },
    { agris_set: { family: 'kharazad', step: 'DEC', stacks: 1000 } },
    { crons_set: { owned: 0 } },
    { crons_set: { owned: 1e9, weekly_income: 1e7 } }
  ];
  for (const b of ok) assert.strictEqual(C.validDeadeyeBody(b), true, JSON.stringify(b));
  const bad = [
    { fs_add: { kind: 'nope', value: 50 } },
    { fs_add: { kind: 'advice', value: 0 } },
    { fs_add: { kind: 'advice', value: 1000 } },
    { fs_add: { kind: 'advice', value: 1.5 } },
    { fs_add: { kind: 'advice' } },
    { fs_add: { kind: 'advice', value: 5, count: 0 } },
    { fs_add: { kind: 'advice', value: 5, x: 1 } },
    { fs_use: 'advice' },
    { agris_set: { family: 'Sov', step: 'TET', stacks: 1 } },
    { agris_set: { family: 'sovereign', step: '+0', stacks: 1 } },
    { agris_set: { family: 'sovereign', step: 'TET', stacks: 1001 } },
    { agris_set: { family: 'sovereign', step: 'TET' } },
    { crons_set: {} },
    { crons_set: { owned: -1 } },
    { crons_set: { weekly_income: 1e7 + 1 } },
    { crons_set: { owned: 1, x: 1 } },
    { rate_set: {} }
  ];
  for (const b of bad) assert.strictEqual(C.validDeadeyeBody(b), false, JSON.stringify(b));
  assert.deepStrictEqual(C.FS_KINDS, ['advice', 'saved', 'cry']);
  assert.strictEqual(C.ENHANCE_STEPS.length, 25);
});

test('parseFsForm: add / use, count defaults to 1 and is omitted', () => {
  assert.deepStrictEqual(C.parseFsForm({ kind: 'advice', value: ' 120 ', count: '' }),
    { ok: true, body: { fs_add: { kind: 'advice', value: 120 } } });
  assert.deepStrictEqual(C.parseFsForm({ kind: 'saved', value: '40', count: '3' }, true),
    { ok: true, body: { fs_use: { kind: 'saved', value: 40, count: 3 } } });
  assert.strictEqual(C.parseFsForm({ kind: 'x', value: '1' }).ok, false);
  assert.match(C.parseFsForm({ kind: 'cry', value: '0' }).error, /FS/);
  assert.match(C.parseFsForm({ kind: 'cry', value: '10', count: '1000' }).error, /count/);
  for (const r of [C.parseFsForm({ kind: 'cry', value: '30', count: '2' }), C.parseFsForm({ kind: 'cry', value: '30' }, true)]) {
    assert.strictEqual(C.validDeadeyeBody(r.body), true);
  }
});

test('parseAgrisForm and parseCronsForm', () => {
  assert.deepStrictEqual(C.parseAgrisForm({ family: 'sovereign', step: 'PEN', stacks: '12' }),
    { ok: true, body: { agris_set: { family: 'sovereign', step: 'PEN', stacks: 12 } } });
  assert.strictEqual(C.parseAgrisForm({ family: '', step: 'PEN', stacks: '1' }).ok, false);
  assert.strictEqual(C.parseAgrisForm({ family: 'edana', step: 'ZZ', stacks: '1' }).ok, false);
  assert.match(C.parseAgrisForm({ family: 'edana', step: 'PRI', stacks: '-1' }).error, /stacks/);
  assert.deepStrictEqual(C.parseCronsForm({ owned: '12,500', weekly_income: '' }),
    { ok: true, body: { crons_set: { owned: 12500 } } });
  assert.deepStrictEqual(C.parseCronsForm({ owned: '0', weekly_income: '700' }),
    { ok: true, body: { crons_set: { owned: 0, weekly_income: 700 } } });
  assert.strictEqual(C.parseCronsForm({ owned: ' ', weekly_income: '' }).ok, false);
  assert.match(C.parseCronsForm({ weekly_income: '10000001' }).error, /weekly income/);
});

test('fmtStacks: bank, Agris N fails, advice and budget lines', () => {
  const f = C.fmtStacks({
    fs_bank: [{ kind: 'advice', value: 100, count: 2 }, { kind: 'cry', value: 30, count: 1 }],
    agris: [{ family: 'sovereign', step: 'TET', stacks: 7, threshold: 20, fails_to_guarantee: 13 },
      { family: 'kharazad', step: 'PRI', stacks: 3, threshold: 3, fails_to_guarantee: 0 },
      { family: 'edana', step: 'PRI', stacks: 1, threshold: null, fails_to_guarantee: null }],
    crons: { owned: 1000, weekly_income: 500 },
    advice: { step_id: 'd1', item: 'Sovereign Ring', family: 'sovereign', level: 'TET', softcap_fs: 100,
      suggest: { kind: 'advice', value: 100 }, agris: { stacks: 7, threshold: 20, fails_to_guarantee: 13 }, reason: null },
    budget: { needed: 7466.2, owned: 1000, gap: 6466.2, weekly_income: 500, weeks: 13, lines: [],
      unknown: [{ step_id: 'd2', family: 'kharazad', level: 'DUO' }] }
  });
  assert.deepStrictEqual(f.bank.map((b) => b.text), ['Advice of Valks 100 x2', "Valks' Cry 30 x1"]);
  assert.deepStrictEqual(f.agris.map((a) => a.text), [
    'sovereign TET: 7 stacks, guaranteed in 13 fails',
    'kharazad PRI: 3 stacks, next attempt guaranteed',
    'edana PRI: 1 stacks, no Agris threshold']);
  assert.strictEqual(f.advice, 'Sovereign Ring -> TET (soft cap 100): use Advice of Valks 100; Agris guaranteed in 13 fails');
  assert.strictEqual(f.budget, 'crons 1,000 owned / 7,467 expected, short 6,467 (~13 weeks)');
  assert.strictEqual(f.unknown, 'no chance data (not counted): kharazad DUO');
});

test('fmtStacks: reasons, covered, no income, junk input', () => {
  const r = C.fmtStacks({ advice: { level: null, reason: 'no open plan step' },
    budget: { needed: 0, owned: 0, gap: 0, weeks: 0, unknown: [] } });
  assert.strictEqual(r.advice, 'no open plan step');
  assert.strictEqual(r.budget, 'crons 0 owned / 0 expected, covered');
  assert.strictEqual(r.unknown, '');
  const n = C.fmtStacks({ advice: { item: 'Kharazad Ring', level: 'DUO', softcap_fs: null, suggest: null,
    agris: { stacks: 0, threshold: 5, fails_to_guarantee: 1 }, reason: 'no soft cap for this level' },
  budget: { needed: 10, owned: 2, gap: 8, weeks: null } });
  assert.strictEqual(n.advice, 'Kharazad Ring -> DUO: no soft cap for this level; Agris guaranteed in 1 fail');
  assert.strictEqual(n.budget, 'crons 2 owned / 10 expected, short 8 (no weekly income set)');
  const j = C.fmtStacks('junk');
  assert.deepStrictEqual(j.bank, []);
  assert.strictEqual(j.advice, '-');
  assert.strictEqual(j.budget, 'crons - owned / - expected');
});

test('deadeye.js Stacks card: ewcore helpers, bridge POSTs, no HTML sink', () => {
  const src = read('dashboard/deadeye.js');
  assert.match(src, /Stacks/);
  for (const fn of ['fmtStacks', 'parseFsForm', 'parseAgrisForm', 'parseCronsForm']) {
    assert.ok(src.indexOf('C.' + fn + '(') >= 0, fn);
  }
  // the markdown preview stays the only innerHTML sink
  assert.strictEqual((src.match(/innerHTML/g) || []).length, 1);
});
