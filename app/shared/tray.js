/* EW tray (plan 010 item 2): menu template, tray icon bitmap and the pure
   helpers behind "Restart server". No Electron here, so node --test covers it;
   main.js only wires it. Restart server touches only the local EW server:
   it kills a pid only when /api/version answers with the EW contract. */
'use strict';

const path = require('path');

const VERSION_KEYS = ['commit', 'config_hash', 'pid', 'schema', 'started'];
const BANNER = 'Ebonwake';
const TILE = [15, 18, 22];
const EMBER = [232, 140, 48];
const CORE = [15, 18, 22];

function menuTemplate(a) {
  ['showDashboard', 'toggleOverlay', 'restartServer', 'quit'].forEach(function (k) {
    if (typeof a[k] !== 'function') throw new Error('tray action missing: ' + k);
  });
  return [
    { label: 'Show dashboard', click: function () { a.showDashboard(); } },
    { label: 'Toggle overlay', click: function () { a.toggleOverlay(); } },
    { label: 'Restart server', click: function () { a.restartServer(); } },
    { type: 'separator' },
    { label: 'Quit', click: function () { a.quit(); } }
  ];
}

// The tray icon is drawn at run time (no binary asset in the repo): a dark
// rounded tile with an ember ring around a dark core. BGRA, as
// nativeImage.createFromBitmap expects on Windows.
function iconBitmap(size) {
  const buf = Buffer.alloc(size * size * 4);
  const c = (size - 1) / 2;
  const s = size / 16;
  const corner = 3 * s;
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const dx = x - c;
      const dy = y - c;
      const r = Math.sqrt(dx * dx + dy * dy);
      const ex = Math.max(Math.abs(dx) - (c - corner), 0);
      const ey = Math.max(Math.abs(dy) - (c - corner), 0);
      let rgb = null;
      if (Math.sqrt(ex * ex + ey * ey) <= corner + 0.5) {
        rgb = r <= 2.5 * s ? CORE : (r <= 6 * s ? EMBER : TILE);
      }
      const i = (y * size + x) * 4;
      if (rgb) { buf[i] = rgb[2]; buf[i + 1] = rgb[1]; buf[i + 2] = rgb[0]; buf[i + 3] = 255; }
    }
  }
  return buf;
}

function isEwVersion(doc, serverHeader) {
  if (!doc || typeof doc !== 'object' || Array.isArray(doc)) return false;
  const keys = Object.keys(doc).sort();
  return keys.length === VERSION_KEYS.length &&
    keys.every(function (k, i) { return k === VERSION_KEYS[i]; }) &&
    doc.schema === 1 && typeof serverHeader === 'string' && serverHeader.indexOf(BANNER) === 0;
}

function killablePid(doc, serverHeader, selfPid) {
  if (!isEwVersion(doc, serverHeader)) return null;
  const pid = doc.pid;
  if (typeof pid !== 'number' || !Number.isInteger(pid) || pid <= 0 || pid === selfPid) return null;
  return pid;
}

// launch.py passes its own pythonw.exe in EW_PYTHONW; PATH lookup otherwise.
function serverStartCommand(env, repo) {
  return {
    cmd: (env && env.EW_PYTHONW) || 'pythonw.exe',
    args: [path.join(repo, 'tools', 'launch.py'), '--server-only'],
    cwd: repo
  };
}

module.exports = {
  menuTemplate: menuTemplate, iconBitmap: iconBitmap, isEwVersion: isEwVersion,
  killablePid: killablePid, serverStartCommand: serverStartCommand, EMBER: EMBER
};
