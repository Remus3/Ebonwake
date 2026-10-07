'use strict';
// Plan 047: density and accessibility. Pure helpers in ewcore.js (tabBadges,
// tabKey, orderTabs, countText) plus static guards on the shell, the CSS and
// the self-test card-scroll check. No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const APP = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(APP, p), 'utf8');
const T0 = Date.parse('2026-10-04T12:00:00Z');
const H = 3600 * 1000;
const iso = (ms) => new Date(ms).toISOString();
const IDS = ['home', 'today', 'market', 'progress', 'grind', 'events', 'deadeye', 'settings', 'system'];

function snaps() {
  return {
    today: { items: [
      { id: 'a', title: 'A', kind: 'daily', ticked_at: null },
      { id: 'b', title: 'B', kind: 'daily', ticked_at: iso(T0 - H) },
      { id: 'c', title: 'C', kind: 'daily', ticked_at: null },
      { id: 'd', title: 'D', kind: 'daily', ticked_at: null },
      { id: 'w', title: 'W', kind: 'weekly', ticked_at: null }
    ] },
    events: { items: [
      { id: 'e1', title: 'Soon', kind: 'event', ends: iso(T0 + 5 * H) },
      { id: 'e2', title: 'Later', kind: 'event', ends: iso(T0 + 200 * H) },
      { id: 'e3', title: 'Done', kind: 'event', ends: iso(T0 + 2 * H), done: true }
    ] },
    at: { today: T0, events: T0 }
  };
}

// ---- tabBadges ----

test('tabBadges: dailies left on Today, events ending within 48 h on Events', () => {
  assert.deepStrictEqual(C.tabBadges(snaps(), T0), { today: '3 left', events: '1 ending' });
});

test('tabBadges: zero counts are hidden, junk and missing snapshots give none', () => {
  const s = snaps();
  s.today.items.forEach((it) => { if (it.kind === 'daily') it.ticked_at = iso(T0 - H); });
  s.events.items = [];
  assert.deepStrictEqual(C.tabBadges(s, T0), {});
  assert.deepStrictEqual(C.tabBadges(null, T0), {});
  assert.deepStrictEqual(C.tabBadges({ today: { items: 'x' }, events: 7 }, T0), {});
  assert.deepStrictEqual(C.tabBadges({ today: { items: [null, 3] } }, T0), {});
});

test('tabBadges: the input is never mutated', () => {
  const s = snaps();
  const before = JSON.stringify(s);
  C.tabBadges(s, T0);
  assert.strictEqual(JSON.stringify(s), before);
});

// ---- tabKey ----

const k = (key, mods) => Object.assign({ key: key, ctrlKey: false, altKey: false, shiftKey: false, metaKey: false }, mods);

test('tabKey: Left/Right move with wrap, Home/End jump', () => {
  assert.strictEqual(C.tabKey(IDS, 'today', k('ArrowRight')), 'market');
  assert.strictEqual(C.tabKey(IDS, 'today', k('ArrowLeft')), 'home');
  assert.strictEqual(C.tabKey(IDS, 'home', k('ArrowLeft')), 'system');
  assert.strictEqual(C.tabKey(IDS, 'system', k('ArrowRight')), 'home');
  assert.strictEqual(C.tabKey(IDS, 'grind', k('Home')), 'home');
  assert.strictEqual(C.tabKey(IDS, 'grind', k('End')), 'system');
});

test('tabKey: Ctrl+1..9 picks the n-th tab; other chords and keys are ignored', () => {
  assert.strictEqual(C.tabKey(IDS, 'home', k('1', { ctrlKey: true })), 'home');
  assert.strictEqual(C.tabKey(IDS, 'home', k('3', { ctrlKey: true })), 'market');
  assert.strictEqual(C.tabKey(IDS, 'home', k('9', { ctrlKey: true })), 'system');
  assert.strictEqual(C.tabKey(IDS.slice(0, 3), 'home', k('5', { ctrlKey: true })), null);
  assert.strictEqual(C.tabKey(IDS, 'home', k('0', { ctrlKey: true })), null);
  assert.strictEqual(C.tabKey(IDS, 'home', k('3')), null, 'a bare digit types into inputs');
  assert.strictEqual(C.tabKey(IDS, 'home', k('3', { ctrlKey: true, altKey: true })), null);
  assert.strictEqual(C.tabKey(IDS, 'home', k('3', { ctrlKey: true, shiftKey: true })), null);
  assert.strictEqual(C.tabKey(IDS, 'home', k('ArrowRight', { ctrlKey: true })), null);
  assert.strictEqual(C.tabKey(IDS, 'home', k('ArrowRight', { altKey: true })), null);
  assert.strictEqual(C.tabKey(IDS, 'home', k('a')), null);
  assert.strictEqual(C.tabKey(IDS, 'nope', k('ArrowRight')), 'home');
  assert.strictEqual(C.tabKey([], 'home', k('ArrowRight')), null);
  assert.strictEqual(C.tabKey(IDS, 'home', null), null);
});

// ---- orderTabs ----

test('orderTabs: System moves last and is marked as the edge (icon) tab', () => {
  const tabs = [{ id: 'home', title: 'Home' }, { id: 'system', title: 'System' }, { id: 'settings', title: 'Settings' }];
  const out = C.orderTabs(tabs);
  assert.deepStrictEqual(out.map((t) => t.id), ['home', 'settings', 'system']);
  assert.strictEqual(out[2].edge, true);
  assert.ok(!out[0].edge && !out[1].edge);
  assert.strictEqual(tabs[1].edge, undefined, 'input not mutated');
  assert.deepStrictEqual(C.orderTabs([{ id: 'system', title: 'System' }]).map((t) => t.id), ['system']);
});

// ---- countText (zero states) ----

test('countText: hidden at zero total, n/total otherwise', () => {
  assert.strictEqual(C.countText(0, 0, 'done'), '');
  assert.strictEqual(C.countText(1, 3, 'done'), '1/3 done');
  assert.strictEqual(C.countText(2, 2), '2/2');
  assert.strictEqual(C.countText('x', 2), '');
});

test('composeNow: zero states hide counts and name the next action', () => {
  const snap = { today: { items: [] }, grind: { buffs: [], spots: [] }, market: { items: [] } };
  // Plan 076: empty cards leave the grid for the quiet line; their texts stay pure.
  assert.deepStrictEqual(C.composeNow(snap, T0).quiet.map((q) => q.title), ['dailies', 'buffs', 'grind', 'alerts']);
  const by = {};
  [C.nowDailies(snap.today, T0), C.nowSession(snap.grind, T0, T0), C.nowAlerts(snap.market)].forEach((c) => {
    by[c.id] = c;
  });
  assert.strictEqual(by.dailies.meta, '', 'no 0/0 done');
  assert.match(by.session.empty, /silver\/h/);
  assert.match(by.alerts.empty, /Market/);
});

test('plan 076: overlay Today line is quiet when daily and weekly totals are both 0', () => {
  assert.strictEqual(C.ovQuiet('daily 0/0  weekly 0/0'), true);
  assert.strictEqual(C.ovQuiet('daily 0/1  weekly 0/0'), false);
  assert.strictEqual(C.ovQuiet('daily 0/0  weekly 1/2'), false);
  assert.strictEqual(C.ovQuiet('daily 2/2  weekly 1/1'), false);
});

test('plan 076: self-test fails a clipped What now card', () => {
  const st = read('selftest.js');
  assert.match(st, /\[data-card=\\*"whatnow\\*"\]/);
  assert.match(st, /textOverflow/);
  assert.match(st, /wnClip/);
});

// ---- static guards ----

test('dashboard tabs: roles, aria-controls, roving tabindex, keyboard switching, badges', () => {
  const d = read('dashboard/dashboard.js');
  assert.match(d, /setAttribute\('role', 'tab'\)/);
  assert.match(d, /setAttribute\('role', 'tabpanel'\)/);
  assert.match(d, /setAttribute\('aria-controls', /);
  assert.match(d, /setAttribute\('aria-labelledby', /);
  assert.match(d, /\.tabIndex = /);
  assert.match(d, /C\.tabKey\(/);
  assert.match(d, /C\.orderTabs\(/);
  assert.match(d, /C\.tabBadges\(/);
  assert.match(d, /addEventListener\('keydown'/);
  assert.match(read('dashboard/home.js'), /snapshots: /);
  // Overlay never gets tab shortcuts (dashboard window only, no global hooks).
  assert.doesNotMatch(read('overlay/overlay.js'), /tabKey/);
});

test('css: content-sized rows, focus-visible, 20 px delete targets, text cues', () => {
  const css = read('shared/ew.css');
  assert.match(css, /grid-auto-rows:\s*min-content/);
  assert.match(css, /align-content:\s*start/);
  assert.match(css, /:focus-visible\s*\{[^}]*outline:/);
  assert.match(css, /\.ew-tx\s*\{[^}]*min-width:\s*20px;[^}]*min-height:\s*20px/);
  assert.match(css, /\.ew-brow\.on \.ew-mname::before\s*\{\s*content:\s*"ON "/);
  assert.match(css, /\.ew-erow\.soon \.ew-mprice::before/);
  assert.match(css, /\.ew-tab-edge\s*\{[^}]*margin-left:\s*auto/);
  assert.match(css, /\.ew-tab-badge/);
  assert.doesNotMatch(css, /:focus\s*\{[^}]*outline:\s*none/, 'no focus ring removal');
  assert.doesNotMatch(css, /grid-auto-rows:\s*minmax\(120px, 1fr\)/, 'cards no longer stretch');
});

test('delete buttons carry an aria-label; hot-list rows are buttons', () => {
  const dir = path.join(APP, 'dashboard');
  for (const f of fs.readdirSync(dir).filter((x) => x.endsWith('.js'))) {
    const src = read('dashboard/' + f);
    const re = /const (\w+) = el\('button', 'ew-tx', 'x'\);/g;
    let m;
    while ((m = re.exec(src))) {
      const tail = src.slice(m.index, m.index + 400);
      assert.match(tail, new RegExp(m[1] + "\\.setAttribute\\('aria-label', "), f + ' delete button without aria-label');
    }
  }
  const mk = read('dashboard/market.js');
  const hot = mk.slice(mk.indexOf('function drawHot'), mk.indexOf('function draw()'));
  assert.match(hot, /el\('button', 'ew-mrow/);
  assert.match(hot, /\.type = 'button'/);
});

// ---- plan 077: stale / expired text contrast ----

function cssBlock(css, sel) {
  const i = css.indexOf(sel + ' {');
  assert.ok(i >= 0, 'missing ' + sel);
  return css.slice(i, css.indexOf('}', i));
}

function hexToken(blk, name) {
  const m = blk.match(new RegExp(name + ':\\s*(#[0-9a-fA-F]{6})'));
  assert.ok(m, name);
  return [1, 3, 5].map((k) => parseInt(m[1].slice(k, k + 2), 16));
}

function lum(rgb) {
  const c = rgb.map((v) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
}

// WCAG ratio of fg drawn at `alpha` over bg (opacity blends toward the surface).
function contrast(fg, bg, alpha) {
  const mix = fg.map((v, k) => alpha * v + (1 - alpha) * bg[k]);
  const a = lum(mix);
  const b = lum(bg);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

test('contrast helper: black on white is 21, same colour is 1', () => {
  assert.strictEqual(Math.round(contrast([0, 0, 0], [255, 255, 255], 1)), 21);
  assert.strictEqual(contrast([90, 90, 90], [90, 90, 90], 1), 1);
});

test('stale and expired text keep 4.5:1 on --fk-surface-2 in light and dark', () => {
  const tokens = fs.readFileSync(path.join(APP, '..', 'ops', 'fleet_kit', 'tokens.css'), 'utf8');
  const css = read('shared/ew.css');
  const themes = { light: cssBlock(tokens, ':root'), dark: cssBlock(tokens, ':root[data-theme="dark"]') };
  for (const sel of ['.ew-stale', '.ew-erow.expired']) {
    const rule = cssBlock(css, sel);
    assert.match(rule, /color: var\(--fk-text-muted\)/, sel);
    const alpha = Number((rule.match(/opacity:\s*([.\d]+)/) || [0, 1])[1]);
    assert.ok(alpha >= 0.85, sel + ' opacity ' + alpha);
    for (const [name, blk] of Object.entries(themes)) {
      const ratio = contrast(hexToken(blk, '--fk-text-muted'), hexToken(blk, '--fk-surface-2'), alpha);
      assert.ok(ratio >= 4.5, sel + ' ' + name + ' ' + ratio.toFixed(2));
    }
  }
  assert.match(css, /\.ew-list\.ew-stale::before \{ content: "stale"/);  // text, not fade alone
  assert.strictEqual(C.fmtLeft(0), 'ended');  // an expired row's clock reads "ended"
});
