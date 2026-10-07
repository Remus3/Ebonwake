'use strict';
// Plan 032: world boss overlay widget, Today / Home card, bossSoon rule.
// Pure formatters in ewcore.js over the GET /api/bosses body (plan 031), plus
// static guards on the overlay, the card module and the bridge. No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const APP = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(APP, p), 'utf8');
const NOW = Date.parse('2026-10-05T19:10:00Z'); // Mon 12:10 PDT
const MIN = 60000;

function spawn(bosses, atIso, day) {
  return { bosses: bosses, at_utc: atIso, at_pt: '', day: day || '2026-10-05', despawn_min: 30 };
}

function view(o) {
  return Object.assign({
    now: '2026-10-05T19:10:00+00:00', tz: 'PT',
    next: [
      spawn(['Sangoon', 'Karanda'], '2026-10-06T00:00:00+00:00'),
      spawn(['Golden Pig King', 'Kutum'], '2026-10-06T03:15:00+00:00'),
      spawn(['Garmoth'], '2026-10-06T04:15:00+00:00')
    ],
    today: { day: '2026-10-05', remaining: [], slots: [
      Object.assign(spawn(['Golden Pig King', 'Kzarka'], '2026-10-05T07:00:00+00:00'), { up: false, past: true }),
      Object.assign(spawn(['Uturi', 'Nouver'], '2026-10-05T17:00:00+00:00'), { up: false, past: true }),
      Object.assign(spawn(['Garmoth'], '2026-10-05T19:00:00+00:00'), { up: true, past: false }),
      Object.assign(spawn(['Sangoon', 'Karanda'], '2026-10-06T00:00:00+00:00'), { up: false, past: false })
    ] },
    looted: {},
    garmoth: { looted: 1, cap: 3, week_reset: '2026-10-08T00:00:00+00:00' }
  }, o || {});
}

// ---- formatter ----

test('fmtBossRow: names joined, countdown from at_utc, key = at_utc', () => {
  const r = C.fmtBossRow(view().next[0], view(), NOW);
  assert.strictEqual(r.key, '2026-10-06T00:00:00+00:00');
  assert.strictEqual(r.text, 'Sangoon + Karanda');
  assert.strictEqual(r.left_s, 4 * 3600 + 50 * 60);
  assert.strictEqual(r.left, '4h 50m');
  assert.strictEqual(r.done, false);
  assert.deepStrictEqual(r.names.map((n) => n.looted), [false, false]);
});

test('fmtBossRow: Garmoth shows n/3; at the weekly cap it greys', () => {
  const v = view();
  const g = C.fmtBossRow(v.next[2], v, NOW);
  assert.strictEqual(g.text, 'Garmoth 1/3');
  assert.strictEqual(g.done, false);
  const capped = view({ garmoth: { looted: 3, cap: 3, week_reset: '' } });
  const c = C.fmtBossRow(capped.next[2], capped, NOW);
  assert.strictEqual(c.text, 'Garmoth 3/3');
  assert.strictEqual(c.done, true);
  // No garmoth block (old server): plain name.
  assert.strictEqual(C.fmtBossRow(v.next[2], view({ garmoth: null }), NOW).text, 'Garmoth');
});

test('fmtBossRow: bosses ticked looted on the spawn day are greyed; all looted -> done', () => {
  const v = view({ looted: { '2026-10-05': ['Karanda'] } });
  const r = C.fmtBossRow(v.next[0], v, NOW);
  assert.deepStrictEqual(r.names.map((n) => [n.name, n.looted]), [['Sangoon', false], ['Karanda', true]]);
  assert.strictEqual(r.done, false);
  const all = view({ looted: { '2026-10-05': ['Karanda', 'Sangoon'] } });
  assert.strictEqual(C.fmtBossRow(all.next[0], all, NOW).done, true);
  // A loot on another day does not grey this spawn.
  const other = view({ looted: { '2026-10-04': ['Karanda', 'Sangoon'] } });
  assert.strictEqual(C.fmtBossRow(other.next[0], other, NOW).done, false);
});

test('fmtBossRow: junk spawn -> null', () => {
  [null, {}, { bosses: [], at_utc: '2026-10-06T00:00:00+00:00' }, { bosses: ['X'], at_utc: 'soon' },
    { bosses: [5], at_utc: '2026-10-06T00:00:00+00:00' }].forEach((s) => {
    assert.strictEqual(C.fmtBossRow(s, view(), NOW), null, JSON.stringify(s));
  });
});

test('bossRows: next n, countdown wraps to the following spawn once one passes', () => {
  const v = view();
  assert.deepStrictEqual(C.bossRows(v, NOW, 2).map((r) => r.text), ['Sangoon + Karanda', 'Golden Pig King + Kutum']);
  const at = Date.parse('2026-10-06T00:00:00Z');
  assert.strictEqual(C.bossRows(v, at - 1000, 1)[0].left, '0m 01s');
  const after = C.bossRows(v, at, 3);
  assert.deepStrictEqual(after.map((r) => r.text), ['Golden Pig King + Kutum', 'Garmoth 1/3']);
  assert.strictEqual(after[0].left, '3h 15m');
  assert.deepStrictEqual(C.bossRows(null, NOW, 3), []);
  assert.deepStrictEqual(C.bossRows({ next: 'x' }, NOW, 3), []);
});

test('bossRows: a spawn across the PT day / week boundary counts down straight through', () => {
  // Sun 23:50 PDT -> Mon 00:00 PDT spawn.
  const v = view({ next: [spawn(['Kzarka'], '2026-10-05T07:00:00+00:00', '2026-10-05')] });
  const r = C.bossRows(v, Date.parse('2026-10-05T06:50:00Z'), 1)[0];
  assert.strictEqual(r.left, '10m 00s');
  assert.strictEqual(r.day, '2026-10-05');
});

test('bossTicks: one per boss already spawned today, looted flag, slots first', () => {
  const v = view({ looted: { '2026-10-05': ['Kzarka'] } });
  const t = C.bossTicks(v, NOW);
  assert.deepStrictEqual(t.map((x) => [x.name, x.looted]), [
    ['Golden Pig King', false], ['Kzarka', true], ['Uturi', false], ['Nouver', false], ['Garmoth', false]]);
  assert.ok(t.every((x) => x.day === '2026-10-05'));
  // Old server without slots: the up rows of `remaining`.
  const old = view({ today: { day: '2026-10-05', remaining: [Object.assign(spawn(['Garmoth'], '2026-10-05T19:00:00+00:00'), { up: true })] } });
  assert.deepStrictEqual(C.bossTicks(old, NOW).map((x) => x.name), ['Garmoth']);
  assert.deepStrictEqual(C.bossTicks(null, NOW), []);
});

test('bossGarmothText: week count, empty without a block', () => {
  assert.strictEqual(C.bossGarmothText(view()), 'Garmoth 1/3 this week');
  assert.strictEqual(C.bossGarmothText(view({ garmoth: null })), '');
});

// ---- widget + bridge ----

test('worldBoss widget: opt-in, default off, query round-trip', () => {
  assert.strictEqual(C.widgetsQuery({}).worldBoss, '0');
  assert.strictEqual(C.overlayWidgets({}).worldBoss, false);
  assert.strictEqual(C.overlayWidgets({ overlay: { widgets: { worldBoss: true } } }).worldBoss, true);
  assert.strictEqual(C.widgetsFromQuery('').worldBoss, false);
  const q = new URLSearchParams(C.widgetsQuery({ worldBoss: true })).toString();
  assert.strictEqual(C.widgetsFromQuery('?' + q).worldBoss, true);
  const ex = JSON.parse(fs.readFileSync(path.join(APP, '..', 'config', 'local.example.json'), 'utf8'));
  assert.strictEqual(ex.overlay.widgets.worldBoss, false);
  assert.strictEqual(ex.notify.bossSoon, false);
});

test('validPost: /api/bosses tick|untick {boss, day} only', () => {
  assert.ok(C.POST_ROUTES.indexOf('/api/bosses') >= 0);
  assert.strictEqual(C.validPost('/api/bosses', { tick: { boss: 'Kzarka', day: '2026-10-05' } }), true);
  assert.strictEqual(C.validPost('/api/bosses', { untick: { boss: 'Golden Pig King', day: '2026-10-05' } }), true);
  [{}, { tick: 'Kzarka' }, { tick: { boss: 'Kzarka' } }, { tick: { boss: 'Kzarka', day: '2026-10-05', x: 1 } },
    { tick: { boss: '', day: '2026-10-05' } }, { tick: { boss: 'Kzarka', day: '10/05/2026' } },
    { tick: { boss: 'K'.repeat(41), day: '2026-10-05' } }, { tick: { boss: 'K' + String.fromCharCode(0xe9), day: '2026-10-05' } },
    { tick: { boss: 'A', day: '2026-10-05' }, untick: { boss: 'A', day: '2026-10-05' } },
    { delete: { boss: 'A', day: '2026-10-05' } }].forEach((b) => {
    assert.strictEqual(C.validPost('/api/bosses', b), false, JSON.stringify(b));
  });
  assert.strictEqual(C.postToast('/api/bosses', { ok: true }).text, 'World bosses saved');
});

test('overlay: worldBoss from GET /api/bosses, opt-in rows, no POST', () => {
  const src = read('overlay/overlay.js');
  assert.match(src, /getJSON\('\/api\/bosses'\)/);
  assert.match(src, /C\.bossRows\(/);
  assert.match(src, /W\.worldBoss/);
  assert.doesNotMatch(src, /method:\s*'POST'|ewApi|innerHTML/);
  const html = read('overlay/index.html');
  assert.match(html, /id="ov-boss-row" hidden/);
  assert.match(html, /id="ov-boss-next-row" hidden/);
});

test('Today card: bosses.js mounted by today.js, ticks via the bridge, safe DOM', () => {
  const src = read('dashboard/bosses.js');
  assert.match(src, /window\.EWBosses\s*=/);
  assert.match(src, /C\.bossRows\(/);
  assert.match(src, /C\.bossTicks\(/);
  assert.match(src, /C\.bossGarmothText\(/);
  assert.match(src, /\.post\('\/api\/bosses'/);
  assert.doesNotMatch(src, /innerHTML|outerHTML|insertAdjacentHTML|document\.write|method:\s*'POST'/);
  assert.match(read('dashboard/today.js'), /EWBosses\.mount\(/);
  const html = read('dashboard/index.html');
  const iB = html.indexOf('<script src="bosses.js"></script>');
  assert.ok(iB > html.indexOf('ewcore.js') && iB < html.indexOf('<script src="today.js"></script>'));
  assert.match(read('preload.js'), /\/api\/bosses/);
});

// Plan 076: the bosses fold into the Home Timers card (Garmoth n/3 as its meta).
test('composeNow: next 3 bosses in Timers (Garmoth meta), absent without the payload', () => {
  const timers = (v) => C.composeNow(v, NOW).cards.filter((c) => c.id === 'timers')[0];
  const b = timers({ bosses: view() });
  assert.strictEqual(b.tab, 'today');
  assert.strictEqual(b.meta, 'Garmoth 1/3 this week');
  const rows = b.rows.filter((r) => r.source === 'boss');
  assert.deepStrictEqual(rows.map((r) => [r.label, r.value]), [
    ['Sangoon + Karanda', '4h 50m'], ['Golden Pig King + Kutum', '8h 05m'], ['Garmoth', '9h 05m']]);
  assert.ok(b.rows.every((r) => r.tick === null), 'Home stays read-only for bosses');
  const looted = timers({ bosses: view({ garmoth: { looted: 3, cap: 3 } }) });
  assert.strictEqual(looted.rows.filter((r) => r.source === 'boss')[2].cls, 'ew-stale');
  assert.ok(!timers({}).rows.some((r) => r.source === 'boss'));
  assert.strictEqual(timers({}).meta, '');
  assert.ok(read('dashboard/home.js').indexOf("'/api/bosses'") >= 0);
});

// ---- bossSoon rule ----

const ON = { bossSoon: true };
const at0 = Date.parse('2026-10-06T00:00:00Z');

test('bossSoon: default off; 15 then 5 min before, once each, keyed per spawn', () => {
  assert.strictEqual(C.notifyPrefs({}).bossSoon, false);
  assert.ok(C.NOTIFY_RULES.some((r) => r.name === 'bossSoon'));
  const snap = { at: NOW, bosses: view() };
  assert.deepStrictEqual(C.notifyRules(null, snap, at0 - 16 * MIN, ON), []);
  const h15 = C.notifyRules(null, snap, at0 - 15 * MIN, ON);
  assert.deepStrictEqual(h15.map((h) => h.key), ['bossSoon:2026-10-06T00:00:00+00:00:15']);
  assert.strictEqual(h15[0].rule, 'bossSoon');
  assert.strictEqual(h15[0].title, 'World boss in 15m: Sangoon + Karanda');
  const h12 = C.notifyRules(null, snap, at0 - 12 * MIN, ON);
  assert.deepStrictEqual(h12.map((h) => h.key), ['bossSoon:2026-10-06T00:00:00+00:00:15'], 'same key: the ledger dedupes');
  const h5 = C.notifyRules(null, snap, at0 - 5 * MIN, ON);
  assert.deepStrictEqual(h5.map((h) => h.key), ['bossSoon:2026-10-06T00:00:00+00:00:5']);
  assert.strictEqual(h5[0].title, 'World boss in 5m: Sangoon + Karanda');
  assert.deepStrictEqual(C.notifyRules(null, snap, at0, ON), [], 'spawned: quiet');
  assert.deepStrictEqual(C.notifyRules(null, snap, at0 - 5 * MIN, {}), [], 'off by default');
});

test('bossSoon: a fully looted (or Garmoth-capped) spawn stays quiet; junk payload quiet', () => {
  const g = Date.parse('2026-10-06T04:15:00Z');
  const capped = { at: NOW, bosses: view({ garmoth: { looted: 3, cap: 3 } }) };
  assert.deepStrictEqual(C.notifyRules(null, capped, g - 5 * MIN, ON), []);
  const open = { at: NOW, bosses: view() };
  assert.strictEqual(C.notifyRules(null, open, g - 5 * MIN, ON).length, 1);
  assert.deepStrictEqual(C.notifyRules(null, { at: NOW, bosses: null }, g, ON), []);
  assert.deepStrictEqual(C.notifyRules(null, { at: NOW, bosses: { next: 'x' } }, g, ON), []);
  assert.ok(read('dashboard/toast.js').indexOf("'/api/bosses'") >= 0);
});
