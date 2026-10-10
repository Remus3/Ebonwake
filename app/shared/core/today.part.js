/* EW shared pure logic, part `today` (plan 107 split of ewcore.js):
    Today checklist, per-item reset rules, gates, weekly plan.
   Installed in order by ../ewcore.js, which re-exports the public names as
   EWCore. Loaded as a plain script before ewcore.js (window.EWCoreParts) and by
   require() under node --test. No DOM, no Electron. */
(function (root, install) {
  if (typeof module !== 'undefined' && module.exports) module.exports = install;
  else (root.EWCoreParts = root.EWCoreParts || {}).today = install;
})(typeof self !== 'undefined' ? self : this, function (K) {
  'use strict';

  // from earlier parts
  const { fmtDuration, isInt, nextDailyReset, nextWeeklyReset, plainObject, zoneOf,
    zoneOffsetMin } = K;
  // from later parts, bound by link() once every part is installed
  let pad2;

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

  // Plan 078: an item rule's next reset in opts.zone (default local) ->
  // {text: 'Sat 17:00' | '22:00', title: 'UTC Sun 00:00'}, or null. The
  // stored rule stays UTC; the weekday is the zone's day of that reset.
  function resetRuleView(r, now, opts) {
    const rule = validResetRule(r);
    if (!rule) return null;
    const at = nextResetOf(rule, now);
    const off = zoneOffsetMin(zoneOf(opts), at);
    if (off === null) return null;
    const d = new Date(at + off * 60000);
    const day = rule.every === 'week' ? WEEKDAYS[(d.getUTCDay() + 6) % 7] + ' ' : '';
    return { text: day + pad2(d.getUTCHours()) + ':' + pad2(d.getUTCMinutes()),
      title: 'UTC ' + fmtResetRule(rule).replace(/ UTC$/, '') };
  }

  // {text: 'resets Sat 17:00 in 2d 4h', title: 'UTC Sun 00:00'}, or null.
  function resetCountdownView(r, now, opts) {
    const v = resetRuleView(r, now, opts);
    if (!v) return null;
    return { text: 'resets ' + v.text + ' in ' + fmtDuration(nextResetOf(validResetRule(r), now) - now), title: v.title };
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
      const next = { ticked_at: iso, done: iso !== null };
      if ('by' in it) next.by = null; // plan 068: an operator toggle is never auto
      return Object.assign({}, it, next);
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

  Object.assign(K, { DAY_MS: DAY_MS, KINDS: KINDS, SLUG: SLUG, TITLE_MAX: TITLE_MAX,
    lastDailyReset: lastDailyReset, lastWeeklyReset: lastWeeklyReset, AT: AT,
    WEEKDAYS: WEEKDAYS, RULE_KIND: RULE_KIND, validResetRule: validResetRule,
    itemRule: itemRule, lastResetOf: lastResetOf, nextResetOf: nextResetOf,
    fmtResetRule: fmtResetRule, fmtResetCountdown: fmtResetCountdown,
    resetRuleView: resetRuleView, resetCountdownView: resetCountdownView, GATE_REQ: GATE_REQ,
    GATE_NAME: GATE_NAME, fmtWeeklyGate: fmtWeeklyGate, GATE_ORDER: GATE_ORDER,
    weeklyPlan: weeklyPlan, resetKey: resetKey, resetPresets: resetPresets,
    presetForm: presetForm, isDone: isDone, isoDateMs: isoDateMs, eventDaysLeft: eventDaysLeft,
    fmtDaysLeft: fmtDaysLeft, groupItems: groupItems, withTick: withTick,
    validTitle: validTitle, onlyKeys: onlyKeys, validTodayBody: validTodayBody,
    parseTodayForm: parseTodayForm });
  return function link() {
    pad2 = K.pad2;
  };
});
