'use strict';
// Plan 011: pure Leveling helpers (ewcore.js), the /api/leveling bridge route,
// grind buff xp_pct, overlay widget opt-in and static guards on the Leveling
// card (leveling.js) and the overlay. No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const APP = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(APP, p), 'utf8');

test('validLevelingBody: exact shapes for every op', () => {
  const ok = [
    { sample: { level: 52, pct: 37.512 } }, { sample: { level: 1, pct: 0 } }, { sample: { level: 75, pct: 100 } },
    { sample_del: '2026-10-05T12:00:00+00:00' },
    { hot_add: { days: [0, 6], start: '22:00', end: '02:00', label: 'Hot Time', pct: 50 } },
    { hot_add: { days: [3], start: '00:00', end: '23:59', label: 'x', pct: 1000 } },
    { hot_del: 'h12' }, { milestones: [] }, { milestones: [50, 56, 61] }
  ];
  for (const b of ok) assert.strictEqual(C.validLevelingBody(b), true, JSON.stringify(b));
  const hot = { days: [0], start: '11:00', end: '12:00', label: 'a', pct: 5 };
  const bad = [
    null, [], 'x', {}, { bogus: 1 }, { sample: { level: 52, pct: 1 }, hot_del: 'h1' },
    { sample: { level: 0, pct: 1 } }, { sample: { level: 76, pct: 1 } }, { sample: { level: 52, pct: 100.5 } },
    { sample: { level: 52, pct: -1 } }, { sample: { level: 52, pct: 1.2345 } }, { sample: { level: 52.5, pct: 1 } },
    { sample: { level: 52, pct: '1' } }, { sample: { level: 52 } }, { sample: { level: 52, pct: 1, x: 1 } },
    { sample: { level: 52, pct: NaN } }, { sample_del: '' }, { sample_del: 5 },
    { hot_del: 'x1' }, { hot_del: 5 },
    { hot_add: Object.assign({}, hot, { days: [] }) }, { hot_add: Object.assign({}, hot, { days: [7] }) },
    { hot_add: Object.assign({}, hot, { days: [0, 0] }) }, { hot_add: Object.assign({}, hot, { start: '24:00' }) },
    { hot_add: Object.assign({}, hot, { end: '11:00' }) }, { hot_add: Object.assign({}, hot, { label: '' }) },
    { hot_add: Object.assign({}, hot, { label: 'x'.repeat(41) }) }, { hot_add: Object.assign({}, hot, { pct: 1001 }) },
    { hot_add: Object.assign({}, hot, { pct: 1.5 }) }, { hot_add: Object.assign({}, hot, { x: 1 }) },
    { milestones: [0] }, { milestones: [76] }, { milestones: [50, 50] }, { milestones: 'x' },
    { milestones: Array.from({ length: 21 }, (_, i) => i + 1) }
  ];
  for (const b of bad) assert.strictEqual(C.validLevelingBody(b), false, JSON.stringify(b));
});

test('validPost allowlist carries /api/leveling with its own validator', () => {
  assert.ok(C.POST_ROUTES.indexOf('/api/leveling') >= 0);
  assert.strictEqual(C.validPost('/api/leveling', { hot_del: 'h1' }), true);
  assert.strictEqual(C.validPost('/api/leveling', { start: 'gyfin' }), false);
  assert.strictEqual(C.validPost('/api/grind', { hot_del: 'h1' }), false);
  for (const r of ['/api/grind', '/api/progress', '/api/ocr', '/api/deadeye']) assert.ok(C.POST_ROUTES.indexOf(r) >= 0, r);
  assert.match(read('preload.js'), /\/api\/leveling/);
});

test('parseSampleForm: "52" + "37.512" -> body; operator errors otherwise', () => {
  assert.deepStrictEqual(C.parseSampleForm({ level: '52', pct: '37.512' }),
    { ok: true, body: { sample: { level: 52, pct: 37.512 } } });
  assert.deepStrictEqual(C.parseSampleForm({ level: ' 52 ', pct: '37,5%' }),
    { ok: true, body: { sample: { level: 52, pct: 37.5 } } });
  assert.deepStrictEqual(C.parseSampleForm({ level: '75', pct: '100' }).body, { sample: { level: 75, pct: 100 } });
  for (const f of [{ level: '', pct: '1' }, { level: '76', pct: '1' }, { level: '52', pct: '' },
    { level: '52', pct: '100.1' }, { level: '52', pct: '1.2345' }, { level: '52', pct: 'abc' }, {}]) {
    const r = C.parseSampleForm(f);
    assert.strictEqual(r.ok, false, JSON.stringify(f));
    assert.strictEqual(typeof r.error, 'string');
  }
  const r = C.parseSampleForm({ level: '52', pct: '0.001' });
  assert.ok(r.ok && C.validLevelingBody(r.body));
});

test('parseHotForm and parseMilestones', () => {
  const r = C.parseHotForm({ days: ['6', 0], start: '22:00', end: '02:00', label: ' Hot Time ', pct: '50' });
  assert.deepStrictEqual(r, { ok: true, body: { hot_add: { days: [0, 6], start: '22:00', end: '02:00', label: 'Hot Time', pct: 50 } } });
  assert.ok(C.validLevelingBody(r.body));
  assert.strictEqual(C.parseHotForm({ days: [], start: '22:00', end: '02:00', label: 'a', pct: '5' }).ok, false);
  assert.strictEqual(C.parseHotForm({ days: [1], start: '9:00', end: '10:00', label: 'a', pct: '5' }).ok, false);
  assert.strictEqual(C.parseHotForm({ days: [1], start: '09:00', end: '09:00', label: 'a', pct: '5' }).ok, false);
  assert.strictEqual(C.parseHotForm({ days: [1], start: '09:00', end: '10:00', label: '', pct: '5' }).ok, false);
  assert.strictEqual(C.parseHotForm({ days: [1], start: '09:00', end: '10:00', label: 'a', pct: '1001' }).ok, false);
  assert.deepStrictEqual(C.parseMilestones('61, 50 56'), { ok: true, body: { milestones: [50, 56, 61] } });
  assert.deepStrictEqual(C.parseMilestones(''), { ok: true, body: { milestones: [] } });
  assert.strictEqual(C.parseMilestones('50, 50').ok, false);
  assert.strictEqual(C.parseMilestones('50, x').ok, false);
  assert.strictEqual(C.parseMilestones('80').ok, false);
});

test('fmtRate / fmtEta / fmtDays', () => {
  assert.strictEqual(C.fmtRate(4.125), '4.1 %/h');
  assert.strictEqual(C.fmtRate(0.04), '0.04 %/h');
  assert.strictEqual(C.fmtRate(null), '-');
  assert.strictEqual(C.fmtEta(15 * 3600 + 12 * 60 + 30), '15h12m');
  assert.strictEqual(C.fmtEta(3780), '1h03m');
  assert.strictEqual(C.fmtEta(59), '0m');
  assert.strictEqual(C.fmtEta(125 * 3600), '5d05h');
  assert.strictEqual(C.fmtEta(null), '-');
  assert.strictEqual(C.fmtEta(-5), '0m');
  assert.strictEqual(C.fmtDays([0, 2, 6]), 'Mon Wed Sun');
  assert.strictEqual(C.fmtDays([0, 1, 2, 3, 4, 5, 6]), 'daily');
});

const BODY = {
  now: '2026-10-05T12:00:00+00:00', level: 52, pct: 37.512, rate_pct_h: 4.1, eta_next_s: 54720,
  next_milestone: 56, xp_stack_pct: 150, xp_parts: [{ name: 'Hot Time', pct: 50 }, { name: 'XP scroll', pct: 100 }],
  hot: { active: [{ id: 'h1', label: 'Hot Time', pct: 50, ends_in_s: 3780 }], next: { id: 'h2', label: 'Late', pct: 100, starts_in_s: 7200 } },
  milestones: [50, 56], milestones_seed: false, hot_windows: [], samples: []
};

test('normalizeLeveling keeps a good body and rejects junk', () => {
  const d = C.normalizeLeveling(BODY);
  assert.strictEqual(d.level, 52);
  assert.strictEqual(d.hot.active.length, 1);
  assert.strictEqual(C.normalizeLeveling(null), null);
  assert.strictEqual(C.normalizeLeveling([]), null);
  assert.strictEqual(C.normalizeLeveling({ level: 52 }), null);
  const loose = C.normalizeLeveling({ hot: { active: [null, { id: 'h1', pct: 'x', ends_in_s: 5 }], next: 7 }, samples: 'x', milestones: [] });
  assert.deepStrictEqual(loose.hot.active, []);
  assert.strictEqual(loose.hot.next, null);
  assert.deepStrictEqual(loose.samples, []);
});

test('hotLive counts down from fetch time; an ended window leaves the stack', () => {
  const at = 1000000;
  const h = C.hotLive(BODY.hot, BODY.xp_stack_pct, at, at + 60000);
  assert.strictEqual(h.active[0].ends_in_s, 3720);
  assert.strictEqual(h.next.starts_in_s, 7140);
  assert.strictEqual(h.stack, 150);
  const later = C.hotLive(BODY.hot, BODY.xp_stack_pct, at, at + 3780000);
  assert.deepStrictEqual(later.active, []);
  assert.strictEqual(later.stack, 100);
  const past = C.hotLive(BODY.hot, BODY.xp_stack_pct, at, at + 7300000);
  assert.strictEqual(past.next, null, 'a started next waits for the re-poll');
  assert.strictEqual(past.due, true);
});

test('levelingLine: overlay one-liner', () => {
  const at = 1000000;
  assert.strictEqual(C.levelingLine(BODY, at, at), 'Lv 52 37.5% | 4.1 %/h | ETA 15h12m | HOT 1h03m +150%');
  const noHot = Object.assign({}, BODY, { hot: { active: [], next: BODY.hot.next }, xp_stack_pct: 0 });
  assert.strictEqual(C.levelingLine(noHot, at, at), 'Lv 52 37.5% | 4.1 %/h | ETA 15h12m | HOT in 2h00m');
  const bare = Object.assign({}, BODY, { rate_pct_h: null, eta_next_s: null, hot: { active: [], next: null }, xp_stack_pct: 0 });
  assert.strictEqual(C.levelingLine(bare, at, at), 'Lv 52 37.5% | - %/h | ETA -');
  assert.strictEqual(C.levelingLine(Object.assign({}, bare, { level: null, pct: null }), at, at), 'no XP sample yet');
  // ETA counts down locally
  assert.strictEqual(C.levelingLine(BODY, at, at + 3600000).indexOf('ETA 14h12m') > 0, true);
});

test('grind buff body: optional xp_pct 0-1000', () => {
  assert.strictEqual(C.validGrindBody({ buff: { name: 'XP scroll', minutes: 30, xp_pct: 100 } }), true);
  assert.strictEqual(C.validGrindBody({ buff: { name: 'XP scroll', minutes: 30 } }), true);
  assert.strictEqual(C.validGrindBody({ buff: { name: 'XP scroll', minutes: 30, xp_pct: 1001 } }), false);
  assert.strictEqual(C.validGrindBody({ buff: { name: 'XP scroll', minutes: 30, xp_pct: 1.5 } }), false);
  assert.deepStrictEqual(C.parseGrindForm('buff', { name: 'XP scroll', minutes: '30', xp_pct: '100' }),
    { ok: true, body: { buff: { name: 'XP scroll', minutes: 30, xp_pct: 100 } } });
  assert.deepStrictEqual(C.parseGrindForm('buff', { name: 'XP scroll', minutes: '30', xp_pct: '' }),
    { ok: true, body: { buff: { name: 'XP scroll', minutes: 30 } } });
  assert.strictEqual(C.parseGrindForm('buff', { name: 'XP scroll', minutes: '30', xp_pct: 'x' }).ok, false);
});

test('overlayWidgets: leveling is opt-in (default off, only a literal true turns it on)', () => {
  assert.strictEqual(C.overlayWidgets({}).leveling, false);
  assert.strictEqual(C.overlayWidgets({ overlay: { widgets: { leveling: true } } }).leveling, true);
  assert.strictEqual(C.overlayWidgets({ overlay: { widgets: { leveling: 1 } } }).leveling, false);
  assert.strictEqual(C.widgetsFromQuery('').leveling, false);
  assert.strictEqual(C.widgetsFromQuery('?leveling=1').leveling, true);
  const q = C.widgetsQuery({ leveling: true });
  assert.strictEqual(C.widgetsFromQuery('?' + new URLSearchParams(q).toString()).leveling, true);
  const off = C.widgetsQuery({});
  assert.strictEqual(C.widgetsFromQuery('?' + new URLSearchParams(off).toString()).leveling, false);
  const ex = JSON.parse(fs.readFileSync(path.join(APP, '..', 'config', 'local.example.json'), 'utf8'));
  assert.strictEqual(ex.overlay.widgets.leveling, false);
});

test('leveling card: DOM-built, bridge writes, SSE leveling event, collapsed editor', () => {
  const src = read('dashboard/leveling.js');
  assert.doesNotMatch(src, /innerHTML|outerHTML|insertAdjacentHTML|document\.write/);
  assert.doesNotMatch(src, /method:\s*'POST'/, 'renderer never POSTs directly');
  assert.match(src, /\/api\/leveling/);
  assert.match(src, /EWBus\.on\('leveling'/); // plan 049: via the shared dashboard stream
  assert.match(src, /createElement|el\('details'/);
  assert.match(src, /'details'/);
  assert.doesNotMatch(src, /\.open\s*=\s*true/, 'editor collapsed by default');
  assert.match(src, /seed, verify/);
  assert.match(src, /window\.EWLeveling\s*=/);
  const html = read('dashboard/index.html');
  const iCore = html.indexOf('ewcore.js');
  const iLev = html.indexOf('<script src="leveling.js"></script>');
  const iDash = html.indexOf('dashboard.js');
  assert.ok(iCore >= 0 && iLev > iCore && iDash > iLev, 'script order ewcore, leveling, dashboard');
  const dash = read('dashboard/dashboard.js');
  assert.match(dash, /EWLeveling\.mount\(/);
  assert.match(dash, /EWLeveling\.show\(/);
});

test('overlay: GET-only leveling widget behind its opt-in', () => {
  const src = read('overlay/overlay.js');
  assert.match(src, /\/api\/leveling/);
  assert.doesNotMatch(src, /POST|ewApi|method:/);
  assert.match(src, /W\.leveling/);
  assert.match(src, /C\.levelingLine\(/);
  assert.match(src, /addEventListener\('leveling'/);
  const html = read('overlay/index.html');
  assert.match(html, /id="ov-leveling-row" hidden/);
});

// ---- plan 041: profile level markers ----

test('fmtLevelLine never prints null; profile marker shows (profile)', () => {
  assert.strictEqual(C.fmtLevelLine({ level: 52, pct: 37.512, level_source: 'typed' }), 'Lv 52 37.5%');
  assert.strictEqual(C.fmtLevelLine({ level: 52, pct: 37.512 }, true), 'Lv 52 37.512%');
  assert.strictEqual(C.fmtLevelLine({ level: 61, pct: null, level_source: 'profile' }), 'Lv 61 (profile)');
  assert.strictEqual(C.fmtLevelLine({ level: 61, pct: null }), 'Lv 61');
  assert.strictEqual(C.fmtLevelLine({ level: null, pct: null }), 'no XP sample yet');
  assert.strictEqual(C.fmtLevelLine(null), 'no XP sample yet');
  for (const d of [{ level: 61, pct: null, level_source: 'profile' }, { level: 61, pct: null }, { level: 61 }, {}, null]) {
    assert.doesNotMatch(C.fmtLevelLine(d) + C.fmtLevelLine(d, true), /null|undefined|NaN/);
  }
});

test('normalizeLeveling passes level_source; pct null with profile', () => {
  const d = C.normalizeLeveling(Object.assign({}, BODY, { level: 61, pct: null, level_source: 'profile' }));
  assert.strictEqual(d.level, 61);
  assert.strictEqual(d.pct, null);
  assert.strictEqual(d.level_source, 'profile');
  assert.strictEqual(C.normalizeLeveling(BODY).level_source, null);
  assert.strictEqual(C.normalizeLeveling(Object.assign({}, BODY, { level_source: 'x' })).level_source, null);
});

test('levelingLine: profile marker body keeps rate / ETA / HOT from typed samples', () => {
  const at = 1000000;
  const body = Object.assign({}, BODY, { level: 61, pct: null, level_source: 'profile', eta_next_s: null });
  const line = C.levelingLine(body, at, at);
  assert.ok(line.startsWith('Lv 61 (profile)'), line);
  assert.match(line, /4\.1 %\/h/);
  assert.match(line, /HOT 1h03m/);
  assert.notStrictEqual(line, 'no XP sample yet');
  assert.doesNotMatch(line, /null/);
  assert.strictEqual(C.levelingLine(Object.assign({}, body, { level: null }), at, at), 'no XP sample yet');
});

test('validLevelingBody rejects a null-pct sample (dashboard cannot forge a marker)', () => {
  assert.strictEqual(C.validLevelingBody({ sample: { level: 60, pct: null } }), false);
  assert.strictEqual(C.validLevelingBody({ sample: { level: 60, pct: null, source: 'profile' } }), false);
  assert.strictEqual(C.validLevelingBody({ sample_del: '2026-10-05T12:00:00+00:00' }), true);
});

test('leveling.js renders the level through fmtLevelLine and marks profile samples', () => {
  const src = read('dashboard/leveling.js');
  assert.match(src, /C\.fmtLevelLine\(/);
  assert.doesNotMatch(src, /'Lv ' \+ d\.level \+ '  ' \+ d\.pct/);
  assert.match(src, /\(profile\)/);
});
