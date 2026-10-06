'use strict';
// Plan 070: prompt hygiene - toast show-once dedupe, game-closed quiet gate,
// the 15/5/1 min alert ladder, resetSoon.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const APP = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(APP, p), 'utf8');
const TTL = JSON.parse(fs.readFileSync(path.join(APP, '..', 'server', 'ew', 'data', 'prompt_ttl.json'), 'utf8'));
const MIN = 60000;
const T0 = Date.UTC(2026, 9, 6, 15, 0, 0);

test('mirrors server/ew/data/prompt_ttl.json', () => {
  assert.strictEqual(C.TOAST_ONCE_MS, TTL.ttl_s.toast * 1000);
  assert.deepStrictEqual(C.LADDER_MIN, TTL.ladder_min);
  assert.deepStrictEqual(C.QUIET_STATES, TTL.quiet.states);
  assert.deepStrictEqual(C.QUIET_ALLOW, TTL.quiet.allow);
});

test('toastQueue once: one prompt shows once across tabs, even after dismiss or expiry', () => {
  const q = C.toastQueue();
  assert.strictEqual(q.push({ key: 'notify:a', level: 'warn', text: 'A', once: true }, T0), true);
  assert.strictEqual(q.push({ key: 'notify:a', level: 'warn', text: 'A', once: true }, T0 + 1), false, 'duplicate');
  assert.strictEqual(q.items().length, 1);
  q.remove('notify:a');
  assert.strictEqual(q.push({ key: 'notify:a', level: 'warn', text: 'A', once: true }, T0 + 2 * MIN), false, 'dismissed stays gone');
  q.expire(T0 + 20 * MIN);
  assert.strictEqual(q.push({ key: 'notify:a', level: 'warn', text: 'A', once: true }, T0 + C.TOAST_ONCE_MS), true,
    'a new prompt after the toast TTL');
  // plain toasts (POST results) still replace and repeat
  assert.strictEqual(q.push({ key: 'post:/api/today', level: 'ok', text: 'saved' }, T0), true);
  assert.strictEqual(q.push({ key: 'post:/api/today', level: 'ok', text: 'saved' }, T0 + 1), true);
  assert.strictEqual(q.items().filter((t) => t.key === 'post:/api/today').length, 1);
  assert.strictEqual(q.push(null, T0), false);
});

test('toast.js: notify toasts show once; the loop reads /api/prompts and gates', () => {
  const src = read('dashboard/toast.js');
  assert.ok(src.indexOf("'notify:' + h.key, again !== true") >= 0);
  assert.ok(src.indexOf("fetchInto('prompts', '/api/prompts')") >= 0);
  assert.ok(src.indexOf('C.promptGate(C.notifyRules(') >= 0);
  assert.ok(src.indexOf('ledger.take(g.drop, t)') >= 0);
});

test('ladderStep and parseLadder', () => {
  assert.strictEqual(C.ladderStep(16 * MIN), null);
  assert.strictEqual(C.ladderStep(15 * MIN), 15);
  assert.strictEqual(C.ladderStep(6 * MIN), 15);
  assert.strictEqual(C.ladderStep(5 * MIN), 5);
  assert.strictEqual(C.ladderStep(MIN), 1);
  assert.strictEqual(C.ladderStep(0), null);
  assert.strictEqual(C.ladderStep(25 * MIN, [30, 10]), 30);
  assert.strictEqual(C.ladderStep(10 * MIN, [10, 30]), 15, 'bad steps -> default ladder');
  assert.deepStrictEqual(C.parseLadder('15, 5,1'), [15, 5, 1]);
  for (const bad of ['', '1,5', '5,5', '0', '121', 'a', '1,2,3,4,5,6,7', null, 15]) assert.strictEqual(C.parseLadder(bad), null, String(bad));
  assert.strictEqual(C.validSettingsBody({ set: { 'notify.ladder_min': '30,10' } }), true);
  assert.strictEqual(C.validSettingsBody({ set: { 'notify.ladder_min': '10,30' } }), false);
  assert.strictEqual(C.validSettingsBody({ set: { 'notify.quiet_closed': false } }), true);
  assert.ok(C.parseSettingInput('notify.ladder_min', 'x').error.indexOf('15,5,1') >= 0);
});

test('promptGate: closed game lets only allowlisted rules through; stale ladder hits drop', () => {
  const hits = [{ key: 'm', rule: 'marketAlert' }, { key: 'g', rule: 'gameExit' }, { key: 'r', rule: 'resetPassed' },
    { key: 'b:15', rule: 'bossSoon', ladder: true }];
  const g = C.promptGate(hits, { state: 'not_running', configured: true }, null);
  assert.deepStrictEqual(g.fire.map((h) => h.key), ['m', 'g']);
  assert.deepStrictEqual(g.drop.map((h) => h.key), ['b:15']);
  for (const s of ['logged_in', 'running', 'disconnected']) {
    assert.deepStrictEqual(C.promptGate(hits, { state: s, configured: true }, null), { fire: hits, drop: [] }, s);
  }
  assert.deepStrictEqual(C.promptGate(hits, { state: 'not_running', configured: true }, { quiet_closed: false }).fire, hits);
  assert.deepStrictEqual(C.promptGate(hits, null, null).fire, hits, 'no game doc: never quiet');
  assert.deepStrictEqual(C.promptGate(hits, { state: 'not_running', configured: true }, { allow_closed: [] }).fire, []);
});

test('ladder respects notify.ladder_min from /api/prompts', () => {
  const at = T0 + 40 * MIN;
  const bosses = { next: [{ bosses: ['Kzarka'], at_utc: new Date(at).toISOString().replace('.000Z', '+00:00'), day: '2026-10-06' }], looted: {} };
  const p = { ladder_min: [30, 10] };
  const L = C.notifyLedger();
  const fired = [];
  for (let t = T0; t < at + MIN; t += MIN) {
    L.take(C.notifyRules(null, { at: t, bosses: bosses, prompts: p }, t, { bossSoon: true }), t).forEach((h) => fired.push(h.title));
  }
  assert.deepStrictEqual(fired, ['World boss in 30m: Kzarka', 'World boss in 10m: Kzarka']);
});

test('resetSoon: server timers on the ladder, default off', () => {
  assert.strictEqual(C.notifyPrefs({}).resetSoon, false);
  const iso = (ms) => new Date(ms).toISOString().replace('.000Z', '+00:00');
  const p = { timers: [{ key: 'resetSoon:daily:1', at: iso(T0 + 5 * MIN), title: 'Daily reset' },
    { key: 'resetSoon:maint:2', at: iso(T0 + 3 * 3600000), title: 'Maintenance' }, { key: 7 }, null] };
  const hits = C.notifyRules(null, { at: T0, prompts: p }, T0, { resetSoon: true });
  assert.deepStrictEqual(hits, [{ key: 'resetSoon:daily:1:5', rule: 'resetSoon', title: 'Daily reset in 5m',
    body: 'Daily reset in 5m 00s', ladder: true }]);
  assert.deepStrictEqual(C.notifyRules(null, { at: T0, prompts: p }, T0, {}), []);
});

test('hotTime end on the ladder', () => {
  const live = { hot: { active: [{ id: 'h1', label: 'Evening', pct: 50, ends_in_s: 240 }], next: null } };
  const s = { at: T0, leveling: live, levelingAt: T0 };
  const hits = C.notifyRules(s, s, T0, { hotTime: true });
  assert.strictEqual(hits.length, 1);
  assert.strictEqual(hits[0].title, 'Hot Time ends in 5m: Evening');
  assert.match(hits[0].key, /^hotTime:end:h1:\d+:5$/);
});

test('acceptance: duplicate toast + boss in 16 min while closed -> nothing until login, then 5 and 1 once each', () => {
  const at = T0 + 16 * MIN;
  const bosses = { next: [{ bosses: ['Kzarka'], at_utc: new Date(at).toISOString().replace('.000Z', '+00:00'), day: '2026-10-06' }], looted: {} };
  const login = T0 + 10.5 * MIN;
  const q = C.toastQueue();
  const L = C.notifyLedger();
  const shown = [];
  let prev = null;
  // the duplicate toast: raised twice at T0 (two tabs) while closed
  const dup = { key: 'notify:hotTime:x', level: 'warn', text: 'dup', once: true };
  for (let t = T0; t <= at + MIN; t += 5000) {
    const game = { state: t >= login ? 'logged_in' : 'not_running', configured: true };
    const next = { at: t, bosses: bosses, game: game, prompts: null };
    const g = C.promptGate(C.notifyRules(prev, next, t, { bossSoon: true, marketAlert: true }), next.game, next.prompts);
    L.take(g.drop, t);
    L.take(g.fire, t).forEach((h) => { if (q.push({ key: 'notify:' + h.key, level: 'warn', text: h.title, once: true }, t)) shown.push(h.title); });
    if (t === T0) {
      // the duplicate's rule is held while closed (neither shown nor consumed)
      assert.deepStrictEqual(C.promptGate([{ key: 'hotTime:x', rule: 'hotTime' }], game, null), { fire: [], drop: [] });
    }
    if (t === login) {
      // raised twice (two tabs) once the game is up: it shows once
      assert.strictEqual(q.push(dup, t), true);
      assert.strictEqual(q.push(dup, t + 1), false);
    }
    if (t < login) assert.deepStrictEqual(shown, []);
    prev = next;
  }
  assert.deepStrictEqual(shown, ['World boss in 5m: Kzarka', 'World boss in 1m: Kzarka']);
});
