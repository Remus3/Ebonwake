/* EW Home / Now tab (plan 025): one glance screen. Reads the existing GETs
   (/api/today, /api/grind, /api/leveling, /api/events, /api/market/watch,
   plan 032 /api/bosses - a read-only World bosses card, plan 046
   /api/summary - the last game session's summary card, plan 051
   /api/onboarding - the "Get started" first-run card, plan 069
   /api/whatnow - the "What now" card on top, also replaced from each SSE
   `whatnow` event's full view, plan 073 /api/signals - one pill on top only
   while a signal is bad, plan 074 /api/maint/digest - the "Before
   maintenance" card from T-24 h) and
   lets C.composeNow order the cards; a 404 (old server) drops that payload's
   card, other errors keep the last data. Plan 076: What now spans the full
   width, each action one button row (text never clipped, `why` on a muted
   second line); cards with nothing to show collapse into one muted "Quiet:"
   line at the bottom, each name a link to its tab. Read-only except the one-click tick
   of a daily, which reuses the Today tick route through the dashboard preload,
   the first-run card's dismiss (POST /api/onboarding, same bridge) and the
   ack of one loss warning (POST /api/maint/digest, same bridge).
   Countdowns update in place every second; a structural change redraws. Every
   node is built with DOM APIs - no HTML from data. */
(function () {
  'use strict';
  const C = window.EWCore;
  const POLL_MS = 60000;
  const SOURCES = { today: '/api/today', grind: '/api/grind', leveling: '/api/leveling',
    events: '/api/events', market: '/api/market/watch', bosses: '/api/bosses', summary: '/api/summary',
    onboarding: '/api/onboarding', whatnow: '/api/whatnow', signals: '/api/signals',
    maint: '/api/maint/digest' };
  const S = { snap: { at: {} }, err: {}, last: null, timer: null, panel: null, shape: null, vals: [],
    pending: {}, msg: '', obMsg: '', mdMsg: '' };

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

  // Plan 074: hide one loss warning until its maintenance is over.
  function ackMaint(key) {
    const pk = 'ack:' + key;
    if (pk in S.pending) return;
    const b = bridge();
    if (!b) { S.mdMsg = 'ack needs the Ebonwake app window'; draw(); return; }
    S.pending[pk] = true;
    S.mdMsg = '';
    draw();
    const done = function (res) {
      delete S.pending[pk];
      if (res && res.ok && res.data && Array.isArray(res.data.warnings)) {
        S.snap.maint = res.data;
        S.snap.at.maint = Date.now();
      } else {
        S.mdMsg = 'ack failed: ' + ((res && res.error) || 'unknown error');
      }
      draw();
    };
    window.EWToast.via(b).post('/api/maint/digest', { ack: key }).then(done, function (e) {
      done({ ok: false, error: String(e && e.message || e) });
    });
  }

  // Plan 051: hide the first-run card for good (stored server-side; only an
  // API POST {restore: true} brings it back).
  function dismissOnboarding() {
    if ('onboarding' in S.pending) return;
    const b = bridge();
    if (!b) { S.obMsg = 'dismiss needs the Ebonwake app window'; draw(); return; }
    S.pending.onboarding = true;
    S.obMsg = '';
    draw();
    const done = function (res) {
      delete S.pending.onboarding;
      if (res && res.ok && res.data && Array.isArray(res.data.steps)) {
        S.snap.onboarding = res.data;
        S.snap.at.onboarding = Date.now();
      } else {
        S.obMsg = 'dismiss failed: ' + ((res && res.error) || 'unknown error');
      }
      draw();
    };
    window.EWToast.via(b).post('/api/onboarding', { dismiss: true }).then(done, function (e) {
      done({ ok: false, error: String(e && e.message || e) });
    });
  }

  // ---- render ----

  function openTab(tab) {
    const b = Array.prototype.find.call(document.querySelectorAll('.ew-tab'), function (t) {
      return t.dataset.tab === tab;
    });
    if (b) b.click();
  }

  // Open a step's tab, then focus its Settings field once the tab has drawn.
  function openStep(go) {
    openTab(go.tab);
    if (!go.field) return;
    let tries = 0;
    (function focus() {
      const f = Array.prototype.find.call(document.querySelectorAll('[data-key]'), function (n) {
        return n.dataset.key === go.field;
      });
      if (f) { f.focus(); return; }
      if (++tries < 20) setTimeout(focus, 100);
    })();
  }

  // Card ids, labels and tick ids: a change means a full redraw; otherwise
  // only the countdown values are refreshed in place.
  function shapeOf(out) {
    return JSON.stringify(out.cards.map(function (c) {
      return [c.id, c.title, c.meta, c.empty, c.rows.map(function (r) { return [r.label, r.note, r.cls, r.tick, r.ack]; })];
    })) + '|' + JSON.stringify(out.quiet) + '|' + JSON.stringify(S.err) + '|' + Object.keys(S.pending).join(',') +
      '|' + S.msg + '|' + S.obMsg + '|' + S.mdMsg + '|' + JSON.stringify(C.signalPill(S.snap.signals));
  }

  // Plan 073: one pill on top only while a signal is `bad`; it opens System.
  function signalNode() {
    const pill = C.signalPill(S.snap.signals);
    if (!pill) return null;
    const b = el('button', 'ew-pill ew-hsig ' + pill.cls, pill.text);
    b.type = 'button';
    b.title = pill.title;
    b.addEventListener('click', function () { openTab(pill.tab); });
    return b;
  }

  // Plan 076: one What now action = one button opening its tab; the text
  // wraps (never ellipsized) and `why` sits on a muted second line.
  function wnRowNode(r) {
    const n = el('button', 'ew-wnrow' + (r.cls ? ' ' + r.cls : ''));
    n.type = 'button';
    n.title = 'open ' + r.go.tab;
    n.addEventListener('click', function () { openTab(r.go.tab); });
    const top = el('span', 'ew-wnline');
    top.appendChild(el('span', 'ew-wntext', r.label));
    const v = el('span', 'ew-hval', r.value);
    top.appendChild(v);
    S.vals.push(v);
    n.appendChild(top);
    if (r.note) n.appendChild(el('span', 'ew-muted ew-wnwhy', r.note));
    return n;
  }

  function rowNode(r) {
    const n = el('div', 'ew-hrow' + (r.cls ? ' ' + r.cls : ''));
    if (r.tick) {
      const b = el('button', 'ew-btn ew-htick', 'tick');
      b.type = 'button';
      b.title = 'tick on Today';
      b.setAttribute('aria-label', 'tick ' + r.label);
      b.disabled = r.tick in S.pending;
      b.addEventListener('click', function () { tickDaily(r.tick); });
      n.appendChild(b);
    }
    if (r.ack) {
      const a = el('button', 'ew-btn ew-htick', 'ack');
      a.type = 'button';
      a.title = 'claimed - hide until this maintenance is over';
      a.disabled = ('ack:' + r.ack) in S.pending;
      a.addEventListener('click', function () { ackMaint(r.ack); });
      n.appendChild(a);
    }
    if (r.go) {
      const g = el('button', 'ew-btn ew-htick', 'open');
      g.type = 'button';
      g.title = 'open ' + r.go.tab + (r.go.field ? ' > ' + r.go.field : '');
      g.addEventListener('click', function () { openStep(r.go); });
      n.appendChild(g);
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
    const sec = el('section', 'ew-card ew-mcard ew-hcard' + (c.wide ? ' ew-wide' : ''));
    sec.dataset.card = c.id;
    const h = el('h2', null, c.title);
    const meta = el('span', 'ew-tmeta');
    if (c.meta) meta.appendChild(el('span', 'ew-pill unknown', c.meta));
    const go = el('button', 'ew-tx', 'open');
    go.type = 'button';
    go.title = 'open the ' + c.tab + ' tab';
    go.setAttribute('aria-label', 'open ' + c.tab);
    go.addEventListener('click', function () { openTab(c.tab); });
    meta.appendChild(go);
    if (c.dismiss) {
      const x = el('button', 'ew-tx', 'dismiss');
      x.type = 'button';
      x.title = 'hide this checklist';
      x.disabled = 'onboarding' in S.pending;
      x.addEventListener('click', dismissOnboarding);
      meta.appendChild(x);
    }
    h.appendChild(meta);
    sec.appendChild(h);
    const body = el('div', 'ew-cbody');
    if (c.id === 'dailies' && S.msg) body.appendChild(el('div', 'ew-err', S.msg));
    if (c.id === 'onboarding' && S.obMsg) body.appendChild(el('div', 'ew-err', S.obMsg));
    if (c.id === 'maintdigest' && S.mdMsg) body.appendChild(el('div', 'ew-err', S.mdMsg));
    if (c.empty) body.appendChild(el('div', 'ew-muted', c.empty));
    const list = el('div', 'ew-list');
    c.rows.forEach(function (r) { list.appendChild(c.id === 'whatnow' ? wnRowNode(r) : rowNode(r)); });
    body.appendChild(list);
    sec.appendChild(body);
    return sec;
  }

  // Plan 076: "Quiet: buffs, grind, alerts" - each name opens its tab.
  function quietNode(quiet) {
    if (!quiet.length) return null;
    const n = el('div', 'ew-muted ew-hquiet ew-wide', 'Quiet: ');
    quiet.forEach(function (q, i) {
      if (i) n.appendChild(document.createTextNode(', '));
      const b = el('button', 'ew-tx', q.title);
      b.type = 'button';
      b.title = 'nothing to show - open the ' + q.tab + ' tab';
      b.setAttribute('aria-label', 'open ' + q.tab);
      b.addEventListener('click', function () { openTab(q.tab); });
      n.appendChild(b);
    });
    return n;
  }

  function errNode() {
    const keys = Object.keys(S.err);
    if (!keys.length) return null;
    return el('div', 'ew-err ew-herr', keys.map(function (k) { return k + ': ' + S.err[k]; }).join(' - '));
  }

  function draw() {
    const p = S.panel;
    if (!p || !p.isConnected) return;
    const out = C.composeNow(S.snap, Date.now());
    const cards = out.cards;
    const shape = shapeOf(out);
    if (shape === S.shape) { values(cards); return; }
    S.shape = shape;
    S.vals = [];
    p.textContent = '';
    cards.forEach(function (c) { p.appendChild(cardNode(c)); });
    const q = quietNode(out.quiet);
    if (q) p.appendChild(q);
    const e = errNode();
    if (e && p.firstChild) p.firstChild.querySelector('.ew-cbody').appendChild(e);
    const sig = signalNode();
    if (sig) p.insertBefore(sig, p.firstChild);
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

  // Plan 069: the SSE event carries the full view; null (a reconnect) re-GETs.
  function onWhatNow(view) {
    if (view && typeof view === 'object' && Array.isArray(view.next)) {
      S.snap.whatnow = view;
      S.snap.at.whatnow = Date.now();
      delete S.err.whatnow;
      draw();
      return;
    }
    getJSON(SOURCES.whatnow).then(function (body) {
      S.snap.whatnow = body;
      S.snap.at.whatnow = Date.now();
      delete S.err.whatnow;
    }, function (e) {
      if (e.gone) { S.snap.whatnow = null; delete S.err.whatnow; } else S.err.whatnow = e.message;
    }).then(draw);
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
      if (window.EWBus) window.EWBus.on('whatnow', onWhatNow);
    }
  }

  function show() { poll(false); }

  // Plan 047: the shell reads these for its tab badges (read-only use).
  function snapshots() { return S.snap; }

  window.EWHome = { mount: mount, show: show, snapshots: snapshots };
})();
