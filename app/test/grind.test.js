'use strict';
// Plan 005 slice B: pure Grind helpers (ewcore.js), the /api/grind bridge route,
// overlay widget opt-in and static guards on the Grind tab (grind.js) and the
// overlay. No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const APP = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(APP, p), 'utf8');

test('silverPerHour = silver * 60 / minutes, integer; bad input gives null', () => {
  assert.strictEqual(C.silverPerHour(100000000, 60), 100000000);
  assert.strictEqual(C.silverPerHour(100000000, 30), 200000000);
  assert.strictEqual(C.silverPerHour(10, 7), 85); // 85.71 -> 85
  assert.strictEqual(C.silverPerHour(0, 60), 0);
  assert.strictEqual(C.silverPerHour(1e13, 1), 6e14);
  for (const bad of [[1, 0], [1, -5], [null, 60], [1, null], ['1', 60], [NaN, 1], [-1, 60]]) {
    assert.strictEqual(C.silverPerHour(bad[0], bad[1]), null, JSON.stringify(bad));
  }
});

test('fmtElapsed is a running clock h:mm:ss', () => {
  assert.strictEqual(C.fmtElapsed(0), '0:00:00');
  assert.strictEqual(C.fmtElapsed(5), '0:00:05');
  assert.strictEqual(C.fmtElapsed(125), '0:02:05');
  assert.strictEqual(C.fmtElapsed(3725.9), '1:02:05');
  assert.strictEqual(C.fmtElapsed(36000 + 59 * 60 + 59), '10:59:59');
  assert.strictEqual(C.fmtElapsed(-4), '0:00:00');
  assert.strictEqual(C.fmtElapsed(null), '-');
  assert.strictEqual(C.fmtElapsed('x'), '-');
});

test('liveElapsed: from started when parseable, else elapsed_s plus time since fetch', () => {
  const t0 = Date.parse('2026-10-04T12:00:00Z');
  assert.strictEqual(C.liveElapsed({ spot: 's', started: '2026-10-04T12:00:00Z', elapsed_s: 1 }, t0, t0 + 90500), 90);
  assert.strictEqual(C.liveElapsed({ spot: 's', started: 'junk', elapsed_s: 30 }, t0, t0 + 10000), 40);
  assert.strictEqual(C.liveElapsed({ spot: 's', elapsed_s: 30 }, t0, t0 - 5000), 30, 'never runs backwards');
  assert.strictEqual(C.liveElapsed({ spot: 's', started: '2026-10-04T12:00:10Z' }, t0, t0), 0, 'never negative');
  assert.strictEqual(C.liveElapsed(null, t0, t0), null);
  assert.strictEqual(C.liveElapsed({ spot: 's' }, t0, t0), null);
});

test('buffsLive: left_s from ends, expired dropped, soonest first; soonestBuff picks it', () => {
  const t0 = Date.parse('2026-10-04T12:00:00Z');
  const buffs = [
    { id: 'hot', name: 'Hot Time', ends: '2026-10-04T13:00:00Z', left_s: 3600 },
    { id: 'xp', name: 'XP scroll', ends: '2026-10-04T12:10:00Z', left_s: 600 },
    { id: 'old', name: 'Gone', ends: '2026-10-04T11:00:00Z', left_s: 0 },
    { id: 'nots', name: 'No ends', ends: null, left_s: 120 },
    { id: 'idle', name: 'Unarmed', ends: null },
    null, 'x'
  ];
  const live = C.buffsLive(buffs, t0, t0 + 60000);
  assert.deepStrictEqual(live.map((b) => [b.id, b.left_s]), [['nots', 60], ['xp', 540], ['hot', 3540]]);
  assert.strictEqual(buffs[1].left_s, 600, 'input not mutated');
  const s = C.soonestBuff(buffs, t0, t0 + 60000);
  assert.deepStrictEqual([s.id, s.left_s], ['nots', 60]);
  assert.strictEqual(C.soonestBuff([], t0, t0), null);
  assert.strictEqual(C.soonestBuff(null, t0, t0), null);
  assert.deepStrictEqual(C.buffsLive('x', t0, t0), []);
});

test('sortSpots: by silver_per_h descending, junk dropped, input not mutated', () => {
  const spots = [
    { id: 'a', name: 'A', sessions: 1, minutes: 60, silver_per_h: 100 },
    { id: 'b', name: 'B', sessions: 2, minutes: 60, silver_per_h: 300 },
    { id: 'c', name: 'C', sessions: 0, minutes: 0, silver_per_h: null },
    null
  ];
  assert.deepStrictEqual(C.sortSpots(spots).map((s) => s.id), ['b', 'a', 'c']);
  assert.strictEqual(spots[0].id, 'a');
  assert.deepStrictEqual(C.sortSpots(undefined), []);
});

test('spotName resolves a session spot by id, falls back to the raw value', () => {
  const spots = [{ id: 'gyfin', name: 'Gyfin Rhasia Temple' }];
  assert.strictEqual(C.spotName(spots, 'gyfin'), 'Gyfin Rhasia Temple');
  assert.strictEqual(C.spotName(spots, 'Olun'), 'Olun');
  assert.strictEqual(C.spotName(null, 'x'), 'x');
  assert.strictEqual(C.spotName(spots, null), '?');
});

test('validGrindBody accepts exactly the slice A POST shapes', () => {
  const ok = [
    { start: 'gyfin' }, { start: 'Gyfin Rhasia Temple' },
    { stop: { silver: 0, trash: 0 } }, { stop: { silver: 1e13, trash: 1e6 } },
    { log: { spot: 'gyfin', minutes: 60, silver: 123456789, trash: 4000 } },
    { log: { spot: 'gyfin', minutes: 1440, silver: 0, trash: 0 } },
    { delete: 's1' }, { delete: 's123456789' },
    { add_spot: 'Olun\'s Valley' }, { add_spot: 'x'.repeat(60) },
    { buff: { name: 'Hot Time', minutes: 60 } }, { buff: { name: 'Value Pack', minutes: 1 } },
    { buff: { name: 'Value Pack', minutes: 1441 } }, { buff: { name: 'Value Pack', minutes: 43200 } },
    { clear_buff: 'hot' }, { clear_buff: 'hot-time' }, { clear_buff: 'x'.repeat(40) }
  ];
  for (const b of ok) assert.strictEqual(C.validGrindBody(b), true, JSON.stringify(b));
  const bad = [
    null, [], 'x', {}, { start: 'a', add_spot: 'b' }, { bogus: 1 },
    { start: '' }, { start: '  ' }, { start: 5 }, { start: 'x'.repeat(61) }, { start: 'a\nb' },
    { stop: {} }, { stop: { silver: 1 } }, { stop: { silver: -1, trash: 0 } }, { stop: { silver: 1e13 + 1, trash: 0 } },
    { stop: { silver: 1, trash: 1e6 + 1 } }, { stop: { silver: 1.5, trash: 0 } }, { stop: { silver: 1, trash: 0, x: 1 } },
    { stop: null },
    { log: { spot: 'a', minutes: 0, silver: 1, trash: 1 } }, { log: { spot: 'a', minutes: 1441, silver: 1, trash: 1 } },
    { log: { spot: '', minutes: 60, silver: 1, trash: 1 } }, { log: { minutes: 60, silver: 1, trash: 1 } },
    { log: { spot: 'a', minutes: 60, silver: '1', trash: 1 } }, { log: { spot: 'a', minutes: 60, silver: 1, trash: 1, y: 2 } },
    { delete: '' }, { delete: -1 }, { delete: 1.5 }, { delete: null }, { delete: 'x'.repeat(61) },
    { delete: 7 }, { delete: 'abc123' }, { delete: 's' }, { delete: 's1234567890' }, { delete: 'S1' }, { delete: 's1 ' },
    { add_spot: '' }, { add_spot: 'x'.repeat(61) }, { add_spot: 3 },
    { buff: { name: 'Hot', minutes: 0 } }, { buff: { name: 'Hot', minutes: 43201 } }, { buff: { name: '', minutes: 5 } },
    { buff: { name: 'Hot' } }, { buff: { name: 'Hot', minutes: 5, ends: 1 } },
    { clear_buff: '' }, { clear_buff: {} }, { clear_buff: 3 }, { clear_buff: 'Hot' }, { clear_buff: 'hot time' },
    { clear_buff: 'x'.repeat(41) }, { clear_buff: 'hot_time' }
  ];
  for (const b of bad) assert.strictEqual(C.validGrindBody(b), false, JSON.stringify(b));
});

test('parseGrindForm: stop / log / buff / spot strings -> bodies or operator errors', () => {
  assert.deepStrictEqual(C.parseGrindForm('stop', { silver: ' 250000000 ', trash: '3200' }),
    { ok: true, body: { stop: { silver: 250000000, trash: 3200 } } });
  assert.deepStrictEqual(C.parseGrindForm('stop', { silver: '', trash: '' }),
    { ok: true, body: { stop: { silver: 0, trash: 0 } } }, 'blank = 0');
  assert.strictEqual(C.parseGrindForm('stop', { silver: '1.5', trash: '0' }).ok, false);
  assert.strictEqual(C.parseGrindForm('stop', { silver: '10000000000001', trash: '0' }).ok, false);
  assert.strictEqual(C.parseGrindForm('stop', { silver: '0', trash: '1000001' }).ok, false);
  assert.deepStrictEqual(C.parseGrindForm('log', { spot: 'gyfin', minutes: '90', silver: '5', trash: '1' }),
    { ok: true, body: { log: { spot: 'gyfin', minutes: 90, silver: 5, trash: 1 } } });
  assert.strictEqual(C.parseGrindForm('log', { spot: '', minutes: '90', silver: '5', trash: '1' }).ok, false);
  assert.strictEqual(C.parseGrindForm('log', { spot: 'a', minutes: '0', silver: '5', trash: '1' }).ok, false);
  assert.strictEqual(C.parseGrindForm('log', { spot: 'a', minutes: '', silver: '5', trash: '1' }).ok, false);
  assert.deepStrictEqual(C.parseGrindForm('buff', { name: 'Hot Time', minutes: '60' }),
    { ok: true, body: { buff: { name: 'Hot Time', minutes: 60 } } });
  assert.deepStrictEqual(C.parseGrindForm('buff', { name: 'Value Pack', minutes: '43200' }),
    { ok: true, body: { buff: { name: 'Value Pack', minutes: 43200 } } }, 'buffs run up to 30 days');
  assert.strictEqual(C.parseGrindForm('buff', { name: 'Hot Time', minutes: '1441' }).ok, true);
  const over = C.parseGrindForm('buff', { name: 'Hot Time', minutes: '43201' });
  assert.strictEqual(over.ok, false);
  assert.match(over.error, /1-43200/);
  assert.strictEqual(C.parseGrindForm('buff', { name: 'Hot Time', minutes: '0' }).ok, false);
  const logOver = C.parseGrindForm('log', { spot: 'a', minutes: '1441', silver: '5', trash: '1' });
  assert.strictEqual(logOver.ok, false, 'sessions stay 1-1440');
  assert.match(logOver.error, /1-1440/);
  assert.deepStrictEqual(C.BUFF_MINUTES, [1, 43200]);
  assert.deepStrictEqual(C.parseGrindForm('spot', { name: '  Olun  ' }), { ok: true, body: { add_spot: 'Olun' } });
  assert.strictEqual(C.parseGrindForm('spot', { name: ' ' }).ok, false);
  assert.strictEqual(C.parseGrindForm('spot', { name: 'x'.repeat(61) }).ok, false);
  assert.strictEqual(C.parseGrindForm('nope', {}).ok, false);
  for (const k of ['stop', 'log', 'buff', 'spot']) {
    const r = C.parseGrindForm(k, {});
    if (r.ok) assert.strictEqual(C.validGrindBody(r.body), true, k);
    else assert.strictEqual(typeof r.error, 'string');
  }
});

test('BUFF_DEFAULTS: the plan\'s buff kinds, each armable with its default minutes', () => {
  const names = C.BUFF_DEFAULTS.map((b) => b.name.toLowerCase()).join('|');
  for (const k of ['xp', 'drop', 'hot time', 'value pack', 'old moon', 'kamasylve']) assert.match(names, new RegExp(k));
  for (const b of C.BUFF_DEFAULTS) {
    assert.strictEqual(C.validGrindBody({ buff: { name: b.name, minutes: b.minutes } }), true, b.name);
  }
});

test('BUFF_DEFAULTS names are exactly the server SEED_BUFFS (one list, both sides)', () => {
  const py = fs.readFileSync(path.join(__dirname, '..', '..', 'server', 'ew', 'grind.py'), 'utf8');
  const m = py.match(/SEED_BUFFS = \(([^)]*)\)/);
  assert.ok(m, 'SEED_BUFFS tuple found');
  const seeds = Array.from(m[1].matchAll(/"([^"]+)"/g), (x) => x[1]);
  assert.ok(seeds.length >= 6);
  assert.deepStrictEqual(C.BUFF_DEFAULTS.map((b) => b.name), seeds);
});

test('buffRows: every server buff listed (armed or not), defaults only for missing names', () => {
  const t0 = Date.parse('2026-10-04T12:00:00Z');
  const server = [
    { id: 'xp-scroll', name: 'XP scroll', ends: null, left_s: null },
    { id: 'hot-time', name: 'Hot Time', ends: '2026-10-04T12:30:00Z', left_s: 1800 },
    { id: 'elixir-set', name: 'Elixir set', ends: null, left_s: null },
    { id: 'old', name: 'Expired one', ends: '2026-10-04T11:00:00Z', left_s: null }
  ];
  const rows = C.buffRows(server, t0, t0);
  assert.deepStrictEqual(rows[0].name, 'Hot Time', 'armed first');
  for (const b of server) {
    const r = rows.filter((x) => x.name.toLowerCase() === b.name.toLowerCase());
    assert.strictEqual(r.length, 1, b.name);
    assert.strictEqual(r[0].id, b.id, b.name + ' keeps its server id');
  }
  assert.strictEqual(rows.filter((r) => r.name === 'Elixir set')[0].left_s, null);
  assert.strictEqual(rows.filter((r) => r.name === 'Expired one')[0].left_s, null);
  const lower = rows.map((r) => r.name.toLowerCase());
  assert.strictEqual(new Set(lower).size, lower.length, 'no duplicate names');
  for (const d of C.BUFF_DEFAULTS) assert.ok(lower.indexOf(d.name.toLowerCase()) >= 0, d.name);
  assert.strictEqual(rows.length, server.length + C.BUFF_DEFAULTS.length - 2);
  // A full server seed list: nothing injected client-side.
  const seeded = C.BUFF_DEFAULTS.map((d, i) => ({ id: 'b' + i, name: d.name.toUpperCase(), ends: null, left_s: null }));
  const r2 = C.buffRows(seeded, t0, t0);
  assert.strictEqual(r2.length, seeded.length);
  assert.ok(r2.every((r) => r.id !== null));
  assert.ok(r2.every((r) => r.minutes >= 1));
});

test('grind.js: buff minutes input fits 43200', () => {
  const src = read('dashboard/grind.js');
  assert.match(src, /min\.maxLength = 5;/);
  assert.match(src, /1-43200/);
  assert.doesNotMatch(src, /minutes \(1-1440\)/);
});

test('buffRows: server buffs merged with defaults by name, armed first by time left', () => {
  const t0 = Date.parse('2026-10-04T12:00:00Z');
  const rows = C.buffRows([
    { id: 'h', name: 'hot time', ends: '2026-10-04T12:30:00Z', left_s: 1800 },
    { id: 'c', name: 'Custom buff', ends: '2026-10-04T12:05:00Z', left_s: 300 }
  ], t0, t0);
  assert.deepStrictEqual(rows.slice(0, 2).map((r) => [r.name, r.left_s, r.id]), [['Custom buff', 300, 'c'], ['hot time', 1800, 'h']]);
  const names = rows.map((r) => r.name.toLowerCase());
  assert.strictEqual(names.filter((n) => n === 'hot time').length, 1, 'no duplicate for an armed default');
  assert.strictEqual(rows.length, C.BUFF_DEFAULTS.length + 1);
  rows.slice(2).forEach((r) => { assert.strictEqual(r.left_s, null); assert.ok(r.minutes >= 1); });
  assert.strictEqual(C.buffRows(null, t0, t0).length, C.BUFF_DEFAULTS.length);
});

test('overlayWidgets: grind widgets default on, opt-out via config.overlay.widgets', () => {
  // eventsSoon (plan 006) rides the same mechanism; see events.test.js.
  // leveling (plan 011) is the one opt-in-only widget: default off.
  const on = { grindSession: true, grindBuff: true, eventsSoon: true, leveling: false, season: false };
  assert.deepStrictEqual(C.overlayWidgets({}), on);
  assert.deepStrictEqual(C.overlayWidgets(null), on);
  assert.deepStrictEqual(C.overlayWidgets({ overlay: { widgets: { grindBuff: false } } }), Object.assign({}, on, { grindBuff: false }));
  assert.deepStrictEqual(C.overlayWidgets({ overlay: { widgets: { grindSession: 'no', grindBuff: 0 } } }),
    on, 'only a literal false turns one off');
  assert.deepStrictEqual(C.widgetsFromQuery('?grindSession=0&grindBuff=1'), Object.assign({}, on, { grindSession: false }));
  assert.deepStrictEqual(C.widgetsFromQuery(''), on);
  const q = C.widgetsQuery({ grindSession: false, grindBuff: true });
  assert.deepStrictEqual(C.widgetsFromQuery('?' + new URLSearchParams(q).toString()), Object.assign({}, on, { grindSession: false }));
});

test('validPost allowlist now carries /api/grind with its own validator', () => {
  assert.ok(['/api/grind', '/api/market/watch', '/api/progress', '/api/today'].every((r) => C.POST_ROUTES.indexOf(r) >= 0));
  assert.strictEqual(C.validPost('/api/grind', { start: 'gyfin' }), true);
  assert.strictEqual(C.validPost('/api/grind', { tick: 'dice' }), false);
  assert.strictEqual(C.validPost('/api/today', { start: 'gyfin' }), false);
  assert.strictEqual(C.validPost('/api/grind/', { start: 'gyfin' }), false);
  assert.match(read('preload.js'), /\/api\/grind/);
});

test('grind.js: safe DOM, POST via the bridge only, uses the shared helpers', () => {
  const src = read('dashboard/grind.js');
  assert.doesNotMatch(src, /innerHTML|outerHTML|insertAdjacentHTML|document\.write/);
  assert.match(src, /\/api\/grind/);
  assert.match(src, /ewApi/);
  for (const f of ['fmtElapsed', 'liveElapsed', 'buffRows', 'sortSpots', 'parseGrindForm', 'fmtSilver']) {
    assert.match(src, new RegExp('C\\.' + f + '\\('), f);
  }
  assert.match(src, /sure\?/, 'two-click delete confirm');
  assert.doesNotMatch(src, /method:\s*'POST'/, 'renderer never POSTs directly');
  assert.match(src, /window\.EWGrind\s*=/);
});

test('dashboard loads and mounts grind.js; CSP unchanged', () => {
  const html = read('dashboard/index.html');
  assert.match(html, /connect-src http:\/\/127\.0\.0\.1:8940;/);
  const iCore = html.indexOf('ewcore.js');
  const iGrind = html.indexOf('<script src="grind.js"></script>');
  const iDash = html.indexOf('dashboard.js');
  assert.ok(iCore >= 0 && iGrind > iCore && iDash > iGrind, 'script order ewcore, grind, dashboard');
  const dash = read('dashboard/dashboard.js');
  assert.match(dash, /EWGrind\.mount\(/);
  assert.match(dash, /EWGrind\.show\(/);
});

test('overlay: GET-only grind widgets, opt-in from the query main passes', () => {
  const src = read('overlay/overlay.js');
  assert.match(src, /\/api\/grind/);
  assert.doesNotMatch(src, /POST|ewApi|method:/);
  assert.match(src, /C\.widgetsFromQuery\(/);
  assert.match(src, /C\.soonestBuff\(/);
  const html = read('overlay/index.html');
  assert.match(html, /id="ov-grind"/);
  assert.match(html, /id="ov-buff"/);
  const m = read('main.js');
  assert.match(m, /core\.overlayWidgets\(/);
  // Plan 022: the overlay query also carries scale / opacity.
  assert.match(m, /const query = Object\.assign\(core\.widgetsQuery\(/);
  assert.match(m, /\{ query: query \}/);
});
