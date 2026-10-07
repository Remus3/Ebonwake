/* EW command palette (plan 050). Ctrl+K in this dashboard window only (its
   own keydown, never a global hotkey; nothing reaches the game). Typed
   commands go through C.parseCommand to an EXISTING POST route, one literal
   EWToast.via(...).post per route, so the result is a toast. A "/" prefix
   searches Today items, events and coupons, market items (plan 028 index),
   Deadeye notes and enhancement steps, and grind spots; picking a hit jumps
   to its tab and row. The catalog is the same GET bodies the tabs read,
   fetched when the palette opens. Every node is built with DOM APIs. */
(function () {
  'use strict';
  const C = window.EWCore;
  const SOURCES = [['today', '/api/today'], ['grind', '/api/grind'], ['events', '/api/events'],
    ['deadeye', '/api/deadeye'], ['state', '/api/state']];
  const MAX = 8;
  const S = { open: false, cat: {}, list: [], sel: 0, busy: false, itemQ: null, timer: null, back: null, ui: null };

  // The palette's only writes: one literal existing route each (C.PALETTE_ROUTES).
  const SENDERS = {
    '/api/today': function (b, body) { return window.EWToast.via(b).post('/api/today', body); },
    '/api/grind': function (b, body) { return window.EWToast.via(b).post('/api/grind', body); },
    '/api/leveling': function (b, body) { return window.EWToast.via(b).post('/api/leveling', body); },
    '/api/market/watch': function (b, body) { return window.EWToast.via(b).post('/api/market/watch', body); }
  };

  function bridge() {
    const b = window.ewApi;
    return b && typeof b.post === 'function' ? b : null;
  }

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
    });
  }

  // A failed GET keeps the last good body; the parser reports what is missing.
  function loadCatalog() {
    return Promise.all(SOURCES.map(function (s) {
      return getJSON(s[1]).then(function (d) {
        if (d && typeof d === 'object' && !Array.isArray(d)) S.cat[s[0]] = d;
      }, function () { /* keep last */ });
    })).then(function () {
      S.cat.tabs = C.normalizeTabs(S.cat.state);
      draw();
    });
  }

  // Market item names come from the server index as the operator types.
  function itemQuery(text) {
    const m = C.paletteMode(text);
    if (m.mode === 'search') return C.searchQuery(m.query);
    const t = m.text.toLowerCase();
    return t.indexOf('watch ') === 0 ? C.searchQuery(m.text.slice(6)) : null;
  }

  function loadItems(text) {
    const q = itemQuery(text);
    clearTimeout(S.timer); // a pending older query never lands after this one
    if (!q || q === S.itemQ) return;
    S.timer = setTimeout(function () {
      S.itemQ = q;
      getJSON(C.searchPath(q)).then(function (d) {
        if (S.itemQ !== q) return;
        S.cat.items = C.searchRows(d);
        draw();
      }, function () { /* names stay as they were */ });
    }, C.SEARCH_DEBOUNCE_MS);
  }

  function msg(text, cls) {
    S.ui.msg.textContent = text || '';
    S.ui.msg.className = 'ew-pal-msg' + (cls ? ' ' + cls : '');
  }

  function rows() {
    const text = S.ui.input.value;
    const m = C.paletteMode(text);
    if (m.mode === 'search') {
      return C.paletteSearch(m.query, C.paletteIndex(S.cat), MAX).map(function (h) {
        return { text: text, label: h.label, hint: h.kind + ' - ' + h.tab, hit: h };
      });
    }
    return C.paletteSuggest(text, S.cat, MAX).map(function (s) { return { text: s.text, label: s.label, hint: '' }; });
  }

  function draw() {
    if (!S.open) return;
    const ui = S.ui;
    S.list = rows();
    if (S.sel >= S.list.length) S.sel = Math.max(0, S.list.length - 1);
    ui.box.textContent = '';
    S.list.forEach(function (r, i) {
      const li = el('li', 'ew-pal-row' + (i === S.sel ? ' sel' : ''));
      li.id = 'ew-pal-opt-' + i;
      li.setAttribute('role', 'option');
      li.setAttribute('aria-selected', String(i === S.sel));
      li.appendChild(el('span', null, r.hit ? r.label : r.text));
      const hint = r.hit ? r.hint : (r.label !== r.text.trim() ? r.label : '');
      if (hint) li.appendChild(el('span', 'ew-muted', hint));
      li.addEventListener('mousedown', function (ev) { ev.preventDefault(); S.sel = i; pick(); });
      ui.box.appendChild(li);
    });
    if (S.list.length) ui.input.setAttribute('aria-activedescendant', 'ew-pal-opt-' + S.sel);
    else ui.input.removeAttribute('aria-activedescendant');
  }

  // Jump to a tab, then to the first row on it showing the hit's text.
  function jump(hit) {
    if (window.EWDash) window.EWDash.select(hit.tab);
    close();
    const panel = document.getElementById('panel-' + hit.tab);
    if (!panel) return;
    const want = hit.find.toLowerCase();
    const fields = panel.querySelectorAll('input, textarea');
    for (let i = 0; i < fields.length; i++) {
      const v = String(fields[i].value || '');
      const at = v.toLowerCase().indexOf(want);
      if (fields[i].tagName === 'TEXTAREA' && at >= 0) {
        fields[i].focus();
        fields[i].setSelectionRange(at, at + want.length);
        return;
      }
    }
    const all = panel.querySelectorAll('*');
    for (let i = 0; i < all.length; i++) {
      const n = all[i];
      if (n.children.length || n.textContent.toLowerCase().indexOf(want) < 0) continue;
      const row = n.closest('.ew-row, li, tr, label, .ew-mrow') || n;
      row.scrollIntoView({ block: 'center' });
      row.classList.add('ew-jump');
      setTimeout(function () { row.classList.remove('ew-jump'); }, 2000);
      return;
    }
  }

  function run(text) {
    const r = C.parseCommand(text, S.cat);
    if (!r.ok) { msg(r.error, 'ew-err'); return; }
    if (r.tab) { if (window.EWDash) window.EWDash.select(r.tab); close(); return; }
    const b = bridge();
    if (!b) { msg('commands need the Ebonwake app window', 'ew-err'); return; }
    if (!C.validPost(r.route, r.body) || !SENDERS[r.route]) { msg('not an allowed write', 'ew-err'); return; }
    if (S.busy) return;
    S.busy = true;
    msg(r.label + '...');
    SENDERS[r.route](b, r.body).then(function (res) {
      S.busy = false;
      if (res && res.ok) { close(); return; }
      msg('failed: ' + ((res && res.error) || 'unknown error'), 'ew-err');
    }, function (e) { S.busy = false; msg('failed: ' + (e && e.message || e), 'ew-err'); });
  }

  // Search: open the hit. Command: fill the input with the suggestion.
  function pick() {
    const r = S.list[S.sel];
    if (!r) return;
    if (r.hit) { jump(r.hit); return; }
    S.ui.input.value = r.text;
    S.ui.input.focus();
    changed();
  }

  // Enter runs what is typed; when it does not parse and a different
  // suggestion is highlighted, Enter fills that in first (never a guess run).
  function enter() {
    const text = S.ui.input.value;
    const m = C.paletteMode(text);
    if (m.mode === 'search') { pick(); return; }
    const r = C.parseCommand(text, S.cat);
    const s = S.list[S.sel];
    const same = function (a) { return a.trim().toLowerCase().replace(/\s+/g, ' '); };
    if (!r.ok && s && same(s.text) !== same(m.text)) { pick(); return; }
    run(text);
  }

  function changed() {
    S.sel = 0;
    msg('');
    loadItems(S.ui.input.value);
    draw();
  }

  // Keys typed here stay here (no tab switch behind the palette); only
  // Ctrl+K bubbles on to toggle it.
  function onInputKey(ev) {
    if (C.paletteKey(ev)) return;
    ev.stopPropagation();
    if (ev.key === 'Escape') { ev.preventDefault(); close(); return; }
    if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
      ev.preventDefault();
      const n = S.list.length;
      if (n) S.sel = (S.sel + (ev.key === 'ArrowDown' ? 1 : n - 1)) % n;
      draw();
      return;
    }
    if (ev.key === 'Tab' && S.list.length && !S.list[S.sel].hit) { ev.preventDefault(); pick(); return; }
    if (ev.key === 'Enter') { ev.preventDefault(); enter(); }
  }

  function build() {
    const wrap = el('div', 'ew-pal-back');
    wrap.hidden = true;
    const dlg = el('div', 'ew-pal');
    dlg.setAttribute('role', 'dialog');
    dlg.setAttribute('aria-modal', 'true');
    dlg.setAttribute('aria-label', 'Command palette');
    const input = el('input', 'ew-pal-input');
    input.type = 'text';
    input.maxLength = 200;
    input.spellcheck = false;
    input.placeholder = 'tick, arm, start grind, stop grind, log xp, watch, go - or / to search';
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-expanded', 'true');
    input.setAttribute('aria-controls', 'ew-pal-list');
    input.setAttribute('aria-label', 'Command');
    const box = el('ul', 'ew-pal-list');
    box.id = 'ew-pal-list';
    box.setAttribute('role', 'listbox');
    const m = el('div', 'ew-pal-msg');
    m.setAttribute('role', 'status');
    dlg.appendChild(input);
    dlg.appendChild(box);
    dlg.appendChild(m);
    wrap.appendChild(dlg);
    wrap.addEventListener('mousedown', function (ev) { if (ev.target === wrap) close(); });
    input.addEventListener('input', changed);
    input.addEventListener('keydown', onInputKey);
    document.body.appendChild(wrap);
    S.ui = { wrap: wrap, input: input, box: box, msg: m };
  }

  function open() {
    if (!S.ui) build();
    S.back = document.activeElement;
    S.open = true;
    S.ui.wrap.hidden = false;
    S.ui.input.value = '';
    S.sel = 0;
    msg('');
    draw();
    S.ui.input.focus();
    loadCatalog();
  }

  function close() {
    if (!S.open) return;
    S.open = false;
    S.ui.wrap.hidden = true;
    if (S.back && typeof S.back.focus === 'function' && document.contains(S.back)) S.back.focus();
    S.back = null;
  }

  document.addEventListener('keydown', function (ev) {
    if (!C.paletteKey(ev)) return;
    ev.preventDefault();
    if (S.open) close(); else open();
  });

  window.EWPalette = { open: open, close: close };
})();
