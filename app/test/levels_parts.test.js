'use strict';
// Plan 107 follow-up: plan 018's old level-cap guard (levels.test.js) scans
// the source text of shared/ewcore.js. Plan 107 moved that code into
// shared/core/*.part.js, so the same scan runs here over ewcore.js plus
// every part file, restoring the guard. No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const SHARED = path.join(__dirname, '..', 'shared');
const CORE = path.join(SHARED, 'core');
const OLD_CAP = /\b1-70\b|\[1, 70\]|numInput\(70\)/;

function sources() {
  const parts = fs.readdirSync(CORE).filter((f) => f.endsWith('.part.js')).sort();
  return [path.join(SHARED, 'ewcore.js')].concat(parts.map((f) => path.join(CORE, f)));
}

test('core parts exist (the scan is not vacuous)', () => {
  const files = sources();
  assert.ok(files.length > 1, 'expected shared/core/*.part.js files');
  const all = files.map((f) => fs.readFileSync(f, 'utf8')).join('\n');
  assert.match(all, /LEVEL_MAX/, 'LEVEL_MAX must be defined in ewcore.js or a part');
});

test('no old level cap (1-70) in ewcore.js or any core part', () => {
  for (const f of sources()) {
    assert.doesNotMatch(fs.readFileSync(f, 'utf8'), OLD_CAP, path.relative(SHARED, f));
  }
});
