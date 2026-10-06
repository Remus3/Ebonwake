'use strict';
// Plan 042: Life & CP card formatter (ewcore.js lifeskillView / trends) and the
// Progress tab card (progress.js). No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const read = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');

const PUBLIC = {
  status: 'ok', character: 'Shooty', at: '2026-10-02T00:00:00+00:00', energy: 401,
  skills: [{ key: 'gathering', name: 'Gathering', rank: 'Artisan 2' }, { key: 'fishing', name: 'Fishing', rank: 'Skilled 9' }],
  cp: { value: 312, next: { cp: 350, label: 'Top of the band', verified: false }, gap: 38, reached: [] },
  source: 'https://example.invalid/', verified: '2026-07-01', error: null
};

test('lifeskillView: public card rows, unverified milestone flagged, skills', () => {
  const v = C.lifeskillView(PUBLIC);
  assert.strictEqual(v.state, 'ok');
  assert.strictEqual(v.character, 'Shooty');
  assert.deepStrictEqual(v.rows, [['Energy', '401'], ['CP', '312'], ['Next CP', '350 (+38) Top of the band (verify)']]);
  assert.deepStrictEqual(v.skills, [['Gathering', 'Artisan 2'], ['Fishing', 'Skilled 9']]);
  assert.strictEqual(v.skillsText, '');
  const ok = C.lifeskillView(Object.assign({}, PUBLIC, { cp: { value: 200, next: { cp: 220, label: 'Weekly', verified: '2026-07-01' }, gap: 20 } }));
  assert.deepStrictEqual(ok.rows[2], ['Next CP', '220 (+20) Weekly']);
});

test('lifeskillView: privacy-hidden fields read hidden, never zero', () => {
  const v = C.lifeskillView({ status: 'ok', character: 'Shooty', energy: 'hidden', skills: 'hidden', cp: 'hidden' });
  assert.deepStrictEqual(v.rows, [['Energy', 'hidden (privacy)'], ['CP', 'hidden (privacy)']]);
  assert.deepStrictEqual(v.skills, []);
  assert.strictEqual(v.skillsText, 'hidden (privacy)');
});

test('lifeskillView: top milestone reached, junk, none', () => {
  const top = C.lifeskillView(Object.assign({}, PUBLIC, { cp: { value: 500, next: null, gap: null, reached: [] } }));
  assert.deepStrictEqual(top.rows[2], ['Next CP', 'all milestones reached']);
  const junk = C.lifeskillView({ status: 'ok', energy: 'x', cp: { value: 'y' }, skills: [null, { name: 'A' }, { name: 'B', rank: 'C' }] });
  assert.deepStrictEqual(junk.rows, [['Energy', '-'], ['CP', '-']]);
  assert.deepStrictEqual(junk.skills, [['B', 'C']]);
  assert.strictEqual(C.lifeskillView({ status: 'ok', skills: [] }).skillsText, '-');
  for (const none of [null, undefined, 'x', { status: 'none' }]) {
    const n = C.lifeskillView(none);
    assert.strictEqual(n.state, 'none');
    assert.deepStrictEqual(n.rows, []);
  }
});

test('trends split: energy / CP on Life & CP, level / GS on Profile', () => {
  const body = { series: { energy: { points: [{ at: '2026-10-01T00:00:00+00:00', v: 300 }] } } };
  assert.deepStrictEqual(C.lifeskillTrends(body).map(function (r) { return r.field; }), ['energy', 'contribution']);
  assert.deepStrictEqual(C.profileTrends(body).map(function (r) { return r.field; }), ['level', 'gs']);
  assert.strictEqual(C.lifeskillTrends(body)[0].text, '300');
});

test('progress.js: Life & CP card from lifeskill with trends, safe DOM', () => {
  const src = read('dashboard/progress.js');
  assert.match(src, /'Life & CP'/);
  assert.match(src, /C\.lifeskillView\(S\.data\.lifeskill\)/);
  assert.match(src, /C\.lifeskillTrends\(S\.hist\)/);
  assert.match(src, /C\.profileTrends\(S\.hist\)/);
  assert.doesNotMatch(src, /innerHTML|outerHTML|insertAdjacentHTML|document\.write/);
});
