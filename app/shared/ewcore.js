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
    if (k === 'tick' || k === 'untick' || k === 'remove') return typeof v === 'string' && SLUG.test(v);
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
  const STEPS_MAX = 60;              // server MAX_STEPS
  const LEVEL_MAX = 75;              // server levels.LEVEL_MAX (plan 018)
  const LEVEL = [1, LEVEL_MAX];
  const STAT = [0, 999];

  function gsTotal(ap, aap, dp) {
    if (!isNum(ap) || !isNum(aap) || !isNum(dp)) return null;
    return (ap + aap) / 2 + dp;
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
    if (k === 'stop') return exact(v, ['silver', 'trash']) && validLoot(v);
    if (k === 'log') {
      return exact(v, ['spot', 'minutes', 'silver', 'trash']) && validName(v.spot) &&
        inRange(v.minutes, MINUTES) && validLoot(v);
    }
    if (k === 'buff') {
      // xp_pct is optional (plan 011): absent = not counted in the XP stack.
      return (exact(v, ['name', 'minutes']) || (exact(v, ['name', 'minutes', 'xp_pct']) && inRange(v.xp_pct, XP_PCT))) &&
        validName(v.name) && inRange(v.minutes, BUFF_MINUTES);
    }
    return false;
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
    if (kind === 'stop') {
      const l = loot();
      return l.error ? { ok: false, error: l.error } : { ok: true, body: { stop: l } };
    }
    if (kind === 'log') {
      if (!validName(f.spot)) return { ok: false, error: 'pick a spot' };
      const m = minutes(MINUTES);
      if (m === null) return { ok: false, error: minErr(MINUTES) };
      const l = loot();
      if (l.error) return { ok: false, error: l.error };
      return { ok: true, body: { log: { spot: f.spot, minutes: m, silver: l.silver, trash: l.trash } } };
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
    if (k === 'add_step') {
      return plainObject(v) && onlyKeys(v, ['item', 'current', 'target', 'note']) &&
        'item' in v && 'current' in v && 'target' in v && stepFieldsOk(v);
    }
    if (k === 'edit_step') {
      return plainObject(v) && onlyKeys(v, ['id', 'item', 'current', 'target', 'note']) &&
        validRef(v.id, STEP_ID_RE) && Object.keys(v).length >= 2 && stepFieldsOk(v);
    }
    return false;
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

  function validAscii(t, max) {
    return typeof t === 'string' && t.trim().length > 0 && t.length <= max && /^[\x20-\x7e]*$/.test(t);
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
      samples: (Array.isArray(d.samples) ? d.samples : []).filter(plainObject)
    };
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

  // Overlay widgets (spec section 3). Main reads config.overlay.widgets and
  // hands the overlay a query string. Default on (only a literal false turns
  // one off), except the WIDGETS_OPT_IN ones: default off, only a literal true
  // turns them on (plan 011 leveling, plan 013 season).
  const WIDGETS = ['grindSession', 'grindBuff', 'eventsSoon', 'leveling', 'season'];
  const WIDGETS_OPT_IN = ['leveling', 'season'];

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

  // The only routes the dashboard bridge forwards, each with its body check.
  const POST_VALIDATORS = {
    '/api/market/watch': validWatchBody, '/api/today': validTodayBody, '/api/progress': validProgressBody,
    '/api/grind': validGrindBody, '/api/events': validEventsBody, '/api/deadeye': validDeadeyeBody,
    '/api/ocr': validOcrBody, '/api/leveling': validLevelingBody
  };
  const POST_ROUTES = Object.keys(POST_VALIDATORS);

  function validPost(route, body) {
    return typeof route === 'string' && Object.prototype.hasOwnProperty.call(POST_VALIDATORS, route) &&
      POST_VALIDATORS[route](body);
  }

  const api = {
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
    trackPct: trackPct,
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
    parseGrindForm: parseGrindForm,
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
    fmtSilver: fmtSilver,
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
