/* EW desktop self-test (EW_SELFTEST=<out.json>). Drives only EW's own windows:
   clicks each dashboard tab via executeJavaScript, measures page overflow,
   captures both windows to PNG, checks overlay transparency and hotkey
   registration. Never touches any other window or the game. */
'use strict';

const fs = require('fs');
const path = require('path');

function wait(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

function loaded(win) {
  return new Promise(function (r) {
    if (!win.webContents.isLoading()) r();
    else win.webContents.once('did-finish-load', r);
  });
}

const MEASURE = '(function(){var d=document.documentElement,b=document.body;' +
  'var a=document.querySelector(".ew-panel.active");' +
  'return {scrollH:Math.max(d.scrollHeight,b.scrollHeight),clientH:d.clientHeight,' +
  'scrollW:Math.max(d.scrollWidth,b.scrollWidth),clientW:d.clientWidth,' +
  'active:a?a.dataset.tab:null,activeH:a?a.getBoundingClientRect().height:0,' +
  'tabs:Array.prototype.map.call(document.querySelectorAll(".ew-tab"),function(t){return t.dataset.tab;}),' +
  'pill:(document.getElementById("server-pill")||{}).textContent};})()';

async function run(o) {
  const outDir = path.dirname(o.out);
  const res = { started: new Date().toISOString(), tabs: [], ok: false };
  try {
    await loaded(o.dashboard);
    await loaded(o.overlay);
    await wait(2500); // let /api/state arrive
    const first = await o.dashboard.webContents.executeJavaScript(MEASURE);
    res.dashboardSize = o.dashboard.getContentSize();
    res.pill = first.pill;
    for (const id of first.tabs) {
      await o.dashboard.webContents.executeJavaScript(
        'document.querySelector(\'.ew-tab[data-tab="' + id + '"]\').click()');
      await wait(250);
      const m = await o.dashboard.webContents.executeJavaScript(MEASURE);
      m.id = id;
      m.fits = m.scrollH <= m.clientH && m.scrollW <= m.clientW;
      m.switched = m.active === id && m.activeH > 0;
      res.tabs.push(m);
      const img = await o.dashboard.webContents.capturePage();
      fs.writeFileSync(path.join(outDir, 'ew-dash-' + id + '.png'), img.toPNG());
    }
    res.hotkeys = {};
    Object.keys(o.keys).forEach(function (k) {
      res.hotkeys[k] = { accel: o.keys[k], registered: o.globalShortcut.isRegistered(o.keys[k]) };
    });
    const before = o.overlay.isVisible();
    o.toggleOverlay();
    await wait(500);
    const after = o.overlay.isVisible();
    const img = await o.overlay.webContents.capturePage();
    fs.writeFileSync(path.join(outDir, 'ew-overlay.png'), img.toPNG());
    const bmp = img.toBitmap();
    const sz = img.getSize();
    // BGRA; alpha of the bottom-right pixel (outside the card) must be 0.
    const idx = ((sz.height - 1) * sz.width + (sz.width - 1)) * 4;
    res.overlay = {
      toggled: before !== after, visibleAfterToggle: after,
      cornerAlpha: bmp.length > idx + 3 ? bmp[idx + 3] : null,
      focusable: o.overlay.isFocusable(), alwaysOnTop: o.overlay.isAlwaysOnTop(),
      hwnd: o.overlay.getNativeWindowHandle().readBigUInt64LE(0).toString(),
      bounds: o.overlay.getBounds()
    };
    res.ok = res.tabs.every(function (t) { return t.fits && t.switched; }) &&
      Object.keys(res.hotkeys).every(function (k) { return res.hotkeys[k].registered; }) &&
      res.overlay.toggled && res.overlay.cornerAlpha === 0 && !res.overlay.focusable;
  } catch (e) {
    res.error = String(e && e.stack || e);
  }
  fs.writeFileSync(o.out, JSON.stringify(res, null, 1));
  if (!process.env.EW_SELFTEST_STAY) o.app.quit();
}

module.exports = { run: run };
