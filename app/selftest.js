/* EW desktop self-test (EW_SELFTEST=<out.json>). Drives only EW's own windows:
   clicks each dashboard tab via executeJavaScript, measures page overflow,
   captures both windows to PNG (each dashboard capture only after
   showInactive() plus one completed paint, plan 010), checks overlay transparency and hotkey
   registration, and (plan 022) that the overlay sits inside its display's work
   area and off the minimap. Never touches any other window or the game. */
'use strict';

const fs = require('fs');
const path = require('path');
const core = require('./shared/ewcore');

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

// A double rAF resolves only after the renderer has produced a frame.
const PAINT = 'new Promise(function(r){requestAnimationFrame(function(){' +
  'requestAnimationFrame(function(){r(true);});});})';

// Chromium switches applied before app ready when EW_SELFTEST is set, so an
// occluded (covered) dashboard still counts as visible and keeps painting.
function switches() {
  return [
    ['disable-backgrounding-occluded-windows'],
    ['disable-renderer-backgrounding'],
    ['disable-features', 'CalculateNativeWinOcclusion']
  ];
}

// Plan 010 item 4: wait for one completed paint, bounded so a renderer that
// never paints is reported (painted: false) rather than hanging the run.
function painted(win, ms) {
  return Promise.race([
    win.webContents.executeJavaScript(PAINT).then(function () { return true; },
      function () { return false; }),
    new Promise(function (r) { setTimeout(function () { r(false); }, ms); })
  ]);
}

async function run(o) {
  const outDir = path.dirname(o.out);
  const pause = o.wait || wait;
  const paintMs = o.paintTimeoutMs || 3000;
  const res = { started: new Date().toISOString(), tabs: [], ok: false };
  try {
    await loaded(o.dashboard);
    await loaded(o.overlay);
    // Shown without stealing focus; occluded frames still render with
    // throttling off (plus switches() at startup).
    o.dashboard.webContents.setBackgroundThrottling(false);
    o.dashboard.showInactive();
    await pause(2500); // let /api/state arrive
    const first = await o.dashboard.webContents.executeJavaScript(MEASURE);
    res.dashboardSize = o.dashboard.getContentSize();
    res.pill = first.pill;
    for (const id of first.tabs) {
      await o.dashboard.webContents.executeJavaScript(
        'document.querySelector(\'.ew-tab[data-tab="' + id + '"]\').click()');
      await pause(250);
      o.dashboard.webContents.invalidate();
      const didPaint = await painted(o.dashboard, paintMs);
      const m = await o.dashboard.webContents.executeJavaScript(MEASURE);
      m.id = id;
      m.painted = didPaint;
      m.fits = m.scrollH <= m.clientH && m.scrollW <= m.clientW;
      m.switched = m.active === id && m.activeH > 0;
      res.tabs.push(m);
      let img = await o.dashboard.webContents.capturePage();
      for (let i = 0; i < 5 && img.isEmpty(); i++) {
        await pause(300);
        img = await o.dashboard.webContents.capturePage();
      }
      m.captured = !img.isEmpty();
      fs.writeFileSync(path.join(outDir, 'ew-dash-' + id + '.png'), img.toPNG());
    }
    res.hotkeys = {};
    Object.keys(o.keys).forEach(function (k) {
      res.hotkeys[k] = { accel: o.keys[k], registered: o.globalShortcut.isRegistered(o.keys[k]) };
    });
    const before = o.overlay.isVisible();
    o.toggleOverlay();
    await pause(500);
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
    // Plan 022: inside the chosen display's work area; on the default anchor
    // also clear of BDO's top-right minimap zone (null = not checked).
    const place = o.overlayPlace || {};
    res.overlay.workArea = place.workArea || null;
    res.overlay.insideWorkArea = core.rectInside(res.overlay.bounds, place.workArea);
    res.overlay.clearOfMinimap = place.defaultAnchor && place.workArea
      ? !core.rectsIntersect(res.overlay.bounds, core.minimapZone(place.workArea)) : null;
    res.ok = res.tabs.every(function (t) { return t.fits && t.switched && t.painted && t.captured; }) &&
      Object.keys(res.hotkeys).every(function (k) { return res.hotkeys[k].registered; }) &&
      res.overlay.toggled && res.overlay.cornerAlpha === 0 && !res.overlay.focusable &&
      res.overlay.insideWorkArea && res.overlay.clearOfMinimap !== false;
  } catch (e) {
    res.error = String(e && e.stack || e);
  }
  fs.writeFileSync(o.out, JSON.stringify(res, null, 1));
  if (!process.env.EW_SELFTEST_STAY) o.app.quit();
}

module.exports = { run: run, switches: switches };
