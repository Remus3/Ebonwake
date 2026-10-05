'use strict';
// Plan 022: overlay placement off the minimap, content-sized height,
// legibility knobs and quiet rows - the pure parts in ewcore.
const test = require('node:test');
const assert = require('node:assert');
const C = require('../shared/ewcore');

const WA = { x: 0, y: 0, width: 1920, height: 1040 };
const SIZE = { width: 340, height: 220 };

test('overlayConfig defaults: middle-left, primary display, scale 1, opacity 0.85', () => {
  for (const cfg of [undefined, null, {}, { overlay: null }, { overlay: [] }, { overlay: {} }]) {
    assert.deepStrictEqual(C.overlayConfig(cfg), { anchor: 'ml', display: null, scale: 1, opacity: 0.85 });
  }
});

test('overlayConfig accepts every named anchor and a work-area point', () => {
  for (const a of ['tl', 'tr', 'bl', 'br', 'ml', 'mr']) {
    assert.strictEqual(C.overlayConfig({ overlay: { anchor: a } }).anchor, a);
  }
  assert.deepStrictEqual(C.overlayConfig({ overlay: { anchor: { x: 40, y: 300 } } }).anchor, { x: 40, y: 300 });
});

test('overlayConfig rejects bad anchors, displays, scale and opacity', () => {
  const bad = [
    { anchor: 'TR' }, { anchor: 'center' }, { anchor: 3 }, { anchor: { x: 1.5, y: 2 } },
    { anchor: { x: 1 } }, { anchor: { x: '1', y: 2 } }, { anchor: [] }, { anchor: { x: 1, y: 2, z: 3 } }
  ];
  for (const o of bad) assert.strictEqual(C.overlayConfig({ overlay: o }).anchor, 'ml', JSON.stringify(o));
  for (const d of [-1, 1.5, '1', true, null, NaN]) {
    assert.strictEqual(C.overlayConfig({ overlay: { display: d } }).display, null, String(d));
  }
  assert.strictEqual(C.overlayConfig({ overlay: { display: 0 } }).display, 0);
  assert.strictEqual(C.overlayConfig({ overlay: { display: 2 } }).display, 2);
  for (const s of ['1.2', NaN, Infinity, null, true]) {
    assert.strictEqual(C.overlayConfig({ overlay: { scale: s } }).scale, 1, String(s));
    assert.strictEqual(C.overlayConfig({ overlay: { opacity: s } }).opacity, 0.85, String(s));
  }
});

test('overlayConfig clamps scale to 0.8-1.6 and opacity to 0.5-0.95', () => {
  const c = (o) => C.overlayConfig({ overlay: o });
  assert.strictEqual(c({ scale: 0.2 }).scale, 0.8);
  assert.strictEqual(c({ scale: 3 }).scale, 1.6);
  assert.strictEqual(c({ scale: 1.25 }).scale, 1.25);
  assert.strictEqual(c({ opacity: 0 }).opacity, 0.5);
  assert.strictEqual(c({ opacity: 1 }).opacity, 0.95);
  assert.strictEqual(c({ opacity: 0.7 }).opacity, 0.7);
});

test('overlayBounds places every named anchor with the margin', () => {
  const b = (a) => C.overlayBounds(WA, a, SIZE, 16);
  assert.deepStrictEqual(b('tl'), { x: 16, y: 16, width: 340, height: 220 });
  assert.deepStrictEqual(b('tr'), { x: 1920 - 340 - 16, y: 16, width: 340, height: 220 });
  assert.deepStrictEqual(b('bl'), { x: 16, y: 1040 - 220 - 16, width: 340, height: 220 });
  assert.deepStrictEqual(b('br'), { x: 1920 - 340 - 16, y: 1040 - 220 - 16, width: 340, height: 220 });
  assert.deepStrictEqual(b('ml'), { x: 16, y: Math.round((1040 - 220) / 2), width: 340, height: 220 });
  assert.deepStrictEqual(b('mr'), { x: 1920 - 340 - 16, y: Math.round((1040 - 220) / 2), width: 340, height: 220 });
});

test('overlayBounds honours a work-area offset (second display, taskbar on top)', () => {
  const wa = { x: 1920, y: 40, width: 2560, height: 1400 };
  assert.deepStrictEqual(C.overlayBounds(wa, 'tl', SIZE, 10), { x: 1930, y: 50, width: 340, height: 220 });
  assert.deepStrictEqual(C.overlayBounds(wa, { x: 100, y: 200 }, SIZE, 10),
    { x: 2020, y: 240, width: 340, height: 220 });
});

test('overlayBounds clamps a point anchor and an oversized window inside the work area', () => {
  assert.deepStrictEqual(C.overlayBounds(WA, { x: 5000, y: -50 }, SIZE, 16),
    { x: 1920 - 340, y: 0, width: 340, height: 220 });
  assert.deepStrictEqual(C.overlayBounds({ x: 0, y: 0, width: 300, height: 100 }, 'br', SIZE, 16),
    { x: 0, y: 0, width: 300, height: 100 });
  const r = C.overlayBounds(WA, 'ml', SIZE, 16);
  assert.ok(C.rectInside(r, WA));
});

test('overlayBounds falls back to the default anchor and sane sizes on bad input', () => {
  assert.deepStrictEqual(C.overlayBounds(WA, 'nope', SIZE, 16), C.overlayBounds(WA, 'ml', SIZE, 16));
  assert.deepStrictEqual(C.overlayBounds(WA, null, SIZE, -5), C.overlayBounds(WA, 'ml', SIZE, 0));
  const r = C.overlayBounds(WA, 'tl', { width: NaN, height: 'x' }, 16);
  assert.ok(r.width > 0 && r.height > 0);
  assert.strictEqual(C.overlayBounds(null, 'tl', SIZE, 16), null);
  assert.strictEqual(C.overlayBounds({ x: 0, y: 0, width: 0, height: 10 }, 'tl', SIZE, 16), null);
});

test('default anchor stays clear of the top-right 360x300 minimap zone', () => {
  for (const wa of [WA, { x: 0, y: 0, width: 1280, height: 680 }, { x: -1920, y: 0, width: 1920, height: 1080 }]) {
    for (const h of [60, 220, 600]) {
      const r = C.overlayBounds(wa, C.overlayConfig({}).anchor, { width: 340, height: h }, 16);
      assert.ok(C.rectInside(r, wa), JSON.stringify([wa, h]));
      assert.ok(!C.rectsIntersect(r, C.minimapZone(wa)), JSON.stringify([wa, h]));
    }
  }
  assert.ok(C.rectsIntersect(C.overlayBounds(WA, 'tr', SIZE, 16), C.minimapZone(WA)));
});

test('rect helpers', () => {
  assert.deepStrictEqual(C.minimapZone(WA), { x: 1920 - 360, y: 0, width: 360, height: 300 });
  assert.ok(C.rectsIntersect({ x: 0, y: 0, width: 10, height: 10 }, { x: 9, y: 9, width: 5, height: 5 }));
  assert.ok(!C.rectsIntersect({ x: 0, y: 0, width: 10, height: 10 }, { x: 10, y: 0, width: 5, height: 5 }));
  assert.ok(C.rectInside({ x: 0, y: 0, width: 10, height: 10 }, { x: 0, y: 0, width: 10, height: 10 }));
  assert.ok(!C.rectInside({ x: 0, y: 0, width: 11, height: 10 }, { x: 0, y: 0, width: 10, height: 10 }));
  assert.ok(!C.rectInside(null, WA));
});

test('overlayHeight clamps reported content height to 60-600, rejects non-numbers', () => {
  assert.strictEqual(C.overlayHeight(10), 60);
  assert.strictEqual(C.overlayHeight(250.4), 251);
  assert.strictEqual(C.overlayHeight(9000), 600);
  for (const v of ['200', NaN, Infinity, null, undefined, {}, -1]) assert.strictEqual(C.overlayHeight(v), null, String(v));
});

test('overlay style rides in the query and is re-validated on the page', () => {
  const q = C.overlayStyleQuery(C.overlayConfig({ overlay: { scale: 1.2, opacity: 0.6 } }));
  assert.deepStrictEqual(q, { scale: '1.2', opacity: '0.6' });
  const s = new URLSearchParams(Object.assign({}, C.widgetsQuery({}), q)).toString();
  assert.deepStrictEqual(C.overlayStyleFromQuery('?' + s), { scale: 1.2, opacity: 0.6 });
  assert.deepStrictEqual(C.overlayStyleFromQuery('?scale=9&opacity=0.1'), { scale: 1.6, opacity: 0.5 });
  assert.deepStrictEqual(C.overlayStyleFromQuery('?scale=x'), { scale: 1, opacity: 0.85 });
  assert.deepStrictEqual(C.overlayStyleFromQuery(''), { scale: 1, opacity: 0.85 });
  assert.deepStrictEqual(C.overlayStyleFromQuery(undefined), { scale: 1, opacity: 0.85 });
});

test('quiet rows: nothing-to-say values hide, real values and offline show', () => {
  for (const v of ['-', 'none', 'idle', '', '?', null, undefined]) assert.strictEqual(C.ovQuiet(v), true, String(v));
  for (const v of ['offline', '1h 02m', 'Velia 0:12:00', 'daily 0/3  weekly 1/2']) {
    assert.strictEqual(C.ovQuiet(v), false, v);
  }
});

test('overlay window stays click-through and unfocusable; size IPC is one-way from the overlay only', () => {
  const fs = require('fs');
  const path = require('path');
  const main = fs.readFileSync(path.join(__dirname, '..', 'main.js'), 'utf8');
  assert.match(main, /focusable: false/);
  assert.match(main, /setIgnoreMouseEvents\(true/);
  assert.match(main, /ipcMain\.on\('ew:overlay-size'/);
  assert.match(main, /event\.sender !== overlay\.webContents/);
  assert.match(main, /core\.overlayHeight\(px\)/);
  assert.doesNotMatch(main, /screen\.getPrimaryDisplay\(\)\.workArea;\s*const w = 340/);
  const pre = fs.readFileSync(path.join(__dirname, '..', 'overlay', 'preload.js'), 'utf8');
  assert.match(pre, /ipcRenderer\.send\('ew:overlay-size'/);
  assert.doesNotMatch(pre, /invoke|sendSync|on\(/);
  const html = fs.readFileSync(path.join(__dirname, '..', 'overlay', 'index.html'), 'utf8');
  assert.match(html, /id="ov-server-row" hidden/);
  assert.match(html, /<span class="ew-ov-lab">Leveling<\/span>/);
  assert.match(html, /<span class="ew-ov-lab">Season<\/span>/);
});

test('overlay css: scale, halo, opacity variable and tabular values', () => {
  const fs = require('fs');
  const path = require('path');
  const css = fs.readFileSync(path.join(__dirname, '..', 'shared', 'ew.css'), 'utf8');
  assert.match(css, /calc\(13px \* var\(--ew-overlay-scale, 1\)\)/);
  assert.match(css, /text-shadow: 0 0 2px #000, 0 1px 2px #000/);
  assert.match(css, /var\(--ew-overlay-alpha, 85%\)/);
  assert.match(css, /font-variant-numeric: tabular-nums/);
  // hidden must beat .ew-dot's display (header server dot, verifier round 1).
  assert.match(css, /\.ew-ov \[hidden\] \{ display: none; \}/);
  const ov = fs.readFileSync(path.join(__dirname, '..', 'overlay', 'overlay.js'), 'utf8');
  assert.match(ov, /document\.body\.getBoundingClientRect\(\)\.height/);
});

test('config example documents the four overlay keys with the defaults', () => {
  const fs = require('fs');
  const path = require('path');
  const ex = JSON.parse(fs.readFileSync(path.join(__dirname, '..', '..', 'config', 'local.example.json'), 'utf8'));
  const c = C.overlayConfig(ex);
  assert.deepStrictEqual(c, { anchor: 'ml', display: null, scale: 1, opacity: 0.85 });
  assert.strictEqual(ex.overlay.anchor, 'ml');
  assert.strictEqual(ex.overlay.scale, 1);
  assert.strictEqual(ex.overlay.opacity, 0.85);
  assert.ok('display' in ex.overlay);
});

test('server row shows only when the server is not ok', () => {
  assert.strictEqual(C.ovServerRowHidden('ok'), true);
  assert.strictEqual(C.ovServerRowHidden('?'), true);
  assert.strictEqual(C.ovServerRowHidden('offline'), false);
});
