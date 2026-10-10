/* EW shared pure logic, part `grind` (plan 107 split of ewcore.js):
    Grind sessions, buffs, loot, drop-rate card, spot recommender.
   Installed in order by ../ewcore.js, which re-exports the public names as
   EWCore. Loaded as a plain script before ewcore.js (window.EWCoreParts) and by
   require() under node --test. No DOM, no Electron. */
(function (root, install) {
  if (typeof module !== 'undefined' && module.exports) module.exports = install;
  else (root.EWCoreParts = root.EWCoreParts || {}).grind = install;
})(typeof self !== 'undefined' ? self : this, function (K) {
  'use strict';

  // from earlier parts
  const { LEVEL, fmtDuration, fmtDurationShort, fmtSilver, inRange, isInt, isNum, onlyKeys,
    parseDuration, parseSilver, plainObject, wholeIn } = K;

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

  // Plan 048: one buffRows row -> display strings. Time left as a countdown,
  // unarmed 'off' (muted); the minutes field pre-filled short ('30d', '1h').
  function buffRowView(row) {
    const armed = plainObject(row) && isNum(row.left_s) && row.left_s > 0;
    return { armed: armed, muted: !armed, left: armed ? fmtDuration(row.left_s * 1000) : 'off',
      minutes: plainObject(row) ? fmtDurationShort(row.minutes) : '-' };
  }

  // Plan 085: a tracked row's patch-notes verdict ({verdict, date, evidence})
  // -> {check, text} for its source tooltip, or null (silent / none).
  // `check` = contradicted: the value is kept and shown with a check badge.
  function patchNote(p) {
    if (!plainObject(p)) return null;
    const d = typeof p.date === 'string' ? p.date : '?';
    if (p.verdict === 'confirmed') return { check: false, text: 'verified by patch notes ' + d };
    if (p.verdict !== 'contradicted') return null;
    const ev = typeof p.evidence === 'string' && p.evidence ? ': "' + p.evidence + '"' : '';
    return { check: true, text: 'check - patch notes ' + d + ' disagree' + ev };
  }

  // Plan 018 XP buff presets from GET /api/grind: [{name, xp_pct, title, check}],
  // junk rows dropped. The values are community / patch-note figures, verify.
  function xpPresets(d) {
    const rows = plainObject(d) && Array.isArray(d.xp_presets) ? d.xp_presets : [];
    return rows.filter(function (p) {
      return plainObject(p) && validName(p.name) && inRange(p.xp_pct, XP_PCT);
    }).map(function (p) {
      const pn = patchNote(p.patch);
      const bits = [typeof p.notes === 'string' ? p.notes : '', typeof p.source === 'string' ? p.source : '',
        typeof p.verified === 'string' ? 'as of ' + p.verified : '', pn ? pn.text : ''].filter(function (x) { return x; });
      return { name: p.name, xp_pct: p.xp_pct, title: bits.join(' - '), check: !!(pn && pn.check) };
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
    if (k === 'stop') {
      // Plan 046: at_exit true ends the session at the pending game-exit time.
      if (plainObject(v) && Object.prototype.hasOwnProperty.call(v, 'at_exit')) {
        if (v.at_exit !== true) return false;
        const rest = {};
        Object.keys(v).forEach(function (x) { if (x !== 'at_exit') rest[x] = v[x]; });
        return withLoot(rest, ['silver', 'trash']) && validLoot(rest);
      }
      return withLoot(v, ['silver', 'trash']) && validLoot(v);
    }
    if (k === 'keep') return v === true;
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
      const pn = patchNote(r.patch);  // plan 085
      const bits = [r.bypass === 'none' ? '' : 'bypass ' + String(r.bypass).replace('to', 'to ') + '%',
        r.verified === false ? 'unverified' : (typeof r.verified === 'string' ? 'as of ' + r.verified : ''),
        typeof r.note === 'string' ? r.note : '', typeof r.source === 'string' ? r.source : '',
        pn ? pn.text : '']
        .filter(function (s) { return s; });
      const val = [pctText(r.rate_pct), isNum(r.amount_pct) ? pctText(r.amount_pct) + ' amt' : '']
        .filter(function (s) { return s; }).join(' ');
      return { id: r.id, name: r.name, value: val, on: r.on === true, timer: r.timer === true,
        unverified: r.verified === false, overridden: r.overridden === true, title: bits.join(' - '),
        check: !!(pn && pn.check) };
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
    // Plan 048: silver / counts take 1.2b, 850m, 1,234,567; minutes take 30d, 1h30m, 90.
    const amount = function (k, r) {
      const v = parseSilver(f[k]);
      return v !== null && inRange(v, r) ? v : null;
    };
    const loot = function () {
      const silver = blank('silver') ? 0 : amount('silver', SILVER);
      if (silver === null) return { error: 'silver: 0-10T, e.g. 1.2b, 850m or 1,234,567' };
      const trash = blank('trash') ? 0 : amount('trash', TRASH);
      if (trash === null) return { error: 'trash: a count 0-1000000, e.g. 3,200 or 3.2k' };
      return { silver: silver, trash: trash };
    };
    const minutes = function (r) {
      const v = parseDuration(f.minutes);
      return v !== null && inRange(v, r) ? v : null;
    };
    const minErr = function (r) {
      return 'minutes ' + r[0] + '-' + r[1] + ' (' + fmtDurationShort(r[1]) + '), e.g. 90, 1h30m or 30d';
    };
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
        const v = amount('vendor_price', VENDOR_PRICE);
        if (v === null) return { ok: false, error: 'vendor price: 0-10B, e.g. 12k or 1,500' };
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

  // Plan 088: "~830 kills to Lv 58" from a spot row's in-game zone read
  // (`zone_xp`), "re-read at Lv 57" when it was read in another level band,
  // '' without one.
  function zoneKillsText(z) {
    if (!plainObject(z)) return '';
    if (z.current === false) return isInt(z.reread_level, 1) ? 're-read at Lv ' + z.reread_level : '';
    if (!isInt(z.kills_to_level, 0) || !isInt(z.next_level, 1)) return '';
    return '~' + String(z.kills_to_level).replace(/\B(?=(\d{3})+(?!\d))/g, ',') +
      ' kills to Lv ' + z.next_level;
  }

  // Plan 088: "in-game Lv 58" (the panel's recommended level), '' without one.
  function zoneLevelText(z) {
    return plainObject(z) && isInt(z.recommended_level, 1) ? 'in-game Lv ' + z.recommended_level : '';
  }

  // Plan 088: the XP-buff card line while the grind session's zone is cap-bound.
  function zoneCapText(zc) {
    return plainObject(zc) && zc.cap_bound === true ? 'cap-bound here - XP buffs add nothing' : '';
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

  Object.assign(K, { NAME_MAX: NAME_MAX, MINUTES: MINUTES, BUFF_MINUTES: BUFF_MINUTES,
    SILVER: SILVER, TRASH: TRASH, XP_PCT: XP_PCT, BUFF_DEFAULTS: BUFF_DEFAULTS,
    silverPerHour: silverPerHour, fmtElapsed: fmtElapsed, sinceFetch: sinceFetch,
    liveElapsed: liveElapsed, buffLeft: buffLeft, buffsLive: buffsLive,
    soonestBuff: soonestBuff, defaultMinutes: defaultMinutes, buffRows: buffRows,
    buffRowView: buffRowView, patchNote: patchNote, xpPresets: xpPresets, sortSpots: sortSpots,
    spotName: spotName, validName: validName, SESSION_ID_RE: SESSION_ID_RE,
    BUFF_ID_RE: BUFF_ID_RE, validRef: validRef, exact: exact, validLoot: validLoot,
    LOOT_MAX: LOOT_MAX, LOOT_COUNT: LOOT_COUNT, VENDOR_PRICE: VENDOR_PRICE, ITEM_ID: ITEM_ID,
    validLootList: validLootList, withLoot: withLoot, validLootItem: validLootItem,
    lootHintText: lootHintText, sessionSilver: sessionSilver, trashPileText: trashPileText,
    validGrindBody: validGrindBody, DROP_ROW_RE: DROP_ROW_RE, DROP_FIELD_RE: DROP_FIELD_RE,
    DROP_BYPASS: DROP_BYPASS, validDropValue: validDropValue, pctText: pctText,
    dropView: dropView, agrisLine: agrisLine, parseGrindForm: parseGrindForm,
    SPOT_GOALS: SPOT_GOALS, SPOT_STAT: SPOT_STAT, SPOT_LEVEL: SPOT_LEVEL, spotsPath: spotsPath,
    spotNeedText: spotNeedText, spotGapText: spotGapText, zoneKillsText: zoneKillsText,
    zoneLevelText: zoneLevelText, zoneCapText: zoneCapText, spotRecs: spotRecs,
    matchSpot: matchSpot });
  return function link() {};
});
