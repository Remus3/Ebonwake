'use strict';
// Plan 008 slice B: pure game-state helpers (ewcore.js), the System tab game
// card (dashboard/game.js) and the overlay state dot. Static guards keep both
// GET-only and safe-DOM. No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const APP = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(APP, p), 'utf8');
const T0 = Date.parse('2026-10-05T12:00:00Z');

test('GAME_STATES match the slice A contract', () => {
  assert.deepStrictEqual(C.GAME_STATES, ['not_running', 'running', 'logged_in', 'disconnected', 'unconfigured']);
});

test('gameStateLabel: grey not running, amber running, green logged in, red disconnected', () => {
  assert.deepStrictEqual(C.gameStateLabel('not_running'), { cls: 'off', label: 'not running' });
  assert.deepStrictEqual(C.gameStateLabel('running'), { cls: 'warn', label: 'running' });
  assert.deepStrictEqual(C.gameStateLabel('logged_in'), { cls: 'ok', label: 'logged in' });
  assert.deepStrictEqual(C.gameStateLabel('disconnected'), { cls: 'bad', label: 'disconnected' });
  assert.deepStrictEqual(C.gameStateLabel('unconfigured'), { cls: 'unknown', label: 'unconfigured' });
  for (const x of [undefined, null, '', 'LOGGED_IN', 'bogus', 5, {}]) {
    assert.deepStrictEqual(C.gameStateLabel(x), { cls: 'unknown', label: 'unknown' }, String(x));
  }
  assert.deepStrictEqual(C.gameStateLabel('offline'), { cls: 'unknown', label: 'offline' });
});

test('gameTimeMs: epoch seconds, epoch millis or ISO string; junk -> null', () => {
  assert.strictEqual(C.gameTimeMs(T0 / 1000), T0);
  assert.strictEqual(C.gameTimeMs(T0 / 1000 + 0.5), T0 + 500);
  assert.strictEqual(C.gameTimeMs(T0), T0);
  assert.strictEqual(C.gameTimeMs('2026-10-05T12:00:00Z'), T0);
  for (const bad of [null, undefined, '', 'junk', NaN, Infinity, -1, 0, true, {}]) {
    assert.strictEqual(C.gameTimeMs(bad), null, String(bad));
  }
});

test('gameEventText: string or {Date, LogType, Log} -> one capped plain line', () => {
  assert.strictEqual(C.gameEventText('Login success'), 'Login success');
  assert.strictEqual(C.gameEventText({ Date: '2026-10-05 12:00:00', LogType: 'Info', Log: 'Enter world' }), 'Enter world');
  assert.strictEqual(C.gameEventText({ log: 'lower key' }), 'lower key');
  assert.strictEqual(C.gameEventText('a\r\nb\tc'), 'a b c');
  assert.strictEqual(C.gameEventText('x'.repeat(500)).length, 200);
  for (const bad of [null, undefined, '', '   ', 5, {}, { Log: 5 }, []]) {
    assert.strictEqual(C.gameEventText(bad), null, JSON.stringify(bad));
  }
});

test('normalizeGame: defensive over missing or wrong fields', () => {
  assert.strictEqual(C.normalizeGame(null), null);
  assert.strictEqual(C.normalizeGame([]), null);
  assert.strictEqual(C.normalizeGame('x'), null);
  assert.deepStrictEqual(C.normalizeGame({}), {
    state: 'unknown', since: null, log_file: null, last_event: null, screenshots: [], configured: null
  });
  const g = C.normalizeGame({
    state: 'logged_in', since: T0 / 1000, log_file: 'Client_2026-10-05_120000.json',
    last_event: { Log: 'Enter world' }, configured: true,
    screenshots: [
      { name: 'a.jpg', size: 1000, mtime: T0 / 1000 - 60 },
      { name: 'b.jpg', size: 2000, mtime: T0 / 1000 },
      { name: 'c.jpg' },
      null, 'x', { name: 5, mtime: T0 / 1000 }, { name: '', mtime: T0 / 1000 }
    ]
  });
  assert.strictEqual(g.state, 'logged_in');
  assert.strictEqual(g.since, T0);
  assert.strictEqual(g.log_file, 'Client_2026-10-05_120000.json');
  assert.strictEqual(g.last_event, 'Enter world');
  assert.strictEqual(g.configured, true);
  assert.deepStrictEqual(g.screenshots, [
    { name: 'b.jpg', size: 2000, mtime: T0 },
    { name: 'a.jpg', size: 1000, mtime: T0 - 60000 },
    { name: 'c.jpg', size: null, mtime: null }
  ], 'newest first, undated last, junk dropped');
});

test('normalizeGame: configured false forces unconfigured; unknown state strings -> unknown', () => {
  assert.strictEqual(C.normalizeGame({ state: 'running', configured: false }).state, 'unconfigured');
  assert.strictEqual(C.normalizeGame({ state: 'nope' }).state, 'unknown');
  assert.strictEqual(C.normalizeGame({ state: 'unconfigured' }).state, 'unconfigured');
  assert.strictEqual(C.normalizeGame({ log_file: 5, screenshots: 'x' }).log_file, null);
  assert.deepStrictEqual(C.normalizeGame({ screenshots: 'x' }).screenshots, []);
});

test('normalizeGame: log_file is a name only, screenshots capped at 50', () => {
  assert.strictEqual(C.normalizeGame({ log_file: 'dir\\sub/Client_x.json' }).log_file, 'Client_x.json');
  const many = [];
  for (let i = 0; i < 80; i++) many.push({ name: 's' + i + '.jpg', size: i, mtime: T0 / 1000 + i });
  const g = C.normalizeGame({ screenshots: many });
  assert.strictEqual(g.screenshots.length, C.GAME_SHOTS_MAX);
  assert.strictEqual(C.GAME_SHOTS_MAX, 50);
  assert.strictEqual(g.screenshots[0].name, 's79.jpg');
});

test('fmtClock: local HH:MM today, MM-DD HH:MM otherwise, null -> "-"', () => {
  const t = new Date(2026, 9, 5, 9, 7, 0).getTime();
  assert.strictEqual(C.fmtClock(t, new Date(2026, 9, 5, 23, 0, 0).getTime()), '09:07');
  assert.strictEqual(C.fmtClock(t, new Date(2026, 9, 6, 1, 0, 0).getTime()), '10-05 09:07');
  assert.strictEqual(C.fmtClock(null, t), '-');
});

test('gameSinceText: clock plus age, "-" without a time', () => {
  const t = new Date(2026, 9, 5, 9, 7, 0).getTime();
  assert.strictEqual(C.gameSinceText(t, t + 5 * 60000), '09:07 (5m ago)');
  assert.strictEqual(C.gameSinceText(t, t - 1000), '09:07 (0s ago)', 'clock skew never goes negative');
  assert.strictEqual(C.gameSinceText(null, t), '-');
});

test('GAME_CONFIG_HINT names both config keys and the file', () => {
  assert.match(C.GAME_CONFIG_HINT, /bdo\.install_dir/);
  assert.match(C.GAME_CONFIG_HINT, /bdo\.documents_dir/);
  assert.match(C.GAME_CONFIG_HINT, /config\/local\.json/);
});

test('game.js: safe DOM, GET-only, uses the shared helpers', () => {
  const src = read('dashboard/game.js');
  assert.doesNotMatch(src, /innerHTML|outerHTML|insertAdjacentHTML|document\.write/);
  assert.match(src, /\/api\/game/);
  // Plan 009 lets the card POST, but only through the preload bridge to
  // /api/ocr and /api/grind (ocr.test.js); never a renderer fetch POST.
  assert.doesNotMatch(src, /method:/, 'no renderer fetch POST');
  const routes = (src.match(/\.post\('([^']+)'/g) || []).map((m) => m.slice(7, -1)).sort();
  assert.deepStrictEqual([...new Set(routes)], ['/api/grind', '/api/ocr']);
  for (const f of ['normalizeGame', 'gameStateLabel', 'gameSinceText', 'fmtClock']) {
    assert.match(src, new RegExp('C\\.' + f + '\\('), f);
  }
  assert.match(src, /C\.GAME_CONFIG_HINT/);
  assert.doesNotMatch(src, /window\.open|location\.href\s*=|\.href\s*=|createElement\('a'\)/);
  assert.match(src, /window\.EWGame\s*=/);
});

test('dashboard loads game.js and mounts it on the System tab; CSP unchanged', () => {
  const html = read('dashboard/index.html');
  assert.match(html, /connect-src http:\/\/127\.0\.0\.1:8940;/);
  const iCore = html.indexOf('ewcore.js');
  const iGame = html.indexOf('<script src="game.js"></script>');
  const iDash = html.indexOf('dashboard.js');
  assert.ok(iCore >= 0 && iGame > iCore && iDash > iGame, 'script order ewcore, game, dashboard');
  const dash = read('dashboard/dashboard.js');
  assert.match(dash, /EWGame\.mount\(/);
  assert.match(dash, /EWGame\.show\(/);
  const css = read('shared/ew.css');
  assert.match(css, /\.ew-game/);
  assert.match(css, /\.ew-dot\.ok/);
  assert.match(css, /\.ew-dot\.warn/);
  assert.match(css, /\.ew-dot\.bad/);
  assert.match(css, /\.ew-dot\.off/);
});

test('overlay: GET-only game dot at the top, refreshed on the SSE game event', () => {
  const src = read('overlay/overlay.js');
  assert.match(src, /\/api\/game/);
  assert.doesNotMatch(src, /POST|ewApi|method:/);
  assert.match(src, /C\.normalizeGame\(/);
  assert.match(src, /C\.gameStateLabel\(/);
  assert.match(src, /addEventListener\('game'/);
  assert.doesNotMatch(src, /innerHTML|outerHTML|insertAdjacentHTML/);
  const html = read('overlay/index.html');
  const iGame = html.indexOf('id="ov-game-row"');
  const iDaily = html.indexOf('id="ov-daily"');
  assert.ok(iGame >= 0 && iGame < iDaily, 'game row is the overlay header');
  assert.match(html, /id="ov-game-dot"[^>]*class="ew-dot/);
  assert.match(html, /id="ov-game"/);
});
