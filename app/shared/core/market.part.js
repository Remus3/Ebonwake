/* EW shared pure logic, part `market` (plan 107 split of ewcore.js):
    market watch, name search, alerts, depth, derived planner inputs, net proceeds
   + pre-order queues.
   Installed in order by ../ewcore.js, which re-exports the public names as
   EWCore. Loaded as a plain script before ewcore.js (window.EWCoreParts) and by
   require() under node --test. No DOM, no Electron. */
(function (root, install) {
  if (typeof module !== 'undefined' && module.exports) module.exports = install;
  else (root.EWCoreParts = root.EWCoreParts || {}).market = install;
})(typeof self !== 'undefined' ? self : this, function (K) {
  'use strict';

  // from later parts, bound by link() once every part is installed
  let parseSilver;

  // ---- Market (plan 002) ----

  function isNum(v) { return typeof v === 'number' && isFinite(v); }

  // Silver amounts to 3 significant digits: 1.23B, 45.6M, 789K, 950.
  function fmtSilver(n) {
    if (!isNum(n)) return '-';
    const a = Math.abs(n);
    const sign = n < 0 && Math.round(a) !== 0 ? '-' : '';
    if (Math.round(a) < 1000) return sign + Math.round(a);
    const units = [['K', 1e3], ['M', 1e6], ['B', 1e9], ['T', 1e12]];
    let i = 0;
    while (i < units.length - 1 && a >= units[i + 1][1]) i++;
    for (; i < units.length; i++) {
      const v = Number((a / units[i][1]).toPrecision(3));
      if (v < 1000 || i === units.length - 1) return sign + v + units[i][0];
    }
    return '-';
  }

  // Exact silver with thousands separators (plan 028): 1,234,567,890.
  function fmtSilverExact(n) {
    if (!isNum(n)) return '-';
    const r = Math.round(Math.abs(n));
    const sign = n < 0 && r !== 0 ? '-' : '';
    return sign + String(r).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  }

  // ---- Market name search (plan 028) ----

  const SEARCH_DEBOUNCE_MS = 250;
  const SEARCH_MIN = 2;
  const SEARCH_MAX = 40;

  // Typed text -> the query the server accepts (trimmed, 2-40 printable
  // ASCII), or null when it would only earn a 400.
  function searchQuery(text) {
    if (typeof text !== 'string') return null;
    const q = text.trim();
    if (q.length < SEARCH_MIN || q.length > SEARCH_MAX) return null;
    return /^[\x20-\x7e]+$/.test(q) ? q : null;
  }

  function searchPath(q) { return '/api/market/search?q=' + encodeURIComponent(q); }

  function isId(v) { return typeof v === 'number' && Number.isInteger(v) && v >= 0 && v <= 2147483647; }

  // /api/market/search body -> [{id, sid, name, label}] (junk rows dropped).
  function searchRows(body) {
    const items = body && Array.isArray(body.items) ? body.items : [];
    return items.filter(function (r) {
      return r && isId(r.id) && isId(r.sid) && typeof r.name === 'string' && r.name !== '';
    }).map(function (r) {
      return { id: r.id, sid: r.sid, name: r.name,
        label: r.name + (r.sid ? ' [' + r.sid + ']' : '') + '  #' + r.id };
    });
  }

  // [[epoch_ms, price], ...] -> valid points sorted by time.
  function cleanPoints(points) {
    if (!Array.isArray(points)) return [];
    return points.filter(function (p) {
      return Array.isArray(p) && p.length >= 2 && isNum(p[0]) && isNum(p[1]);
    }).slice().sort(function (a, b) { return a[0] - b[0]; });
  }

  function r1(v) { return String(Math.round(v * 10) / 10); }

  // SVG path `d` for a sparkline in a w x h box (y inverted: high price at top).
  function sparkPath(points, w, h) {
    const pts = cleanPoints(points);
    if (!pts.length) return '';
    const t0 = pts[0][0];
    const t1 = pts[pts.length - 1][0];
    let lo = Infinity;
    let hi = -Infinity;
    pts.forEach(function (p) { lo = Math.min(lo, p[1]); hi = Math.max(hi, p[1]); });
    const y = function (p) { return hi === lo ? h / 2 : (hi - p) / (hi - lo) * h; };
    if (pts.length === 1 || t1 === t0) {
      return 'M0,' + r1(y(pts[0][1])) + 'L' + r1(w) + ',' + r1(y(pts[0][1]));
    }
    return pts.map(function (p, i) {
      return (i ? 'L' : 'M') + r1((p[0] - t0) / (t1 - t0) * w) + ',' + r1(y(p[1]));
    }).join('');
  }

  function historyStats(points) {
    const pts = cleanPoints(points);
    if (!pts.length) return { min: null, max: null, last: null };
    const ps = pts.map(function (p) { return p[1]; });
    return { min: Math.min.apply(null, ps), max: Math.max.apply(null, ps), last: ps[ps.length - 1] };
  }

  // Same rule as the server (slice A): below wins when both hold; plan 052
  // `below_p20` (opt-in) only when no explicit threshold hit.
  function alertFor(price, below, above, bands, p20) {
    if (!isNum(price)) return null;
    if (isNum(below) && price <= below) return 'below';
    if (isNum(above) && price >= above) return 'above';
    if (p20 === true && bands && isNum(bands.p20) && price < bands.p20) return 'below_p20';
    return null;
  }

  const MARKET_ALERTS = ['below', 'above', 'below_p20'];

  // A watch row's alert: the server's when it sent a known kind, else derived.
  function watchAlert(it) {
    if (!it || typeof it !== 'object') return null;
    if (MARKET_ALERTS.indexOf(it.alert) >= 0) return it.alert;
    return alertFor(it.price, it.below, it.above, it.bands, it.p20);
  }

  // Plan 071: badge for an auto-watched row ({text, title}) or null for a
  // manual row; title names the band thresholds when the server used them.
  function autoWatchBadge(it) {
    if (!it || typeof it !== 'object' || it.auto !== true) return null;
    const b = it.auto_band;
    if (it.threshold === 'auto band' && b && typeof b === 'object') {
      const parts = [];
      if (isNum(b.below)) parts.push('below ' + fmtSilverExact(b.below));
      if (isNum(b.above)) parts.push('above ' + fmtSilverExact(b.above));
      return { text: 'auto band', title: 'auto-watched; alert ' + parts.join(' / ') + ' (90-day p20 / p80)' };
    }
    return { text: 'auto', title: 'auto-watched from shopping list, loot or recipes; no band yet' };
  }

  // Plan 052 band strip: positions (0..1) of p20/p50/p80 and the current
  // price on a scale spanning the band and the price, or null without a band.
  function bandStrip(bands, price) {
    if (!bands || typeof bands !== 'object' || !['p20', 'p50', 'p80'].every(function (k) { return isNum(bands[k]); })) return null;
    const vals = [bands.p20, bands.p80].concat(isNum(price) ? [price] : []);
    const lo = Math.min.apply(null, vals);
    const hi = Math.max.apply(null, vals);
    const x = function (v) { return hi === lo ? 0.5 : (v - lo) / (hi - lo); };
    const basis = bands.basis === 'history' ? '90d history' : 'own samples';
    return {
      p20: x(bands.p20), p50: x(bands.p50), p80: x(bands.p80),
      price: isNum(price) ? x(price) : null,
      zone: !isNum(price) ? null : price < bands.p20 ? 'low' : price > bands.p80 ? 'high' : 'mid',
      text: 'p20 ' + fmtSilver(bands.p20) + '  p50 ' + fmtSilver(bands.p50) + '  p80 ' + fmtSilver(bands.p80),
      source: basis + ', n=' + (isNum(bands.n) ? bands.n : '?') + (isNum(bands.age_s) ? ', ' + fmtAge(bands.age_s) + ' old' : '')
    };
  }

  // Order book -> top-n sell levels (lowest first) and buy levels (highest
  // first); w = count relative to the largest shown level (0..1).
  function depthBars(orders, n) {
    const k = n === undefined ? 5 : n;
    const ok = Array.isArray(orders) ? orders.filter(function (o) { return o && isNum(o.price); }) : [];
    const side = function (key, dir) {
      return ok.filter(function (o) { return isNum(o[key]) && o[key] > 0; })
        .sort(function (a, b) { return dir * (a.price - b.price); })
        .slice(0, k).map(function (o) { return { price: o.price, count: o[key] }; });
    };
    const sell = side('sellers', 1);
    const buy = side('buyers', -1);
    const max = Math.max.apply(null, [0].concat(sell, buy).map(function (l) { return l.count || 0; }));
    const w = function (l) { return { price: l.price, count: l.count, w: max ? l.count / max : 0 }; };
    return { sell: sell.map(w), buy: buy.map(w) };
  }

  function fmtAge(s) {
    const v = Math.max(0, Math.floor(s));
    if (v < 60) return v + 's';
    if (v < 3600) return Math.floor(v / 60) + 'm';
    if (v < 86400) return Math.floor(v / 3600) + 'h';
    return Math.floor(v / 86400) + 'd';
  }

  // Plan 081: source + age of one derived planner input (server derived.pick
  // row {source, age_s, stale, sessions?}) -> {text, stale}. A stale value
  // (> 7 d) says so in words; the caller adds the plan 077 muted class.
  function derivedText(row) {
    if (!plainObject(row) || typeof row.source !== 'string') return { text: '', stale: false };
    const s = row.source;
    let text;
    if (s === 'typed' || s === 'operator') text = 'typed';
    else if (s === 'default') text = 'default';
    else if (s === 'profile') text = 'from profile';
    else if (/^sessions:/.test(s)) {
      const n = isInt(row.sessions, 0) ? row.sessions : Number(s.slice(9));
      text = 'from ' + n + ' play session' + (n === 1 ? '' : 's');
    } else if (s === 'ocr' || /^ocr:/.test(s)) text = 'from screenshot';
    else text = 'from ' + s;
    if (text !== 'typed' && text !== 'default' && isNum(row.age_s)) text += ', ' + fmtAge(row.age_s) + ' ago';
    const stale = row.stale === true;
    return { text: stale ? text + ' - stale' : text, stale: stale };
  }

  // Freshness pill from slice A's {fetched_at, age_s, ttl_s, stale, error}.
  // Stale data is never shown as ok.
  // /api/market/item nests freshness per source {sub, history, orders}; flatten
  // to the sub's freshness, stale if any source is stale, first error wins.
  function itemFreshness(fr) {
    if (!fr || typeof fr !== 'object' || !('sub' in fr)) return fr;
    const parts = ['sub', 'history', 'orders'].map(function (k) { return fr[k]; })
      .filter(function (x) { return x && typeof x === 'object'; });
    const base = Object.assign({}, fr.sub || {});
    base.stale = parts.some(function (x) { return x.stale; }) || !fr.sub;
    const err = parts.map(function (x) { return x.error; }).filter(Boolean)[0];
    base.error = err || null;
    return base;
  }

  // Pill for any cached web source (market, profile): same freshness shape.
  function sourcePill(fr, name, dfltTtl) {
    const error = (fr && fr.error) || null;
    if (!fr || !fr.fetched_at || !isNum(fr.age_s)) {
      return { cls: 'unknown', label: name + ' no data', stale: true, error: error };
    }
    const ttl = isNum(fr.ttl_s) && fr.ttl_s > 0 ? fr.ttl_s : dfltTtl;
    let cls = fr.age_s <= ttl ? 'ok' : (fr.age_s <= 3 * ttl ? 'warn' : 'bad');
    if (fr.stale && cls === 'ok') cls = 'warn';
    return { cls: cls, label: name + ' ' + fmtAge(fr.age_s) + ' ago', stale: !!fr.stale, error: error };
  }

  function marketPill(fr) { return sourcePill(fr, 'arsha', 300); }

  function isInt(v, min) {
    return typeof v === 'number' && Number.isSafeInteger(v) && v >= min;
  }

  function plainObject(o) {
    return !!o && typeof o === 'object' && !Array.isArray(o);
  }

  // Exact shape check for POST /api/market/watch bodies (main-process IPC guard).
  function validWatchBody(body) {
    if (!plainObject(body)) return false;
    const keys = Object.keys(body);
    if (keys.length !== 1 || (keys[0] !== 'add' && keys[0] !== 'remove')) return false;
    const e = body[keys[0]];
    if (!plainObject(e)) return false;
    const allowed = keys[0] === 'add' ? ['id', 'sid', 'below', 'above', 'p20'] : ['id', 'sid'];
    if (!Object.keys(e).every(function (k) { return allowed.indexOf(k) >= 0; })) return false;
    if (!isInt(e.id, 1) || !isInt(e.sid, 0)) return false;
    if (e.p20 !== undefined && typeof e.p20 !== 'boolean') return false;
    return ['below', 'above'].every(function (k) {
      return e[k] === undefined || e[k] === null || isInt(e[k], 0);
    });
  }

  // Add/edit form strings -> POST body, or an error for the operator.
  function parseWatchForm(form, action) {
    const f = form || {};
    const num = function (k, min, dflt) {
      const s = f[k] === undefined || f[k] === null ? '' : String(f[k]).trim();
      if (s === '') return dflt;
      if (!/^\d+$/.test(s)) return NaN;
      const v = Number(s);
      return Number.isSafeInteger(v) && v >= min ? v : NaN;
    };
    if (action !== 'add' && action !== 'remove') return { ok: false, error: 'unknown action' };
    const id = num('id', 1, NaN);
    if (Number.isNaN(id)) return { ok: false, error: 'id must be a whole number >= 1' };
    const sid = num('sid', 0, 0);
    if (Number.isNaN(sid)) return { ok: false, error: 'sid must be a whole number >= 0' };
    if (action === 'remove') return { ok: true, body: { remove: { id: id, sid: sid } } };
    const add = { id: id, sid: sid };
    for (const k of ['below', 'above']) {
      const s = f[k] === undefined || f[k] === null ? '' : String(f[k]).trim();
      if (s === '') continue;
      const v = parseSilver(s);
      if (v === null) return { ok: false, error: k + ': a silver amount like 1.2b, 850m or 1,234,567' };
      add[k] = v;
    }
    if (f.p20 === true) add.p20 = true; // plan 052 opt-in; off = key absent
    return { ok: true, body: { add: add } };
  }

  // ---- plan 027: net proceeds after tax + pre-order queues ----

  // Mirrors server/ew/market.py net_proceeds: rates in basis points, integer
  // maths in BigInt so a 1 T sale floors exactly. opts = /api/market/watch
  // `tax` {vp, fame_pct, tax, vp_bonus}; missing rates use the tracked defaults.
  function netProceeds(price, opts) {
    const o = opts || {};
    if (!isInt(price, 0)) return null;
    const fame = o.fame_pct === undefined || o.fame_pct === null ? 0 : o.fame_pct;
    if (!isNum(fame) || fame < 0 || fame > 1.5) return null;
    const bp = function (r) { return Math.round(r * 10000); };
    const keep = 10000 - bp(isNum(o.tax) ? o.tax : 0.35);
    const mult = 10000 + (o.vp === true ? bp(isNum(o.vp_bonus) ? o.vp_bonus : 0.30) : 0) +
      Math.round(fame * 100);
    return Number(BigInt(price) * BigInt(keep) * BigInt(mult) / 100000000n);
  }

  Object.assign(K, { isNum: isNum, fmtSilver: fmtSilver, fmtSilverExact: fmtSilverExact,
    SEARCH_DEBOUNCE_MS: SEARCH_DEBOUNCE_MS, SEARCH_MIN: SEARCH_MIN, SEARCH_MAX: SEARCH_MAX,
    searchQuery: searchQuery, searchPath: searchPath, isId: isId, searchRows: searchRows,
    cleanPoints: cleanPoints, r1: r1, sparkPath: sparkPath, historyStats: historyStats,
    alertFor: alertFor, MARKET_ALERTS: MARKET_ALERTS, watchAlert: watchAlert,
    autoWatchBadge: autoWatchBadge, bandStrip: bandStrip, depthBars: depthBars, fmtAge: fmtAge,
    derivedText: derivedText, itemFreshness: itemFreshness, sourcePill: sourcePill,
    marketPill: marketPill, isInt: isInt, plainObject: plainObject,
    validWatchBody: validWatchBody, parseWatchForm: parseWatchForm, netProceeds: netProceeds });
  return function link() {
    parseSilver = K.parseSilver;
  };
});
