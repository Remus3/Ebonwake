/* EW dashboard shell: tabs from /api/state, server pill, reset clocks.
   Plan 001 renders placeholders; plans 002+ fill each tab (home.js, market.js, today.js,
   progress.js + leveling.js, grind.js, events.js, deadeye.js; game.js on System).
   Plan 020: the pill re-checks /api/version every 30 s (and on SSE reconnect),
   flags a server older than the app, and the System tab shows data freshness. */
(function () {
  'use strict';
  const C = window.EWCore;
  const bridge = window.ewApi;
  const appCommit = bridge && typeof bridge.appCommit === 'function' ? bridge.appCommit() : 'unknown';
  let attempt = 0;
  let active = null;
  let ticks = 0;
  // Health inputs for C.healthPill; sseOk stays null until the stream reports.
  const H = { version: null, health: null, sources: null, lastOkMs: null, sseOk: null, restarting: false };

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function card(title, body) {
    const c = el('section', 'ew-card');
    c.appendChild(el('h2', null, title));
    if (body) c.appendChild(body);
    return c;
  }

  function select(id) {
    active = id;
    document.querySelectorAll('.ew-tab').forEach(function (b) {
      b.setAttribute('aria-selected', String(b.dataset.tab === id));
    });
    document.querySelectorAll('.ew-panel').forEach(function (p) {
      p.classList.toggle('active', p.dataset.tab === id);
    });
    try { localStorage.setItem('ew.tab', id); } catch (e) { /* storage optional */ }
    if (id === 'home' && window.EWHome) window.EWHome.show();
    if (id === 'market' && window.EWMarket) window.EWMarket.show();
    if (id === 'today' && window.EWToday) window.EWToday.show();
    if (id === 'progress' && window.EWProgress) window.EWProgress.show();
    if (id === 'progress' && window.EWLeveling) window.EWLeveling.show();
    if (id === 'grind' && window.EWGrind) window.EWGrind.show();
    if (id === 'events' && window.EWEvents) window.EWEvents.show();
    if (id === 'deadeye' && window.EWDeadeye) window.EWDeadeye.show();
    if (id === 'system' && window.EWGame) window.EWGame.show();
  }

  function render(state) {
    const tabs = C.normalizeTabs(state);
    const nav = document.getElementById('tabs');
    const panels = document.getElementById('panels');
    nav.textContent = '';
    panels.textContent = '';
    tabs.forEach(function (t) {
      const b = el('button', 'ew-tab', t.title);
      b.dataset.tab = t.id;
      b.setAttribute('role', 'tab');
      b.addEventListener('click', function () { select(t.id); });
      nav.appendChild(b);
      const p = el('div', 'ew-panel');
      p.dataset.tab = t.id;
      if (t.id === 'home' && window.EWHome) {
        window.EWHome.mount(p);
      } else if (t.id === 'today' && window.EWToday) {
        window.EWToday.mount(p);
      } else if (t.id === 'today') {
        p.appendChild(card('Daily reset', el('div', 'ew-num', '-')));
        p.lastChild.lastChild.id = 'daily-reset';
        p.appendChild(card('Weekly reset', el('div', 'ew-num', '-')));
        p.lastChild.lastChild.id = 'weekly-reset';
      } else if (t.id === 'progress' && window.EWProgress) {
        window.EWProgress.mount(p);
        if (window.EWLeveling) window.EWLeveling.mount(p);
      } else if (t.id === 'grind' && window.EWGrind) {
        window.EWGrind.mount(p);
      } else if (t.id === 'events' && window.EWEvents) {
        window.EWEvents.mount(p);
      } else if (t.id === 'deadeye' && window.EWDeadeye) {
        window.EWDeadeye.mount(p);
      } else if (t.id === 'market' && window.EWMarket) {
        window.EWMarket.mount(p);
      } else if (t.id === 'system') {
        const sv = el('div', 'ew-kv');
        sv.id = 'sys-server';
        p.appendChild(card('Server', sv));
        const fr = el('div', 'ew-fresh');
        fr.id = 'sys-fresh';
        p.appendChild(card('Data freshness', fr));
        if (window.EWGame) window.EWGame.mount(p);
      } else {
        p.appendChild(card(t.title, el('p', 'ew-muted', 'Arrives in plan ' + (t.plan || '?') + '.')));
      }
      panels.appendChild(p);
    });
    let saved = null;
    try { saved = localStorage.getItem('ew.tab'); } catch (e) { /* storage optional */ }
    const ids = tabs.map(function (t) { return t.id; });
    select(active && ids.indexOf(active) >= 0 ? active : (ids.indexOf(saved) >= 0 ? saved : ids[0]));
    paintSystem();
  }

  function paintPill() {
    const p = document.getElementById('server-pill');
    const s = C.healthPill({ version: H.version, appCommit: appCommit, lastOkMs: H.lastOkMs,
      nowMs: Date.now(), sseOk: H.sseOk });
    p.className = 'ew-pill ' + s.level;
    p.textContent = H.restarting ? 'server restarting' : s.text;
    const b = document.getElementById('server-restart');
    if (b) {
      b.hidden = !(s.level === 'warn' && bridge && typeof bridge.restartServer === 'function');
      b.disabled = H.restarting;
    }
  }

  function paintSystem() {
    const now = Date.now();
    const sv = document.getElementById('sys-server');
    if (sv) {
      sv.textContent = '';
      C.serverRows(H.version, H.health, appCommit, now).forEach(function (r) {
        sv.appendChild(el('span', 'ew-muted', r[0]));
        sv.appendChild(el('span', null, r[1]));
      });
    }
    const fr = document.getElementById('sys-fresh');
    if (fr) {
      fr.textContent = '';
      const rows = C.sourceFreshness(H.sources, now);
      if (!rows.length) fr.appendChild(el('p', 'ew-muted', 'no source data yet'));
      rows.forEach(function (r) {
        const p = el('span', 'ew-pill ' + r.cls, r.name + ' ' + r.age + ' - ' + r.status + (r.stale ? ' (stale)' : ''));
        fr.appendChild(p);
      });
    }
  }

  function getJSON(path) {
    return fetch(C.SERVER + path).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    });
  }

  // /api/version is the liveness probe; /api/health and /api/state.sources
  // ride along for the System cards. A failure leaves lastOkMs to age out.
  function pollVersion() {
    return getJSON('/api/version').then(function (v) {
      H.version = v;
      H.lastOkMs = Date.now();
      return Promise.all([
        getJSON('/api/health').then(function (h) { H.health = h; }, function () { H.health = null; }),
        getJSON('/api/state').then(function (s) { H.sources = s && s.sources; }, function () { /* keep last */ })
      ]);
    }, function () { H.health = null; }).then(function () { paintPill(); paintSystem(); });
  }

  function restart() {
    if (H.restarting || !bridge || typeof bridge.restartServer !== 'function') return;
    H.restarting = true;
    paintPill();
    bridge.restartServer().catch(function () { /* the pill reports what follows */ }).then(function () {
      H.restarting = false;
      setTimeout(pollVersion, 2000);
      setTimeout(pollVersion, 6000);
      paintPill();
    });
  }

  function watchStream() {
    if (typeof EventSource !== 'function') return;
    const src = new EventSource(C.SERVER + '/events');
    src.onopen = function () {
      const reconnect = H.sseOk === false;
      H.sseOk = true;
      H.lastOkMs = Date.now();
      if (reconnect) pollVersion();
      paintPill();
    };
    src.onmessage = function () { H.lastOkMs = Date.now(); };
    src.onerror = function () { H.sseOk = false; paintPill(); };
  }

  function load() {
    getJSON('/api/state').then(function (s) {
      attempt = 0;
      H.lastOkMs = Date.now();
      H.sources = s && s.sources;
      render(s);
      pollVersion();
    }).catch(function () {
      paintPill();
      if (!document.querySelector('.ew-tab')) render(null);
      setTimeout(load, C.backoffMs(attempt++));
    });
  }

  function tick() {
    const now = Date.now();
    const d = document.getElementById('daily-reset');
    const w = document.getElementById('weekly-reset');
    if (d) d.textContent = C.fmtDuration(C.nextDailyReset(now) - now);
    if (w) w.textContent = C.fmtDuration(C.nextWeeklyReset(now) - now);
    if (++ticks % 5 === 0) { paintPill(); paintSystem(); } // ages + the 90 s cut-off
  }

  const rb = document.getElementById('server-restart');
  if (rb) rb.addEventListener('click', restart);
  load();
  watchStream();
  setInterval(pollVersion, C.VERSION_POLL_MS);
  setInterval(tick, 1000);
})();
