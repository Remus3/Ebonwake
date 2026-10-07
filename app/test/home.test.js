'use strict';
// Plan 025: Home / Now tab. composeNow (ewcore.js) folds the existing GET
// payloads into ordered glance cards; eventsThisWeek feeds the Today tab's
// read-only Events card (M8). Static guards on home.js and the tab wiring.
// No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const APP = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(APP, p), 'utf8');
const T0 = Date.parse('2026-10-04T12:00:00Z'); // a Sunday
const H = 3600 * 1000;
const iso = (ms) => new Date(ms).toISOString();

function full() {
  return {
    today: { items: [
      { id: 'black-spirit', title: 'Black Spirit dailies', kind: 'daily', ticked_at: null },
      { id: 'barter', title: 'Barter', kind: 'daily', ticked_at: iso(T0 - H) },
      { id: 'guild', title: 'Guild mission', kind: 'daily', ticked_at: null },
      { id: 'boss', title: 'Boss scroll', kind: 'weekly', ticked_at: null },
      { id: 'sunday', title: 'Sunday shop', kind: 'weekly', ticked_at: null,
        reset: { every: 'week', weekday: 6, at: '00:00' } },
      { id: 'ev-old', title: 'Old event', kind: 'event', until: '2026-10-05' }
    ] },
    grind: {
      active: { spot: 'sp1', started: iso(T0 - 90 * 60000) },
      spots: [{ id: 'sp1', name: 'Olun', silver_per_h: 420000000 }],
      buffs: [
        { id: 'b1', name: 'Value Pack', ends: iso(T0 + 10 * 86400000) },
        { id: 'b2', name: 'XP scroll', ends: iso(T0 + 4 * 60000) },
        { id: 'b3', name: 'Hot Time', ends: null }
      ]
    },
    leveling: { milestones: [61], level: 60, pct: 37.5, rate_pct_h: 4.1, eta_next_s: 15 * 3600,
      hot: { active: [], next: { pct: 50, starts_in_s: 2 * 3600 } } },
    progress: { characters: [] },
    events: {
      items: [
        { id: 'e1', kind: 'coupon', title: 'Weekend coupon', code: 'ABCD-1234', ends: iso(T0 + 47 * H) },
        { id: 'e2', kind: 'event', title: 'Far event', ends: iso(T0 + 72 * H) },
        { id: 'e3', kind: 'event', title: 'Soon event', ends: iso(T0 + 2 * H) }
      ],
      suggested: { status: 'ok', candidates: [
        { code: 'ABCD-1234', title: 'Known', url: 'https://example.com/a' },
        { code: 'NEW-CODE-1', title: 'Fresh coupon', url: 'https://example.com/b' }
      ] }
    },
    market: { items: [
      { id: 1, sid: 0, name: 'Memory Fragment', price: 1500000, below: 2000000, above: null, alert: 'below' },
      { id: 2, sid: 0, name: 'Black Stone', price: 200000, below: 100000, above: null, alert: null }
    ] },
    at: {}
  };
}

const ids = (cards) => cards.map((c) => c.id);
const card = (cards, id) => cards.filter((c) => c.id === id)[0];
// Plan 076: composeNow returns {cards, quiet}; most tests read the cards.
const now = (snap, t) => C.composeNow(snap, t === undefined ? T0 : t).cards;

test('composeNow: card order is fixed (dailies, Timers, then content cards)', () => {
  const out = C.composeNow(full(), T0);
  assert.deepStrictEqual(ids(out.cards),
    ['dailies', 'timers', 'buffs', 'session', 'leveling', 'alerts', 'coupons']);
  assert.deepStrictEqual(out.quiet, []);
  out.cards.forEach((c) => {
    assert.strictEqual(typeof c.title, 'string');
    assert.ok(Array.isArray(c.rows), c.id);
    assert.strictEqual(typeof c.tab, 'string', c.id);
  });
});

test('composeNow: Timers merges resets, custom rules and events ending, sorted by due', () => {
  const r = card(now(full()), 'timers');
  assert.strictEqual(r.title, 'Timers');
  assert.deepStrictEqual(r.rows.map((x) => x.label),
    ['Soon event', 'Daily reset', 'Weekend coupon', 'Weekly reset', 'Sunday shop']);
  assert.strictEqual(r.rows[1].value, C.fmtDuration(C.nextDailyReset(T0) - T0));
  assert.strictEqual(r.rows[3].value, C.fmtDuration(C.nextWeeklyReset(T0) - T0));
  assert.strictEqual(r.rows[4].note, 'Sun 00:00 UTC');
  assert.strictEqual(r.rows[4].value, C.fmtDuration(6 * 86400000 + 12 * H)); // next Sun 00:00
  assert.strictEqual(r.rows[2].note, 'ABCD-1234');
  assert.deepStrictEqual(r.rows.map((x) => x.source), ['event', 'reset', 'coupon', 'reset', 'reset']);
});

// Plan 076 fixture: a boss and the daily reset both 3 min out, What now
// already lists both -> Timers drops them; the weekly reset row remains.
const T3 = Date.parse('2026-10-04T23:57:00Z');
const isoP = (ms) => new Date(ms).toISOString().replace('.000Z', '+00:00');
function bossView(spawns) {
  return { now: isoP(T3), tz: 'PT', today: { day: '2026-10-04', remaining: [], slots: [] }, looted: {},
    garmoth: { looted: 1, cap: 3, week_reset: '2026-10-08T00:00:00+00:00' },
    next: spawns.map((s) => ({ bosses: s[0], at_utc: isoP(s[1]), at_pt: '', day: '2026-10-04', despawn_min: 30 })) };
}
const RESET = Date.parse('2026-10-05T00:00:00Z');
function wnBoth() {
  return { top: { text: 'Kzarka spawns', why: 'world boss', due: isoP(RESET), source: 'boss' },
    next: [{ text: 'Finish 5 dailies before reset', why: 'A, B, C', due: isoP(RESET), source: 'reset' }],
    empty: false, empty_text: 'All clear - play', errors: [] };
}

test('nowTimers: a row What now already shows (source + due minute) is dropped', () => {
  const snap = { at: {}, today: { items: [] }, bosses: bossView([[['Kzarka'], RESET], [['Nouver'], RESET + 5 * H]]) };
  const plain = card(now(snap, T3), 'timers');
  assert.deepStrictEqual(plain.rows.map((x) => x.label), ['Daily reset', 'Kzarka', 'Nouver', 'Weekly reset'], 'a due tie keeps resets, bosses, events order');
  snap.whatnow = wnBoth();
  const out = C.composeNow(snap, T3);
  const t = card(out.cards, 'timers');
  assert.deepStrictEqual(t.rows.map((x) => x.label), ['Nouver', 'Weekly reset']);
  assert.strictEqual(out.cards[0].id, 'whatnow');
  // An undated or other-source action never drops a Timers row.
  snap.whatnow.next = [{ text: 'Re-arm XP scroll', why: '', due: isoP(RESET), source: 'buff' }];
  snap.whatnow.top.due = null;
  assert.strictEqual(card(now(snap, T3), 'timers').rows.length, 4);
});

test('nowTimers: next 3 bosses, Garmoth count only as the meta, max 6 rows', () => {
  const spawns = [[['Kzarka'], RESET + H], [['Garmoth'], RESET + 2 * H], [['Nouver'], RESET + 3 * H],
    [['Kutum'], RESET + 4 * H]];
  const snap = { at: {}, bosses: bossView(spawns), events: { items: [
    { id: 'e1', kind: 'event', title: 'E1', ends: iso(T3 + 10 * 60000) },
    { id: 'e2', kind: 'event', title: 'E2', ends: iso(T3 + 20 * 60000) },
    { id: 'e3', kind: 'event', title: 'E3', ends: iso(T3 + 30 * 60000) }
  ] } };
  const t = card(now(snap, T3), 'timers');
  assert.strictEqual(t.meta, 'Garmoth 1/3 this week');
  assert.strictEqual(t.rows.length, 6);
  assert.deepStrictEqual(t.rows.map((x) => x.label), ['Daily reset', 'E1', 'E2', 'E3', 'Kzarka', 'Garmoth']);
  assert.ok(t.rows.every((x) => x.tick === null), 'Home stays read-only for bosses');
  assert.ok(t.rows.every((x) => !/\d\/\d/.test(x.label)), 'no n/3 in a row');
});

test('composeNow: dailies left by name, ticks carry the item id', () => {
  const d = card(now(full()), 'dailies');
  assert.deepStrictEqual(d.rows.map((x) => x.label), ['Black Spirit dailies', 'Guild mission']);
  assert.deepStrictEqual(d.rows.map((x) => x.tick), ['black-spirit', 'guild']);
  assert.strictEqual(d.meta, '1/3 done');
});

test('composeNow: buffs soonest first with countdowns, unarmed dropped', () => {
  const b = card(now(full()), 'buffs');
  assert.deepStrictEqual(b.rows.map((x) => x.label), ['XP scroll', 'Value Pack']);
  assert.strictEqual(b.rows[0].value, '4m 00s');
  assert.strictEqual(b.rows[0].cls, 'warn');
  assert.strictEqual(b.rows[1].value, '10d 0h');
});

test('composeNow: running session shows spot, elapsed and the spot silver/h', () => {
  const s = card(now(full()), 'session');
  assert.strictEqual(s.rows[0].label, 'Olun');
  assert.strictEqual(s.rows[0].value, '1:30:00');
  assert.strictEqual(s.rows[1].value, C.fmtSilver(420000000) + '/h');
});

test('composeNow: level ETA and next Hot Time, ETA ticks from the fetch time', () => {
  const snap = full();
  snap.at.leveling = T0 - 3600 * 1000;
  const l = card(now(snap), 'leveling');
  assert.strictEqual(l.rows[0].label, 'Lv 60 37.5%');
  assert.strictEqual(l.rows[0].value, 'ETA 14h00m');
  assert.strictEqual(l.rows[1].label, 'Next Hot Time +50%');
  assert.strictEqual(l.rows[1].value, 'in 1h00m');
});

test('composeNow: plan 024 deadline late/tight shows in the Level ETA card (merge 025)', () => {
  const snap = full();
  snap.leveling.deadlines = [{ id: 'olvia', label: 'Olvia Academy', needs_level: 60,
    enrol_by_utc: '2026-11-05T00:00:00Z', state: 'late', reach_utc: null, margin_h: null }];
  const l = card(now(snap), 'leveling');
  const r = l.rows[l.rows.length - 1];
  assert.strictEqual(r.label, 'Deadline');
  assert.strictEqual(r.value, 'Olvia Academy Lv 60 late');
  assert.strictEqual(r.cls, 'bad');
  snap.leveling.deadlines[0].state = 'on_track';
  assert.strictEqual(card(now(snap), 'leveling').rows.length, 2);
});

test('composeNow: market alert hits only', () => {
  const a = card(now(full()), 'alerts');
  assert.strictEqual(a.rows.length, 1);
  assert.strictEqual(a.rows[0].label, 'Memory Fragment');
  assert.strictEqual(a.rows[0].value, C.fmtSilver(1500000));
  assert.strictEqual(a.rows[0].note, 'below ' + C.fmtSilver(2000000));
});

test('composeNow: events ending within 48 h fold into Timers; new coupons only', () => {
  const cards = now(full());
  assert.ok(!card(cards, 'ending'), 'no separate ending card');
  const e = card(cards, 'timers').rows.filter((x) => x.source !== 'reset');
  assert.deepStrictEqual(e.map((x) => x.label), ['Soon event', 'Weekend coupon']);
  assert.strictEqual(e[1].cls, 'warn');
  const c = card(cards, 'coupons');
  assert.deepStrictEqual(c.rows.map((x) => x.label), ['NEW-CODE-1']);
  assert.strictEqual(c.rows[0].note, 'Fresh coupon');
});

test('composeNow: 48 h window edges (exactly 48 h in, 48 h + 1 s out, ended out)', () => {
  const snap = full();
  snap.events.items = [
    { id: 'e1', kind: 'event', title: 'Edge in', ends: iso(T0 + 48 * H) },
    { id: 'e2', kind: 'event', title: 'Edge out', ends: iso(T0 + 48 * H + 1000) },
    { id: 'e3', kind: 'event', title: 'Ended', ends: iso(T0 - 1000) },
    { id: 'e4', kind: 'event', title: 'Done', ends: iso(T0 + H), done: true },
    { id: 'e5', kind: 'event', title: 'No end' }
  ];
  const e = card(now(snap), 'timers').rows.filter((x) => x.source !== 'reset');
  assert.deepStrictEqual(e.map((x) => x.label), ['Edge in']);
});

const CLEAR = { top: null, next: [], empty: true, empty_text: 'All clear - play', errors: [] };

test('composeNow: empty cards move to one quiet list; no card has zero rows but What now', () => {
  const snap = {
    today: { items: [] }, grind: { active: null, spots: [], buffs: [] },
    leveling: { milestones: [], level: null, pct: null, hot: {} },
    events: { items: [], suggested: { status: 'ok', candidates: [] } }, market: { items: [] },
    summary: { session: { empty: true } }, whatnow: CLEAR
  };
  const out = C.composeNow(snap, T0);
  // Timers always carries the daily + weekly reset rows (as-built deviation 1).
  assert.deepStrictEqual(ids(out.cards), ['whatnow', 'timers']);
  assert.strictEqual(out.cards[0].empty, 'All clear - play');
  out.cards.slice(1).forEach((c) => assert.ok(c.rows.length > 0, c.id));
  assert.deepStrictEqual(out.quiet.map((q) => q.title),
    ['dailies', 'buffs', 'grind', 'level ETA', 'alerts', 'coupons', 'last session']);
  assert.deepStrictEqual(out.quiet.map((q) => q.tab),
    ['today', 'grind', 'grind', 'progress', 'market', 'events', 'grind']);
  // Dailies with rows stay a card (the plan 076 live fixture: 5 dailies open).
  snap.today.items = [{ id: 'a', title: 'A', kind: 'daily', ticked_at: null }];
  assert.deepStrictEqual(ids(now(snap)), ['whatnow', 'dailies', 'timers']);
  const all = { today: { items: [{ id: 'a', title: 'A', kind: 'daily', ticked_at: iso(T0) }] } };
  assert.deepStrictEqual(C.composeNow(all, T0).quiet, [{ title: 'dailies', tab: 'today' }]);
});

test('composeNow: order is What now, Get started, Dailies, Timers, content, Last session last', () => {
  const snap = full();
  snap.whatnow = { top: { text: 'Kzarka spawns', why: '', due: null, source: 'boss' }, next: [], empty: false };
  snap.onboarding = { show: true, done: 0, total: 1, steps: [{ id: 'bdo', title: 'Point EW at BDO', done: false,
    link: { tab: 'system' } }] };
  snap.summary = { session: { since: '', until: '', empty: false, grind: { sessions: 1, minutes: 30, silver: 5,
    silver_per_h: 10, spots: [] }, xp: null, buffs: [], dailies: [], events: [] } };
  assert.deepStrictEqual(ids(now(snap)), ['whatnow', 'onboarding', 'dailies', 'timers', 'buffs', 'session',
    'leveling', 'alerts', 'coupons', 'summary']);
  snap.whatnow = CLEAR;  // 069 deviation 4: Get started leads an all-clear What now
  assert.deepStrictEqual(ids(now(snap)).slice(0, 3), ['onboarding', 'whatnow', 'dailies']);
});

test('composeNow: a missing payload drops its card, never the screen', () => {
  const snap = full();
  delete snap.grind;
  snap.market = null;
  snap.events.suggested = undefined; // older server: no plan 014 block
  assert.deepStrictEqual(ids(now(snap)), ['dailies', 'timers', 'leveling']);
  assert.deepStrictEqual(C.composeNow({}, T0), { cards: C.composeNow({}, T0).cards, quiet: [] });
  assert.deepStrictEqual(ids(now({})), ['timers']);
  assert.deepStrictEqual(ids(now(null)), ['timers']);
  const junk = { today: 'x', grind: [], leveling: { nope: 1 }, events: 5, market: { items: 'no' } };
  assert.deepStrictEqual(ids(now(junk)), ['timers']);
});

test('home collapse: key, toggle round trip, corrupt storage -> default (collapsed)', () => {
  const k = C.collapsedKey('progress', 'mounts');
  assert.strictEqual(k, 'progress/mounts');
  const open = C.toggleCollapsed([], k);
  assert.deepStrictEqual(open, [k]);
  assert.strictEqual(C.isCollapsed(open, k), false);
  const shut = C.toggleCollapsed(open, k);
  assert.deepStrictEqual(shut, []);
  assert.deepStrictEqual(open, [k], 'input not mutated');
  assert.strictEqual(C.isCollapsed(shut, k), true, 'default collapsed');
  assert.deepStrictEqual(C.parseCollapsed(JSON.stringify([k, 'home/pets'])), [k, 'home/pets']);
  for (const raw of ['{x', 'null', '"progress/mounts"', '{"a":1}', null, undefined, '[1, {}]']) {
    assert.deepStrictEqual(C.parseCollapsed(raw), [], String(raw));
  }
  assert.deepStrictEqual(C.parseCollapsed('["progress/mounts", 5, "../x y"]'), ['progress/mounts']);
  assert.deepStrictEqual(C.parseCollapsed(C.serializeCollapsed([k, 'home/pets'])), [k, 'home/pets']);
  assert.strictEqual(C.serializeCollapsed([k, 5, '../x y', k]), JSON.stringify([k]));
  assert.strictEqual(C.serializeCollapsed(null), '[]');
});

test('composeNow: does not mutate its input', () => {
  const snap = full();
  const before = JSON.stringify(snap);
  C.composeNow(snap, T0);
  assert.strictEqual(JSON.stringify(snap), before);
});

test('eventsThisWeek: open Events-tab items ending before the weekly reset', () => {
  const items = [
    { id: 'e1', kind: 'event', title: 'Before reset', ends: '2026-10-07T23:00:00Z' },
    { id: 'e2', kind: 'event', title: 'At reset', ends: '2026-10-08T00:00:00Z' },
    { id: 'e3', kind: 'event', title: 'After reset', ends: '2026-10-08T00:00:01Z' },
    { id: 'e4', kind: 'event', title: 'Done', ends: '2026-10-06T00:00:00Z', done: true },
    { id: 'e5', kind: 'event', title: 'Ended', ends: '2026-10-04T11:00:00Z' },
    { id: 'e6', kind: 'event', title: 'No end' }
  ];
  const rows = C.eventsThisWeek(items, T0, T0);
  assert.deepStrictEqual(rows.map((r) => r.title), ['Before reset', 'At reset']);
  assert.deepStrictEqual(C.eventsThisWeek(null, T0, T0), []);
});

test('Home is the first tab on the server and loaded by the dashboard', () => {
  const app = fs.readFileSync(path.join(APP, '..', 'server', 'ew', 'app.py'), 'utf8');
  assert.match(app, /TABS = \[\s*\{"id": "home", "title": "Home"/);
  const html = read('dashboard/index.html');
  assert.ok(html.indexOf('home.js') > 0 && html.indexOf('home.js') < html.indexOf('dashboard.js'));
  assert.match(read('dashboard/dashboard.js'), /EWHome/);
});

test('home.js is read-only apart from daily ticks, the plan 051 dismiss and the plan 074 ack', () => {
  const src = read('dashboard/home.js');
  assert.doesNotMatch(src, /innerHTML|insertAdjacentHTML|eval\(/);
  const posts = src.match(/\.post\(\s*'[^']+'/g) || [];
  assert.deepStrictEqual(posts.map((p) => p.replace(/\s+/g, '')),
    [".post('/api/today'", ".post('/api/maint/digest'", ".post('/api/onboarding'"]);
  assert.match(src, /\{ ack: key \}/);
  assert.match(src, /\{ tick: /);
  assert.doesNotMatch(src, /untick|remove:|add:/);
  ['/api/today', '/api/grind', '/api/leveling', '/api/events', '/api/market/watch'].forEach((p) => {
    assert.ok(src.indexOf("'" + p + "'") >= 0, p);
  });
});

test('plan 076: home.js wide What now rows, labelled tick / open, one quiet line', () => {
  const src = read('dashboard/home.js');
  assert.match(src, /ew-wide/);
  assert.match(src, /el\('button', 'ew-btn ew-htick', 'tick'\)/);
  assert.match(src, /setAttribute\('aria-label', 'tick ' \+ r\.label\)/);
  assert.match(src, /setAttribute\('aria-label', 'open ' \+ c\.tab\)/);
  assert.match(src, /'Quiet: '/);
  assert.match(src, /\.quiet/);
  assert.doesNotMatch(src, /'done'\)/, 'tick button no longer reads done');
  const css = read('shared/ew.css');
  assert.match(css, /\.ew-wide\s*\{[^}]*grid-column:\s*1 \/ -1/);
  assert.match(css, /\.ew-wnrow/);
  assert.match(css, /\.ew-collapsed > \.ew-cbody\s*\{[^}]*display:\s*none/);
  // the What now text never ellipsizes
  const wn = css.split('\n').filter((l) => /ew-wn/.test(l)).join('\n');
  assert.doesNotMatch(wn, /text-overflow/);
});

test('plan 076: Progress Mounts / Pets / Inventory / Life & CP cards are collapsible', () => {
  const want = { 'dashboard/progress.js': ['mounts', 'life'], 'dashboard/pets.js': ['pets'],
    'dashboard/inventory.js': ['inventory'] };
  Object.keys(want).forEach((f) => {
    want[f].forEach((id) => assert.match(read(f), new RegExp("dataset\\.collapse = '" + id + "'"), f + ' ' + id));
  });
  const d = read('dashboard/dashboard.js');
  assert.match(d, /C\.toggleCollapsed\(/);
  assert.match(d, /C\.parseCollapsed\(/);
  assert.match(d, /localStorage/);
  assert.match(d, /aria-expanded/);
  // Mounts prose is normal text, not a number cell.
  assert.doesNotMatch(read('dashboard/progress.js'), /ui\.fern = el\('div', 'ew-num'/);
});

test('Today: Events card shows Events-tab items read-only, no new event adds', () => {
  const src = read('dashboard/today.js');
  assert.match(src, /eventsThisWeek/);
  assert.match(src, /'\/api\/events'/);
  assert.doesNotMatch(src, /\['daily', 'weekly', 'event'\]\.forEach/);
});
