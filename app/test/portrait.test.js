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

// ---- Plan 083: Deadeye portrait gallery ----

const P1 = '12345678901234567-1791230400';
const P0 = '12345678901234567-1791140400';
const S1 = 's0123456789abcdef';
const U1 = 's89abcdef01234567';
const GVIEW = {
  cls: 'Deadeye',
  classes: { Deadeye: { current: { id: P1, kind: 'portrait', at: '2026-10-06T19:48:00+00:00', char_no: '12345678901234567', from: 'auto' } } },
  history: [{ id: P1, kind: 'portrait', at: '2026-10-06T19:48:00+00:00' }, { id: P0, kind: 'portrait', at: '2026-10-05T18:48:00+00:00' }],
  shots: [{ id: S1, kind: 'shot', at: '2026-10-06T20:05:00+00:00' }],
  unknown_items: [{ id: U1, kind: 'shot', at: '2026-10-04T10:00:00+00:00' }],
  unknown: 0
};

test('gallery model: history then shots, aria-pressed on the current one, alt text', () => {
  const m = C.portraitGallery(GVIEW, 'Deadeye', UTC);
  assert.deepStrictEqual(m.items.map((i) => i.id), [P1, P0, S1]);
  assert.deepStrictEqual(m.items.map((i) => i.pressed), [true, false, false]);
  assert.deepStrictEqual(m.items.map((i) => i.tabindex), [0, -1, -1]);
  assert.strictEqual(m.items[0].alt, 'Deadeye portrait, 2026-10-06 19:48');
  assert.strictEqual(m.items[2].alt, 'Deadeye screenshot, 2026-10-06 20:05');
  assert.strictEqual(m.items[0].src, C.SERVER + '/api/portraits/img/' + P1 + '?size=s');
  assert.strictEqual(m.items[2].src, C.SERVER + '/api/portraits/img/' + S1); // original, no thumb
  assert.strictEqual(m.current.src, C.SERVER + '/api/portraits/img/' + P1 + '?size=m');
  assert.strictEqual(m.current.source, 'auto - newest portrait, 2026-10-06 19:48');
  assert.strictEqual(m.current.pinned, false);
  assert.strictEqual(m.current.badge, null);
  assert.strictEqual(m.empty, false);
  assert.deepStrictEqual(m.unknown.map((i) => i.id), [U1]);
  assert.strictEqual(m.unknown[0].alt, 'Unknown character screenshot, 2026-10-04 10:00');
});

test('gallery override: badge text, pinned date, Use newest key, pressed follows the pick', () => {
  const v = JSON.parse(JSON.stringify(GVIEW));
  v.classes.Deadeye.current = { id: S1, kind: 'shot', at: '2026-10-06T20:05:00+00:00', from: 'override',
    set_at: '2026-10-06T21:00:00+00:00',
    entry: { key: 'portrait.Deadeye', label: 'Portrait pick: Deadeye', value: S1, source: 'typed',
      set_at: '2026-10-06T21:00:00+00:00', expires_in_s: null, reason: 'picked in the gallery' } };
  const m = C.portraitGallery(v, 'Deadeye', UTC);
  assert.strictEqual(m.current.pinned, true);
  assert.strictEqual(m.current.badge.text, 'override');
  assert.strictEqual(m.current.badge.cls, 'ew-ovr');
  assert.match(m.current.badge.title, /^Portrait pick: Deadeye = /);
  assert.strictEqual(m.current.source, 'pinned 2026-10-06');
  assert.strictEqual(m.current.key, 'portrait.Deadeye');
  assert.deepStrictEqual(m.items.map((i) => i.pressed), [false, false, true]);
  assert.deepStrictEqual(m.items.map((i) => i.tabindex), [-1, -1, 0]);
  const chip = C.portraitChip(v, 'Deadeye', UTC);
  assert.strictEqual(chip.pinned, true);
  assert.strictEqual(chip.src, C.SERVER + '/api/portraits/img/' + S1);
  assert.match(chip.title, /pinned 2026-10-06 \(override\)$/);
  assert.strictEqual(C.portraitChip(GVIEW, 'Deadeye', UTC).pinned, false);
});

test('gallery empty: one muted line, no placeholder art, no other class', () => {
  const m = C.portraitGallery({ cls: 'Deadeye', classes: { Deadeye: { current: null } }, history: [], shots: [] }, 'Deadeye');
  assert.strictEqual(m.empty, true);
  assert.strictEqual(m.emptyText, 'No Deadeye portrait yet');
  assert.strictEqual(m.current, null);
  assert.deepStrictEqual(m.items, []);
  const w = C.portraitGallery({ classes: { Wizard: { current: GVIEW.classes.Deadeye.current } } }, 'Deadeye');
  assert.strictEqual(w.empty, true);
  assert.strictEqual(C.portraitGallery(null, 'Deadeye').emptyText, 'No Deadeye portrait yet');
});

test('gallery drops malformed ids', () => {
  const v = { classes: { Deadeye: { current: { id: '../x', at: 'x' } } },
    history: [{ id: '../x' }, { id: 5 }], shots: [{ id: 's0123' }, { id: 'S0123456789ABCDEF' }], unknown_items: [{ id: '..' }] };
  const m = C.portraitGallery(v, 'Deadeye');
  assert.ok(m.empty && m.items.length === 0 && m.unknown.length === 0);
});

test('roving tabindex: arrow keys move, Home / End, others unhandled', () => {
  assert.strictEqual(C.galleryMove(0, 'ArrowRight', 3), 1);
  assert.strictEqual(C.galleryMove(2, 'ArrowRight', 3), 2);
  assert.strictEqual(C.galleryMove(1, 'ArrowLeft', 3), 0);
  assert.strictEqual(C.galleryMove(0, 'ArrowUp', 3), 0);
  assert.strictEqual(C.galleryMove(0, 'ArrowDown', 3), 1);
  assert.strictEqual(C.galleryMove(1, 'Home', 3), 0);
  assert.strictEqual(C.galleryMove(0, 'End', 3), 2);
  assert.strictEqual(C.galleryMove(0, 'Enter', 3), null);
  assert.strictEqual(C.galleryMove(0, 'ArrowRight', 0), null);
});

test('validPost: /api/portraits pick / clear / bind exact bodies only; settings clear accepts portrait keys', () => {
  assert.ok(C.POST_ROUTES.indexOf('/api/portraits') >= 0);
  const ok = [{ pick: { cls: 'Deadeye', id: P1 } }, { pick: { cls: 'Deadeye', id: S1 } },
    { clear: 'portrait.Deadeye' }, { bind: { id: U1, cls: 'Dark Knight' } }];
  for (const b of ok) assert.strictEqual(C.validPost('/api/portraits', b), true, JSON.stringify(b));
  const bad = [{}, { pick: { cls: 'Deadeye' } }, { pick: { cls: 'Deadeye', id: '../x' } },
    { pick: { cls: 'Deadeye', id: P1, x: 1 } }, { clear: 'market.vp' }, { clear: 'portrait.' },
    { pick: { cls: 'Deadeye', id: P1 }, clear: 'portrait.Deadeye' }, { bind: { id: U1, cls: '<b>' } }];
  for (const b of bad) assert.strictEqual(C.validPost('/api/portraits', b), false, JSON.stringify(b));
  assert.strictEqual(C.validPost('/api/settings', { clear: 'portrait.Deadeye' }), true);
  assert.match(read('preload.js'), /\/api\/portraits/);
});

test('Deadeye tab: Portraits is the first card; strip a11y; chip dot; css tokens only', () => {
  const js = read('dashboard/deadeye.js');
  const mount = js.slice(js.indexOf('function mount('));
  assert.ok(mount.indexOf('galleryCard()') < mount.indexOf('notesCard()'));
  assert.match(js, /card\('Portraits'/);
  assert.match(js, /C\.portraitGallery\(/);
  assert.match(js, /aria-pressed/);
  assert.match(js, /loading = 'lazy'/);
  assert.match(js, /C\.galleryMove\(/);
  assert.match(js, /'Use newest'/);
  assert.match(js, /\/api\/portraits\?cls=/);
  assert.match(js, /el\('details'/);
  assert.match(read('dashboard/dashboard.js'), /chip\.pinned[^\n]*ew-chip-dot/);
  const css = read('shared/ew.css');
  const block = css.slice(css.indexOf('/* plan 083'), css.indexOf('/* end plan 083'));
  assert.ok(block.length > 0);
  assert.doesNotMatch(block, /#[0-9a-fA-F]{3,8}\b|rgb\(/);
  assert.match(block, /width: 156px; height: 201px/);
  assert.match(block, /width: 48px; height: 62px/);
  assert.match(block, /object-fit: cover/);
});
