'use strict';
// Plan 067: context-aware overlay - pure helpers in ewcore plus the in-place
// re-render of overlay.js, run in a vm against a minimal fake DOM (no Electron).
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const C = require('../shared/ewcore');

const APP = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(APP, p), 'utf8');

const IN_GAME = { context: 'in_game', active: ['in_game'], widgets: ['grindSession', 'grindBuff', 'leveling', 'eventsSoon'], hidden: false, auto: true, maint_at: null };
const BOSS = { context: 'boss_soon', active: ['boss_soon', 'in_game'], widgets: ['worldBoss', 'grindSession', 'grindBuff', 'leveling'], hidden: false, auto: true, maint_at: null };
const CLOSED = { context: 'closed', active: ['closed'], widgets: [], hidden: true, auto: true, maint_at: null };

// ---- pure helpers ----

test('overlayContext normalises a payload and refuses junk', () => {
  const c = C.overlayContext(BOSS);
  assert.strictEqual(c.context, 'boss_soon');
  assert.deepStrictEqual(c.order, ['worldBoss', 'grindSession', 'grindBuff', 'leveling']);
  assert.strictEqual(c.widgets.worldBoss, true);
  assert.strictEqual(c.widgets.dice, false);
  assert.strictEqual(c.widgets.maintenance, false);
  assert.deepStrictEqual(Object.keys(c.widgets).sort(), C.OVERLAY_WIDGETS.slice().sort());
  assert.strictEqual(c.hidden, false);
  assert.strictEqual(c.maintAt, null);
  for (const bad of [null, [], {}, { context: 1, widgets: [] }, { context: 'x' }]) {
    assert.strictEqual(C.overlayContext(bad), null);
  }
  const odd = C.overlayContext({ context: 'x', widgets: ['dice', 'dice', 'nope', 3], maint_at: '2026-10-08T07:00:00+00:00', hidden: 'yes' });
  assert.deepStrictEqual(odd.order, ['dice']);
  assert.strictEqual(odd.hidden, false);
  assert.strictEqual(odd.maintAt, Date.parse('2026-10-08T07:00:00Z'));
});

test('widgetsTurnedOn lists only newly shown widgets', () => {
  const a = C.overlayContext(IN_GAME).widgets;
  const b = C.overlayContext(BOSS).widgets;
  assert.deepStrictEqual(C.widgetsTurnedOn(a, b), ['worldBoss']);
  assert.deepStrictEqual(C.widgetsTurnedOn(b, a), ['eventsSoon']);
  assert.deepStrictEqual(C.widgetsTurnedOn(null, C.overlayContext(CLOSED).widgets), []);
});

test('overlayRowOrder: shown widgets first in context order, every row once', () => {
  const ids = C.overlayRowOrder(['worldBoss', 'grindSession']);
  assert.deepStrictEqual(ids.slice(0, 3), ['ov-boss-row', 'ov-boss-next-row', 'ov-grind-row']);
  const all = [].concat(...Object.values(C.OVERLAY_ROWS));
  assert.deepStrictEqual(ids.slice().sort(), all.slice().sort());
  assert.deepStrictEqual(C.overlayRowOrder(null).slice().sort(), all.slice().sort());
  const html = read('overlay/index.html');
  for (const id of all) assert.match(html, new RegExp('id="' + id + '"[^>]*hidden'), id);
});

test('maintLine counts down, then says now', () => {
  const at = Date.parse('2026-10-08T07:00:00Z');
  assert.strictEqual(C.maintLine(at, at - 42 * 60000), 'in ' + C.fmtLeft(42 * 60));
  assert.strictEqual(C.maintLine(at, at), 'now');
  assert.strictEqual(C.maintLine(null, at), '');
});

test('settings: auto, idle_min and one pin/block mode per widget; context keys apply live', () => {
  assert.ok(C.SETTINGS_KEYS.includes('overlay.auto'));
  assert.ok(C.SETTINGS_KEYS.includes('overlay.idle_min'));
  for (const w of C.OVERLAY_WIDGETS.filter((k) => k !== 'maintenance')) {
    assert.ok(C.SETTINGS_KEYS.includes('overlay.mode.' + w), w);
  }
  assert.ok(C.validSettingsBody({ set: { 'overlay.mode.dice': 'pin', 'overlay.auto': false, 'overlay.idle_min': 30 } }));
  assert.ok(!C.validSettingsBody({ set: { 'overlay.mode.dice': 'always' } }));
  assert.ok(!C.validSettingsBody({ set: { 'overlay.idle_min': 4 } }));
  assert.ok(!C.validSettingsBody({ set: { 'overlay.mode.maintenance': 'pin' } }));
  assert.deepStrictEqual(C.settingsEffects(['overlay.mode.dice', 'overlay.auto', 'overlay.idle_min']),
    { overlay: false, shell: false, theme: false });
  assert.strictEqual(C.settingsEffects(['overlay.mode.dice', 'overlay.scale']).overlay, true);
});

// ---- overlay.js in a vm: in-place re-render ----

class El {
  constructor(id, hidden) {
    this.id = id;
    this.hidden = hidden;
    this.textContent = '';
    this.className = '';
    this.title = '';
    this.children = [];
    this.parent = null;
    this.style = { setProperty() {} };
  }
  get firstChild() { return this.children[0] || null; }
  appendChild(c) {
    if (c.parent) c.parent.children.splice(c.parent.children.indexOf(c), 1);
    c.parent = this;
    this.children.push(c);
    return c;
  }
  removeChild(c) {
    this.children.splice(this.children.indexOf(c), 1);
    c.parent = null;
    return c;
  }
}

function rig() {
  const html = read('overlay/index.html');
  const els = {};
  for (const m of html.matchAll(/<(\w+)[^>]*\bid="([^"]+)"([^>]*)>/g)) {
    els[m[2]] = new El(m[2], /\bhidden\b/.test(m[0]));
  }
  const box = els['ov-widgets'];
  const order = [...html.matchAll(/id="(ov-[a-z-]+-row)"/g)].map((m) => m[1]);
  for (const id of order) if ([].concat(...Object.values(C.OVERLAY_ROWS)).includes(id)) box.appendChild(els[id]);
  const calls = { fetch: [], size: 0, sources: [] };
  const bodies = {
    '/api/today': { items: [], dice: null },
    '/api/grind': { active: null, spots: [], buffs: [] },
    '/api/events': { items: [] },
    '/api/leveling': null,
    '/api/progress': { tracks: [] },
    '/api/bosses': { next: [] },
    '/api/market/watch': { items: [] },
    '/api/game': { state: 'logged_in' },
    '/api/overlay/context': IN_GAME
  };
  function FakeES(url) {
    this.url = url;
    this.listeners = {};
    calls.sources.push(this);
  }
  FakeES.prototype.addEventListener = function (n, fn) { this.listeners[n] = fn; };
  FakeES.prototype.close = function () {};
  const sandbox = {
    window: {
      location: { search: '' },
      EWCore: C,
      ewOverlay: { reportSize() { calls.size += 1; } }
    },
    document: {
      getElementById: (id) => els[id] || null,
      createElement: (t) => new El(null, false),
      createTextNode: (t) => { const e = new El(null, false); e.textContent = t; return e; },
      body: { getBoundingClientRect: () => ({ height: 100 + calls.size }) },
      documentElement: { style: { setProperty() {} } }
    },
    fetch: (url, opts) => {
      calls.fetch.push({ url, opts });
      const p = url.slice(C.SERVER.length);
      return Promise.resolve({ ok: true, json: () => Promise.resolve(bodies[p]) });
    },
    EventSource: FakeES,
    setInterval: () => 0,
    setTimeout: () => 0,
    Date,
    JSON,
    Math,
    Object,
    Promise,
    URLSearchParams
  };
  vm.createContext(sandbox);
  vm.runInContext(read('overlay/overlay.js'), sandbox);
  return { els, calls, box, src: calls.sources[0] };
}

const settle = () => new Promise((r) => setImmediate(r));

function shownWidgetRows(r) {
  return r.box.children.filter((e) => !e.hidden).map((e) => e.id);
}

test('overlay applies contexts in place: closed -> in_game -> boss_soon -> in_game -> closed', async () => {
  const r = rig();
  await settle();
  await settle();
  const panel = r.els['ov-panel'];
  const push = (d) => r.src.listeners.overlay_context({ data: JSON.stringify(d) });
  const seen = [];
  const snap = () => seen.push({ hidden: panel.hidden, first: r.box.children[0].id,
    order: r.box.children.slice(0, 3).map((e) => e.id), rows: shownWidgetRows(r) });

  push(CLOSED);
  snap();
  push(IN_GAME);
  await settle();
  snap();
  push(BOSS);
  await settle();
  snap();
  push(IN_GAME);
  await settle();
  snap();
  push(CLOSED);
  snap();

  assert.deepStrictEqual(seen.map((s) => s.hidden), [true, false, false, false, true]);
  assert.strictEqual(seen[1].first, 'ov-grind-row');
  assert.strictEqual(seen[2].first, 'ov-boss-row');
  assert.deepStrictEqual(seen[2].order, ['ov-boss-row', 'ov-boss-next-row', 'ov-grind-row']);
  assert.strictEqual(seen[3].first, 'ov-grind-row');
  // Only the context's rows can show (leveling reads 'offline' here, so it shows).
  const allowed = (d) => [].concat(...d.widgets.map((w) => C.OVERLAY_ROWS[w]));
  [CLOSED, IN_GAME, BOSS, IN_GAME, CLOSED].forEach((d, i) => {
    seen[i].rows.forEach((id) => assert.ok(allowed(d).includes(id), i + ':' + id));
  });
  assert.ok(seen[1].rows.includes('ov-leveling-row') && seen[2].rows.includes('ov-leveling-row'));
  assert.deepStrictEqual(seen[4].rows, []);
  // Same DOM nodes throughout: moved, never rebuilt; the window is never recreated.
  assert.strictEqual(r.box.children.length, [].concat(...Object.values(C.OVERLAY_ROWS)).length);
  assert.strictEqual(r.calls.sources.length, 1);
  // Read-only GETs only; a boss context loads the boss table at once.
  assert.ok(r.calls.fetch.every((f) => f.opts === undefined));
  assert.ok(r.calls.fetch.some((f) => f.url.endsWith('/api/bosses')));
  assert.ok(r.calls.fetch.some((f) => f.url.endsWith('/api/overlay/context')));
});

test('overlay: a widget turned off stays hidden after a late load', async () => {
  const r = rig();
  await settle();
  r.src.listeners.overlay_context({ data: JSON.stringify(BOSS) });
  r.src.listeners.overlay_context({ data: JSON.stringify(IN_GAME) });
  await settle();
  await settle();
  assert.strictEqual(r.els['ov-boss-row'].hidden, true);
  assert.strictEqual(r.els['ov-boss-next-row'].hidden, true);
});

test('overlay: maintenance context shows the countdown line first', async () => {
  const r = rig();
  await settle();
  const at = new Date(Date.now() + 30 * 60000).toISOString();
  r.src.listeners.overlay_context({ data: JSON.stringify({ context: 'maint_soon', active: ['maint_soon', 'in_game'], widgets: ['maintenance', 'eventsSoon'], hidden: false, auto: true, maint_at: at }) });
  assert.strictEqual(r.box.children[0].id, 'ov-maint-row');
  assert.strictEqual(r.els['ov-maint-row'].hidden, false);
  assert.match(r.els['ov-maint'].textContent, /^in /);
});

test('overlay: a GET in flight before an SSE push never rolls the context back', async () => {
  const r = rig(); // the start-up GET answers IN_GAME
  r.src.listeners.overlay_context({ data: JSON.stringify(BOSS) }); // pushed before it lands
  await settle();
  await settle();
  assert.strictEqual(r.box.children[0].id, 'ov-boss-row');
});

test('overlay source: SSE overlay_context, GET context route, no recreate path', () => {
  const src = read('overlay/overlay.js');
  assert.match(src, /addEventListener\('overlay_context'/);
  assert.match(src, /getJSON\('\/api\/overlay\/context'\)/);
  assert.match(src, /C\.overlayContext\(/);
  assert.doesNotMatch(src, /POST|ewApi|method:|location\.reload|ipcRenderer/);
});
