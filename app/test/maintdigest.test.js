'use strict';
// Plan 074: before-maintenance digest - the Home card formatter, the ack body
// check and the maintLoss notify rule (ladder on unacked warnings, plus T-24 h
// / T-1 h toasts that pass the game-closed quiet). No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const APP = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(APP, p), 'utf8');
const START = Date.parse('2026-10-08T08:00:00Z');
const H = 3600000;
const KEY = '10663:0123456789ab';
const TAG = 'All unclaimed EXP in the Tag Characters UI will be deleted when the update goes live, ' +
  'so please claim it beforehand.';

function view(extra) {
  return Object.assign({
    show: true,
    maint: { start_utc: '2026-10-08T08:00:00+00:00', end_utc: '2026-10-08T12:00:00+00:00', source: 'x' },
    warnings: [{ key: KEY, text: TAG, notice_no: 10663, url: 'u', due_utc: '2026-10-08T08:00:00+00:00' }],
    ending: [{ kind: 'event', title: 'Autumn hunt', ends: '2026-10-08T08:00:00+00:00' }],
    acked: []
  }, extra || {});
}

test('Home card: "Before maintenance (in 18 h)", warnings first with an ack, then what ends', () => {
  const c = C.nowMaintDigest(view(), START - 18 * H);
  assert.strictEqual(c.id, 'maintdigest');
  assert.strictEqual(c.title, 'Before maintenance (in 18 h)');
  assert.strictEqual(c.tab, 'events');
  assert.deepStrictEqual(c.rows.map((r) => [r.label, r.cls, r.ack || null]),
    [[TAG, 'warn', KEY], ['Autumn hunt', '', null]]);
  assert.strictEqual(c.rows[0].note, 'official notice 10663');
  assert.strictEqual(c.rows[1].value, '18h 00m');
  assert.strictEqual(c.rows[1].note, 'event ends');
});

test('Home card: hidden before T-24 h (server show false) and on junk', () => {
  assert.strictEqual(C.nowMaintDigest(view({ show: false }), START - 44 * H), null);
  assert.strictEqual(C.nowMaintDigest(null, START), null);
  assert.strictEqual(C.nowMaintDigest(view({ maint: null }), START), null);
  const bad = view({ warnings: [{ key: 'nope', text: TAG }, { key: KEY, text: 'x'.repeat(201) }, 5],
    ending: [{ title: '', ends: 'x' }, null] });
  const c = C.nowMaintDigest(bad, START - H);
  assert.deepStrictEqual(c.rows, []);
  assert.strictEqual(c.empty, 'nothing ends at this maintenance');
});

test('maintInText: whole hours, minutes under an hour, now once due', () => {
  assert.strictEqual(C.maintInText(18 * H + 59 * 60000), 'in 18 h');
  assert.strictEqual(C.maintInText(40 * 60000), 'in 40 m');
  assert.strictEqual(C.maintInText(10000), 'in 1 m');
  assert.strictEqual(C.maintInText(0), 'now');
});

test('composeNow: the digest card sits under What now', () => {
  const snap = { at: {}, maint: view(), whatnow: { top: { text: 'x', why: '', due: null, source: 'maint_loss' },
    next: [] } };
  const out = C.composeNow(snap, START - 20 * H);
  assert.deepStrictEqual(out.cards.map((c) => c.id).slice(0, 2), ['whatnow', 'maintdigest']);
  assert.ok(out.quiet.every((q) => q.tab !== 'events' || q.title !== 'Before maintenance'));
  assert.ok(C.composeNow({ at: {}, maint: view({ show: false }) }, START - 44 * H).cards
    .every((c) => c.id !== 'maintdigest'));
});

test('What now: maint_loss and event actions open the Events tab', () => {
  const v = { top: { text: 'Before maintenance: claim', why: 'official notice 10663', due: null, source: 'maint_loss' },
    next: [{ text: 'Autumn hunt ends', why: 'event ends', due: null, source: 'event' }] };
  assert.deepStrictEqual(C.nowWhatNow(v, START).rows.map((r) => r.go.tab), ['events', 'events']);
});

test('ack body: exactly {ack: "<notice>:<12 hex>"}', () => {
  assert.ok(C.POST_ROUTES.indexOf('/api/maint/digest') >= 0);
  assert.strictEqual(C.validPost('/api/maint/digest', { ack: KEY }), true);
  for (const b of [{}, { ack: 'x' }, { ack: KEY, more: 1 }, { ack: 5 }, null, { unack: KEY }]) {
    assert.strictEqual(C.validPost('/api/maint/digest', b), false, JSON.stringify(b));
  }
  assert.strictEqual(C.postToast('/api/maint/digest', { ok: true }).text, 'Maintenance warning saved');
});

// ---- maintLoss rule ----

const prompts = (at) => ({ ladder_min: [15, 5, 1],
  maint_loss: [{ key: 'maintLoss:' + KEY, at: new Date(START).toISOString(), title: 'Before maintenance', text: TAG },
    { key: 7 }, null] });

test('maintLoss: on with zero config (plan 080), T-24 h toast passes the game-closed quiet', () => {
  assert.strictEqual(C.notifyPrefs({}).maintLoss, true);
  const now = START - 24 * H + 60000;
  const hits = C.notifyRules(null, { at: now, prompts: prompts() }, now, { maintLoss: true });
  assert.deepStrictEqual(hits.map((h) => [h.key, h.rule, h.closed === true]),
    [['maintLoss:' + KEY + ':24h', 'maintLoss', true]]);
  const g = C.promptGate(hits, { state: 'not_running' }, { quiet_closed: true });
  assert.strictEqual(g.fire.length, 1);
  assert.strictEqual(C.notifyRules(null, { at: now, prompts: prompts() }, now, {}).length, 0, 'empty prefs: nothing');
});

test('maintLoss: quiet between the toasts, ladder near the start (dropped while closed)', () => {
  const mid = START - 10 * H;
  assert.deepStrictEqual(C.notifyRules(null, { at: mid, prompts: prompts() }, mid, { maintLoss: true }), []);
  const near = START - 4 * 60000;
  const hits = C.notifyRules(null, { at: near, prompts: prompts() }, near, { maintLoss: true });
  assert.deepStrictEqual(hits.map((h) => h.key), ['maintLoss:' + KEY + ':1h', 'maintLoss:' + KEY + ':5']);
  const g = C.promptGate(hits, { state: 'not_running' }, { quiet_closed: true });
  assert.deepStrictEqual(g.fire.map((h) => h.key), ['maintLoss:' + KEY + ':1h']);
  assert.deepStrictEqual(g.drop.map((h) => h.key), ['maintLoss:' + KEY + ':5']);
  const live = C.promptGate(hits, { state: 'logged_in' }, { quiet_closed: true });
  assert.strictEqual(live.fire.length, 2);
  assert.deepStrictEqual(C.notifyRules(null, { at: START + 1, prompts: prompts() }, START + 1, { maintLoss: true }), []);
});

test('wiring: Home polls the digest; server knows the route; the rule is always on', () => {
  assert.match(read('dashboard/home.js'), /maint: '\/api\/maint\/digest'/);
  const app = fs.readFileSync(path.join(APP, '..', 'server', 'ew', 'app.py'), 'utf8');
  assert.match(app, /"\/api\/maint\/digest": _post_maint_digest/);
  const settings = fs.readFileSync(path.join(APP, '..', 'server', 'ew', 'settings.py'), 'utf8');
  assert.doesNotMatch(settings, /maintLoss/, 'plan 080: no per-rule setting');
  assert.ok(C.NOTIFY_RULES.some((r) => r.name === 'maintLoss' && r.defaultOn === true));
});
