'use strict';
// Plan 051: first-run "Get started" card. onboardingRows / nowOnboarding
// (ewcore.js) over GET /api/onboarding, the composeNow placement, the
// /api/onboarding bridge guard and static wiring guards on home.js.
// No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const APP = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(APP, p), 'utf8');
const T0 = Date.parse('2026-10-05T12:00:00Z');

function ob(over) {
  return Object.assign({
    steps: [
      { id: 'family', title: 'Set your family name', hint: 'Settings > profile.family',
        link: { tab: 'settings', field: 'profile.family' }, done: true },
      { id: 'log', title: 'Point EW at the BDO install folder', hint: 'bdo.install_dir',
        link: { tab: 'system' }, done: false },
      { id: 'watch', title: 'Watch 3 market items', hint: 'Market', link: { tab: 'market' }, done: false }
    ],
    done: 1, total: 3, complete: false, dismissed: false, dismissed_at: null, show: true
  }, over || {});
}

test('onboardingRows: open steps only, in server order, with their links', () => {
  const rows = C.onboardingRows(ob());
  assert.deepStrictEqual(rows.map((r) => r.step), ['log', 'watch']);
  assert.deepStrictEqual(rows[0].go, { tab: 'system' });
  assert.strictEqual(rows[0].label, 'Point EW at the BDO install folder');
  assert.strictEqual(rows[0].value, 'to do');
  assert.strictEqual(rows[0].note, 'Game folders > BDO install folder'); // plan 078: C.labelHint
  assert.strictEqual(rows[0].tick, null);
});

test('onboardingRows: Settings field link kept, junk dropped', () => {
  const steps = [
    { id: 'overlay', title: 'Choose an overlay corner', link: { tab: 'settings', field: 'overlay.anchor' }, done: false },
    { id: 'x', title: 'Bad tab', link: { tab: 'a"]b' }, done: false },
    { id: 'y', title: 'Bad field', link: { tab: 'settings', field: 'x"]' }, done: false },
    { id: 'Bad id', title: 'T', done: false },
    { id: 'z', title: '', done: false },
    { id: 'w', title: 'Done-ish', done: 'no' },
    null, 7
  ];
  const rows = C.onboardingRows({ steps: steps });
  assert.deepStrictEqual(rows.map((r) => r.step), ['overlay', 'x', 'y']);
  assert.deepStrictEqual(rows[0].go, { tab: 'settings', field: 'overlay.anchor' });
  assert.strictEqual(rows[1].go, null);
  assert.deepStrictEqual(rows[2].go, { tab: 'settings' });
  assert.deepStrictEqual(C.onboardingRows(null), []);
  assert.deepStrictEqual(C.onboardingRows({ steps: 'x' }), []);
});

test('nowOnboarding: shown only while the server says show', () => {
  const c = C.nowOnboarding(ob());
  assert.strictEqual(c.id, 'onboarding');
  assert.strictEqual(c.title, 'Get started');
  assert.strictEqual(c.meta, '1/3 done');
  assert.strictEqual(c.dismiss, true);
  assert.strictEqual(c.rows.length, 2);
  assert.strictEqual(C.nowOnboarding(ob({ show: false, dismissed: true })), null);
  assert.strictEqual(C.nowOnboarding(ob({ show: false, complete: true })), null);
  assert.strictEqual(C.nowOnboarding(ob({ steps: [] })), null);
  assert.strictEqual(C.nowOnboarding(null), null);
});

test('composeNow: the first-run card leads Home, absent when hidden or 404', () => {
  const ids = (snap) => C.composeNow(snap, T0).cards.map((c) => c.id);
  assert.strictEqual(ids({ at: {}, onboarding: ob() })[0], 'onboarding');
  assert.ok(ids({ at: {}, onboarding: ob({ show: false }) }).indexOf('onboarding') < 0);
  assert.ok(ids({ at: {}, onboarding: null }).indexOf('onboarding') < 0);
  assert.ok(ids({ at: {} }).indexOf('onboarding') < 0);
});

test('validPost: /api/onboarding exact bodies only', () => {
  assert.ok(C.POST_ROUTES.indexOf('/api/onboarding') >= 0);
  assert.strictEqual(C.validPost('/api/onboarding', { dismiss: true }), true);
  assert.strictEqual(C.validPost('/api/onboarding', { restore: true }), true);
  const bad = [{}, { dismiss: false }, { dismiss: 1 }, { restore: 'yes' }, { dismiss: true, restore: true },
    { nope: true }, null, [], 'dismiss'];
  for (const b of bad) assert.strictEqual(C.validPost('/api/onboarding', b), false, JSON.stringify(b));
  assert.strictEqual(C.postToast('/api/onboarding', { ok: true }).text, 'Get started saved');
});

test('wiring: home.js reads /api/onboarding and posts dismiss through the bridge only', () => {
  const src = read('dashboard/home.js');
  assert.match(src, /onboarding: '\/api\/onboarding'/);
  assert.ok(src.indexOf("post('/api/onboarding', { dismiss: true })") >= 0);
  assert.ok(src.indexOf('innerHTML') < 0);
  assert.ok(read('preload.js').indexOf('/api/onboarding') >= 0);
  assert.ok(read('overlay/overlay.js').indexOf('/api/onboarding') < 0);
});
