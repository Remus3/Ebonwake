'use strict';
// Plan 082: top-left class chip. Pure helper (ewcore.js) + static guards on
// the dashboard shell, CSP and CSS. No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const APP = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(APP, p), 'utf8');
const UTC = { zone: 'utc' };

const ID = '12345678901234567-1791230400';
const VIEW = {
  classes: {
    Deadeye: { current: { id: ID, at: '2026-10-06T19:48:00+00:00', char_no: '12345678901234567', from: 'auto' } },
    Wizard: { current: null }
  },
  unknown: 1, char_no: null, loaded_cls: null, progress_cls: 'Deadeye'
};

test('chip for a bound class carries the small thumbnail', () => {
  const c = C.portraitChip(VIEW, 'Deadeye', UTC);
  assert.strictEqual(c.cls, 'Deadeye');
  assert.strictEqual(c.empty, false);
  assert.strictEqual(c.src, C.SERVER + '/api/portraits/img/' + ID + '?size=s');
  assert.strictEqual(c.alt, 'Deadeye portrait');
  assert.match(c.title, /2026-10-06 19:48/);
});

test('empty chip for a class with no portrait: no src, aria label, hint title', () => {
  const c = C.portraitChip(VIEW, 'Wizard', UTC);
  assert.deepStrictEqual(c, { cls: 'Wizard', src: null, alt: 'Wizard: no portrait yet',
    title: 'Take your character portrait in game; EW picks it up automatically.', empty: true });
  const none = C.portraitChip({ classes: {}, progress_cls: 'Deadeye' }, null);
  assert.strictEqual(none.cls, 'Deadeye');
  assert.strictEqual(none.alt, 'Deadeye: no portrait yet');
  assert.strictEqual(none.src, null);
});

test('never another class image when the active class has none', () => {
  ['Wizard', 'Sorceress', 'Musa'].forEach(function (cls) {
    const c = C.portraitChip(VIEW, cls);
    assert.strictEqual(c.src, null);
    assert.strictEqual(c.cls, cls);
    assert.ok(c.empty);
  });
});

test('class falls back: active tab, then loaded character, then Progress class', () => {
  const v = Object.assign({}, VIEW, { loaded_cls: 'Wizard' });
  assert.strictEqual(C.portraitChip(v, 'Deadeye').cls, 'Deadeye');
  assert.strictEqual(C.portraitChip(v, null).cls, 'Wizard');
  assert.strictEqual(C.portraitChip(VIEW, undefined).cls, 'Deadeye');
  const blank = C.portraitChip(null, null);
  assert.strictEqual(blank.cls, null);
  assert.ok(blank.empty && blank.src === null);
});

test('malformed ids never become a src', () => {
  ['../x', '', 5, null, '1-2'].forEach(function (id) {
    const v = { classes: { Deadeye: { current: { id: id, at: 'x' } } } };
    assert.strictEqual(C.portraitChip(v, 'Deadeye').src, null);
  });
});

test('tab <-> class mapping', () => {
  assert.strictEqual(C.classOfTab('deadeye', VIEW), 'Deadeye');
  assert.strictEqual(C.classOfTab('market', VIEW), null);
  assert.strictEqual(C.classOfTab('darkknight', { classes: { 'Dark Knight': { current: null } } }), 'Dark Knight');
  assert.strictEqual(C.tabOfClass('Deadeye', ['home', 'deadeye']), 'deadeye');
  assert.strictEqual(C.tabOfClass('Wizard', ['home', 'deadeye']), null);
});

test('dashboard CSP allows server images; overlay CSP unchanged', () => {
  assert.match(read('dashboard/index.html'), /img-src 'self' http:\/\/127\.0\.0\.1:8940/);
  assert.doesNotMatch(read('overlay/index.html'), /img-src/);
});

test('shell: chip replaces the brand text, no toast or onboarding for a missing portrait', () => {
  const html = read('dashboard/index.html');
  assert.doesNotMatch(html, /class="ew-brand"/);
  // EBONWAKE stays only as the chip's placeholder until /api/portraits answers.
  assert.match(html, /<button class="ew-chip" id="class-chip"[^>]*>.*ew-chip-empty.*role="img"/);
  const js = read('dashboard/dashboard.js');
  assert.match(js, /C\.portraitChip\(/);
  assert.match(js, /DOMAINS\.push\('portraits'\)/);
  const chip = js.slice(js.indexOf('function paintChip'), js.indexOf('function paintChip') + 1600);
  assert.doesNotMatch(chip, /toast|onboarding/i);
});

test('css: chip classes use tokens only', () => {
  const css = read('shared/ew.css');
  ['.ew-chip', '.ew-chip-img', '.ew-chip-empty'].forEach(function (k) { assert.ok(css.indexOf(k) >= 0, k); });
  const block = css.slice(css.indexOf('/* plan 082'), css.indexOf('/* end plan 082'));
  assert.ok(block.length > 0);
  assert.doesNotMatch(block, /#[0-9a-fA-F]{3,8}\b|rgb\(/);
  assert.match(block, /var\(--fk-border/);
});
