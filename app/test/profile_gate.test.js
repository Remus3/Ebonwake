'use strict';
// Plan 061: profile source off - settings base URL check and the off pill.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

test('profile.base_url is an allowlisted Profile setting', () => {
  const g = C.SETTINGS_GROUPS.find((x) => x.id === 'profile');
  assert.deepStrictEqual(g.fields.map((f) => f.key), ['profile.family', 'profile.base_url']);
});

test('validProfileBase mirrors progress.base_ok', () => {
  for (const v of ['https://mirror.example.com/v1', 'http://127.0.0.1:8001/v1',
    'http://localhost:8001/v1', 'http://[::1]:8001/v1', 'https://127.0.0.1/v1']) {
    assert.strictEqual(C.validProfileBase(v), true, v);
  }
  for (const v of ['', 'http://mirror.example.com/v1', 'ftp://127.0.0.1/v1', 'https://',
    'https://u:p@mirror.example.com', 'https://mirror.example.com/v1?x=1',
    'http://127.0.0.1:8001/v1\n', 'https://x' + 'a'.repeat(300), null, 7]) {
    assert.strictEqual(C.validProfileBase(v), false, String(v));
  }
});

test('parseSettingInput: blank turns the profile source off; bad bases name the fix', () => {
  assert.deepStrictEqual(C.parseSettingInput('profile.base_url', ''), { value: '' });
  assert.deepStrictEqual(C.parseSettingInput('profile.base_url', ' http://127.0.0.1:8001/v1 '),
    { value: 'http://127.0.0.1:8001/v1' });
  assert.match(C.parseSettingInput('profile.base_url', 'http://mirror.example.com').error, /https/);
  assert.strictEqual(C.validSettingsBody({ set: { 'profile.base_url': 'http://mirror.example.com' } }), false);
});

test('profilePill: state off is muted with its reason', () => {
  const p = C.profilePill({ data: null, freshness: null, status: 'off', state: 'off', reason: 'robots' });
  assert.strictEqual(p.off, true);
  assert.strictEqual(p.reason, 'robots');
  assert.strictEqual(p.cls, 'unknown');
  assert.strictEqual(p.none, false);
  assert.strictEqual(C.profilePill({ data: null, freshness: null, status: 'none' }).off, undefined);
});

test('Profile and Life cards show the one off line', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'dashboard', 'progress.js'), 'utf8');
  assert.strictEqual(src.split('C.PROFILE_OFF_TEXT').length - 1, 2);
  assert.strictEqual(C.PROFILE_OFF_TEXT, 'Profile source off - set a self-hosted base in Settings');
});
