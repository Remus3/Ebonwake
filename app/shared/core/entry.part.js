/* EW shared pure logic, part `entry` (plan 107 split of ewcore.js):
    quick-entry parsers, local time, loot prefill, hot windows, buff rows.
   Installed in order by ../ewcore.js, which re-exports the public names as
   EWCore. Loaded as a plain script before ewcore.js (window.EWCoreParts) and by
   require() under node --test. No DOM, no Electron. */
(function (root, install) {
  if (typeof module !== 'undefined' && module.exports) module.exports = install;
  else (root.EWCoreParts = root.EWCoreParts || {}).entry = install;
})(typeof self !== 'undefined' ? self : this, function (K) {
  'use strict';

  // from earlier parts
  const { fmtAge, isInt, isNum, netProceeds, plainObject } = K;
  // from later parts, bound by link() once every part is installed
  let ISO_TS, fmtDays, inRange, pad2;

  // ---- plan 048: quick-entry parsers + local time ----
  // Inputs accept what the operator reads in game or in patch notes; stored
  // values stay integers and UTC.

  const SILVER_PLACES = { k: 3, m: 6, b: 9, t: 12 };

  // Operator-typed silver: "84,500,000", "100m", "1.2b", "750k" -> int or null.
  // Exact decimal maths: a fraction finer than one silver is refused, not rounded.
  function parseSilver(s) {
    if (s === null || s === undefined) return null;
    const t = String(s).trim().toLowerCase();
    const m = /^(\d{1,3}(?:,\d{3})+|\d+)(?:(?:\.(\d+))? ?([kmbt]))?$/.exec(t);
    if (!m) return null;
    const places = m[3] ? SILVER_PLACES[m[3]] : 0;
    const frac = m[2] || '';
    if (frac.length > places) return null;
    const v = Number(m[1].replace(/,/g, '') + frac.padEnd(places, '0'));
    return Number.isSafeInteger(v) ? v : null;
  }

  // Duration -> whole minutes or null: "30d", "1h30m", "90m", "90min", "1.5h";
  // a bare number is minutes ("45"). Units in d, h, m order, each at most once.
  function parseDuration(s) {
    if (s === null || s === undefined) return null;
    const t = String(s).toLowerCase().replace(/\s+/g, '');
    if (/^\d{1,7}$/.test(t)) return Number(t);
    const m = /^(?:(\d{1,5}(?:\.\d+)?)d)?(?:(\d{1,6}(?:\.\d+)?)h)?(?:(\d{1,7})(?:m|min))?$/.exec(t);
    if (!t || !m) return null;
    const v = Number(m[1] || 0) * 1440 + Number(m[2] || 0) * 60 + Number(m[3] || 0);
    const r = Math.round(v);
    return Math.abs(v - r) < 1e-6 && Number.isSafeInteger(r) ? r : null;
  }

  // Whole minutes -> "30d", "1h", "1h30m", "1d1m", "45m"; parseDuration reads it back.
  function fmtDurationShort(min) {
    if (!isInt(min, 0)) return '-';
    if (min === 0) return '0m';
    const d = Math.floor(min / 1440);
    const h = Math.floor((min % 1440) / 60);
    const m = min % 60;
    return (d ? d + 'd' : '') + (h ? h + 'h' : '') + (m ? m + 'm' : '');
  }

  // US Pacific DST, a port of server/ew/bosses.py (plan 031): [2nd Sun Mar
  // 02:00, 1st Sun Nov 02:00) PT wall clock. Wall clocks are carried as
  // Date.UTC ms of their fields. app/test/ewcore.test.js pins the same edge
  // dates as tests/test_bosses.py.
  function nthSunday(y, mo, n) {
    const first = new Date(Date.UTC(y, mo - 1, 1)).getUTCDay();
    return 1 + (7 - first) % 7 + 7 * (n - 1);
  }

  function ptDstWall(y) {
    return [Date.UTC(y, 2, nthSunday(y, 3, 2), 2), Date.UTC(y, 10, nthSunday(y, 11, 1), 2)];
  }

  // UTC offset of Pacific time at instant utcMs: -7 (PDT) or -8 (PST).
  function ptOffsetHours(utcMs) {
    const b = ptDstWall(new Date(utcMs).getUTCFullYear());
    return utcMs >= b[0] + 8 * 3600000 && utcMs < b[1] + 7 * 3600000 ? -7 : -8;
  }

  function wallMs(y, mo, d, h, mi) {
    if (![y, mo, d, h, mi].every(function (v) { return isInt(v, 0); }) || h > 23 || mi > 59) return null;
    const w = Date.UTC(y, mo - 1, d, h, mi);
    const t = new Date(w);
    return t.getUTCFullYear() === y && t.getUTCMonth() === mo - 1 && t.getUTCDate() === d ? w : null;
  }

  // PT wall clock -> UTC ms, or null for an impossible date. The repeated 01:xx
  // in November and the skipped 02:xx in March both resolve to PDT, as in Python.
  function ptToUtc(y, mo, d, h, mi) {
    const w = wallMs(y, mo, d, h, mi);
    if (w === null) return null;
    const b = ptDstWall(y);
    return w + (w >= b[0] && w < b[1] ? 7 : 8) * 3600000;
  }

  // Minutes east of UTC for zone 'utc' | 'pt' | 'local' (this machine) at utcMs.
  function zoneOffsetMin(zone, utcMs) {
    if (zone === 'utc') return 0;
    if (zone === 'pt') return ptOffsetHours(utcMs) * 60;
    if (zone === 'local') return -new Date(utcMs).getTimezoneOffset();
    return null;
  }

  // Zone wall clock -> UTC ms. Local time goes through Date so the host's own
  // DST rule applies; null for an impossible date or an unknown zone.
  function zoneToUtc(zone, y, mo, d, h, mi) {
    if (zone === 'pt') return ptToUtc(y, mo, d, h, mi);
    const w = wallMs(y, mo, d, h, mi);
    if (w === null) return null;
    if (zone === 'utc') return w;
    if (zone !== 'local') return null;
    const t = new Date(y, mo - 1, d, h, mi);
    return t.getDate() === d ? t.getTime() : null;
  }

  function zoneOf(o) {
    return o && typeof o.zone === 'string' ? o.zone : 'local';
  }

  function hm(min) { return pad2(Math.floor(min / 60)) + ':' + pad2(min % 60); }

  // "21:00" / "9:30" / "0900" / "9pm" / "12:15 am" -> minutes of day, or null.
  function clockMinutes(s) {
    const m = /^(\d{1,2})(?::?([0-5]\d))?\s*([ap]m)?$/.exec(String(s === null || s === undefined ? '' : s).trim().toLowerCase());
    if (!m || (!m[2] && !m[3])) return null;
    let h = Number(m[1]);
    if (m[3]) {
      if (h < 1 || h > 12) return null;
      h = h % 12 + (m[3] === 'pm' ? 12 : 0);
    } else if (h > 23 || m[1].length === 1 && !/:/.test(s)) {
      return null;
    }
    return h * 60 + Number(m[2] || 0);
  }

  // Wall time in opts.zone (default local) -> {utc: 'HH:MM', shift: -1|0|1}
  // days added to reach the UTC weekday. Uses the zone offset at opts.now.
  function parseLocalTime(s, opts) {
    const o = opts || {};
    const min = clockMinutes(s);
    const off = zoneOffsetMin(zoneOf(o), isNum(o.now) ? o.now : Date.now());
    if (min === null || off === null) return null;
    const t = min - off;
    const shift = Math.floor(t / 1440);
    return { utc: hm(t - shift * 1440), shift: shift };
  }

  // UTC 'HH:MM' -> the zone's {hhmm, shift} at opts.now (inverse of parseLocalTime).
  function utcClockIn(hhmm, opts) {
    const o = opts || {};
    const m = typeof hhmm === 'string' ? /^([01]\d|2[0-3]):([0-5]\d)$/.exec(hhmm) : null;
    const off = zoneOffsetMin(zoneOf(o), isNum(o.now) ? o.now : Date.now());
    if (!m || off === null) return null;
    const t = Number(m[1]) * 60 + Number(m[2]) + off;
    const shift = Math.floor(t / 1440);
    return { hhmm: hm(t - shift * 1440), shift: shift };
  }

  function utcMsOf(iso) {
    if (typeof iso !== 'string' || !ISO_TS.test(iso)) return null;
    const ms = Date.parse(iso);
    return isFinite(ms) ? ms : null;
  }

  function wallText(ms) {
    const d = new Date(ms);
    return d.getUTCFullYear() + '-' + pad2(d.getUTCMonth() + 1) + '-' + pad2(d.getUTCDate()) + ' ' +
      pad2(d.getUTCHours()) + ':' + pad2(d.getUTCMinutes());
  }

  // Stored UTC ISO -> {text: 'YYYY-MM-DD HH:MM' in opts.zone (default local),
  // title: 'UTC HH:MM' (with the UTC date when it differs)}, or null.
  function fmtLocal(utcIso, opts) {
    const ms = utcMsOf(utcIso);
    const off = ms === null ? null : zoneOffsetMin(zoneOf(opts), ms);
    if (off === null) return null;
    const text = wallText(ms + off * 60000);
    const utc = wallText(ms);
    return { text: text, title: 'UTC ' + (utc.slice(0, 10) === text.slice(0, 10) ? utc.slice(11) : utc) };
  }

  // 'YYYY-MM-DD HH:MM' (or T) in opts.zone -> UTC 'YYYY-MM-DDTHH:MM:00Z', or null.
  function parseLocalDateTime(s, opts) {
    const m = typeof s === 'string' ? /^(\d{4})-(\d{2})-(\d{2})[ T](\d{1,2}):(\d{2})$/.exec(s.trim()) : null;
    if (!m) return null;
    const ms = zoneToUtc(zoneOf(opts), +m[1], +m[2], +m[3], +m[4], +m[5]);
    return ms === null ? null : new Date(ms).toISOString().slice(0, 16) + ':00Z';
  }

  // Stored UTC Hot Time window {days (Mon=0), start, end} -> the zone's view
  // {days, start, end, title 'UTC <days> HH:MM-HH:MM'}; days follow the start.
  function hotWindowView(w, opts) {
    if (!plainObject(w) || !Array.isArray(w.days)) return null;
    const a = utcClockIn(w.start, opts);
    const b = utcClockIn(w.end, opts);
    if (!a || !b) return null;
    const days = w.days.filter(function (x) { return inRange(x, [0, 6]); })
      .map(function (x) { return ((x + a.shift) % 7 + 7) % 7; }).sort(function (x, y) { return x - y; });
    return { days: days, start: a.hhmm, end: b.hhmm, title: 'UTC ' + fmtDays(w.days) + ' ' + w.start + '-' + w.end };
  }

  // Plan 064 auto window {start, end (ISO UTC), bonus, pct} -> 'MM-DD HH:MM -
  // MM-DD HH:MM <bonus>' in the zone, or '' when a time is unreadable.
  function hotAutoText(w, opts) {
    if (!plainObject(w)) return '';
    const a = fmtLocal(w.start, opts);
    const b = fmtLocal(w.end, opts);
    if (!a || !b) return '';
    const bonus = typeof w.bonus === 'string' ? w.bonus : (isNum(w.pct) ? '+' + w.pct + '%' : '');
    return (a.text.slice(5) + ' - ' + b.text.slice(5) + ' ' + bonus).trim();
  }

  // Plan 081: /api/grind/loot `prefill` {source: 'ocr', at, items: [{name,
  // count, low}]} -> {counts: {name: 'n'}, low: [name], text} for the count
  // boxes the operator has not typed in (`typed`: {name: true}).
  function lootPrefill(p, counts, typed, now) {
    const out = { counts: {}, low: [], text: '' };
    if (!plainObject(p) || !Array.isArray(p.items)) return out;
    const t = plainObject(typed) ? typed : {};
    let n = 0;
    p.items.forEach(function (it) {
      if (!plainObject(it) || typeof it.name !== 'string' || !isInt(it.count, 1) || t[it.name] === true) return;
      out.counts[it.name] = String(it.count);
      if (it.low === true) out.low.push(it.name);
      n += 1;
    });
    if (n) {
      const ms = utcMsOf(p.at);
      const age = ms === null ? '' : ', ' + fmtAge(((isNum(now) ? now : Date.now()) - ms) / 1000) + ' ago';
      out.text = n + ' count' + (n === 1 ? '' : 's') + ' from screenshots this session' + age + ' - type to correct';
    }
    return out;
  }

  // Plan 081: a typed recurring window's end -> {text, ended}. A pre-081 row
  // without one says so (it never ends by itself).
  function hotUntilText(w, opts) {
    if (!plainObject(w)) return { text: '', ended: false };
    if (w.until === null || w.until === undefined) return { text: 'no end set', ended: false };
    const u = fmtLocal(w.until, opts);
    if (!u) return { text: '', ended: false };
    if (w.ended === true) return { text: 'ended ' + u.text.slice(5), ended: true };
    return { text: 'until ' + u.text.slice(5), ended: false };
  }

  // Entry zones for time inputs: [value, label]. 'pt' is the "paste PT" helper
  // for patch-note times.
  const ZONES = [['local', 'local'], ['pt', 'PT'], ['utc', 'UTC']];

  function zoneName(zone) {
    const z = ZONES.filter(function (x) { return x[0] === zone; })[0];
    return z ? z[1] : String(zone);
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

  Object.assign(K, { SILVER_PLACES: SILVER_PLACES, parseSilver: parseSilver,
    parseDuration: parseDuration, fmtDurationShort: fmtDurationShort, nthSunday: nthSunday,
    ptDstWall: ptDstWall, ptOffsetHours: ptOffsetHours, wallMs: wallMs, ptToUtc: ptToUtc,
    zoneOffsetMin: zoneOffsetMin, zoneToUtc: zoneToUtc, zoneOf: zoneOf, hm: hm,
    clockMinutes: clockMinutes, parseLocalTime: parseLocalTime, utcClockIn: utcClockIn,
    utcMsOf: utcMsOf, wallText: wallText, fmtLocal: fmtLocal,
    parseLocalDateTime: parseLocalDateTime, hotWindowView: hotWindowView,
    hotAutoText: hotAutoText, lootPrefill: lootPrefill, hotUntilText: hotUntilText,
    ZONES: ZONES, zoneName: zoneName, pairProfit: pairProfit, PREORDER_NOTE: PREORDER_NOTE,
    PREORDER_WHY: PREORDER_WHY, preorderState: preorderState, preorderBadge: preorderBadge });
  return function link() {
    ISO_TS = K.ISO_TS;
    fmtDays = K.fmtDays;
    inRange = K.inRange;
    pad2 = K.pad2;
  };
});
