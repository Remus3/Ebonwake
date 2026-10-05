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

test('composeNow: card order is fixed (resets first, coupons last)', () => {
  const cards = C.composeNow(full(), T0);
  assert.deepStrictEqual(ids(cards),
    ['resets', 'dailies', 'buffs', 'session', 'leveling', 'alerts', 'ending', 'coupons']);
  cards.forEach((c) => {
    assert.strictEqual(typeof c.title, 'string');
    assert.ok(Array.isArray(c.rows), c.id);
    assert.strictEqual(typeof c.tab, 'string', c.id);
  });
});

test('composeNow: resets list daily, weekly, then custom rules soonest first', () => {
  const r = card(C.composeNow(full(), T0), 'resets');
  assert.strictEqual(r.rows[0].label, 'Daily reset');
  assert.strictEqual(r.rows[0].value, C.fmtDuration(C.nextDailyReset(T0) - T0));
  assert.strictEqual(r.rows[1].label, 'Weekly reset');
  assert.strictEqual(r.rows[1].value, C.fmtDuration(C.nextWeeklyReset(T0) - T0));
  assert.strictEqual(r.rows.length, 3);
  assert.strictEqual(r.rows[2].label, 'Sunday shop');
  assert.strictEqual(r.rows[2].note, 'Sun 00:00 UTC');
  assert.strictEqual(r.rows[2].value, C.fmtDuration(6 * 86400000 + 12 * H)); // next Sun 00:00
});

test('composeNow: dailies left by name, ticks carry the item id', () => {
  const d = card(C.composeNow(full(), T0), 'dailies');
  assert.deepStrictEqual(d.rows.map((x) => x.label), ['Black Spirit dailies', 'Guild mission']);
  assert.deepStrictEqual(d.rows.map((x) => x.tick), ['black-spirit', 'guild']);
  assert.strictEqual(d.meta, '1/3 done');
});

test('composeNow: buffs soonest first with countdowns, unarmed dropped', () => {
  const b = card(C.composeNow(full(), T0), 'buffs');
  assert.deepStrictEqual(b.rows.map((x) => x.label), ['XP scroll', 'Value Pack']);
  assert.strictEqual(b.rows[0].value, '4m 00s');
  assert.strictEqual(b.rows[0].cls, 'warn');
  assert.strictEqual(b.rows[1].value, '10d 0h');
});

test('composeNow: running session shows spot, elapsed and the spot silver/h', () => {
  const s = card(C.composeNow(full(), T0), 'session');
  assert.strictEqual(s.rows[0].label, 'Olun');
  assert.strictEqual(s.rows[0].value, '1:30:00');
  assert.strictEqual(s.rows[1].value, C.fmtSilver(420000000) + '/h');
});

test('composeNow: level ETA and next Hot Time, ETA ticks from the fetch time', () => {
  const snap = full();
  snap.at.leveling = T0 - 3600 * 1000;
  const l = card(C.composeNow(snap, T0), 'leveling');
  assert.strictEqual(l.rows[0].label, 'Lv 60 37.5%');
  assert.strictEqual(l.rows[0].value, 'ETA 14h00m');
  assert.strictEqual(l.rows[1].label, 'Next Hot Time +50%');
  assert.strictEqual(l.rows[1].value, 'in 1h00m');
});

test('composeNow: plan 024 deadline late/tight shows in the Level ETA card (merge 025)', () => {
  const snap = full();
  snap.leveling.deadlines = [{ id: 'olvia', label: 'Olvia Academy', needs_level: 60,
    enrol_by_utc: '2026-11-05T00:00:00Z', state: 'late', reach_utc: null, margin_h: null }];
  const l = card(C.composeNow(snap, T0), 'leveling');
  const r = l.rows[l.rows.length - 1];
  assert.strictEqual(r.label, 'Deadline');
  assert.strictEqual(r.value, 'Olvia Academy Lv 60 late');
  assert.strictEqual(r.cls, 'bad');
  snap.leveling.deadlines[0].state = 'on_track';
  assert.strictEqual(card(C.composeNow(snap, T0), 'leveling').rows.length, 2);
});

test('composeNow: market alert hits only', () => {
  const a = card(C.composeNow(full(), T0), 'alerts');
  assert.strictEqual(a.rows.length, 1);
  assert.strictEqual(a.rows[0].label, 'Memory Fragment');
  assert.strictEqual(a.rows[0].value, C.fmtSilver(1500000));
  assert.strictEqual(a.rows[0].note, 'below ' + C.fmtSilver(2000000));
});

test('composeNow: events ending within 48 h, soonest first; new coupons only', () => {
  const cards = C.composeNow(full(), T0);
  const e = card(cards, 'ending');
  assert.deepStrictEqual(e.rows.map((x) => x.label), ['Soon event', 'Weekend coupon']);
  assert.strictEqual(e.rows[1].note, 'ABCD-1234');
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
  const e = card(C.composeNow(snap, T0), 'ending');
  assert.deepStrictEqual(e.rows.map((x) => x.label), ['Edge in']);
});

test('composeNow: empty states keep the card with a message', () => {
  const snap = {
    today: { items: [] }, grind: { active: null, spots: [], buffs: [] },
    leveling: { milestones: [], level: null, pct: null, hot: {} },
    events: { items: [], suggested: { status: 'ok', candidates: [] } }, market: { items: [] }
  };
  const cards = C.composeNow(snap, T0);
  assert.deepStrictEqual(ids(cards),
    ['resets', 'dailies', 'buffs', 'session', 'leveling', 'alerts', 'ending', 'coupons']);
  cards.slice(1).forEach((c) => {
    assert.strictEqual(c.rows.length, 0, c.id);
    assert.ok(typeof c.empty === 'string' && c.empty.length > 0, c.id);
  });
  const all = { today: { items: [{ id: 'a', title: 'A', kind: 'daily', ticked_at: iso(T0) }] } };
  assert.match(card(C.composeNow(all, T0), 'dailies').empty, /all 1 dailies done/);
});

test('composeNow: a missing payload drops its card, never the screen', () => {
  const snap = full();
  delete snap.grind;
  snap.market = null;
  snap.events.suggested = undefined; // older server: no plan 014 block
  const cards = C.composeNow(snap, T0);
  assert.deepStrictEqual(ids(cards), ['resets', 'dailies', 'leveling', 'ending']);
  assert.deepStrictEqual(ids(C.composeNow({}, T0)), ['resets']);
  assert.deepStrictEqual(ids(C.composeNow(null, T0)), ['resets']);
  const junk = { today: 'x', grind: [], leveling: { nope: 1 }, events: 5, market: { items: 'no' } };
  assert.deepStrictEqual(ids(C.composeNow(junk, T0)), ['resets']);
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

test('home.js is read-only apart from daily ticks through the bridge', () => {
  const src = read('dashboard/home.js');
  assert.doesNotMatch(src, /innerHTML|insertAdjacentHTML|eval\(/);
  const posts = src.match(/\.post\(\s*'[^']+'/g) || [];
  assert.deepStrictEqual(posts.map((p) => p.replace(/\s+/g, '')), [".post('/api/today'"]);
  assert.match(src, /\{ tick: /);
  assert.doesNotMatch(src, /untick|remove:|add:/);
  ['/api/today', '/api/grind', '/api/leveling', '/api/events', '/api/market/watch'].forEach((p) => {
    assert.ok(src.indexOf("'" + p + "'") >= 0, p);
  });
});

test('Today: Events card shows Events-tab items read-only, no new event adds', () => {
  const src = read('dashboard/today.js');
  assert.match(src, /eventsThisWeek/);
  assert.match(src, /'\/api\/events'/);
  assert.doesNotMatch(src, /\['daily', 'weekly', 'event'\]\.forEach/);
});
