'use strict';
// Plan 086: reward claim windows - the Events row formatter ("claim by Oct 29"
// + countdown), the claimed ack body, Home Timers rows inside 7 days and the
// T-24 h claimDue toast. No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const C = require('../shared/ewcore');

const H = 3600000;
const URL = 'https://www.naeu.playblackdesert.com/en-US/News/Detail?groupContentNo=10656&countryType=en-US';
const TITLE = "[Donghwa's Gift] One Day Only, Time-Limited Coupon";
const CLAIM = '2026-10-29T07:00:00+00:00';
const ENDS = '2026-10-15T07:00:00+00:00';
const TEXT = 'Rewards from redeemed coupons are sent to your Mail (B) and stay there until the Oct 29, 2026 (Thu) maintenance.';
const AFTER = Date.parse('2026-10-16T12:00:00Z');
const LATE = Date.parse('2026-10-27T12:00:00Z');

function item(over) {
  return Object.assign({ id: 'e1', kind: 'event', title: TITLE, code: null, rewards: null, starts: null,
    ends: ENDS, url: URL, done: false, claim_until: CLAIM, claim_text: TEXT }, over || {});
}

test('eventRows: an ended event keeps an open claim window, counted down locally', () => {
  const rows = C.eventRows([item(), item({ id: 'e2', title: 'Plain' }), item({ id: 'e3', claim_until: undefined, claim_text: undefined })], null, AFTER);
  const r = rows.find((x) => x.id === 'e1');
  assert.strictEqual(r.status, 'expired');
  assert.strictEqual(r.claim_open, true);
  assert.strictEqual(r.claim_left_s, (Date.parse(CLAIM) - AFTER) / 1000);
  const none = rows.find((x) => x.id === 'e3');
  assert.ok(!('claim_open' in none) && !('claim_left_s' in none), 'no claim fields without a window');
  // acked or past: closed
  assert.strictEqual(C.eventRows([item({ claimed: true })], null, AFTER)[0].claim_open, false);
  assert.strictEqual(C.eventRows([item()], null, Date.parse(CLAIM) + 1000)[0].claim_open, false);
});

test('claimLine: "claim by Oct 29" in the chosen zone, place from the sentence', () => {
  const r = C.eventRows([item()], null, AFTER)[0];
  const c = C.claimLine(r, { zone: 'utc' });
  assert.strictEqual(c.text, 'claim by Oct 29');
  assert.strictEqual(c.place, 'Mail');
  assert.strictEqual(c.left, C.fmtLeft(r.claim_left_s));
  assert.strictEqual(c.at, Date.parse(CLAIM));
  assert.ok(c.title.indexOf(TEXT) === 0);
  // PT: 07:00 UTC on Oct 29 is midnight Oct 29 PDT; a 06:00 UTC claim is Oct 28 PT
  assert.strictEqual(C.claimLine(r, { zone: 'pt' }).text, 'claim by Oct 29');
  const early = C.eventRows([item({ claim_until: '2026-10-29T06:00:00Z' })], null, AFTER)[0];
  assert.strictEqual(C.claimLine(early, { zone: 'pt' }).text, 'claim by Oct 28');
  // local zone follows the host clock
  const d = new Date(Date.parse(CLAIM));
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  assert.strictEqual(C.claimLine(r).text, 'claim by ' + months[d.getMonth()] + ' ' + d.getDate());
  assert.strictEqual(C.claimLine(C.eventRows([item({ claimed: true })], null, AFTER)[0]), null);
  assert.strictEqual(C.claimLine(null), null);
  assert.strictEqual(C.claimPlace("the Black Spirit's Safe"), 'Safe');
  assert.strictEqual(C.claimPlace('Mail or Safe'), 'Mail / Safe');
});

test('claimBody: the notice number from the row link; the IPC guard allows it', () => {
  assert.deepStrictEqual(C.claimBody(item()), { claimed: 10656 });
  assert.strictEqual(C.claimBody(item({ url: null })), null);
  assert.strictEqual(C.claimBody(item({ url: 'https://x/Detail?groupContentNo=abc' })), null);
  assert.strictEqual(C.validEventsBody({ claimed: 10656 }), true);
  assert.strictEqual(C.validEventsBody({ claimed: '10656' }), false);
  assert.strictEqual(C.validEventsBody({ claimed: 0 }), false);
});

test('claimRows: one per notice, soonest first', () => {
  const rows = C.claimRows([item(), item({ id: 'e2', kind: 'coupon', code: 'DAILY24HCOUPON07' }),
    item({ id: 'e3', title: 'Combat', url: 'https://x/Detail?groupContentNo=10592', claim_until: '2026-10-22T07:00:00Z' })], null, AFTER);
  assert.deepStrictEqual(rows.map((r) => r.id), ['e3', 'e1']);
});

test('nowTimers: claim windows inside 7 days, deduped against What now', () => {
  const events = { items: [item()] };
  const far = C.nowTimers({ items: [] }, null, events, null, AFTER, null);
  assert.ok(!far.rows.some((r) => r.source === 'claim'), '13 days out: not a timer');
  const near = C.nowTimers({ items: [] }, null, events, null, LATE, null);
  const row = near.rows.find((r) => r.source === 'claim');
  assert.strictEqual(row.label, 'Claim ' + TITLE);
  assert.match(row.note, /^claim by Oct 2[89] \(Mail\)$/);
  assert.strictEqual(row.due, Date.parse(CLAIM));
  const wn = { top: { text: 'Claim x rewards (Mail) - 2 days left', why: '', due: CLAIM, source: 'claim' },
    next: [], empty: false, empty_text: '', errors: [] };
  assert.ok(!C.nowTimers({ items: [] }, null, events, null, LATE, wn).rows.some((r) => r.source === 'claim'));
});

test('claimDue: one T-24 h toast per notice that passes the game-closed quiet, rule on (plan 080)', () => {
  assert.strictEqual(C.notifyPrefs({}).claimDue, true);
  const next = (t) => ({ at: t, events: { items: [item(), item({ id: 'e2', kind: 'coupon', code: 'DAILY24HCOUPON07' })] } });
  const t24 = Date.parse(CLAIM) - 23.5 * H;
  const hits = C.notifyRules(null, next(t24), t24, { claimDue: true });
  assert.deepStrictEqual(hits.map((h) => [h.key, h.rule, h.closed]), [['claimDue:' + URL + ':2026-10-29', 'claimDue', true]]);
  // a re-resolved time on the same date (maintenance notice imported) keeps the key
  const tm = Date.parse('2026-10-29T08:30:00Z') - 23.5 * H;
  const moved = { at: tm, events: { items: [item({ claim_until: '2026-10-29T08:30:00+00:00' })] } };
  assert.strictEqual(C.notifyRules(null, moved, tm, { claimDue: true })[0].key, hits[0].key);
  assert.ok(hits[0].title.indexOf('Claim rewards: ') === 0);
  const g = C.promptGate(hits, { state: 'not_running' }, {});
  assert.strictEqual(g.fire.length, 1);
  for (const t of [Date.parse(CLAIM) - 25 * H, Date.parse(CLAIM) - 22 * H, Date.parse(CLAIM) - 5 * 60000]) {
    assert.deepStrictEqual(C.notifyRules(null, next(t), t, { claimDue: true }), [], 'no ladder, one toast');
  }
  const acked = { at: t24, events: { items: [item({ claimed: true })] } };
  assert.deepStrictEqual(C.notifyRules(null, acked, t24, { claimDue: true }), []);
});
