/* EW shared pure logic. Loaded by the dashboard and overlay as a plain script
   (window.EWCore) and by node --test via require(). No DOM, no Electron. */
(function (root) {
  'use strict';

  const SERVER = 'http://127.0.0.1:8940';

  // NA daily reset 00:00 UTC; weekly reset Thursday 00:00 UTC.
  function nextDailyReset(now) {
    const d = new Date(now);
    return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate() + 1);
  }

  function nextWeeklyReset(now, weekday) {
    const wd = weekday === undefined ? 4 : weekday; // 4 = Thursday
    const d = new Date(now);
    let add = (wd - d.getUTCDay() + 7) % 7;
    if (add === 0) add = 7;
    return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate() + add);
  }

  function fmtDuration(ms) {
    let s = Math.max(0, Math.floor(ms / 1000));
    const h = Math.floor(s / 3600);
    s -= h * 3600;
    const m = Math.floor(s / 60);
    s -= m * 60;
    if (h >= 24) return Math.floor(h / 24) + 'd ' + (h % 24) + 'h';
    if (h > 0) return h + 'h ' + String(m).padStart(2, '0') + 'm';
    return m + 'm ' + String(s).padStart(2, '0') + 's';
  }

  // Freshness pill for a data source: ok under ttl, warn under 3x ttl, bad beyond.
  function freshness(updatedMs, nowMs, ttlMs) {
    if (updatedMs === null || updatedMs === undefined) return { cls: 'unknown', label: 'no data' };
    const age = nowMs - updatedMs;
    const label = fmtDuration(age) + ' ago';
    if (age <= ttlMs) return { cls: 'ok', label: label };
    if (age <= 3 * ttlMs) return { cls: 'warn', label: label };
    return { cls: 'bad', label: label };
  }

  // Validate the tab list served by /api/state; fall back to a System tab.
  function normalizeTabs(state) {
    const tabs = state && Array.isArray(state.tabs) ? state.tabs : [];
    const ok = tabs.filter(function (t) {
      return t && typeof t.id === 'string' && /^[a-z0-9-]+$/.test(t.id) && typeof t.title === 'string';
    });
    return ok.length ? ok : [{ id: 'system', title: 'System', plan: '001' }];
  }

  // Exponential backoff for SSE / fetch reconnects, capped.
  function backoffMs(attempt, baseMs, capMs) {
    const b = baseMs || 1000;
    const c = capMs || 30000;
    return Math.min(c, b * Math.pow(2, Math.max(0, attempt)));
  }

  // ---- Server health + version skew (plan 020) ----

  const HEALTH_DEAD_MS = 90000;
  const VERSION_POLL_MS = 30000;
  const RESTART_HINT = 'tray > Restart server';

  function knownCommit(c) {
    return typeof c === 'string' && /^[0-9a-f]{4,40}$/i.test(c.trim());
  }

  // The app reads `git rev-parse --short HEAD`, the server the full sha: equal
  // when one is a prefix of the other. Unknown on either side never differs.
  function commitsDiffer(a, b) {
    if (!knownCommit(a) || !knownCommit(b)) return false;
    const x = a.trim().toLowerCase();
    const y = b.trim().toLowerCase();
    return x.indexOf(y) !== 0 && y.indexOf(x) !== 0;
  }

  // Header pill: bad when no OK answer for 90 s (or never) or the SSE stream
  // errored; warn when both commits are known and differ; else ok.
  function healthPill(o) {
    const a = o || {};
    const fresh = isNum(a.lastOkMs) && isNum(a.nowMs) && a.nowMs - a.lastOkMs <= HEALTH_DEAD_MS;
    if (!fresh) return { level: 'bad', text: 'server offline' };
    if (a.sseOk === false) return { level: 'bad', text: 'server stream lost' };
    const sc = a.version && a.version.commit;
    if (commitsDiffer(sc, a.appCommit)) return { level: 'warn', text: 'server outdated - restart' };
    return { level: 'ok', text: 'server ok' };
  }

  // One honest 404 line for every module: a 404 on an API route means the
  // running server predates the app. Accepts 'grind' or '/api/spots?goal=xp'.
  function notOnServer(module) {
    let m = typeof module === 'string' ? module : '';
    m = m.split('?')[0].replace(/^\/api\//, '').replace(/^\//, '') || 'this';
    return m + ' API missing: server is older than the app - restart it (' + RESTART_HINT + ')';
  }

  function parseWhen(v) {
    if (isNum(v)) return v < 1e12 ? v * 1000 : v; // epoch seconds or ms
    if (typeof v !== 'string' || !v) return null;
    const t = Date.parse(v);
    return isNaN(t) ? null : t;
  }

  // /api/state.sources -> one pill per source, sorted by name. A source with
  // ttl_s is stale once its age passes ttl_s; error-like statuses are bad.
  function sourceFreshness(sources, nowMs) {
    if (!sources || typeof sources !== 'object' || Array.isArray(sources)) return [];
    return Object.keys(sources).sort().map(function (name) {
      const s = sources[name] && typeof sources[name] === 'object' ? sources[name] : {};
      let status = typeof s.status === 'string' ? s.status : null;
      if (status === null && isNum(s.done) && isNum(s.total)) status = s.done + '/' + s.total;
      const at = parseWhen(s.updated);
      const ageS = at === null ? null : Math.max(0, (nowMs - at) / 1000);
      const stale = ageS !== null && isNum(s.ttl_s) && ageS > s.ttl_s;
      let cls = 'ok';
      if (/^(error|bad|failed|blocked)$/.test(status || '')) cls = 'bad';
      else if (stale || status === 'stale') cls = 'warn';
      else if (at === null) cls = 'unknown';
      return { name: name, age: ageS === null ? 'never' : fmtAge(ageS) + ' ago',
        status: status || '-', stale: stale, cls: cls };
    });
  }

  // Server card rows from /api/version + /api/health (either may be null).
  function serverRows(version, health, appCommit, nowMs) {
    const v = version && typeof version === 'object' ? version : {};
    const started = parseWhen(v.started);
    const short = function (c) { return knownCommit(c) ? c.trim().slice(0, 7) : 'unknown'; };
    return [
      ['status', health && health.ok === true ? 'ok' : 'no answer'],
      ['server commit', short(v.commit)],
      ['app commit', short(appCommit)],
      ['outdated', commitsDiffer(v.commit, appCommit) ? 'yes - restart (' + RESTART_HINT + ')' : 'no'],
      ['started', typeof v.started === 'string' ? v.started : '-'],
      ['uptime', started === null ? '-' : fmtDuration(nowMs - started)],
      ['pid', isNum(v.pid) ? String(v.pid) : '-']
    ];
  }

  const DEFAULT_HOTKEYS = { toggleOverlay: 'Control+Alt+E', showDashboard: 'Control+Alt+D' };

  // Accept only Electron accelerator strings made of known tokens.
  function validAccelerator(acc) {
    if (typeof acc !== 'string' || !acc) return false;
    const mods = ['Control', 'Ctrl', 'Alt', 'Shift', 'CommandOrControl', 'Super'];
    const parts = acc.split('+');
    if (parts.length < 2) return false;
    const key = parts[parts.length - 1];
    return parts.slice(0, -1).every(function (p) { return mods.indexOf(p) >= 0; }) &&
      /^([A-Z0-9]|F([1-9]|1[0-9]|2[0-4]))$/.test(key);
  }

  function hotkeys(config) {
    const out = Object.assign({}, DEFAULT_HOTKEYS);
    const hk = (config && config.hotkeys) || {};
    Object.keys(DEFAULT_HOTKEYS).forEach(function (k) {
      if (validAccelerator(hk[k])) out[k] = hk[k];
    });
    return out;
  }

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

  // Same rule as the server (slice A): below wins when both hold.
  function alertFor(price, below, above) {
    if (!isNum(price)) return null;
    if (isNum(below) && price <= below) return 'below';
    if (isNum(above) && price >= above) return 'above';
    return null;
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
    const allowed = keys[0] === 'add' ? ['id', 'sid', 'below', 'above'] : ['id', 'sid'];
    if (!Object.keys(e).every(function (k) { return allowed.indexOf(k) >= 0; })) return false;
    if (!isInt(e.id, 1) || !isInt(e.sid, 0)) return false;
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
      const v = num(k, 0, null);
      if (Number.isNaN(v)) return { ok: false, error: k + ' must be a whole number >= 0' };
      if (v !== null) add[k] = v;
    }
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

  // Operator-typed silver: "84,500,000", "100m", "1.5b", "750k" -> int or null.
  function parseSilver(s) {
    if (s === null || s === undefined) return null;
    const t = String(s).trim().toLowerCase();
    let m = /^(\d{1,3}(?:,\d{3})+|\d+)$/.exec(t);
    if (m) {
      const v = Number(m[1].replace(/,/g, ''));
      return Number.isSafeInteger(v) ? v : null;
    }
    m = /^(\d+(?:\.\d+)?)([kmb])$/.exec(t);
    if (!m) return null;
    const v = Math.round(Number(m[1]) * { k: 1e3, m: 1e6, b: 1e9 }[m[2]]);
    return Number.isSafeInteger(v) ? v : null;
  }

  // Item-detail calculator: buy at X, sell at Y -> net and profit after tax.
  function pairProfit(buyText, sellText, opts) {
    const buy = parseSilver(buyText);
    const sell = parseSilver(sellText);
    if (buy === null) return { ok: false, error: 'buy: a silver amount like 80m or 80,000,000' };
    if (sell === null) return { ok: false, error: 'sell: a silver amount like 100m or 100,000,000' };
    const net = netProceeds(sell, opts);
    if (net === null) return { ok: false, error: 'tax settings invalid' };
    return { ok: true, buy: buy, sell: sell, net: net, profit: net - buy };
  }

  const PREORDER_NOTE = 'listing fills only from pre-orders at max; >= 20 B items fill at random';
  const PREORDER_WHY = { capped: 'last sold at the max price', no_stock: 'no stock listed' };

  // Server-sent `preorder` wins; otherwise the market.preorder_state rule.
  function preorderState(it) {
    if (!it || typeof it !== 'object') return null;
    if ('preorder' in it) return PREORDER_WHY[it.preorder] ? it.preorder : null;
    if (isInt(it.lastSoldPrice, 1) && it.lastSoldPrice === it.priceMax) return 'capped';
    if (it.currentStock === 0) return 'no_stock';
    return null;
  }

  function preorderBadge(state) {
    if (!Object.prototype.hasOwnProperty.call(PREORDER_WHY, state)) return null;
    return { label: 'pre-order', cls: 'preorder', title: PREORDER_WHY[state] + ' - ' + PREORDER_NOTE };
  }

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
      const alert = it.alert === 'below' || it.alert === 'above' ? it.alert : alertFor(it.price, it.below, it.above);
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

  // ---- Today (plan 003) ----
  // A tick is a timestamp; done = ticked_at >= last reset of its kind. Same rule
  // as the server (slice A), so the client re-derives across a reset by itself.

  const DAY_MS = 86400000;
  const KINDS = ['daily', 'weekly', 'event'];
  const SLUG = /^[a-z0-9-]{1,40}$/;
  const TITLE_MAX = 80;

  function lastDailyReset(now) { return nextDailyReset(now) - DAY_MS; }

  function lastWeeklyReset(now) { return nextWeeklyReset(now) - 7 * DAY_MS; }

  // ---- Per-item reset rules (plan 021) ----
  // {every: 'day'|'week', weekday: 0-6 (Mon=0, week only), at: 'HH:MM' UTC}; an
  // item without one keeps its kind's default (server today.DEFAULT_RULES).

  const AT = /^([01]\d|2[0-3]):[0-5]\d$/;
  const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  const RULE_KIND = { day: 'daily', week: 'weekly' };

  // Normalised copy of a rule (server today.validate_rule), or null.
  function validResetRule(r) {
    if (!plainObject(r) || !onlyKeys(r, ['every', 'weekday', 'at'])) return null;
    const at = r.at === undefined ? '00:00' : r.at;
    if (typeof at !== 'string' || !AT.test(at)) return null;
    if (r.every === 'day') return r.weekday === undefined ? { every: 'day', at: at } : null;
    if (r.every !== 'week' || !isInt(r.weekday, 0) || r.weekday > 6) return null;
    return { every: 'week', weekday: r.weekday, at: at };
  }

  // A valid rule that fits an item of `kind` (events take none), or null.
  function itemRule(kind, r) {
    const rule = validResetRule(r);
    return rule && RULE_KIND[rule.every] === kind ? rule : null;
  }

  function lastResetOf(rule, now) {
    const d = new Date(now);
    const at = (Number(rule.at.slice(0, 2)) * 60 + Number(rule.at.slice(3))) * 60000;
    let back = 0;
    let period = DAY_MS;
    if (rule.every === 'week') {
      back = (d.getUTCDay() - (rule.weekday + 1) % 7 + 7) % 7;
      period = 7 * DAY_MS;
    }
    const when = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate() - back) + at;
    return when > now ? when - period : when;
  }

  function nextResetOf(rule, now) {
    return lastResetOf(rule, now) + (rule.every === 'week' ? 7 * DAY_MS : DAY_MS);
  }

  // 'Sun 00:00 UTC' (week) or '05:00 UTC' (day).
  function fmtResetRule(r) {
    const rule = validResetRule(r);
    if (!rule) return '';
    return (rule.every === 'week' ? WEEKDAYS[rule.weekday] + ' ' : '') + rule.at + ' UTC';
  }

  // 'resets Sun 00:00 UTC in 2d 4h' for an item's own rule.
  function fmtResetCountdown(r, now) {
    const rule = validResetRule(r);
    if (!rule) return '';
    return 'resets ' + fmtResetRule(rule) + ' in ' + fmtDuration(nextResetOf(rule, now) - now);
  }

  // Plan 033: one gate -> 'needs Lv +2, +10 AP (+5 AP crosses a bracket: +8 bonus
  // AP)'; unknown stats say whether the gate is unsourced or the stat unset.
  const GATE_REQ = { level: 'min_level', ap: 'ap', dp: 'dp' };
  const GATE_NAME = { level: 'level', ap: 'AP', dp: 'DP' };
  function fmtWeeklyGate(row) {
    const g = plainObject(row) && plainObject(row.gate) ? row.gate : null;
    if (!g) return '';
    const needs = plainObject(g.needs) ? g.needs : {};
    const parts = [];
    if (Number.isInteger(needs.level)) parts.push('Lv +' + needs.level);
    if (Number.isInteger(needs.ap)) parts.push('+' + needs.ap + ' AP');
    if (Number.isInteger(needs.dp)) parts.push('+' + needs.dp + ' DP');
    let out = parts.length ? 'needs ' + parts.join(', ') : '';
    const b = g.bracket;
    if (parts.length && plainObject(b) && Number.isInteger(b.to_next) && b.to_next > 0) {
      out += ' (+' + b.to_next + ' AP crosses a bracket' +
        (Number.isInteger(b.next_gain) ? ': +' + b.next_gain + ' bonus AP' : '') + ')';
    }
    const unsourced = [];
    const unset = [];
    (Array.isArray(g.unknown) ? g.unknown : []).forEach(function (k) {
      if (typeof k !== 'string' || !Object.prototype.hasOwnProperty.call(GATE_REQ, k)) return;
      (row[GATE_REQ[k]] === null ? unsourced : unset).push(GATE_NAME[k]);
    });
    const more = [];
    if (unset.length) more.push('set ' + unset.join('/') + ' on Progress');
    if (unsourced.length) more.push(unsourced.join('/') + ' gate not sourced');
    if (more.length) out += (out ? '; ' : '') + more.join('; ');
    return out;
  }

  // Plan 033: GET /api/today weekly_plan -> {rows, eligible, total}; eligible rows
  // first, then unknown, then locked (data order kept inside each group). A
  // count whose next_reset has passed reads 0 until the next poll.
  const GATE_ORDER = { eligible: 0, unknown: 1, locked: 2 };
  function weeklyPlan(d, now) {
    const plan = plainObject(d) && plainObject(d.weekly_plan) ? d.weekly_plan : null;
    const raw = plan && Array.isArray(plan.rows) ? plan.rows : [];
    const rows = [];
    raw.forEach(function (r, n) {
      if (!plainObject(r) || typeof r.id !== 'string' || !validTitle(r.name)) return;
      if (!Number.isInteger(r.per_week) || r.per_week < 1) return;
      const state = plainObject(r.gate) && typeof r.gate.state === 'string' &&
        Object.prototype.hasOwnProperty.call(GATE_ORDER, r.gate.state) ? r.gate.state : 'unknown';
      const next = Date.parse(r.next_reset);
      let done = Number.isInteger(r.done) ? Math.max(0, Math.min(r.done, r.per_week)) : 0;
      if (Number.isFinite(next) && now >= next) done = 0;
      rows.push({ id: r.id, name: r.name, state: state, done: done, per_week: r.per_week,
        full: done >= r.per_week, ticks: done + '/' + r.per_week, gap: fmtWeeklyGate(r),
        reset: Number.isFinite(next) ? next : null,
        title: [typeof r.note === 'string' ? r.note : '',
          typeof r.source === 'string' ? 'source: ' + r.source : '',
          typeof r.verified === 'string' ? 'verified ' + r.verified : ''].filter(Boolean).join('\n'),
        n: n });
    });
    rows.sort(function (a, b) { return GATE_ORDER[a.state] - GATE_ORDER[b.state] || a.n - b.n; });
    return { rows: rows, eligible: rows.filter(function (r) { return r.state === 'eligible'; }).length,
      total: rows.length, error: plan && typeof plan.error === 'string' ? plan.error : null };
  }

  // Changes whenever any list's or item's reset passes (Today redraw trigger).
  function resetKey(items, now) {
    const keys = [lastResetOf({ every: 'day', at: '00:00' }, now),
      lastResetOf({ every: 'week', weekday: 3, at: '00:00' }, now)];
    (Array.isArray(items) ? items : []).forEach(function (it) {
      const rule = plainObject(it) ? itemRule(it.kind, it.reset) : null;
      if (rule) keys.push(lastResetOf(rule, now));
    });
    return keys.join(':');
  }

  // Preset rows from GET /api/today reset_presets -> [{name, label, kind, reset}];
  // unverified rows are labelled '(verify)'. Junk rows are dropped.
  function resetPresets(d) {
    const rows = plainObject(d) && Array.isArray(d.reset_presets) ? d.reset_presets : [];
    const out = [];
    rows.forEach(function (p) {
      if (!plainObject(p) || !validTitle(p.name)) return;
      const rule = itemRule(p.kind, p.reset);
      if (!rule) return;
      out.push({ name: p.name, label: p.name + ' - ' + fmtResetRule(rule) +
        (p.verified === true ? '' : ' (verify)'), kind: p.kind, reset: rule });
    });
    return out;
  }

  // A preset -> add-form field strings (title, kind, reset weekday, reset at).
  function presetForm(p) {
    return { title: p.name, kind: p.kind, until: '',
      reset_weekday: p.reset.every === 'week' ? String(p.reset.weekday) : '', reset_at: p.reset.at };
  }

  function isDone(tickIso, kind, now, reset) {
    if (typeof tickIso !== 'string' || !tickIso) return false;
    const t = Date.parse(tickIso);
    if (!isFinite(t)) return false;
    const rule = itemRule(kind, reset);
    if (rule) return t >= lastResetOf(rule, now);
    if (kind === 'daily' || kind === 'event') return t >= lastDailyReset(now);
    if (kind === 'weekly') return t >= lastWeeklyReset(now);
    return false;
  }

  // 'YYYY-MM-DD' -> epoch ms of its 00:00 UTC, or null (rejects 2026-02-30).
  function isoDateMs(s) {
    if (typeof s !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(s)) return null;
    const ms = Date.parse(s + 'T00:00:00Z');
    if (!isFinite(ms) || new Date(ms).toISOString().slice(0, 10) !== s) return null;
    return ms;
  }

  // Whole UTC days from today to the until date: 0 = last day, < 0 = expired.
  function eventDaysLeft(until, now) {
    const u = isoDateMs(until);
    return u === null ? null : Math.round((u - lastDailyReset(now)) / DAY_MS);
  }

  function fmtDaysLeft(d) {
    if (d === null || d === undefined) return 'no end date';
    if (d < 0) return 'ended';
    if (d === 0) return 'ends today';
    return d + (d === 1 ? ' day left' : ' days left');
  }

  // /api/today items -> {daily, weekly, event} each {items, done, total}, server
  // order kept. With `now`, done is re-derived from ticked_at; without, the
  // server's flag is trusted. Expired events are dropped.
  function groupItems(items, now) {
    const out = {};
    KINDS.forEach(function (k) { out[k] = { items: [], done: 0, total: 0 }; });
    (Array.isArray(items) ? items : []).forEach(function (it) {
      if (!plainObject(it) || KINDS.indexOf(it.kind) < 0 || typeof it.id !== 'string') return;
      const x = Object.assign({}, it);
      if (now !== undefined) x.done = isDone(it.ticked_at, it.kind, now, it.reset);
      else x.done = !!it.done;
      x.reset = itemRule(it.kind, it.reset);
      if (x.kind === 'event') {
        x.days_left = now === undefined ? null : eventDaysLeft(it.until, now);
        if (x.days_left !== null && x.days_left < 0) return;
      }
      const g = out[x.kind];
      g.items.push(x);
      g.total += 1;
      if (x.done) g.done += 1;
    });
    return out;
  }

  // Optimistic update: a copy of the GET body with one item's tick set (iso) or
  // cleared (null). The input is never mutated.
  function withTick(data, id, iso) {
    if (!plainObject(data) || !Array.isArray(data.items)) return data === undefined ? null : data;
    const copy = Object.assign({}, data);
    copy.items = data.items.map(function (it) {
      if (!it || it.id !== id) return it;
      return Object.assign({}, it, { ticked_at: iso, done: iso !== null });
    });
    return copy;
  }

  function validTitle(t) {
    return typeof t === 'string' && t.trim().length > 0 && t.length <= TITLE_MAX &&
      !/[\u0000-\u001f\u007f]/.test(t);
  }

  function onlyKeys(o, allowed) {
    return Object.keys(o).every(function (k) { return allowed.indexOf(k) >= 0; });
  }

  // Exact shape check for POST /api/today bodies (main-process IPC guard).
  function validTodayBody(body) {
    if (!plainObject(body)) return false;
    const keys = Object.keys(body);
    if (keys.length !== 1) return false;
    const k = keys[0];
    const v = body[k];
    if (k === 'tick' || k === 'untick' || k === 'remove' || k === 'weekly_tick' ||
        k === 'weekly_untick') return typeof v === 'string' && SLUG.test(v);
    if (k === 'move') {
      return plainObject(v) && onlyKeys(v, ['id', 'to']) && typeof v.id === 'string' &&
        SLUG.test(v.id) && isInt(v.to, 0);
    }
    if (k === 'add') {
      return plainObject(v) && onlyKeys(v, ['title', 'kind', 'until', 'reset']) && validTitle(v.title) &&
        KINDS.indexOf(v.kind) >= 0 &&
        (v.until === undefined || v.until === null || isoDateMs(v.until) !== null) &&
        (v.reset === undefined || v.reset === null || itemRule(v.kind, v.reset) !== null);
    }
    return false;
  }

  // Add form strings -> {add: {...}} body, or an error for the operator. An
  // event needs an end date; daily/weekly drop any until.
  function parseTodayForm(form) {
    const f = form || {};
    const title = typeof f.title === 'string' ? f.title.trim() : '';
    if (!title) return { ok: false, error: 'title required' };
    if (title.length > TITLE_MAX || !validTitle(title)) {
      return { ok: false, error: 'title: up to ' + TITLE_MAX + ' plain characters' };
    }
    if (KINDS.indexOf(f.kind) < 0) return { ok: false, error: 'kind must be daily, weekly or event' };
    const add = { title: title, kind: f.kind };
    if (f.kind === 'event') {
      const until = typeof f.until === 'string' ? f.until.trim() : '';
      if (isoDateMs(until) === null) return { ok: false, error: 'event needs an end date (YYYY-MM-DD)' };
      add.until = until;
    }
    // Optional custom reset (plan 021): blank weekday + blank time = kind default.
    const wd = typeof f.reset_weekday === 'string' ? f.reset_weekday.trim() : '';
    const at = typeof f.reset_at === 'string' ? f.reset_at.trim() : '';
    if (wd || at) {
      if (f.kind === 'event') return { ok: false, error: 'event items take no custom reset' };
      if (wd && f.kind !== 'weekly') return { ok: false, error: 'reset weekday is for weekly items' };
      if (at && !AT.test(at)) return { ok: false, error: 'reset time must be HH:MM (UTC)' };
      add.reset = f.kind === 'weekly' ?
        { every: 'week', weekday: wd ? Number(wd) : 3, at: at || '00:00' } :
        { every: 'day', at: at || '00:00' };
      if (!itemRule(f.kind, add.reset)) return { ok: false, error: 'reset weekday must be 0-6 (Mon=0)' };
    }
    return { ok: true, body: { add: add } };
  }

  // ---- Progress (plan 004) ----
  // A step is done when it carries a done_at stamp; counts are re-derived on
  // the client so an optimistic toggle updates the bar before the server answers.

  const TRACK_KINDS = ['quest', 'season', 'gear'];
  const ID = /^[a-z0-9-]{1,40}$/;   // server ID_RE
  const SEED_ID = /^[a-z][a-z0-9_]{0,39}$/;   // server SEED_ID_RE (plan 034)
  const STEPS_MAX = 60;              // server MAX_STEPS
  const LEVEL_MAX = 75;              // server levels.LEVEL_MAX (plan 018)
  const LEVEL = [1, LEVEL_MAX];
  const STAT = [0, 999];

  function gsTotal(ap, aap, dp) {
    if (!isNum(ap) || !isNum(aap) || !isNum(dp)) return null;
    return (ap + aap) / 2 + dp;
  }

  // Plan 023: GET /api/progress `brackets` -> [{key, text, cliff, verify}],
  // one line per set stat (DP gives a DR % line and an all-DR line). A null
  // value is a span the tracked table does not hold: said so, never guessed.
  function bracketLine(label, b, fmt, unit, pctUnit) {
    if (!b || typeof b !== 'object' || !isNum(b.x)) return null;
    if (b.value === null) {
      const span = isNum(b.bracket_min) && isNum(b.bracket_max) ? ' (' + b.bracket_min + '-' + b.bracket_max + ')' : '';
      return label + ' ' + b.x + ' -> ' + unit + ' not in table' + span;
    }
    if (!isNum(b.value)) return null;
    let s = label + ' ' + b.x + ' -> ' + fmt(b.value);
    if (!isNum(b.next_min)) return s + ' (top bracket)';
    s += '; +' + (b.next_min - b.x) + ' ' + label + ' to ' + b.next_min;
    return s + (isNum(b.next_gain) ? ' gives +' + b.next_gain + (pctUnit ? '%' : '') : ': next bracket not in table');
  }

  function bracketLines(br) {
    if (!br || typeof br !== 'object') return [];
    const tables = br.tables && typeof br.tables === 'object' ? br.tables : {};
    const verify = function (name) { return !!(tables[name] && tables[name].reverify === true); };
    const bonus = function (v) { return '+' + v + ' bonus'; };
    const out = [];
    const push = function (key, table, text, cliff) {
      if (text) out.push({ key: key, text: text, cliff: cliff === true, verify: verify(table) });
    };
    push('ap', 'ap', bracketLine('AP', br.ap, bonus, 'bonus'), br.ap && br.ap.cliff);
    push('aap', 'ap', bracketLine('AAP', br.aap, bonus, 'bonus'), br.aap && br.aap.cliff);
    if (br.dp && typeof br.dp === 'object') {
      push('dp', 'dp_dr', bracketLine('DP', br.dp, function (v) { return v + '% DR'; }, 'DR', true), br.dp.cliff);
      const all = br.dp.all_dr;
      push('dp_all', 'dp_all_dr', bracketLine('DP', all, function (v) { return 'all-DR +' + v; }, 'all-DR'), false);
    }
    return out;
  }

  // Plan 034: "Add track" seed choices from GET /api/progress `seeds`; an added
  // seed stays listed but disabled (seeding twice is a server no-op anyway).
  function seedOptions(seeds) {
    if (!Array.isArray(seeds)) return [];
    const out = [];
    seeds.forEach(function (s) {
      if (!plainObject(s) || typeof s.id !== 'string' || !SEED_ID.test(s.id)) return;
      const title = typeof s.title === 'string' && s.title ? s.title : s.id;
      const unv = isInt(s.unverified, 1) ? ' (' + s.unverified + ' to verify)' : '';
      out.push({ id: s.id, label: title + (s.added === true ? ' - added' : unv), added: s.added === true });
    });
    return out;
  }

  // Plan 034: gate chips for a track step: [{text, cls, title}]. `ready` is ok,
  // `needs` warn (with the plan 023 bonus-AP hint), unknown stats muted.
  function gateChips(step) {
    const gates = plainObject(step) && Array.isArray(step.gates) ? step.gates : [];
    const unit = { level: 'Lv', ap: 'AP', dp: 'DP' };
    const out = [];
    gates.forEach(function (g) {
      if (!plainObject(g) || !unit[g.stat] || !isNum(g.need)) return;
      const need = unit[g.stat] + ' ' + g.need;
      if (g.state === 'ready') {
        out.push({ text: need + ' ready', cls: 'ok', title: 'gate met' });
      } else if (g.state === 'needs' && isNum(g.gap)) {
        const sfx = g.stat === 'level' ? ' lv' : ' ' + unit[g.stat];
        const bonus = g.stat === 'ap' && isNum(g.bonus_gain) && g.bonus_gain > 0 ? ' (+' + g.bonus_gain + ' bonus AP)' : '';
        out.push({ text: need + ': needs +' + g.gap + sfx + bonus, cls: 'warn', title: 'have ' + g.have });
      } else {
        out.push({ text: need, cls: 'unknown', title: 'set ' + (g.stat === 'level' ? 'level' : unit[g.stat]) + ' on the Character card' });
      }
    });
    return out;
  }

  // Whole percent, floored like the server's pct(), so 100 only when every step is done.
  function trackPct(track) {
    let done = 0;
    let total = 0;
    const steps = track && Array.isArray(track.steps) ? track.steps : [];
    steps.forEach(function (s) {
      if (!plainObject(s)) return;
      total += 1;
      if (s.done_at) done += 1;
    });
    const pct = total ? Math.floor(100 * done / total) : 0;
    return { done: done, total: total, pct: pct };
  }

  // Optimistic update: a copy of the GET body with one step's done_at set (iso)
  // or cleared (null) and that track's counts re-derived. Input never mutated.
  function withStep(data, trackId, stepId, iso) {
    if (!plainObject(data) || !Array.isArray(data.tracks)) return data === undefined ? null : data;
    const copy = Object.assign({}, data);
    copy.tracks = data.tracks.map(function (t) {
      if (!t || t.id !== trackId || !Array.isArray(t.steps)) return t;
      const nt = Object.assign({}, t);
      nt.steps = t.steps.map(function (s) {
        if (!s || s.id !== stepId) return s;
        return Object.assign({}, s, { done_at: iso });
      });
      // Season objectives (plan 013) mirror the step; an untick drops the claim too.
      if (Array.isArray(t.objectives)) {
        nt.objectives = t.objectives.map(function (o) {
          if (!o || o.id !== stepId) return o;
          const patch = { done_at: iso, done: iso !== null };
          if (iso === null) { patch.claimed_at = null; patch.claimed = false; }
          return Object.assign({}, o, patch);
        });
      }
      return Object.assign(nt, trackPct(nt));
    });
    return copy;
  }

  // ---- Season pass by objective (plan 013) ----
  const OBJ_KINDS = ['level', 'gear', 'quest', 'other'];
  const GEAR_TARGET = [1, 20];               // server GEAR_RANGE
  const REWARD_MAX = 80;                     // server MAX_REWARD
  const GEAR_GRADES = ['PRI', 'DUO', 'TRI', 'TET', 'PEN'];
  const NEXT_OPEN = 3;

  function fmtTarget(kind, target) {
    if (kind === 'level' && inRange(target, LEVEL)) return 'Lv ' + target;
    if (kind === 'gear' && inRange(target, GEAR_TARGET)) return target > 15 ? GEAR_GRADES[target - 16] : '+' + target;
    return '';
  }

  // Next open (up to 3, list order), done-but-unclaimed, and counts.
  function seasonGroups(track) {
    const objs = track && Array.isArray(track.objectives) ? track.objectives.filter(plainObject) : [];
    const done = objs.filter(function (o) { return !!o.done_at; });
    return {
      next: objs.filter(function (o) { return !o.done_at; }).slice(0, NEXT_OPEN),
      unclaimed: done.filter(function (o) { return !o.claimed_at; }),
      done: done.length,
      total: objs.length,
      claimed: done.length - done.filter(function (o) { return !o.claimed_at; }).length
    };
  }

  // Overlay one-liner from GET /api/progress `season`: "Pass 23/40 - next: Lv 50 (2 lv)".
  function seasonLine(s) {
    if (!plainObject(s) || typeof s.track !== 'string' || !isNum(s.done) || !isNum(s.total)) {
      return 'no season pass track';
    }
    let line = 'Pass ' + s.done + '/' + s.total;
    const n = Array.isArray(s.next) && plainObject(s.next[0]) ? s.next[0] : null;
    if (!n) line += ' - all done';
    else if (n.kind === 'level' && inRange(n.target, LEVEL)) {
      line += ' - next: Lv ' + n.target + (isNum(n.gap) && n.gap > 0 ? ' (' + n.gap + ' lv)' : '');
    } else line += ' - next: ' + String(n.title || n.id || '?');
    const un = Array.isArray(s.unclaimed) ? s.unclaimed.length : 0;
    if (un > 0) line += ' | claim ' + un;
    return line;
  }

  function validReward(r) {
    return typeof r === 'string' && r.length <= REWARD_MAX && !/[\u0000-\u001f\u007f]/.test(r);
  }

  function validObjFields(v) {
    if (v.title !== undefined && !validTitle(v.title)) return false;
    if (v.kind !== undefined && OBJ_KINDS.indexOf(v.kind) < 0) return false;
    if (v.target !== undefined && v.target !== null && !isInt(v.target, 1)) return false;
    return v.reward === undefined || validReward(v.reward);
  }

  function validObjRef(v, keys) {
    return plainObject(v) && onlyKeys(v, keys) && typeof v.track === 'string' && ID.test(v.track) &&
      typeof v.objective === 'string' && ID.test(v.objective);
  }

  // Objective add form -> {obj_add: {...}} body, or an operator error. Gear
  // target takes a number or a grade (PRI..PEN). With `objective` (an id) the
  // same form edits in place: {obj_edit: {track, objective, title, kind,
  // target, reward}} - done/claim marks and list position are kept.
  function parseObjectiveForm(track, form, objective) {
    const r = parseObjectiveAdd(track, form);
    if (!r.ok || objective === undefined) return r;
    return { ok: true, body: { obj_edit: Object.assign({ objective: objective }, r.body.obj_add) } };
  }

  // Inverse of fmtTarget for the form's target box.
  function targetInput(kind, target) {
    if (kind === 'gear') return fmtTarget(kind, target).replace(/^\+/, '');
    return kind === 'level' && inRange(target, LEVEL) ? String(target) : '';
  }

  function parseObjectiveAdd(track, form) {
    const f = form || {};
    const title = typeof f.title === 'string' ? f.title.trim() : '';
    if (!title) return { ok: false, error: 'title required' };
    if (!validTitle(title)) return { ok: false, error: 'title: up to ' + TITLE_MAX + ' plain characters' };
    if (OBJ_KINDS.indexOf(f.kind) < 0) return { ok: false, error: 'kind must be level, gear, quest or other' };
    const raw = f.target === undefined || f.target === null ? '' : String(f.target).trim().toUpperCase();
    let target = null;
    if (f.kind === 'level') {
      target = wholeIn(raw, LEVEL);
      if (target === null) return { ok: false, error: 'level target must be ' + LEVEL[0] + '-' + LEVEL[1] };
    } else if (f.kind === 'gear' && raw) {
      const g = GEAR_GRADES.indexOf(raw);
      target = g >= 0 ? 16 + g : wholeIn(raw.replace(/^\+/, ''), GEAR_TARGET);
      if (target === null) return { ok: false, error: 'gear target: +1..+20 or PRI..PEN' };
    } else if (raw) return { ok: false, error: f.kind + ' takes no target' };
    const reward = typeof f.reward === 'string' ? f.reward.trim() : '';
    if (!validReward(reward)) return { ok: false, error: 'reward: up to ' + REWARD_MAX + ' plain characters' };
    return { ok: true, body: { obj_add: { track: track, title: title, kind: f.kind, target: target, reward: reward } } };
  }

  function inRange(v, r) { return isInt(v, r[0]) && v <= r[1]; }

  function validCharacter(c) {
    if (!plainObject(c) || !Object.keys(c).length || !onlyKeys(c, ['name', 'cls', 'level', 'gs'])) return false;
    if (c.name !== undefined && !validTitle(c.name)) return false;
    if (c.cls !== undefined && !validTitle(c.cls)) return false;
    if (c.level !== undefined && !inRange(c.level, LEVEL)) return false;
    if (c.gs === undefined) return true;
    return plainObject(c.gs) && Object.keys(c.gs).length > 0 && onlyKeys(c.gs, ['ap', 'aap', 'dp']) &&
      Object.keys(c.gs).every(function (k) { return inRange(c.gs[k], STAT); });
  }

  // Exact shape check for POST /api/progress bodies (main-process IPC guard).
  function validProgressBody(body) {
    if (!plainObject(body)) return false;
    const keys = Object.keys(body);
    if (keys.length !== 1) return false;
    const k = keys[0];
    const v = body[k];
    if (k === 'character') return validCharacter(v);
    if (k === 'remove_track') return typeof v === 'string' && ID.test(v);
    if (k === 'track_seed') return typeof v === 'string' && SEED_ID.test(v);
    if (k === 'step') {
      return plainObject(v) && onlyKeys(v, ['track', 'step', 'done']) && typeof v.track === 'string' &&
        ID.test(v.track) && typeof v.step === 'string' && ID.test(v.step) && typeof v.done === 'boolean';
    }
    if (k === 'add_track') {
      return plainObject(v) && onlyKeys(v, ['title', 'kind', 'steps']) && validTitle(v.title) &&
        TRACK_KINDS.indexOf(v.kind) >= 0 && Array.isArray(v.steps) && v.steps.length <= STEPS_MAX &&
        v.steps.every(validTitle);
    }
    if (k === 'claim') {
      return validObjRef(v, ['track', 'objective', 'claimed']) && typeof v.claimed === 'boolean';
    }
    if (k === 'obj_add') {
      return plainObject(v) && onlyKeys(v, ['track', 'title', 'kind', 'target', 'reward']) &&
        typeof v.track === 'string' && ID.test(v.track) && v.title !== undefined && v.kind !== undefined &&
        validObjFields(v);
    }
    if (k === 'obj_edit') {
      const edits = ['title', 'kind', 'target', 'reward'];
      return validObjRef(v, ['track', 'objective'].concat(edits)) &&
        edits.some(function (e) { return v[e] !== undefined; }) && validObjFields(v);
    }
    if (k === 'obj_del') return validObjRef(v, ['track', 'objective']);
    return false;
  }

  function wholeIn(s, r) {
    const t = s === undefined || s === null ? '' : String(s).trim();
    if (!/^\d+$/.test(t)) return null;
    const v = Number(t);
    return inRange(v, r) ? v : null;
  }

  // Character card inputs -> {character: {level, gs}} body, or an operator error.
  function parseCharacterForm(form) {
    const f = form || {};
    const level = wholeIn(f.level, LEVEL);
    if (level === null) return { ok: false, error: 'level must be a whole number ' + LEVEL[0] + '-' + LEVEL[1] };
    const gs = {};
    for (const k of ['ap', 'aap', 'dp']) {
      const v = wholeIn(f[k], STAT);
      if (v === null) return { ok: false, error: k.toUpperCase() + ' must be a whole number ' + STAT[0] + '-' + STAT[1] };
      gs[k] = v;
    }
    return { ok: true, body: { character: { level: level, gs: gs } } };
  }

  // Add-track form: steps one per line, blank lines dropped.
  function parseTrackForm(form) {
    const f = form || {};
    const title = typeof f.title === 'string' ? f.title.trim() : '';
    if (!title) return { ok: false, error: 'title required' };
    if (!validTitle(title)) return { ok: false, error: 'title: up to ' + TITLE_MAX + ' plain characters' };
    if (TRACK_KINDS.indexOf(f.kind) < 0) return { ok: false, error: 'kind must be quest, season or gear' };
    const steps = String(f.steps || '').split(/\r?\n/).map(function (s) { return s.trim(); })
      .filter(function (s) { return s; });
    if (!steps.length) return { ok: false, error: 'at least one step (one per line)' };
    if (steps.length > STEPS_MAX) return { ok: false, error: 'at most ' + STEPS_MAX + ' steps' };
    if (!steps.every(validTitle)) return { ok: false, error: 'each step: up to ' + TITLE_MAX + ' plain characters' };
    return { ok: true, body: { add_track: { title: title, kind: f.kind, steps: steps } } };
  }

  // /api/progress `profile`: null or status "none" = no family configured;
  // "pending" = upstream is fetching and nothing is cached yet (not an error).
  function profilePill(profile) {
    if (!plainObject(profile) || profile.status === 'none') {
      return { cls: 'unknown', label: 'no profile', stale: true, error: null, none: true, pending: false };
    }
    if (profile.status === 'pending' && !plainObject(profile.data)) {
      return { cls: 'unknown', label: 'profile fetching', stale: true, error: null, none: false, pending: true };
    }
    const p = sourcePill(profile.freshness, 'profile', 3600);
    p.none = false;
    p.pending = false;
    return p;
  }

  function plainText(v) {
    return (typeof v === 'string' && v.trim() && v.length <= TITLE_MAX) || isNum(v) ? String(v) : null;
  }

  // Served profile {family, region, guild: str|null, characters: [{name, cls,
  // level, main}]} -> [label, value] rows from known keys only.
  function profileRows(data) {
    if (!plainObject(data)) return [];
    const rows = [];
    const push = function (label, v) { const t = plainText(v); if (t !== null) rows.push([label, t]); };
    push('Family', data.family);
    push('Region', data.region);
    push('Guild', data.guild);
    const chars = Array.isArray(data.characters) ? data.characters.filter(plainObject) : [];
    const main = chars.filter(function (c) { return c.main; })[0] || chars[0];
    if (main && plainText(main.name)) {
      const bits = [plainText(main.cls), plainText(main.level)].filter(Boolean).join(' ');
      push('Main', main.name + (bits ? ' - ' + bits : ''));
    }
    if (chars.length) push('Characters', chars.length);
    return rows;
  }

  // ---- Grind (plan 005) ----
  // Elapsed clocks and buff countdowns run locally between polls: from the
  // absolute stamps when parseable, else from the server's seconds minus the
  // time since that fetch.

  const NAME_MAX = 60;
  const MINUTES = [1, 1440];        // sessions (log)
  const BUFF_MINUTES = [1, 43200];  // buffs: 30 days (server MAX_BUFF_MINUTES)
  const SILVER = [0, 1e13];
  const TRASH = [0, 1e6];
  const XP_PCT = [0, 1000];         // buff xp_pct and hot window pct (plan 011)

  // The one default buff list: names are exactly the server's SEED_BUFFS
  // (server/ew/grind.py, test-pinned); minutes are the arm defaults, within
  // BUFF_MINUTES.
  const BUFF_DEFAULTS = [
    { name: 'XP scroll', minutes: 30 },
    { name: 'Drop rate scroll', minutes: 60 },
    { name: 'Hot Time', minutes: 60 },
    { name: 'Value Pack', minutes: 43200 },
    { name: 'Old Moon book', minutes: 60 },
    { name: 'Kamasylve blessing', minutes: 43200 }
  ];

  function silverPerHour(silver, minutes) {
    if (!isNum(silver) || silver < 0 || !isNum(minutes) || minutes <= 0) return null;
    return Math.floor(silver * 60 / minutes);
  }

  function fmtElapsed(s) {
    if (!isNum(s)) return '-';
    let v = Math.max(0, Math.floor(s));
    const h = Math.floor(v / 3600);
    v -= h * 3600;
    const m = Math.floor(v / 60);
    return h + ':' + String(m).padStart(2, '0') + ':' + String(v - m * 60).padStart(2, '0');
  }

  function sinceFetch(fetchedMs, now) { return Math.max(0, (now - fetchedMs) / 1000); }

  // Whole seconds the active session has run, or null when none is active.
  function liveElapsed(active, fetchedMs, now) {
    if (!plainObject(active)) return null;
    const t = typeof active.started === 'string' ? Date.parse(active.started) : NaN;
    if (isFinite(t)) return Math.max(0, Math.floor((now - t) / 1000));
    if (!isNum(active.elapsed_s)) return null;
    return Math.max(0, Math.floor(active.elapsed_s + sinceFetch(fetchedMs, now)));
  }

  function buffLeft(b, fetchedMs, now) {
    const t = typeof b.ends === 'string' ? Date.parse(b.ends) : NaN;
    if (isFinite(t)) return Math.floor((t - now) / 1000);
    if (isNum(b.left_s)) return Math.floor(b.left_s - sinceFetch(fetchedMs, now));
    return null;
  }

  // Armed buffs with a live left_s, expired dropped, soonest first. Copies.
  function buffsLive(buffs, fetchedMs, now) {
    return (Array.isArray(buffs) ? buffs : []).filter(plainObject).map(function (b) {
      return Object.assign({}, b, { left_s: buffLeft(b, fetchedMs, now) });
    }).filter(function (b) { return b.left_s !== null && b.left_s > 0; })
      .sort(function (a, b) { return a.left_s - b.left_s; });
  }

  function soonestBuff(buffs, fetchedMs, now) {
    return buffsLive(buffs, fetchedMs, now)[0] || null;
  }

  function defaultMinutes(name) {
    const n = String(name).toLowerCase();
    const d = BUFF_DEFAULTS.filter(function (x) { return x.name.toLowerCase() === n; })[0];
    return d ? d.minutes : 60;
  }

  // Buffs card rows: every server buff (armed soonest first, then unarmed or
  // expired in server order, ids kept), then only the defaults whose names
  // (case-insensitive) the server did not list. One row per name.
  function buffRows(buffs, fetchedMs, now) {
    const seen = [];
    const rows = [];
    (Array.isArray(buffs) ? buffs : []).filter(function (b) {
      return plainObject(b) && typeof b.name === 'string';
    }).forEach(function (b) {
      const n = b.name.toLowerCase();
      if (seen.indexOf(n) >= 0) return;
      seen.push(n);
      const left = buffLeft(b, fetchedMs, now);
      rows.push({ id: b.id === undefined ? null : b.id, name: b.name,
        left_s: left !== null && left > 0 ? left : null, minutes: defaultMinutes(b.name),
        xp_pct: inRange(b.xp_pct, XP_PCT) ? b.xp_pct : null,
        xp_hint: typeof b.xp_hint === 'string' ? b.xp_hint : null });
    });
    const armed = rows.filter(function (r) { return r.left_s !== null; })
      .sort(function (a, b) { return a.left_s - b.left_s; });
    const idle = rows.filter(function (r) { return r.left_s === null; });
    const extra = BUFF_DEFAULTS.filter(function (d) { return seen.indexOf(d.name.toLowerCase()) < 0; })
      .map(function (d) { return { id: null, name: d.name, left_s: null, minutes: d.minutes, xp_pct: null, xp_hint: null }; });
    return armed.concat(idle, extra);
  }

  // Plan 018 XP buff presets from GET /api/grind: [{name, xp_pct, title}],
  // junk rows dropped. The values are community / patch-note figures, verify.
  function xpPresets(d) {
    const rows = plainObject(d) && Array.isArray(d.xp_presets) ? d.xp_presets : [];
    return rows.filter(function (p) {
      return plainObject(p) && validName(p.name) && inRange(p.xp_pct, XP_PCT);
    }).map(function (p) {
      const bits = [typeof p.notes === 'string' ? p.notes : '', typeof p.source === 'string' ? p.source : '',
        typeof p.verified === 'string' ? 'as of ' + p.verified : ''].filter(function (x) { return x; });
      return { name: p.name, xp_pct: p.xp_pct, title: bits.join(' - ') };
    });
  }

  // Best silver/h first; spots without an average last.
  function sortSpots(spots) {
    const v = function (s) { return isNum(s.silver_per_h) ? s.silver_per_h : -1; };
    return (Array.isArray(spots) ? spots : []).filter(plainObject).slice()
      .sort(function (a, b) { return v(b) - v(a); });
  }

  function spotName(spots, ref) {
    if (ref === null || ref === undefined) return '?';
    const s = (Array.isArray(spots) ? spots : []).filter(function (x) { return plainObject(x) && x.id === ref; })[0];
    return s && typeof s.name === 'string' ? s.name : String(ref);
  }

  function validName(t) {
    return typeof t === 'string' && t.trim().length > 0 && t.length <= NAME_MAX &&
      !/[\u0000-\u001f\u007f]/.test(t);
  }

  // Ids exactly as the server mints them (grind.py SID_RE / ID_RE).
  const SESSION_ID_RE = /^s[0-9]{1,9}$/;
  const BUFF_ID_RE = /^[a-z0-9-]{1,40}$/;
  function validRef(v, re) { return typeof v === 'string' && re.test(v); }

  function exact(o, keys) {
    return plainObject(o) && Object.keys(o).length === keys.length && onlyKeys(o, keys);
  }

  function validLoot(v) { return inRange(v.silver, SILVER) && inRange(v.trash, TRASH); }

  // Plan 039: per-session loot list [{name|id, count}] and operator loot items
  // (bounds mirror server/ew/grind.py MAX_LOOT / MAX_LOOT_COUNT / MAX_VENDOR).
  const LOOT_MAX = 50;
  const LOOT_COUNT = [1, 1e7];
  const VENDOR_PRICE = [0, 1e10];
  const ITEM_ID = [1, 2147483647];

  function validLootList(l) {
    return Array.isArray(l) && l.length <= LOOT_MAX && l.every(function (e) {
      if (exact(e, ['name', 'count'])) return validName(e.name) && inRange(e.count, LOOT_COUNT);
      return exact(e, ['id', 'count']) && inRange(e.id, ITEM_ID) && inRange(e.count, LOOT_COUNT);
    });
  }

  // stop / log with an optional `loot` key.
  function withLoot(v, keys) {
    if (plainObject(v) && Object.prototype.hasOwnProperty.call(v, 'loot')) {
      return exact(v, keys.concat(['loot'])) && validLootList(v.loot);
    }
    return exact(v, keys);
  }

  function validLootItem(v) {
    if (!plainObject(v) || !onlyKeys(v, ['spot', 'name', 'marketable', 'id', 'vendor_price'])) return false;
    if (!validName(v.spot) || !validName(v.name) || typeof v.marketable !== 'boolean') return false;
    if (v.id !== undefined && !inRange(v.id, ITEM_ID)) return false;
    if (v.vendor_price !== undefined && !inRange(v.vendor_price, VENDOR_PRICE)) return false;
    return v.marketable || v.vendor_price !== undefined;
  }

  // Sell-vs-vendor hint {choice, diff} -> short text; diff is silver per unit.
  function lootHintText(h) {
    if (!plainObject(h)) return '';
    if (h.choice === 'unknown') return 'no price';
    if (h.choice === 'either') return 'either';
    if (h.choice !== 'vendor' && h.choice !== 'market') return '';
    return isNum(h.diff) && h.diff > 0 ? h.choice + ' +' + fmtSilver(h.diff) + '/u' : h.choice;
  }

  // Silver a session counts toward silver/h: loot value when valued, else typed.
  function sessionSilver(s) {
    return plainObject(s) && isNum(s.valued_silver) ? s.valued_silver : (plainObject(s) ? s.silver : null);
  }

  // "trash pile worth X" + unpriced count for a loot-valued session, else ''.
  function trashPileText(s) {
    const v = plainObject(s) ? s.loot_value : null;
    if (!plainObject(v)) return '';
    const parts = [];
    if (isNum(v.trash) && v.trash > 0) parts.push('trash pile worth ' + fmtSilver(v.trash));
    const n = Array.isArray(v.unknown) ? v.unknown.length : 0;
    if (n) parts.push(n + (n === 1 ? ' item' : ' items') + ' unpriced');
    return parts.join(', ');
  }

  // Exact shape check for POST /api/grind bodies (main-process IPC guard).
  function validGrindBody(body) {
    if (!plainObject(body)) return false;
    const keys = Object.keys(body);
    if (keys.length !== 1) return false;
    const k = keys[0];
    const v = body[k];
    if (k === 'start' || k === 'add_spot') return validName(v);
    if (k === 'delete') return validRef(v, SESSION_ID_RE);
    if (k === 'clear_buff') return validRef(v, BUFF_ID_RE);
    if (k === 'stop') return withLoot(v, ['silver', 'trash']) && validLoot(v);
    if (k === 'log') {
      return withLoot(v, ['spot', 'minutes', 'silver', 'trash']) && validName(v.spot) &&
        inRange(v.minutes, MINUTES) && validLoot(v);
    }
    if (k === 'loot_item') return validLootItem(v);
    if (k === 'loot_forget') return exact(v, ['spot', 'name']) && validName(v.spot) && validName(v.name);
    if (k === 'buff') {
      // xp_pct is optional (plan 011): absent = not counted in the XP stack.
      return (exact(v, ['name', 'minutes']) || (exact(v, ['name', 'minutes', 'xp_pct']) && inRange(v.xp_pct, XP_PCT))) &&
        validName(v.name) && inRange(v.minutes, BUFF_MINUTES);
    }
    // Plan 038: passive drop source on/off, and an operator override (null = tracked value).
    if (k === 'drop_toggle') return exact(v, ['id', 'on']) && validRef(v.id, BUFF_ID_RE) && typeof v.on === 'boolean';
    if (k === 'drop_override') {
      return exact(v, ['id', 'field', 'value']) && validRef(v.id, DROP_ROW_RE) &&
        validRef(v.field, DROP_FIELD_RE) && validDropValue(v.value);
    }
    return false;
  }

  // ---- Drop-rate card (plan 038) ----
  // Every game number (caps, scroll price, dates) comes from GET /api/grind;
  // these helpers only shape it for display.

  const DROP_ROW_RE = /^([a-z0-9-]{1,40}|agris_scroll)$/;
  const DROP_FIELD_RE = /^(rate_pct|amount_pct|bypass|base_pct|bypass_pct|beyond_pct|price_silver|minutes|per_week|sale_until_utc|removed_utc)$/;
  const DROP_BYPASS = ['none', 'to400', 'to500'];

  function validDropValue(v) {
    if (v === null) return true;
    if (isNum(v)) return v >= 0 && v <= 1e13;
    return typeof v === 'string' && (DROP_BYPASS.indexOf(v) >= 0 || /^\d{4}-\d{2}-\d{2}$/.test(v));
  }

  function pctText(v) { return isNum(v) ? '+' + v + '%' : ''; }

  // GET /api/grind body -> {error, line, wasted, amount, active[], toggles[], roi}.
  // roi is null when the server has none or the scroll's removal date passed.
  function dropView(d) {
    const x = plainObject(d) && plainObject(d.drops) ? d.drops : null;
    if (!x) return { error: 'no drop data', line: '', wasted: false, amount: '', active: [], toggles: [], roi: null };
    if (typeof x.error === 'string' && x.error) {
      return { error: x.error, line: '', wasted: false, amount: '', active: [], toggles: [], roi: null };
    }
    const n = function (v) { return isNum(v) ? v : 0; };
    const wasted = n(x.wasted) > 0;
    const line = n(x.rate_capped) + '% / ' + n(x.cap_used) + '% cap' +
      (wasted ? ' (' + n(x.rate_total) + '% stacked, ' + n(x.wasted) + '% wasted)' : '');
    const active = (Array.isArray(x.active) ? x.active : []).filter(plainObject).map(function (a) {
      const bits = [pctText(a.rate_pct), isNum(a.amount_pct) ? pctText(a.amount_pct) + ' amount' : '']
        .filter(function (s) { return s; });
      return { id: a.id, name: String(a.name), text: bits.join(' '), via: a.via === 'timer' ? 'timer' : 'toggle' };
    });
    const toggles = (Array.isArray(x.buffs) ? x.buffs : []).filter(function (r) {
      return plainObject(r) && typeof r.id === 'string' && typeof r.name === 'string';
    }).map(function (r) {
      const bits = [r.bypass === 'none' ? '' : 'bypass ' + String(r.bypass).replace('to', 'to ') + '%',
        r.verified === false ? 'unverified' : (typeof r.verified === 'string' ? 'as of ' + r.verified : ''),
        typeof r.note === 'string' ? r.note : '', typeof r.source === 'string' ? r.source : '']
        .filter(function (s) { return s; });
      const val = [pctText(r.rate_pct), isNum(r.amount_pct) ? pctText(r.amount_pct) + ' amt' : '']
        .filter(function (s) { return s; }).join(' ');
      return { id: r.id, name: r.name, value: val, on: r.on === true, timer: r.timer === true,
        unverified: r.verified === false, overridden: r.overridden === true, title: bits.join(' - ') };
    });
    return { error: null, line: line, wasted: wasted, amount: n(x.amount_total) ? '+' + n(x.amount_total) + '% amount' : '',
      active: active, toggles: toggles, roi: agrisLine(d.agris_roi) };
  }

  // Blessing of Agris ROI line, or null (no data / removed).
  function agrisLine(r) {
    if (!plainObject(r) || r.hidden === true) return null;
    const be = isNum(r.break_even_silver_h) ? fmtSilver(r.break_even_silver_h) + '/h' : 'never (no uplift)';
    let verdict = 'log a session to compare';
    if (isNum(r.silver_h)) {
      verdict = (r.worth ? 'worth it' : 'not worth it') + ' at ' + fmtSilver(r.silver_h) + '/h' +
        (typeof r.spot_name === 'string' ? ' (' + r.spot_name + ')' : '') +
        ', net ' + fmtSilver(r.net_silver);
    }
    const dates = (r.on_sale ? 'sale until ' + r.sale_until_utc + ', ' : 'sale over, ') + 'removed ' + r.removed_utc;
    return {
      text: 'Agris ' + fmtSilver(r.price_silver) + ' / ' + r.minutes + 'm: +' + r.gain_pct + '% drops, break-even ' + be,
      verdict: verdict, worth: r.worth === true, dates: dates
    };
  }

  // Grind form strings -> POST body, or an error for the operator.
  // kind: stop {silver, trash} | log {spot, minutes, silver, trash} |
  // buff {name, minutes} | spot {name}. Blank silver / trash = 0.
  function parseGrindForm(kind, form) {
    const f = form || {};
    const blank = function (k) { return f[k] === undefined || f[k] === null || String(f[k]).trim() === ''; };
    const loot = function () {
      const silver = blank('silver') ? 0 : wholeIn(f.silver, SILVER);
      if (silver === null) return { error: 'silver must be a whole number 0-10000000000000' };
      const trash = blank('trash') ? 0 : wholeIn(f.trash, TRASH);
      if (trash === null) return { error: 'trash must be a whole number 0-1000000' };
      return { silver: silver, trash: trash };
    };
    const minutes = function (r) { return wholeIn(f.minutes, r); };
    const minErr = function (r) { return 'minutes must be a whole number ' + r[0] + '-' + r[1]; };
    const name = function (k) { return typeof f[k] === 'string' ? f[k].trim() : ''; };
    // Plan 039: f.loot = [{name, count string}]; blank / 0 counts are skipped.
    const items = function () {
      const out = [];
      const rows = Array.isArray(f.loot) ? f.loot : [];
      for (let i = 0; i < rows.length; i++) {
        const c = rows[i] && rows[i].count;
        const t = c === undefined || c === null ? '' : String(c).trim();
        if (t === '' || t === '0') continue;
        const n = wholeIn(t, LOOT_COUNT);
        if (n === null || !validName(rows[i].name)) {
          return { error: 'loot count must be a whole number 1-10000000 (' + rows[i].name + ')' };
        }
        out.push({ name: rows[i].name, count: n });
      }
      return out.length > LOOT_MAX ? { error: 'at most ' + LOOT_MAX + ' loot items' } : { list: out };
    };
    const withItems = function (body) {
      const it = items();
      if (it.error) return { error: it.error };
      if (it.list.length) body.loot = it.list;
      return body;
    };
    if (kind === 'stop') {
      const l = loot();
      const b = l.error ? l : withItems(l);
      return b.error ? { ok: false, error: b.error } : { ok: true, body: { stop: b } };
    }
    if (kind === 'log') {
      if (!validName(f.spot)) return { ok: false, error: 'pick a spot' };
      const m = minutes(MINUTES);
      if (m === null) return { ok: false, error: minErr(MINUTES) };
      const l = loot();
      if (l.error) return { ok: false, error: l.error };
      const b = withItems({ spot: f.spot, minutes: m, silver: l.silver, trash: l.trash });
      return b.error ? { ok: false, error: b.error } : { ok: true, body: { log: b } };
    }
    if (kind === 'loot_item') {
      if (!validName(f.spot)) return { ok: false, error: 'pick a spot' };
      const n = name('name');
      if (!validName(n)) return { ok: false, error: 'item name: 1-' + NAME_MAX + ' plain characters' };
      const item = { spot: f.spot, name: n, marketable: f.marketable === true };
      if (!blank('id')) {
        const id = wholeIn(f.id, ITEM_ID);
        if (id === null) return { ok: false, error: 'item id must be a whole number (blank = none)' };
        item.id = id;
      }
      if (!blank('vendor_price')) {
        const v = wholeIn(f.vendor_price, VENDOR_PRICE);
        if (v === null) return { ok: false, error: 'vendor price must be a whole number 0-10000000000' };
        item.vendor_price = v;
      }
      if (!item.marketable && item.vendor_price === undefined) {
        return { ok: false, error: 'trash (not marketable) needs a vendor price' };
      }
      return { ok: true, body: { loot_item: item } };
    }
    if (kind === 'buff') {
      if (!validName(name('name'))) return { ok: false, error: 'buff name: 1-' + NAME_MAX + ' plain characters' };
      const m = minutes(BUFF_MINUTES);
      if (m === null) return { ok: false, error: minErr(BUFF_MINUTES) };
      const buff = { name: name('name'), minutes: m };
      if (!blank('xp_pct')) {
        const xp = wholeIn(f.xp_pct, XP_PCT);
        if (xp === null) return { ok: false, error: 'XP % must be a whole number 0-1000 (blank = none)' };
        buff.xp_pct = xp;
      }
      return { ok: true, body: { buff: buff } };
    }
    if (kind === 'spot') {
      const n = name('name');
      if (!validName(n)) return { ok: false, error: 'spot name: 1-' + NAME_MAX + ' plain characters' };
      return { ok: true, body: { add_spot: n } };
    }
    return { ok: false, error: 'unknown action' };
  }

  // ---- Grind spot recommender (plan 012) ----
  // GET /api/spots; the server ranks. Blank what-if fields fall back to the
  // Progress character server-side.

  const SPOT_GOALS = ['xp', 'silver'];
  const SPOT_STAT = [0, 999];
  const SPOT_LEVEL = LEVEL;

  // goal + what-if strings -> { ok, path } or { ok: false, error }.
  function spotsPath(goal, form) {
    const f = form || {};
    if (SPOT_GOALS.indexOf(goal) < 0) return { ok: false, error: 'goal must be xp or silver' };
    let path = '/api/spots?goal=' + goal;
    const fields = [['ap', SPOT_STAT], ['dp', SPOT_STAT], ['level', SPOT_LEVEL]];
    for (let i = 0; i < fields.length; i++) {
      const k = fields[i][0];
      const r = fields[i][1];
      if (f[k] === undefined || f[k] === null || String(f[k]).trim() === '') continue;
      const v = wholeIn(f[k], r);
      if (v === null) return { ok: false, error: k + ' must be a whole number ' + r[0] + '-' + r[1] + ' (blank = Progress)' };
      path += '&' + k + '=' + v;
    }
    return { ok: true, path: path };
  }

  // "+50 AP +40 DP +2 lvl" for an unlock row; '' when nothing is missing.
  function spotNeedText(r) {
    if (!plainObject(r)) return '';
    const out = [];
    if (isInt(r.need_ap, 1)) out.push('+' + r.need_ap + ' AP');
    if (isInt(r.need_dp, 1)) out.push('+' + r.need_dp + ' DP');
    if (isInt(r.need_level, 1)) out.push('+' + r.need_level + ' lvl');
    return out.join(' ');
  }

  // Plan 018 level-gap note: "Lv +2 vs mob: +6 DR" (out-levelled), "Lv -3 vs
  // mob" (under), '' without a monster level.
  function spotGapText(r) {
    if (!plainObject(r) || !isInt(r.level_gap, -200)) return '';
    const sign = r.level_gap > 0 ? '+' : '';
    const dr = isInt(r.outlevel_dr, 1) ? ': +' + r.outlevel_dr + ' DR' : '';
    return 'Lv ' + sign + r.level_gap + ' vs mob' + dr;
  }

  // /api/spots body -> { top, unlocks, missing, error } with junk rows dropped.
  function spotRecs(d) {
    const rows = function (v) {
      return (Array.isArray(v) ? v : []).filter(function (r) {
        return plainObject(r) && typeof r.name === 'string' && r.name.length > 0;
      });
    };
    if (!plainObject(d)) return { top: [], unlocks: [], missing: [], error: null };
    return {
      top: rows(d.top),
      unlocks: rows(d.unlocks),
      missing: Array.isArray(d.missing) ? d.missing.filter(function (m) { return typeof m === 'string'; }) : [],
      error: typeof d.error === 'string' ? d.error : null
    };
  }

  // Grind spot id whose name matches `name` (case-insensitive), or null.
  function matchSpot(spots, name) {
    if (typeof name !== 'string') return null;
    const n = name.trim().toLowerCase();
    const s = (Array.isArray(spots) ? spots : []).filter(function (x) {
      return plainObject(x) && typeof x.name === 'string' && x.name.trim().toLowerCase() === n;
    })[0];
    return s ? (s.id === undefined || s.id === null ? s.name : String(s.id)) : null;
  }

  // ---- Events (plan 006) ----
  // Coupons, events and Twitch drops; all operator input. Status / soon follow
  // the server rule (events.py) so countdowns flip locally between polls.

  const EVENT_KINDS = ['coupon', 'event', 'drop'];
  const EVENTS_SOON_S = 172800;            // server: soon when left_s <= 48 h
  const EVENT_ID_RE = /^e[0-9]{1,9}$/;
  const COUPON_RE = /^[A-Za-z0-9-]{4,40}$/;
  const REWARDS_MAX = 200;
  const URL_MAX = 300;
  const ISO_TS = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.\d{1,6})?)?(Z|[+-]\d{2}:\d{2})$/;
  const ISO_DAY = /^(\d{4})-(\d{2})-(\d{2})$/;
  const LOCAL_TS = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/;

  function realDate(y, mo, d) {
    const t = new Date(Date.UTC(+y, +mo - 1, +d));
    return t.getUTCFullYear() === +y && t.getUTCMonth() === +mo - 1 && t.getUTCDate() === +d;
  }

  // Server-accepted date (ISO with offset or Z, or YYYY-MM-DD) -> ms, else null.
  // YYYY-MM-DD as `ends` means 23:59:59 UTC that day, as `starts` 00:00 UTC.
  function eventDateMs(s, asEnd) {
    if (typeof s !== 'string') return null;
    let m = ISO_DAY.exec(s);
    if (m) {
      if (!realDate(m[1], m[2], m[3])) return null;
      return Date.UTC(+m[1], +m[2] - 1, +m[3]) + (asEnd ? 86399000 : 0);
    }
    m = ISO_TS.exec(s);
    if (!m || !realDate(m[1], m[2], m[3]) || +m[4] > 23 || +m[5] > 59 || +(m[6] || 0) > 59) return null;
    const t = Date.parse(s);
    return isFinite(t) ? t : null;
  }

  // Seconds left -> countdown text; null when there is no deadline.
  function fmtLeft(s) {
    if (!isNum(s)) return '-';
    if (s <= 0) return 'ended';
    return fmtDuration(s * 1000);
  }

  function liveLeft(it, fetchedMs, now) {
    const end = eventDateMs(it.ends, true);
    if (end !== null) return Math.floor((end - now) / 1000);
    if (isNum(it.left_s)) return Math.floor(it.left_s - sinceFetch(fetchedMs, now));
    return null;
  }

  // GET /api/events items -> display rows with live left_s / status / soon, in
  // the server order: open by ends ascending (no ends last), then done, then
  // expired newest ends first. Junk dropped; input not mutated.
  function eventRows(items, fetchedMs, now) {
    const list = Array.isArray(items) ? items : [];
    const rows = [];
    list.forEach(function (it, i) {
      if (!plainObject(it) || typeof it.id !== 'string' || typeof it.title !== 'string') return;
      const r = Object.assign({}, it);
      r.left_s = liveLeft(it, fetchedMs, now);
      const start = eventDateMs(it.starts, false);
      if (it.done === true) r.status = 'done';
      else if (r.left_s !== null && r.left_s <= 0) r.status = 'expired';
      else if (start !== null && start > now) r.status = 'upcoming';
      else r.status = 'active';
      r.soon = r.status !== 'done' && r.status !== 'expired' && r.left_s !== null && r.left_s <= EVENTS_SOON_S;
      r._i = i;
      rows.push(r);
    });
    const rank = { active: 0, upcoming: 0, done: 1, expired: 2 };
    rows.sort(function (a, b) {
      const g = rank[a.status] - rank[b.status];
      if (g) return g;
      if (a.status === 'expired') return (b.left_s - a.left_s) || (a._i - b._i);
      if (rank[a.status] === 0) {
        if (a.left_s === null || b.left_s === null) {
          if (a.left_s !== b.left_s) return a.left_s === null ? 1 : -1;
        } else if (a.left_s !== b.left_s) return a.left_s - b.left_s;
      }
      return a._i - b._i;
    });
    rows.forEach(function (r) { delete r._i; });
    return rows;
  }

  function soonestEvent(items, fetchedMs, now) {
    const rows = eventRows(items, fetchedMs, now).filter(function (r) { return r.soon; });
    return rows.length ? rows[0] : null;
  }

  // <input type="datetime-local"> value (local wall time) -> UTC ISO "...Z".
  function localToUtcIso(v) {
    const m = typeof v === 'string' ? LOCAL_TS.exec(v) : null;
    if (!m || +m[4] > 23 || +m[5] > 59 || +(m[6] || 0) > 59) return null;
    const d = new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +(m[6] || 0));
    if (d.getFullYear() !== +m[1] || d.getMonth() !== +m[2] - 1 || d.getDate() !== +m[3]) return null;
    return isFinite(d.getTime()) ? d.toISOString().slice(0, 19) + 'Z' : null;
  }

  function validCode(v) { return typeof v === 'string' && COUPON_RE.test(v); }
  function validRewards(v) { return typeof v === 'string' && v.length <= REWARDS_MAX && !/[\u0000-\u0008\u000b-\u001f\u007f]/.test(v); }
  function validUrl(v) {
    return typeof v === 'string' && v.length <= URL_MAX && /^https:\/\/[^\s]+$/.test(v) && v.length > 8;
  }

  // Optional add/edit fields; null (clear) is only valid on edit.
  const EVENT_FIELDS = {
    code: validCode, rewards: validRewards, url: validUrl,
    starts: function (v) { return eventDateMs(v, false) !== null; },
    ends: function (v) { return eventDateMs(v, true) !== null; }
  };

  function eventFieldsOk(o, allowNull) {
    for (const k of Object.keys(EVENT_FIELDS)) {
      if (!(k in o)) continue;
      if (o[k] === null && allowNull && k !== 'code') continue;
      if (!EVENT_FIELDS[k](o[k])) return false;
    }
    if (typeof o.starts === 'string' && typeof o.ends === 'string' &&
        eventDateMs(o.starts, false) > eventDateMs(o.ends, true)) return false;
    return true;
  }

  // Exact shape check for POST /api/events bodies (main-process IPC guard).
  // Ranges the client cannot know (ends within 2 years, duplicates, coupon-only
  // code on edit) are the server's to refuse.
  function validEventsBody(body) {
    if (!plainObject(body)) return false;
    const keys = Object.keys(body);
    if (keys.length !== 1) return false;
    const k = keys[0];
    const v = body[k];
    if (k === 'delete') return validRef(v, EVENT_ID_RE);
    if (k === 'purge_expired') return v === true;
    if (k === 'done') return exact(v, ['id', 'done']) && validRef(v.id, EVENT_ID_RE) && typeof v.done === 'boolean';
    if (k === 'add') {
      if (!plainObject(v) || !onlyKeys(v, ['kind', 'title', 'code', 'rewards', 'starts', 'ends', 'url'])) return false;
      if (EVENT_KINDS.indexOf(v.kind) < 0 || !validTitle(v.title)) return false;
      if ((v.kind === 'coupon') !== ('code' in v)) return false;
      return eventFieldsOk(v, false);
    }
    if (k === 'edit') {
      if (!plainObject(v) || !onlyKeys(v, ['id', 'title', 'code', 'rewards', 'starts', 'ends', 'url'])) return false;
      if (!validRef(v.id, EVENT_ID_RE) || Object.keys(v).length < 2) return false;
      if ('title' in v && !validTitle(v.title)) return false;
      return eventFieldsOk(v, true);
    }
    return false;
  }

  // Add form strings -> POST body, or an error for the operator. form: {kind,
  // code, title, rewards, ends (datetime-local, local time), url}; blanks omitted.
  function parseEventForm(form) {
    const f = form || {};
    const s = function (k) { return typeof f[k] === 'string' ? f[k].trim() : ''; };
    const kind = s('kind');
    if (EVENT_KINDS.indexOf(kind) < 0) return { ok: false, error: 'pick a kind' };
    const add = { kind: kind, title: s('title') };
    if (!validTitle(add.title)) return { ok: false, error: 'title: 1-' + TITLE_MAX + ' plain characters' };
    if (kind === 'coupon') {
      if (!validCode(s('code'))) return { ok: false, error: 'code: 4-40 letters, digits or -' };
      add.code = s('code').toUpperCase();
    }
    if (s('rewards')) {
      if (!validRewards(s('rewards'))) return { ok: false, error: 'rewards: up to ' + REWARDS_MAX + ' characters' };
      add.rewards = s('rewards');
    }
    if (s('ends')) {
      const iso = localToUtcIso(s('ends'));
      if (!iso) return { ok: false, error: 'ends: pick a valid date and time' };
      add.ends = iso;
    }
    if (s('url')) {
      if (!validUrl(s('url'))) return { ok: false, error: 'url must start with https:// (max ' + URL_MAX + ')' };
      add.url = s('url');
    }
    return { ok: true, body: { add: add } };
  }

  // ---- Coupon suggestions (plan 014) ----
  // GET /api/events `suggested`: candidates read from the official news list
  // (robots.txt-gated, server side). Suggest only: one click posts a normal
  // coupon add; nothing is added without the operator.

  const SUGGEST_STATUS = {
    ok: 'checked', stale: 'stale - last good list', pending: 'checking...',
    off: 'off - robots.txt', error: 'check failed', none: 'off'
  };

  // Valid candidates whose code is not already an item, in server order.
  function suggestedRows(suggested, items) {
    const s = plainObject(suggested) ? suggested : {};
    const list = Array.isArray(s.candidates) ? s.candidates : [];
    const known = {};
    (Array.isArray(items) ? items : []).forEach(function (it) {
      if (plainObject(it) && typeof it.code === 'string') known[it.code.toUpperCase()] = true;
    });
    const out = [];
    list.forEach(function (c) {
      if (!plainObject(c) || !validCode(c.code) || !validTitle(c.title) || !validUrl(c.url)) return;
      const code = c.code.toUpperCase();
      if (known[code]) return;
      known[code] = true;
      const date = typeof c.date === 'string' && ISO_DAY.test(c.date) ? c.date : null;
      out.push({ code: code, title: c.title, url: c.url, date: date });
    });
    return out;
  }

  // Candidate -> POST /api/events body (a plain coupon add), or null.
  function suggestAddBody(c) {
    if (!plainObject(c) || !validCode(c.code) || !validTitle(c.title)) return null;
    const add = { kind: 'coupon', title: c.title, code: c.code.toUpperCase() };
    if (validUrl(c.url)) add.url = c.url;
    return { add: add };
  }

  // Sources card line for the coupon check: {status, text}; reason when off.
  function suggestStatus(suggested) {
    const s = plainObject(suggested) ? suggested : null;
    const st = s && Object.prototype.hasOwnProperty.call(SUGGEST_STATUS, s.status) ? s.status : 'none';
    let text = 'coupon check: ' + SUGGEST_STATUS[st];
    if (st === 'off' && (s.robots === 'disallow' || s.robots === 'unreachable')) text += ' ' + s.robots;
    return { status: st, text: text };
  }

  // ---- Deadeye (plan 007) ----
  // Operator build notes (markdown, rendered as a safe subset) and an ordered
  // enhancement plan. Text only: nothing here is executed or sent to the game.

  const DEADEYE_LEVELS = [];
  for (let i = 0; i <= 15; i++) DEADEYE_LEVELS.push('+' + i);
  DEADEYE_LEVELS.push('PRI', 'DUO', 'TRI', 'TET', 'PEN');
  const DEADEYE_SECTIONS = ['addons', 'crystals', 'artifacts', 'lightstones', 'rotation', 'misc'];
  const NOTE_MAX = 20000;            // server: note text 0-20000 chars after \r\n -> \n
  const STEP_NOTE_MAX = 200;
  const STEP_ID_RE = /^d[0-9]{1,9}$/;

  function levelIndex(v) { return typeof v === 'string' ? DEADEYE_LEVELS.indexOf(v) : -1; }

  function escHtml(s) {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  // Inline markup on already-escaped text: `code` spans are left literal,
  // then **bold**, then *em* (not inside words, not around spaces).
  function mdInline(s) {
    return s.split(/(`[^`\n]+`)/).map(function (part, i) {
      if (i % 2) return '<code>' + part.slice(1, -1) + '</code>';
      return part.replace(/\*\*(?=\S)([^*]*?\S)\*\*/g, '<strong>$1</strong>')
        .replace(/(^|[^*\w])\*(?=\S)([^*]*?\S)\*(?![*\w])/g, '$1<em>$2</em>');
    }).join('');
  }

  // Markdown -> HTML, safe subset. ALL of & < > " ' are escaped first; the
  // only markup added afterwards is fixed attribute-less tags (h1-h3, p,
  // ul/ol/li, strong, em, code, pre). No links, images or raw HTML ever.
  function renderMarkdown(text) {
    if (typeof text !== 'string') return '';
    const lines = escHtml(text.replace(/\r\n?/g, '\n')).split('\n');
    const out = [];
    let para = [];
    let list = null;
    const flush = function () {
      if (para.length) out.push('<p>' + mdInline(para.join('\n')) + '</p>');
      para = [];
      if (list) out.push('</' + list + '>');
      list = null;
    };
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      if (/^\s*```/.test(line)) {
        flush();
        const code = [];
        for (i++; i < lines.length && !/^\s*```\s*$/.test(lines[i]); i++) code.push(lines[i]);
        out.push('<pre><code>' + code.join('\n') + '</code></pre>');
        continue;
      }
      if (/^\s*$/.test(line)) { flush(); continue; }
      const h = /^(#{1,3})[ \t]+(\S.*)$/.exec(line);
      if (h) {
        flush();
        out.push('<h' + h[1].length + '>' + mdInline(h[2].trim()) + '</h' + h[1].length + '>');
        continue;
      }
      const ul = /^\s*[-*][ \t]+(.*)$/.exec(line);
      const ol = ul ? null : /^\s*\d{1,9}\.[ \t]+(.*)$/.exec(line);
      if (ul || ol) {
        const kind = ul ? 'ul' : 'ol';
        if (para.length || list !== kind) { flush(); out.push('<' + kind + '>'); list = kind; }
        out.push('<li>' + mdInline((ul || ol)[1].trim()) + '</li>');
        continue;
      }
      if (list) flush();
      para.push(line.trim());
    }
    flush();
    return out.join('');
  }

  function plainLine(v, max) {
    return typeof v === 'string' && v.length <= max && !/[\u0000-\u001f\u007f]/.test(v);
  }

  function validNoteText(v) {
    return typeof v === 'string' && v.replace(/\r\n/g, '\n').length <= NOTE_MAX &&
      !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(v);
  }

  const STEP_CHECKS = {
    item: validName, current: function (v) { return levelIndex(v) >= 0; },
    target: function (v) { return levelIndex(v) >= 0; },
    note: function (v) { return plainLine(v, STEP_NOTE_MAX); }
  };

  function stepFieldsOk(o) {
    for (const k of Object.keys(STEP_CHECKS)) {
      if (k in o && !STEP_CHECKS[k](o[k])) return false;
    }
    return !('current' in o && 'target' in o) || levelIndex(o.target) > levelIndex(o.current);
  }

  // Exact shape check for POST /api/deadeye bodies (main-process IPC guard).
  // What the client cannot know (an edit's order against the stored level,
  // unknown ids, the 100-step cap) is the server's to refuse.
  function validDeadeyeBody(body) {
    if (!plainObject(body)) return false;
    const keys = Object.keys(body);
    if (keys.length !== 1) return false;
    const k = keys[0];
    const v = body[k];
    if (k === 'note') {
      return exact(v, ['section', 'text']) && DEADEYE_SECTIONS.indexOf(v.section) >= 0 && validNoteText(v.text);
    }
    if (k === 'delete_step') return validRef(v, STEP_ID_RE);
    if (k === 'step_done') return exact(v, ['id', 'done']) && validRef(v.id, STEP_ID_RE) && typeof v.done === 'boolean';
    if (k === 'move_step') return exact(v, ['id', 'dir']) && validRef(v.id, STEP_ID_RE) && (v.dir === -1 || v.dir === 1);
    if (k === 'shop_set') return validShopSet(v);
    if (k === 'shop_step') return validShopStep(v);
    if (k === 'add_step') {
      return plainObject(v) && onlyKeys(v, ['item', 'current', 'target', 'note']) &&
        'item' in v && 'current' in v && 'target' in v && stepFieldsOk(v);
    }
    if (k === 'edit_step') {
      return plainObject(v) && onlyKeys(v, ['id', 'item', 'current', 'target', 'note']) &&
        validRef(v.id, STEP_ID_RE) && Object.keys(v).length >= 2 && stepFieldsOk(v);
    }
    const st = validStacksOp(k, v); // plan 036
    return st === null ? false : st;
  }

  // Add-step form strings -> {add_step} body, or an error for the operator.
  function parseStepForm(form) {
    const f = form || {};
    const s = function (k) { return typeof f[k] === 'string' ? f[k].trim() : ''; };
    const item = s('item');
    if (!validName(item)) return { ok: false, error: 'item: 1-' + NAME_MAX + ' plain characters' };
    const cur = levelIndex(f.current);
    const tgt = levelIndex(f.target);
    if (cur < 0 || tgt < 0) return { ok: false, error: 'pick a level for current and target' };
    if (tgt <= cur) return { ok: false, error: 'target must be above current' };
    const add = { item: item, current: f.current, target: f.target };
    if (s('note')) {
      if (!plainLine(s('note'), STEP_NOTE_MAX)) return { ok: false, error: 'note: up to ' + STEP_NOTE_MAX + ' plain characters' };
      add.note = s('note');
    }
    return { ok: true, body: { add_step: add } };
  }

  // ---- Enhancement EV (plan 035) ----
  // GET /api/deadeye/enhance?family=&step=&fs=&crons=0|1 -> {chance_pct, approx,
  // attempts_mean, attempts_p90, pity_cap, crons_mean, cost_mean_silver, ...}.
  // Math on sourced tables and cached prices only.

  const ENHANCE_MAX_FS = 999;

  // Typed FS -> int 0..999, or null.
  function parseFs(text) {
    const s = typeof text === 'string' ? text.trim() : (isNum(text) ? String(text) : '');
    if (!/^[0-9]{1,3}$/.test(s)) return null;
    const n = Number(s);
    return n <= ENHANCE_MAX_FS ? n : null;
  }

  // Levels a plan 007 step climbs through: (current, target], each the level
  // one attempt reaches (the server's `step` key).
  function enhanceSubSteps(current, target) {
    const a = levelIndex(current);
    const b = levelIndex(target);
    return a >= 0 && b > a ? DEADEYE_LEVELS.slice(a + 1, b + 1) : [];
  }

  // A gear family named inside the operator's item text, else null.
  function enhanceFamilyGuess(item, families) {
    if (typeof item !== 'string' || !Array.isArray(families)) return null;
    const low = item.toLowerCase();
    for (const f of families) {
      if (typeof f === 'string' && f && low.indexOf(f.replace(/_/g, ' ')) >= 0) return f;
    }
    return null;
  }

  function enhancePath(family, step, fs, crons) {
    if (typeof family !== 'string' || !/^[a-z][a-z0-9_]{0,23}$/.test(family)) return null;
    if (typeof step !== 'string' || !step || parseFs(fs) === null) return null;
    return '/api/deadeye/enhance?family=' + encodeURIComponent(family) + '&step=' +
      encodeURIComponent(step) + '&fs=' + parseFs(fs) + '&crons=' + (crons ? 1 : 0);
  }

  function fmtAttempts(n) {
    if (!isNum(n)) return '-';
    return n < 100 ? n.toFixed(1) : fmtSilver(n);
  }

  // One EV reply -> display strings; any missing number shows '-'.
  function fmtEv(b) {
    const o = b && typeof b === 'object' ? b : {};
    const pct = isNum(o.chance_pct) ? (o.approx ? '~' : '') + o.chance_pct.toFixed(2) + '%' : '-';
    return {
      step: typeof o.step === 'string' ? o.step : '-',
      chance: pct,
      attempts: fmtAttempts(o.attempts_mean),
      p90: isNum(o.attempts_p90) ? String(o.attempts_p90) : '-',
      pity: isNum(o.pity_cap) ? String(o.pity_cap) : '-',
      crons: isNum(o.crons_mean) && o.crons_mean > 0 ? fmtSilver(o.crons_mean) : '-',
      silver: isNum(o.cost_mean_silver) ? fmtSilver(o.cost_mean_silver) : '-',
      note: typeof o.cost_note === 'string' ? o.cost_note : '',
      // unverified_used: preview fields this result rests on (a crons-off result
      // ignores a preview cron count); older replies fall back to `verified`.
      unverified: Array.isArray(o.unverified_used) ? o.unverified_used.length > 0 : o.verified === false
    };
  }

  function evLine(b) {
    const f = fmtEv(b);
    return f.step + '  ' + f.chance + '  ' + f.attempts + ' tries (p90 ' + f.p90 + ', pity ' + f.pity +
      ')  crons ' + f.crons + '  silver ' + f.silver + (f.unverified ? '  [unverified]' : '');
  }

  // ---- Stacks: failstack bank, Agris pity, cron budget (plan 036) ----
  // GET /api/deadeye `stacks` block; writes are POST /api/deadeye fs_add /
  // fs_use / agris_set / crons_set. Operator-typed inventory only.

  const FS_KINDS = ['advice', 'saved', 'cry'];
  const FS_KIND_LABELS = { advice: 'Advice of Valks', saved: 'saved stack', cry: "Valks' Cry" };
  const FS_COUNT_MAX = 999;
  const AGRIS_STACKS_MAX = 1000;
  const CRONS_MAX = 1e9;
  const CRONS_WEEKLY_MAX = 1e7;
  const ENHANCE_STEPS = [];
  for (let i = 1; i <= 15; i++) ENHANCE_STEPS.push('+' + i);
  ENHANCE_STEPS.push('PRI', 'DUO', 'TRI', 'TET', 'PEN', 'HEX', 'SEP', 'OCT', 'NOV', 'DEC');
  const GEAR_FAMILY_RE = /^[a-z][a-z0-9_]{0,23}$/;

  function intIn(v, lo, hi) { return isInt(v, lo) && v <= hi; }

  function validFsArg(v) {
    return plainObject(v) && onlyKeys(v, ['kind', 'value', 'count']) && 'kind' in v && 'value' in v &&
      FS_KINDS.indexOf(v.kind) >= 0 && intIn(v.value, 1, ENHANCE_MAX_FS) &&
      (!('count' in v) || intIn(v.count, 1, FS_COUNT_MAX));
  }

  // Shape check for the plan 036 ops inside validDeadeyeBody; null = not a stacks op.
  function validStacksOp(k, v) {
    if (k === 'fs_add' || k === 'fs_use') return validFsArg(v);
    if (k === 'agris_set') {
      return exact(v, ['family', 'step', 'stacks']) && typeof v.family === 'string' && GEAR_FAMILY_RE.test(v.family) &&
        ENHANCE_STEPS.indexOf(v.step) >= 0 && intIn(v.stacks, 0, AGRIS_STACKS_MAX);
    }
    if (k === 'crons_set') {
      return plainObject(v) && onlyKeys(v, ['owned', 'weekly_income']) && Object.keys(v).length >= 1 &&
        (!('owned' in v) || intIn(v.owned, 0, CRONS_MAX)) &&
        (!('weekly_income' in v) || intIn(v.weekly_income, 0, CRONS_WEEKLY_MAX));
    }
    return null;
  }

  // Typed whole number lo..hi -> int, or null.
  function parseWhole(text, lo, hi) {
    const s = typeof text === 'string' ? text.trim().replace(/,/g, '') : (isNum(text) ? String(text) : '');
    if (!/^[0-9]{1,10}$/.test(s)) return null;
    const n = Number(s);
    return n >= lo && n <= hi ? n : null;
  }

  // Bank form {kind, value, count} strings -> {fs_add|fs_use: ...}; `use` picks the op.
  function parseFsForm(form, use) {
    const f = form || {};
    if (FS_KINDS.indexOf(f.kind) < 0) return { ok: false, error: 'pick a stack kind' };
    const value = parseWhole(f.value, 1, ENHANCE_MAX_FS);
    if (value === null) return { ok: false, error: 'FS: a whole number 1-' + ENHANCE_MAX_FS };
    const blank = typeof f.count !== 'string' || !f.count.trim();
    const count = blank ? 1 : parseWhole(f.count, 1, FS_COUNT_MAX);
    if (count === null) return { ok: false, error: 'count: a whole number 1-' + FS_COUNT_MAX };
    const arg = { kind: f.kind, value: value };
    if (count !== 1) arg.count = count;
    const body = {};
    body[use ? 'fs_use' : 'fs_add'] = arg;
    return { ok: true, body: body };
  }

  function parseAgrisForm(form) {
    const f = form || {};
    if (typeof f.family !== 'string' || !GEAR_FAMILY_RE.test(f.family)) return { ok: false, error: 'pick a gear family' };
    if (ENHANCE_STEPS.indexOf(f.step) < 0) return { ok: false, error: 'pick a level' };
    const stacks = parseWhole(f.stacks, 0, AGRIS_STACKS_MAX);
    if (stacks === null) return { ok: false, error: 'stacks: a whole number 0-' + AGRIS_STACKS_MAX };
    return { ok: true, body: { agris_set: { family: f.family, step: f.step, stacks: stacks } } };
  }

  // Blank fields are left out; at least one is needed.
  function parseCronsForm(form) {
    const f = form || {};
    const arg = {};
    const fields = [['owned', CRONS_MAX], ['weekly_income', CRONS_WEEKLY_MAX]];
    for (const pair of fields) {
      const raw = f[pair[0]];
      if (typeof raw !== 'string' || !raw.trim()) continue;
      const n = parseWhole(raw, 0, pair[1]);
      if (n === null) return { ok: false, error: pair[0].replace('_', ' ') + ': a whole number 0-' + fmtSilverExact(pair[1]) };
      arg[pair[0]] = n;
    }
    if (!Object.keys(arg).length) return { ok: false, error: 'type crons owned and/or weekly income' };
    return { ok: true, body: { crons_set: arg } };
  }

  function fmtCrons(n) { return isNum(n) ? fmtSilverExact(Math.ceil(n - 1e-9)) : '-'; }

  function fsKind(k) { return FS_KIND_LABELS[k] || String(k); }

  function pityText(fails) {
    if (!isNum(fails)) return 'no Agris threshold';
    return fails === 0 ? 'next attempt guaranteed' : 'guaranteed in ' + fails + (fails === 1 ? ' fail' : ' fails');
  }

  // stacks block -> display strings (never HTML).
  function fmtStacks(s) {
    const o = plainObject(s) ? s : {};
    const bank = (Array.isArray(o.fs_bank) ? o.fs_bank : []).filter(plainObject).map(function (r) {
      return { kind: r.kind, value: r.value, text: fsKind(r.kind) + ' ' + r.value + ' x' + r.count };
    });
    const agris = (Array.isArray(o.agris) ? o.agris : []).filter(plainObject).map(function (r) {
      return { family: r.family, step: r.step, text: r.family + ' ' + r.step + ': ' + r.stacks + ' stacks, ' + pityText(r.fails_to_guarantee) };
    });
    const a = plainObject(o.advice) ? o.advice : {};
    let advice;
    if (typeof a.level !== 'string') {
      advice = typeof a.reason === 'string' ? a.reason : '-';
    } else {
      const head = (typeof a.item === 'string' ? a.item + ' ' : '') + '-> ' + a.level +
        (isNum(a.softcap_fs) ? ' (soft cap ' + a.softcap_fs + ')' : '');
      const pick = plainObject(a.suggest) ? 'use ' + fsKind(a.suggest.kind) + ' ' + a.suggest.value :
        (typeof a.reason === 'string' ? a.reason : '-');
      const pity = plainObject(a.agris) && isNum(a.agris.threshold) ? '; Agris ' + pityText(a.agris.fails_to_guarantee) : '';
      advice = head + ': ' + pick + pity;
    }
    const b = plainObject(o.budget) ? o.budget : {};
    let budget = 'crons ' + fmtCrons(b.owned) + ' owned / ' + fmtCrons(b.needed) + ' expected';
    if (isNum(b.gap) && b.gap > 0) {
      budget += ', short ' + fmtCrons(b.gap) + (isNum(b.weeks) ? ' (~' + b.weeks + (b.weeks === 1 ? ' week)' : ' weeks)') : ' (no weekly income set)');
    } else if (isNum(b.needed)) {
      budget += ', covered';
    }
    const unk = (Array.isArray(b.unknown) ? b.unknown : []).filter(plainObject).map(function (u) { return u.family + ' ' + u.level; });
    return { bank: bank, agris: agris, advice: advice, budget: budget,
      unknown: unk.length ? 'no chance data (not counted): ' + unk.join(', ') : '' };
  }

  // ---- Shopping list (plan 037) ----
  // GET /api/deadeye/shopping -> {lines: [{id, name, qty, expected, unit, total,
  // preorder, watched, note}], total, priced_total, missing_prices,
  // can_afford_by, afford: {need, silver_per_h, hours_per_day, per_day, days,
  // reason}, steps, families, settings: {silver_on_hand, hours_per_day}}.
  // Cached read-only prices; "watch" only edits EW's own watchlist.

  const SHOP_MAX_SILVER = 1e15;
  const SHOP_MAX_HOURS = 24;
  // GEAR_FAMILY_RE: shared with the plan 036 stacks block above.

  function validShopSet(v) {
    if (!plainObject(v) || !Object.keys(v).length || !onlyKeys(v, ['silver_on_hand', 'hours_per_day'])) return false;
    if ('silver_on_hand' in v && !(isInt(v.silver_on_hand, 0) && v.silver_on_hand <= SHOP_MAX_SILVER)) return false;
    return !('hours_per_day' in v) || (isNum(v.hours_per_day) && v.hours_per_day > 0 && v.hours_per_day <= SHOP_MAX_HOURS);
  }

  function validShopStep(v) {
    if (!plainObject(v) || !onlyKeys(v, ['id', 'family', 'fs', 'crons']) || Object.keys(v).length < 2) return false;
    if (!validRef(v.id, STEP_ID_RE)) return false;
    if ('family' in v && v.family !== null && !(typeof v.family === 'string' && GEAR_FAMILY_RE.test(v.family))) return false;
    if ('fs' in v && !(isInt(v.fs, 0) && v.fs <= ENHANCE_MAX_FS)) return false;
    return !('crons' in v) || typeof v.crons === 'boolean';
  }

  // Settings form strings -> {shop_set} body, or an error for the operator.
  // Silver accepts digits with optional , separators.
  function parseShopForm(form) {
    const f = form || {};
    const silver = String(f.silver === undefined || f.silver === null ? '' : f.silver).trim().replace(/,/g, '');
    const hours = String(f.hours === undefined || f.hours === null ? '' : f.hours).trim();
    const body = {};
    if (silver !== '') {
      const n = /^[0-9]{1,16}$/.test(silver) ? Number(silver) : NaN;
      if (!(n <= SHOP_MAX_SILVER)) return { ok: false, error: 'silver on hand: a whole number 0-' + fmtSilver(SHOP_MAX_SILVER) };
      body.silver_on_hand = n;
    }
    if (hours !== '') {
      const h = /^[0-9]{1,2}(\.[0-9]{1,2})?$/.test(hours) ? Number(hours) : NaN;
      if (!(h > 0 && h <= SHOP_MAX_HOURS)) return { ok: false, error: 'hours per day: a number above 0, up to 24' };
      body.hours_per_day = h;
    }
    if (!Object.keys(body).length) return { ok: false, error: 'enter silver on hand or hours per day' };
    return { ok: true, body: { shop_set: body } };
  }

  // One shopping line -> display strings; any missing number shows '-'.
  function fmtShopLine(ln) {
    const o = plainObject(ln) ? ln : {};
    const pre = { capped: 'pre-order (capped)', no_stock: 'pre-order (no stock)' };
    return {
      name: typeof o.name === 'string' && o.name ? o.name : (isInt(o.id, 0) ? '#' + o.id : '-'),
      qty: isInt(o.qty, 0) ? fmtSilver(o.qty) : '-',
      unit: isNum(o.unit) ? fmtSilver(o.unit) : '-',
      total: isNum(o.total) ? fmtSilver(o.total) : '-',
      preorder: pre[o.preorder] || '',
      note: typeof o.note === 'string' ? o.note : ''
    };
  }

  // The list's summary -> {total, afford} strings.
  function fmtShopping(b) {
    const o = plainObject(b) ? b : {};
    const a = plainObject(o.afford) ? o.afford : {};
    const missing = Array.isArray(o.missing_prices) ? o.missing_prices.length : 0;
    let total = isNum(o.total) ? fmtSilver(o.total) : '-';
    if (!isNum(o.total) && missing) {
      total = (isNum(o.priced_total) && o.priced_total > 0 ? '>= ' + fmtSilver(o.priced_total) + ' ' : '') +
        '(' + missing + ' unpriced)';
    }
    let afford;
    if (typeof o.can_afford_by === 'string' && a.days === 0) afford = 'covered by silver on hand';
    else if (typeof o.can_afford_by === 'string') {
      afford = 'can afford by ' + o.can_afford_by + ' (' + a.days + ' d at ' + fmtSilver(a.silver_per_h) +
        '/h x ' + a.hours_per_day + ' h/day)';
    } else afford = 'can afford by: - ' + (typeof a.reason === 'string' && a.reason ? '(' + a.reason + ')' : '');
    return { total: total, afford: afford.trim() };
  }

  // "watch" button -> plan 002 POST /api/market/watch body (EW's own list only).
  function shopWatchBody(id) {
    return isInt(id, 1) ? { add: { id: id, sid: 0 } } : null;
  }

  // ---- Game state (plan 008) ----
  // GET /api/game -> {state, since, log_file, last_event, screenshots, configured}.
  // Display only: nothing here ever reaches the game.

  const GAME_STATES = ['not_running', 'running', 'logged_in', 'disconnected', 'unconfigured'];
  const GAME_LABELS = {
    not_running: { cls: 'off', label: 'not running' },
    running: { cls: 'warn', label: 'running' },
    logged_in: { cls: 'ok', label: 'logged in' },
    disconnected: { cls: 'bad', label: 'disconnected' },
    unconfigured: { cls: 'unknown', label: 'unconfigured' },
    offline: { cls: 'unknown', label: 'offline' }
  };
  const GAME_SHOTS_MAX = 50;
  const GAME_EVENT_MAX = 200;
  const GAME_CONFIG_HINT = 'set bdo.install_dir and bdo.documents_dir in config/local.json, then restart the server';

  function gameStateLabel(state) {
    if (typeof state === 'string' && Object.prototype.hasOwnProperty.call(GAME_LABELS, state)) {
      return Object.assign({}, GAME_LABELS[state]);
    }
    return { cls: 'unknown', label: 'unknown' };
  }

  // Number below 1e12 = epoch seconds (Python time.time()), else millis; or ISO.
  function gameTimeMs(v) {
    let ms = null;
    if (isNum(v)) ms = v < 1e12 ? v * 1000 : v;
    else if (typeof v === 'string' && v) ms = Date.parse(v);
    return isNum(ms) && ms > 0 ? Math.round(ms) : null;
  }

  function gameEventText(ev) {
    let s = ev;
    if (plainObject(ev)) s = typeof ev.Log === 'string' ? ev.Log : ev.log;
    if (typeof s !== 'string') return null;
    s = s.replace(/[\x00-\x1f\x7f]+/g, ' ').replace(/\s+/g, ' ').trim();
    return s ? s.slice(0, GAME_EVENT_MAX) : null;
  }

  function gameShot(s) {
    if (!plainObject(s) || typeof s.name !== 'string' || !s.name) return null;
    return { name: s.name, size: isNum(s.size) && s.size >= 0 ? s.size : null, mtime: gameTimeMs(s.mtime) };
  }

  function normalizeGame(d) {
    if (!plainObject(d)) return null;
    let state = GAME_STATES.indexOf(d.state) >= 0 ? d.state : 'unknown';
    const configured = typeof d.configured === 'boolean' ? d.configured : null;
    if (configured === false) state = 'unconfigured';
    const log = typeof d.log_file === 'string' && d.log_file ? d.log_file.split(/[\\/]/).pop() : '';
    const shots = (Array.isArray(d.screenshots) ? d.screenshots : []).map(gameShot)
      .filter(Boolean)
      .sort(function (a, b) { return (b.mtime === null ? -1 : b.mtime) - (a.mtime === null ? -1 : a.mtime); })
      .slice(0, GAME_SHOTS_MAX);
    return {
      state: state, since: gameTimeMs(d.since), log_file: log || null,
      last_event: gameEventText(d.last_event), screenshots: shots, configured: configured
    };
  }

  function pad2(n) { return (n < 10 ? '0' : '') + n; }

  // Local wall clock: HH:MM on the same local day as now, else MM-DD HH:MM.
  function fmtClock(ms, now) {
    if (!isNum(ms)) return '-';
    const d = new Date(ms);
    const n = new Date(isNum(now) ? now : ms);
    const hm = pad2(d.getHours()) + ':' + pad2(d.getMinutes());
    const same = d.getFullYear() === n.getFullYear() && d.getMonth() === n.getMonth() && d.getDate() === n.getDate();
    return same ? hm : pad2(d.getMonth() + 1) + '-' + pad2(d.getDate()) + ' ' + hm;
  }

  function gameSinceText(sinceMs, now) {
    if (!isNum(sinceMs)) return '-';
    return fmtClock(sinceMs, now) + ' (' + fmtAge(Math.max(0, now - sinceMs) / 1000) + ' ago)';
  }

  // ---- OCR (plan 009) ----
  // POST /api/ocr {file} -> {text, silver, buffs}. Nothing is applied on its
  // own: the card offers "use silver" / "arm buff", which go through the
  // existing grind routes as normal operator input.

  const OCR_NAME_MAX = 255;
  const OCR_TEXT_MAX = 20000;

  // Exact shape check for POST /api/ocr bodies (main-process IPC guard): a bare
  // file name as the watcher lists it - never a path. The server checks the list.
  function validOcrBody(body) {
    if (!exact(body, ['file'])) return false;
    const f = body.file;
    return typeof f === 'string' && f.trim().length > 0 && f.length <= OCR_NAME_MAX &&
      !/[\u0000-\u001f\u007f\\/:]/.test(f) && f !== '.' && f !== '..';
  }

  function normalizeOcr(d) {
    if (!plainObject(d)) return null;
    const text = typeof d.text === 'string' ? d.text.replace(/\r\n?/g, '\n').slice(0, OCR_TEXT_MAX) : '';
    const silver = Number.isInteger(d.silver) && inRange(d.silver, SILVER) ? d.silver : null;
    const seen = [];
    const buffs = [];
    (Array.isArray(d.buffs) ? d.buffs : []).forEach(function (b) {
      if (!plainObject(b) || !validName(b.name) || !isNum(b.minutes)) return;
      const m = Math.min(BUFF_MINUTES[1], Math.round(b.minutes));
      const n = b.name.toLowerCase();
      if (m < BUFF_MINUTES[0] || seen.indexOf(n) >= 0) return;
      seen.push(n);
      buffs.push({ name: b.name, minutes: m });
    });
    return { text: text, silver: silver, buffs: buffs };
  }

  // An OCR buff -> the grind "buff" POST body, or null when it would not pass.
  function ocrBuffBody(b) {
    if (!plainObject(b)) return null;
    const body = { buff: { name: b.name, minutes: b.minutes } };
    return validGrindBody(body) ? body : null;
  }

  // Silver -> the plain digits the grind stop form parses (and the clipboard gets).
  function ocrSilverInput(n) {
    return Number.isInteger(n) && inRange(n, SILVER) ? String(n) : null;
  }

  function ocrBuffLabel(b) {
    const m = b.minutes;
    return b.name + ' - ' + (m < 60 ? m + 'm' : fmtDuration(m * 60000));
  }

  // ---- Leveling (plan 011) ----
  // Operator-typed level + XP percent samples; rate, ETA and Hot Time windows
  // come from the server (server/ew/leveling.py). Countdowns run locally from
  // the fetch time; an ended window leaves the XP stack until the next poll.

  const PCT_RE = /^\d{1,3}(\.\d{1,3})?$/;
  const HHMM_RE = /^([01][0-9]|2[0-3]):[0-5][0-9]$/;
  const HOT_ID_RE = /^h[0-9]{1,9}$/;
  const HOT_LABEL_MAX = 40;
  const MILESTONES_MAX = 20;
  const DAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

  // 0..100 with at most 3 decimals (server _ok_pct).
  function validXpPct(v) {
    return isNum(v) && v >= 0 && v <= 100 && Math.abs(v * 1000 - Math.round(v * 1000)) < 1e-6;
  }

  function validLabel(t) {
    return typeof t === 'string' && t.trim().length > 0 && t.length <= HOT_LABEL_MAX &&
      !/[\u0000-\u001f\u007f]/.test(t);
  }

  function validDays(v) {
    return Array.isArray(v) && v.length > 0 && v.every(function (d) { return inRange(d, [0, 6]); }) &&
      v.filter(function (d, i) { return v.indexOf(d) === i; }).length === v.length;
  }

  // Plan 018 XP epochs: server levels.validate_epoch (printable ASCII source).
  const EPOCH_ID_RE = /^[a-z0-9-]{1,40}$/;
  const EPOCH_SOURCE_MAX = 200;

  function validAscii(t, max, allowEmpty) {
    return typeof t === 'string' && (allowEmpty === true || t.trim().length > 0) && t.length <= max &&
      /^[\x20-\x7e]*$/.test(t);
  }

  function validMilestones(v) {
    return Array.isArray(v) && v.length <= MILESTONES_MAX && v.every(function (m) { return inRange(m, LEVEL); }) &&
      v.filter(function (m, i) { return v.indexOf(m) === i; }).length === v.length;
  }

  // Exact shape check for POST /api/leveling bodies (main-process IPC guard).
  function validLevelingBody(body) {
    if (!plainObject(body)) return false;
    const keys = Object.keys(body);
    if (keys.length !== 1) return false;
    const k = keys[0];
    const v = body[k];
    if (k === 'sample') return exact(v, ['level', 'pct']) && inRange(v.level, LEVEL) && validXpPct(v.pct);
    if (k === 'sample_del') return typeof v === 'string' && ISO_TS.test(v);
    if (k === 'hot_del') return validRef(v, HOT_ID_RE);
    if (k === 'milestones') return validMilestones(v);
    if (k === 'epoch_del') return validRef(v, EPOCH_ID_RE);
    if (k === 'deadline_del') return validRef(v, EPOCH_ID_RE);
    if (k === 'deadline_set') {
      const keys = ['id', 'label', 'needs_level', 'enrol_by_utc', 'quests_by_utc', 'source', 'verified'];
      const extra = plainObject(v) ? Object.keys(v).filter(function (x) { return keys.indexOf(x) < 0; }) : [];
      return plainObject(v) && keys.every(function (x) { return x in v; }) &&
        (extra.length === 0 || (extra.length === 1 && extra[0] === 'note' && validAscii(v.note, EPOCH_SOURCE_MAX, true))) &&
        validRef(v.id, EPOCH_ID_RE) && validAscii(v.label, HOT_LABEL_MAX) && inRange(v.needs_level, LEVEL) &&
        typeof v.enrol_by_utc === 'string' && ISO_TS.test(v.enrol_by_utc) &&
        (v.quests_by_utc === null || (typeof v.quests_by_utc === 'string' && ISO_TS.test(v.quests_by_utc))) &&
        validAscii(v.source, EPOCH_SOURCE_MAX) && typeof v.verified === 'boolean';
    }
    if (k === 'epoch_add') {
      return exact(v, ['id', 'starts_utc', 'label', 'source', 'verified']) && validRef(v.id, EPOCH_ID_RE) &&
        typeof v.starts_utc === 'string' && ISO_TS.test(v.starts_utc) && validAscii(v.label, HOT_LABEL_MAX) &&
        validAscii(v.source, EPOCH_SOURCE_MAX) && typeof v.verified === 'boolean';
    }
    if (k === 'hot_add') {
      return exact(v, ['days', 'start', 'end', 'label', 'pct']) && validDays(v.days) &&
        typeof v.start === 'string' && HHMM_RE.test(v.start) && typeof v.end === 'string' &&
        HHMM_RE.test(v.end) && v.start !== v.end && validLabel(v.label) && inRange(v.pct, XP_PCT);
    }
    return false;
  }

  // Quick entry strings ("52", "37.512" / "37,5%") -> {sample} body or an error.
  function parseSampleForm(form) {
    const f = form || {};
    const level = wholeIn(f.level, LEVEL);
    if (level === null) return { ok: false, error: 'level must be a whole number ' + LEVEL[0] + '-' + LEVEL[1] };
    const t = (f.pct === undefined || f.pct === null ? '' : String(f.pct)).trim().replace(/%$/, '').trim().replace(',', '.');
    const pct = PCT_RE.test(t) ? Number(t) : NaN;
    if (!validXpPct(pct)) return { ok: false, error: 'XP % must be 0-100, up to 3 decimals' };
    return { ok: true, body: { sample: { level: level, pct: pct } } };
  }

  // Hot window editor -> {hot_add} body or an error. days: weekday numbers
  // (Monday=0) as numbers or strings; times are UTC HH:MM.
  function parseHotForm(form) {
    const f = form || {};
    const raw = Array.isArray(f.days) ? f.days : [];
    const days = [];
    for (const d of raw) {
      const n = wholeIn(d, [0, 6]);
      if (n === null) return { ok: false, error: 'bad weekday' };
      if (days.indexOf(n) < 0) days.push(n);
    }
    days.sort(function (a, b) { return a - b; });
    if (!days.length) return { ok: false, error: 'pick at least one day' };
    const start = typeof f.start === 'string' ? f.start.trim() : '';
    const end = typeof f.end === 'string' ? f.end.trim() : '';
    if (!HHMM_RE.test(start) || !HHMM_RE.test(end)) return { ok: false, error: 'start / end must be HH:MM (UTC)' };
    if (start === end) return { ok: false, error: 'start and end must differ' };
    const label = typeof f.label === 'string' ? f.label.trim() : '';
    if (!validLabel(label)) return { ok: false, error: 'label: 1-' + HOT_LABEL_MAX + ' plain characters' };
    const pct = wholeIn(f.pct, XP_PCT);
    if (pct === null) return { ok: false, error: 'XP % must be a whole number 0-1000' };
    return { ok: true, body: { hot_add: { days: days, start: start, end: end, label: label, pct: pct } } };
  }

  // "61, 50 56" -> {milestones: [50, 56, 61]}; blank clears the list.
  function parseMilestones(s) {
    const parts = String(s === undefined || s === null ? '' : s).split(/[\s,]+/).filter(function (x) { return x; });
    const out = [];
    for (const p of parts) {
      const n = wholeIn(p, LEVEL);
      if (n === null) return { ok: false, error: 'milestones: whole levels ' + LEVEL[0] + '-' + LEVEL[1] };
      if (out.indexOf(n) >= 0) return { ok: false, error: 'milestones: level ' + n + ' twice' };
      out.push(n);
    }
    if (out.length > MILESTONES_MAX) return { ok: false, error: 'at most ' + MILESTONES_MAX + ' milestones' };
    out.sort(function (a, b) { return a - b; });
    return { ok: true, body: { milestones: out } };
  }

  // Epoch editor (plan 018) -> {epoch_add} body or an error. start is UTC
  // "YYYY-MM-DD HH:MM"; a blank id is slugged from the label (an existing id
  // corrects that epoch, e.g. the confirmed live maintenance time).
  function parseEpochForm(form) {
    const f = form || {};
    const label = typeof f.label === 'string' ? f.label.trim() : '';
    if (!validAscii(label, HOT_LABEL_MAX)) return { ok: false, error: 'label: 1-' + HOT_LABEL_MAX + ' plain ASCII characters' };
    let id = typeof f.id === 'string' ? f.id.trim() : '';
    if (!id) id = label.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40).replace(/-+$/, '');
    if (!EPOCH_ID_RE.test(id)) return { ok: false, error: 'id: lowercase letters, digits and - (1-40)' };
    const m = /^(\d{4}-\d{2}-\d{2})[ T]([01]\d|2[0-3]):([0-5]\d)$/.exec(typeof f.start === 'string' ? f.start.trim() : '');
    const starts = m ? m[1] + 'T' + m[2] + ':' + m[3] + ':00Z' : '';
    if (!m || !isFinite(Date.parse(starts))) return { ok: false, error: 'start must be YYYY-MM-DD HH:MM (UTC)' };
    const source = typeof f.source === 'string' ? f.source.trim() : '';
    if (!validAscii(source, EPOCH_SOURCE_MAX)) return { ok: false, error: 'source: 1-' + EPOCH_SOURCE_MAX + ' plain ASCII characters' };
    return { ok: true, body: { epoch_add: { id: id, starts_utc: starts, label: label, source: source, verified: f.verified === true } } };
  }

  // "Lv 75 cap ... live 2d03h ago" / "... in 2d03h" for an epoch brief, '' for none.
  function epochText(e) {
    if (!plainObject(e) || typeof e.label !== 'string' || !isNum(e.starts_in_s)) return '';
    const when = e.starts_in_s > 0 ? 'in ' + fmtEta(e.starts_in_s) : 'live ' + fmtEta(-e.starts_in_s) + ' ago';
    return e.label + ' ' + when + (e.verified === true ? '' : ' (verify date)');
  }

  function fmtRate(r) {
    if (!isNum(r)) return '-';
    return (r >= 1 ? r.toFixed(1) : r.toFixed(2)) + ' %/h';
  }

  // Seconds -> "15h12m" / "45m" / "5d05h" (100 h and up).
  function fmtEta(s) {
    if (!isNum(s)) return '-';
    const v = Math.max(0, Math.floor(s));
    const h = Math.floor(v / 3600);
    const m = Math.floor((v % 3600) / 60);
    if (h >= 100) return Math.floor(h / 24) + 'd' + pad2(h % 24) + 'h';
    return h > 0 ? h + 'h' + pad2(m) + 'm' : m + 'm';
  }

  function fmtDays(days) {
    const d = (Array.isArray(days) ? days : []).filter(function (x) { return inRange(x, [0, 6]); });
    if (d.length === 7) return 'daily';
    return d.map(function (x) { return DAY_NAMES[x]; }).join(' ');
  }

  function epochBrief(e) {
    if (!plainObject(e) || typeof e.id !== 'string' || typeof e.label !== 'string' ||
      typeof e.starts_utc !== 'string' || !isNum(e.starts_in_s)) return null;
    return { id: e.id, label: e.label, starts_utc: e.starts_utc, starts_in_s: e.starts_in_s,
      source: typeof e.source === 'string' ? e.source : '', verified: e.verified === true, tracked: e.tracked === true };
  }

  function normalizeLeveling(d) {
    if (!plainObject(d) || !Array.isArray(d.milestones)) return null;
    const hot = plainObject(d.hot) ? d.hot : {};
    const nx = hot.next;
    return {
      now: typeof d.now === 'string' ? d.now : null,
      level: inRange(d.level, LEVEL) ? d.level : null,
      pct: validXpPct(d.pct) ? d.pct : null,
      rate_pct_h: isNum(d.rate_pct_h) && d.rate_pct_h > 0 ? d.rate_pct_h : null,
      eta_next_s: isNum(d.eta_next_s) ? d.eta_next_s : null,
      next_milestone: inRange(d.next_milestone, LEVEL) ? d.next_milestone : null,
      xp_stack_pct: isNum(d.xp_stack_pct) ? d.xp_stack_pct : 0,
      xp_parts: (Array.isArray(d.xp_parts) ? d.xp_parts : []).filter(function (p) {
        return plainObject(p) && isNum(p.pct);
      }),
      hot: {
        active: (Array.isArray(hot.active) ? hot.active : []).filter(function (a) {
          return plainObject(a) && isNum(a.pct) && isNum(a.ends_in_s);
        }),
        next: plainObject(nx) && isNum(nx.pct) && isNum(nx.starts_in_s) ? nx : null
      },
      next_milestone_label: typeof d.next_milestone_label === 'string' ? d.next_milestone_label : null,
      milestones: d.milestones.filter(function (m) { return inRange(m, LEVEL); }),
      milestone_labels: plainObject(d.milestone_labels) ? d.milestone_labels : {},
      milestones_seed: d.milestones_seed === true,
      hot_windows: (Array.isArray(d.hot_windows) ? d.hot_windows : []).filter(function (w) {
        return plainObject(w) && typeof w.id === 'string';
      }),
      // Plan 018 XP epochs: the rate only counts samples since `epoch`.
      epoch: epochBrief(d.epoch),
      epoch_next: epochBrief(d.epoch_next),
      epochs: (Array.isArray(d.epochs) ? d.epochs : []).map(epochBrief).filter(function (e) { return e !== null; }),
      epoch_error: typeof d.epoch_error === 'string' ? d.epoch_error : null,
      kill_xp_cap: typeof d.kill_xp_cap === 'string' ? d.kill_xp_cap : null,
      // Plan 024 level-gated deadlines, already decorated with state by the server.
      deadlines: (Array.isArray(d.deadlines) ? d.deadlines : []).map(deadlineBrief).filter(function (x) { return x !== null; }),
      samples: (Array.isArray(d.samples) ? d.samples : []).filter(plainObject)
    };
  }

  // ---- plan 024: level-gated deadlines (Olvia Academy) ----
  const DEADLINE_STATES = ['done', 'on_track', 'tight', 'late', 'unknown'];
  const MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  function deadlineBrief(d) {
    if (!plainObject(d) || typeof d.id !== 'string' || typeof d.label !== 'string' ||
      !inRange(d.needs_level, LEVEL) || typeof d.enrol_by_utc !== 'string' ||
      DEADLINE_STATES.indexOf(d.state) < 0) return null;
    return { id: d.id, label: d.label, needs_level: d.needs_level, enrol_by_utc: d.enrol_by_utc,
      state: d.state, reach_utc: typeof d.reach_utc === 'string' ? d.reach_utc : null,
      margin_h: isNum(d.margin_h) ? d.margin_h : null, verified: d.verified === true };
  }

  // ISO time -> "Nov 5" (UTC date: deadlines are published as UTC days), '?' for junk.
  function fmtMonthDay(iso) {
    const t = typeof iso === 'string' ? Date.parse(iso) : NaN;
    if (!isFinite(t)) return '?';
    const d = new Date(t);
    return MONTH_NAMES[d.getUTCMonth()] + ' ' + d.getUTCDate();
  }

  // Pill for one deadline row: {text, cls} (cls is an ew-pill state class).
  function deadlinePill(d) {
    const b = deadlineBrief(d);
    if (!b) return { text: '-', cls: 'unknown' };
    return {
      done: { text: 'done', cls: 'ok' }, on_track: { text: 'on track', cls: 'ok' },
      tight: { text: 'tight', cls: 'warn' }, late: { text: 'late', cls: 'bad' },
      unknown: { text: 'no rate', cls: 'unknown' }
    }[b.state];
  }

  // "Olvia Academy: Lv 60 by Nov 5 - you reach 60 ~Oct 29 (on track)".
  function deadlineLine(d) {
    const b = deadlineBrief(d);
    if (!b) return '';
    const head = b.label + ': Lv ' + b.needs_level + ' by ' + fmtMonthDay(b.enrol_by_utc) +
      (b.verified ? '' : ' (verify date)') + ' - ';
    let tail;
    if (b.state === 'done') tail = 'Lv ' + b.needs_level + ' reached';
    else if (b.reach_utc) tail = 'you reach ' + b.needs_level + ' ~' + fmtMonthDay(b.reach_utc);
    else if (b.state === 'late') tail = 'enrolment closed';
    else tail = 'log XP for an ETA';
    return head + tail + ' (' + deadlinePill(b).text + ')';
  }

  // Overlay: the worst tight / late deadline as {text, cls}, or null (shown
  // only when one is at risk).
  function deadlineAlert(list) {
    const rows = (Array.isArray(list) ? list : []).map(deadlineBrief).filter(function (b) {
      return b !== null && (b.state === 'late' || b.state === 'tight');
    });
    if (!rows.length) return null;
    const late = rows.filter(function (b) { return b.state === 'late'; });
    const b = (late.length ? late : rows)[0];
    return { text: b.label + ' Lv ' + b.needs_level + ' ' + deadlinePill(b).text, cls: deadlinePill(b).cls };
  }

  // Live hot status `since` the fetch: {active, next, stack, due}. due = a
  // window ended or started locally, so the caller should re-poll.
  function hotLive(hot, stack, fetchedMs, now) {
    const el = sinceFetch(fetchedMs, now);
    const h = plainObject(hot) ? hot : {};
    let total = isNum(stack) ? stack : 0;
    let due = false;
    const active = [];
    (Array.isArray(h.active) ? h.active : []).forEach(function (a) {
      if (!plainObject(a) || !isNum(a.ends_in_s)) return;
      const left = Math.floor(a.ends_in_s - el);
      if (left > 0) active.push(Object.assign({}, a, { ends_in_s: left }));
      else { due = true; if (isNum(a.pct)) total -= a.pct; }
    });
    let next = null;
    if (plainObject(h.next) && isNum(h.next.starts_in_s)) {
      const left = Math.floor(h.next.starts_in_s - el);
      if (left > 0) next = Object.assign({}, h.next, { starts_in_s: left });
      else due = true;
    }
    return { active: active, next: next, stack: Math.max(0, total), due: due };
  }

  // Overlay one-liner: "Lv 52 37.5% | 4.1 %/h | ETA 15h12m | HOT 1h03m +50%".
  function levelingLine(body, fetchedMs, now) {
    const d = normalizeLeveling(body);
    if (!d || d.level === null || d.pct === null) return 'no XP sample yet';
    const el = sinceFetch(fetchedMs, now);
    const parts = ['Lv ' + d.level + ' ' + (Math.floor(d.pct * 10) / 10).toFixed(1) + '%'];
    parts.push(d.rate_pct_h === null ? '- %/h' : fmtRate(d.rate_pct_h));
    parts.push('ETA ' + (d.eta_next_s === null ? '-' : fmtEta(d.eta_next_s - el)));
    const h = hotLive(d.hot, d.xp_stack_pct, fetchedMs, now);
    if (h.active.length) {
      const ends = Math.min.apply(null, h.active.map(function (a) { return a.ends_in_s; }));
      parts.push('HOT ' + fmtEta(ends) + ' +' + h.stack + '%');
    } else {
      if (h.next) parts.push('HOT in ' + fmtEta(h.next.starts_in_s));
      if (h.stack > 0) parts.push('XP +' + h.stack + '%');
    }
    return parts.join(' | ');
  }

  // ---- World bosses (plan 032) ----
  // Formatters over the GET /api/bosses body (plan 031): {next: [{bosses,
  // at_utc, day, despawn_min}], today: {day, remaining, slots}, looted:
  // {<PT day>: [names]}, garmoth: {looted, cap}}. Countdowns run from at_utc,
  // so they stay right between polls; nothing comes from the game client.

  const BOSS_GARMOTH = 'Garmoth';
  const BOSS_NAME_MAX = 40;
  const BOSS_SOON_MIN = [5, 15];

  function bossGarmoth(view) {
    const g = plainObject(view) && plainObject(view.garmoth) ? view.garmoth : null;
    return g && isNum(g.looted) && isNum(g.cap) ? g : null;
  }

  function bossLooted(view, day, name) {
    const l = plainObject(view) && plainObject(view.looted) ? view.looted[day] : null;
    return Array.isArray(l) && l.indexOf(name) >= 0;
  }

  // One spawn -> {key, day, at_ms, left_s, left, names: [{name, label, looted}],
  // text, done}; null for junk. Greyed (looted) = ticked on the spawn's PT day,
  // or Garmoth at its weekly cap; done = every name greyed.
  function fmtBossRow(spawn, view, now) {
    if (!plainObject(spawn) || !Array.isArray(spawn.bosses) || !spawn.bosses.length ||
      !spawn.bosses.every(function (b) { return typeof b === 'string' && b; }) ||
      typeof spawn.at_utc !== 'string' || !ISO_TS.test(spawn.at_utc)) return null;
    const at = Date.parse(spawn.at_utc);
    if (!isFinite(at)) return null;
    const day = typeof spawn.day === 'string' ? spawn.day : '';
    const g = bossGarmoth(view);
    const names = spawn.bosses.map(function (b) {
      const gar = b === BOSS_GARMOTH && g;
      return { name: b, label: gar ? b + ' ' + g.looted + '/' + g.cap : b,
        looted: bossLooted(view, day, b) || !!(gar && g.looted >= g.cap) };
    });
    const leftMs = at - now;
    return { key: spawn.at_utc, day: day, at_ms: at, left_s: Math.floor(leftMs / 1000), left: fmtDuration(leftMs),
      names: names, text: names.map(function (n) { return n.label; }).join(' + '),
      done: names.every(function (n) { return n.looted; }) };
  }

  // The next n spawns still ahead of `now`: once one passes, the following one
  // leads (the server list refreshes on the next poll).
  function bossRows(view, now, n) {
    const next = plainObject(view) && Array.isArray(view.next) ? view.next : [];
    return next.map(function (s) { return fmtBossRow(s, view, now); })
      .filter(function (r) { return r && r.at_ms > now; })
      .sort(function (a, b) { return a.at_ms - b.at_ms; })
      .slice(0, n);
  }

  // Tick targets: each boss that has already spawned today (PT), once, in
  // spawn order. today.slots (plan 032) keeps despawned ones; an older server
  // only has `remaining`, of which the `up` rows count.
  function bossTicks(view, now) {
    const t = plainObject(view) && plainObject(view.today) ? view.today : null;
    if (!t) return [];
    const src = Array.isArray(t.slots) ? t.slots : (Array.isArray(t.remaining) ? t.remaining : []);
    const seen = {};
    const out = [];
    src.forEach(function (s) {
      const at = plainObject(s) && typeof s.at_utc === 'string' ? Date.parse(s.at_utc) : NaN;
      const spawned = Array.isArray(t.slots) ? isFinite(at) && at <= now : s && s.up === true;
      if (!spawned || !Array.isArray(s.bosses) || typeof s.day !== 'string') return;
      s.bosses.forEach(function (b) {
        if (!validAscii(b, BOSS_NAME_MAX) || seen[b]) return;
        seen[b] = true;
        out.push({ name: b, day: s.day, looted: bossLooted(view, s.day, b) });
      });
    });
    return out;
  }

  function bossGarmothText(view) {
    const g = bossGarmoth(view);
    return g ? BOSS_GARMOTH + ' ' + g.looted + '/' + g.cap + ' this week' : '';
  }

  // POST /api/bosses body: exactly {tick|untick: {boss, day}}.
  function validBossesBody(body) {
    if (!plainObject(body)) return false;
    const keys = Object.keys(body);
    if (keys.length !== 1 || (keys[0] !== 'tick' && keys[0] !== 'untick')) return false;
    const v = body[keys[0]];
    if (!exact(v, ['boss', 'day']) || !validAscii(v.boss, BOSS_NAME_MAX)) return false;
    const m = typeof v.day === 'string' ? ISO_DAY.exec(v.day) : null;
    return !!m && realDate(m[1], m[2], m[3]);
  }

  // Notify rule bossSoon: one hit at 15 min and one at 5 min before each
  // spawn that is not already all looted. A state rule (fires on a baseline);
  // the key carries the threshold so the ledger lets each fire once.
  function bossHits(prev, next, now) {
    return bossRows(next.bosses, now, 3).filter(function (r) { return !r.done; }).map(function (r) {
      const mins = BOSS_SOON_MIN.filter(function (m) { return r.at_ms - now <= m * 60000; })[0];
      if (mins === undefined) return null;
      return hit('bossSoon:' + r.key + ':' + mins, 'World boss in ' + mins + 'm: ' + r.text,
        r.text + ' spawns in ' + r.left);
    }).filter(Boolean);
  }

  // ---- Home / Now (plan 025) ----
  // One glance screen composed from the existing GET payloads. snapshots:
  // {today, grind, leveling, progress, events, market (GET /api/market/watch),
  // at: {<key>: fetchedMs}}; a missing or malformed payload (404 on an old
  // server) drops its card, never the screen. Read-only; the input is never
  // mutated. Cards: {id, title, tab, meta, rows: [{label, value, note, cls,
  // tick}], empty}; `tick` is a /api/today item id (the one Home write).

  const BUFF_WARN_S = 300;

  function nowCard(id, title, tab, rows, empty) {
    return { id: id, title: title, tab: tab, meta: '', rows: rows, empty: rows.length ? null : empty };
  }

  function nowRow(label, value, note, cls) {
    return { label: label, value: value || '', note: note || '', cls: cls || '', tick: null };
  }

  // Daily + weekly resets, then each distinct custom item rule, soonest first.
  function nowResets(today, now) {
    const rows = [nowRow('Daily reset', fmtDuration(nextDailyReset(now) - now)),
      nowRow('Weekly reset', fmtDuration(nextWeeklyReset(now) - now))];
    const rules = {};
    (today ? today.items : []).forEach(function (it) {
      const rule = plainObject(it) && validTitle(it.title) ? itemRule(it.kind, it.reset) : null;
      if (!rule) return;
      const k = fmtResetRule(rule);
      if (!rules[k]) rules[k] = { next: nextResetOf(rule, now), titles: [] };
      rules[k].titles.push(it.title);
    });
    Object.keys(rules).map(function (k) { return [k, rules[k]]; })
      .sort(function (a, b) { return a[1].next - b[1].next || (a[0] < b[0] ? -1 : 1); })
      .forEach(function (p) { rows.push(nowRow(p[1].titles.join(', '), fmtDuration(p[1].next - now), p[0])); });
    return nowCard('resets', 'Next resets', 'today', rows, '');
  }

  function nowDailies(today, now) {
    const g = groupItems(today.items, now).daily;
    const rows = g.items.filter(function (it) { return !it.done; }).map(function (it) {
      const r = nowRow(validTitle(it.title) ? it.title : it.id, '');
      r.tick = it.id;
      return r;
    });
    const c = nowCard('dailies', 'Dailies left', 'today', rows,
      g.total ? 'all ' + g.total + ' dailies done' : 'no dailies - add them on Today');
    c.meta = g.done + '/' + g.total + ' done';
    return c;
  }

  function nowBuffs(grind, at, now) {
    const rows = buffsLive(grind.buffs, at, now).filter(function (b) { return typeof b.name === 'string'; })
      .map(function (b) {
        return nowRow(b.name, fmtDuration(b.left_s * 1000), '', b.left_s <= BUFF_WARN_S ? 'warn' : '');
      });
    return nowCard('buffs', 'Buffs', 'grind', rows, 'no buffs running');
  }

  function nowSession(grind, at, now) {
    const el = liveElapsed(grind.active, at, now);
    const rows = [];
    if (el !== null) {
      const ref = grind.active.spot;
      rows.push(nowRow(spotName(grind.spots, ref), fmtElapsed(el)));
      const sp = (Array.isArray(grind.spots) ? grind.spots : []).filter(function (s) {
        return plainObject(s) && s.id === ref;
      })[0];
      const sph = sp && isNum(sp.silver_per_h) ? fmtSilver(sp.silver_per_h) + '/h' : '-';
      rows.push(nowRow('avg silver here', sph));
    }
    return nowCard('session', 'Grind session', 'grind', rows, 'no session running');
  }

  function nowLeveling(d, at, now) {
    const el = sinceFetch(at, now);
    const rows = [];
    if (d.level !== null && d.pct !== null) {
      rows.push(nowRow('Lv ' + d.level + ' ' + (Math.floor(d.pct * 10) / 10).toFixed(1) + '%',
        'ETA ' + (d.eta_next_s === null ? '-' : fmtEta(d.eta_next_s - el)),
        d.rate_pct_h === null ? '' : fmtRate(d.rate_pct_h)));
    }
    const h = hotLive(d.hot, d.xp_stack_pct, at, now);
    if (h.active.length) {
      const ends = Math.min.apply(null, h.active.map(function (a) { return a.ends_in_s; }));
      rows.push(nowRow('Hot Time +' + h.stack + '%', 'ends ' + fmtEta(ends), '', 'ok'));
    } else if (h.next) {
      rows.push(nowRow('Next Hot Time' + (isNum(h.next.pct) ? ' +' + h.next.pct + '%' : ''),
        'in ' + fmtEta(h.next.starts_in_s)));
    }
    const dl = deadlineAlert(d.deadlines);  // plan 024: only a tight or late deadline
    if (dl) rows.push(nowRow('Deadline', dl.text, '', dl.cls));
    return nowCard('leveling', 'Level ETA', 'progress', rows, 'no XP sample yet');
  }

  function nowAlerts(market) {
    const rows = [];
    market.items.forEach(function (it) {
      if (!plainObject(it)) return;
      const hit = alertFor(it.price, it.below, it.above);
      if (!hit) return;
      const name = typeof it.name === 'string' && it.name ? it.name : '#' + it.id;
      rows.push(nowRow(name, fmtSilver(it.price), hit + ' ' + fmtSilver(hit === 'below' ? it.below : it.above), 'ok'));
    });
    return nowCard('alerts', 'Market alerts', 'market', rows,
      market.items.length ? 'no alert hits' : 'watchlist empty');
  }

  function nowEnding(events, at, now) {
    const rows = eventRows(events.items, at, now).filter(function (r) { return r.soon; }).map(function (r) {
      return nowRow(r.title, fmtLeft(r.left_s), typeof r.code === 'string' ? r.code : '', 'warn');
    });
    return nowCard('ending', 'Ending within 48 h', 'events', rows, 'nothing ends within 48 h');
  }

  function nowCoupons(events) {
    const rows = suggestedRows(events.suggested, events.items).map(function (c) {
      return nowRow(c.code, c.date || '', c.title);
    });
    return nowCard('coupons', 'New coupons', 'events', rows, 'no new coupons');
  }

  // Plan 032: next 3 world bosses, read-only here (ticks live on Today).
  function nowBosses(view, now) {
    const rows = bossRows(view, now, 3).map(function (r) {
      return nowRow(r.text, r.left, '', r.done ? 'ew-stale' : '');
    });
    const c = nowCard('bosses', 'World bosses', 'today', rows, 'no spawns listed');
    c.meta = bossGarmothText(view);
    return c;
  }

  function composeNow(snapshots, nowMs) {
    const s = plainObject(snapshots) ? snapshots : {};
    const at = function (k) { return plainObject(s.at) && isNum(s.at[k]) ? s.at[k] : nowMs; };
    const has = function (k, list) { return plainObject(s[k]) && (!list || Array.isArray(s[k][list])); };
    const today = has('today', 'items') ? s.today : null;
    const cards = [nowResets(today, nowMs)];
    if (today) cards.push(nowDailies(today, nowMs));
    if (has('grind')) {
      cards.push(nowBuffs(s.grind, at('grind'), nowMs));
      cards.push(nowSession(s.grind, at('grind'), nowMs));
    }
    const lv = has('leveling') ? normalizeLeveling(s.leveling) : null;
    if (lv) cards.push(nowLeveling(lv, at('leveling'), nowMs));
    if (has('market', 'items')) cards.push(nowAlerts(s.market));
    if (has('events', 'items')) {
      cards.push(nowEnding(s.events, at('events'), nowMs));
      if (plainObject(s.events.suggested)) cards.push(nowCoupons(s.events));
    }
    if (has('bosses', 'next')) cards.push(nowBosses(s.bosses, nowMs));
    return cards;
  }

  // Plan 025 M8: open Events-tab items that end before the next weekly reset,
  // soonest first (the Today tab's read-only Events card).
  function eventsThisWeek(items, fetchedMs, now) {
    const limit = Math.floor((nextWeeklyReset(now) - now) / 1000);
    return eventRows(items, fetchedMs, now).filter(function (r) {
      return (r.status === 'active' || r.status === 'upcoming') && r.left_s !== null && r.left_s <= limit;
    });
  }

  // Overlay widgets (spec section 3). Main reads config.overlay.widgets and
  // hands the overlay a query string. Default on (only a literal false turns
  // one off), except the WIDGETS_OPT_IN ones: default off, only a literal true
  // turns them on (plan 011 leveling, plan 013 season, plan 029 marketTicker,
  // plan 032 worldBoss).
  const WIDGETS = ['grindSession', 'grindBuff', 'eventsSoon', 'leveling', 'season', 'marketTicker', 'worldBoss'];
  const WIDGETS_OPT_IN = ['leveling', 'season', 'marketTicker', 'worldBoss'];

  function optIn(k) { return WIDGETS_OPT_IN.indexOf(k) >= 0; }

  function overlayWidgets(config) {
    const w = config && plainObject(config.overlay) && plainObject(config.overlay.widgets) ? config.overlay.widgets : {};
    const out = {};
    WIDGETS.forEach(function (k) { out[k] = optIn(k) ? w[k] === true : w[k] !== false; });
    return out;
  }

  function widgetsQuery(widgets) {
    const out = {};
    WIDGETS.forEach(function (k) {
      if (optIn(k)) out[k] = widgets && widgets[k] === true ? '1' : '0';
      else out[k] = widgets && widgets[k] === false ? '0' : '1';
    });
    return out;
  }

  function widgetsFromQuery(search) {
    const q = new URLSearchParams(typeof search === 'string' ? search : '');
    const out = {};
    WIDGETS.forEach(function (k) { out[k] = optIn(k) ? q.get(k) === '1' : q.get(k) !== '0'; });
    return out;
  }

  // Overlay placement and legibility (plan 022). Config overlay.anchor is a
  // named anchor or a work-area-relative {x, y}; display an index (null =
  // primary); scale 0.8-1.6; opacity 0.5-0.95. Default middle-left, off BDO's
  // top-right minimap and buff tray.
  const OVERLAY_ANCHORS = ['tl', 'tr', 'bl', 'br', 'ml', 'mr'];
  const OVERLAY_DEFAULT = { anchor: 'ml', display: null, scale: 1, opacity: 0.85 };
  const OVERLAY_SCALE = [0.8, 1.6];
  const OVERLAY_OPACITY = [0.5, 0.95];
  const OVERLAY_SIZE = { width: 340, height: 220 };
  const OVERLAY_HEIGHT = [60, 600];
  const MINIMAP_ZONE = { width: 360, height: 300 };

  function clampNum(v, range, dflt) {
    if (typeof v !== 'number' || !isFinite(v)) return dflt;
    return Math.min(range[1], Math.max(range[0], v));
  }

  function validAnchor(a) {
    if (typeof a === 'string') return OVERLAY_ANCHORS.indexOf(a) >= 0;
    return plainObject(a) && Object.keys(a).length === 2 &&
      Number.isInteger(a.x) && Number.isInteger(a.y);
  }

  function overlayConfig(config) {
    const o = config && plainObject(config.overlay) ? config.overlay : {};
    return {
      anchor: validAnchor(o.anchor) ? (typeof o.anchor === 'string' ? o.anchor : { x: o.anchor.x, y: o.anchor.y })
        : OVERLAY_DEFAULT.anchor,
      display: Number.isInteger(o.display) && o.display >= 0 ? o.display : OVERLAY_DEFAULT.display,
      scale: clampNum(o.scale, OVERLAY_SCALE, OVERLAY_DEFAULT.scale),
      opacity: clampNum(o.opacity, OVERLAY_OPACITY, OVERLAY_DEFAULT.opacity)
    };
  }

  function validRect(r) {
    return plainObject(r) && [r.x, r.y, r.width, r.height].every(isNum);
  }

  // {x, y, width, height} clamped inside the work area; null without one.
  function overlayBounds(workArea, anchor, size, margin) {
    const wa = workArea;
    if (!validRect(wa) || wa.width <= 0 || wa.height <= 0) return null;
    const a = validAnchor(anchor) ? anchor : OVERLAY_DEFAULT.anchor;
    const m = isNum(margin) && margin > 0 ? margin : 0;
    const sw = size && isNum(size.width) && size.width > 0 ? size.width : OVERLAY_SIZE.width;
    const sh = size && isNum(size.height) && size.height > 0 ? size.height : OVERLAY_SIZE.height;
    const w = Math.round(Math.min(sw, wa.width));
    const h = Math.round(Math.min(sh, wa.height));
    let x;
    let y;
    if (typeof a === 'string') {
      x = a[1] === 'l' ? wa.x + m : wa.x + wa.width - w - m;
      if (a[0] === 't') y = wa.y + m;
      else if (a[0] === 'b') y = wa.y + wa.height - h - m;
      else y = wa.y + Math.round((wa.height - h) / 2);
    } else {
      x = wa.x + a.x;
      y = wa.y + a.y;
    }
    x = Math.min(wa.x + wa.width - w, Math.max(wa.x, Math.round(x)));
    y = Math.min(wa.y + wa.height - h, Math.max(wa.y, Math.round(y)));
    return { x: x, y: y, width: w, height: h };
  }

  // BDO's minimap + buff tray: the top-right 360x300 of the work area.
  function minimapZone(workArea) {
    return {
      x: workArea.x + workArea.width - MINIMAP_ZONE.width, y: workArea.y,
      width: MINIMAP_ZONE.width, height: MINIMAP_ZONE.height
    };
  }

  function rectInside(inner, outer) {
    return validRect(inner) && validRect(outer) && inner.x >= outer.x && inner.y >= outer.y &&
      inner.x + inner.width <= outer.x + outer.width && inner.y + inner.height <= outer.y + outer.height;
  }

  function rectsIntersect(a, b) {
    return validRect(a) && validRect(b) && a.x < b.x + b.width && b.x < a.x + a.width &&
      a.y < b.y + b.height && b.y < a.y + a.height;
  }

  // Content height the overlay page reports (ew:overlay-size), clamped; null
  // for anything that is not a non-negative finite number. Rounded up so a
  // fractional last line is never clipped.
  function overlayHeight(px) {
    if (!isNum(px) || px < 0) return null;
    return Math.ceil(Math.min(OVERLAY_HEIGHT[1], Math.max(OVERLAY_HEIGHT[0], px)));
  }

  // Scale and opacity ride in the overlay query (the overlay page has no bridge).
  function overlayStyleQuery(oc) {
    const c = overlayConfig({ overlay: oc });
    return { scale: String(c.scale), opacity: String(c.opacity) };
  }

  function overlayStyleFromQuery(search) {
    const q = new URLSearchParams(typeof search === 'string' ? search : '');
    const num = function (k) {
      const s = q.get(k);
      return s !== null && /^\d+(\.\d+)?$/.test(s) ? Number(s) : null;
    };
    return {
      scale: clampNum(num('scale'), OVERLAY_SCALE, OVERLAY_DEFAULT.scale),
      opacity: clampNum(num('opacity'), OVERLAY_OPACITY, OVERLAY_DEFAULT.opacity)
    };
  }

  // Quiet rows (plan 022): a row whose value says nothing is hidden; offline
  // still shows (it is news).
  const OV_QUIET = ['', '-', '?', 'none', 'idle'];
  function ovQuiet(text) {
    return text === null || text === undefined || OV_QUIET.indexOf(String(text)) >= 0;
  }

  function ovServerRowHidden(text) {
    return text === 'ok' || ovQuiet(text);
  }

  // ---- Settings (plan 030) ----
  // Form model for the Settings tab. The server (server/ew/settings.py) is the
  // authority; these mirror its allowlist so the bridge refuses anything else.
  // Every key is a dotted config/local.json path; secrets and loop never appear.
  const THEMES = ['system', 'dark', 'light'];
  const UI_SCALE = [0.9, 1.3];
  const NOTIFY_RULES_PREFS = ['marketAlert', 'buffEnding', 'hotTime', 'resetPassed', 'newCoupon', 'gameExit', 'bossSoon'];
  const SETTINGS_GROUPS = [
    { id: 'overlay', title: 'Overlay', fields: WIDGETS.map(function (w) {
      return { key: 'overlay.widgets.' + w, label: 'Widget: ' + w, type: 'bool' };
    }).concat([
      { key: 'overlay.anchor', label: 'Anchor', type: 'anchor', options: OVERLAY_ANCHORS },
      { key: 'overlay.display', label: 'Display (blank = primary)', type: 'display' },
      { key: 'overlay.scale', label: 'Scale', type: 'number', min: OVERLAY_SCALE[0], max: OVERLAY_SCALE[1], step: 0.05 },
      { key: 'overlay.opacity', label: 'Opacity', type: 'number', min: OVERLAY_OPACITY[0], max: OVERLAY_OPACITY[1], step: 0.05 }
    ]) },
    { id: 'hotkeys', title: 'Hotkeys', fields: [
      { key: 'hotkeys.toggleOverlay', label: 'Toggle overlay', type: 'hotkey' },
      { key: 'hotkeys.showDashboard', label: 'Show dashboard', type: 'hotkey' }
    ] },
    { id: 'profile', title: 'Profile', fields: [
      { key: 'profile.family', label: 'Family name (blank = none)', type: 'family' }
    ] },
    { id: 'appearance', title: 'Appearance', fields: [
      { key: 'ui.theme', label: 'Theme', type: 'enum', options: THEMES },
      { key: 'ui.scale', label: 'Dashboard scale', type: 'number', min: UI_SCALE[0], max: UI_SCALE[1], step: 0.05 }
    ] },
    { id: 'notify', title: 'Notifications', fields: NOTIFY_RULES_PREFS.map(function (n) {
      return { key: 'notify.' + n, label: n, type: 'bool' };
    }).concat([{ key: 'coupons.check', label: 'Coupon suggestions', type: 'bool' }]) },
    { id: 'market', title: 'Market', fields: [
      { key: 'market.vp', label: 'Value Pack active', type: 'bool' },
      { key: 'market.fame_pct', label: 'Family fame bonus (0-1.5 %)', type: 'number', min: 0, max: 1.5, step: 0.05 }
    ] }
  ];
  const SETTINGS_FIELDS = {};
  SETTINGS_GROUPS.forEach(function (g) { g.fields.forEach(function (f) { SETTINGS_FIELDS[f.key] = f; }); });
  const SETTINGS_KEYS = Object.keys(SETTINGS_FIELDS);
  const FAMILY_RE = /^[A-Za-z0-9_]{2,16}$/;

  function settingValueOk(f, v) {
    switch (f.type) {
      case 'bool': return typeof v === 'boolean';
      case 'anchor': return validAnchor(v) && (typeof v === 'string' || (Math.abs(v.x) <= 100000 && Math.abs(v.y) <= 100000));
      case 'display': return v === null || (Number.isInteger(v) && v >= 0 && v <= 16);
      case 'number': return isNum(v) && v >= f.min && v <= f.max;
      case 'hotkey': return validAccelerator(v) && v.length <= 64;
      case 'family': return v === '' || (typeof v === 'string' && FAMILY_RE.test(v));
      case 'enum': return typeof v === 'string' && f.options.indexOf(v) >= 0;
      default: return false;
    }
  }

  // Bridge check for POST /api/settings: {set: {key: value}} over the allowlist.
  function validSettingsBody(body) {
    if (!plainObject(body) || Object.keys(body).length !== 1 || !plainObject(body.set)) return false;
    const keys = Object.keys(body.set);
    return keys.length >= 1 && keys.length <= 64 && keys.every(function (k) {
      return Object.prototype.hasOwnProperty.call(SETTINGS_FIELDS, k) && settingValueOk(SETTINGS_FIELDS[k], body.set[k]);
    });
  }

  // One raw form input -> {value} or {error}. Checkboxes pass a boolean;
  // anchors pass a named anchor or 'x,y'; everything else passes a string.
  function parseSettingInput(key, raw) {
    const f = SETTINGS_FIELDS[key];
    if (!f) return { error: key + ': unknown setting' };
    let v = raw;
    const s = typeof raw === 'string' ? raw.trim() : raw;
    if (f.type === 'number') v = typeof s === 'string' && /^-?\d+(\.\d+)?$/.test(s) ? Number(s) : NaN;
    else if (f.type === 'display') v = s === '' ? null : (typeof s === 'string' && /^\d+$/.test(s) ? Number(s) : NaN);
    else if (f.type === 'anchor' && typeof s === 'string' && s.indexOf(',') >= 0) {
      const m = /^(-?\d+)\s*,\s*(-?\d+)$/.exec(s);
      v = m ? { x: Number(m[1]), y: Number(m[2]) } : null;
    } else if (typeof s === 'string') v = s;
    if (!settingValueOk(f, v)) {
      let hint = 'invalid';
      if (f.type === 'number') hint = 'a number ' + f.min + '-' + f.max;
      else if (f.type === 'hotkey') hint = 'modifier(s) + key, e.g. Control+Alt+E';
      else if (f.type === 'family') hint = '2-16 letters, digits or _';
      else if (f.type === 'anchor') hint = OVERLAY_ANCHORS.join('|') + ' or x,y';
      else if (f.type === 'display') hint = 'blank or a display number 0-16';
      return { error: f.label + ': ' + hint };
    }
    return { value: v };
  }

  // Text shown in an input for a stored value.
  function settingInputText(key, v) {
    const f = SETTINGS_FIELDS[key];
    if (!f) return '';
    if (f.type === 'anchor' && plainObject(v)) return v.x + ',' + v.y;
    if (v === null || v === undefined) return '';
    return String(v);
  }

  function sameSetting(a, b) {
    if (plainObject(a) && plainObject(b)) return a.x === b.x && a.y === b.y;
    return a === b;
  }

  // Edited values that differ from the saved ones, as a POST body (or null).
  function settingsBody(saved, edits) {
    const set = {};
    Object.keys(edits || {}).forEach(function (k) {
      if (!SETTINGS_FIELDS[k]) return;
      if (!saved || !sameSetting(saved[k], edits[k])) set[k] = edits[k];
    });
    return Object.keys(set).length ? { set: set } : null;
  }

  // What the app must redo after a save, from the server's `changed` list.
  function settingsEffects(changed) {
    const ks = Array.isArray(changed) ? changed : [];
    const has = function (p) { return ks.some(function (k) { return typeof k === 'string' && k.indexOf(p) === 0; }); };
    return { overlay: has('overlay.'), shell: has('hotkeys.') || has('ui.scale'), theme: has('ui.theme') };
  }

  // data-theme for ui.theme; 'system' follows prefers-color-scheme.
  function themeAttr(theme, prefersDark) {
    if (theme === 'light' || theme === 'dark') return theme;
    if (theme === 'system') return prefersDark ? 'dark' : 'light';
    return 'dark';
  }

  // Dashboard zoom factor from config ui.scale (main process).
  function uiScale(config) {
    const u = config && plainObject(config.ui) ? config.ui.scale : undefined;
    return isNum(u) && u >= UI_SCALE[0] && u <= UI_SCALE[1] ? u : 1;
  }

  // The only routes the dashboard bridge forwards, each with its body check.
  const POST_VALIDATORS = {
    '/api/market/watch': validWatchBody, '/api/today': validTodayBody, '/api/progress': validProgressBody,
    '/api/grind': validGrindBody, '/api/events': validEventsBody, '/api/deadeye': validDeadeyeBody,
    '/api/ocr': validOcrBody, '/api/leveling': validLevelingBody, '/api/settings': validSettingsBody,
    '/api/bosses': validBossesBody
  };
  const POST_ROUTES = Object.keys(POST_VALIDATORS);

  function validPost(route, body) {
    return typeof route === 'string' && Object.prototype.hasOwnProperty.call(POST_VALIDATORS, route) &&
      POST_VALIDATORS[route](body);
  }

  // ---- Toasts + notifications (plan 026) ----
  // toastQueue: the dashboard's toast region model (max 4, newest last, a key
  // pushed again replaces its toast). notifyRules: pure rule engine over two
  // snapshots of the payloads the dashboard already polls:
  //   {at, market, grind, grindAt, leveling, levelingAt, events, today, game, bosses}
  // (raw GET bodies; *At = fetch time ms). A missing prev is a baseline: only
  // state rules (buffEnding) fire on it, transition rules wait for a second look.

  const TOAST_MAX = 4;
  const TOAST_TEXT_MAX = 160;
  const TOAST_MS = { ok: 4000, warn: 8000, bad: 10000 };
  const NOTIFY_TITLE_MAX = 64;
  const NOTIFY_BODY_MAX = 200;
  const NOTIFY_RATE = { max: 6, windowMs: 60000 };
  const BUFF_ENDING_S = 300;
  const NOTIFY_PRINTABLE = /^[\x20-\x7e]*$/;

  function toastQueue() {
    let list = [];
    let seq = 0;
    return {
      push: function (t, now) {
        if (!plainObject(t)) return;
        const level = Object.prototype.hasOwnProperty.call(TOAST_MS, t.level) ? t.level : 'warn';
        const key = typeof t.key === 'string' && t.key ? t.key : 'toast:' + (++seq);
        list = list.filter(function (x) { return x.key !== key; });
        list.push({ key: key, level: level, text: String(t.text === undefined ? '' : t.text).slice(0, TOAST_TEXT_MAX),
          until: now + TOAST_MS[level] });
        if (list.length > TOAST_MAX) list = list.slice(list.length - TOAST_MAX);
      },
      expire: function (now) {
        const n = list.length;
        list = list.filter(function (x) { return x.until > now; });
        return list.length !== n;
      },
      remove: function (key) { list = list.filter(function (x) { return x.key !== key; }); },
      items: function () { return list.map(function (x) { return Object.assign({}, x); }); }
    };
  }

  const POST_LABELS = {
    '/api/market/watch': 'Market watch', '/api/today': 'Today', '/api/progress': 'Progress',
    '/api/grind': 'Grind', '/api/events': 'Events', '/api/deadeye': 'Deadeye', '/api/ocr': 'OCR',
    '/api/leveling': 'Leveling', '/api/settings': 'Settings', '/api/bosses': 'World bosses'
  };

  // One POST result (the ew:post bridge reply) -> one toast.
  function postToast(route, res) {
    const label = POST_LABELS[route] || 'Save';
    const key = 'post:' + route;
    if (res && res.ok) return { key: key, level: 'ok', text: label + ' saved' };
    if (res && res.status === 404) return { key: key, level: 'warn', text: label + ': ' + notOnServer(route) };
    return { key: key, level: 'bad', text: label + ' failed: ' + ((res && res.error) || 'unknown error') };
  }

  // ASCII-printable, clipped: what the OS notification (and validNotify) accepts.
  function notifyText(s, max) {
    return String(s === undefined || s === null ? '' : s).replace(/[^\x20-\x7e]/g, '?').slice(0, max);
  }

  function hit(key, title, body) { return { key: key, title: title, body: body }; }

  function marketHits(prev, next) {
    if (!prev || !plainObject(prev.market) || !plainObject(next.market)) return [];
    const was = {};
    (Array.isArray(prev.market.items) ? prev.market.items : []).forEach(function (it) {
      if (plainObject(it)) was[it.id + ':' + (it.sid || 0)] = it.alert || null;
    });
    const out = [];
    (Array.isArray(next.market.items) ? next.market.items : []).forEach(function (it) {
      if (!plainObject(it) || (it.alert !== 'below' && it.alert !== 'above')) return;
      const k = it.id + ':' + (it.sid || 0);
      if (was[k] === it.alert) return;
      const name = typeof it.name === 'string' && it.name ? it.name : 'item ' + it.id;
      const limit = it.alert === 'below' ? 'at or below ' + fmtSilver(it.below) : 'at or above ' + fmtSilver(it.above);
      out.push(hit('marketAlert:' + k + ':' + it.alert, 'Market alert: ' + name,
        name + ' at ' + fmtSilver(it.price) + ', ' + limit));
    });
    return out;
  }

  function buffHits(prev, next, now) {
    if (!plainObject(next.grind)) return [];
    const at = isNum(next.grindAt) ? next.grindAt : next.at;
    return buffsLive(next.grind.buffs, at, now).filter(function (b) {
      return b.left_s <= BUFF_ENDING_S && typeof b.name === 'string';
    }).map(function (b) {
      // One key per arming: the server's ends, else the end minute.
      const end = typeof b.ends === 'string' ? b.ends : String(Math.round((now + b.left_s * 1000) / 60000));
      const id = b.id === undefined || b.id === null ? b.name : b.id;
      return hit('buffEnding:' + id + ':' + end, 'Buff ending: ' + b.name,
        b.name + ' ends in ' + fmtDuration(b.left_s * 1000));
    });
  }

  function hotActive(s, now) {
    if (!s || !plainObject(s.leveling)) return null;
    return hotLive(s.leveling.hot, 0, isNum(s.levelingAt) ? s.levelingAt : s.at, now).active;
  }

  function hotHits(prev, next, now) {
    const was = hotActive(prev, isNum(prev && prev.at) ? prev.at : now);
    const cur = hotActive(next, now);
    if (!was || !cur) return [];
    const ids = was.map(function (a) { return a.id; });
    return cur.filter(function (a) { return ids.indexOf(a.id) < 0; }).map(function (a) {
      const label = typeof a.label === 'string' && a.label ? a.label : 'Hot Time';
      return hit('hotTime:' + a.id + ':' + lastResetOf({ every: 'day', at: '00:00' }, now),
        'Hot Time started: ' + label,
        label + (isNum(a.pct) ? ' +' + a.pct + '% XP' : '') + ' for ' + fmtDuration(a.ends_in_s * 1000));
    });
  }

  function resetHits(prev, next, now) {
    if (!prev || !isNum(prev.at) || prev.at >= now) return [];
    const out = [];
    const passed = function (rule) {
      const last = lastResetOf(rule, now);
      return last > prev.at ? last : null;
    };
    const d = passed({ every: 'day', at: '00:00' });
    if (d !== null) out.push(hit('resetPassed:daily:' + d, 'Daily reset passed', 'Daily checklist is fresh'));
    const w = passed({ every: 'week', weekday: 3, at: '00:00' });
    if (w !== null) out.push(hit('resetPassed:weekly:' + w, 'Weekly reset passed', 'Weekly checklist is fresh'));
    const items = plainObject(next.today) && Array.isArray(next.today.items) ? next.today.items : [];
    items.forEach(function (it) {
      const rule = plainObject(it) ? itemRule(it.kind, it.reset) : null;
      const t = rule ? passed(rule) : null;
      if (t === null) return;
      const title = typeof it.title === 'string' && it.title ? it.title : String(it.id);
      out.push(hit('resetPassed:' + it.id + ':' + t, 'Reset: ' + title, title + ' is ready again'));
    });
    return out;
  }

  function couponHits(prev, next) {
    if (!prev || !plainObject(prev.events) || !plainObject(next.events)) return [];
    const was = suggestedRows(prev.events.suggested, prev.events.items).map(function (c) { return c.code; });
    return suggestedRows(next.events.suggested, next.events.items).filter(function (c) {
      return was.indexOf(c.code) < 0;
    }).map(function (c) {
      return hit('newCoupon:' + c.code, 'New coupon: ' + c.code, c.title + ' - add it in the Events tab');
    });
  }

  const GAME_UP = ['running', 'logged_in', 'disconnected'];

  function gameHits(prev, next, now) {
    const a = prev ? normalizeGame(prev.game) : null;
    const b = normalizeGame(next.game);
    if (!a || !b || GAME_UP.indexOf(a.state) < 0 || b.state !== 'not_running') return [];
    if (!plainObject(next.grind) || !plainObject(next.grind.active)) return [];
    return [hit('gameExit:' + (b.since === null ? now : b.since), 'Game exited',
      'A grind session is still running - stop it in the Grind tab')];
  }

  // The rule table. Names are stable (config notify.<name>); later plans push
  // {name, defaultOn, fire(prev, next, nowMs) -> [{key, title, body}]}.
  const NOTIFY_RULES = [
    { name: 'marketAlert', defaultOn: true, fire: marketHits },
    { name: 'buffEnding', defaultOn: true, fire: buffHits },
    { name: 'hotTime', defaultOn: false, fire: hotHits },
    { name: 'resetPassed', defaultOn: false, fire: resetHits },
    { name: 'newCoupon', defaultOn: false, fire: couponHits },
    { name: 'gameExit', defaultOn: false, fire: gameHits },
    { name: 'bossSoon', defaultOn: false, fire: bossHits }
  ];

  // config/local.json `notify` block -> {rule: bool}; non-booleans keep the default.
  function notifyPrefs(cfg) {
    const n = plainObject(cfg) && plainObject(cfg.notify) ? cfg.notify : {};
    const out = {};
    NOTIFY_RULES.forEach(function (r) {
      out[r.name] = typeof n[r.name] === 'boolean' ? n[r.name] : !!r.defaultOn;
    });
    return out;
  }

  // notify.silent (default true): OS notifications make no sound.
  function notifySilent(cfg) {
    const n = plainObject(cfg) && plainObject(cfg.notify) ? cfg.notify : {};
    return typeof n.silent === 'boolean' ? n.silent : true;
  }

  // Prefs ride to the sandboxed dashboard as a launch argument: the enabled names.
  function notifyArg(prefs) {
    return NOTIFY_RULES.filter(function (r) { return prefs && prefs[r.name] === true; })
      .map(function (r) { return r.name; }).join(',');
  }

  function notifyPrefsFromArg(arg) {
    if (typeof arg !== 'string') return notifyPrefs({});
    const on = arg.split(',');
    const out = {};
    NOTIFY_RULES.forEach(function (r) { out[r.name] = on.indexOf(r.name) >= 0; });
    return out;
  }

  function notifyRules(prev, next, nowMs, prefs) {
    if (!plainObject(next)) return [];
    const out = [];
    NOTIFY_RULES.forEach(function (r) {
      if (!prefs || prefs[r.name] !== true) return;
      let hits = [];
      try { hits = r.fire(prev || null, next, nowMs) || []; } catch (e) { hits = []; }
      hits.forEach(function (h) {
        if (!plainObject(h) || typeof h.key !== 'string' || !h.key) return;
        out.push({ key: h.key, rule: r.name, title: notifyText(h.title, NOTIFY_TITLE_MAX) || r.name,
          body: notifyText(h.body, NOTIFY_BODY_MAX) });
      });
    });
    return out;
  }

  // In-memory dedupe: each key fires once, the ledger empties at daily reset.
  function notifyLedger() {
    let seen = {};
    let day = null;
    return {
      take: function (hits, now) {
        const d = lastResetOf({ every: 'day', at: '00:00' }, now);
        if (d !== day) { seen = {}; day = d; }
        return (Array.isArray(hits) ? hits : []).filter(function (h) {
          if (!plainObject(h) || seen[h.key]) return false;
          seen[h.key] = true;
          return true;
        });
      }
    };
  }

  // ew:notify payload check (main process): exactly {title, body}.
  function validNotify(n) {
    if (!plainObject(n)) return false;
    const keys = Object.keys(n).sort();
    if (keys.length !== 2 || keys[0] !== 'body' || keys[1] !== 'title') return false;
    return typeof n.title === 'string' && n.title.length >= 1 && n.title.length <= NOTIFY_TITLE_MAX &&
      typeof n.body === 'string' && n.body.length <= NOTIFY_BODY_MAX &&
      NOTIFY_PRINTABLE.test(n.title) && NOTIFY_PRINTABLE.test(n.body);
  }

  // Sliding-window limiter: allow(now) is true at most `max` times per window.
  function rateLimiter(max, windowMs) {
    let stamps = [];
    return {
      allow: function (now) {
        stamps = stamps.filter(function (t) { return now - t < windowMs; });
        if (stamps.length >= max) return false;
        stamps.push(now);
        return true;
      }
    };
  }

  const api = {
    TOAST_MAX: TOAST_MAX,
    TOAST_TEXT_MAX: TOAST_TEXT_MAX,
    TOAST_MS: TOAST_MS,
    NOTIFY_TITLE_MAX: NOTIFY_TITLE_MAX,
    NOTIFY_BODY_MAX: NOTIFY_BODY_MAX,
    NOTIFY_RATE: NOTIFY_RATE,
    NOTIFY_RULES: NOTIFY_RULES,
    toastQueue: toastQueue,
    postToast: postToast,
    notifyPrefs: notifyPrefs,
    notifySilent: notifySilent,
    notifyArg: notifyArg,
    notifyPrefsFromArg: notifyPrefsFromArg,
    notifyRules: notifyRules,
    fmtBossRow: fmtBossRow,
    bossRows: bossRows,
    bossTicks: bossTicks,
    bossGarmothText: bossGarmothText,
    validBossesBody: validBossesBody,
    notifyLedger: notifyLedger,
    validNotify: validNotify,
    rateLimiter: rateLimiter,
    SERVER: SERVER,
    GAME_STATES: GAME_STATES,
    GAME_SHOTS_MAX: GAME_SHOTS_MAX,
    GAME_CONFIG_HINT: GAME_CONFIG_HINT,
    gameStateLabel: gameStateLabel,
    gameTimeMs: gameTimeMs,
    gameEventText: gameEventText,
    normalizeGame: normalizeGame,
    fmtClock: fmtClock,
    gameSinceText: gameSinceText,
    OCR_NAME_MAX: OCR_NAME_MAX,
    OCR_TEXT_MAX: OCR_TEXT_MAX,
    validOcrBody: validOcrBody,
    normalizeOcr: normalizeOcr,
    ocrBuffBody: ocrBuffBody,
    ocrSilverInput: ocrSilverInput,
    ocrBuffLabel: ocrBuffLabel,
    lastDailyReset: lastDailyReset,
    lastWeeklyReset: lastWeeklyReset,
    validResetRule: validResetRule,
    itemRule: itemRule,
    lastResetOf: lastResetOf,
    nextResetOf: nextResetOf,
    fmtResetRule: fmtResetRule,
    fmtResetCountdown: fmtResetCountdown,
    fmtWeeklyGate: fmtWeeklyGate,
    weeklyPlan: weeklyPlan,
    resetKey: resetKey,
    resetPresets: resetPresets,
    presetForm: presetForm,
    isDone: isDone,
    eventDaysLeft: eventDaysLeft,
    fmtDaysLeft: fmtDaysLeft,
    groupItems: groupItems,
    withTick: withTick,
    validTodayBody: validTodayBody,
    parseTodayForm: parseTodayForm,
    gsTotal: gsTotal,
    bracketLines: bracketLines,
    trackPct: trackPct,
    seedOptions: seedOptions,
    gateChips: gateChips,
    withStep: withStep,
    OBJ_KINDS: OBJ_KINDS,
    fmtTarget: fmtTarget,
    seasonGroups: seasonGroups,
    seasonLine: seasonLine,
    parseObjectiveForm: parseObjectiveForm,
    targetInput: targetInput,
    validProgressBody: validProgressBody,
    parseCharacterForm: parseCharacterForm,
    parseTrackForm: parseTrackForm,
    profilePill: profilePill,
    profileRows: profileRows,
    BUFF_DEFAULTS: BUFF_DEFAULTS,
    BUFF_MINUTES: BUFF_MINUTES,
    silverPerHour: silverPerHour,
    fmtElapsed: fmtElapsed,
    liveElapsed: liveElapsed,
    buffsLive: buffsLive,
    soonestBuff: soonestBuff,
    buffRows: buffRows,
    xpPresets: xpPresets,
    sortSpots: sortSpots,
    spotName: spotName,
    validGrindBody: validGrindBody,
    dropView: dropView,
    agrisLine: agrisLine,
    parseGrindForm: parseGrindForm,
    lootHintText: lootHintText,
    sessionSilver: sessionSilver,
    trashPileText: trashPileText,
    SPOT_GOALS: SPOT_GOALS,
    spotsPath: spotsPath,
    spotNeedText: spotNeedText,
    spotGapText: spotGapText,
    spotRecs: spotRecs,
    matchSpot: matchSpot,
    EVENT_KINDS: EVENT_KINDS,
    EVENTS_SOON_S: EVENTS_SOON_S,
    fmtLeft: fmtLeft,
    eventRows: eventRows,
    soonestEvent: soonestEvent,
    localToUtcIso: localToUtcIso,
    validEventsBody: validEventsBody,
    parseEventForm: parseEventForm,
    suggestedRows: suggestedRows,
    suggestAddBody: suggestAddBody,
    suggestStatus: suggestStatus,
    DEADEYE_LEVELS: DEADEYE_LEVELS,
    DEADEYE_SECTIONS: DEADEYE_SECTIONS,
    NOTE_MAX: NOTE_MAX,
    levelIndex: levelIndex,
    renderMarkdown: renderMarkdown,
    validDeadeyeBody: validDeadeyeBody,
    parseStepForm: parseStepForm,
    ENHANCE_MAX_FS: ENHANCE_MAX_FS,
    parseFs: parseFs,
    enhanceSubSteps: enhanceSubSteps,
    enhanceFamilyGuess: enhanceFamilyGuess,
    enhancePath: enhancePath,
    fmtEv: fmtEv,
    evLine: evLine,
    FS_KINDS: FS_KINDS,
    ENHANCE_STEPS: ENHANCE_STEPS,
    parseFsForm: parseFsForm,
    parseAgrisForm: parseAgrisForm,
    parseCronsForm: parseCronsForm,
    fmtStacks: fmtStacks,
    parseShopForm: parseShopForm,
    fmtShopLine: fmtShopLine,
    fmtShopping: fmtShopping,
    shopWatchBody: shopWatchBody,
    validLevelingBody: validLevelingBody,
    parseSampleForm: parseSampleForm,
    parseHotForm: parseHotForm,
    parseMilestones: parseMilestones,
    parseEpochForm: parseEpochForm,
    epochText: epochText,
    LEVEL_MAX: LEVEL_MAX,
    fmtRate: fmtRate,
    fmtEta: fmtEta,
    fmtDays: fmtDays,
    DAY_NAMES: DAY_NAMES,
    normalizeLeveling: normalizeLeveling,
    hotLive: hotLive,
    levelingLine: levelingLine,
    DEADLINE_STATES: DEADLINE_STATES,
    deadlineBrief: deadlineBrief,
    fmtMonthDay: fmtMonthDay,
    deadlinePill: deadlinePill,
    deadlineLine: deadlineLine,
    deadlineAlert: deadlineAlert,
    composeNow: composeNow,
    eventsThisWeek: eventsThisWeek,
    overlayWidgets: overlayWidgets,
    widgetsQuery: widgetsQuery,
    widgetsFromQuery: widgetsFromQuery,
    OVERLAY_ANCHORS: OVERLAY_ANCHORS,
    OVERLAY_SIZE: OVERLAY_SIZE,
    overlayConfig: overlayConfig,
    overlayBounds: overlayBounds,
    minimapZone: minimapZone,
    rectInside: rectInside,
    rectsIntersect: rectsIntersect,
    overlayHeight: overlayHeight,
    overlayStyleQuery: overlayStyleQuery,
    overlayStyleFromQuery: overlayStyleFromQuery,
    ovQuiet: ovQuiet,
    ovServerRowHidden: ovServerRowHidden,
    validPost: validPost,
    POST_ROUTES: POST_ROUTES,
    THEMES: THEMES,
    SETTINGS_GROUPS: SETTINGS_GROUPS,
    SETTINGS_KEYS: SETTINGS_KEYS,
    validSettingsBody: validSettingsBody,
    parseSettingInput: parseSettingInput,
    settingInputText: settingInputText,
    settingsBody: settingsBody,
    settingsEffects: settingsEffects,
    themeAttr: themeAttr,
    uiScale: uiScale,
    fmtSilver: fmtSilver,
    fmtSilverExact: fmtSilverExact,
    SEARCH_DEBOUNCE_MS: SEARCH_DEBOUNCE_MS,
    searchQuery: searchQuery,
    searchPath: searchPath,
    searchRows: searchRows,
    sparkPath: sparkPath,
    historyStats: historyStats,
    alertFor: alertFor,
    depthBars: depthBars,
    fmtAge: fmtAge,
    marketPill: marketPill,
    itemFreshness: itemFreshness,
    validWatchBody: validWatchBody,
    parseWatchForm: parseWatchForm,
    pollDue: pollDue,
    netProceeds: netProceeds,
    parseSilver: parseSilver,
    pairProfit: pairProfit,
    preorderState: preorderState,
    preorderBadge: preorderBadge,
    tickerRows: tickerRows,
    tickerTrack: tickerTrack,
    nextDailyReset: nextDailyReset,
    nextWeeklyReset: nextWeeklyReset,
    fmtDuration: fmtDuration,
    freshness: freshness,
    normalizeTabs: normalizeTabs,
    backoffMs: backoffMs,
    HEALTH_DEAD_MS: HEALTH_DEAD_MS,
    VERSION_POLL_MS: VERSION_POLL_MS,
    commitsDiffer: commitsDiffer,
    healthPill: healthPill,
    notOnServer: notOnServer,
    sourceFreshness: sourceFreshness,
    serverRows: serverRows,
    validAccelerator: validAccelerator,
    hotkeys: hotkeys,
    DEFAULT_HOTKEYS: DEFAULT_HOTKEYS
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.EWCore = api;
})(typeof self !== 'undefined' ? self : this);
