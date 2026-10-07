/* EW override badges (plan 079). GET /api/overrides is cached here and shared
   by every tab: `EWOverrides.mount(h2, card)` puts an `override` badge (title:
   value, source, set-at, expiry) plus a `clear` action into that card header
   for each active override whose policy lists `card`; nothing renders when the
   list is empty. Clear posts {clear: key} to /api/settings through the
   dashboard preload (window.ewApi). Every node is built with DOM APIs. */
(function () {
  'use strict';
  const C = window.EWCore;
  const MAX_AGE_MS = 30000;
  const S = { doc: null, at: 0, inflight: null, slots: [], pending: {} };

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function fetchDoc() {
    if (S.inflight) return S.inflight;
    S.inflight = fetch(C.SERVER + '/api/overrides').then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status); // 404 = old server: no badges
      return r.json();
    }).then(function (d) { S.doc = d; }, function () { S.doc = null; }).then(function () {
      S.at = Date.now();
      S.inflight = null;
      paintAll();
    });
    return S.inflight;
  }

  function refresh(force) {
    if (force || Date.now() - S.at > MAX_AGE_MS) fetchDoc();
  }

  function clear(key) {
    const b = window.ewApi;
    if (!b || typeof b.post !== 'function' || key in S.pending) return;
    S.pending[key] = true;
    paintAll();
    const post = window.EWToast ? window.EWToast.via(b).post : b.post.bind(b);
    Promise.resolve(post('/api/settings', { clear: key })).then(function (res) {
      if (res && res.ok && res.data && res.data.overrides) {
        S.doc = res.data.overrides;
        S.at = Date.now();
      }
    }, function () {}).then(function () {
      delete S.pending[key];
      paintAll();
      refresh(true);
    });
  }

  function paint(slot) {
    slot.box.textContent = '';
    C.overrideBadges(S.doc, slot.card).forEach(function (badge) {
      const p = el('span', 'ew-pill warn ' + badge.cls, badge.text);
      p.title = badge.title;
      slot.box.appendChild(p);
      if (badge.clearable) {
        const x = el('button', 'ew-tx', 'clear');
        x.type = 'button';
        x.title = 'clear this override (back to the default)';
        x.disabled = badge.key in S.pending;
        x.addEventListener('click', function (ev) { ev.stopPropagation(); clear(badge.key); });
        slot.box.appendChild(x);
      }
    });
  }

  // A header not yet in the page stays registered; one removed after it was
  // shown (a redraw replaced it) is dropped.
  function paintAll() {
    S.slots = S.slots.filter(function (s) {
      if (s.box.isConnected) s.seen = true;
      return s.box.isConnected || !s.seen;
    });
    S.slots.forEach(paint);
  }

  // Header `h` of the card whose policy id is `card` (grind, crafting, ...).
  function mount(h, card) {
    if (!h || !C || typeof C.overrideBadges !== 'function') return;
    const box = el('span', 'ew-ovrs');
    h.appendChild(box);
    const slot = { box: box, card: card };
    S.slots.push(slot);
    paint(slot);
    refresh(false);
  }

  window.EWOverrides = { mount: mount, refresh: refresh, doc: function () { return S.doc; },
    clear: clear };
})();
