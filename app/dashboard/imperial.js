/* EW Imperial delivery card (plan 053) on the Today tab: boxes left today per
   type (CP / 2 each, C.imperialRows) with -1 / +1 / +10 ticks, the reset
   countdown (C.imperialReset, live between polls), the best boxes by payout
   per input silver (C.imperialBest), a typed CP used while the profile hides
   it, and an add-box form. Reads GET /api/imperial every 60 s; writes go
   through the dashboard preload (window.ewApi, route /api/imperial) because
   the server refuses renderer POSTs. Prices are EW's market cache; nothing
   is read from the game. Every node is built with DOM APIs - no HTML from
   data. */
(function () {
  'use strict';
  const C = window.EWCore;
  const POLL_MS = 60000;
  const S = { data: null, err: null, last: null, timer: null, ui: null, busy: false, msg: '', shape: null };

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function getJSON(path) {
    return fetch(C.SERVER + path).then(function (r) {
      if (r.status === 404) throw new Error(C.notOnServer(path));
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    }).catch(function (e) {
      throw new Error(e instanceof TypeError ? 'server offline' : String(e.message || e));
    });
  }

  function bridge() {
    const b = window.ewApi;
    return b && typeof b.post === 'function' ? b : null;
  }

  function accept(d) {
    if (d && typeof d === 'object' && Array.isArray(d.types)) { S.data = d; S.err = null; }
  }

  function poll(force) {
    const now = Date.now();
    if (!force && !C.pollDue(S.last, now, POLL_MS)) { draw(); return; }
    S.last = now;
    clearTimeout(S.timer);
    S.timer = setTimeout(function () { if (!C.pollPaused(S.panel, document)) poll(true); }, POLL_MS);
    getJSON('/api/imperial').then(accept, function (e) { S.err = e.message; }).then(draw);
  }

  function send(body) {
    if (S.busy) return;
    const b = bridge();
    if (!b) { S.msg = 'saving needs the Ebonwake app window'; draw(); return; }
    S.busy = true;
    S.msg = '';
    draw();
    const done = function (res) {
      S.busy = false;
      if (res && res.ok) accept(res.data);
      else S.msg = 'save failed: ' + ((res && res.error) || 'unknown error');
      draw();
    };
    window.EWToast.via(b).post('/api/imperial', body).then(done, function (e) {
      done({ ok: false, error: String(e && e.message || e) });
    });
  }

  // ---- render ----

  function tickButton(type, add, label) {
    const btn = el('button', 'ew-btn', label);
    btn.type = 'button';
    btn.disabled = S.busy;
    btn.title = (add > 0 ? 'delivered ' : 'take back ') + Math.abs(add) + ' ' + type + ' box' + (Math.abs(add) > 1 ? 'es' : '');
    btn.addEventListener('click', function () { send({ deliver: { type: type, add: add } }); });
    return btn;
  }

  function typeRow(r) {
    const n = el('div', 'ew-trow' + (r.done ? ' done' : ''));
    const lab = el('span', 'ew-mname', r.label + (r.mastery ? ' (+' + r.mastery + '% mastery)' : ''));
    n.appendChild(lab);
    n.appendChild(el('span', 'ew-muted ew-tdays', r.text));
    [[-1, '-1'], [1, '+1'], [10, '+10']].forEach(function (p) { n.appendChild(tickButton(r.type, p[0], p[1])); });
    return n;
  }

  function cpForm() {
    const f = el('form', 'ew-form');
    const inp = el('input');
    inp.type = 'text';
    inp.placeholder = 'CP (blank = from profile)';
    inp.maxLength = 6;
    const c = S.data && S.data.cp;
    if (c && typeof c.typed === 'number') inp.value = String(c.typed);
    const btn = el('button', 'ew-btn', 'Set CP');
    btn.type = 'submit';
    btn.disabled = S.busy;
    f.appendChild(inp);
    f.appendChild(btn);
    f.addEventListener('submit', function (ev) {
      ev.preventDefault();
      const p = C.parseImperialCp(inp.value);
      if (!p.ok) { S.msg = p.error; draw(); return; }
      send(p.body);
    });
    return f;
  }

  function boxForm() {
    const f = el('form', 'ew-form');
    const sel = el('select');
    C.IMP_TYPES.forEach(function (t) {
      const o = el('option', null, t);
      o.value = t;
      sel.appendChild(o);
    });
    const name = el('input');
    name.type = 'text';
    name.placeholder = 'box name';
    name.maxLength = 40;
    const items = el('input');
    items.type = 'text';
    items.placeholder = 'item id x qty, ...';
    items.maxLength = 160;
    const btn = el('button', 'ew-btn', 'Add box');
    btn.type = 'submit';
    btn.disabled = S.busy;
    [sel, name, items, btn].forEach(function (x) { f.appendChild(x); });
    f.addEventListener('submit', function (ev) {
      ev.preventDefault();
      const p = C.parseImperialBox({ type: sel.value, name: name.value, items: items.value });
      if (!p.ok) { S.msg = p.error; draw(); return; }
      send(p.body);
    });
    return f;
  }

  function draw() {
    const ui = S.ui;
    if (!ui || !ui.body.isConnected) return;
    const now = Date.now();
    ui.meta.textContent = C.imperialReset(S.data, now) || '-';
    const shape = JSON.stringify([S.data, S.err, S.msg, S.busy]);
    if (shape === S.shape) return;
    S.shape = shape;
    const body = ui.body;
    body.textContent = '';
    if (S.err) body.appendChild(el('div', 'ew-err', (S.data ? 'last data - ' : '') + S.err));
    if (S.msg) body.appendChild(el('div', 'ew-err', S.msg));
    if (!S.data) { if (!S.err) body.appendChild(el('div', 'ew-muted', 'loading...')); return; }
    if (S.data.data_error) body.appendChild(el('div', 'ew-err', 'rules file: ' + S.data.data_error));
    const cpStale = !!(S.data.cp && S.data.cp.stale === true);  // plan 081: muted, never silent
    body.appendChild(el('div', 'ew-muted' + (cpStale ? ' ew-stale' : ''), C.imperialCpText(S.data) + ' - boxes per type = CP / 2, paid 250% untaxed'));
    const list = el('div', 'ew-list' + (S.err ? ' ew-stale' : ''));
    C.imperialRows(S.data).forEach(function (r) { list.appendChild(typeRow(r)); });
    body.appendChild(list);
    C.IMP_TYPES.forEach(function (t) {
      const best = C.imperialBest(S.data, t);
      if (!best.length) return;
      body.appendChild(el('div', 'ew-muted', 'Best ' + t + ' boxes (payout for input cost)'));
      best.forEach(function (b) {
        const n = el('div', 'ew-trow');
        n.appendChild(el('span', 'ew-mname', b.name));
        n.appendChild(el('span', 'ew-muted ew-tdays', b.text));
        body.appendChild(n);
      });
    });
    C.imperialUnpriced(S.data).forEach(function (line) { body.appendChild(el('div', 'ew-muted', line)); });
    body.appendChild(cpForm());
    body.appendChild(boxForm());
  }

  // ---- mount ----

  function card() {
    const c = el('section', 'ew-card ew-mcard');
    const h = el('h2', null, 'Imperial delivery');
    const meta = el('span', 'ew-tmeta');
    const pill = el('span', 'ew-pill unknown', '-');
    pill.title = 'daily reset (midnight server time)';
    meta.appendChild(pill);
    h.appendChild(meta);
    c.appendChild(h);
    const b = el('div', 'ew-cbody');
    c.appendChild(b);
    return { card: c, body: b, meta: pill };
  }

  // Appends the card to `panel` (the Today tab mounts it).
  function mount(panel) {
    S.panel = panel;
    const ui = card();
    panel.appendChild(ui.card);
    S.ui = ui;
    S.shape = null;
    if (!S.last) {
      setInterval(draw, 1000);
      poll(false);
    } else {
      draw();
    }
  }

  function show() { poll(false); }

  window.EWImperial = { mount: mount, show: show };
})();
