/* EW shared pure logic, part `bus` (plan 107 split of ewcore.js):
    overlay market ticker, shared SSE bus, hidden-tab pause, keyed rows.
   Installed in order by ../ewcore.js, which re-exports the public names as
   EWCore. Loaded as a plain script before ewcore.js (window.EWCoreParts) and by
   require() under node --test. No DOM, no Electron. */
(function (root, install) {
  if (typeof module !== 'undefined' && module.exports) module.exports = install;
  else (root.EWCoreParts = root.EWCoreParts || {}).bus = install;
})(typeof self !== 'undefined' ? self : this, function (K) {
  'use strict';

  // from earlier parts
  const { fmtSilver, isInt, isNum, plainObject, preorderBadge, preorderState, watchAlert } = K;

  // ---- plan 029: overlay market ticker ----
  // GET /api/market/watch rows -> at most `max` (default 5) ticker rows: alert
  // hits first, then operator (watchlist) order. `base` maps "id:sid" to the
  // previous different price (tickerTrack) for the arrow. A row is stale when
  // its source age at nowMs passes its TTL, or the server already says stale.

  function tickerKey(it) { return it.id + ':' + (isNum(it.sid) ? it.sid : 0); }

  function tickerStale(fr, nowMs) {
    if (!plainObject(fr) || fr.stale === true) return true;
    const ttl = isNum(fr.ttl_s) && fr.ttl_s > 0 ? fr.ttl_s : 300;
    const at = typeof fr.fetched_at === 'string' ? Date.parse(fr.fetched_at) : NaN;
    if (isNum(at) && isNum(nowMs)) return (nowMs - at) / 1000 > ttl;
    return isNum(fr.age_s) ? fr.age_s > ttl : true;
  }

  const TICKER_ARROW = { up: String.fromCharCode(0x25b2), down: String.fromCharCode(0x25bc) };

  function tickerRows(watchRows, nowMs, max, base) {
    const n = isInt(max, 1) ? max : 5;
    const b = plainObject(base) ? base : {};
    const rows = (Array.isArray(watchRows) ? watchRows : []).filter(function (it) {
      return plainObject(it) && isInt(it.id, 1);
    }).map(function (it, i) {
      const key = tickerKey(it);
      const alert = watchAlert(it);
      const prev = Object.prototype.hasOwnProperty.call(b, key) ? b[key] : null;
      let arrow = null;
      if (isNum(it.price) && isNum(prev) && it.price !== prev) arrow = it.price > prev ? 'up' : 'down';
      const stale = tickerStale(it.freshness, nowMs);
      return {
        key: key, id: it.id, order: i,
        name: typeof it.name === 'string' && it.name ? it.name : '#' + it.id,
        price: fmtSilver(it.price), arrow: arrow, arrowText: arrow ? TICKER_ARROW[arrow] : '',
        net: isInt(it.net, 0) ? fmtSilver(it.net) : null,
        badge: preorderBadge(preorderState(it)), alert: alert, stale: stale,
        cls: 'ew-ov-val' + (alert ? ' ew-tick-hit' : '') + (stale ? ' ew-stale' : ''),
      };
    });
    rows.sort(function (a, b2) { return (a.alert ? 0 : 1) - (b2.alert ? 0 : 1) || a.order - b2.order; });
    return rows.slice(0, n);
  }

  // Poll-to-poll price memory: `last` = this poll's prices, `base` = the price
  // before the most recent change (kept across unchanged polls). Items no
  // longer watched (or without a price) drop out.
  function tickerTrack(state, watchRows) {
    const s = plainObject(state) ? state : {};
    const last0 = plainObject(s.last) ? s.last : {};
    const base0 = plainObject(s.base) ? s.base : {};
    const out = { last: {}, base: {} };
    (Array.isArray(watchRows) ? watchRows : []).forEach(function (it) {
      if (!plainObject(it) || !isInt(it.id, 1) || !isNum(it.price)) return;
      const k = tickerKey(it);
      if (isNum(last0[k]) && last0[k] !== it.price) out.base[k] = last0[k];
      else if (isNum(base0[k])) out.base[k] = base0[k];
      out.last[k] = it.price;
    });
    return out;
  }

  function pollDue(lastMs, nowMs, intervalMs) {
    return lastMs === null || lastMs === undefined || nowMs - lastMs >= intervalMs;
  }

  // ---- Plan 049: shared SSE bus, hidden-tab pause, keyed rows ----

  // In-page pub/sub fed by the dashboard's one EventSource. on() returns off();
  // a throwing listener never stops the others.
  function createBus() {
    const subs = {};
    return {
      on: function (domain, fn) {
        if (typeof domain !== 'string' || !domain || typeof fn !== 'function') return function () {};
        (subs[domain] = subs[domain] || []).push(fn);
        return function () {
          const i = subs[domain] ? subs[domain].indexOf(fn) : -1;
          if (i >= 0) subs[domain].splice(i, 1);
        };
      },
      emit: function (domain, data) {
        (subs[domain] || []).slice().forEach(function (fn) {
          try { fn(data); } catch (e) { /* one bad listener stays local */ }
        });
      },
      domains: function () {
        return Object.keys(subs).filter(function (k) { return subs[k].length > 0; });
      }
    };
  }

  // A timer poll skips while the window is hidden or the node's tab panel is
  // not the active one; show() re-polls (pollDue) when the tab comes back.
  function pollPaused(node, doc) {
    if (doc && doc.hidden) return true;
    const p = node && typeof node.closest === 'function' ? node.closest('.ew-panel') : null;
    return !!(p && p.classList && !p.classList.contains('active'));
  }

  // Keyed children update: a row whose key and JSON stay the same keeps its
  // node (focus, scroll, an open <details> survive); a changed row is rebuilt
  // in place, a new one inserted, a gone one removed, non-keyed nodes dropped.
  // render(row) builds a fresh node; only the container's own methods are used.
  function reconcile(container, rows, key, render) {
    const out = { added: 0, updated: 0, moved: 0, removed: 0 };
    const old = {};
    Array.prototype.slice.call(container.children).forEach(function (n) {
      if (n.ewKey !== undefined && !(n.ewKey in old)) old[n.ewKey] = n;
    });
    const want = [];
    const seen = {};
    (rows || []).forEach(function (row) {
      const k = String(key(row));
      if (seen[k]) return;
      seen[k] = true;
      const sig = JSON.stringify(row);
      let n = old[k];
      if (n && n.ewSig !== sig) {
        const fresh = render(row);
        container.insertBefore(fresh, n);
        container.removeChild(n);
        n = fresh;
        out.updated++;
      } else if (!n) {
        n = render(row);
        out.added++;
      }
      n.ewKey = k;
      n.ewSig = sig;
      want.push(n);
    });
    const keep = new Set(want);
    Array.prototype.slice.call(container.children).forEach(function (n) {
      if (keep.has(n)) return;
      container.removeChild(n);
      if (n.ewKey !== undefined) out.removed++;
    });
    want.forEach(function (n, i) {
      const at = container.children[i];
      if (at === n) return;
      const isNew = n.parentNode !== container;
      container.insertBefore(n, at || null);
      if (!isNew) out.moved++;
    });
    return out;
  }

  Object.assign(K, { tickerKey: tickerKey, tickerStale: tickerStale, TICKER_ARROW: TICKER_ARROW,
    tickerRows: tickerRows, tickerTrack: tickerTrack, pollDue: pollDue, createBus: createBus,
    pollPaused: pollPaused, reconcile: reconcile });
  return function link() {};
});
