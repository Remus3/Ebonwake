/* EW shared pure logic, part `leveling` (plan 107 split of ewcore.js):
    Leveling, level-gated deadlines, Combat Secret Book ledger.
   Installed in order by ../ewcore.js, which re-exports the public names as
   EWCore. Loaded as a plain script before ewcore.js (window.EWCoreParts) and by
   require() under node --test. No DOM, no Electron. */
(function (root, install) {
  if (typeof module !== 'undefined' && module.exports) module.exports = install;
  else (root.EWCoreParts = root.EWCoreParts || {}).leveling = install;
})(typeof self !== 'undefined' ? self : this, function (K) {
  'use strict';

  // from earlier parts
  const { ISO_TS, LEVEL, XP_PCT, exact, inRange, isNum, pad2, parseLocalDateTime,
    parseLocalTime, patchNote, plainObject, sinceFetch, utcMsOf, validRef, wholeIn, zoneName,
    zoneOffsetMin } = K;

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
  // Plan 060 Combat Secret Books: server xpbooks.SIZES / MAX_N.
  const BOOK_SIZES = ['small', 'medium', 'large', 'xl'];
  const BOOK_SHORT = { small: 'S', medium: 'M', large: 'L', xl: 'XL' };
  const BOOK_N_MAX = 99;

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
    if (k === 'book_add') {
      const keys = Object.keys(plainObject(v) ? v : {});
      return plainObject(v) && BOOK_SIZES.indexOf(v.size) >= 0 && Number.isInteger(v.n) && v.n !== 0 &&
        v.n >= -BOOK_N_MAX && v.n <= BOOK_N_MAX && keys.every(function (x) { return ['size', 'n', 'activity'].indexOf(x) >= 0; }) &&
        (!('activity' in v) || validRef(v.activity, EPOCH_ID_RE));
    }
    if (k === 'book_use') {
      return exact(v, ['size', 'pct_before', 'pct_after']) && BOOK_SIZES.indexOf(v.size) >= 0 &&
        validXpPct(v.pct_before) && validXpPct(v.pct_after) && v.pct_before !== v.pct_after;
    }
    if (k === 'book_del') return Number.isInteger(v) && v >= 0;
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
  // (Monday=0) as numbers or strings; times are UTC HH:MM, or (plan 048) wall
  // times in f.zone 'local' | 'pt' at f.now, stored as UTC with the days moved
  // when the start crosses midnight.
  function parseHotForm(form) {
    const f = form || {};
    if (f.zone !== undefined) {
      const opts = { zone: f.zone, now: f.now };
      const a = parseLocalTime(f.start, opts);
      const b = parseLocalTime(f.end, opts);
      if (zoneOffsetMin(f.zone, 0) === null) return { ok: false, error: 'unknown time zone' };
      if (!a || !b) return { ok: false, error: 'start / end: a time like 21:00 or 9pm (' + zoneName(f.zone) + ')' };
      const days = (Array.isArray(f.days) ? f.days : []).map(function (d) {
        const n = wholeIn(d, [0, 6]);
        return n === null ? d : (n + a.shift + 7) % 7;
      });
      return parseHotForm(Object.assign({}, f, { zone: undefined, days: days, start: a.utc, end: b.utc }));
    }
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
  // "YYYY-MM-DD HH:MM" (plan 048: or wall time in f.zone 'local' | 'pt'); a
  // blank id is slugged from the label (an existing id corrects that epoch,
  // e.g. the confirmed live maintenance time).
  function parseEpochForm(form) {
    const f0 = form || {};
    if (f0.zone !== undefined && f0.zone !== 'utc') {
      const iso = parseLocalDateTime(typeof f0.start === 'string' ? f0.start : '', { zone: f0.zone });
      if (!iso) return { ok: false, error: 'start must be YYYY-MM-DD HH:MM (' + zoneName(f0.zone) + ')' };
      return parseEpochForm(Object.assign({}, f0, { zone: 'utc', start: iso.slice(0, 16) }));
    }
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
      source: typeof e.source === 'string' ? e.source : '', verified: e.verified === true, tracked: e.tracked === true,
      patch: patchNote(e.patch) };  // plan 085
  }

  // Plan 085: source tooltip of an epoch brief: "source: ..." + the patch-notes line.
  function epochTitle(e) {
    if (!plainObject(e)) return '';
    return [typeof e.source === 'string' && e.source ? 'source: ' + e.source : '',
      e.patch && typeof e.patch.text === 'string' ? e.patch.text : ''].filter(function (s) { return s; }).join('\n');
  }

  const LEVEL_SOURCES = ['typed', 'profile', 'ocr'];  // plan 066: ocr = read from a screenshot

  function normalizeLeveling(d) {
    if (!plainObject(d) || !Array.isArray(d.milestones)) return null;
    const hot = plainObject(d.hot) ? d.hot : {};
    const nx = hot.next;
    return {
      now: typeof d.now === 'string' ? d.now : null,
      level: inRange(d.level, LEVEL) ? d.level : null,
      // Plan 041: pct is null when a profile marker raised the level.
      pct: validXpPct(d.pct) ? d.pct : null,
      level_source: LEVEL_SOURCES.indexOf(d.level_source) >= 0 ? d.level_source : null,
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
      // Plan 064: dated windows auto-added from official Hot Time notices.
      hot_auto: (Array.isArray(d.hot_auto) ? d.hot_auto : []).filter(function (w) {
        return plainObject(w) && typeof w.id === 'string' && utcMsOf(w.start) !== null &&
          utcMsOf(w.end) !== null && isNum(w.pct);
      }),
      // Plan 018 XP epochs: the rate only counts samples since `epoch`.
      epoch: epochBrief(d.epoch),
      epoch_next: epochBrief(d.epoch_next),
      epochs: (Array.isArray(d.epochs) ? d.epochs : []).map(epochBrief).filter(function (e) { return e !== null; }),
      epoch_error: typeof d.epoch_error === 'string' ? d.epoch_error : null,
      kill_xp_cap: typeof d.kill_xp_cap === 'string' ? d.kill_xp_cap : null,
      // Plan 088: the grind session's in-game zone read (cap-bound flag), or null.
      zone_cap: plainObject(d.zone_cap) && typeof d.zone_cap.zone_name === 'string' ?
        { zone_name: d.zone_cap.zone_name, cap_bound: d.zone_cap.cap_bound === true,
          source: typeof d.zone_cap.source === 'string' ? d.zone_cap.source : null } : null,
      // Plan 024 level-gated deadlines, already decorated with state by the server.
      deadlines: (Array.isArray(d.deadlines) ? d.deadlines : []).map(deadlineBrief).filter(function (x) { return x !== null; }),
      // Plan 060 Combat Secret Book ledger (null on a pre-060 server).
      books: normalizeBooks(d.books),
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

  // ---- plan 060: Combat Secret Book ledger ----
  function normalizeBooks(b) {
    if (!plainObject(b) || typeof b.available !== 'boolean') return null;
    const dl = b.deadline;
    if (!b.available) {
      return { available: false, min_level: inRange(b.min_level, LEVEL) ? b.min_level : 60,
        error: typeof b.error === 'string' ? b.error : null,
        deadline: plainObject(dl) && typeof dl.label === 'string' && inRange(dl.needs_level, LEVEL) &&
          typeof dl.enrol_by_utc === 'string' ? { label: dl.label, needs_level: dl.needs_level, enrol_by_utc: dl.enrol_by_utc } : null };
    }
    const owned = {};
    const per = {};
    const pb = plainObject(b.per_book) ? b.per_book : {};
    const ow = plainObject(b.owned) ? b.owned : {};
    const tn = plainObject(b.to_next) ? b.to_next : null;
    const toNext = tn ? {} : null;
    BOOK_SIZES.forEach(function (s) {
      owned[s] = Number.isInteger(ow[s]) && ow[s] >= 0 ? ow[s] : 0;
      const p = pb[s];
      per[s] = plainObject(p) && isNum(p.pct) ? { pct: p.pct, observed: p.observed === true,
        uses: Number.isInteger(p.uses) ? p.uses : 0, flag: typeof p.flag === 'string' ? p.flag : null } : null;
      if (toNext) toNext[s] = Number.isInteger(tn[s]) && tn[s] >= 0 ? tn[s] : null;
    });
    const named = function (x) { return plainObject(x) && typeof x.name === 'string'; };
    return {
      available: true, error: null, owned: owned, per_book: per, flagged: b.flagged === true,
      owned_pct: isNum(b.owned_pct) ? b.owned_pct : 0, to_next: toNext,
      pct_week: isNum(b.pct_week) ? b.pct_week : 0,
      eta_next_with_books_s: isNum(b.eta_next_with_books_s) ? b.eta_next_with_books_s : null,
      sizes: (Array.isArray(b.sizes) ? b.sizes : []).filter(function (x) { return named(x) && BOOK_SIZES.indexOf(x.id) >= 0; }),
      activities: (Array.isArray(b.activities) ? b.activities : []).filter(function (x) {
        return named(x) && typeof x.activity === 'string' && EPOCH_ID_RE.test(x.activity);
      }),
      used: (Array.isArray(b.used) ? b.used : []).filter(function (u) {
        return plainObject(u) && Number.isInteger(u.index) && BOOK_SIZES.indexOf(u.size) >= 0 && isNum(u.gain);
      })
    };
  }

  function fmtBookPct(v) {
    return isNum(v) ? String(Math.round(v * 100) / 100) : '0';
  }

  // "Books: +22.5 % owned, +1 %/week expected (Lv 66 values, verify)"; below
  // Lv 60 "books from Lv 60" (+ the Lv 60 deadline, e.g. Olvia Academy by Nov 5).
  function booksLine(b) {
    const n = normalizeBooks(b);
    if (!n) return '';
    if (!n.available) {
      if (n.error) return 'book table: ' + n.error;
      const dl = n.deadline;
      return 'books from Lv ' + n.min_level + (dl ? ' - ' + dl.label + ' Lv ' + dl.needs_level + ' by ' + fmtMonthDay(dl.enrol_by_utc) : '');
    }
    const flags = BOOK_SIZES.map(function (s) { return n.per_book[s] && n.per_book[s].flag; }).filter(function (f) { return f; });
    return 'Books: +' + fmtBookPct(n.owned_pct) + ' % owned, +' + fmtBookPct(n.pct_week) + ' %/week expected' +
      (flags.length ? ' (' + flags[0].replace(/ value, verify$/, ' values, verify') + ')' : '');
  }

  // "to next: 8 L / 4 XL / 60 M / 300 S" (largest size first), '' without a pct.
  function booksToNextText(b) {
    const n = normalizeBooks(b);
    if (!n || !n.available || !n.to_next) return '';
    const parts = BOOK_SIZES.slice().reverse().filter(function (s) { return n.to_next[s] !== null; })
      .map(function (s) { return n.to_next[s] + ' ' + BOOK_SHORT[s]; });
    return parts.length ? 'to next: ' + parts.join(' / ') : '';
  }

  // Book forms -> {book_add} / {book_use} body or an error. add: size, n
  // ("3", "-1" corrects), optional activity id; use: size + XP % before / after.
  function parseBookForm(form) {
    const f = form || {};
    const size = typeof f.size === 'string' ? f.size : '';
    if (BOOK_SIZES.indexOf(size) < 0) return { ok: false, error: 'pick a book size' };
    if (f.op === 'use') {
      const pct = function (v) {
        const t = (v === undefined || v === null ? '' : String(v)).trim().replace(/%$/, '').trim().replace(',', '.');
        return PCT_RE.test(t) ? Number(t) : NaN;
      };
      const a = pct(f.before);
      const z = pct(f.after);
      if (!validXpPct(a) || !validXpPct(z)) return { ok: false, error: 'XP % before / after: 0-100, up to 3 decimals' };
      if (a === z) return { ok: false, error: 'XP % after must differ from before' };
      return { ok: true, body: { book_use: { size: size, pct_before: a, pct_after: z } } };
    }
    const t = (f.n === undefined || f.n === null ? '' : String(f.n)).trim();
    const n = /^[+-]?\d{1,2}$/.test(t) ? Number(t) : NaN;
    if (!Number.isInteger(n) || n === 0) return { ok: false, error: 'count: a whole number -' + BOOK_N_MAX + '..' + BOOK_N_MAX + ', not 0' };
    const body = { size: size, n: n };
    const act = typeof f.activity === 'string' ? f.activity.trim() : '';
    if (act) {
      if (!EPOCH_ID_RE.test(act)) return { ok: false, error: 'bad activity' };
      body.activity = act;
    }
    return { ok: true, body: { book_add: body } };
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

  // Plan 041: "Lv 52 37.5%" for a typed sample (`exact` keeps every decimal),
  // "Lv 61 (profile)" when a profile marker raised the level (XP percent of
  // that level unknown). Never prints null.
  function fmtLevelLine(d, exact) {
    if (!plainObject(d) || !inRange(d.level, LEVEL)) return 'no XP sample yet';
    if (validXpPct(d.pct)) {
      return 'Lv ' + d.level + ' ' + (exact ? String(d.pct) : (Math.floor(d.pct * 10) / 10).toFixed(1)) + '%';
    }
    return 'Lv ' + d.level + (d.level_source === 'profile' ? ' (profile)' : '');
  }

  // Overlay one-liner: "Lv 52 37.5% | 4.1 %/h | ETA 15h12m | HOT 1h03m +50%".
  function levelingLine(body, fetchedMs, now) {
    const d = normalizeLeveling(body);
    if (!d || d.level === null) return 'no XP sample yet';
    const el = sinceFetch(fetchedMs, now);
    const parts = [fmtLevelLine(d)];
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

  Object.assign(K, { PCT_RE: PCT_RE, HHMM_RE: HHMM_RE, HOT_ID_RE: HOT_ID_RE,
    HOT_LABEL_MAX: HOT_LABEL_MAX, MILESTONES_MAX: MILESTONES_MAX, DAY_NAMES: DAY_NAMES,
    validXpPct: validXpPct, validLabel: validLabel, validDays: validDays,
    EPOCH_ID_RE: EPOCH_ID_RE, EPOCH_SOURCE_MAX: EPOCH_SOURCE_MAX, BOOK_SIZES: BOOK_SIZES,
    BOOK_SHORT: BOOK_SHORT, BOOK_N_MAX: BOOK_N_MAX, validAscii: validAscii,
    validMilestones: validMilestones, validLevelingBody: validLevelingBody,
    parseSampleForm: parseSampleForm, parseHotForm: parseHotForm,
    parseMilestones: parseMilestones, parseEpochForm: parseEpochForm, epochText: epochText,
    fmtRate: fmtRate, fmtEta: fmtEta, fmtDays: fmtDays, epochBrief: epochBrief,
    epochTitle: epochTitle, LEVEL_SOURCES: LEVEL_SOURCES, normalizeLeveling: normalizeLeveling,
    DEADLINE_STATES: DEADLINE_STATES, MONTH_NAMES: MONTH_NAMES, deadlineBrief: deadlineBrief,
    fmtMonthDay: fmtMonthDay, deadlinePill: deadlinePill, deadlineLine: deadlineLine,
    deadlineAlert: deadlineAlert, normalizeBooks: normalizeBooks, fmtBookPct: fmtBookPct,
    booksLine: booksLine, booksToNextText: booksToNextText, parseBookForm: parseBookForm,
    hotLive: hotLive, fmtLevelLine: fmtLevelLine, levelingLine: levelingLine });
  return function link() {};
});
