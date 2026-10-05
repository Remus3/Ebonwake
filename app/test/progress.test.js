'use strict';
// Plan 004 slice B: pure Progress helpers (ewcore.js), the /api/progress bridge
// route and static guards on the Progress tab (progress.js). No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const APP = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(APP, p), 'utf8');

test('gsTotal = (ap + aap) / 2 + dp; non-numbers give null', () => {
  assert.strictEqual(C.gsTotal(300, 300, 400), 700);
  assert.strictEqual(C.gsTotal(301, 300, 400), 700.5);
  assert.strictEqual(C.gsTotal(0, 0, 0), 0);
  assert.strictEqual(C.gsTotal(999, 999, 999), 1998);
  for (const bad of [[null, 1, 1], [1, undefined, 1], [1, 1, '1'], [NaN, 1, 1], [1, 1, Infinity]]) {
    assert.strictEqual(C.gsTotal(bad[0], bad[1], bad[2]), null, JSON.stringify(bad));
  }
});

test('trackPct counts steps with done_at; pct = floor(100 * done / total) like the server', () => {
  const t = (marks) => ({ id: 't', steps: marks.map((m, i) => ({ id: 's' + i, title: 'S' + i, done_at: m ? '2026-10-04T00:00:00Z' : null })) });
  assert.deepStrictEqual(C.trackPct(t([1, 0, 0])), { done: 1, total: 3, pct: 33 });
  assert.deepStrictEqual(C.trackPct(t([1, 1, 0])), { done: 2, total: 3, pct: 66 });
  assert.deepStrictEqual(C.trackPct(t([1, 1, 1])), { done: 3, total: 3, pct: 100 });
  assert.deepStrictEqual(C.trackPct(t([])), { done: 0, total: 0, pct: 0 });
  // floor: 59/60 is 98, never a false 100
  const many = t(Array.from({ length: 60 }, (_, i) => (i < 59 ? 1 : 0)));
  assert.deepStrictEqual(C.trackPct(many), { done: 59, total: 60, pct: 98 });
  assert.deepStrictEqual(C.trackPct(t([1, 0, 0, 0, 0, 0, 0, 0])), { done: 1, total: 8, pct: 12 });
  // missing done_at key = not done; junk steps skipped
  assert.deepStrictEqual(C.trackPct({ steps: [{ id: 'a' }, null, 'x', { id: 'b', done_at: '2026-10-04T00:00:00Z' }] }),
    { done: 1, total: 2, pct: 50 });
  for (const bad of [null, undefined, {}, { steps: 'x' }]) {
    assert.deepStrictEqual(C.trackPct(bad), { done: 0, total: 0, pct: 0 });
  }
});

test('withStep: optimistic copy with one step set/cleared and counts re-derived', () => {
  const data = {
    character: {},
    tracks: [
      { id: 'main', steps: [{ id: 'c1', done_at: null }, { id: 'c2', done_at: null }], done: 0, total: 2, pct: 0 },
      { id: 'pen', steps: [{ id: 'c1', done_at: 'old' }], done: 1, total: 1, pct: 100 }
    ]
  };
  const d = C.withStep(data, 'main', 'c2', '2026-10-04T12:00:00Z');
  assert.strictEqual(d.tracks[0].steps[1].done_at, '2026-10-04T12:00:00Z');
  assert.deepStrictEqual([d.tracks[0].done, d.tracks[0].total, d.tracks[0].pct], [1, 2, 50]);
  assert.strictEqual(d.tracks[1], data.tracks[1], 'other tracks untouched');
  assert.strictEqual(data.tracks[0].steps[1].done_at, null, 'input not mutated');
  const u = C.withStep(data, 'pen', 'c1', null);
  assert.strictEqual(u.tracks[1].steps[0].done_at, null);
  assert.deepStrictEqual([u.tracks[1].done, u.tracks[1].pct], [0, 0]);
  assert.strictEqual(C.withStep(null, 'main', 'c1', null), null);
});

test('validProgressBody accepts exactly the slice A POST shapes', () => {
  const ok = [
    { character: { level: 62, gs: { ap: 310, aap: 312, dp: 400 } } },
    { character: { level: 1 } }, { character: { level: 70 } },
    { character: { gs: { ap: 0, aap: 999, dp: 0 } } },
    { character: { name: 'Moonbeam', cls: 'Deadeye', level: 61 } },
    { step: { track: 'main-story', step: 'ch-1', done: true } },
    { step: { track: 'tuvala-pen', step: 'weapon', done: false } },
    { add_track: { title: 'Life skill', kind: 'quest', steps: ['Apprentice', 'Skilled'] } },
    { add_track: { title: 'Season', kind: 'season', steps: [] } },
    { add_track: { title: 'Gear', kind: 'gear', steps: ['Weapon'] } },
    { add_track: { title: 'Max', kind: 'gear', steps: Array(60).fill('s') } },
    { remove_track: 'main-story' }
  ];
  for (const b of ok) assert.strictEqual(C.validProgressBody(b), true, JSON.stringify(b));
  const bad = [
    null, 'x', [], {}, { character: {} }, { character: null },
    { character: { level: 0 } }, { character: { level: 71 } }, { character: { level: 1.5 } },
    { character: { level: '60' } }, { character: { gs: { ap: -1, aap: 0, dp: 0 } } },
    { character: { gs: { ap: 1000 } } }, { character: { gs: { ap: 1, x: 1 } } }, { character: { gs: 5 } },
    { character: { gs: {} } }, { character: { level: 60, extra: 1 } }, { character: { name: '' } },
    { character: { name: 'a\u0007b' } }, { character: { cls: 5 } },
    { step: { track: 'main', step: 'c1' } }, { step: { track: 'main', step: 'c1', done: 1 } },
    { step: { track: 'Main', step: 'c1', done: true } }, { step: { track: 'main', step: '', done: true } },
    { step: { track: 'main', step: 'c1', done: true, x: 1 } }, { step: 'main' },
    { add_track: { title: '', kind: 'quest', steps: [] } }, { add_track: { title: 'x', kind: 'daily', steps: [] } },
    { add_track: { title: 'x', kind: 'quest' } }, { add_track: { title: 'x', kind: 'quest', steps: 'a' } },
    { add_track: { title: 'x', kind: 'quest', steps: [''] } }, { add_track: { title: 'x', kind: 'quest', steps: [5] } },
    { add_track: { title: 'x', kind: 'quest', steps: Array(61).fill('s') } },
    { step: { track: 'tuvala_pen', step: 'weapon', done: false } }, { remove_track: 'a_b' },
    { add_track: { title: 'x', kind: 'quest', steps: [], y: 1 } },
    { remove_track: '' }, { remove_track: 5 }, { remove_track: 'a b' }, { remove_track: 'a'.repeat(41) },
    { remove_track: 'main', step: { track: 'main', step: 'c1', done: true } }, { bogus: 1 }
  ];
  for (const b of bad) assert.strictEqual(C.validProgressBody(b), false, JSON.stringify(b));
});

test('validPost allowlist now carries /api/progress with its own validator', () => {
  assert.ok(C.POST_ROUTES.indexOf('/api/progress') >= 0);
  assert.strictEqual(C.validPost('/api/progress', { remove_track: 'main' }), true);
  assert.strictEqual(C.validPost('/api/progress', { tick: 'dice' }), false);
  assert.strictEqual(C.validPost('/api/today', { remove_track: 'main' }), false);
  assert.strictEqual(C.validPost('/api/progress/', { remove_track: 'main' }), false);
});

test('parseCharacterForm builds a character body or an operator error', () => {
  assert.deepStrictEqual(C.parseCharacterForm({ level: ' 62 ', ap: '310', aap: '312', dp: '400' }),
    { ok: true, body: { character: { level: 62, gs: { ap: 310, aap: 312, dp: 400 } } } });
  for (const bad of [
    { level: '', ap: '1', aap: '1', dp: '1' }, { level: '71', ap: '1', aap: '1', dp: '1' },
    { level: '0', ap: '1', aap: '1', dp: '1' }, { level: '60', ap: '1000', aap: '1', dp: '1' },
    { level: '60', ap: '-1', aap: '1', dp: '1' }, { level: '60', ap: '1.5', aap: '1', dp: '1' },
    { level: '60', ap: '1', aap: '', dp: '1' }, {}
  ]) {
    const r = C.parseCharacterForm(bad);
    assert.strictEqual(r.ok, false, JSON.stringify(bad));
    assert.strictEqual(typeof r.error, 'string');
  }
  const r = C.parseCharacterForm({ level: '70', ap: '0', aap: '999', dp: '0' });
  assert.strictEqual(C.validProgressBody(r.body), true);
});

test('parseTrackForm: one step per line, blanks dropped', () => {
  assert.deepStrictEqual(C.parseTrackForm({ title: ' Life ', kind: 'quest', steps: 'A\r\n\n  B  \n' }),
    { ok: true, body: { add_track: { title: 'Life', kind: 'quest', steps: ['A', 'B'] } } });
  for (const bad of [
    { title: '', kind: 'quest', steps: 'A' }, { title: 'x', kind: 'daily', steps: 'A' },
    { title: 'x', kind: 'gear', steps: '\n \n' }, { title: 'x', kind: 'gear', steps: 'y'.repeat(81) },
    { title: 'x', kind: 'gear', steps: Array(61).fill('s').join('\n') }
  ]) {
    const r = C.parseTrackForm(bad);
    assert.strictEqual(r.ok, false, JSON.stringify(bad));
    assert.strictEqual(typeof r.error, 'string');
  }
  const r = C.parseTrackForm({ title: 'Gear', kind: 'gear', steps: 'Weapon\nAwakening' });
  assert.strictEqual(C.validProgressBody(r.body), true);
});

test('profilePill: none / no data / fresh / stale / error', () => {
  for (const none of [null, undefined, { status: 'none' }, { data: null, freshness: null, status: 'none' }]) {
    const p = C.profilePill(none);
    assert.strictEqual(p.cls, 'unknown');
    assert.strictEqual(p.none, true, JSON.stringify(none));
  }
  const fresh = C.profilePill({ data: {}, freshness: { fetched_at: 'x', age_s: 120, ttl_s: 3600, stale: false, error: null } });
  assert.deepStrictEqual([fresh.cls, fresh.label, fresh.none], ['ok', 'profile 2m ago', false]);
  const stale = C.profilePill({ data: {}, freshness: { fetched_at: 'x', age_s: 120, ttl_s: 3600, stale: true, error: 'being fetched, retry later' } });
  assert.deepStrictEqual([stale.cls, stale.stale, stale.error], ['warn', true, 'being fetched, retry later']);
  assert.strictEqual(C.profilePill({ data: {}, freshness: { fetched_at: 'x', age_s: 4 * 3600, ttl_s: 3600 } }).cls, 'bad');
  // default TTL is the plan's 3600 s
  assert.strictEqual(C.profilePill({ data: {}, freshness: { fetched_at: 'x', age_s: 3000 } }).cls, 'ok');
  const nod = C.profilePill({ data: null, freshness: { fetched_at: null, error: 'HTTP 503' } });
  assert.deepStrictEqual([nod.cls, nod.label, nod.none, nod.error], ['unknown', 'profile no data', false, 'HTTP 503']);
  // market pill unchanged by the shared helper
  assert.strictEqual(C.marketPill({ fetched_at: 'x', age_s: 59, ttl_s: 300 }).label, 'arsha 59s ago');
});

test('profileRows: server shape {family, region, guild: str|null, characters: [{name, cls, level, main}]}', () => {
  const rows = C.profileRows({
    family: 'Fam', region: 'NA', guild: 'G',
    characters: [{ name: 'B', cls: 'Witch', level: 56, main: false }, { name: 'A', cls: 'Deadeye', level: 62, main: true }],
    secret: '<b>x</b>', contributionPoints: 300
  });
  assert.deepStrictEqual(rows, [
    ['Family', 'Fam'], ['Region', 'NA'], ['Guild', 'G'], ['Main', 'A - Deadeye 62'], ['Characters', '2']
  ]);
  assert.deepStrictEqual(C.profileRows({ family: 'F', region: 'NA', guild: null, characters: [{ name: 'C', cls: 'Ranger', level: null, main: false }] }),
    [['Family', 'F'], ['Region', 'NA'], ['Main', 'C - Ranger'], ['Characters', '1']]);
  // raw upstream names are not the served contract
  assert.deepStrictEqual(C.profileRows({ familyName: 'F', guild: { name: 'G' }, characters: [{ name: 'C', class: 'Ranger' }] }),
    [['Main', 'C'], ['Characters', '1']]);
  assert.deepStrictEqual(C.profileRows({ family: 'F', characters: [] }), [['Family', 'F']]);
  assert.deepStrictEqual(C.profileRows(null), []);
  assert.deepStrictEqual(C.profileRows('x'), []);
});

test('profilePill: status "pending" (being fetched, no cache) is muted, not an error', () => {
  const p = C.profilePill({ data: null, freshness: null, status: 'pending' });
  assert.deepStrictEqual([p.cls, p.pending, p.none, p.error], ['unknown', true, false, null]);
  assert.match(p.label, /fetching/);
  const q = C.profilePill({ data: null, freshness: { fetched_at: null, age_s: null, ttl_s: 3600, stale: true, error: 'being fetched, retry later' }, status: 'pending' });
  assert.deepStrictEqual([q.pending, q.error], [true, null]);
  assert.strictEqual(C.profilePill({ data: null, freshness: { fetched_at: null, error: 'HTTP 503' }, status: 'error' }).pending, false);
  const src = read('dashboard/progress.js');
  assert.match(src, /p\.pending/);
});

test('progress.js: safe DOM, POST via the bridge only, optimistic with revert', () => {
  const src = read('dashboard/progress.js');
  assert.doesNotMatch(src, /innerHTML|outerHTML|insertAdjacentHTML|document\.write/);
  assert.match(src, /\/api\/progress/);
  assert.match(src, /ewApi/);
  assert.match(src, /C\.withStep\(/);
  assert.match(src, /C\.trackPct\(/);
  assert.match(src, /C\.gsTotal\(/);
  assert.match(src, /C\.profilePill\(/);
  assert.doesNotMatch(src, /method:\s*'POST'/, 'renderer never POSTs directly');
  assert.match(src, /window\.EWProgress\s*=/);
});

test('dashboard loads and mounts progress.js; CSP unchanged', () => {
  const html = read('dashboard/index.html');
  assert.match(html, /connect-src http:\/\/127\.0\.0\.1:8940;/);
  const iCore = html.indexOf('ewcore.js');
  const iProg = html.indexOf('<script src="progress.js"></script>');
  const iDash = html.indexOf('dashboard.js');
  assert.ok(iCore >= 0 && iProg > iCore && iDash > iProg, 'script order ewcore, progress, dashboard');
  const dash = read('dashboard/dashboard.js');
  assert.match(dash, /EWProgress\.mount\(/);
  assert.match(dash, /EWProgress\.show\(/);
});

test('overlay never posts to /api/progress', () => {
  const src = read('overlay/overlay.js');
  assert.doesNotMatch(src, /\/api\/progress|POST|ewApi/);
});
