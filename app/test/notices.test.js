'use strict';
// Plan 064: official notice auto-import - undo rows, the Steam backup hint,
// Hot Time auto windows on the Leveling card and the settings key. Pure
// helpers (ewcore.js) plus static guards; no network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const APP = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(APP, p), 'utf8');
const URL1 = 'https://www.naeu.playblackdesert.com/en-US/News/Detail?groupContentNo=20002&countryType=en-US';

function auto(o) {
  return Object.assign({ group_no: 20002, title: '[Event] Hot Time Returns', url: URL1,
    events: 1, hot: 1, at: '2026-10-06T12:00:00+00:00' }, o);
}

test('noticeAutoRows: valid ledger rows, one per notice, with a what-was-added line', () => {
  const rows = C.noticeAutoRows({ auto: [
    auto(), auto({ title: 'dup' }), auto({ group_no: 20003, events: 2, hot: 0 }),
    auto({ group_no: 1, url: 'javascript:alert(1)' }), auto({ group_no: 2, title: '' }),
    auto({ group_no: '7' }), null, 5
  ] });
  assert.deepStrictEqual(rows.map((r) => r.group_no), [20002, 20003]);
  assert.strictEqual(rows[0].text, 'auto-added 1 entry + 1 Hot Time window');
  assert.strictEqual(rows[1].text, 'auto-added 2 entries');
  assert.deepStrictEqual(C.noticeAutoRows(null), []);
  assert.deepStrictEqual(C.noticeAutoRows({ auto: 'x' }), []);
});

test('noticeUndoBody + bridge guard: undo by notice number only', () => {
  assert.deepStrictEqual(C.noticeUndoBody(auto()), { undo_notice: 20002 });
  assert.strictEqual(C.validPost('/api/events', { undo_notice: 20002 }), true);
  for (const bad of [0, -1, 1.5, '20002', null, true, 1e9]) {
    assert.strictEqual(C.validEventsBody({ undo_notice: bad }), false, String(bad));
  }
  assert.strictEqual(C.noticeUndoBody({ group_no: 'x' }), null);
});

test('noticeSteamHint: titles only, null when absent', () => {
  assert.strictEqual(C.noticeSteamHint(null), null);
  assert.strictEqual(C.noticeSteamHint({ steam_hint: null }), null);
  const h = C.noticeSteamHint({ steam_hint: { titles: ['Patch Notes', 5, '', 'x'.repeat(81)],
    url: 'https://store.steampowered.com/news/app/582660' } });
  assert.deepStrictEqual(h.titles, ['Patch Notes']);
  assert.match(h.text, /check official notices/);
  assert.strictEqual(C.noticeSteamHint({ steam_hint: { titles: [], url: 'javascript:x' } }).url, null);
});

test('normalizeLeveling keeps valid hot_auto rows; hotAutoText reads in the zone', () => {
  const w = { id: 'a3', start: '2026-10-08T12:30:00+00:00', end: '2026-10-22T07:00:00+00:00',
    label: 'Hot Time', bonus: 'Combat EXP +1,000%', pct: 1000, auto: true, group_no: 20002,
    source: URL1 };
  const d = C.normalizeLeveling({ milestones: [56], hot_windows: [], samples: [],
    hot_auto: [w, { id: 'a4', start: 'x', end: w.end, pct: 5 }, null] });
  assert.deepStrictEqual(d.hot_auto.map((x) => x.id), ['a3']);
  assert.deepStrictEqual(C.normalizeLeveling({ milestones: [] }).hot_auto, []);
  assert.strictEqual(C.hotAutoText(w, { zone: 'utc' }), '10-08 12:30 - 10-22 07:00 Combat EXP +1,000%');
  assert.strictEqual(C.hotAutoText({ start: 'x', end: w.end }, { zone: 'utc' }), '');
  assert.strictEqual(C.hotAutoText(null), '');
});

test('settings: notices.auto_add is not a setting (plan 080: fixed on)', () => {
  assert.ok(C.SETTINGS_KEYS.indexOf('notices.auto_add') < 0);
  assert.strictEqual(C.validSettingsBody({ set: { 'notices.auto_add': false } }), false);
});

test('static: the Events tab offers undo; the Leveling card lists auto windows', () => {
  const ev = read('dashboard/events.js');
  assert.match(ev, /noticeUndoBody/);
  assert.match(ev, /noticeSteamHint/);
  const lv = read('dashboard/leveling.js');
  assert.match(lv, /hot_auto/);
  assert.match(lv, /hotAutoText/);
});
