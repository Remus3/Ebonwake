'use strict';
// Plan 010 item 4: the self-test captures each dashboard tab only after
// showInactive() plus one completed paint (a rAF round trip), so unattended
// captures of an occluded dashboard are real. Driven with fake EW windows.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const selftest = require('../selftest');

const TABS = ['home', 'today', 'market', 'progress', 'grind', 'events', 'deadeye', 'system'];

function fakeImage(empty) {
  return {
    isEmpty: () => empty, toPNG: () => Buffer.from('png'),
    toBitmap: () => Buffer.alloc(4 * 4 * 4), getSize: () => ({ width: 4, height: 4 })
  };
}

function fakes(log, opts) {
  let active = TABS[0];
  let visible = false;
  const dash = {
    getContentSize: () => [1264, 761], // plan 025: the operator's dashboard size
    isVisible: () => visible,
    showInactive: () => { visible = true; log.push('showInactive'); },
    webContents: {
      isLoading: () => false,
      invalidate: () => log.push('invalidate'),
      setBackgroundThrottling: (v) => log.push('throttle:' + v),
      executeJavaScript: async (src) => {
        if (src.indexOf('requestAnimationFrame') >= 0) {
          if (opts.noPaint) return new Promise(() => {}); // never paints
          log.push('painted');
          return true;
        }
        if (src.indexOf('EWToast.selfTest()') >= 0) {
          log.push('notify-selftest');
          return { hits: 1, toasts: 1, notify: { ok: true } };
        }
        const m = /data-tab="([a-z]+)"/.exec(src);
        if (m && src.indexOf('.click()') >= 0) { active = m[1]; log.push('click:' + active); return null; }
        return { scrollH: 700, clientH: 761, scrollW: 1200, clientW: 1264, active: active,
          activeH: 500, tabs: TABS, pill: 'server ok' };
      },
      capturePage: async () => { log.push('capture:' + active); return fakeImage(false); }
    }
  };
  let ovVisible = false;
  const ov = {
    isVisible: () => ovVisible, isFocusable: () => false, isAlwaysOnTop: () => true,
    getNativeWindowHandle: () => Buffer.alloc(8),
    getBounds: () => opts.bounds || { x: 16, y: 410, width: 340, height: 220 },
    webContents: { isLoading: () => false, capturePage: async () => fakeImage(false) }
  };
  return { dash, ov, toggle: () => { ovVisible = !ovVisible; } };
}

async function runFake(opts) {
  const log = [];
  const f = fakes(log, opts || {});
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ewst-'));
  const out = path.join(dir, 'out.json');
  process.env.EW_SELFTEST_STAY = '1';
  await selftest.run({
    app: { quit() {} }, dashboard: f.dash, overlay: f.ov, keys: { a: 'F9' },
    globalShortcut: { isRegistered: () => true }, toggleOverlay: f.toggle, out: out,
    notifyShown: (() => { let n = 0; return () => n++; })(),
    wait: () => Promise.resolve(), paintTimeoutMs: 20,
    overlayPlace: Object.assign({ workArea: { x: 0, y: 0, width: 1920, height: 1040 }, defaultAnchor: true },
      (opts || {}).place)
  });
  delete process.env.EW_SELFTEST_STAY;
  return { log, res: JSON.parse(fs.readFileSync(out, 'utf8')) };
}

test('every tab capture follows showInactive and a paint after its click', async () => {
  const { log, res } = await runFake();
  assert.ok(res.ok, JSON.stringify(res));
  assert.strictEqual(res.tabs.length, 8);
  assert.strictEqual(res.tabs[0].id, 'home', 'Home paints first');
  assert.deepStrictEqual(res.dashboardSize, [1264, 761]);
  assert.ok(res.tabs.every((t) => t.fits), 'every tab fits 1264x761');
  assert.ok(log.indexOf('showInactive') >= 0);
  assert.ok(log.indexOf('throttle:false') >= 0);
  for (const id of TABS) {
    const c = log.indexOf('capture:' + id);
    const k = log.indexOf('click:' + id);
    assert.ok(c > k && k >= 0, id);
    assert.ok(log.indexOf('showInactive') < c, id);
    assert.ok(log.slice(k, c).indexOf('painted') >= 0, 'paint between click and capture: ' + id);
  }
  assert.ok(res.tabs.every((t) => t.painted && t.captured));
});

test('a paint that never comes is reported, not hung on', async () => {
  const { res } = await runFake({ noPaint: true });
  assert.strictEqual(res.tabs.length, 8);
  assert.ok(res.tabs.every((t) => t.painted === false));
  assert.strictEqual(res.ok, false);
});

// Plan 022 item 6: overlay inside its display's work area and, on the default
// anchor, clear of the top-right 360x300 minimap zone.
test('overlay placement: inside the work area and off the minimap', async () => {
  const { res } = await runFake();
  assert.strictEqual(res.overlay.insideWorkArea, true);
  assert.strictEqual(res.overlay.clearOfMinimap, true);
  assert.ok(res.ok, JSON.stringify(res));
});

test('overlay on the minimap with the default anchor fails the self-test', async () => {
  const { res } = await runFake({ bounds: { x: 1564, y: 16, width: 340, height: 220 } });
  assert.strictEqual(res.overlay.clearOfMinimap, false);
  assert.strictEqual(res.ok, false);
});

test('a chosen top-right anchor skips the minimap check but not the work-area check', async () => {
  const tr = await runFake({ bounds: { x: 1564, y: 16, width: 340, height: 220 }, place: { defaultAnchor: false } });
  assert.strictEqual(tr.res.overlay.clearOfMinimap, null);
  assert.ok(tr.res.ok, JSON.stringify(tr.res));
  const off = await runFake({ bounds: { x: 1800, y: 16, width: 340, height: 220 }, place: { defaultAnchor: false } });
  assert.strictEqual(off.res.overlay.insideWorkArea, false);
  assert.strictEqual(off.res.ok, false);
});

test('selftest switches keep Chromium from treating an occluded window as hidden', () => {
  const sw = selftest.switches();
  assert.ok(sw.some((s) => s[0] === 'disable-backgrounding-occluded-windows'));
  assert.ok(sw.some((s) => s[0] === 'disable-features' && /CalculateNativeWinOcclusion/.test(s[1])));
  const m = fs.readFileSync(path.join(__dirname, '..', 'main.js'), 'utf8');
  assert.match(m, /selftest\.switches\(\)/);
});

test('plan 026: the self-test fires one synthetic alert -> toast + OS notification (reported)', async () => {
  const { log, res } = await runFake();
  assert.ok(log.indexOf('notify-selftest') > log.lastIndexOf('capture:system'), 'after the tab captures');
  assert.deepStrictEqual(res.notify, { hits: 1, toasts: 1, bridge: { ok: true }, osShown: 1, ok: true });
  assert.ok(res.ok, 'report-only: never fails the run');
});
