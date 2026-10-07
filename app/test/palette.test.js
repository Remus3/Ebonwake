'use strict';
// Plan 050: command palette (Ctrl+K). Pure parser + search over the payloads
// the dashboard already polls (ewcore.js), and static guards on palette.js:
// dashboard-window keydown only, no global hotkey, every write an existing
// POST route through the toast bridge. No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const APP = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(APP, p), 'utf8');

const CAT = {
  today: { items: [
    { id: 'bs-dailies', title: 'Black Spirit dailies', kind: 'daily' },
    { id: 'guild-quest', title: 'Guild quest', kind: 'daily' },
    { id: 'guild-mission', title: 'Guild mission', kind: 'weekly' },
    { id: 'imperial', title: 'Imperial delivery', kind: 'daily' }
  ] },
  grind: {
    spots: [{ id: 'olun', name: "Olun's Valley" }, { id: 'gyfin', name: 'Gyfin Rhasia Temple' },
      { id: 'gyfin-up', name: 'Gyfin Underground' }],
    buffs: [{ id: 'xp-scroll', name: 'XP scroll' }],
    active: null
  },
  items: [{ id: 721003, sid: 0, name: 'Caphras Stone' }, { id: 16001, sid: 0, name: 'Black Stone (Weapon)' },
    { id: 16002, sid: 0, name: 'Black Stone (Armor)' }],
  tabs: [{ id: 'home', title: 'Home' }, { id: 'today', title: 'Today' }, { id: 'market', title: 'Market' },
    { id: 'grind', title: 'Grind' }, { id: 'system', title: 'System' }, { id: 'settings', title: 'Settings' }],
  events: { items: [
    { id: 'e1', kind: 'coupon', title: 'Weekend coupon', code: 'ABCD-EFGH-1234' },
    { id: 'e2', kind: 'event', title: 'Season pass week 3' }
  ] },
  deadeye: {
    sections: [{ id: 'rotation', title: 'Rotation', text: 'open with Vortex Charge\n\nthen Flash Kick' }],
    plan: [{ id: 'st1', item: 'Blackstar Mainhand', current: 'PRI', target: 'TET' }]
  }
};

const ok = (text, cat) => {
  const r = C.parseCommand(text, cat || CAT);
  assert.strictEqual(r.ok, true, text + ' -> ' + JSON.stringify(r));
  return r;
};
const bad = (text, cat) => {
  const r = C.parseCommand(text, cat || CAT);
  assert.strictEqual(r.ok, false, text + ' -> ' + JSON.stringify(r));
  assert.strictEqual(typeof r.error, 'string');
  assert.ok(r.error.length > 0);
  return r;
};

test('palette routes are exactly existing POST routes (no new write route)', () => {
  assert.deepStrictEqual(C.PALETTE_ROUTES.slice().sort(),
    ['/api/grind', '/api/leveling', '/api/market/watch', '/api/today']);
  for (const r of C.PALETTE_ROUTES) assert.ok(C.POST_ROUTES.indexOf(r) >= 0, r);
  const verbs = C.PALETTE_COMMANDS.map((c) => c.verb);
  assert.deepStrictEqual(verbs, ['tick', 'arm', 'start grind', 'stop grind', 'log xp', 'watch', 'go']);
  for (const c of C.PALETTE_COMMANDS) {
    assert.ok(c.route === null || C.PALETTE_ROUTES.indexOf(c.route) >= 0, c.verb);
    assert.match(c.usage, new RegExp('^' + c.verb));
  }
});

test('tick <daily>: fuzzy match against Today items -> {tick: id}', () => {
  const r = ok('tick black spirit');
  assert.strictEqual(r.route, '/api/today');
  assert.deepStrictEqual(r.body, { tick: 'bs-dailies' });
  assert.match(r.label, /Black Spirit dailies/);
  assert.deepStrictEqual(ok('TICK   imperial').body, { tick: 'imperial' });
  assert.deepStrictEqual(ok('tick guild quest').body, { tick: 'guild-quest' }, 'exact beats prefix');
  assert.deepStrictEqual(ok('tick bsd').body, { tick: 'bs-dailies' }, 'initials / subsequence');
  assert.deepStrictEqual(ok('tick spirit').body, { tick: 'bs-dailies' }, 'substring');
});

test('ambiguity is an error naming the candidates, never a guess', () => {
  const r = bad('tick guild');
  assert.match(r.error, /ambiguous/);
  assert.match(r.error, /Guild quest/);
  assert.match(r.error, /Guild mission/);
  assert.match(bad('start grind gyfin').error, /Gyfin Rhasia Temple.*Gyfin Underground|Gyfin Underground.*Gyfin Rhasia Temple/);
  assert.match(bad('watch black stone').error, /ambiguous/);
  assert.match(bad('tick nothing like it').error, /no Today item matches/);
});

test('rows sharing a name stay pickable: suggestions carry a key that parses to one row', () => {
  const cat = { items: [{ id: 1, sid: 0, name: 'X' }, { id: 1, sid: 1, name: 'X' }, { id: 1, sid: 0, name: 'X' }],
    today: { items: [{ id: 'a', title: 'Same' }, { id: 'b', title: 'same' }] } };
  assert.deepStrictEqual(C.parseCommand('watch x', cat).body, { add: { id: 1, sid: 0 } });
  assert.deepStrictEqual(C.parseCommand('watch x [1]', cat).body, { add: { id: 1, sid: 1 } });
  const ws = C.paletteSuggest('watch x', cat, 8).map((s) => s.text);
  assert.deepStrictEqual(ws, ['watch X', 'watch X [1]'], 'repeated (id, sid) dropped');
  assert.match(C.parseCommand('tick same', cat).error, /ambiguous/);
  const ts = C.paletteSuggest('tick same', cat, 8).map((s) => s.text);
  assert.deepStrictEqual(ts, ['tick Same (a)', 'tick same (b)']);
  for (const t of ts) assert.strictEqual(C.parseCommand(t, cat).ok, true, t);
  assert.deepStrictEqual(C.parseCommand('tick same (b)', cat).body, { tick: 'b' });
});

test('arm <buff> [duration]: plan 048 durations, default minutes per buff', () => {
  let r = ok('arm xp scroll');
  assert.strictEqual(r.route, '/api/grind');
  assert.deepStrictEqual(r.body, { buff: { name: 'XP scroll', minutes: 30 } });
  assert.deepStrictEqual(ok('arm xp scroll 1h30m').body, { buff: { name: 'XP scroll', minutes: 90 } });
  assert.deepStrictEqual(ok('arm value pack 30d').body, { buff: { name: 'Value Pack', minutes: 43200 } });
  assert.deepStrictEqual(ok('arm hot time 90').body, { buff: { name: 'Hot Time', minutes: 90 } }, 'bare number = minutes');
  r = ok('arm kama');
  assert.deepStrictEqual(r.body, { buff: { name: 'Kamasylve blessing', minutes: 43200 } }, 'default list without a payload row');
  assert.match(bad('arm value pack 31d').error, /43200/);
  assert.match(bad('arm 30m').error, /buff/);
  assert.match(bad('arm').error, /usage: arm/);
  // a buff whose name ends in a number: the whole name wins over a duration
  const tiers = { grind: { buffs: [{ id: 'tier', name: 'Tier' }, { id: 'tier-2', name: 'Tier 2' }] } };
  assert.deepStrictEqual(C.parseCommand('arm tier 2', tiers).body, { buff: { name: 'Tier 2', minutes: 60 } });
  assert.deepStrictEqual(C.parseCommand('arm tier 2m', tiers).body, { buff: { name: 'Tier', minutes: 2 } });
  assert.deepStrictEqual(C.parseCommand('arm tier 2 30m', tiers).body, { buff: { name: 'Tier 2', minutes: 30 } });
  const elixir = { grind: { buffs: [{ id: 'tier-2-elixir', name: 'Tier 2 Elixir' }] } };
  assert.deepStrictEqual(C.parseCommand('arm tier 2', elixir).body, { buff: { name: 'Tier 2 Elixir', minutes: 60 } });
  assert.deepStrictEqual(C.parseCommand('arm tier 2 elixir 30m', elixir).body, { buff: { name: 'Tier 2 Elixir', minutes: 30 } });
  assert.deepStrictEqual(C.paletteSuggest('arm tier 2', elixir, 1)[0].text, 'arm Tier 2 Elixir');
  for (const s of C.paletteSuggest('arm tie', tiers, 8)) {
    const r = C.parseCommand(s.text, tiers);
    assert.strictEqual(r.body.buff.name + ' - ' + C.fmtDurationShort(r.body.buff.minutes), s.label, s.text);
  }
  // no grind payload yet: the default list still arms
  assert.deepStrictEqual(C.parseCommand('arm drop', {}).body, { buff: { name: 'Drop rate scroll', minutes: 60 } });
});

test('start grind <spot> / stop grind [silver]', () => {
  let r = ok('start grind olun');
  assert.deepStrictEqual(r.body, { start: 'olun' });
  assert.strictEqual(r.route, '/api/grind');
  assert.deepStrictEqual(ok('start grind gyfin under').body, { start: 'gyfin-up' });
  const running = Object.assign({}, CAT, { grind: Object.assign({}, CAT.grind, { active: { spot: 'olun', started: '2026-10-05T00:00:00Z' } }) });
  assert.match(bad('start grind olun', running).error, /already running/);
  r = ok('stop grind 1.2b', running);
  assert.deepStrictEqual(r.body, { stop: { silver: 1200000000, trash: 0 } });
  assert.deepStrictEqual(ok('stop grind', running).body, { stop: { silver: 0, trash: 0 } });
  assert.deepStrictEqual(ok('stop grind 84,500,000', running).body, { stop: { silver: 84500000, trash: 0 } });
  assert.match(bad('stop grind lots', running).error, /silver/);
  assert.match(bad('stop grind 1.2b').error, /no grind session/);
  assert.match(bad('start grind').error, /usage: start grind/);
  assert.match(bad('start grind olun', {}).error, /no spots/);
});

test('log xp <level> <pct> -> leveling sample', () => {
  const r = ok('log xp 62 45.5');
  assert.strictEqual(r.route, '/api/leveling');
  assert.deepStrictEqual(r.body, { sample: { level: 62, pct: 45.5 } });
  assert.deepStrictEqual(ok('log xp 61 12.25%').body, { sample: { level: 61, pct: 12.25 } });
  assert.match(bad('log xp 62').error, /usage: log xp/);
  assert.match(bad('log xp 62 101').error, /XP %/);
  assert.match(bad('log xp abc 5').error, /level/);
  assert.match(bad('log xp 62 5 extra').error, /usage: log xp/);
});

test('watch <item name> -> add to the watchlist from the item index rows', () => {
  const r = ok('watch caphras');
  assert.strictEqual(r.route, '/api/market/watch');
  assert.deepStrictEqual(r.body, { add: { id: 721003, sid: 0 } });
  assert.deepStrictEqual(ok('watch black stone (armor)').body, { add: { id: 16002, sid: 0 } });
  assert.match(bad('watch caphras', {}).error, /no item/);
  assert.match(bad('watch').error, /usage: watch/);
});

test('go <tab> -> {tab}, no route', () => {
  const r = ok('go grind');
  assert.deepStrictEqual([r.tab, r.route, r.body], ['grind', undefined, undefined]);
  assert.strictEqual(ok('go mark').tab, 'market');
  assert.match(bad('go s').error, /ambiguous/);
  assert.match(bad('go nowhere').error, /no tab/);
});

test('bad input: unknown verb, blank, junk types, oversize', () => {
  assert.match(bad('fly to calpheon').error, /unknown command/);
  assert.match(bad('').error, /type a command/);
  assert.match(bad('   ').error, /type a command/);
  assert.strictEqual(C.parseCommand(null, CAT).ok, false);
  assert.strictEqual(C.parseCommand(42, CAT).ok, false);
  assert.strictEqual(C.parseCommand('tick ' + 'x'.repeat(300), CAT).ok, false);
  assert.strictEqual(C.parseCommand('tick black', null).ok, false);
  assert.strictEqual(C.parseCommand('tick black', { today: { items: 'junk' } }).ok, false);
  assert.match(bad('ticking black').error, /unknown command/, 'verb is a whole word');
});

test('every successful write passes the bridge validator for its route', () => {
  const running = Object.assign({}, CAT, { grind: Object.assign({}, CAT.grind, { active: { spot: 'olun' } }) });
  const cases = [['tick imperial', CAT], ['arm xp scroll 45m', CAT], ['start grind olun', CAT],
    ['stop grind 750k', running], ['log xp 60 0', CAT], ['watch caphras', CAT]];
  for (const [t, cat] of cases) {
    const r = ok(t, cat);
    assert.strictEqual(C.validPost(r.route, r.body), true, t);
  }
});

test('paletteSuggest: verbs while typing the verb, names after it', () => {
  const v = C.paletteSuggest('st', CAT, 8).map((s) => s.text);
  assert.deepStrictEqual(v, ['start grind ', 'stop grind ']);
  assert.strictEqual(C.paletteSuggest('', CAT, 8).length, 7, 'empty input lists every command');
  const t = C.paletteSuggest('tick gui', CAT, 8).map((s) => s.text);
  assert.deepStrictEqual(t, ['tick Guild quest', 'tick Guild mission']);
  const a = C.paletteSuggest('arm xp', CAT, 8);
  assert.strictEqual(a[0].text, 'arm XP scroll');
  assert.match(a[0].label, /30m/);
  assert.deepStrictEqual(C.paletteSuggest('go mar', CAT, 8).map((s) => s.text), ['go Market']);
  assert.strictEqual(C.paletteSuggest('log xp 6', CAT, 8)[0].label, 'log xp <level> <pct>');
  assert.strictEqual(C.paletteSuggest('tick a', CAT, 1).length, 1, 'max respected');
  assert.deepStrictEqual(C.paletteSuggest(null, CAT, 8), []);
});

test('paletteMode: "/" prefix is search, else a command', () => {
  assert.deepStrictEqual(C.paletteMode('/caph'), { mode: 'search', query: 'caph' });
  assert.deepStrictEqual(C.paletteMode('  / vortex '), { mode: 'search', query: 'vortex' });
  assert.deepStrictEqual(C.paletteMode('tick x'), { mode: 'command', text: 'tick x' });
  assert.deepStrictEqual(C.paletteMode(undefined), { mode: 'command', text: '' });
});

test('search mode: items, notes, coupons, enhancement steps, spots -> tab + row', () => {
  const idx = C.paletteIndex(CAT);
  const kinds = Array.from(new Set(idx.map((e) => e.kind))).sort();
  assert.deepStrictEqual(kinds, ['coupon', 'event', 'item', 'note', 'spot', 'step', 'today']);
  for (const e of idx) {
    assert.strictEqual(typeof e.label, 'string');
    assert.strictEqual(typeof e.find, 'string');
    assert.ok(['market', 'today', 'events', 'deadeye', 'grind'].indexOf(e.tab) >= 0, e.tab);
  }
  let hits = C.paletteSearch('caph', idx, 8);
  assert.strictEqual(hits[0].kind, 'item');
  assert.strictEqual(hits[0].tab, 'market');
  assert.strictEqual(hits[0].find, 'Caphras Stone');
  hits = C.paletteSearch('vortex', idx, 8);
  assert.deepStrictEqual([hits[0].kind, hits[0].tab, hits[0].find], ['note', 'deadeye', 'open with Vortex Charge']);
  assert.strictEqual(C.paletteSearch('flash', idx, 8)[0].find, 'then Flash Kick', 'one entry per note line');
  hits = C.paletteSearch('abcd', idx, 8);
  assert.deepStrictEqual([hits[0].kind, hits[0].tab, hits[0].find], ['coupon', 'events', 'ABCD-EFGH-1234']);
  assert.strictEqual(C.paletteSearch('blackstar', idx, 8)[0].kind, 'step');
  assert.strictEqual(C.paletteSearch('olun', idx, 8)[0].kind, 'spot');
  assert.strictEqual(C.paletteSearch('olun', idx, 8)[0].tab, 'grind');
  assert.deepStrictEqual(C.paletteSearch('', idx, 8), []);
  assert.deepStrictEqual(C.paletteSearch('zzzzqq', idx, 8), []);
  assert.strictEqual(C.paletteSearch('a', idx, 2).length, 2);
  // exact / prefix rank above loose matches
  assert.strictEqual(C.paletteSearch('black stone', idx, 8)[0].kind, 'item');
  assert.deepStrictEqual(C.paletteIndex(null), []);
  assert.deepStrictEqual(C.paletteIndex({ deadeye: { sections: 'x', plan: [null, 5] }, grind: { spots: [{}] } }), []);
});

test('palette.js: Ctrl+K in the dashboard window only, literal existing routes via the toast bridge', () => {
  const src = read('dashboard/palette.js');
  assert.match(src, /document\.addEventListener\('keydown'/);
  assert.match(src, /C\.paletteKey\(ev\)/);
  assert.doesNotMatch(src, /globalShortcut|ipcRenderer|innerHTML|outerHTML|insertAdjacentHTML|document\.write|eval\(/);
  const routes = (src.match(/\.post\('(\/api\/[^']+)'/g) || []).map((s) => s.replace(/^\.post\('|'$/g, '')).sort();
  assert.deepStrictEqual(routes, C.PALETTE_ROUTES.slice().sort(), 'one literal POST per palette route');
  assert.match(src, /C\.parseCommand\(/);
  assert.match(src, /C\.paletteSearch\(/);
  assert.match(src, /C\.validPost\(/);
  const html = read('dashboard/index.html');
  assert.match(html, /<script src="palette\.js"><\/script>/);
  assert.ok(html.indexOf('src="palette.js"') > html.indexOf('src="toast.js"'));
  assert.ok(html.indexOf('src="palette.js"') < html.indexOf('src="dashboard.js"'));
  assert.match(read('dashboard/dashboard.js'), /window\.EWDash = \{/);
  // main.js registers no new global shortcut for the palette
  assert.doesNotMatch(read('main.js'), /Ctrl\+K|CommandOrControl\+K/i);
});

test('paletteKey: Ctrl+K only (no Alt/Shift/Meta), case-insensitive', () => {
  assert.strictEqual(C.paletteKey({ ctrlKey: true, key: 'k' }), true);
  assert.strictEqual(C.paletteKey({ ctrlKey: true, key: 'K' }), true);
  assert.strictEqual(C.paletteKey({ ctrlKey: false, key: 'k' }), false);
  assert.strictEqual(C.paletteKey({ ctrlKey: true, shiftKey: true, key: 'k' }), false);
  assert.strictEqual(C.paletteKey({ ctrlKey: true, altKey: true, key: 'k' }), false);
  assert.strictEqual(C.paletteKey(null), false);
});
