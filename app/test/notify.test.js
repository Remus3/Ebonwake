'use strict';
// Plan 026: toast queue, notify rule engine, prefs, OS-notification guards.
const test = require('node:test');
const assert = require('node:assert');
const C = require('../shared/ewcore');

const E_ACUTE = String.fromCharCode(0xe9);
const T0 = Date.UTC(2026, 9, 5, 12, 0, 0); // Monday 12:00 UTC
const ALL_ON = { marketAlert: true, buffEnding: true, hotTime: true, resetPassed: true, newCoupon: true, gameExit: true };

function snap(o) { return Object.assign({ at: T0 }, o); }
function names(hits) { return hits.map((h) => h.rule); }

// ---- toast queue ----

test('toastQueue: push, max 4 (oldest dropped), dedupe by key, expire', () => {
  const q = C.toastQueue();
  for (let i = 0; i < 5; i++) q.push({ key: 'k' + i, level: 'ok', text: 't' + i }, T0);
  assert.deepStrictEqual(q.items().map((t) => t.key), ['k1', 'k2', 'k3', 'k4']);
  q.push({ key: 'k2', level: 'bad', text: 'again' }, T0 + 1000);
  const it = q.items();
  assert.strictEqual(it.length, 4);
  assert.deepStrictEqual(it[it.length - 1], { key: 'k2', level: 'bad', text: 'again', until: T0 + 1000 + C.TOAST_MS.bad });
  assert.strictEqual(q.expire(T0 + C.TOAST_MS.ok - 1), false);
  assert.strictEqual(q.expire(T0 + C.TOAST_MS.ok), true);
  assert.deepStrictEqual(q.items().map((t) => t.key), ['k2']);
  q.remove('k2');
  assert.strictEqual(q.items().length, 0);
});

test('toastQueue: unknown level -> warn, text clipped, keyless toasts never collide', () => {
  const q = C.toastQueue();
  q.push({ level: 'loud', text: 'x'.repeat(500) }, T0);
  q.push({ text: 'b' }, T0);
  const it = q.items();
  assert.strictEqual(it.length, 2);
  assert.strictEqual(it[0].level, 'warn');
  assert.strictEqual(it[0].text.length, C.TOAST_TEXT_MAX);
  assert.notStrictEqual(it[0].key, it[1].key);
  q.push(null, T0);
  assert.strictEqual(q.items().length, 2);
});

test('postToast: every POST result -> one toast (ok / 404 warn / bad)', () => {
  assert.deepStrictEqual(C.postToast('/api/today', { ok: true, data: {} }),
    { key: 'post:/api/today', level: 'ok', text: 'Today saved' });
  const nf = C.postToast('/api/grind', { ok: false, status: 404, error: 'HTTP 404' });
  assert.strictEqual(nf.level, 'warn');
  assert.strictEqual(nf.text, 'Grind: ' + C.notOnServer('/api/grind'));
  assert.deepStrictEqual(C.postToast('/api/market/watch', { ok: false, error: 'invalid request' }),
    { key: 'post:/api/market/watch', level: 'bad', text: 'Market watch failed: invalid request' });
  assert.strictEqual(C.postToast('/api/events', null).text, 'Events failed: unknown error');
  for (const r of C.POST_ROUTES) assert.ok(C.postToast(r, { ok: true }).text.indexOf('/') < 0, r);
});

// ---- rule table + prefs ----

test('NOTIFY_RULES: stable names, marketAlert + buffEnding on by default', () => {
  assert.deepStrictEqual(C.NOTIFY_RULES.map((r) => r.name),
    ['marketAlert', 'buffEnding', 'hotTime', 'resetPassed', 'newCoupon', 'gameExit', 'bossSoon', 'resetSoon']);
  assert.deepStrictEqual(C.notifyPrefs({}), { marketAlert: true, buffEnding: true, hotTime: false,
    resetPassed: false, newCoupon: false, gameExit: false, bossSoon: false, resetSoon: false });
  const p = C.notifyPrefs({ notify: { gameExit: true, buffEnding: false, hotTime: 'yes', bogus: true } });
  assert.strictEqual(p.gameExit, true);
  assert.strictEqual(p.buffEnding, false);
  assert.strictEqual(p.hotTime, false);
  assert.ok(!('bogus' in p));
});

test('notifySilent: default true, boolean override only', () => {
  assert.strictEqual(C.notifySilent({}), true);
  assert.strictEqual(C.notifySilent({ notify: { silent: false } }), false);
  assert.strictEqual(C.notifySilent({ notify: { silent: 'no' } }), true);
  assert.ok(!('silent' in C.notifyPrefs({ notify: { silent: false } })), 'silent is not a rule');
});

test('notifyPrefs round-trips through the launch argument', () => {
  const p = C.notifyPrefs({ notify: { gameExit: true } });
  const arg = C.notifyArg(p);
  assert.strictEqual(arg, 'marketAlert,buffEnding,gameExit');
  assert.deepStrictEqual(C.notifyPrefsFromArg(arg), p);
  assert.deepStrictEqual(C.notifyPrefsFromArg(''), C.notifyPrefsFromArg('x,y'));
  assert.strictEqual(C.notifyPrefsFromArg('').marketAlert, false);
  assert.deepStrictEqual(C.notifyPrefsFromArg(null), C.notifyPrefs({}), 'no arg -> defaults');
});

test('a later plan registers a rule without editing the engine', () => {
  const rule = { name: 'testRule', defaultOn: true, fire: (prev, next) => (next.x ? [{ key: 'testRule:1', title: 'T', body: 'B' }] : []) };
  C.NOTIFY_RULES.push(rule);
  try {
    assert.strictEqual(C.notifyPrefs({}).testRule, true);
    assert.deepStrictEqual(C.notifyRules(null, snap({ x: 1 }), T0, C.notifyPrefs({})),
      [{ key: 'testRule:1', rule: 'testRule', title: 'T', body: 'B' }]);
  } finally {
    C.NOTIFY_RULES.pop();
  }
});

test('notifyRules: prefs off silences a rule; a throwing rule never breaks the rest', () => {
  const prev = snap({ market: { items: [{ id: 1, sid: 0, name: 'Ore', price: 90, below: 100, alert: null }] } });
  const next = snap({ at: T0 + 60000, market: { items: [{ id: 1, sid: 0, name: 'Ore', price: 90, below: 100, alert: 'below' }] } });
  assert.deepStrictEqual(names(C.notifyRules(prev, next, T0 + 60000, ALL_ON)), ['marketAlert']);
  assert.deepStrictEqual(C.notifyRules(prev, next, T0 + 60000, Object.assign({}, ALL_ON, { marketAlert: false })), []);
  C.NOTIFY_RULES.push({ name: 'boom', defaultOn: true, fire: () => { throw new Error('x'); } });
  try {
    assert.deepStrictEqual(names(C.notifyRules(prev, next, T0 + 60000, Object.assign({ boom: true }, ALL_ON))), ['marketAlert']);
  } finally {
    C.NOTIFY_RULES.pop();
  }
});

// ---- each rule ----

test('marketAlert: fires on the crossing only, per item and direction', () => {
  const row = (alert, price) => ({ id: 4901, sid: 0, name: 'Black Stone', price: price, below: 100000, above: 300000, alert: alert });
  const a = snap({ market: { items: [row(null, 150000)] } });
  const b = snap({ market: { items: [row('below', 95000)] } });
  const hits = C.notifyRules(a, b, T0, ALL_ON);
  assert.deepStrictEqual(hits, [{ key: 'marketAlert:4901:0:below', rule: 'marketAlert',
    title: 'Market alert: Black Stone', body: 'Black Stone at 95K, at or below 100K' }]);
  assert.deepStrictEqual(C.notifyRules(b, b, T0, ALL_ON), [], 'still below: no repeat');
  assert.deepStrictEqual(C.notifyRules(null, b, T0, ALL_ON), [], 'first look is a baseline');
  const up = C.notifyRules(a, snap({ market: { items: [row('above', 310000)] } }), T0, ALL_ON);
  assert.strictEqual(up[0].body, 'Black Stone at 310K, at or above 300K');
  assert.deepStrictEqual(C.notifyRules(a, snap({ market: null }), T0, ALL_ON), []);
});

test('marketAlert: plan 052 below_p20 crossing fires once', () => {
  const row = (alert, price) => ({ id: 4901, sid: 0, name: 'Black Stone', price: price, below: null, above: null,
    p20: true, bands: { p20: 200000, p50: 220000, p80: 240000 }, alert: alert });
  const a = snap({ market: { items: [row(null, 210000)] } });
  const b = snap({ market: { items: [row('below_p20', 190000)] } });
  assert.deepStrictEqual(C.notifyRules(a, b, T0, ALL_ON), [{ key: 'marketAlert:4901:0:below_p20', rule: 'marketAlert',
    title: 'Market alert: Black Stone', body: 'Black Stone at 190K, under its 90-day p20 200K' }]);
  assert.deepStrictEqual(C.notifyRules(b, b, T0, ALL_ON), []);
});

test('buffEnding: armed buff on the 15/5/1 min ladder (plan 070), key per arming and step', () => {
  const ends = new Date(T0 + 4 * 60000).toISOString();
  const g = { buffs: [{ id: 3, name: 'XP scroll', ends: ends }, { id: 4, name: 'Value Pack', ends: new Date(T0 + 3600000).toISOString() }] };
  const hits = C.notifyRules(null, snap({ grind: g, grindAt: T0 }), T0, ALL_ON);
  assert.deepStrictEqual(hits, [{ key: 'buffEnding:3:' + ends + ':5', rule: 'buffEnding', title: 'Buff ending: XP scroll',
    body: 'XP scroll ends in 4m 00s', ladder: true }]);
  assert.deepStrictEqual(C.notifyRules(null, snap({ grind: g, grindAt: T0 }), T0 + 4 * 60000, ALL_ON), [], 'expired: quiet');
  assert.deepStrictEqual(C.notifyRules(null, snap({ grind: g, grindAt: T0 }), T0 - 12 * 60000, ALL_ON), [], '16 min left: quiet');
  assert.deepStrictEqual(C.notifyRules(null, snap({ grind: g, grindAt: T0 }), T0 - 2 * 60000, ALL_ON).map((h) => h.key),
    ['buffEnding:3:' + ends + ':15'], '6 min left: the 15 min step');
  assert.deepStrictEqual(C.notifyRules(null, snap({ grind: g, grindAt: T0 }), T0 + 3 * 60000, ALL_ON).map((h) => h.key),
    ['buffEnding:3:' + ends + ':1']);
  const rel = C.notifyRules(null, snap({ grind: { buffs: [{ name: 'Hot Time', left_s: 120 }] }, grindAt: T0 }), T0, ALL_ON);
  assert.strictEqual(rel.length, 1);
  assert.match(rel[0].key, /^buffEnding:Hot Time:/);
});

test('hotTime: a window that is active now but was not before', () => {
  const idle = { hot: { active: [], next: { id: 'h1', label: 'Evening', pct: 50, starts_in_s: 30 } } };
  const live = { hot: { active: [{ id: 'h1', label: 'Evening', pct: 50, ends_in_s: 3600 }], next: null } };
  const hits = C.notifyRules(snap({ leveling: idle, levelingAt: T0 }), snap({ at: T0 + 60000, leveling: live, levelingAt: T0 + 60000 }), T0 + 60000, ALL_ON);
  assert.deepStrictEqual(names(hits), ['hotTime']);
  assert.strictEqual(hits[0].title, 'Hot Time started: Evening');
  assert.strictEqual(hits[0].body, 'Evening +50% XP for 1h 00m');
  assert.match(hits[0].key, /^hotTime:h1:/);
  assert.deepStrictEqual(C.notifyRules(snap({ leveling: live, levelingAt: T0 }), snap({ leveling: live, levelingAt: T0 }), T0, ALL_ON), []);
  assert.deepStrictEqual(C.notifyRules(null, snap({ leveling: live, levelingAt: T0 }), T0, ALL_ON), []);
});

test('resetPassed: daily, weekly (Thursday) and an item reset rule (plan 021)', () => {
  const before = Date.UTC(2026, 9, 7, 23, 59, 0); // Wednesday
  const after = Date.UTC(2026, 9, 8, 0, 1, 0); // Thursday
  const hits = C.notifyRules(snap({ at: before }), snap({ at: after }), after, ALL_ON);
  assert.deepStrictEqual(hits.map((h) => h.title), ['Daily reset passed', 'Weekly reset passed']);
  assert.strictEqual(hits[0].key, 'resetPassed:daily:' + Date.UTC(2026, 9, 8));
  const mid = Date.UTC(2026, 9, 6, 12, 0, 0);
  assert.deepStrictEqual(C.notifyRules(snap({ at: T0 }), snap({ at: mid }), mid, ALL_ON).map((h) => h.title), ['Daily reset passed']);
  assert.deepStrictEqual(C.notifyRules(snap({ at: T0 }), snap({ at: T0 + 60000 }), T0 + 60000, ALL_ON), []);
  const today = { items: [{ id: 'b1', title: 'Boss scroll', kind: 'daily', reset: { every: 'day', at: '12:30' } }] };
  const r = C.notifyRules(snap({ at: T0, today: today }), snap({ at: T0 + 3600000, today: today }), T0 + 3600000, ALL_ON);
  assert.deepStrictEqual(r.map((h) => h.title), ['Reset: Boss scroll']);
  assert.strictEqual(r[0].key, 'resetPassed:b1:' + Date.UTC(2026, 9, 5, 12, 30));
});

test('newCoupon: a suggestion that was not suggested before', () => {
  const cand = (code) => ({ code: code, title: 'Coupon ' + code, url: 'https://www.naeu.playblackdesert.com/en-US/News/Detail?groupContentNo=1' });
  const a = snap({ events: { items: [], suggested: { status: 'ok', candidates: [cand('AAAA-1111')] } } });
  const b = snap({ events: { items: [], suggested: { status: 'ok', candidates: [cand('AAAA-1111'), cand('bbbb-2222')] } } });
  const hits = C.notifyRules(a, b, T0, ALL_ON);
  assert.deepStrictEqual(hits, [{ key: 'newCoupon:BBBB-2222', rule: 'newCoupon', title: 'New coupon: BBBB-2222', body: 'Coupon bbbb-2222 - add it in the Events tab' }]);
  assert.deepStrictEqual(C.notifyRules(null, b, T0, ALL_ON), []);
});

test('gameExit: running -> not running while a grind session is open', () => {
  const g = { active: { spot: 's1', started: new Date(T0 - 3600000).toISOString() } };
  const run = snap({ game: { state: 'logged_in' }, grind: g });
  const off = snap({ game: { state: 'not_running', since: new Date(T0).toISOString() }, grind: g });
  const hits = C.notifyRules(run, off, T0, ALL_ON);
  assert.deepStrictEqual(names(hits), ['gameExit']);
  assert.strictEqual(hits[0].title, 'Game exited');
  assert.strictEqual(hits[0].body, 'A grind session is still running - stop it in the Grind tab');
  assert.strictEqual(hits[0].key, 'gameExit:' + T0);
  assert.deepStrictEqual(C.notifyRules(run, snap({ game: { state: 'not_running' }, grind: { active: null } }), T0, ALL_ON), []);
  assert.deepStrictEqual(C.notifyRules(off, off, T0, ALL_ON), []);
  assert.deepStrictEqual(C.notifyRules(null, off, T0, ALL_ON), []);
});

test('bossSoon (plan 032, ladder plan 070): 15, 5 then 1 min before a spawn, through the ledger once each', () => {
  const at = T0 + 20 * 60000;
  const bosses = { next: [{ bosses: ['Kzarka'], at_utc: new Date(at).toISOString().replace('.000Z', '+00:00'), day: '2026-10-05' }],
    looted: {} };
  const on = Object.assign({ bossSoon: true }, ALL_ON);
  const L = C.notifyLedger();
  const fired = [];
  for (let t = T0; t < at + 60000; t += 60000) {
    L.take(C.notifyRules(null, snap({ at: t, bosses: bosses }), t, on), t).forEach((h) => fired.push([h.rule, h.title]));
  }
  assert.deepStrictEqual(fired, [['bossSoon', 'World boss in 15m: Kzarka'], ['bossSoon', 'World boss in 5m: Kzarka'],
    ['bossSoon', 'World boss in 1m: Kzarka']]);
  const h = C.notifyRules(null, snap({ bosses: bosses }), at - 60000, on)[0];
  assert.ok(C.validNotify({ title: h.title, body: h.body }));
  assert.strictEqual(h.body, 'Kzarka spawns in 1m 00s');
});

test('notify text is ASCII-printable and within the OS-notification limits', () => {
  const name = 'Caphras' + E_ACUTE + ' ' +'x'.repeat(200) + '\n';
  const a = snap({ market: { items: [{ id: 1, sid: 0, name: name, price: 1, below: 2, alert: null }] } });
  const b = snap({ market: { items: [{ id: 1, sid: 0, name: name, price: 1, below: 2, alert: 'below' }] } });
  const h = C.notifyRules(a, b, T0, ALL_ON)[0];
  assert.ok(C.validNotify({ title: h.title, body: h.body }));
  assert.ok(h.title.length <= C.NOTIFY_TITLE_MAX && h.body.length <= C.NOTIFY_BODY_MAX);
});

// ---- dedupe ledger ----

test('notifyLedger: each key once, cleared when the daily reset passes', () => {
  const L = C.notifyLedger();
  const h = [{ key: 'a', rule: 'r', title: 't', body: '' }, { key: 'a', rule: 'r', title: 't', body: '' }, { key: 'b', rule: 'r', title: 't', body: '' }];
  assert.deepStrictEqual(L.take(h, T0).map((x) => x.key), ['a', 'b']);
  assert.deepStrictEqual(L.take(h, T0 + 60000), []);
  assert.deepStrictEqual(L.take(h, Date.UTC(2026, 9, 6, 0, 0, 1)).map((x) => x.key), ['a', 'b']);
});

// ---- OS notification guards (main process) ----

test('validNotify: exact shape, lengths, ASCII-printable', () => {
  assert.ok(C.validNotify({ title: 'Market alert', body: '' }));
  assert.ok(C.validNotify({ title: 'x'.repeat(64), body: 'y'.repeat(200) }));
  const bad = [null, [], 'x', {}, { title: '' , body: '' }, { title: 'x'.repeat(65), body: '' },
    { title: 't', body: 'y'.repeat(201) }, { title: 't\n', body: '' }, { title: 't', body: E_ACUTE },
    { title: 't', body: '', extra: 1 }, { title: 1, body: '' }, { title: 't' }];
  for (const n of bad) assert.strictEqual(C.validNotify(n), false, JSON.stringify(n));
});

test('rateLimiter: 6 a minute, sliding window', () => {
  const r = C.rateLimiter(C.NOTIFY_RATE.max, C.NOTIFY_RATE.windowMs);
  assert.strictEqual(C.NOTIFY_RATE.max, 6);
  assert.strictEqual(C.NOTIFY_RATE.windowMs, 60000);
  for (let i = 0; i < 6; i++) assert.strictEqual(r.allow(T0 + i * 1000), true, 'call ' + i);
  assert.strictEqual(r.allow(T0 + 6000), false);
  assert.strictEqual(r.allow(T0 + 59999), false);
  assert.strictEqual(r.allow(T0 + 60000), true, 'first slot freed');
  assert.strictEqual(r.allow(T0 + 60001), false);
});
