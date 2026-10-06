'use strict';
// Plan 062: auto play-session. sessionPill (Grind tab session card pill) reads
// GET /api/grind session.auto; static guard on the grind.js wiring. No DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const ACTIVE = { spot: 'orc-camp', started: '2026-10-05T10:00:00+00:00' };

test('sessionPill: auto, running, idle, no data', () => {
  assert.deepStrictEqual(C.sessionPill({ active: Object.assign({ auto: true }, ACTIVE), session: { auto: true } }),
    { text: 'auto', cls: 'ok' });
  assert.deepStrictEqual(C.sessionPill({ active: Object.assign({ auto: false }, ACTIVE), session: { auto: false } }),
    { text: 'running', cls: 'ok' });
  assert.deepStrictEqual(C.sessionPill({ active: null, session: { auto: false } }), { text: 'idle', cls: 'unknown' });
  assert.deepStrictEqual(C.sessionPill(null), { text: '-', cls: 'unknown' });
});

test('sessionPill: an older server without session falls back to active.auto', () => {
  assert.strictEqual(C.sessionPill({ active: Object.assign({ auto: true }, ACTIVE) }).text, 'auto');
  assert.strictEqual(C.sessionPill({ active: ACTIVE }).text, 'running');
  assert.strictEqual(C.sessionPill({ active: ACTIVE, session: { auto: 'yes' } }).text, 'running');
});

test('play settings: auto_session bool, grace_s whole seconds 60-600', () => {
  assert.strictEqual(C.validSettingsBody({ set: { 'play.auto_session': false, 'play.grace_s': 300 } }), true);
  for (const v of [59, 601, 120.5, '120', true]) {
    assert.strictEqual(C.validSettingsBody({ set: { 'play.grace_s': v } }), false, String(v));
  }
  assert.strictEqual(C.validSettingsBody({ set: { 'play.auto_session': 1 } }), false);
});

test('grind.js draws the session pill through sessionPill', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'dashboard', 'grind.js'), 'utf8');
  assert.ok(src.includes('C.sessionPill(S.data)'));
});
