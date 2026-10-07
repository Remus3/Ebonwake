'use strict';
// Plan 065: "Game folders" settings - blank = auto-detected, a path = "use other".
// Pure ewcore helpers + static wiring guards on settings.js. No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const read = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');
const DET = '/srv/steam/steamapps/common/Black Desert Online';

test('bdo folder keys are allowlisted as dir fields', () => {
  assert.ok(C.SETTINGS_KEYS.indexOf('bdo.install_dir') >= 0);
  assert.ok(C.SETTINGS_KEYS.indexOf('bdo.documents_dir') >= 0);
  assert.strictEqual(C.validSettingsBody({ set: { 'bdo.install_dir': '' } }), true);
  assert.strictEqual(C.validSettingsBody({ set: { 'bdo.install_dir': DET } }), true);
  assert.strictEqual(C.validSettingsBody({ set: { 'bdo.documents_dir': '\\\\nas\\share\\Black Desert' } }), true);
  for (const bad of ['relative/dir', ' /lead', 'x\n', 5, null, { env: 'K' }, '/' + 'a'.repeat(1024)]) {
    assert.strictEqual(C.validSettingsBody({ set: { 'bdo.install_dir': bad } }), false, String(bad));
  }
});

test('parseSettingInput: blank means auto-detect, junk names the field', () => {
  assert.deepStrictEqual(C.parseSettingInput('bdo.install_dir', '  '), { value: '' });
  assert.deepStrictEqual(C.parseSettingInput('bdo.install_dir', DET), { value: DET });
  assert.match(C.parseSettingInput('bdo.install_dir', 'nope').error, /auto-detect/);
});

test('settingDetectedNote: detected, not detected, and "use other"', () => {
  const det = { 'bdo.install_dir': DET, 'bdo.documents_dir': null };
  assert.strictEqual(C.settingDetectedNote('bdo.install_dir', '', det), 'auto-detected: ' + DET);
  assert.strictEqual(C.settingDetectedNote('bdo.documents_dir', '', det), 'not detected - type the folder path');
  assert.strictEqual(C.settingDetectedNote('bdo.install_dir', '/other', det),
    'using this folder (auto-detected: ' + DET + ')');
  assert.strictEqual(C.settingDetectedNote('bdo.install_dir', DET, det), 'using this folder');
  assert.strictEqual(C.settingDetectedNote('bdo.install_dir', '', null), 'not detected - type the folder path');
  assert.strictEqual(C.settingDetectedNote('ui.theme', 'dark', det), '');
});

test('settings.js reads `detected` and renders the note on dir fields', () => {
  const src = read('dashboard/settings.js');
  assert.match(src, /doc\.detected/);
  assert.match(src, /settingDetectedNote\(f\.key/);
});
