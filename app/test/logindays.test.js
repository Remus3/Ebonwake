'use strict';
// Plan 075: login-day reward tracker - pure formatters over the server's
// `login_days` block, the POST bodies, the loginRisk notify rule and a static
// guard on the Events tab. No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const read = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');
const T0 = Date.parse('2026-10-21T10:00:00Z');

const row = (o) => Object.assign({ id: 'e1', title: 'Special Login Reward', needed: 14, credited: 6,
  days_left: 8, today_done: false, at_risk: true, lost: false, complete: false, today_minutes: 0,
  min_minutes: 0, weekend: null, counting_since: null }, o);

test('loginDayText: "Login days 6/14 - 8 days left", singular, ended', () => {
  assert.strictEqual(C.loginDayText(row()), 'Login days 6/14 - 8 days left');
  assert.strictEqual(C.loginDayText(row({ days_left: 1 })), 'Login days 6/14 - 1 day left');
  assert.strictEqual(C.loginDayText(row({ days_left: 0 })), 'Login days 6/14 - ended');
});

test('loginPill: complete > lost > at risk > today done > log in today', () => {
  assert.deepStrictEqual(C.loginPill(row()), { cls: 'warn', text: 'at risk' });
  assert.deepStrictEqual(C.loginPill(row({ at_risk: false, lost: true })), { cls: 'bad', text: 'lost' });
  assert.deepStrictEqual(C.loginPill(row({ complete: true })), { cls: 'ok', text: 'complete' });
  assert.deepStrictEqual(C.loginPill(row({ at_risk: false, today_done: true })), { cls: 'ok', text: 'today done' });
  assert.deepStrictEqual(C.loginPill(row({ at_risk: false })), { cls: 'unknown', text: 'log in today' });
});

test('loginTodayText: minute rules only, while logged in or with minutes counted', () => {
  assert.strictEqual(C.loginTodayText(row({ min_minutes: 60, today_minutes: 38 }), true), '38/60 min today');
  assert.strictEqual(C.loginTodayText(row({ min_minutes: 60, today_minutes: 75 }), true), '60/60 min today');
  assert.strictEqual(C.loginTodayText(row({ min_minutes: 60, today_minutes: 0 }), true), '0/60 min today');
  assert.strictEqual(C.loginTodayText(row({ min_minutes: 60, today_minutes: 0 }), false), null);
  assert.strictEqual(C.loginTodayText(row({ min_minutes: 0, today_minutes: 30 }), true), null);
});

test('loginWeekendText: shown only with a weekend rule', () => {
  assert.strictEqual(C.loginWeekendText(row({ weekend: { credited: 2, total: 4, minutes: 60 } })),
    'weekend bonus 2/4 (60 min)');
  assert.strictEqual(C.loginWeekendText(row()), null);
});

test('loginDays: rows by id, suggestions, bad rows dropped', () => {
  const ld = C.loginDays({ logged_in: true, today: '2026-10-21', rows: [row(), { id: 'e2', credited: 'x' }, null],
    suggest: [{ id: 'e3', title: 'x', rule: { days_needed: 7, min_minutes: 60 }, label: 'track logins? (7 days)' },
      { id: 'e4', rule: { days_needed: 0, min_minutes: 0 } }] });
  assert.deepStrictEqual(Object.keys(ld.byId), ['e1']);
  assert.deepStrictEqual(Object.keys(ld.suggest), ['e3']);
  assert.strictEqual(ld.loggedIn, true);
  assert.deepStrictEqual(C.loginDays(null), { byId: {}, suggest: {}, loggedIn: false });
});

test('POST bodies: track (edit login_rule), mark a past day; the IPC guard accepts them', () => {
  const s = { id: 'e3', rule: { days_needed: 7, min_minutes: 60 } };
  const b = C.loginTrackBody(s);
  assert.deepStrictEqual(b, { edit: { id: 'e3', login_rule: { days_needed: 7, min_minutes: 60 } } });
  assert.strictEqual(C.validEventsBody(b), true);
  assert.strictEqual(C.validEventsBody({ edit: { id: 'e3', login_rule: null } }), true);
  assert.strictEqual(C.validEventsBody({ edit: { id: 'e3', login_rule: { days_needed: 61, min_minutes: 0 } } }), false);
  assert.strictEqual(C.validEventsBody({ edit: { id: 'e3', login_rule: { days_needed: 7, min_minutes: 0, x: 1 } } }), false);
  assert.strictEqual(C.validEventsBody({ add: { kind: 'event', title: 'T', login_rule: { days_needed: 14, min_minutes: 0, weekend_minutes: 60 } } }), true);
  assert.strictEqual(C.loginTrackBody({ id: 'e3', rule: { days_needed: 0, min_minutes: 0 } }), null);
  const m = C.loginMarkBody('2026-10-20');
  assert.deepStrictEqual(m, { login_mark: { date: '2026-10-20', on: true } });
  assert.strictEqual(C.validEventsBody(m), true);
  assert.strictEqual(C.loginMarkBody(''), null);
  assert.strictEqual(C.validEventsBody({ login_mark: { date: '2026-10-20' } }), false);
  assert.strictEqual(C.validEventsBody({ login_mark: { date: 'x', on: true } }), false);
});

test('loginRisk: default off, one hit per at-risk row per UTC day, allowed while closed', () => {
  assert.strictEqual(C.notifyPrefs({}).loginRisk, false);
  const next = { events: { login_days: { today: '2026-10-21', rows: [row(), row({ id: 'e2', at_risk: false })] } } };
  const hits = C.notifyRules(null, next, T0, { loginRisk: true });
  assert.deepStrictEqual(hits, [{ key: 'loginRisk:e1:2026-10-21', rule: 'loginRisk',
    title: 'Log in today: Special Login Reward', body: 'Login days 6/14 - 8 days left' }]);
  const L = C.notifyLedger();
  assert.strictEqual(L.take(hits, T0).length, 1);
  assert.strictEqual(L.take(C.notifyRules(null, next, T0 + 60000, { loginRisk: true }), T0 + 60000).length, 0);
  assert.deepStrictEqual(C.notifyRules(null, next, T0, {}), []);
  const cfg = JSON.parse(read('../server/ew/data/prompt_ttl.json'));
  assert.ok(cfg.quiet.allow.indexOf('loginRisk') >= 0);
});

test('Events tab draws the login line through the core helpers', () => {
  const src = read('dashboard/events.js');
  ['C.loginDays(', 'C.loginDayText(', 'C.loginPill(', 'C.loginTrackBody(', 'C.loginMarkBody('].forEach((s) => {
    assert.ok(src.indexOf(s) >= 0, s);
  });
});
