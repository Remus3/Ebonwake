'use strict';
// Plan 043: pet roster card on the Progress tab. Pure helpers in ewcore.js
// (validPetsBody, parsePetForm, petRows, petCoverageLines, petExchangeLines)
// and static guards on the card module and the bridge. No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const APP = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(APP, p), 'utf8');
const SPECIES = [{ id: 'cat', name: 'Cat', skills: ['gathering_detection'], skill_names: ['gathering detection'] },
  { id: 'dragon', name: 'Dragon', skills: [], skill_names: [] }];

test('validPetsBody: exact shapes only', () => {
  const ok = [
    { add: { name: 'Kitty', species: 'cat', tier: 1 } },
    { add: { name: 'Kitty', species: 'cat', tier: 5, talents: ['Combat EXP'], alpha: true, out: true } },
    { edit: { id: 'kitty', tier: 3 } },
    { edit: { id: 'kitty', name: 'M', species: 'dog', talents: [], alpha: false, out: true } },
    { remove: 'kitty' }, { feed: 'kitty-2' },
    { goals: [] }, { goals: ['fishing', 'gathering'] }
  ];
  for (const b of ok) assert.strictEqual(C.validPetsBody(b), true, JSON.stringify(b));
  const bad = [
    null, [], {}, { nope: 1 }, { remove: 'a', feed: 'a' },
    { add: { name: 'K', species: 'cat' } },
    { add: { name: '', species: 'cat', tier: 1 } },
    { add: { name: 'K', species: 'Cat!', tier: 1 } },
    { add: { name: 'K', species: 'cat', tier: 6 } },
    { add: { name: 'K', species: 'cat', tier: 1.5 } },
    { add: { name: 'K', species: 'cat', tier: 1, talents: ['a', 'b', 'c', 'd', 'e', 'f'] } },
    { add: { name: 'K', species: 'cat', tier: 1, out: 'yes' } },
    { add: { name: 'K', species: 'cat', tier: 1, fed_at: 'x' } },
    { add: { name: 'K' + String.fromCharCode(233), species: 'cat', tier: 1 } },
    { edit: { id: 'kitty' } }, { edit: { tier: 2 } }, { edit: { id: 'Bad Id', tier: 2 } },
    { remove: 'Bad Id' }, { feed: 5 },
    { goals: 'fishing' }, { goals: ['fishing', 'fishing'] }, { goals: ['Bad'] }
  ];
  for (const b of bad) assert.strictEqual(C.validPetsBody(b), false, JSON.stringify(b));
});

test('validPost / labels: /api/pets is a bridge route', () => {
  assert.ok(C.POST_ROUTES.indexOf('/api/pets') >= 0);
  assert.strictEqual(C.validPost('/api/pets', { feed: 'kitty' }), true);
  assert.strictEqual(C.postToast('/api/pets', { ok: true }).text, 'Pets saved');
});

test('parsePetForm: add and edit bodies, talents split on commas', () => {
  const f = { name: ' Kitty ', species: 'cat', tier: '3', talents: 'Combat EXP, Item drop ,', out: true, alpha: false };
  assert.deepStrictEqual(C.parsePetForm(f), { ok: true, body: { add: {
    name: 'Kitty', species: 'cat', tier: 3, talents: ['Combat EXP', 'Item drop'], out: true, alpha: false } } });
  const e = C.parsePetForm(f, 'kitty');
  assert.deepStrictEqual(e.body, { edit: { id: 'kitty', name: 'Kitty', species: 'cat', tier: 3,
    talents: ['Combat EXP', 'Item drop'], out: true, alpha: false } });
  assert.strictEqual(C.validPetsBody(e.body), true);
  assert.match(C.parsePetForm(Object.assign({}, f, { name: ' ' })).error, /name/);
  assert.match(C.parsePetForm(Object.assign({}, f, { species: '' })).error, /type/);
  assert.match(C.parsePetForm(Object.assign({}, f, { tier: '9' })).error, /tier/);
  assert.match(C.parsePetForm(Object.assign({}, f, { alpha: true })).error, /T5/);
  assert.match(C.parsePetForm(Object.assign({}, f, { talents: 'a,b,c,d,e,f' })).error, /talents/);
});

test('petRows: label, skills, feed age; junk dropped', () => {
  const v = { roster: [
    { id: 'kitty', name: 'Kitty', species: 'cat', species_name: 'Cat', tier: 4, talents: ['Combat EXP'],
      skill_names: ['gathering detection'], alpha: false, out: true, fed_at: '2026-10-05T10:00:00+00:00', fed_ago_s: 5400 },
    { id: 'boss', name: 'Boss', species: 'dragon', species_name: 'Dragon', tier: 5, talents: [],
      skill_names: [], alpha: true, out: false, fed_at: null, fed_ago_s: null },
    { id: 'Bad Id' }, null
  ] };
  const rows = C.petRows(v);
  assert.strictEqual(rows.length, 2);
  assert.deepStrictEqual(rows[0], { id: 'kitty', name: 'Kitty', label: 'Cat T4', skills: 'gathering detection',
    talents: 'Combat EXP', out: true, alpha: false, tier: 4, species: 'cat', talentList: ['Combat EXP'],
    fed: 'fed 1h 30m ago' });
  assert.strictEqual(rows[1].label, 'Dragon T5 Alpha');
  assert.strictEqual(rows[1].skills, 'no special skill');
  assert.strictEqual(rows[1].fed, 'not fed yet');
  assert.deepStrictEqual(C.petRows(null), []);
});

test('petCoverageLines: out count, loot, alpha, goals, warnings', () => {
  const cov = { out: 2, max_out: 5, free_slots: 3,
    loot: { covered: true, pets: 2, t4_plus: 1, alpha: 'Boss', alpha_bonus_pct: 15 },
    goals: [{ id: 'detection', title: 'Detection', covered: true, by: ['Drake'], missing: [], missing_names: [] },
      { id: 'gathering', title: 'Gathering', covered: false, by: [], missing: ['gathering_amount'], missing_names: ['gathering amount'] }],
    missing: ['gathering'], warnings: ['alpha X is not out: no loot bonus'] };
  const lines = C.petCoverageLines({ coverage: cov });
  assert.deepStrictEqual(lines.map((l) => [l.text, l.cls]), [
    ['out 2/5 - loot ok, 1 at T4+, Alpha Boss +15% loot speed', 'ok'],
    ['Detection: Drake', 'ok'],
    ['Gathering: missing gathering amount', 'warn'],
    ['alpha X is not out: no loot bonus', 'warn']]);
  const none = C.petCoverageLines({ coverage: { out: 0, max_out: 5, loot: { covered: false, t4_plus: 0 }, goals: [], warnings: [] } });
  assert.deepStrictEqual(none.map((l) => l.cls), ['bad']);
  assert.match(none[0].text, /no pets out/);
  assert.deepStrictEqual(C.petCoverageLines(null), []);
});

test('petExchangeLines: chance, need, parents-destroyed warning', () => {
  const v = { exchange: [
    { species: 'cat', species_name: 'Cat', target_tier: 4, count: 6, use: 5, chance_pct: 100, need_for_full: 0,
      destroyed: ['A', 'B', 'C', 'D', 'E'], out_used: ['A'], warning: 'parents destroyed: A, B, C, D, E' },
    { species: 'otter', species_name: 'Otter', target_tier: 4, count: 2, use: 2, chance_pct: null, need_for_full: 3,
      destroyed: ['F', 'G'], out_used: [], warning: 'parents destroyed: F, G' }
  ] };
  const l = C.petExchangeLines(v);
  assert.deepStrictEqual(l.map((x) => x.text), [
    'Cat x5 -> T4: 100%',
    'Otter x2 -> T4: chance not sourced, 3 more for 100%']);
  assert.deepStrictEqual(l.map((x) => x.warn), [
    'parents destroyed: A, B, C, D, E (A is out)', 'parents destroyed: F, G']);
  assert.deepStrictEqual(C.petExchangeLines({ exchange: 'x' }), []);
});

test('speciesOptions: server species list, junk dropped', () => {
  assert.deepStrictEqual(C.petSpeciesOptions({ species: SPECIES.concat([{ id: 'Bad!' }, null]) }),
    [{ id: 'cat', label: 'Cat (gathering detection)' }, { id: 'dragon', label: 'Dragon' }]);
});

test('pets.js card: Progress tab, GET + bridge POST, no HTML sinks', () => {
  const src = read('dashboard/pets.js');
  assert.match(src, /window\.EWPets\s*=/);
  assert.match(src, /'Pets'/);
  assert.match(src, /getJSON\('\/api\/pets'\)/);
  assert.match(src, /\.post\('\/api\/pets'/);
  for (const f of ['petRows', 'petCoverageLines', 'petExchangeLines', 'parsePetForm', 'petSpeciesOptions']) {
    assert.ok(src.indexOf('C.' + f + '(') >= 0, f);
  }
  assert.doesNotMatch(src, /innerHTML|outerHTML|insertAdjacentHTML|document\.write|method:\s*'POST'/);
  const dash = read('dashboard/dashboard.js');
  assert.match(dash, /EWPets\.mount\(/);
  assert.match(dash, /EWPets\.show\(/);
  const html = read('dashboard/index.html');
  const iP = html.indexOf('<script src="pets.js"></script>');
  assert.ok(iP > html.indexOf('ewcore.js') && iP < html.indexOf('<script src="dashboard.js"></script>'));
  assert.match(read('preload.js'), /\/api\/pets/);
});
