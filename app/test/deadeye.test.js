'use strict';
// Plan 007 slice B: pure Deadeye helpers (ewcore.js: renderMarkdown,
// levelIndex, validDeadeyeBody, parseStepForm), the /api/deadeye bridge route
// and static guards on the Deadeye tab (deadeye.js). No network, no DOM.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const C = require('../shared/ewcore');

const APP = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(APP, p), 'utf8');
const md = C.renderMarkdown;

// Every tag the renderer may emit, never with attributes.
const TAGS = ['h1', 'h2', 'h3', 'p', 'ul', 'ol', 'li', 'strong', 'em', 'code', 'pre'];
function onlySafeTags(html) {
  const tags = html.match(/<[^>]*>/g) || [];
  for (const t of tags) {
    const m = /^<\/?([a-z0-9]+)>$/.exec(t);
    assert.ok(m && TAGS.indexOf(m[1]) >= 0, 'unexpected tag ' + t);
  }
}

test('DEADEYE_LEVELS: +0..+15 then PRI DUO TRI TET PEN (21, ordered)', () => {
  assert.strictEqual(C.DEADEYE_LEVELS.length, 21);
  assert.strictEqual(C.DEADEYE_LEVELS[0], '+0');
  assert.strictEqual(C.DEADEYE_LEVELS[15], '+15');
  assert.deepStrictEqual(C.DEADEYE_LEVELS.slice(16), ['PRI', 'DUO', 'TRI', 'TET', 'PEN']);
  assert.deepStrictEqual(C.DEADEYE_SECTIONS, ['addons', 'crystals', 'artifacts', 'lightstones', 'rotation', 'misc']);
});

test('levelIndex: position in LEVELS, -1 for anything else', () => {
  assert.strictEqual(C.levelIndex('+0'), 0);
  assert.strictEqual(C.levelIndex('+15'), 15);
  assert.strictEqual(C.levelIndex('PRI'), 16);
  assert.strictEqual(C.levelIndex('PEN'), 20);
  for (const bad of ['+16', 'pen', ' PRI', '0', '', null, undefined, 3, {}, 'V']) {
    assert.strictEqual(C.levelIndex(bad), -1, String(bad));
  }
});

test('renderMarkdown: escapes all of & < > " \' before any markup', () => {
  assert.strictEqual(md('a & b < c > d " e \' f'), '<p>a &amp; b &lt; c &gt; d &quot; e &#39; f</p>');
  assert.strictEqual(md('&amp;'), '<p>&amp;amp;</p>', 'entities are not passed through');
  assert.strictEqual(md(''), '');
  assert.strictEqual(md(null), '');
  assert.strictEqual(md(undefined), '');
  assert.strictEqual(md(42), '');
});

test('renderMarkdown: <script>, <img onerror> and javascript: come out inert', () => {
  const evil = [
    '<script>alert(1)</script>',
    '<img src=x onerror="alert(1)">',
    '[click](javascript:alert(1))',
    '![x](javascript:alert(1))',
    '<a href="javascript:alert(1)">x</a>',
    '**<script>x</script>**',
    '`<img src=x onerror=alert(1)>`',
    '```\n<script>alert(1)</script>\n```',
    '# <svg onload=alert(1)>',
    '- <iframe src="javascript:alert(1)">',
    '<scr<script>ipt>',
    '"><b onmouseover=alert(1)>',
    'https://evil.example/x'
  ];
  for (const s of evil) {
    const out = md(s);
    onlySafeTags(out);
    assert.doesNotMatch(out, /<script|<img|<a[\s>]|<svg|<iframe|<b[\s>]/i, s);
    assert.doesNotMatch(out, /<[^>]*(=|javascript:)/i, 'no attribute in any emitted tag: ' + s);
    assert.doesNotMatch(out, /["']/, 'quotes only ever appear escaped: ' + s);
  }
  assert.match(md('<script>alert(1)</script>'), /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.strictEqual(md('[click](javascript:alert(1))'), '<p>[click](javascript:alert(1))</p>', 'links stay literal text');
});

test('renderMarkdown: headings # to ### only', () => {
  assert.strictEqual(md('# One'), '<h1>One</h1>');
  assert.strictEqual(md('## Two'), '<h2>Two</h2>');
  assert.strictEqual(md('### Three'), '<h3>Three</h3>');
  assert.strictEqual(md('#### Four'), '<p>#### Four</p>');
  assert.strictEqual(md('#NoSpace'), '<p>#NoSpace</p>');
  assert.strictEqual(md('## *em* **b**'), '<h2><em>em</em> <strong>b</strong></h2>');
});

test('renderMarkdown: bullet and ordered lists', () => {
  assert.strictEqual(md('- a\n- b\n* c'), '<ul><li>a</li><li>b</li><li>c</li></ul>');
  assert.strictEqual(md('1. one\n2. two\n10. ten'), '<ol><li>one</li><li>two</li><li>ten</li></ol>');
  assert.strictEqual(md('- a\n1. b'), '<ul><li>a</li></ul><ol><li>b</li></ol>', 'list kind change closes the list');
  assert.strictEqual(md('- a\n\n- b'), '<ul><li>a</li></ul><ul><li>b</li></ul>');
  assert.strictEqual(md('-a'), '<p>-a</p>');
  assert.strictEqual(md('- **Black Spirit** `Q`'), '<ul><li><strong>Black Spirit</strong> <code>Q</code></li></ul>');
});

test('renderMarkdown: bold, em, inline code; code is not re-parsed', () => {
  assert.strictEqual(md('**b** and *e*'), '<p><strong>b</strong> and <em>e</em></p>');
  assert.strictEqual(md('`**not bold**`'), '<p><code>**not bold**</code></p>');
  assert.strictEqual(md('a * b * c'), '<p>a * b * c</p>', 'spaced asterisks are not emphasis');
  assert.strictEqual(md('2*3*4'), '<p>2*3*4</p>', 'word-internal asterisks need a closing word edge');
  assert.strictEqual(md('`a & b`'), '<p><code>a &amp; b</code></p>');
  assert.strictEqual(md('`unclosed'), '<p>`unclosed</p>');
});

test('renderMarkdown: fenced code blocks keep text literal, unclosed runs to the end', () => {
  assert.strictEqual(md('```\n# not h\n- not li\n**x**\n```'), '<pre><code># not h\n- not li\n**x**</code></pre>');
  assert.strictEqual(md('```js\nlet a = 1 < 2;\n```\nafter'), '<pre><code>let a = 1 &lt; 2;</code></pre><p>after</p>');
  assert.strictEqual(md('```\nopen'), '<pre><code>open</code></pre>');
  assert.strictEqual(md('```\n```'), '<pre><code></code></pre>');
});

test('renderMarkdown: paragraphs split on blank lines, lines inside joined, CRLF ok', () => {
  assert.strictEqual(md('a\nb\n\nc'), '<p>a\nb</p><p>c</p>');
  assert.strictEqual(md('a\r\nb\r\n\r\nc'), '<p>a\nb</p><p>c</p>');
  assert.strictEqual(md('para\n# H\n- li\ntext'), '<p>para</p><h1>H</h1><ul><li>li</li></ul><p>text</p>');
  assert.strictEqual(md('\n\n  \n'), '');
  onlySafeTags(md('# a\n## b\n### c\n- d\n1. e\n**f** *g* `h`\n```\ni\n```\nj'));
});

test('renderMarkdown: large input renders in bounded time', () => {
  const big = ('**' + 'x*'.repeat(50) + ' `' + '*'.repeat(100) + '\n').repeat(150).slice(0, 20000);
  const t = Date.now();
  onlySafeTags(md(big));
  assert.ok(Date.now() - t < 1000);
});

test('validDeadeyeBody accepts exactly the slice A POST shapes', () => {
  const ok = [
    { note: { section: 'addons', text: '' } },
    { note: { section: 'rotation', text: '# Rot\n- Q\tE\r\n' } },
    { note: { section: 'misc', text: 'x'.repeat(20000) } },
    { note: { section: 'misc', text: 'x\r\n'.repeat(10000) }, },
    { add_step: { item: 'Kzarka longbow', current: '+15', target: 'PRI' } },
    { add_step: { item: 'x'.repeat(60), current: '+0', target: 'PEN', note: 'y'.repeat(200) } },
    { add_step: { item: 'Belt', current: 'TET', target: 'PEN', note: '' } },
    { edit_step: { id: 'd1', item: 'New' } },
    { edit_step: { id: 'd12', current: 'PRI', target: 'TRI', note: '' } },
    { edit_step: { id: 'd3', target: 'DUO' } },
    { step_done: { id: 'd1', done: true } }, { step_done: { id: 'd999999999', done: false } },
    { delete_step: 'd1' },
    { move_step: { id: 'd2', dir: -1 } }, { move_step: { id: 'd2', dir: 1 } }
  ];
  for (const b of ok) assert.strictEqual(C.validDeadeyeBody(b), true, JSON.stringify(b).slice(0, 80));
  const bad = [
    null, [], 'x', {}, { bogus: 1 }, { delete_step: 'd1', step_done: { id: 'd1', done: true } },
    { note: null }, { note: {} }, { note: { section: 'addons' } }, { note: { text: 'a' } },
    { note: { section: 'gear', text: 'a' } }, { note: { section: 'addons', text: 5 } },
    { note: { section: 'addons', text: 'x'.repeat(20001) } }, { note: { section: 'addons', text: 'a', x: 1 } },
    { note: { section: 'addons', text: 'a\u0000b' } },
    { add_step: null }, { add_step: { item: 'a', current: '+0' } }, { add_step: { current: '+0', target: '+1' } },
    { add_step: { item: '', current: '+0', target: '+1' } }, { add_step: { item: '  ', current: '+0', target: '+1' } },
    { add_step: { item: 'x'.repeat(61), current: '+0', target: '+1' } }, { add_step: { item: 'a\nb', current: '+0', target: '+1' } },
    { add_step: { item: 'a', current: '+16', target: 'PRI' } }, { add_step: { item: 'a', current: '+0', target: 'pri' } },
    { add_step: { item: 'a', current: 'PRI', target: 'PRI' } }, { add_step: { item: 'a', current: 'DUO', target: 'PRI' } },
    { add_step: { item: 'a', current: '+0', target: '+1', note: 'y'.repeat(201) } },
    { add_step: { item: 'a', current: '+0', target: '+1', note: null } },
    { add_step: { item: 'a', current: '+0', target: '+1', note: 'a\nb' } },
    { add_step: { item: 'a', current: '+0', target: '+1', done: true } }, { add_step: { item: 'a', current: '+0', target: '+1', id: 'd1' } },
    { edit_step: { id: 'd1' } }, { edit_step: { item: 'a' } }, { edit_step: { id: 'x1', item: 'a' } },
    { edit_step: { id: 'd1', item: null } }, { edit_step: { id: 'd1', current: 'TRI', target: 'DUO' } },
    { edit_step: { id: 'd1', target: 'XX' } }, { edit_step: { id: 'd1', done: true } }, { edit_step: { id: 'd1', note: null } },
    { step_done: { id: 'd1' } }, { step_done: { id: 'd1', done: 1 } }, { step_done: { id: 'd1', done: true, x: 1 } },
    { step_done: 'd1' },
    { delete_step: '' }, { delete_step: 'd' }, { delete_step: 'D1' }, { delete_step: 'd1 ' }, { delete_step: 1 },
    { delete_step: 'd1234567890' }, { delete_step: 'e1' }, { delete_step: null },
    { move_step: { id: 'd1', dir: 0 } }, { move_step: { id: 'd1', dir: 2 } }, { move_step: { id: 'd1', dir: '1' } },
    { move_step: { id: 'd1' } }, { move_step: { id: 'd1', dir: 1, x: 1 } }, { move_step: 'd1' }
  ];
  for (const b of bad) assert.strictEqual(C.validDeadeyeBody(b), false, JSON.stringify(b).slice(0, 80));
});

test('parseStepForm: form strings -> add_step body or an operator error', () => {
  assert.deepStrictEqual(C.parseStepForm({ item: ' Kzarka ', current: '+15', target: 'PRI', note: ' cron ' }),
    { ok: true, body: { add_step: { item: 'Kzarka', current: '+15', target: 'PRI', note: 'cron' } } });
  assert.deepStrictEqual(C.parseStepForm({ item: 'Belt', current: 'TET', target: 'PEN', note: '' }),
    { ok: true, body: { add_step: { item: 'Belt', current: 'TET', target: 'PEN' } } });
  assert.match(C.parseStepForm({ item: '', current: '+0', target: '+1' }).error, /item/);
  assert.match(C.parseStepForm({ item: 'x'.repeat(61), current: '+0', target: '+1' }).error, /item/);
  assert.match(C.parseStepForm({ item: 'a', current: 'PRI', target: 'PRI' }).error, /above/);
  assert.match(C.parseStepForm({ item: 'a', current: 'nope', target: 'PRI' }).error, /level/);
  assert.match(C.parseStepForm({ item: 'a', current: '+0', target: '+1', note: 'y'.repeat(201) }).error, /note/);
  assert.strictEqual(C.parseStepForm(null).ok, false);
  const r = C.parseStepForm({ item: 'a', current: '+0', target: '+15' });
  assert.strictEqual(C.validDeadeyeBody(r.body), true);
});

test('validPost allowlist carries /api/deadeye with its own validator', () => {
  assert.ok(C.POST_ROUTES.indexOf('/api/deadeye') >= 0);
  assert.strictEqual(C.validPost('/api/deadeye', { delete_step: 'd1' }), true);
  assert.strictEqual(C.validPost('/api/deadeye', { delete: 'e1' }), false);
  assert.strictEqual(C.validPost('/api/events', { delete_step: 'd1' }), false);
  assert.strictEqual(C.validPost('/api/deadeye/', { delete_step: 'd1' }), false);
  assert.match(read('preload.js'), /\/api\/deadeye/);
});

test('deadeye.js: HTML only from renderMarkdown into the preview, POST via the bridge only', () => {
  const src = read('dashboard/deadeye.js');
  assert.doesNotMatch(src, /outerHTML|insertAdjacentHTML|document\.write|DOMParser|createContextualFragment/);
  const html = src.match(/innerHTML/g) || [];
  assert.strictEqual(html.length, 1, 'exactly one innerHTML sink');
  assert.match(src, /preview\.innerHTML\s*=\s*C\.renderMarkdown\(/);
  assert.match(src, /\/api\/deadeye/);
  assert.match(src, /ewApi/);
  for (const f of ['renderMarkdown', 'parseStepForm', 'levelIndex']) assert.match(src, new RegExp('C\\.' + f + '\\('), f);
  assert.match(src, /sure\?/, 'two-click confirm');
  for (const op of ['note', 'step_done', 'delete_step', 'move_step']) assert.match(src, new RegExp(op + ':'), op);
  assert.match(src, /20000/, 'char counter vs the server limit');
  assert.match(src, /textarea/);
  assert.doesNotMatch(src, /method:\s*'POST'/, 'renderer never POSTs directly');
  assert.doesNotMatch(src, /window\.open|location\.href\s*=|\.href\s*=/, 'no navigation inside the dashboard');
  assert.match(src, /window\.EWDeadeye\s*=/);
});

test('dashboard loads and mounts deadeye.js; CSP unchanged', () => {
  const html = read('dashboard/index.html');
  assert.match(html, /connect-src http:\/\/127\.0\.0\.1:8940;/);
  assert.match(html, /script-src 'self'">/);
  const iCore = html.indexOf('ewcore.js');
  const iDe = html.indexOf('<script src="deadeye.js"></script>');
  const iDash = html.indexOf('dashboard.js');
  assert.ok(iCore >= 0 && iDe > iCore && iDash > iDe, 'script order ewcore, deadeye, dashboard');
  const dash = read('dashboard/dashboard.js');
  assert.match(dash, /EWDeadeye\.mount\(/);
  assert.match(dash, /EWDeadeye\.show\(/);
  const css = read('shared/ew.css');
  assert.match(css, /\.ew-deadeye/);
  assert.match(css, /\.ew-md/);
});

// ---- Plan 057: draft autosave ----

test('draft helpers: key, encode/decode round trip, rejects junk', () => {
  assert.strictEqual(C.DEADEYE_DRAFT_MS, 2000);
  assert.strictEqual(C.draftKey('rotation'), 'ew.deadeye.draft.rotation');
  assert.strictEqual(C.draftKey(''), null);
  assert.strictEqual(C.draftKey('a b'), null);
  assert.strictEqual(C.draftKey(null), null);
  const raw = C.draftEncode('hello\nworld', '2026-10-06T01:02:03.000Z');
  assert.deepStrictEqual(JSON.parse(raw), { v: 1, text: 'hello\nworld', at: '2026-10-06T01:02:03.000Z' });
  assert.deepStrictEqual(C.draftDecode(raw), { text: 'hello\nworld', at: '2026-10-06T01:02:03.000Z' });
  for (const junk of [null, undefined, '', '{', '[]', '"x"', '{"v":1,"text":3,"at":"x"}',
    '{"v":2,"text":"a","at":"2026-10-06T01:02:03.000Z"}', '{"v":1,"text":"a"}',
    JSON.stringify({ v: 1, text: 'x'.repeat(C.NOTE_MAX + 1), at: '2026-10-06T01:02:03.000Z' })]) {
    assert.strictEqual(C.draftDecode(junk), null, String(junk));
  }
});

test('draftPending: a stored draft is offered only when it differs from the saved text', () => {
  const d = { text: 'new text', at: '2026-10-06T01:02:03.000Z' };
  assert.strictEqual(C.draftPending(d, 'old text'), true);
  assert.strictEqual(C.draftPending(d, 'new text'), false);
  assert.strictEqual(C.draftPending({ text: 'a\r\nb', at: d.at }, 'a\nb'), false, 'CRLF equal to LF');
  assert.strictEqual(C.draftPending(null, 'x'), false);
  assert.strictEqual(C.draftPending(d, undefined), true);
  assert.strictEqual(C.draftPending({ text: '', at: d.at }, undefined), false);
});

test('draftsToRestore: per section, only differing stored drafts, never over a live edit', () => {
  const at = '2026-10-06T01:02:03.000Z';
  const secs = [{ id: 'addons', text: 'A' }, { id: 'rotation', text: 'R' }, { id: 'misc', text: 'M' }];
  const store = {
    'ew.deadeye.draft.addons': C.draftEncode('A2', at),
    'ew.deadeye.draft.rotation': C.draftEncode('R', at),
    'ew.deadeye.draft.misc': 'garbage'
  };
  const get = (k) => (Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null);
  assert.deepStrictEqual(C.draftsToRestore(secs, get, {}), { addons: { text: 'A2', at: at } });
  assert.deepStrictEqual(C.draftsToRestore(secs, get, { addons: 'typing' }), {}, 'live draft wins');
  assert.deepStrictEqual(C.draftsToRestore(secs, function () { throw new Error('denied'); }, {}), {});
  assert.deepStrictEqual(C.draftsToRestore(null, get, {}), {});
});

test('deadeye.js: debounced localStorage autosave, restore bar, Ctrl+S, unload guard', () => {
  const src = read('dashboard/deadeye.js');
  assert.match(src, /C\.DEADEYE_DRAFT_MS/);
  assert.match(src, /localStorage\.setItem\(/);
  assert.match(src, /localStorage\.removeItem\(/);
  assert.match(src, /C\.draftsToRestore\(/);
  assert.match(src, /C\.draftEncode\(/);
  // localStorage access is wrapped: a denied store never breaks the tab.
  const calls = src.split(/localStorage\.\w+\(/).length - 1;
  const tries = (src.match(/try \{[^}]*localStorage\.\w+\(/g) || []).length;
  assert.ok(calls >= 2);
  assert.strictEqual(tries, calls, 'every localStorage call sits in a try');
  assert.match(src, /ev\.ctrlKey && \(ev\.key === 's' \|\| ev\.key === 'S'\)/);
  assert.match(src, /addEventListener\('beforeunload'/);
  assert.match(src, /'Restore'/);
  assert.match(src, /'Discard'/);
});
