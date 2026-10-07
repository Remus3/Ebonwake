/* EW dashboard shell: tabs from /api/state, server pill, reset clocks.
   Plan 001 renders placeholders; plans 002+ fill each tab (home.js, market.js, today.js,
   progress.js + leveling.js, grind.js, events.js, deadeye.js; game.js on System;
   settings.js, plan 030).
   Plan 020: the pill re-checks /api/version every 30 s (and on SSE reconnect),
   flags a server older than the app, and the System tab shows data freshness.
   Plan 073: that card is now the signal health digest (/api/signals). */
(function () {
  'use strict';
  const C = window.EWCore;
  const bridge = window.ewApi;
  const appCommit = bridge && typeof bridge.appCommit === 'function' ? bridge.appCommit() : 'unknown';
  let attempt = 0;
  let active = null;
  let ticks = 0;
  const GEAR = String.fromCharCode(0x2699); // System icon tab (plan 047)
  // Health inputs for C.healthPill; sseOk stays null until the stream reports.
  const H = { version: null, health: null, sources: null, signals: null, lastOkMs: null, sseOk: null,
    restarting: false };
  // Plan 049: the one /events stream fans out here; modules subscribe in mount().
  const bus = window.EWBus = C.createBus();
  const DOMAINS = ['today', 'grind', 'game', 'leveling', 'market', 'progress', 'events', 'whatnow', 'ocr'];

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

  // Plan 076: cards marked data-collapse get a header toggle; collapsed shows
  // the header (title + the module's own summary pill) only. Default
  // collapsed; the expanded keys persist in localStorage (corrupt = default).
  const OPEN_KEY = 'ew.cards.open';

  function readOpen() {
    try { return C.parseCollapsed(localStorage.getItem(OPEN_KEY)); } catch (e) { return []; }
  }

  function collapsibles(panel, tab) {
    panel.querySelectorAll('[data-collapse]').forEach(function (c) {
      const h = c.querySelector('h2');
      if (!h || h.querySelector('.ew-ctoggle')) return;
      const key = C.collapsedKey(tab, c.dataset.collapse);
      const name = h.firstChild && h.firstChild.nodeType === 3 ? h.firstChild.nodeValue : c.dataset.collapse;
      const b = el('button', 'ew-tx ew-ctoggle');
      b.type = 'button';
      const apply = function () {
        const shut = C.isCollapsed(readOpen(), key);
        c.classList.toggle('ew-collapsed', shut);
        b.textContent = shut ? '+' : '-';
        b.setAttribute('aria-expanded', String(!shut));
        b.setAttribute('aria-label', (shut ? 'expand ' : 'collapse ') + name);
        b.title = b.getAttribute('aria-label');
      };
      b.addEventListener('click', function () {
        try { localStorage.setItem(OPEN_KEY, C.serializeCollapsed(C.toggleCollapsed(readOpen(), key))); } catch (e) {
          c.classList.toggle('ew-collapsed'); return; // storage optional: toggle this view only
        }
        apply();
      });
      c.classList.add('ew-collapsible');
      h.insertBefore(b, h.firstChild);
      apply();
    });
  }

  function tabIds() {
    return Array.prototype.map.call(document.querySelectorAll('.ew-tab'), function (b) { return b.dataset.tab; });
  }

  // Plan 047: roving tabindex - only the selected tab is in the Tab order.
  function select(id, focus) {
    if (id === undefined) id = active; // plan 049: re-show after the window un-hides
    if (id === null) return;
    active = id;
    document.querySelectorAll('.ew-tab').forEach(function (b) {
      const on = b.dataset.tab === id;
      b.setAttribute('aria-selected', String(on));
      b.tabIndex = on ? 0 : -1;
      if (on && focus) b.focus();
    });
    document.querySelectorAll('.ew-panel').forEach(function (p) {
      p.classList.toggle('active', p.dataset.tab === id);
    });
    try { localStorage.setItem('ew.tab', id); } catch (e) { /* storage optional */ }
    if (id === 'home' && window.EWHome) window.EWHome.show();
    if (id === 'market' && window.EWMarket) window.EWMarket.show();
    if (id === 'market' && window.EWCrafting) window.EWCrafting.show();
    if (id === 'today' && window.EWToday) window.EWToday.show();
    if (id === 'progress' && window.EWProgress) window.EWProgress.show();
    if (id === 'progress' && window.EWLeveling) window.EWLeveling.show();
    if (id === 'progress' && window.EWPets) window.EWPets.show();
    if (id === 'progress' && window.EWInventory) window.EWInventory.show();
    if (id === 'grind' && window.EWGrind) window.EWGrind.show();
    if (id === 'events' && window.EWEvents) window.EWEvents.show();
    if (id === 'deadeye' && window.EWDeadeye) window.EWDeadeye.show();
    if (id === 'system' && window.EWGame) window.EWGame.show();
    if (id === 'settings' && window.EWSettings) window.EWSettings.show();
  }

  function render(state) {
    const tabs = C.orderTabs(C.normalizeTabs(state));
    const nav = document.getElementById('tabs');
    const panels = document.getElementById('panels');
    nav.textContent = '';
    panels.textContent = '';
    tabs.forEach(function (t) {
      const b = el('button', 'ew-tab' + (t.edge ? ' ew-tab-edge' : ''));
      b.type = 'button';
      b.appendChild(el('span', null, t.edge ? GEAR : t.title));
      const badge = el('span', 'ew-tab-badge');
      badge.hidden = true;
      b.appendChild(badge);
      if (t.edge) { b.title = t.title; b.setAttribute('aria-label', t.title); }
      b.dataset.tab = t.id;
      b.id = 'tab-' + t.id;
      b.setAttribute('role', 'tab');
      b.setAttribute('aria-controls', 'panel-' + t.id);
      b.tabIndex = -1;
      b.addEventListener('click', function () { select(t.id); });
      nav.appendChild(b);
      const p = el('div', 'ew-panel');
      p.dataset.tab = t.id;
      p.id = 'panel-' + t.id;
      p.setAttribute('role', 'tabpanel');
      p.setAttribute('aria-labelledby', 'tab-' + t.id);
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
        if (window.EWPets) window.EWPets.mount(p); // plan 043
        if (window.EWInventory) window.EWInventory.mount(p); // plan 045
        collapsibles(p, t.id); // plan 076
      } else if (t.id === 'grind' && window.EWGrind) {
        window.EWGrind.mount(p);
      } else if (t.id === 'events' && window.EWEvents) {
        window.EWEvents.mount(p);
      } else if (t.id === 'deadeye' && window.EWDeadeye) {
        window.EWDeadeye.mount(p);
      } else if (t.id === 'market' && window.EWMarket) {
        window.EWMarket.mount(p);
        if (window.EWCrafting) window.EWCrafting.mount(p); // plan 054
      } else if (t.id === 'settings' && window.EWSettings) {
        window.EWSettings.mount(p);
      } else if (t.id === 'system') {
        const sv = el('div', 'ew-kv');
        sv.id = 'sys-server';
        p.appendChild(card('Server', sv));
        const fr = el('div', 'ew-fresh');
        fr.id = 'sys-fresh';
        p.appendChild(card('Signal health', fr)); // plan 073: was "Data freshness"
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
    paintBadges();
  }

  // Badges ride on the Home snapshots (polled every 60 s whatever tab shows).
  function paintBadges() {
    const snaps = window.EWHome && typeof window.EWHome.snapshots === 'function' ? window.EWHome.snapshots() : null;
    const badges = C.tabBadges(snaps, Date.now());
    document.querySelectorAll('.ew-tab').forEach(function (b) {
      const s = b.querySelector('.ew-tab-badge');
      if (!s) return;
      const text = badges[b.dataset.tab] || '';
      if (s.textContent !== text) s.textContent = text;
      s.hidden = !text;
    });
  }

  // Left/Right/Home/End on the strip; Ctrl+1..9 anywhere in this window
  // (dashboard only - the overlay is focus-less and has no tab strip).
  function onKey(ev) {
    const onStrip = ev.target && ev.target.classList && ev.target.classList.contains('ew-tab');
    if (!ev.ctrlKey && !onStrip) return;
    const id = C.tabKey(tabIds(), active, ev);
    if (!id) return;
    ev.preventDefault();
    select(id, onStrip);
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
        const val = el('span', null, r[1]);
        if (r[2]) val.title = r[2]; // plan 078: local time shown, UTC on hover
        sv.appendChild(val);
      });
    }
    const fr = document.getElementById('sys-fresh');
    if (fr && H.signals) {
      // Plan 073: one row per signal with its fix hint; an older server
      // (no /api/signals) keeps the plan 020 freshness pills below.
      fr.textContent = '';
      fr.classList.add('ew-sig');
      C.signalRows(H.signals).forEach(function (r) {
        const row = el('div', 'ew-sigrow');
        const p = el('span', 'ew-pill ' + r.cls, r.text);
        if (r.detail) p.title = r.detail;
        row.appendChild(p);
        if (r.hint && r.level !== 'ok') row.appendChild(el('span', 'ew-muted', r.hint));
        fr.appendChild(row);
      });
      // Plan 079: the override ledger's active entries, each with clear.
      C.overrideRows(H.signals.overrides).forEach(function (o) {
        const row = el('div', 'ew-sigrow');
        const p = el('span', 'ew-pill warn ew-ovr', 'override');
        p.title = o.title;
        row.appendChild(p);
        row.appendChild(el('span', 'ew-muted', o.text));
        if (o.clearable && window.EWOverrides) {
          const x = el('button', 'ew-tx', 'clear');
          x.type = 'button';
          x.title = 'clear this override (back to the default)';
          x.addEventListener('click', function () { window.EWOverrides.clear(o.key); });
          row.appendChild(x);
        }
        fr.appendChild(row);
      });
    } else if (fr) {
      fr.textContent = '';
      fr.classList.remove('ew-sig');
      const rows = C.sourceFreshness(H.sources, now);
      if (!rows.length) fr.appendChild(el('p', 'ew-muted', 'no source data yet'));
      rows.forEach(function (r) {
        const p = el('span', 'ew-pill ' + r.cls, r.name + ' ' + r.age + ' - ' + r.status + (r.stale ? ' (stale)' : ''));
        fr.appendChild(p);
      });
      // Plan 072: boss schedule drift banner (details on the Bosses card).
      const drift = C.bossDriftBanner(H.sources && H.sources.bossdrift);
      if (drift) fr.appendChild(el('div', 'ew-drift', drift.text + ' (see World bosses)'));
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
        getJSON('/api/state').then(function (s) { H.sources = s && s.sources; }, function () { /* keep last */ }),
        getJSON('/api/signals').then(function (s) { H.signals = s; }, function () { H.signals = null; })
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

  // Named events carry a domain name (plan 049) or a full view (game, leveling);
  // listeners get the parsed data or null. A reconnect re-emits every domain
  // with null so each module re-GETs what it may have missed.
  function watchStream() {
    if (typeof EventSource !== 'function') return;
    const src = new EventSource(C.SERVER + '/events');
    src.onopen = function () {
      const reconnect = H.sseOk === false;
      H.sseOk = true;
      H.lastOkMs = Date.now();
      if (reconnect) {
        pollVersion();
        DOMAINS.forEach(function (d) { bus.emit(d, null); });
      }
      paintPill();
    };
    src.onmessage = function () { H.lastOkMs = Date.now(); };
    src.onerror = function () { H.sseOk = false; paintPill(); };
    DOMAINS.forEach(function (d) {
      src.addEventListener(d, function (ev) {
        H.lastOkMs = Date.now();
        let data = null;
        try { data = JSON.parse(ev.data); } catch (e) { data = null; }
        bus.emit(d, data);
      });
    });
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
    if (++ticks % 5 === 0) { paintPill(); paintSystem(); paintBadges(); } // ages + the 90 s cut-off
  }

  // Plan 050: the command palette's `go` and search jumps.
  window.EWDash = { select: function (id) { if (tabIds().indexOf(id) >= 0) select(id); } };

  const rb = document.getElementById('server-restart');
  if (rb) rb.addEventListener('click', restart);
  // Plan 078: the palette (plan 050, Ctrl+K in this window) gets a visible button.
  const pb = document.getElementById('palette-open');
  if (pb) {
    pb.title = C.paletteTitle();
    pb.addEventListener('click', function () { if (window.EWPalette) window.EWPalette.open(); });
  }
  document.addEventListener('keydown', onKey);
  // Timers skip while hidden (C.pollPaused); coming back re-shows the tab.
  document.addEventListener('visibilitychange', function () { if (!document.hidden) select(); });
  load();
  watchStream();
  setInterval(pollVersion, C.VERSION_POLL_MS);
  setInterval(tick, 1000);
})();
