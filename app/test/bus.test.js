'use strict';
// Plan 049: the shared SSE bus, hidden-tab poll pause and keyed reconcile
// (ewcore.js), plus the dashboard wiring that replaces per-module streams.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const C = require('../shared/ewcore');

const read = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');

// ---- fake DOM: just what reconcile touches ----
class Node {
  constructor(name) { this.name = name; this.parentNode = null; this.children = []; }
  get isConnected() { return true; }
  insertBefore(n, ref) {
    if (n.parentNode) n.parentNode.removeChild(n);
    const i = ref ? this.children.indexOf(ref) : -1;
    if (i < 0) this.children.push(n); else this.children.splice(i, 0, n);
    n.parentNode = this;
    this.moves = (this.moves || 0) + 1;
    if (Node.focused === n) Node.focused = null; // a detached node loses focus
    return n;
  }
  appendChild(n) { return this.insertBefore(n, null); }
  removeChild(n) {
    const i = this.children.indexOf(n);
    if (i >= 0) this.children.splice(i, 1);
    n.parentNode = null;
    if (Node.focused === n) Node.focused = null;
    return n;
  }
}
Node.focused = null;

function rows(...ids) { return ids.map((id) => ({ id: id, label: 'row ' + id })); }
let built = 0;
function render(r) { built++; const n = new Node(r.id); n.label = r.label; return n; }
const key = (r) => r.id;
const names = (c) => c.children.map((n) => n.name);

test('reconcile inserts rows in order on an empty container', () => {
  const c = new Node('list');
  const out = C.reconcile(c, rows('a', 'b', 'c'), key, render);
  assert.deepStrictEqual(names(c), ['a', 'b', 'c']);
  assert.deepStrictEqual(out, { added: 3, updated: 0, moved: 0, removed: 0 });
});

test('reconcile keeps unchanged nodes (identity) and focus', () => {
  const c = new Node('list');
  C.reconcile(c, rows('a', 'b', 'c'), key, render);
  const b = c.children[1];
  Node.focused = b;
  built = 0;
  const out = C.reconcile(c, rows('a', 'b', 'c'), key, render);
  assert.strictEqual(built, 0);
  assert.strictEqual(c.children[1], b);
  assert.strictEqual(Node.focused, b);
  assert.deepStrictEqual(out, { added: 0, updated: 0, moved: 0, removed: 0 });
});

test('reconcile inserts in the middle without touching neighbours', () => {
  const c = new Node('list');
  C.reconcile(c, rows('a', 'c'), key, render);
  const [a, cc] = c.children;
  Node.focused = cc;
  C.reconcile(c, rows('a', 'b', 'c'), key, render);
  assert.deepStrictEqual(names(c), ['a', 'b', 'c']);
  assert.strictEqual(c.children[0], a);
  assert.strictEqual(c.children[2], cc);
  assert.strictEqual(Node.focused, cc);
});

test('reconcile moves a row and removes a gone one', () => {
  const c = new Node('list');
  C.reconcile(c, rows('a', 'b', 'c', 'd'), key, render);
  const a = c.children[0];
  const d = c.children[3];
  Node.focused = d;
  const out = C.reconcile(c, rows('c', 'a', 'd'), key, render);
  assert.deepStrictEqual(names(c), ['c', 'a', 'd']);
  assert.strictEqual(c.children[1], a);
  assert.strictEqual(c.children[2], d);
  assert.strictEqual(Node.focused, d, 'a row that kept its relative place is not re-inserted');
  assert.strictEqual(out.removed, 1);
  assert.strictEqual(out.moved, 1);
});

test('reconcile rebuilds a changed row in place and drops non-keyed nodes', () => {
  const c = new Node('list');
  c.appendChild(new Node('loading...'));
  C.reconcile(c, rows('a', 'b'), key, render);
  assert.deepStrictEqual(names(c), ['a', 'b']);
  const a = c.children[0];
  const next = rows('a', 'b');
  next[1].label = 'changed';
  const out = C.reconcile(c, next, key, render);
  assert.strictEqual(c.children[0], a);
  assert.strictEqual(c.children[1].label, 'changed');
  assert.deepStrictEqual(out, { added: 0, updated: 1, moved: 0, removed: 0 });
});

test('reconcile empties the container for no rows', () => {
  const c = new Node('list');
  C.reconcile(c, rows('a', 'b'), key, render);
  C.reconcile(c, [], key, render);
  assert.deepStrictEqual(names(c), []);
});

test('reconcile duplicate keys keep the first row only', () => {
  const c = new Node('list');
  C.reconcile(c, [{ id: 'a', v: 1 }, { id: 'a', v: 2 }], key, render);
  assert.deepStrictEqual(names(c), ['a']);
});

// ---- bus ----

test('bus delivers to every listener of a domain only', () => {
  const bus = C.createBus();
  const got = [];
  bus.on('today', (d) => got.push(['t1', d]));
  bus.on('today', (d) => got.push(['t2', d]));
  bus.on('grind', (d) => got.push(['g', d]));
  bus.emit('today', 'x');
  assert.deepStrictEqual(got, [['t1', 'x'], ['t2', 'x']]);
  assert.deepStrictEqual(bus.domains().sort(), ['grind', 'today']);
});

test('bus off unsubscribes and a throwing listener does not stop the rest', () => {
  const bus = C.createBus();
  let n = 0;
  const off = bus.on('market', () => { n++; });
  bus.on('market', () => { throw new Error('boom'); });
  bus.on('market', () => { n += 10; });
  bus.emit('market');
  off();
  bus.emit('market');
  assert.strictEqual(n, 21);
});

test('bus.on ignores a non-function or empty domain', () => {
  const bus = C.createBus();
  assert.strictEqual(typeof bus.on('', () => {}), 'function');
  assert.strictEqual(typeof bus.on('x', null), 'function');
  assert.deepStrictEqual(bus.domains(), []);
});

// ---- hidden-tab pause ----

test('pollPaused: hidden window or inactive panel pauses, active panel polls', () => {
  const panel = { classList: { contains: (c) => c === 'ew-panel' || panel.on && c === 'active' }, on: true };
  const node = { closest: (sel) => (sel === '.ew-panel' ? panel : null) };
  assert.strictEqual(C.pollPaused(node, { hidden: false }), false);
  assert.strictEqual(C.pollPaused(node, { hidden: true }), true);
  panel.on = false;
  assert.strictEqual(C.pollPaused(node, { hidden: false }), true);
  assert.strictEqual(C.pollPaused(null, { hidden: false }), false, 'unmounted: never block');
});

// ---- wiring (source checks; no Electron in tests) ----

test('dashboard owns the only EventSource and publishes window.EWBus', () => {
  const d = read('dashboard/dashboard.js');
  assert.match(d, /window\.EWBus = C\.createBus\(\)/);
  assert.match(d, /new EventSource\(/);
  assert.match(d, /visibilitychange/);
  for (const f of fs.readdirSync(path.join(__dirname, '..', 'dashboard'))) {
    if (f === 'dashboard.js' || !f.endsWith('.js')) continue;
    assert.doesNotMatch(read('dashboard/' + f), /new EventSource/, f + ' opens its own stream');
  }
});

test('modules refresh on their domain event', () => {
  const want = { today: ['today'], grind: ['grind'], game: ['game'], leveling: ['leveling'], market: ['market'] };
  for (const [mod, doms] of Object.entries(want)) {
    const src = read('dashboard/' + mod + '.js');
    for (const dom of doms) assert.match(src, new RegExp("\\.on\\('" + dom + "'"), mod + ' listens to ' + dom);
  }
});

test('game card drops the 2 s poll', () => {
  const src = read('dashboard/game.js');
  assert.doesNotMatch(src, /POLL_MS = 2000/);
});

test('polled modules pause while their tab is hidden', () => {
  for (const mod of ['today', 'grind', 'game', 'leveling', 'market', 'progress', 'events',
    'deadeye', 'pets', 'inventory', 'bosses']) {
    assert.match(read('dashboard/' + mod + '.js'), /C\.pollPaused\(/, mod);
  }
});

test('per-GET values stay out of row signatures (refute r1)', () => {
  // freshness.age_s is recomputed on every GET: in the signature it would
  // rebuild every watchlist row each poll and drop focus.
  const mk = read('dashboard/market.js');
  assert.match(mk, /delete rest\.freshness/);
  assert.match(mk, /paintWatchRow\(/);
  assert.match(read('dashboard/grind.js'), /on: row\.left_s !== null/);
});

test('market watchlist, Today lists and buff list adopt reconcile', () => {
  assert.match(read('dashboard/market.js'), /C\.reconcile\(/);
  assert.match(read('dashboard/today.js'), /C\.reconcile\(/);
  assert.match(read('dashboard/grind.js'), /C\.reconcile\(/);
});
