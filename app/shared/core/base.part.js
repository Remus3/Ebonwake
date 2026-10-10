/* EW shared pure logic, part `base` (plan 107 split of ewcore.js):
    reset clocks, durations, freshness, tabs, server health + version skew,
   signals, override ledger, zone-aware start text.
   Installed in order by ../ewcore.js, which re-exports the public names as
   EWCore. Loaded as a plain script before ewcore.js (window.EWCoreParts) and by
   require() under node --test. No DOM, no Electron. */
(function (root, install) {
  if (typeof module !== 'undefined' && module.exports) module.exports = install;
  else (root.EWCoreParts = root.EWCoreParts || {}).base = install;
})(typeof self !== 'undefined' ? self : this, function (K) {
  'use strict';

  // from later parts, bound by link() once every part is installed
  let fmtAge, fmtLocal, isNum;

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
      else if (stale || status === 'stale' || status === 'differs') cls = 'warn'; // plan 072
      else if (at === null) cls = 'unknown';
      return { name: name, age: ageS === null ? 'never' : fmtAge(ageS) + ' ago',
        status: status || '-', stale: stale, cls: cls };
    });
  }

  // Plan 073: /api/signals digest -> one row per signal (server order). `off`
  // (quiet by design: game closed, feature off) renders muted, never bad.
  const SIGNAL_CLS = { ok: 'ok', warn: 'warn', bad: 'bad', off: 'unknown' };
  function signalRows(doc) {
    const rows = doc && Array.isArray(doc.rows) ? doc.rows : [];
    return rows.filter(function (r) { return r && typeof r === 'object'; }).map(function (r) {
      const level = SIGNAL_CLS[r.level] ? r.level : 'warn';
      const name = typeof r.name === 'string' ? r.name : String(r.id || '?');
      const age = isNum(r.age_s) ? fmtAge(r.age_s) + ' ago' : '-';
      return { id: String(r.id || ''), name: name, level: level, cls: SIGNAL_CLS[level],
        text: name + ' ' + level + (isNum(r.age_s) ? ' - ' + age : ''),
        detail: typeof r.detail === 'string' ? r.detail : '',
        hint: typeof r.hint === 'string' ? r.hint : '',
        // plan 085: evidence lines (data row) shown under the row on System
        lines: (Array.isArray(r.lines) ? r.lines : []).filter(function (s) { return typeof s === 'string' && s; }).slice(0, 10) };
    });
  }

  // Plan 073: Home shows one pill only while any row is `bad`; null otherwise.
  function signalPill(doc) {
    const bad = signalRows(doc).filter(function (r) { return r.level === 'bad'; });
    if (!bad.length) return null;
    return { cls: 'bad', tab: 'system',
      text: bad.length === 1 ? bad[0].name + ': not working' : bad.length + ' signals not working',
      title: bad.map(function (r) { return r.name + ': ' + (r.hint || r.detail); }).join('\n') };
  }

  // Plan 079: override ledger. One /api/overrides item -> the card-header
  // badge {cls, text, title}; null for junk. The title says what is in force,
  // where it came from and when it lapses.
  function fmtOverrideValue(v) {
    if (v === null || v === undefined) return '-';
    if (typeof v === 'boolean') return v ? 'on' : 'off';
    if (typeof v === 'object') {
      return Object.keys(v).filter(function (k) { return v[k] !== null && v[k] !== undefined; })
        .map(function (k) { return k + ' ' + v[k]; }).join(', ') || '-';
    }
    return String(v);
  }
  function overrideBadge(entry) {
    if (!entry || typeof entry !== 'object' || typeof entry.key !== 'string') return null;
    const label = typeof entry.label === 'string' && entry.label ? entry.label : entry.key;
    const src = entry.source === 'config' ? 'from config/local.json' : 'typed';
    const set = typeof entry.set_at === 'string' && entry.set_at ? ', set ' + entry.set_at.slice(0, 10) : '';
    const exp = isNum(entry.expires_in_s) ? 'expires in ' + fmtAge(entry.expires_in_s)
      : (entry.reason ? entry.reason : 'no expiry');
    return { cls: 'ew-ovr', text: 'override',
      title: label + ' = ' + fmtOverrideValue(entry.value) + ' (' + src + set + '); ' + exp,
      key: entry.key, clearable: entry.clearable !== false };
  }
  function overrideItems(doc) {
    const items = doc && Array.isArray(doc.items) ? doc.items : [];
    return items.filter(function (i) { return i && typeof i === 'object' && typeof i.key === 'string'; });
  }
  // Badges for one card id (policy `cards`); [] when nothing overrides it.
  function overrideBadges(doc, card) {
    return overrideItems(doc).filter(function (i) {
      return Array.isArray(i.cards) && i.cards.indexOf(card) >= 0;
    }).map(overrideBadge).filter(Boolean);
  }
  // Signal health card rows: one per active override.
  function overrideRows(doc) {
    return overrideItems(doc).map(function (i) {
      const b = overrideBadge(i);
      return { key: i.key, text: (i.label || i.key) + ': ' + fmtOverrideValue(i.value),
        title: b.title, clearable: b.clearable };
    });
  }
  // Home status line: `N overrides` while N > 0 (signals digest section); else null.
  function overridePill(doc) {
    const sec = doc && doc.overrides && typeof doc.overrides === 'object' ? doc.overrides : null;
    const n = overrideItems(sec).length;
    if (!n) return null;
    return { cls: 'warn ew-ovr', tab: 'system', text: n === 1 ? '1 override' : n + ' overrides',
      title: overrideItems(sec).map(function (i) { return overrideBadge(i).title; }).join('\n') };
  }

  // Server card rows from /api/version + /api/health (either may be null).
  // Plan 078: `started` in opts.zone (default local) with the UTC as a third
  // element (the row's hover title); an unreadable stamp stays as sent.
  function serverRows(version, health, appCommit, nowMs, opts) {
    const v = version && typeof version === 'object' ? version : {};
    const started = parseWhen(v.started);
    const loc = fmtLocal(v.started, opts);
    const short = function (c) { return knownCommit(c) ? c.trim().slice(0, 7) : 'unknown'; };
    return [
      ['status', health && health.ok === true ? 'ok' : 'no answer'],
      ['server commit', short(v.commit)],
      ['app commit', short(appCommit)],
      ['outdated', commitsDiffer(v.commit, appCommit) ? 'yes - restart (' + RESTART_HINT + ')' : 'no'],
      loc ? ['started', loc.text, loc.title] : ['started', typeof v.started === 'string' ? v.started : '-'],
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

  Object.assign(K, { nextDailyReset: nextDailyReset, nextWeeklyReset: nextWeeklyReset,
    fmtDuration: fmtDuration, freshness: freshness, normalizeTabs: normalizeTabs,
    backoffMs: backoffMs, HEALTH_DEAD_MS: HEALTH_DEAD_MS, VERSION_POLL_MS: VERSION_POLL_MS,
    RESTART_HINT: RESTART_HINT, knownCommit: knownCommit, commitsDiffer: commitsDiffer,
    healthPill: healthPill, notOnServer: notOnServer, parseWhen: parseWhen,
    sourceFreshness: sourceFreshness, SIGNAL_CLS: SIGNAL_CLS, signalRows: signalRows,
    signalPill: signalPill, fmtOverrideValue: fmtOverrideValue, overrideBadge: overrideBadge,
    overrideItems: overrideItems, overrideBadges: overrideBadges, overrideRows: overrideRows,
    overridePill: overridePill, serverRows: serverRows, DEFAULT_HOTKEYS: DEFAULT_HOTKEYS,
    validAccelerator: validAccelerator, hotkeys: hotkeys });
  return function link() {
    fmtAge = K.fmtAge;
    fmtLocal = K.fmtLocal;
    isNum = K.isNum;
  };
});
