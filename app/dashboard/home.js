/* EW Home / Now tab (plan 025): one glance screen. Reads the existing GETs
   (/api/today, /api/grind, /api/leveling, /api/events, /api/market/watch,
   plan 032 /api/bosses - a read-only World bosses card) and
   lets C.composeNow order the cards; a 404 (old server) drops that payload's
   card, other errors keep the last data. Read-only except the one-click tick
   of a daily, which reuses the Today tick route through the dashboard preload.
   Countdowns update in place every second; a structural change redraws. Every
   node is built with DOM APIs - no HTML from data. */
(function () {
  'use strict';
  const C = window.EWCore;
  const POLL_MS = 60000;
  const SOURCES = { today: '/api/today', grind: '/api/grind', leveling: '/api/leveling',
    events: '/api/events', market: '/api/market/watch', bosses: '/api/bosses' };
  const S = { snap: { at: {} }, err: {}, last: null, timer: null, panel: null, shape: null, vals: [],
    pending: {}, msg: '' };

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  // A 404 (the server predates the route) throws an error flagged `gone`.
  function getJSON(path) {
    return fetch(C.SERVER + path).then(function (r) {
      if (r.status === 404) throw Object.assign(new Error(C.notOnServer(path)), { gone: true });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    }).catch(function (e) {
      if (e && e.gone) throw e;
      throw new Error(e instanceof TypeError ? 'server offline' : String(e.message || e));
    });
  }

  function bridge() {
    const b = window.ewApi;
    return b && typeof b.post === 'function' ? b : null;
  }

  // ---- data ----

  function poll(force) {
    const now = Date.now();
    if (!force && !C.pollDue(S.last, now, POLL_MS)) { draw(); return; }
    S.last = now;
    clearTimeout(S.timer);
    S.timer = setTimeout(function () { poll(true); }, POLL_MS);
    Promise.all(Object.keys(SOURCES).map(function (k) {
      return getJSON(SOURCES[k]).then(function (body) {
        S.snap[k] = body;
        S.snap.at[k] = Date.now();
        delete S.err[k];
      }, function (e) {
        if (e.gone) { S.snap[k] = null; delete S.err[k]; } else S.err[k] = e.message; // 404 drops the card
      });
    })).then(draw);
  }

  function tickDaily(id) {
    if (id in S.pending) return;
    const b = bridge();
    if (!b) { S.msg = 'ticking needs the Ebonwake app window'; draw(); return; }
    S.pending[id] = true;
    S.msg = '';
    draw();
    const done = function (res) {
      delete S.pending[id];
      if (res && res.ok && res.data && Array.isArray(res.data.items)) {
        S.snap.today = res.data;
        S.snap.at.today = Date.now();
      } else {
        S.msg = 'tick failed: ' + ((res && res.error) || 'unknown error');
      }
      draw();
    };
    window.EWToast.via(b).post('/api/today', { tick: id }).then(done, function (e) {
      done({ ok: false, error: String(e && e.message || e) });
    });
  }

  // ---- render ----

  function openTab(tab) {
    const b = document.querySelector('.ew-tab[data-tab="' + tab + '"]');
    if (b) b.click();
  }

  // Card ids, labels and tick ids: a change means a full redraw; otherwise
  // only the countdown values are refreshed in place.
  function shapeOf(cards) {
    return JSON.stringify(cards.map(function (c) {
      return [c.id, c.meta, c.empty, c.rows.map(function (r) { return [r.label, r.note, r.cls, r.tick]; })];
    })) + '|' + JSON.stringify(S.err) + '|' + Object.keys(S.pending).join(',') + '|' + S.msg;
  }

  function rowNode(r) {
    const n = el('div', 'ew-hrow' + (r.cls ? ' ' + r.cls : ''));
    if (r.tick) {
      const b = el('button', 'ew-btn ew-htick', 'done');
      b.type = 'button';
      b.title = 'tick on Today';
      b.disabled = r.tick in S.pending;
      b.addEventListener('click', function () { tickDaily(r.tick); });
      n.appendChild(b);
    }
    const lab = el('span', 'ew-mname', r.label);
    if (r.note) lab.title = r.note;
    n.appendChild(lab);
    n.appendChild(el('span', 'ew-muted ew-hnote', r.note));
    const v = el('span', 'ew-hval', r.value);
    n.appendChild(v);
    S.vals.push(v);
    return n;
  }

  function cardNode(c) {
    const sec = el('section', 'ew-card ew-mcard ew-hcard');
    sec.dataset.card = c.id;
    const h = el('h2', null, c.title);
    const meta = el('span', 'ew-tmeta');
    if (c.meta) meta.appendChild(el('span', 'ew-pill unknown', c.meta));
    const go = el('button', 'ew-tx', 'open');
    go.type = 'button';
    go.title = 'open the ' + c.tab + ' tab';
    go.addEventListener('click', function () { openTab(c.tab); });
    meta.appendChild(go);
    h.appendChild(meta);
    sec.appendChild(h);
    const body = el('div', 'ew-cbody');
    if (c.id === 'dailies' && S.msg) body.appendChild(el('div', 'ew-err', S.msg));
    if (c.empty) body.appendChild(el('div', 'ew-muted', c.empty));
    const list = el('div', 'ew-list');
    c.rows.forEach(function (r) { list.appendChild(rowNode(r)); });
    body.appendChild(list);
    sec.appendChild(body);
    return sec;
  }

  function errNode() {
    const keys = Object.keys(S.err);
    if (!keys.length) return null;
    return el('div', 'ew-err ew-herr', keys.map(function (k) { return k + ': ' + S.err[k]; }).join(' - '));
  }

  function draw() {
    const p = S.panel;
    if (!p || !p.isConnected) return;
    const cards = C.composeNow(S.snap, Date.now());
    const shape = shapeOf(cards);
    if (shape === S.shape) { values(cards); return; }
    S.shape = shape;
    S.vals = [];
    p.textContent = '';
    cards.forEach(function (c) { p.appendChild(cardNode(c)); });
    const e = errNode();
    if (e) p.firstChild.querySelector('.ew-cbody').appendChild(e);
  }

  function values(cards) {
    let i = 0;
    cards.forEach(function (c) {
      c.rows.forEach(function (r) {
        const v = S.vals[i++];
        if (v && v.textContent !== r.value) v.textContent = r.value;
      });
    });
  }

  // ---- mount ----

  function mount(panel) {
    panel.classList.add('ew-home');
    S.panel = panel;
    S.shape = null;
    draw();
    if (!S.timer) {
      setInterval(draw, 1000);
      poll(false);
    }
  }

  function show() { poll(false); }

  window.EWHome = { mount: mount, show: show };
})();
