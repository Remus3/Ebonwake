'use strict';
// Plan 006 slice B: pure Events helpers (ewcore.js), the /api/events bridge
// route, the eventsSoon overlay widget and static guards on the Events tab
// (events.js) and the overlay. No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const APP = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(APP, p), 'utf8');
const T0 = Date.parse('2026-10-04T12:00:00Z');

test('fmtLeft: countdown text; null "-", ended at or below 0', () => {
  assert.strictEqual(C.fmtLeft(null), '-');
  assert.strictEqual(C.fmtLeft(undefined), '-');
  assert.strictEqual(C.fmtLeft('5'), '-');
  assert.strictEqual(C.fmtLeft(NaN), '-');
  assert.strictEqual(C.fmtLeft(0), 'ended');
  assert.strictEqual(C.fmtLeft(-30), 'ended');
  assert.strictEqual(C.fmtLeft(65), '1m 05s');
  assert.strictEqual(C.fmtLeft(3 * 3600 + 120), '3h 02m');
  assert.strictEqual(C.fmtLeft(2 * 86400 + 5 * 3600 + 59), '2d 5h');
  assert.strictEqual(C.fmtLeft(0.4), '0m 00s', 'a fraction left is not ended yet');
});

test('EVENT_KINDS and EVENTS_SOON_S match the plan', () => {
  assert.deepStrictEqual(C.EVENT_KINDS, ['coupon', 'event', 'drop']);
  assert.strictEqual(C.EVENTS_SOON_S, 172800);
});

function item(o) {
  return Object.assign({ id: 'e1', kind: 'event', title: 'T', code: null, rewards: '', starts: null,
    ends: null, url: null, done: false, left_s: null, status: 'active', soon: false }, o);
}

test('eventRows: live left_s from ends, status and soon re-derived, server order rule', () => {
  const items = [
    item({ id: 'e1', title: 'far', ends: '2026-10-20T00:00:00Z', left_s: 1339200 }),
    item({ id: 'e2', kind: 'coupon', code: 'ABCD', title: 'near', ends: '2026-10-04T13:00:00Z', left_s: 3600, soon: true }),
    item({ id: 'e3', title: 'no end' }),
    item({ id: 'e4', title: 'done', done: true, ends: '2026-10-05T00:00:00Z', status: 'done' }),
    item({ id: 'e5', title: 'old', ends: '2026-10-01T00:00:00Z', status: 'expired' }),
    item({ id: 'e6', title: 'older', ends: '2026-09-01T00:00:00Z', status: 'expired' }),
    item({ id: 'e7', title: 'later', starts: '2026-10-06T00:00:00Z', ends: '2026-10-06T06:00:00Z' }),
    item({ id: 'e8', title: 'ends at tick', ends: '2026-10-04T12:01:00Z', left_s: 60 }),
    null, 'x', { id: 5 }
  ];
  const rows = C.eventRows(items, T0, T0 + 60000);
  assert.deepStrictEqual(rows.map((r) => r.id), ['e2', 'e7', 'e1', 'e3', 'e4', 'e8', 'e5', 'e6']);
  const by = {};
  rows.forEach((r) => { by[r.id] = r; });
  assert.strictEqual(by.e2.left_s, 3540);
  assert.strictEqual(by.e2.status, 'active');
  assert.strictEqual(by.e2.soon, true);
  assert.strictEqual(by.e1.soon, false);
  assert.strictEqual(by.e7.status, 'upcoming');
  assert.strictEqual(by.e7.soon, true, 'upcoming items can end soon too');
  assert.strictEqual(by.e3.left_s, null);
  assert.strictEqual(by.e3.status, 'active');
  assert.strictEqual(by.e3.soon, false);
  assert.strictEqual(by.e4.status, 'done');
  assert.strictEqual(by.e4.soon, false);
  assert.strictEqual(by.e8.status, 'expired', 'flips locally when the clock passes ends');
  assert.strictEqual(by.e8.soon, false);
  assert.strictEqual(items[1].left_s, 3600, 'input not mutated');
  assert.deepStrictEqual(C.eventRows(null, T0, T0), []);
  assert.deepStrictEqual(C.eventRows('x', T0, T0), []);
});

test('eventRows: unparseable ends falls back to left_s minus time since fetch', () => {
  const rows = C.eventRows([item({ id: 'e1', ends: 'junk', left_s: 100 })], T0, T0 + 30000);
  assert.strictEqual(rows[0].left_s, 70);
  const gone = C.eventRows([item({ id: 'e1', ends: 'junk', left_s: 10 })], T0, T0 + 30000);
  assert.strictEqual(gone[0].status, 'expired');
});

test('soonestEvent: first open soon row, else null', () => {
  const items = [
    item({ id: 'e1', title: 'far', ends: '2026-10-20T00:00:00Z' }),
    item({ id: 'e2', title: 'done soon', done: true, ends: '2026-10-04T12:30:00Z' }),
    item({ id: 'e3', title: 'soon', ends: '2026-10-05T00:00:00Z' })
  ];
  const s = C.soonestEvent(items, T0, T0);
  assert.deepStrictEqual([s.id, s.left_s], ['e3', 43200]);
  assert.strictEqual(C.soonestEvent([items[0]], T0, T0), null);
  assert.strictEqual(C.soonestEvent(null, T0, T0), null);
});

test('localToUtcIso: datetime-local value (local time) -> UTC ISO with Z, seconds, no millis', () => {
  const want = new Date(2026, 9, 5, 18, 30, 0).toISOString().slice(0, 19) + 'Z';
  assert.strictEqual(C.localToUtcIso('2026-10-05T18:30'), want);
  assert.strictEqual(C.localToUtcIso('2026-10-05T18:30:15'), new Date(2026, 9, 5, 18, 30, 15).toISOString().slice(0, 19) + 'Z');
  for (const bad of ['', null, undefined, '2026-10-05', '2026-13-05T10:00', '2026-02-30T10:00', '2026-10-05T25:00', 'x', 5]) {
    assert.strictEqual(C.localToUtcIso(bad), null, String(bad));
  }
});

test('validEventsBody accepts exactly the slice A POST shapes', () => {
  const ok = [
    { add: { kind: 'coupon', title: 'Autumn', code: 'ABCD-1234-EFGH' } },
    { add: { kind: 'coupon', title: 'x'.repeat(80), code: 'abcd', rewards: 'y'.repeat(200), ends: '2026-10-31' } },
    { add: { kind: 'event', title: 'Boss rush', starts: '2026-10-05T00:00:00Z', ends: '2026-10-12T23:59:59+02:00' } },
    { add: { kind: 'drop', title: 'Twitch drops', url: 'https://www.twitch.tv/drops/campaigns', ends: '2026-10-12T10:00Z' } },
    { add: { kind: 'event', title: 'Z', rewards: '' } },
    { edit: { id: 'e1', title: 'New' } },
    { edit: { id: 'e12', rewards: null, ends: null, starts: null, url: null } },
    { edit: { id: 'e3', code: 'NEWCODE1', ends: '2026-11-01' } },
    { done: { id: 'e1', done: true } }, { done: { id: 'e999999999', done: false } },
    { delete: 'e1' },
    { purge_expired: true }
  ];
  for (const b of ok) assert.strictEqual(C.validEventsBody(b), true, JSON.stringify(b));
  const bad = [
    null, [], 'x', {}, { add: { kind: 'event', title: 'a' }, delete: 'e1' }, { bogus: 1 },
    { add: null }, { add: {} }, { add: { kind: 'raid', title: 'a' } }, { add: { kind: 'event' } },
    { add: { kind: 'event', title: '' } }, { add: { kind: 'event', title: '  ' } },
    { add: { kind: 'event', title: 'x'.repeat(81) } }, { add: { kind: 'event', title: 'a\nb' } },
    { add: { kind: 'coupon', title: 'a' } }, { add: { kind: 'event', title: 'a', code: 'ABCD' } },
    { add: { kind: 'coupon', title: 'a', code: 'ABC' } }, { add: { kind: 'coupon', title: 'a', code: 'x'.repeat(41) } },
    { add: { kind: 'coupon', title: 'a', code: 'AB CD' } }, { add: { kind: 'coupon', title: 'a', code: 'AB_CD' } },
    { add: { kind: 'event', title: 'a', rewards: 'y'.repeat(201) } }, { add: { kind: 'event', title: 'a', rewards: 5 } },
    { add: { kind: 'event', title: 'a', url: 'http://example.com' } },
    { add: { kind: 'event', title: 'a', url: 'https://' + 'x'.repeat(300) } },
    { add: { kind: 'event', title: 'a', url: 'javascript:alert(1)' } },
    { add: { kind: 'event', title: 'a', ends: '2026-10-05T10:00:00' } }, { add: { kind: 'event', title: 'a', ends: 'tomorrow' } },
    { add: { kind: 'event', title: 'a', ends: '2026-02-30' } }, { add: { kind: 'event', title: 'a', ends: 1700000000 } },
    { add: { kind: 'event', title: 'a', starts: '2026-10-06', ends: '2026-10-05' } },
    { add: { kind: 'event', title: 'a', done: true } }, { add: { kind: 'event', title: 'a', id: 'e1' } },
    { add: { kind: 'event', title: 'a', ends: null } },
    { edit: { id: 'e1' } }, { edit: { title: 'a' } }, { edit: { id: 'x1', title: 'a' } },
    { edit: { id: 'e1', title: null } }, { edit: { id: 'e1', kind: 'drop' } }, { edit: { id: 'e1', done: true } },
    { edit: { id: 'e1', code: 'AB' } }, { edit: { id: 'e1', code: null } }, { edit: { id: 'e1', url: 'ftp://x' } },
    { done: { id: 'e1' } }, { done: { id: 'e1', done: 1 } }, { done: { id: 'e1', done: true, x: 1 } }, { done: 'e1' },
    { delete: '' }, { delete: 'e' }, { delete: 'E1' }, { delete: 'e1 ' }, { delete: 1 }, { delete: 'e1234567890' },
    { delete: null },
    { purge_expired: false }, { purge_expired: 1 }, { purge_expired: 'true' }
  ];
  for (const b of bad) assert.strictEqual(C.validEventsBody(b), false, JSON.stringify(b));
});

test('parseEventForm: form strings -> add body (code only for coupons, ends local -> UTC) or an error', () => {
  const ends = C.localToUtcIso('2026-10-31T23:00');
  assert.deepStrictEqual(C.parseEventForm({ kind: 'coupon', code: ' abcd-1234 ', title: ' Autumn ', rewards: ' 10 cron ', ends: '2026-10-31T23:00', url: '' }),
    { ok: true, body: { add: { kind: 'coupon', title: 'Autumn', code: 'ABCD-1234', rewards: '10 cron', ends: ends } } });
  assert.deepStrictEqual(C.parseEventForm({ kind: 'event', code: 'IGNORED', title: 'Boss rush', rewards: '', ends: '', url: ' https://example.com/x ' }),
    { ok: true, body: { add: { kind: 'event', title: 'Boss rush', url: 'https://example.com/x' } } });
  assert.strictEqual(C.parseEventForm({ kind: 'coupon', title: 'a', code: '' }).ok, false);
  assert.match(C.parseEventForm({ kind: 'coupon', title: 'a', code: 'a b' }).error, /code/);
  assert.match(C.parseEventForm({ kind: 'event', title: '' }).error, /title/);
  assert.match(C.parseEventForm({ kind: 'event', title: 'a', url: 'http://x' }).error, /https/);
  assert.match(C.parseEventForm({ kind: 'event', title: 'a', rewards: 'y'.repeat(201) }).error, /rewards/);
  assert.match(C.parseEventForm({ kind: 'event', title: 'a', ends: '2026-02-30T10:00' }).error, /ends/);
  assert.strictEqual(C.parseEventForm({ kind: 'nope', title: 'a' }).ok, false);
  assert.strictEqual(C.parseEventForm(null).ok, false);
  for (const f of [{ kind: 'coupon', title: 'a', code: 'ABCD' }, { kind: 'drop', title: 'b', ends: '2026-10-10T10:10' }]) {
    const r = C.parseEventForm(f);
    assert.strictEqual(r.ok, true);
    assert.strictEqual(C.validEventsBody(r.body), true, JSON.stringify(r.body));
  }
});

test('validPost allowlist carries /api/events with its own validator', () => {
  assert.ok(['/api/events', '/api/grind', '/api/market/watch', '/api/progress', '/api/today']
    .every((r) => C.POST_ROUTES.indexOf(r) >= 0));
  assert.strictEqual(C.validPost('/api/events', { delete: 'e1' }), true);
  assert.strictEqual(C.validPost('/api/events', { start: 'gyfin' }), false);
  assert.strictEqual(C.validPost('/api/grind', { delete: 'e1' }), false);
  assert.strictEqual(C.validPost('/api/events/', { delete: 'e1' }), false);
  assert.match(read('preload.js'), /\/api\/events/);
});

test('overlayWidgets: eventsSoon default on, only a literal false turns it off', () => {
  assert.strictEqual(C.overlayWidgets({}).eventsSoon, true);
  assert.strictEqual(C.overlayWidgets(null).eventsSoon, true);
  assert.strictEqual(C.overlayWidgets({ overlay: { widgets: { eventsSoon: false } } }).eventsSoon, false);
  assert.strictEqual(C.overlayWidgets({ overlay: { widgets: { eventsSoon: 0 } } }).eventsSoon, true);
  assert.strictEqual(C.widgetsFromQuery('?eventsSoon=0').eventsSoon, false);
  assert.strictEqual(C.widgetsFromQuery('').eventsSoon, true);
  const q = C.widgetsQuery({ grindSession: true, grindBuff: true, eventsSoon: false });
  assert.strictEqual(C.widgetsFromQuery('?' + new URLSearchParams(q).toString()).eventsSoon, false);
  const ex = JSON.parse(fs.readFileSync(path.join(APP, '..', 'config', 'local.example.json'), 'utf8'));
  assert.strictEqual(ex.overlay.widgets.eventsSoon, true);
});

test('events.js: safe DOM, POST via the bridge only, uses the shared helpers', () => {
  const src = read('dashboard/events.js');
  assert.doesNotMatch(src, /innerHTML|outerHTML|insertAdjacentHTML|document\.write/);
  assert.match(src, /\/api\/events/);
  assert.match(src, /ewApi/);
  for (const f of ['fmtLeft', 'eventRows', 'parseEventForm']) assert.match(src, new RegExp('C\\.' + f + '\\('), f);
  assert.match(src, /sure\?/, 'two-click confirm');
  assert.match(src, /purge_expired/);
  assert.match(src, /navigator\.clipboard/);
  assert.match(src, /datetime-local/);
  assert.doesNotMatch(src, /method:\s*'POST'/, 'renderer never POSTs directly');
  assert.doesNotMatch(src, /window\.open|location\.href\s*=|\.href\s*=/, 'no navigation inside the dashboard');
  assert.doesNotMatch(src, /createElement\('a'\)/, 'sources are plain text, not links');
  assert.match(src, /window\.EWEvents\s*=/);
});

test('suggestedRows: valid candidates only, known codes and duplicates dropped, upper-cased', () => {
  const sug = { status: 'ok', candidates: [
    { code: 'AUTUMN-2026-GIFT', title: 'Autumn coupon', url: 'https://www.naeu.playblackdesert.com/en-US/News/Detail?groupContentNo=1', date: '2026-10-03' },
    { code: 'oldcode-1234', title: 'Old', url: 'https://www.naeu.playblackdesert.com/x', date: null },
    { code: 'HAWKEYE7TREAT', title: 'Deadeye', url: 'https://www.naeu.playblackdesert.com/y', date: 'junk' },
    { code: 'AUTUMN-2026-GIFT', title: 'dup', url: 'https://www.naeu.playblackdesert.com/z', date: null },
    { code: 'bad code', title: 'x', url: 'https://a.b/c' }, { code: 'GOOD1234', title: '', url: 'https://a.b/c' },
    { code: 'GOOD1234', title: 'x', url: 'javascript:alert(1)' }, null, 'x', 5
  ] };
  const items = [item({ id: 'e1', kind: 'coupon', code: 'OLDCODE-1234' }), null];
  const rows = C.suggestedRows(sug, items);
  assert.deepStrictEqual(rows.map((r) => r.code), ['AUTUMN-2026-GIFT', 'HAWKEYE7TREAT']);
  assert.strictEqual(rows[0].date, '2026-10-03');
  assert.strictEqual(rows[1].date, null, 'junk date dropped');
  assert.deepStrictEqual(C.suggestedRows(null, null), []);
  assert.deepStrictEqual(C.suggestedRows({ candidates: 'x' }, []), []);
});

test('suggestAddBody: one click = a plain coupon add the bridge accepts', () => {
  const c = { code: 'hawkeye7treat', title: 'Deadeye coupons', url: 'https://www.naeu.playblackdesert.com/en-US/News/Detail?groupContentNo=2', date: '2026-10-01' };
  const b = C.suggestAddBody(c);
  assert.deepStrictEqual(b, { add: { kind: 'coupon', title: 'Deadeye coupons', code: 'HAWKEYE7TREAT', url: c.url } });
  assert.strictEqual(C.validEventsBody(b), true);
  assert.strictEqual(C.validPost('/api/events', b), true);
  assert.deepStrictEqual(C.suggestAddBody({ code: 'ABCD1', title: 't', url: 'http://x' }), { add: { kind: 'coupon', title: 't', code: 'ABCD1' } });
  for (const bad of [null, {}, { code: 'AB', title: 't' }, { code: 'ABCD1', title: '' }]) {
    assert.strictEqual(C.suggestAddBody(bad), null, JSON.stringify(bad));
  }
});

test('suggestStatus: coupon check line for the Sources card, robots reason when off', () => {
  assert.deepStrictEqual(C.suggestStatus({ status: 'off', robots: 'disallow' }), { status: 'off', text: 'coupon check: off - robots.txt disallow' });
  assert.match(C.suggestStatus({ status: 'off', robots: 'unreachable' }).text, /unreachable/);
  assert.strictEqual(C.suggestStatus({ status: 'stale' }).status, 'stale');
  assert.strictEqual(C.suggestStatus({ status: 'ok' }).status, 'ok');
  assert.strictEqual(C.suggestStatus({ status: 'weird' }).status, 'none');
  assert.strictEqual(C.suggestStatus(null).status, 'none');
});

test('events.js: Suggested card uses the helpers and never auto-adds', () => {
  const src = read('dashboard/events.js');
  for (const f of ['suggestedRows', 'suggestAddBody', 'suggestStatus']) assert.match(src, new RegExp('C\\.' + f + '\\('), f);
  assert.match(src, /Suggested coupons/);
  const i = src.indexOf('C.suggestAddBody(');
  assert.ok(src.lastIndexOf("addEventListener('click'", i) > src.lastIndexOf('function drawSuggested', i), 'add only inside a click handler');
});

// ---- plan 059: event-notice suggestions ----

const NURL = 'https://www.naeu.playblackdesert.com/en-US/News/Detail?groupContentNo=10656&countryType=en-US';
function notice(o) {
  return Object.assign({ title: "[Donghwa's Gift] One Day Only, Time-Limited Coupon", url: NURL,
    group_no: 10656, starts: '2026-10-01T10:00:00+00:00', ends: '2026-10-15T07:00:00+00:00',
    ends_text: 'Oct 15, 2026 (Thu) before maintenance', kind: 'event', maint_relative: true,
    ends_edge: 'before' }, o);
}

test('noticeRows: valid candidates only; same url or title + ends already stored is skipped', () => {
  const sug = { status: 'ok', candidates: [
    notice(), notice({ group_no: 10656, title: 'dup number' }),
    notice({ group_no: 10545, title: 'Heidel Ball', url: NURL.replace('10656', '10545'),
      starts: '2026-09-04T00:00:00+00:00', ends: '2026-10-21T23:59:00+00:00', maint_relative: false, ends_edge: null }),
    notice({ group_no: 1, url: 'javascript:alert(1)' }), notice({ group_no: 2, title: '' }),
    notice({ group_no: 3, ends: 'soon' }), notice({ group_no: 4, starts: '2026-10-20T00:00:00Z' }),
    notice({ group_no: 0 }), notice({ group_no: '7' }), notice({ group_no: 8, ends_edge: 'sideways' }),
    null, 'x', 5
  ] };
  const rows = C.noticeRows(sug, []);
  assert.deepStrictEqual(rows.map((r) => r.group_no), [10656, 10545, 8]);
  assert.strictEqual(rows[2].ends_edge, null, 'unknown edge dropped');
  const byUrl = C.noticeRows(sug, [item({ url: NURL })]);
  assert.deepStrictEqual(byUrl.map((r) => r.group_no), [10545], 'both NURL rows skipped');
  const byEnds = C.noticeRows(sug, [item({ title: 'Heidel Ball', ends: '2026-10-21T23:59:00Z' })]);
  assert.ok(!byEnds.some((r) => r.group_no === 10545), 'same title + ends (any offset form)');
  assert.deepStrictEqual(C.noticeRows(null, null), []);
  assert.deepStrictEqual(C.noticeRows({ candidates: 'x' }, []), []);
});

test('noticeAddBody: a plain event add with the source link the bridge accepts', () => {
  const b = C.noticeAddBody(notice());
  assert.deepStrictEqual(b, { add: { kind: 'event', title: notice().title,
    starts: '2026-10-01T10:00:00+00:00', ends: '2026-10-15T07:00:00+00:00', url: NURL } });
  assert.strictEqual(C.validPost('/api/events', b), true);
  for (const bad of [null, {}, notice({ title: '' }), notice({ ends: 'x' }),
    notice({ starts: '2026-10-20T00:00:00Z' })]) {
    assert.strictEqual(C.noticeAddBody(bad), null, JSON.stringify(bad));
  }
});

test('noticeDismissBody + bridge guard: dismiss by notice number only', () => {
  assert.deepStrictEqual(C.noticeDismissBody(notice()), { dismiss_notice: 10656 });
  assert.strictEqual(C.validPost('/api/events', { dismiss_notice: 10656 }), true);
  for (const bad of [0, -1, 1.5, '10656', null, true, 1e9]) {
    assert.strictEqual(C.validEventsBody({ dismiss_notice: bad }), false, String(bad));
  }
  assert.strictEqual(C.noticeDismissBody({ group_no: 'x' }), null);
});

test('noticeEndText: maintenance-relative ends say so and ask to verify', () => {
  assert.strictEqual(C.noticeEndText(notice(), { zone: 'utc' }), '2026-10-15 before maint. (~07:00 local, verify)');
  assert.strictEqual(C.noticeEndText(notice(), { zone: 'pt' }), '2026-10-15 before maint. (~00:00 local, verify)');
  assert.strictEqual(C.noticeEndText(notice({ maint_relative: false, ends_edge: null,
    ends: '2026-10-21T23:59:00+00:00' }), { zone: 'utc' }), '2026-10-21 23:59');
  assert.strictEqual(C.noticeEndText(null), '');
  assert.strictEqual(C.noticeEndText(notice({ ends: 'x' })), '');
});

test('noticeStatus: event check line, robots reason when off', () => {
  assert.deepStrictEqual(C.noticeStatus({ status: 'off', robots: 'disallow' }), { status: 'off', text: 'event check: off - robots.txt disallow' });
  assert.strictEqual(C.noticeStatus({ status: 'ok' }).text, 'event check: checked');
  assert.strictEqual(C.noticeStatus({ status: 'weird' }).status, 'none');
  assert.strictEqual(C.noticeStatus(null).status, 'none');
});

test('events.js: Suggested events card uses the helpers; add / dismiss only on click', () => {
  const src = read('dashboard/events.js');
  for (const f of ['noticeRows', 'noticeAddBody', 'noticeDismissBody', 'noticeEndText', 'noticeStatus']) {
    assert.match(src, new RegExp('C\\.' + f + '\\('), f);
  }
  assert.match(src, /Suggested events/);
  for (const f of ['C.noticeAddBody(', 'C.noticeDismissBody(']) {
    const i = src.indexOf(f);
    assert.ok(src.lastIndexOf("addEventListener('click'", i) > src.lastIndexOf('function drawNotices', i), f + ' inside a click handler');
  }
});

test('dashboard loads and mounts events.js; CSP unchanged', () => {
  const html = read('dashboard/index.html');
  assert.match(html, /connect-src http:\/\/127\.0\.0\.1:8940;/);
  const iCore = html.indexOf('ewcore.js');
  const iEv = html.indexOf('<script src="events.js"></script>');
  const iDash = html.indexOf('dashboard.js');
  assert.ok(iCore >= 0 && iEv > iCore && iDash > iEv, 'script order ewcore, events, dashboard');
  const dash = read('dashboard/dashboard.js');
  assert.match(dash, /EWEvents\.mount\(/);
  assert.match(dash, /EWEvents\.show\(/);
  assert.match(read('shared/ew.css'), /\.ew-events/);
});

test('overlay: GET-only eventsSoon widget, opt-in from the query', () => {
  const src = read('overlay/overlay.js');
  assert.match(src, /\/api\/events/);
  assert.doesNotMatch(src, /POST|ewApi|method:/);
  assert.match(src, /W\.eventsSoon/);
  assert.match(src, /C\.soonestEvent\(/);
  assert.match(src, /C\.fmtLeft\(/);
  const html = read('overlay/index.html');
  assert.match(html, /id="ov-events-row"[^>]*hidden/);
  assert.match(html, /id="ov-events"/);
  assert.match(html, /id="ov-events-name"/);
});
