'use strict';
// Plan 107: app/shared/ewcore.js is split into per-feature parts under
// app/shared/core/, installed in order and re-exported unchanged.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const APP = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(APP, p), 'utf8');
const PINNED = JSON.parse(read('test/ewcore_exports.json')).exports;

function parts() {
  const m = /const PARTS = \[([^\]]*)\]/.exec(read('shared/ewcore.js'));
  assert.ok(m, 'ewcore.js declares its PARTS list');
  return m[1].split(',').map((s) => s.trim().replace(/^'|'$/g, '')).filter(Boolean);
}

function surface(api) {
  return Object.keys(api).map((k) => [k, typeof api[k]]);
}

test('require(ewcore) exports exactly the pre-split surface, in order', () => {
  const C = require('../shared/ewcore');
  assert.deepStrictEqual(surface(C), PINNED);
});

test('every part file exists and nothing else lives in shared/core', () => {
  const names = parts();
  assert.ok(names.length >= 10, 'split into per-feature parts');
  assert.strictEqual(new Set(names).size, names.length, 'no duplicate part');
  const files = fs.readdirSync(path.join(APP, 'shared', 'core')).sort();
  assert.deepStrictEqual(files, names.map((n) => n + '.part.js').sort());
});

test('dashboard and overlay load every part, in PARTS order, before ewcore.js', () => {
  const names = parts();
  for (const page of ['dashboard/index.html', 'overlay/index.html']) {
    const html = read(page);
    const iCore = html.indexOf('<script src="../shared/ewcore.js"></script>');
    assert.ok(iCore > 0, page);
    let last = -1;
    for (const n of names) {
      const i = html.indexOf('<script src="../shared/core/' + n + '.part.js"></script>');
      assert.ok(i > last && i < iCore, page + ': ' + n);
      last = i;
    }
  }
});

test('plain-script load (window.EWCore) gives the same surface and results', () => {
  const ctx = { console: console };
  ctx.self = ctx;
  vm.createContext(ctx);
  for (const n of parts()) vm.runInContext(read('shared/core/' + n + '.part.js'), ctx, { filename: n });
  vm.runInContext(read('shared/ewcore.js'), ctx, { filename: 'ewcore.js' });
  assert.deepStrictEqual(surface(ctx.EWCore), PINNED);
  const C = require('../shared/ewcore');
  assert.strictEqual(ctx.EWCore.fmtDuration(90061000), C.fmtDuration(90061000));
  assert.strictEqual(ctx.EWCore.SERVER, C.SERVER);
  assert.strictEqual(typeof ctx.EWCoreParts, 'object');
});

test('a missing part fails loudly in the plain-script load', () => {
  const ctx = {};
  ctx.self = ctx;
  vm.createContext(ctx);
  assert.throws(() => vm.runInContext(read('shared/ewcore.js'), ctx), /EWCore part missing/);
});

test('the monolith is gone: ewcore.js is an aggregator, parts stay small', () => {
  assert.ok(read('shared/ewcore.js').split('\n').length < 800, 'ewcore.js under 800 lines');
  for (const n of parts()) {
    assert.ok(read('shared/core/' + n + '.part.js').split('\n').length < 1000, n + ' under 1000 lines');
  }
});
