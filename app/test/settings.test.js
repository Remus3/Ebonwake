'use strict';
// Plan 030: Settings tab form model (pure helpers in ewcore) + shell wiring.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const APP = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(APP, p), 'utf8');

test('groups in plan order, every key dotted, no secrets or loop keys', () => {
  assert.deepStrictEqual(C.SETTINGS_GROUPS.map((g) => g.title),
    ['Overlay', 'Hotkeys', 'Profile', 'Appearance', 'Notifications', 'Events', 'Market', 'Play session',
      'Game folders']);
  for (const k of C.SETTINGS_KEYS) {
    assert.match(k, /^[a-z]+(\.[A-Za-z_]+)+$/, k);
    assert.doesNotMatch(k, /^(secrets|loop)\b/, k);
  }
  assert.strictEqual(new Set(C.SETTINGS_KEYS).size, C.SETTINGS_KEYS.length);
});

test('client allowlist matches the server allowlist key for key', () => {
  const py = fs.readFileSync(path.join(APP, '..', 'server', 'ew', 'settings.py'), 'utf8');
  const keys = new Set();
  for (const m of py.matchAll(/^\s+"([a-z]+\.[A-Za-z_.]+)": \(/gm)) keys.add(m[1]);
  const w = /WIDGETS = \{([^}]*)\}/.exec(py)[1].match(/"(\w+)"/g).map((s) => 'overlay.widgets.' + s.slice(1, -1));
  const n = /NOTIFY = \{([^}]*)\}/.exec(py)[1].match(/"(\w+)"/g).map((s) => 'notify.' + s.slice(1, -1));
  w.concat(n).forEach((k) => keys.add(k));
  assert.deepStrictEqual([...keys].sort(), C.SETTINGS_KEYS.slice().sort());
});

test('validSettingsBody: allowlisted keys with valid values only', () => {
  assert.strictEqual(C.validSettingsBody({ set: { 'ui.theme': 'light', 'overlay.scale': 1.2 } }), true);
  assert.strictEqual(C.validSettingsBody({ set: { 'overlay.anchor': { x: 4, y: 5 } } }), true);
  assert.strictEqual(C.validSettingsBody({ set: { 'overlay.display': null } }), true);
  const bad = [null, {}, { set: {} }, { set: [] }, { set: { 'ui.theme': 'pink' } },
    { set: { 'secrets.ocr': { env: 'K' } } }, { set: { 'loop.max_notes_per_day': 9 } },
    { set: { 'overlay.anchor': { env: 'K' } } }, { set: { 'hotkeys.toggleOverlay': 'E' } },
    { set: { 'ui.scale': 1.5 } }, { set: { 'profile.family': 'a b' } }, { set: { 'notify.x': true } },
    { set: { 'ui.theme': 'dark' }, x: 1 }, { set: { 'overlay.anchor': { x: 100001, y: 0 } } },
    { set: { 'hotkeys.toggleOverlay': 'Control+'.repeat(10) + 'E' } },
    { set: { 'hotkeys.toggleOverlay': 'Control+Alt+F\n' } }, { set: { 'profile.family': 'abc\n' } }];
  for (const b of bad) assert.strictEqual(C.validSettingsBody(b), false, JSON.stringify(b));
  assert.strictEqual(C.validPost('/api/settings', { set: { 'coupons.check': false } }), true);
  assert.ok(C.POST_ROUTES.indexOf('/api/settings') >= 0);
});

test('parseSettingInput turns form text into typed values or a hint', () => {
  assert.deepStrictEqual(C.parseSettingInput('overlay.scale', ' 1.25 '), { value: 1.25 });
  assert.deepStrictEqual(C.parseSettingInput('overlay.display', ''), { value: null });
  assert.deepStrictEqual(C.parseSettingInput('overlay.display', '1'), { value: 1 });
  assert.deepStrictEqual(C.parseSettingInput('overlay.anchor', 'mr'), { value: 'mr' });
  assert.deepStrictEqual(C.parseSettingInput('overlay.anchor', '40, -3'), { value: { x: 40, y: -3 } });
  assert.deepStrictEqual(C.parseSettingInput('notify.gameExit', true), { value: true });
  assert.deepStrictEqual(C.parseSettingInput('profile.family', ''), { value: '' });
  assert.deepStrictEqual(C.parseSettingInput('hotkeys.showDashboard', 'Control+Shift+F2'),
    { value: 'Control+Shift+F2' });
  for (const [k, v] of [['overlay.scale', '2'], ['overlay.scale', 'abc'], ['overlay.display', '-1'],
    ['overlay.anchor', 'zz'], ['hotkeys.toggleOverlay', 'Q'], ['profile.family', 'x'],
    ['ui.theme', 'neon'], ['market.fame_pct', '1.6'], ['nope', '1']]) {
    const r = C.parseSettingInput(k, v);
    assert.ok(typeof r.error === 'string' && r.error && !('value' in r), k + '=' + v);
  }
  assert.match(C.parseSettingInput('hotkeys.toggleOverlay', 'Q').error, /modifier/);
});

test('plan 059: maintenance start is blank or HH:MM UTC', () => {
  assert.deepStrictEqual(C.parseSettingInput('events.maintenance_start_utc', ' 08:30 '), { value: '08:30' });
  assert.deepStrictEqual(C.parseSettingInput('events.maintenance_start_utc', ''), { value: '' });
  for (const v of ['8:30', '24:00', '07:60', 'noon']) {
    assert.match(C.parseSettingInput('events.maintenance_start_utc', v).error, /HH:MM/, v);
  }
  assert.strictEqual(C.validSettingsBody({ set: { 'events.notice_check': false } }), true);
  assert.strictEqual(C.validSettingsBody({ set: { 'events.maintenance_start_utc': 7 } }), false);
});

test('settingInputText round-trips through parseSettingInput', () => {
  const vals = { 'overlay.anchor': { x: 10, y: 20 }, 'overlay.display': null, 'overlay.scale': 1.1,
    'profile.family': 'Abc', 'ui.theme': 'system' };
  for (const k of Object.keys(vals)) {
    assert.deepStrictEqual(C.parseSettingInput(k, C.settingInputText(k, vals[k])), { value: vals[k] }, k);
  }
});

test('settingsBody keeps only changed allowlisted keys', () => {
  const saved = { 'ui.theme': 'dark', 'overlay.anchor': { x: 1, y: 2 }, 'overlay.scale': 1 };
  assert.strictEqual(C.settingsBody(saved, { 'ui.theme': 'dark', 'overlay.anchor': { x: 1, y: 2 } }), null);
  assert.deepStrictEqual(C.settingsBody(saved, { 'ui.theme': 'light', 'overlay.scale': 1, bogus: 1 }),
    { set: { 'ui.theme': 'light' } });
  assert.deepStrictEqual(C.settingsBody(saved, { 'overlay.anchor': 'tl' }), { set: { 'overlay.anchor': 'tl' } });
});

test('settingsEffects maps changed keys to app actions', () => {
  assert.deepStrictEqual(C.settingsEffects(['overlay.widgets.season']), { overlay: true, shell: false, theme: false });
  assert.deepStrictEqual(C.settingsEffects(['hotkeys.toggleOverlay']), { overlay: false, shell: true, theme: false });
  assert.deepStrictEqual(C.settingsEffects(['ui.scale', 'ui.theme']), { overlay: false, shell: true, theme: true });
  assert.deepStrictEqual(C.settingsEffects(null), { overlay: false, shell: false, theme: false });
});

test('themeAttr and uiScale', () => {
  assert.strictEqual(C.themeAttr('light', true), 'light');
  assert.strictEqual(C.themeAttr('dark', false), 'dark');
  assert.strictEqual(C.themeAttr('system', true), 'dark');
  assert.strictEqual(C.themeAttr('system', false), 'light');
  assert.strictEqual(C.themeAttr(undefined, false), 'dark');
  assert.strictEqual(C.uiScale({ ui: { scale: 1.2 } }), 1.2);
  for (const c of [{}, null, { ui: { scale: 2 } }, { ui: { scale: '1.1' } }, { ui: 5 }]) assert.strictEqual(C.uiScale(c), 1);
});

test('shell: settings IPC is payload-free, sender-checked, globalShortcut only', () => {
  const pre = read('preload.js');
  const m = read('main.js');
  assert.match(pre, /ipcRenderer\.invoke\('ew:reload-overlay'\)/);
  assert.match(pre, /ipcRenderer\.invoke\('ew:reload-shell'\)/);
  for (const ch of ['ew:reload-overlay', 'ew:reload-shell']) {
    const h = m.slice(m.indexOf("ipcMain.handle('" + ch + "'"));
    assert.match(h.slice(0, 400), /function \(event\) \{\s*if \(!dashboard \|\| event\.sender !== dashboard\.webContents\)/, ch);
  }
  assert.match(m, /setZoomFactor\(core\.uiScale\(/);
  assert.match(m, /globalShortcut\.register\(/);
  assert.doesNotMatch(m, /uiohook|iohook|robotjs|nut-js|SetWindowsHookEx|sendInputEvent/);
});

test('dashboard: settings tab mounted, script loaded, min text 12px', () => {
  assert.match(read('dashboard/index.html'), /<script src="settings\.js"><\/script>/);
  assert.match(read('dashboard/dashboard.js'), /t\.id === 'settings' && window\.EWSettings/);
  const css = read('shared/ew.css');
  for (const m of css.matchAll(/font-size:\s*(\d+)px/g)) assert.ok(Number(m[1]) >= 12, m[0]);
});
