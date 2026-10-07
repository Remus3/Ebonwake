'use strict';
// Plan 018: level cap 75 readiness on the client - one level range, XP patch
// epoch bodies and form, normalized epoch / kill-cap fields, buff presets and
// pre-patch hints, spot level gap and re-verify badge. No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const APP = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(APP, p), 'utf8');

test('level range matches the server: 75 accepted, 76 rejected everywhere', () => {
  assert.strictEqual(C.LEVEL_MAX, 75);
  const py = fs.readFileSync(path.join(APP, '..', 'server', 'ew', 'levels.py'), 'utf8');
  assert.match(py, /^LEVEL_MAX = 75$/m);
  assert.strictEqual(C.validLevelingBody({ sample: { level: 75, pct: 1 } }), true);
  assert.strictEqual(C.validLevelingBody({ sample: { level: 76, pct: 1 } }), false);
  assert.strictEqual(C.validProgressBody({ character: { level: 75 } }), true);
  assert.strictEqual(C.validProgressBody({ character: { level: 76 } }), false);
  assert.strictEqual(C.spotsPath('xp', { level: '75' }).ok, true);
  assert.strictEqual(C.spotsPath('xp', { level: '76' }).ok, false);
  assert.strictEqual(C.parseSampleForm({ level: '76', pct: '1' }).error, 'level must be a whole number 1-75');
  assert.deepStrictEqual(C.parseMilestones('75, 71').body, { milestones: [71, 75] });
  for (const f of ['dashboard/leveling.js', 'dashboard/progress.js', 'shared/ewcore.js']) {
    assert.doesNotMatch(read(f), /\b1-70\b|\[1, 70\]|numInput\(70\)/, f);
  }
});

const EP = { id: 'cap75', starts_utc: '2026-10-08T14:00:00Z', label: 'Lv 75 patch', source: 'patch notes', verified: true };

test('validLevelingBody: epoch_add / epoch_del shapes', () => {
  assert.strictEqual(C.validLevelingBody({ epoch_add: EP }), true);
  assert.strictEqual(C.validLevelingBody({ epoch_del: 'cap75' }), true);
  const bad = [
    { epoch_del: 'Bad Id' }, { epoch_del: 5 },
    { epoch_add: Object.assign({}, EP, { id: 'A' }) }, { epoch_add: Object.assign({}, EP, { starts_utc: '2026-10-08' }) },
    { epoch_add: Object.assign({}, EP, { starts_utc: '2026-10-08T14:00:00' }) },
    { epoch_add: Object.assign({}, EP, { label: '' }) }, { epoch_add: Object.assign({}, EP, { label: 'caf' + String.fromCharCode(233) }) },
    { epoch_add: Object.assign({}, EP, { source: 'x'.repeat(201) }) }, { epoch_add: Object.assign({}, EP, { verified: 1 }) },
    { epoch_add: Object.assign({}, EP, { x: 1 }) }, { epoch_add: { id: 'a' } }
  ];
  for (const b of bad) assert.strictEqual(C.validLevelingBody(b), false, JSON.stringify(b));
});

test('parseEpochForm: UTC start, id from label, operator errors', () => {
  const r = C.parseEpochForm({ id: '', start: '2026-10-08 14:00', label: ' Lv 75 patch ', source: 'NA notes', verified: true });
  assert.deepStrictEqual(r, { ok: true, body: { epoch_add: { id: 'lv-75-patch', starts_utc: '2026-10-08T14:00:00Z', label: 'Lv 75 patch', source: 'NA notes', verified: true } } });
  assert.ok(C.validLevelingBody(r.body));
  const keep = C.parseEpochForm({ id: 'cap75-xp-rescale', start: '2026-10-08T07:30', label: 'x', source: 's', verified: 'yes' });
  assert.strictEqual(keep.body.epoch_add.id, 'cap75-xp-rescale');
  assert.strictEqual(keep.body.epoch_add.verified, false, 'only a literal true verifies');
  for (const f of [{ start: '2026-10-08', label: 'a', source: 's' }, { start: '2026-10-08 25:00', label: 'a', source: 's' },
    { start: '2026-10-08 14:00', label: '', source: 's' }, { start: '2026-10-08 14:00', label: 'a', source: '' },
    { id: 'Bad Id', start: '2026-10-08 14:00', label: 'a', source: 's' }, { start: '2026-10-08 14:00', label: '!!!', source: 's' }]) {
    const x = C.parseEpochForm(f);
    assert.strictEqual(x.ok, false, JSON.stringify(f));
    assert.strictEqual(typeof x.error, 'string');
  }
});

const BODY = {
  now: '2026-10-09T12:00:00+00:00', level: 58, pct: 10, rate_pct_h: null, eta_next_s: null,
  next_milestone: 60, next_milestone_label: 'Rebirth of Darkness', xp_stack_pct: 0, xp_parts: [],
  hot: { active: [], next: null }, milestones: [56, 60, 61, 70, 75],
  milestone_labels: { 60: 'Rebirth of Darkness' }, milestones_seed: true, hot_windows: [],
  epoch: { id: 'cap75', label: 'Lv 75 patch', starts_utc: '2026-10-08T00:00:00+00:00', source: 's', verified: false, starts_in_s: -129600 },
  epoch_next: null,
  epochs: [{ id: 'cap75', label: 'Lv 75 patch', starts_utc: '2026-10-08T00:00:00+00:00', source: 's', verified: false, starts_in_s: -129600, tracked: true }, null, { id: 'x' }],
  epoch_error: null, kill_xp_cap: 'cap ~0.2%',
  samples: [{ ts: '2026-10-09T11:00:00+00:00', level: 58, pct: 10, pre_patch: false },
    { ts: '2026-10-07T11:00:00+00:00', level: 58, pct: 1, pre_patch: true }]
};

test('normalizeLeveling carries epochs, kill cap and milestone labels', () => {
  const d = C.normalizeLeveling(BODY);
  assert.strictEqual(d.epoch.id, 'cap75');
  assert.strictEqual(d.epoch_next, null);
  assert.deepStrictEqual(d.epochs.map((e) => e.id), ['cap75']);
  assert.strictEqual(d.epochs[0].tracked, true);
  assert.strictEqual(d.kill_xp_cap, 'cap ~0.2%');
  assert.strictEqual(d.next_milestone_label, 'Rebirth of Darkness');
  assert.strictEqual(d.milestone_labels['60'], 'Rebirth of Darkness');
  assert.strictEqual(d.samples[1].pre_patch, true);
  assert.strictEqual(C.normalizeLeveling(Object.assign({}, BODY, { level: 75 })).level, 75);
  const old = C.normalizeLeveling({ milestones: [50] });  // a pre-018 server body
  assert.strictEqual(old.epoch, null);
  assert.deepStrictEqual(old.epochs, []);
  assert.strictEqual(old.kill_xp_cap, null);
  assert.deepStrictEqual(old.milestone_labels, {});
});

test('epochText: countdown, live-since and verify flag', () => {
  assert.strictEqual(C.epochText({ label: 'Lv 75 patch', starts_in_s: 2 * 86400 + 3 * 3600 + 60, verified: false }), 'Lv 75 patch in 51h01m (verify date)');
  assert.strictEqual(C.epochText({ label: 'Lv 75 patch', starts_in_s: -3600, verified: true }), 'Lv 75 patch live 1h00m ago');
  assert.strictEqual(C.epochText(null), '');
});

test('buff presets and pre-patch hint', () => {
  const d = { xp_presets: [{ name: 'Body Enhancement', xp_pct: 100, notes: 'verify', source: 's', verified: '2026-10-05' },
    { name: '', xp_pct: 5 }, { name: 'X', xp_pct: 1001 }, null] };
  const p = C.xpPresets(d);
  assert.deepStrictEqual(p.map((x) => [x.name, x.xp_pct]), [['Body Enhancement', 100]]);
  assert.match(p[0].title, /verify/);
  assert.deepStrictEqual(C.xpPresets(null), []);
  const rows = C.buffRows([{ id: 'be', name: 'Body Enhancement', ends: null, xp_pct: 50, xp_hint: 'pre-patch value?' }], 0, 0);
  assert.strictEqual(rows[0].xp_hint, 'pre-patch value?');
  assert.strictEqual(rows[rows.length - 1].xp_hint, null);
  // the server tracked file agrees with the plan values
  const tracked = JSON.parse(fs.readFileSync(path.join(APP, '..', 'server', 'ew', 'data', 'xp_buffs.json'), 'utf8'));
  assert.deepStrictEqual(tracked.map((r) => [r.name, r.xp_pct, r.pre_patch_xp_pct]),
    [['Body Enhancement', 100, 50], ['Adventure Blessing', 30, 15], ['Pearl outfit set', 50, 10]]);
});

test('spotGapText', () => {
  assert.strictEqual(C.spotGapText({ level_gap: 2, outlevel_dr: 6 }), 'Lv +2 vs mob: +6 DR');
  assert.strictEqual(C.spotGapText({ level_gap: -3, outlevel_dr: 0 }), 'Lv -3 vs mob');
  assert.strictEqual(C.spotGapText({ level_gap: 0, outlevel_dr: 0 }), 'Lv 0 vs mob');
  assert.strictEqual(C.spotGapText({ level_gap: null }), '');
  assert.strictEqual(C.spotGapText(null), '');
});

test('cards wire the plan 018 fields with safe DOM', () => {
  const lev = read('dashboard/leveling.js');
  for (const re of [/C\.parseEpochForm\(/, /epoch_del/, /C\.epochText\(/, /kill_xp_cap/, /pre_patch/, /next_milestone_label/, /C\.LEVEL_MAX/]) {
    assert.match(lev, re);
  }
  const g = read('dashboard/grind.js');
  for (const re of [/C\.xpPresets\(/, /xp_hint/, /C\.spotGapText\(/, /re-verify after patch/, /reverify/]) assert.match(g, re);
  for (const src of [lev, g]) {
    assert.doesNotMatch(src, /innerHTML|outerHTML|insertAdjacentHTML|document\.write/);
    assert.doesNotMatch(src, /method:\s*'POST'/);
  }
});
