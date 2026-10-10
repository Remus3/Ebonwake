/* EW shared pure logic, part `ocr` (plan 107 split of ewcore.js):
    game state, OCR, auto-OCR review queue, OCR loot import.
   Installed in order by ../ewcore.js, which re-exports the public names as
   EWCore. Loaded as a plain script before ewcore.js (window.EWCoreParts) and by
   require() under node --test. No DOM, no Electron. */
(function (root, install) {
  if (typeof module !== 'undefined' && module.exports) module.exports = install;
  else (root.EWCoreParts = root.EWCoreParts || {}).ocr = install;
})(typeof self !== 'undefined' ? self : this, function (K) {
  'use strict';

  // from earlier parts
  const { BUFF_MINUTES, LOOT_COUNT, SILVER, exact, fmtAge, fmtDuration, hm, inRange, isNum,
    plainObject, validGrindBody, validName } = K;

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
    if (exact(body, ['review'])) return validOcrReview(body.review);
    if (exact(body, ['undo'])) return typeof body.undo === 'string' && OCR_UNDO_RE.test(body.undo);
    if (exact(body, ['loot'])) {
      const l = body.loot;
      return exact(l, ['shot', 'spot']) && validShotName(l.shot) &&
        typeof l.spot === 'string' && OCR_SPOT_RE.test(l.spot);
    }
    return exact(body, ['file']) && validShotName(body.file);
  }

  function validShotName(f) {
    return typeof f === 'string' && f.trim().length > 0 && f.length <= OCR_NAME_MAX &&
      !/[\u0000-\u001f\u007f\\/:]/.test(f) && f !== '.' && f !== '..';
  }

  // ---- auto-OCR review queue (plan 063) ----
  // GET /api/ocr/auto -> {enabled, commit_min, daily_cap, today, pending,
  // review: [{id, file, kind, name, value, conf, why, shot_at}], commits: [{id,
  // file, kind, name, value, at, via}], silver}. POST /api/ocr {review: {id,
  // action: accept|discard}} / {review: {id, action: 'fix', value}} / {undo: id}.

  const OCR_REVIEW_RE = /^r[0-9]{1,9}$/;
  const OCR_UNDO_RE = /^u[0-9]{1,9}$/;
  // Plan 066: + level {level, pct}, gear (ap / aap / dp int) and book_use
  // {size, pct_before, pct_after} (accept / discard only).
  const OCR_AUTO_KINDS = ['silver', 'buff', 'level', 'gear', 'book_use'];
  const OCR_REVIEW_MAX = 50;
  const OCR_LEVEL_MAX = 75;  // server levels.LEVEL_MAX

  function ocrPct(v) {
    return isNum(v) && v >= 0 && v <= 100 && Math.abs(v * 1000 - Math.round(v * 1000)) < 1e-6;
  }

  function ocrLevelValue(v) {
    return plainObject(v) && exact(v, ['level', 'pct']) && Number.isInteger(v.level) &&
      v.level >= 1 && v.level <= OCR_LEVEL_MAX && ocrPct(v.pct);
  }

  function ocrBookValue(v) {
    return plainObject(v) && exact(v, ['size', 'pct_before', 'pct_after']) &&
      typeof v.size === 'string' && /^[a-z]{1,10}$/.test(v.size) && ocrPct(v.pct_before) &&
      ocrPct(v.pct_after);
  }

  function ocrAutoValue(kind, v) {
    if (kind === 'level') return ocrLevelValue(v) ? { level: v.level, pct: v.pct } : null;
    if (kind === 'book_use') {
      return ocrBookValue(v) ? { size: v.size, pct_before: v.pct_before, pct_after: v.pct_after } : null;
    }
    return Number.isInteger(v) && v >= 0 ? v : null;
  }

  function validOcrReview(r) {
    if (!plainObject(r) || typeof r.id !== 'string' || !OCR_REVIEW_RE.test(r.id)) return false;
    if (r.action === 'fix') {
      return exact(r, ['id', 'action', 'value']) && (ocrLevelValue(r.value) ||
        (Number.isInteger(r.value) && r.value >= 0 && r.value <= 1e13));
    }
    return (r.action === 'accept' || r.action === 'discard') && exact(r, ['id', 'action']);
  }

  function ocrAutoRow(r, re) {
    if (!plainObject(r) || typeof r.id !== 'string' || !re.test(r.id)) return null;
    if (OCR_AUTO_KINDS.indexOf(r.kind) < 0) return null;
    const value = ocrAutoValue(r.kind, r.value);
    if (value === null) return null;
    return {
      id: r.id, kind: r.kind, value: value,
      name: typeof r.name === 'string' ? r.name.slice(0, 60) : r.kind,
      file: typeof r.file === 'string' ? r.file.slice(0, OCR_NAME_MAX) : '',
      conf: isNum(r.conf) && r.conf >= 0 && r.conf <= 1 ? r.conf : null,
      why: typeof r.why === 'string' ? r.why.slice(0, 200) : '',
      via: typeof r.via === 'string' ? r.via.slice(0, 20) : ''
    };
  }

  function normalizeOcrAuto(d) {
    if (!plainObject(d)) return null;
    const pick = function (list, re) {
      return (Array.isArray(list) ? list : []).map(function (r) { return ocrAutoRow(r, re); })
        .filter(Boolean).slice(0, OCR_REVIEW_MAX);
    };
    return {
      enabled: d.enabled !== false,
      today: Number.isInteger(d.today) && d.today >= 0 ? d.today : 0,
      cap: Number.isInteger(d.daily_cap) && d.daily_cap >= 0 ? d.daily_cap : null,
      pending: Number.isInteger(d.pending) && d.pending >= 0 ? d.pending : 0,
      review: pick(d.review, OCR_REVIEW_RE),
      commits: pick(d.commits, OCR_UNDO_RE),
      silverH: ocrSilverHRow(d.silver_h)
    };
  }

  // Plan 066 silver/h between OCR silver reads in one play session.
  function ocrSilverHRow(s) {
    if (!plainObject(s) || !Number.isInteger(s.per_h) || !Number.isInteger(s.span_s) ||
        s.span_s <= 0 || !Number.isInteger(s.n) || s.n < 2) return null;
    return { per_h: s.per_h, span_s: s.span_s, n: s.n };
  }

  function ocrSilverH(a) {
    const s = a && a.silverH;
    if (!s) return '';
    return 'silver/h this session: ' + String(s.per_h).replace(/\B(?=(\d{3})+(?!\d))/g, ',') +
      ' (' + s.n + ' shots over ' + Math.round(s.span_s / 60) + 'm)';
  }

  function ocrValueText(r) {
    // Exact digits: a review decision needs the read number, not a rounded 1.23M.
    if (r.kind === 'silver') return 'silver ' + String(r.value).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    if (r.kind === 'level') return 'Lv ' + r.value.level + ' ' + r.value.pct + '%';
    if (r.kind === 'gear') return r.name.toUpperCase() + ' ' + r.value;
    if (r.kind === 'book_use') {
      return r.value.size + ' book used? ' + r.value.pct_before + '% -> ' + r.value.pct_after + '%';
    }
    return r.name + ' ' + r.value + 'm';
  }

  // One review / commit row -> "silver 1,234,567" / "XP scroll 30m" (+ conf).
  function ocrAutoLabel(r) {
    const c = r.conf === null || r.conf === undefined ? '' : ' (' + Math.round(r.conf * 100) + '%)';
    return ocrValueText(r) + c;
  }

  // Fix input placeholder per kind; '' = no fix (a book suggestion).
  function ocrFixHint(kind) {
    return { silver: 'silver', buff: 'minutes left now', level: 'level pct, e.g. 61 12.5',
      gear: 'stat 0-999' }[kind] || '';
  }

  // Header line of the System card: "N to review".
  function ocrAutoHead(a) {
    if (!a) return '';
    const n = a.review.length;
    return (n ? n + ' to review' : 'nothing to review') + ' - ' + a.today + (a.cap === null ? '' : '/' + a.cap) +
      ' read today' + (a.enabled ? '' : ' (auto off)');
  }

  // Operator-typed fix value -> POST body, or null when it is not a whole number.
  function ocrFixBody(id, kind, text) {
    if (kind === 'book_use') return null;
    if (kind === 'level') {
      // "61 12.5" / "Lv 61 12.500%"
      const m = /^\s*(?:lv\.?\s*)?([0-9]{1,2})\s+([0-9]{1,3}(?:\.[0-9]{1,3})?)\s*%?\s*$/i
        .exec(String(text === undefined || text === null ? '' : text));
      if (!m) return null;
      const lv = { review: { id: id, action: 'fix', value: { level: Number(m[1]), pct: Number(m[2]) } } };
      return validOcrBody(lv) ? lv : null;
    }
    const s = String(text === undefined || text === null ? '' : text).replace(/[,.\s]/g, '');
    if (!/^[0-9]{1,14}$/.test(s)) return null;
    const v = Number(s);
    if (kind === 'buff' && (v < 1 || v > 43200)) return null;
    if (kind === 'gear' && v > 999) return null;
    const body = { review: { id: id, action: 'fix', value: v } };
    return validOcrBody(body) ? body : null;
  }

  // ---- OCR loot import (plan 040) ----
  // POST /api/ocr {loot: {shot, spot}} -> {shot, spot, text, rows: [{name,
  // count, confidence}], unmatched}. Rows only pre-fill the Grind loot counts;
  // the operator checks them and logs with Stop + log as usual.

  const OCR_SPOT_RE = /^[a-z0-9-]{1,40}$/;   // server grind ID_RE
  const OCR_LOW_CONF = 1;                    // anything short of an exact name read is flagged
  const OCR_UNMATCHED_MAX = 50;

  function normalizeLootOcr(d) {
    if (!plainObject(d)) return null;
    const rows = [];
    const seen = [];
    (Array.isArray(d.rows) ? d.rows : []).forEach(function (r) {
      if (!plainObject(r) || !validName(r.name)) return;
      const n = r.name.toLowerCase();
      if (seen.indexOf(n) >= 0) return;
      seen.push(n);
      const count = Number.isInteger(r.count) && inRange(r.count, LOOT_COUNT) ? r.count : null;
      const conf = isNum(r.confidence) && r.confidence >= 0 && r.confidence <= 1 ? r.confidence : 0;
      rows.push({ name: r.name, count: count, confidence: conf });
    });
    const unmatched = (Array.isArray(d.unmatched) ? d.unmatched : [])
      .filter(function (t) { return typeof t === 'string' && t.length > 0; })
      .slice(0, OCR_UNMATCHED_MAX).map(function (t) { return t.slice(0, 200); });
    return { shot: typeof d.shot === 'string' ? d.shot : '', rows: rows, unmatched: unmatched };
  }

  // Rows -> {counts: {itemName: 'digits'}, low: [itemName], filled, missing}
  // keyed by the spot's own item names (case-insensitive match), so the counts
  // land in the existing loot inputs. A row without a count or an item the
  // list no longer has is reported, never invented.
  function lootImportCounts(rows, itemNames) {
    const byLower = {};
    (Array.isArray(itemNames) ? itemNames : []).forEach(function (n) {
      if (typeof n === 'string') byLower[n.toLowerCase()] = n;
    });
    const out = { counts: {}, low: [], filled: 0, missing: [] };
    (Array.isArray(rows) ? rows : []).forEach(function (r) {
      const name = r && typeof r.name === 'string' ? byLower[r.name.toLowerCase()] : undefined;
      if (name === undefined || r.count === null || !Number.isInteger(r.count)) {
        if (r && typeof r.name === 'string') out.missing.push(r.name);
        return;
      }
      out.counts[name] = String(r.count);
      out.filled += 1;
      if (!(r.confidence >= OCR_LOW_CONF)) out.low.push(name);
    });
    return out;
  }

  function lootImportText(shot, imp, unmatched) {
    let t = imp.filled ? 'filled ' + imp.filled + ' from ' + shot + ' - check, then Stop + log'
      : 'no loot counts read from ' + shot;
    if (imp.low.length) t += '; check ? rows';
    if (imp.missing.length) t += '; no count: ' + imp.missing.join(', ');
    if (unmatched && unmatched.length) t += '; ' + unmatched.length + ' line(s) not on this list';
    return t;
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

  Object.assign(K, { GAME_STATES: GAME_STATES, GAME_LABELS: GAME_LABELS,
    GAME_SHOTS_MAX: GAME_SHOTS_MAX, GAME_EVENT_MAX: GAME_EVENT_MAX,
    GAME_CONFIG_HINT: GAME_CONFIG_HINT, gameStateLabel: gameStateLabel, gameTimeMs: gameTimeMs,
    gameEventText: gameEventText, gameShot: gameShot, normalizeGame: normalizeGame, pad2: pad2,
    fmtClock: fmtClock, gameSinceText: gameSinceText, OCR_NAME_MAX: OCR_NAME_MAX,
    OCR_TEXT_MAX: OCR_TEXT_MAX, validOcrBody: validOcrBody, validShotName: validShotName,
    OCR_REVIEW_RE: OCR_REVIEW_RE, OCR_UNDO_RE: OCR_UNDO_RE, OCR_AUTO_KINDS: OCR_AUTO_KINDS,
    OCR_REVIEW_MAX: OCR_REVIEW_MAX, OCR_LEVEL_MAX: OCR_LEVEL_MAX, ocrPct: ocrPct,
    ocrLevelValue: ocrLevelValue, ocrBookValue: ocrBookValue, ocrAutoValue: ocrAutoValue,
    validOcrReview: validOcrReview, ocrAutoRow: ocrAutoRow, normalizeOcrAuto: normalizeOcrAuto,
    ocrSilverHRow: ocrSilverHRow, ocrSilverH: ocrSilverH, ocrValueText: ocrValueText,
    ocrAutoLabel: ocrAutoLabel, ocrFixHint: ocrFixHint, ocrAutoHead: ocrAutoHead,
    ocrFixBody: ocrFixBody, OCR_SPOT_RE: OCR_SPOT_RE, OCR_LOW_CONF: OCR_LOW_CONF,
    OCR_UNMATCHED_MAX: OCR_UNMATCHED_MAX, normalizeLootOcr: normalizeLootOcr,
    lootImportCounts: lootImportCounts, lootImportText: lootImportText,
    normalizeOcr: normalizeOcr, ocrBuffBody: ocrBuffBody, ocrSilverInput: ocrSilverInput,
    ocrBuffLabel: ocrBuffLabel });
  return function link() {};
});
