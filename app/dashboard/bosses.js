/* EW World bosses card (plan 032) on the Today tab: next 3 spawns with a
   live countdown (C.bossRows; looted names greyed, Garmoth n/3), the week's
   Garmoth count, and a looted tick per boss that has already spawned today
   (C.bossTicks). Reads GET /api/bosses (plan 031) every 60 s; ticks go through
   the dashboard preload (window.ewApi, route /api/bosses) because the server
   refuses renderer POSTs. Schedule data is EW's own table; nothing is read
   from the game. Every node is built with DOM APIs - no HTML from data. */
(function () {
  'use strict';
  const C = window.EWCore;
  const POLL_MS = 60000;
  const S = { data: null, err: null, last: null, timer: null, ui: null, pending: {}, msg: '', shape: null, vals: [] };

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
    if (d && typeof d === 'object' && Array.isArray(d.next)) { S.data = d; S.err = null; }
  }

  function poll(force) {
    const now = Date.now();
    if (!force && !C.pollDue(S.last, now, POLL_MS)) { draw(); return; }
    S.last = now;
    clearTimeout(S.timer);
    S.timer = setTimeout(function () { poll(true); }, POLL_MS);
    getJSON('/api/bosses').then(accept, function (e) { S.err = e.message; }).then(draw);
  }

  function toggle(t) {
    const key = t.day + '|' + t.name;
    if (key in S.pending) return;
    const b = bridge();
    if (!b) { S.msg = 'ticking needs the Ebonwake app window'; draw(); return; }
    S.pending[key] = true;
    S.msg = '';
    draw();
    const body = t.looted ? { untick: { boss: t.name, day: t.day } } : { tick: { boss: t.name, day: t.day } };
    const done = function (res) {
      delete S.pending[key];
      if (res && res.ok) accept(res.data);
      else S.msg = 'tick failed: ' + ((res && res.error) || 'unknown error');
      draw();
    };
    window.EWToast.via(b).post('/api/bosses', body).then(done, function (e) {
      done({ ok: false, error: String(e && e.message || e) });
    });
  }

  // ---- render ----

  function nameNode(r) {
    const lab = el('span', 'ew-mname ew-bnext');
    r.names.forEach(function (n, i) {
      if (i) lab.appendChild(document.createTextNode(' + '));
      lab.appendChild(el('span', n.looted ? 'ew-boss-looted' : '', n.label));
    });
    lab.title = r.text;
    return lab;
  }

  function draw() {
    const ui = S.ui;
    if (!ui || !ui.body.isConnected) return;
    const now = Date.now();
    const rows = C.bossRows(S.data, now, 3);
    const ticks = C.bossTicks(S.data, now);
    const shape = JSON.stringify([rows.map(function (r) { return [r.key, r.names]; }), ticks, S.err, S.msg,
      Object.keys(S.pending)]);
    if (shape === S.shape) { values(rows); return; }
    S.shape = shape;
    S.vals = [];
    const body = ui.body;
    body.textContent = '';
    ui.meta.textContent = C.bossGarmothText(S.data) || '-';
    if (S.err) body.appendChild(el('div', 'ew-err', (S.data ? 'last data - ' : '') + S.err));
    if (S.msg) body.appendChild(el('div', 'ew-err', S.msg));
    if (!S.data) { if (!S.err) body.appendChild(el('div', 'ew-muted', 'loading...')); return; }
    const list = el('div', 'ew-list' + (S.err ? ' ew-stale' : ''));
    if (!rows.length) list.appendChild(el('div', 'ew-muted', 'no spawns listed'));
    rows.forEach(function (r) {
      const n = el('div', 'ew-trow' + (r.done ? ' done' : ''));
      n.appendChild(nameNode(r));
      const v = el('span', 'ew-muted ew-tdays', r.left);
      n.appendChild(v);
      S.vals.push(v);
      list.appendChild(n);
    });
    body.appendChild(list);
    if (ticks.length) {
      const tk = el('div', 'ew-bticks');
      tk.title = 'looted today (PT day)';
      ticks.forEach(function (t) {
        const lab = el('label', null);
        const cb = el('input');
        cb.type = 'checkbox';
        cb.checked = t.looted;
        cb.disabled = (t.day + '|' + t.name) in S.pending;
        cb.addEventListener('change', function () { toggle(t); });
        lab.appendChild(cb);
        lab.appendChild(el('span', t.looted ? 'ew-boss-looted' : '', t.name));
        tk.appendChild(lab);
      });
      body.appendChild(tk);
    }
  }

  function values(rows) {
    rows.forEach(function (r, i) {
      const v = S.vals[i];
      if (v && v.textContent !== r.left) v.textContent = r.left;
    });
  }

  // ---- mount ----

  function card() {
    const c = el('section', 'ew-card ew-mcard');
    const h = el('h2', null, 'World bosses');
    const meta = el('span', 'ew-tmeta');
    const pill = el('span', 'ew-pill unknown', '-');
    pill.title = 'Garmoth loots this week (cap per weekly reset)';
    meta.appendChild(pill);
    h.appendChild(meta);
    c.appendChild(h);
    const b = el('div', 'ew-cbody');
    c.appendChild(b);
    return { card: c, body: b, meta: pill };
  }

  // Appends the card to `panel` (the Today tab mounts it).
  function mount(panel) {
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

  window.EWBosses = { mount: mount, show: show };
})();
