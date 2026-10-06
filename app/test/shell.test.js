'use strict';
// Static guards for the Electron shell (no Electron needed). The desktop
// self-test (EW_SELFTEST=<out.json>, app/selftest.js) checks the same live.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const APP = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(APP, p), 'utf8');

test('overlay html root is transparent (html bg would paint opaque)', () => {
  assert.match(read('overlay/index.html'), /<html[^>]*class="ew-overlay"/);
  assert.match(read('shared/ew.css'), /html\.ew-overlay,\s*html\.ew-overlay body\s*\{\s*background:\s*transparent/);
});

test('overlay window is transparent, click-through, focus-less, globalShortcut only', () => {
  const m = read('main.js');
  assert.match(m, /transparent:\s*true/);
  assert.match(m, /focusable:\s*false/);
  assert.match(m, /setIgnoreMouseEvents\(true/);
  assert.match(m, /globalShortcut\.register\(keys\.toggleOverlay/);
  assert.doesNotMatch(m, /uiohook|iohook|robotjs|nut-js|SetWindowsHookEx|sendInputEvent/);
});

// ---- Plan 020 ----

test('renderer and main scripts compile (no Electron needed)', () => {
  const vm = require('vm');
  const files = ['main.js', 'preload.js'].concat(
    fs.readdirSync(path.join(APP, 'dashboard')).filter((f) => f.endsWith('.js')).map((f) => 'dashboard/' + f));
  for (const f of files) assert.doesNotThrow(() => new vm.Script(read(f), { filename: f }), f);
});

test('every dashboard module answers a 404 with C.notOnServer, no ad-hoc copy', () => {
  const dir = path.join(APP, 'dashboard');
  const mods = fs.readdirSync(dir).filter((f) => f.endsWith('.js'));
  assert.ok(mods.length >= 9);
  for (const f of mods) {
    const src = read('dashboard/' + f);
    assert.doesNotMatch(src, /not on this server/, f + ' carries ad-hoc 404 copy');
    const checks = src.match(/status === 404[^\n]*/g) || [];
    for (const line of checks) assert.match(line, /C\.notOnServer\(/, f + ': ' + line);
    // Every module-level getJSON that throws on !r.ok maps 404 first.
    if (/function getJSON[\s\S]*?if \(!r\.ok\)/.test(src) && f !== 'dashboard.js') {
      assert.ok(checks.length >= 1, f + ' getJSON has no 404 branch');
    }
  }
});

test('dashboard re-checks /api/version on a timer and on SSE reconnect', () => {
  const d = read('dashboard/dashboard.js');
  assert.match(d, /getJSON\('\/api\/version'\)/);
  assert.match(d, /setInterval\(pollVersion, C\.VERSION_POLL_MS\)/);
  assert.match(d, /onopen[\s\S]*?if \(reconnect\) \{\s*pollVersion\(\);/);
  assert.match(d, /C\.healthPill\(/);
  assert.doesNotMatch(d, /JSON\.stringify\(/, 'System tab renders cards, not a JSON blob');
  assert.match(d, /C\.sourceFreshness\(/);
  assert.match(d, /C\.serverRows\(/);
});

test('appCommit is read-only; restart is one allowlisted, sender-checked IPC', () => {
  const pre = read('preload.js');
  const m = read('main.js');
  assert.match(pre, /appCommit: function \(\) \{ return APP_COMMIT; \}/);
  assert.match(pre, /ipcRenderer\.invoke\('ew:restart-server'\)/);
  // plan 030 adds the two payload-free settings reloads (settings.test.js).
  // plan 057 adds ew:open-external (allowlisted https links).
  assert.strictEqual((pre.match(/ipcRenderer\.\w+\(/g) || []).length, 6,
    'post + restart + notify + 2 reloads + open-external only');
  assert.match(m, /rev-parse', '--short', 'HEAD'/);
  assert.match(m, /windowsHide: true/);
  assert.match(m, /additionalArguments: \['--ew-app-commit=' \+ APP_COMMIT[,\]]/);
  const h = m.slice(m.indexOf("ipcMain.handle('ew:restart-server'"));
  assert.match(h, /event\.sender !== dashboard\.webContents/);
  assert.match(h, /await restartServer\(\)/);
  assert.match(read('dashboard/index.html'), /id="server-restart"[^>]*hidden/);
});

// ---- Plan 026 ----

test('ew:notify is the one new allowlisted channel: sender-checked, validated, rate-limited', () => {
  const pre = read('preload.js');
  const m = read('main.js');
  const channels = (pre.match(/ipcRenderer\.\w+\('([^']+)'/g) || []).map((s) => s.replace(/^.*'([^']+)'$/, '$1')).sort();
  // plan 030 adds the two payload-free settings reloads (settings.test.js).
  assert.deepStrictEqual(channels, ['ew:notify', 'ew:open-external', 'ew:post', 'ew:reload-overlay',
    'ew:reload-shell', 'ew:restart-server']);
  assert.match(pre, /notify: function \(n\) \{ return ipcRenderer\.invoke\('ew:notify', n\); \}/);
  assert.match(pre, /notifyPrefs: function \(\) \{ return NOTIFY_ARG; \}/);
  const mainChannels = (m.match(/ipcMain\.(handle|on)\('([^']+)'/g) || []).map((s) => s.replace(/^.*'([^']+)'$/, '$1')).sort();
  assert.deepStrictEqual(mainChannels, ['ew:notify', 'ew:open-external', 'ew:overlay-size', 'ew:post',
    'ew:reload-overlay', 'ew:reload-shell', 'ew:restart-server']);
  const h = m.slice(m.indexOf("ipcMain.handle('ew:notify'"), m.indexOf("ipcMain.handle('ew:notify'") + 900);
  assert.match(h, /event\.sender !== dashboard\.webContents/);
  assert.match(h, /core\.validNotify\(n\)/);
  assert.match(h, /notifyLimit\.allow\(Date\.now\(\)\)/);
  assert.match(h, /new Notification\(\{ title: n\.title, body: n\.body, silent: NOTIFY_SILENT \}\)/);
  assert.match(h, /on\('click', showDashboard\)/);
  assert.doesNotMatch(h, /actions:/, 'no notification actions');
  assert.match(m, /core\.rateLimiter\(core\.NOTIFY_RATE\.max, core\.NOTIFY_RATE\.windowMs\)/);
  assert.match(m, /'--ew-notify=' \+ core\.notifyArg\(core\.notifyPrefs\(cfg\)\)/);
  // The overlay preload stays one-way: no notify there.
  assert.doesNotMatch(read('overlay/preload.js'), /ew:notify/);
});

test('dashboard: toast region, every POST through EWToast.via(...).post, toast.js loads before modules', () => {
  const html = read('dashboard/index.html');
  assert.match(html, /<div class="ew-toasts" id="toasts" role="status" aria-live="polite"><\/div>/);
  assert.ok(html.indexOf('src="toast.js"') > html.indexOf('ewcore.js'));
  assert.ok(html.indexOf('src="toast.js"') < html.indexOf('src="market.js"'));
  const dir = path.join(APP, 'dashboard');
  for (const f of fs.readdirSync(dir).filter((x) => x.endsWith('.js') && x !== 'toast.js')) {
    const src = read('dashboard/' + f);
    const all = (src.match(/\.post\(/g) || []).length;
    const via = (src.match(/window\.EWToast\.via\(\w+\)\.post\('\/api\//g) || []).length;
    assert.strictEqual(via, all, f + ' posts around the toast');
  }
  const t = read('dashboard/toast.js');
  assert.match(t, /C\.postToast\(route, res\)/);
  assert.match(t, /C\.notifyRules\(/);
  assert.match(t, /ledger\.take\(/);
  assert.match(read('dashboard/today.js'), /S\.rowErr\[/);
});

// ---- Plan 057 ----

test('externalUrl: https on the fixed allowlist only', () => {
  const C = require('../shared/ewcore');
  const AT = String.fromCharCode(64);
  assert.deepStrictEqual(C.EXTERNAL_HOSTS, ['naeu.playblackdesert.com', 'www.naeu.playblackdesert.com',
    'www.blackdesertfoundry.com', 'api.arsha.io', 'github.com']);
  assert.ok(Object.isFrozen(C.EXTERNAL_HOSTS));
  const ok = [
    'https://www.naeu.playblackdesert.com/en-US/Wiki?wikiNo=83',
    'https://naeu.playblackdesert.com/en-US/News/Notice',
    'https://www.blackdesertfoundry.com/pets-guide/',
    'https://api.arsha.io/v2/na/GetWorldMarketHotList',
    'https://github.com/',
    'https://GITHUB.com/x#frag'
  ];
  for (const u of ok) assert.ok(C.externalUrl(u), u);
  assert.strictEqual(C.externalUrl('https://GITHUB.com/x#frag'), 'https://github.com/x#frag');
  const bad = [
    'http://www.blackdesertfoundry.com/pets-guide/',
    'javascript:alert(1)',
    'JavaScript://github.com/%0aalert(1)',
    'file:///tmp/x',
    'data:text/html,x',
    // credentials in the URL (built up so the leak sweep sees no address shape)
    'https://user:pw' + AT + 'github.com/',
    'https://user' + AT + 'github.com/',
    'https://:pw' + AT + 'github.com/',
    'https://evil.com/',
    'https://github.com.evil.com/',
    'https://evilgithub.com/',
    'https://bdocodex.com/us/item/16001/',
    'https://github.com:8443/',
    'https://127.0.0.1:8940/api/health',
    'https:github.com',
    '//github.com/',
    'github.com',
    ' https://github.com/',
    'https://github.com/a b',
    'https://github.com/\n',
    'https://github.com/' + 'a'.repeat(2048),
    '', null, undefined, 42, {}, ['https://github.com/']
  ];
  for (const u of bad) assert.strictEqual(C.externalUrl(u), null, String(u));
});

test('validOpenExternal: exactly {url} with an allowlisted url', () => {
  const C = require('../shared/ewcore');
  assert.ok(C.validOpenExternal({ url: 'https://github.com/' }));
  assert.ok(!C.validOpenExternal({ url: 'http://github.com/' }));
  assert.ok(!C.validOpenExternal({ url: 'https://github.com/', x: 1 }));
  assert.ok(!C.validOpenExternal('https://github.com/'));
  assert.ok(!C.validOpenExternal(null));
  assert.ok(!C.validOpenExternal([]));
});

test('ew:open-external: sender-checked, validated, rate-limited, shell.openExternal only', () => {
  const pre = read('preload.js');
  const m = read('main.js');
  assert.match(pre, /openExternal: function \(url\) \{ return ipcRenderer\.invoke\('ew:open-external', \{ url: url \}\); \}/);
  const at = m.indexOf("ipcMain.handle('ew:open-external'");
  assert.ok(at > 0);
  const h = m.slice(at, at + 700);
  assert.match(h, /event\.sender !== dashboard\.webContents/);
  assert.match(h, /core\.validOpenExternal\(body\)/);
  assert.match(h, /openLimit\.allow\(Date\.now\(\)\)/);
  assert.match(h, /shell\.openExternal\(core\.externalUrl\(body\.url\)\)/);
  assert.strictEqual((m.match(/shell\.openExternal\(/g) || []).length, 1, 'one openExternal site');
  // In-app navigation stays blocked.
  assert.match(m, /dashboard\.webContents\.on\('will-navigate', function \(e\) \{ e\.preventDefault\(\); \}\)/);
  assert.match(m, /dashboard\.webContents\.setWindowOpenHandler\(function \(\) \{ return \{ action: 'deny' \}; \}\)/);
  assert.doesNotMatch(read('overlay/preload.js'), /open-external/);
});

test('linkButton: shared open button in toast.js, only for allowlisted urls', () => {
  const t = read('dashboard/toast.js');
  assert.match(t, /function linkButton\(url\)/);
  assert.match(t, /if \(!C\.externalUrl\(url\)\) return null;/);
  assert.match(t, /\.openExternal\(url\)/);
  assert.match(t, /linkButton: linkButton/);
  const ev = read('dashboard/events.js');
  const src = ev.slice(ev.indexOf('function drawSources'));
  assert.match(src.slice(0, 1500), /window\.EWToast\.linkButton\(s\.url\)/);
  for (const f of ['progress.js', 'pets.js', 'inventory.js']) {
    assert.match(read('dashboard/' + f), /window\.EWToast\.linkButton\(/, f + ' source link');
  }
  for (const f of fs.readdirSync(path.join(APP, 'dashboard')).filter((x) => x.endsWith('.js'))) {
    const s = read('dashboard/' + f);
    assert.doesNotMatch(s, /window\.open\(|location\.href\s*=|\.href\s*=/, f + ' navigates');
    if (f !== 'toast.js') assert.doesNotMatch(s, /ewApi\.openExternal/, f + ' bypasses linkButton');
  }
});

test('main: an unload the dashboard blocks (unsaved note) asks before leaving', () => {
  const m = read('main.js');
  const at = m.indexOf("dashboard.webContents.on('will-prevent-unload'");
  assert.ok(at > 0);
  const h = m.slice(at, at + 700);
  assert.match(h, /dialog\.showMessageBoxSync\(/);
  assert.match(h, /e\.preventDefault\(\)/);
});
