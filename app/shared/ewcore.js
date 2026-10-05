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

  function isDone(tickIso, kind, now) {
    if (typeof tickIso !== 'string' || !tickIso) return false;
    const t = Date.parse(tickIso);
    if (!isFinite(t)) return false;
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
      if (now !== undefined) x.done = isDone(it.ticked_at, it.kind, now);
      else x.done = !!it.done;
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
      return plainObject(v) && onlyKeys(v, ['title', 'kind', 'until']) && validTitle(v.title) &&
        KINDS.indexOf(v.kind) >= 0 &&
        (v.until === undefined || v.until === null || isoDateMs(v.until) !== null);
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
    return { ok: true, body: { add: add } };
  }

  // ---- Progress (plan 004) ----
  // A step is done when it carries a done_at stamp; counts are re-derived on
  // the client so an optimistic toggle updates the bar before the server answers.

  const TRACK_KINDS = ['quest', 'season', 'gear'];
  const ID = /^[a-z0-9_-]{1,40}$/;
  const STEPS_MAX = 200;
  const LEVEL = [1, 70];
  const STAT = [0, 999];

  function gsTotal(ap, aap, dp) {
    if (!isNum(ap) || !isNum(aap) || !isNum(dp)) return null;
    return (ap + aap) / 2 + dp;
  }

  // Whole percent, rounded but capped at 99 so an unfinished track never reads 100.
  function trackPct(track) {
    let done = 0;
    let total = 0;
    const steps = track && Array.isArray(track.steps) ? track.steps : [];
    steps.forEach(function (s) {
      if (!plainObject(s)) return;
      total += 1;
      if (s.done_at) done += 1;
    });
    const pct = total ? (done === total ? 100 : Math.min(99, Math.round(100 * done / total))) : 0;
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
      return Object.assign(nt, trackPct(nt));
    });
    return copy;
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

  // /api/progress `profile`: null or status "none" = no family configured.
  function profilePill(profile) {
    if (!plainObject(profile) || profile.status === 'none') {
      return { cls: 'unknown', label: 'no profile', stale: true, error: null, none: true };
    }
    const p = sourcePill(profile.freshness, 'profile', 3600);
    p.none = false;
    return p;
  }

  function plainText(v) {
    return (typeof v === 'string' && v.trim() && v.length <= TITLE_MAX) || isNum(v) ? String(v) : null;
  }

  // BDO-REST-API adventurer profile -> [label, value] rows from known keys only.
  function profileRows(data) {
    if (!plainObject(data)) return [];
    const rows = [];
    const push = function (label, v) { const t = plainText(v); if (t !== null) rows.push([label, t]); };
    push('Family', data.familyName);
    push('Region', data.region);
    if (plainObject(data.guild)) push('Guild', data.guild.name);
    const chars = Array.isArray(data.characters) ? data.characters.filter(plainObject) : [];
    const main = chars.filter(function (c) { return c.main; })[0] || chars[0];
    if (main && plainText(main.name)) {
      const bits = [plainText(main.class), plainText(main.level)].filter(Boolean).join(' ');
      push('Main', main.name + (bits ? ' - ' + bits : ''));
    }
    if (chars.length) push('Characters', chars.length);
    push('Contribution', data.contributionPoints);
    return rows;
  }

  // ---- Grind (plan 005) ----
  // Elapsed clocks and buff countdowns run locally between polls: from the
  // absolute stamps when parseable, else from the server's seconds minus the
  // time since that fetch.

  const NAME_MAX = 60;
  const MINUTES = [1, 1440];
  const SILVER = [0, 1e13];
  const TRASH = [0, 1e6];

  // Operator-editable defaults; minutes capped by the server's 1440 limit.
  const BUFF_DEFAULTS = [
    { name: 'Combat XP scroll', minutes: 30 },
    { name: 'Skill XP scroll', minutes: 30 },
    { name: 'Item drop scroll', minutes: 60 },
    { name: 'Hot Time', minutes: 60 },
    { name: 'Value Pack', minutes: 1440 },
    { name: 'Old Moon book', minutes: 60 },
    { name: 'Kamasylve blessing', minutes: 1440 }
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

  // Buffs card rows: armed buffs (soonest first) then every default not armed.
  function buffRows(buffs, fetchedMs, now) {
    const armed = buffsLive(buffs, fetchedMs, now).filter(function (b) { return typeof b.name === 'string'; })
      .map(function (b) {
        return { id: b.id === undefined ? null : b.id, name: b.name, left_s: b.left_s, minutes: defaultMinutes(b.name) };
      });
    const seen = armed.map(function (b) { return b.name.toLowerCase(); });
    const idle = BUFF_DEFAULTS.filter(function (d) { return seen.indexOf(d.name.toLowerCase()) < 0; })
      .map(function (d) { return { id: null, name: d.name, left_s: null, minutes: d.minutes }; });
    return armed.concat(idle);
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

  // Session / buff ids: a short string or a whole number (server picks).
  function validRef(v) {
    return (typeof v === 'string' && v.length > 0 && v.length <= NAME_MAX && !/[\u0000-\u001f\u007f]/.test(v)) ||
      isInt(v, 0);
  }

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
    if (k === 'delete' || k === 'clear_buff') return validRef(v);
    if (k === 'stop') return exact(v, ['silver', 'trash']) && validLoot(v);
    if (k === 'log') {
      return exact(v, ['spot', 'minutes', 'silver', 'trash']) && validName(v.spot) &&
        inRange(v.minutes, MINUTES) && validLoot(v);
    }
    if (k === 'buff') return exact(v, ['name', 'minutes']) && validName(v.name) && inRange(v.minutes, MINUTES);
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
    const minutes = function () { return wholeIn(f.minutes, MINUTES); };
    const minErr = 'minutes must be a whole number ' + MINUTES[0] + '-' + MINUTES[1];
    const name = function (k) { return typeof f[k] === 'string' ? f[k].trim() : ''; };
    if (kind === 'stop') {
      const l = loot();
      return l.error ? { ok: false, error: l.error } : { ok: true, body: { stop: l } };
    }
    if (kind === 'log') {
      if (!validName(f.spot)) return { ok: false, error: 'pick a spot' };
      const m = minutes();
      if (m === null) return { ok: false, error: minErr };
      const l = loot();
      if (l.error) return { ok: false, error: l.error };
      return { ok: true, body: { log: { spot: f.spot, minutes: m, silver: l.silver, trash: l.trash } } };
    }
    if (kind === 'buff') {
      if (!validName(name('name'))) return { ok: false, error: 'buff name: 1-' + NAME_MAX + ' plain characters' };
      const m = minutes();
      if (m === null) return { ok: false, error: minErr };
      return { ok: true, body: { buff: { name: name('name'), minutes: m } } };
    }
    if (kind === 'spot') {
      const n = name('name');
      if (!validName(n)) return { ok: false, error: 'spot name: 1-' + NAME_MAX + ' plain characters' };
      return { ok: true, body: { add_spot: n } };
    }
    return { ok: false, error: 'unknown action' };
  }

  // Overlay widgets (spec section 3: each opt-in, default on). Main reads
  // config.overlay.widgets and hands the overlay a query string; only a literal
  // false turns a widget off.
  const WIDGETS = ['grindSession', 'grindBuff'];

  function overlayWidgets(config) {
    const w = config && plainObject(config.overlay) && plainObject(config.overlay.widgets) ? config.overlay.widgets : {};
    const out = {};
    WIDGETS.forEach(function (k) { out[k] = w[k] !== false; });
    return out;
  }

  function widgetsQuery(widgets) {
    const out = {};
    WIDGETS.forEach(function (k) { out[k] = widgets && widgets[k] === false ? '0' : '1'; });
    return out;
  }

  function widgetsFromQuery(search) {
    const q = new URLSearchParams(typeof search === 'string' ? search : '');
    const out = {};
    WIDGETS.forEach(function (k) { out[k] = q.get(k) !== '0'; });
    return out;
  }

  // The only routes the dashboard bridge forwards, each with its body check.
  const POST_VALIDATORS = {
    '/api/market/watch': validWatchBody, '/api/today': validTodayBody, '/api/progress': validProgressBody,
    '/api/grind': validGrindBody
  };
  const POST_ROUTES = Object.keys(POST_VALIDATORS);

  function validPost(route, body) {
    return typeof route === 'string' && Object.prototype.hasOwnProperty.call(POST_VALIDATORS, route) &&
      POST_VALIDATORS[route](body);
  }

  const api = {
    SERVER: SERVER,
    lastDailyReset: lastDailyReset,
    lastWeeklyReset: lastWeeklyReset,
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
    validProgressBody: validProgressBody,
    parseCharacterForm: parseCharacterForm,
    parseTrackForm: parseTrackForm,
    profilePill: profilePill,
    profileRows: profileRows,
    BUFF_DEFAULTS: BUFF_DEFAULTS,
    silverPerHour: silverPerHour,
    fmtElapsed: fmtElapsed,
    liveElapsed: liveElapsed,
    buffsLive: buffsLive,
    soonestBuff: soonestBuff,
    buffRows: buffRows,
    sortSpots: sortSpots,
    spotName: spotName,
    validGrindBody: validGrindBody,
    parseGrindForm: parseGrindForm,
    overlayWidgets: overlayWidgets,
    widgetsQuery: widgetsQuery,
    widgetsFromQuery: widgetsFromQuery,
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
    validAccelerator: validAccelerator,
    hotkeys: hotkeys,
    DEFAULT_HOTKEYS: DEFAULT_HOTKEYS
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.EWCore = api;
})(typeof self !== 'undefined' ? self : this);
