'use strict';
// Plan 013: season pass by objective - pure helpers (ewcore.js), the
// /api/progress bridge guard for the new ops, the overlay opt-in line and
// static guards on the Progress tab season card. No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const APP = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(APP, p), 'utf8');

test('fmtTarget: level Lv N, gear +N then PRI..PEN, none otherwise', () => {
  assert.strictEqual(C.fmtTarget('level', 50), 'Lv 50');
  assert.strictEqual(C.fmtTarget('gear', 10), '+10');
  assert.strictEqual(C.fmtTarget('gear', 15), '+15');
  assert.strictEqual(C.fmtTarget('gear', 16), 'PRI');
  assert.strictEqual(C.fmtTarget('gear', 20), 'PEN');
  assert.strictEqual(C.fmtTarget('gear', null), '');
  assert.strictEqual(C.fmtTarget('quest', null), '');
  assert.strictEqual(C.fmtTarget('level', 'x'), '');
});

test('seasonLine: "Pass 23/40 - next: Lv 50 (2 lv)" and its variants', () => {
  const s = { track: 'sp', done: 23, total: 40, unclaimed: [], level: 48,
    next: [{ id: 'reach-lv-50', kind: 'level', target: 50, gap: 2, title: 'Reach Lv 50', label: 'Lv 50' }] };
  assert.strictEqual(C.seasonLine(s), 'Pass 23/40 - next: Lv 50 (2 lv)');
  assert.strictEqual(C.seasonLine(Object.assign({}, s, { next: [Object.assign({}, s.next[0], { gap: null })] })),
    'Pass 23/40 - next: Lv 50');
  assert.strictEqual(C.seasonLine(Object.assign({}, s, { next: [{ id: 'q', kind: 'quest', target: null, gap: null, title: 'Season quest: Calpheon' }] })),
    'Pass 23/40 - next: Season quest: Calpheon');
  assert.strictEqual(C.seasonLine(Object.assign({}, s, { next: [{ id: 'g', kind: 'gear', target: 16, gap: null, title: 'Tuvala armor PRI' }] })),
    'Pass 23/40 - next: Tuvala armor PRI');
  assert.strictEqual(C.seasonLine(Object.assign({}, s, { unclaimed: ['a', 'b'] })),
    'Pass 23/40 - next: Lv 50 (2 lv) | claim 2');
  assert.strictEqual(C.seasonLine({ track: 'sp', done: 40, total: 40, unclaimed: [], next: [] }), 'Pass 40/40 - all done');
  assert.strictEqual(C.seasonLine(null), 'no season pass track');
  assert.strictEqual(C.seasonLine({}), 'no season pass track');
});

test('validProgressBody: claim / obj_add / obj_edit / obj_del shapes', () => {
  const ok = [
    { claim: { track: 'season-pass', objective: 'graduate', claimed: true } },
    { claim: { track: 'season-pass', objective: 'graduate', claimed: false } },
    { obj_add: { track: 'season-pass', title: 'Reach Lv 61', kind: 'level', target: 61 } },
    { obj_add: { track: 'season-pass', title: 'Box', kind: 'other', reward: 'Cron x10' } },
    { obj_add: { track: 'season-pass', title: 'Gear', kind: 'gear', target: null, reward: '' } },
    { obj_edit: { track: 'season-pass', objective: 'graduate', reward: 'Naru' } },
    { obj_edit: { track: 'season-pass', objective: 'graduate', kind: 'level', target: 60 } },
    { obj_del: { track: 'season-pass', objective: 'graduate' } }
  ];
  ok.forEach((b) => assert.strictEqual(C.validProgressBody(b), true, JSON.stringify(b)));
  const bad = [
    { claim: { track: 'season-pass', objective: 'graduate' } },
    { claim: { track: 'season-pass', objective: 'graduate', claimed: 1 } },
    { claim: { track: 'BAD', objective: 'graduate', claimed: true } },
    { obj_add: { track: 'season-pass', title: 'x', kind: 'bogus' } },
    { obj_add: { track: 'season-pass', kind: 'other' } },
    { obj_add: { track: 'season-pass', title: 'x', kind: 'other', target: 1.5 } },
    { obj_add: { track: 'season-pass', title: 'x', kind: 'other', reward: 'a'.repeat(81) } },
    { obj_add: { track: 'season-pass', title: 'x', kind: 'other', extra: 1 } },
    { obj_edit: { track: 'season-pass', objective: 'graduate' } },
    { obj_edit: { track: 'season-pass', objective: 'graduate', nope: 1 } },
    { obj_del: { track: 'season-pass' } },
    { obj_del: 'graduate' }
  ];
  bad.forEach((b) => assert.strictEqual(C.validProgressBody(b), false, JSON.stringify(b)));
  assert.strictEqual(C.validPost('/api/progress', ok[0]), true);
});

test('parseObjectiveForm: title/kind/target/reward -> obj_add body or an error', () => {
  assert.deepStrictEqual(C.parseObjectiveForm('season-pass', { title: ' Reach Lv 61 ', kind: 'level', target: '61', reward: ' Ring ' }),
    { ok: true, body: { obj_add: { track: 'season-pass', title: 'Reach Lv 61', kind: 'level', target: 61, reward: 'Ring' } } });
  assert.deepStrictEqual(C.parseObjectiveForm('season-pass', { title: 'Q', kind: 'quest', target: '', reward: '' }),
    { ok: true, body: { obj_add: { track: 'season-pass', title: 'Q', kind: 'quest', target: null, reward: '' } } });
  assert.deepStrictEqual(C.parseObjectiveForm('season-pass', { title: 'G', kind: 'gear', target: 'PRI', reward: '' }).body.obj_add.target, 16);
  assert.strictEqual(C.parseObjectiveForm('season-pass', { title: '', kind: 'other' }).ok, false);
  assert.strictEqual(C.parseObjectiveForm('season-pass', { title: 'x', kind: 'level', target: '' }).ok, false);
  assert.strictEqual(C.parseObjectiveForm('season-pass', { title: 'x', kind: 'level', target: '76' }).ok, false);
  assert.strictEqual(C.parseObjectiveForm('season-pass', { title: 'x', kind: 'gear', target: '21' }).ok, false);
  assert.strictEqual(C.parseObjectiveForm('season-pass', { title: 'x', kind: 'quest', target: '3' }).ok, false);
  assert.strictEqual(C.parseObjectiveForm('season-pass', { title: 'x', kind: 'nope' }).ok, false);
  for (const b of [C.parseObjectiveForm('season-pass', { title: 'Reach Lv 61', kind: 'level', target: '61', reward: '' })]) {
    assert.strictEqual(C.validProgressBody(b.body), true);
  }
});

test('parseObjectiveForm with an objective id -> obj_edit body the guard accepts; targetInput round-trips', () => {
  const r = C.parseObjectiveForm('season-pass', { title: 'Season quest: Balenos', kind: 'quest', target: '', reward: 'Cron x10' }, 'season-quest-balenos');
  assert.deepStrictEqual(r, { ok: true, body: { obj_edit: { objective: 'season-quest-balenos', track: 'season-pass',
    title: 'Season quest: Balenos', kind: 'quest', target: null, reward: 'Cron x10' } } });
  assert.strictEqual(C.validProgressBody(r.body), true);
  assert.strictEqual(C.parseObjectiveForm('season-pass', { title: '', kind: 'quest' }, 'x').ok, false);
  assert.strictEqual(C.targetInput('gear', 16), 'PRI');
  assert.strictEqual(C.targetInput('gear', 10), '10');
  assert.strictEqual(C.targetInput('gear', null), '');
  assert.strictEqual(C.targetInput('level', 50), '50');
  assert.strictEqual(C.targetInput('quest', null), '');
  for (const [k, t] of [['gear', 16], ['gear', 7], ['level', 55]]) {
    const b = C.parseObjectiveForm('sp', { title: 'x', kind: k, target: C.targetInput(k, t), reward: '' }).body.obj_add;
    assert.strictEqual(b.target, t);
  }
  assert.match(read('dashboard/progress.js'), /obj_edit|parseObjectiveForm\([^)]*,[^)]*,/);
});

test('withStep updates a season objective and clears its claim on untick', () => {
  const data = { tracks: [{ id: 'sp', kind: 'season',
    steps: [{ id: 'a', title: 'A', done_at: '2026-10-04T00:00:00Z' }, { id: 'b', title: 'B', done_at: null }],
    objectives: [{ id: 'a', title: 'A', kind: 'other', done_at: '2026-10-04T00:00:00Z', claimed_at: '2026-10-04T01:00:00Z' },
      { id: 'b', title: 'B', kind: 'other', done_at: null, claimed_at: null }] }] };
  const off = C.withStep(data, 'sp', 'a', null);
  assert.strictEqual(off.tracks[0].objectives[0].done_at, null);
  assert.strictEqual(off.tracks[0].objectives[0].claimed_at, null);
  assert.strictEqual(off.tracks[0].done, 0);
  const on = C.withStep(data, 'sp', 'b', '2026-10-05T00:00:00Z');
  assert.strictEqual(on.tracks[0].objectives[1].done_at, '2026-10-05T00:00:00Z');
  assert.strictEqual(on.tracks[0].done, 2);
  assert.strictEqual(data.tracks[0].objectives[0].claimed_at, '2026-10-04T01:00:00Z'); // input untouched
});

test('seasonGroups: next open (3), done-but-unclaimed, rest', () => {
  const o = (id, done, claimed) => ({ id: id, title: id, kind: 'other', done_at: done ? 'x' : null, claimed_at: claimed ? 'y' : null });
  const t = { objectives: [o('a', 1, 1), o('b', 1, 0), o('c', 0), o('d', 0), o('e', 0), o('f', 0), o('g', 1, 0)] };
  const g = C.seasonGroups(t);
  assert.deepStrictEqual(g.next.map((x) => x.id), ['c', 'd', 'e']);
  assert.deepStrictEqual(g.unclaimed.map((x) => x.id), ['b', 'g']);
  assert.deepStrictEqual({ done: g.done, total: g.total, claimed: g.claimed }, { done: 3, total: 7, claimed: 1 });
  assert.deepStrictEqual(C.seasonGroups(null), { next: [], unclaimed: [], done: 0, total: 0, claimed: 0 });
});

test('overlay widget season is opt-in (default off); example config lists it off', () => {
  assert.strictEqual(C.overlayWidgets({}).season, false);
  assert.strictEqual(C.overlayWidgets({ overlay: { widgets: { season: true } } }).season, true);
  assert.strictEqual(C.widgetsFromQuery('').season, false);
  assert.strictEqual(C.widgetsFromQuery('?' + new URLSearchParams(C.widgetsQuery({ season: true })).toString()).season, true);
  const ex = JSON.parse(fs.readFileSync(path.join(APP, '..', 'config', 'local.example.json'), 'utf8'));
  assert.ok(!('widgets' in ex.overlay), 'plan 080: no manual overlay layout in the example');
});

test('overlay: season line from GET /api/progress, opt-in row, no POST', () => {
  const src = read('overlay/overlay.js');
  assert.match(src, /getJSON\('\/api\/progress'\)/);
  assert.match(src, /C\.seasonLine\(/);
  assert.match(src, /W\.season/);
  assert.doesNotMatch(src, /method:\s*'POST'|ewApi/);
  const html = read('overlay/index.html');
  assert.match(html, /id="ov-season-row" hidden/);
  assert.match(html, /id="ov-season"/);
});

test('progress tab: season card with seed label, next, unclaimed highlight, claim via bridge', () => {
  const src = read('dashboard/progress.js');
  assert.match(src, /seed, verify against the in-game pass/);
  assert.match(src, /C\.seasonGroups\(/);
  assert.match(src, /claim:\s*\{/);
  assert.match(src, /obj_add|parseObjectiveForm/);
  assert.match(src, /obj_del/);
  assert.match(src, /ew-unclaimed/);
  assert.doesNotMatch(src, /innerHTML/);
  assert.match(read('shared/ew.css'), /\.ew-unclaimed/);
});
