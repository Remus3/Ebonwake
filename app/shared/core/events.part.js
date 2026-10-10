/* EW shared pure logic, part `events` (plan 107 split of ewcore.js):
    Events, claim windows, coupons, login days, event currency, notice suggestions.
   Installed in order by ../ewcore.js, which re-exports the public names as
   EWCore. Loaded as a plain script before ewcore.js (window.EWCoreParts) and by
   require() under node --test. No DOM, no Electron. */
(function (root, install) {
  if (typeof module !== 'undefined' && module.exports) module.exports = install;
  else (root.EWCoreParts = root.EWCoreParts || {}).events = install;
})(typeof self !== 'undefined' ? self : this, function (K) {
  'use strict';

  // from earlier parts
  const { TITLE_MAX, exact, fmtDuration, fmtLocal, isNum, onlyKeys, plainObject, sinceFetch,
    utcMsOf, validRef, validTitle, zoneOf, zoneOffsetMin } = K;
  // from later parts, bound by link() once every part is installed
  let MONTH_NAMES, intIn;

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
      // Plan 086: the claim window of its notice, counted down locally too.
      const claim = eventDateMs(it.claim_until, true);
      if (claim !== null) {
        r.claim_left_s = Math.max(0, Math.floor((claim - now) / 1000));
        r.claim_open = it.claimed !== true && claim > now;
      } else {
        delete r.claim_left_s;
        delete r.claim_open;
      }
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

  // ---- plan 086: reward claim windows ----
  const CLAIM_TIMER_S = 7 * 86400;     // Home Timers lists claim windows inside 7 days
  const CLAIM_TOAST = [24, 23];        // one T-24 h toast: [hours mark, window floor]
  const GROUP_NO_RE = /[?&]groupContentNo=([0-9]{1,9})(?:&|$)/;

  // Where the claim sentence says the rewards wait (= whatnow.claim_place).
  function claimPlace(text) {
    const t = typeof text === 'string' ? text.toLowerCase() : '';
    const mail = t.indexOf('mail') >= 0;
    const safe = t.indexOf('safe') >= 0;
    return mail && !safe ? 'Mail' : safe && !mail ? 'Safe' : 'Mail / Safe';
  }

  // An eventRows row -> {text 'claim by Oct 29', place, left_s, left, title,
  // at} while its claim window is open (opts.zone: 'local' default, 'utc',
  // 'pt'), else null.
  function claimLine(r, opts) {
    if (!plainObject(r) || r.claim_open !== true) return null;
    const ms = eventDateMs(r.claim_until, true);
    const off = ms === null ? null : zoneOffsetMin(zoneOf(opts), ms);
    if (off === null) return null;
    const d = new Date(ms + off * 60000);
    const place = claimPlace(r.claim_text);
    return {
      text: 'claim by ' + MONTH_NAMES[d.getUTCMonth()] + ' ' + d.getUTCDate(), place: place,
      left_s: r.claim_left_s, left: fmtLeft(r.claim_left_s), at: ms,
      title: (typeof r.claim_text === 'string' ? r.claim_text + '\n' : '') + 'rewards wait in ' +
        place + ' until ' + r.claim_until
    };
  }

  // Open claim windows, one per notice (url, else title), soonest first.
  function claimRows(items, fetchedMs, now) {
    const seen = {};
    return eventRows(items, fetchedMs, now).filter(function (r) {
      if (r.claim_open !== true) return false;
      const k = typeof r.url === 'string' ? r.url : r.title;
      if (seen[k]) return false;
      seen[k] = true;
      return true;
    }).sort(function (a, b) { return a.claim_left_s - b.claim_left_s; });
  }

  // POST /api/events body acking a row's claim window, or null (no notice link).
  function claimBody(r) {
    const m = plainObject(r) && typeof r.url === 'string' ? GROUP_NO_RE.exec(r.url) : null;
    const n = m ? Number(m[1]) : null;
    return n !== null && validNoticeNo(n) ? { claimed: n } : null;
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
    ends: function (v) { return eventDateMs(v, true) !== null; },
    login_rule: function (v) { return validLoginRule(v) && onlyKeys(v, ['days_needed', 'min_minutes', 'weekend_minutes']); }
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
    if (k === 'dismiss_notice' || k === 'undo_notice' || k === 'claimed') return validNoticeNo(v); // 086: claimed
    if (k === 'login_mark') return exact(v, ['date', 'on']) && typeof v.date === 'string' && ISO_DAY.test(v.date) && typeof v.on === 'boolean';
    if (k === 'done') return exact(v, ['id', 'done']) && validRef(v.id, EVENT_ID_RE) && typeof v.done === 'boolean';
    if (k === 'add') {
      if (!plainObject(v) || !onlyKeys(v, ['kind', 'title', 'code', 'rewards', 'starts', 'ends', 'url', 'login_rule'])) return false;
      if (EVENT_KINDS.indexOf(v.kind) < 0 || !validTitle(v.title)) return false;
      if ((v.kind === 'coupon') !== ('code' in v)) return false;
      return eventFieldsOk(v, false);
    }
    if (k === 'edit') {
      if (!plainObject(v) || !onlyKeys(v, ['id', 'title', 'code', 'rewards', 'starts', 'ends', 'url', 'login_rule'])) return false;
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

  // ---- plan 075: login-day reward tracker (server `login_days` block) ----

  // intIn: the stacks section's definition (deadeye part) is the one in force;
  // the shadowed duplicate that sat here was dropped by plan 107.

  function validLoginRule(r) {
    return plainObject(r) && intIn(r.days_needed, 1, 60) && intIn(r.min_minutes, 0, 600) &&
      (r.weekend_minutes === undefined || intIn(r.weekend_minutes, 0, 600));
  }

  // `login_days` -> {byId: {item id: row}, suggest: {item id: suggestion}}; bad rows dropped.
  function loginDays(block) {
    const b = plainObject(block) ? block : {};
    const out = { byId: {}, suggest: {}, loggedIn: b.logged_in === true };
    (Array.isArray(b.rows) ? b.rows : []).forEach(function (r) {
      if (!plainObject(r) || typeof r.id !== 'string' || !intIn(r.credited, 0, 60) ||
        !intIn(r.needed, 1, 60) || !intIn(r.days_left, 0, 100000)) return;
      out.byId[r.id] = r;
    });
    (Array.isArray(b.suggest) ? b.suggest : []).forEach(function (s) {
      if (plainObject(s) && typeof s.id === 'string' && validLoginRule(s.rule)) out.suggest[s.id] = s;
    });
    return out;
  }

  // "Login days 6/14 - 22 days left" (mirrors server logindays.label).
  function loginDayText(r) {
    const left = r.days_left;
    const tail = left <= 0 ? 'ended' : left + ' day' + (left === 1 ? '' : 's') + ' left';
    return 'Login days ' + r.credited + '/' + r.needed + ' - ' + tail;
  }

  // "38/60 min today" for a minute rule while logged in (or once some minutes count), else null.
  function loginTodayText(r, loggedIn) {
    if (!plainObject(r) || !intIn(r.min_minutes, 1, 600) || r.today_done === true && !loggedIn) return null;
    if (!loggedIn && !(r.today_minutes > 0)) return null;
    const m = intIn(r.today_minutes, 0, 1440) ? Math.min(r.today_minutes, r.min_minutes) : 0;
    return m + '/' + r.min_minutes + ' min today';
  }

  // {cls, text} pill for a tracked row.
  function loginPill(r) {
    if (r.complete === true) return { cls: 'ok', text: 'complete' };
    if (r.lost === true) return { cls: 'bad', text: 'lost' };
    if (r.at_risk === true) return { cls: 'warn', text: 'at risk' };
    if (r.today_done === true) return { cls: 'ok', text: 'today done' };
    return { cls: 'unknown', text: 'log in today' };
  }

  function loginWeekendText(r) {
    const w = plainObject(r) ? r.weekend : null;
    if (!plainObject(w) || !intIn(w.credited, 0, 1000) || !intIn(w.total, 0, 1000)) return null;
    return 'weekend bonus ' + w.credited + '/' + w.total + ' (' + w.minutes + ' min)';
  }

  // Suggestion -> POST /api/events body (one click to track), or null.
  function loginTrackBody(s) {
    if (!plainObject(s) || typeof s.id !== 'string' || !validLoginRule(s.rule)) return null;
    return { edit: { id: s.id, login_rule: s.rule } };
  }

  // "I logged in that day" -> POST body, or null for a bad date.
  function loginMarkBody(date) {
    return typeof date === 'string' && ISO_DAY.test(date) ? { login_mark: { date: date, on: true } } : null;
  }

  // ---- plan 095: event currency planner (server `currency` block, Today `event_rows`) ----

  const CUR_NUM_MAX = 1e9;
  function curNum(v) { return intIn(v, 0, CUR_NUM_MAX); }
  function curText(s, n) { return typeof s === 'string' && s.length > 0 && s.length <= n; }

  // `currency` ({events: [...]}) -> valid rows; wishlist / exchange junk rows dropped.
  function currencyRows(block) {
    const b = plainObject(block) ? block : {};
    const out = [];
    (Array.isArray(b.events) ? b.events : []).forEach(function (r) {
      if (!plainObject(r) || typeof r.event !== 'string' || !/^[0-9]{1,9}$/.test(r.event)) return;
      if (!curText(r.unit, 30) || !curText(r.currency, 120) || typeof r.ends !== 'string' ||
        !/^\d{4}-\d{2}-\d{2}T/.test(r.ends)) return;
      if (!['earned', 'guaranteed', 'wishlist_cost', 'shortfall'].every(function (k) { return curNum(r[k]); })) return;
      const wish = (Array.isArray(r.wishlist) ? r.wishlist : []).filter(function (w) {
        return plainObject(w) && curText(w.item, 120) && intIn(w.qty, 1, 999) && curNum(w.subtotal);
      });
      const exchange = (Array.isArray(r.exchange) ? r.exchange : []).filter(function (x) {
        return plainObject(x) && curText(x.item, 120) && intIn(x.cost, 1, CUR_NUM_MAX) && intIn(x.limit, 1, 999);
      });
      out.push(Object.assign({}, r, { wishlist: wish, exchange: exchange,
        earned_src: r.earned_src === 'typed' ? 'typed' : 'log' }));
    });
    return out;
  }

  // "Seals: 120 earned, 640 guaranteed by 11-05, wishlist 590 - covered"
  // (mirrors server eventcurrency.label).
  function currencyText(r) {
    const unit = r.unit.charAt(0).toUpperCase() + r.unit.slice(1);
    const have = r.earned_src === 'typed' ? r.earned + ' in hand (typed)' : r.earned + ' earned';
    let out = unit + ': ' + have + ', ' + r.guaranteed + ' guaranteed by ' + r.ends.slice(5, 10);
    if (r.wishlist_cost > 0) {
      out += ', wishlist ' + r.wishlist_cost + ' - ' +
        (r.shortfall > 0 ? r.shortfall + ' short - from drops' : 'covered');
    }
    return out;
  }

  // {cls, text} pill: covered / N short / no wishlist.
  function currencyPill(r) {
    if (!(r.wishlist_cost > 0)) return { cls: 'unknown', text: 'no wishlist' };
    return r.shortfall > 0 ? { cls: 'warn', text: r.shortfall + ' short' } : { cls: 'ok', text: 'covered' };
  }

  // "Mythical Censer x2 (160) - use before 11-12"
  function currencyWishText(w) {
    return w.item + ' x' + w.qty + ' (' + w.subtotal + ')' + (curText(w.use_before, 40) ? ' - ' + w.use_before : '');
  }

  // Wishlist row -> POST /api/events body; qty 0 removes; null when out of 0..limit.
  function currencyWishBody(event, x, qty) {
    const n = typeof qty === 'string' && /^\d{1,3}$/.test(qty.trim()) ? Number(qty.trim()) : qty;
    if (typeof event !== 'string' || !plainObject(x) || !curText(x.item, 120) || !intIn(n, 0, x.limit)) return null;
    return { wish: { event: event, item: x.item, qty: n } };
  }

  // Typed balance -> POST body; '' clears it; null for junk.
  function currencyBalanceBody(event, value) {
    if (typeof event !== 'string') return null;
    const v = typeof value === 'string' ? value.trim() : value;
    if (v === '' || v === null) return { currency_balance: { event: event, value: null } };
    const n = typeof v === 'string' && /^\d{1,7}$/.test(v) ? Number(v) : v;
    return intIn(n, 0, 1e7) ? { currency_balance: { event: event, value: n } } : null;
  }

  // GET /api/today event_rows -> valid rows (weekly / once, inside the event window).
  function eventGameRows(d) {
    const raw = plainObject(d) && Array.isArray(d.event_rows) ? d.event_rows : [];
    return raw.filter(function (r) {
      return plainObject(r) && typeof r.key === 'string' && /^[0-9]{1,9}:[a-z0-9_-]{1,40}$/.test(r.key) &&
        validTitle(r.name) && (r.kind === 'weekly' || r.kind === 'once') && curNum(r.amount) && curText(r.unit, 30);
    }).map(function (r) {
      return { key: r.key, name: r.name, kind: r.kind, done: r.done === true,
        text: '+' + r.amount + ' ' + r.unit + (r.kind === 'once' ? ' (once)' : ' / week'),
        reset: typeof r.next_reset === 'string' && Number.isFinite(Date.parse(r.next_reset)) ? Date.parse(r.next_reset) : null };
    });
  }

  function eventTickBody(key, on) {
    return typeof key === 'string' ? (on ? { event_tick: key } : { event_untick: key }) : null;
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

  // ---- Event-notice suggestions (plan 059) ----
  // GET /api/events `suggested_events`: windows read from the official Events
  // board (robots.txt-gated, server side). Suggest only: add posts a normal
  // event entry with the source link; dismiss is remembered by the server.

  function validNoticeNo(v) { return Number.isInteger(v) && v > 0 && v < 1e9; }

  // Valid candidates not already an item (same url, or same title + ends).
  function noticeRows(suggested, items) {
    const s = plainObject(suggested) ? suggested : {};
    const list = Array.isArray(s.candidates) ? s.candidates : [];
    const urls = {};
    const known = {};
    (Array.isArray(items) ? items : []).forEach(function (it) {
      if (!plainObject(it)) return;
      if (typeof it.url === 'string') urls[it.url] = true;
      const e = utcMsOf(it.ends);
      if (typeof it.title === 'string' && e !== null) known[it.title + '\n' + e] = true;
    });
    const seen = {};
    const out = [];
    list.forEach(function (c) {
      if (!plainObject(c) || !validNoticeNo(c.group_no) || seen[c.group_no]) return;
      if (!validTitle(c.title) || !validUrl(c.url)) return;
      const s0 = utcMsOf(c.starts);
      const e0 = utcMsOf(c.ends);
      if (s0 === null || e0 === null || s0 > e0) return;
      if (urls[c.url] || known[c.title + '\n' + e0]) return;
      seen[c.group_no] = true;
      out.push({
        group_no: c.group_no, title: c.title, url: c.url, starts: c.starts, ends: c.ends,
        ends_text: typeof c.ends_text === 'string' ? c.ends_text.slice(0, 120) : '',
        maint_relative: c.maint_relative === true,
        ends_edge: c.ends_edge === 'before' || c.ends_edge === 'after' ? c.ends_edge : null
      });
    });
    return out;
  }

  // Candidate -> POST /api/events body (a plain event add with its link), or null.
  function noticeAddBody(c) {
    if (!plainObject(c) || !validTitle(c.title)) return null;
    if (utcMsOf(c.starts) === null || utcMsOf(c.ends) === null) return null;
    const add = { kind: 'event', title: c.title, starts: c.starts, ends: c.ends };
    if (validUrl(c.url)) add.url = c.url;
    return validEventsBody({ add: add }) ? { add: add } : null;
  }

  function noticeDismissBody(c) {
    return plainObject(c) && validNoticeNo(c.group_no) ? { dismiss_notice: c.group_no } : null;
  }

  // End label: a maintenance-relative end reads 'YYYY-MM-DD before maint.
  // (~HH:MM local, verify)' (the slot is a guess until verified); else the
  // local end time.
  function noticeEndText(c, opts) {
    if (!plainObject(c)) return '';
    const t = fmtLocal(c.ends, opts);
    if (!t) return '';
    if (c.maint_relative && (c.ends_edge === 'before' || c.ends_edge === 'after')) {
      return t.text.slice(0, 10) + ' ' + c.ends_edge + ' maint. (~' + t.text.slice(11) + ' local, verify)';
    }
    return t.text;
  }

  // ---- plan 064: auto-added notices (undo) + the Steam backup hint ----
  // `suggested_events.auto`: [{group_no, title, url, events, hot, at}] newest
  // first; undo removes what the notice added and dismisses it for good.
  function noticeAutoRows(suggested) {
    const s = plainObject(suggested) ? suggested : {};
    const seen = {};
    const out = [];
    (Array.isArray(s.auto) ? s.auto : []).forEach(function (a) {
      if (!plainObject(a) || !validNoticeNo(a.group_no) || seen[a.group_no]) return;
      if (!validTitle(a.title) || !validUrl(a.url)) return;
      seen[a.group_no] = true;
      const ev = Number.isInteger(a.events) && a.events >= 0 ? a.events : 0;
      const hot = Number.isInteger(a.hot) && a.hot >= 0 ? a.hot : 0;
      const what = [];
      if (ev) what.push(ev + (ev === 1 ? ' entry' : ' entries'));
      if (hot) what.push(hot + ' Hot Time window' + (hot === 1 ? '' : 's'));
      out.push({ group_no: a.group_no, title: a.title, url: a.url, events: ev, hot: hot,
        text: 'auto-added ' + (what.join(' + ') || 'nothing') });
    });
    return out;
  }

  function noticeUndoBody(a) {
    return plainObject(a) && validNoticeNo(a.group_no) ? { undo_notice: a.group_no } : null;
  }

  // Official host down for a day: Steam store news titles as a hint, or null.
  function noticeSteamHint(suggested) {
    const h = plainObject(suggested) && plainObject(suggested.steam_hint) ? suggested.steam_hint : null;
    if (!h) return null;
    const titles = (Array.isArray(h.titles) ? h.titles : []).filter(function (t) {
      return typeof t === 'string' && t.length > 0 && t.length <= 80;
    }).slice(0, 10);
    return { text: 'official news unreachable for a day - check official notices',
      titles: titles, url: validUrl(h.url) ? h.url : null };
  }

  const NOTICE_STATUS = {
    ok: 'checked', stale: 'stale - last good list', pending: 'checking...',
    off: 'off - robots.txt', error: 'check failed', none: 'off'
  };

  function noticeStatus(suggested) {
    const s = plainObject(suggested) ? suggested : null;
    const st = s && Object.prototype.hasOwnProperty.call(NOTICE_STATUS, s.status) ? s.status : 'none';
    let text = 'event check: ' + NOTICE_STATUS[st];
    if (st === 'off' && (s.robots === 'disallow' || s.robots === 'unreachable')) text += ' ' + s.robots;
    return { status: st, text: text };
  }

  Object.assign(K, { EVENT_KINDS: EVENT_KINDS, EVENTS_SOON_S: EVENTS_SOON_S,
    EVENT_ID_RE: EVENT_ID_RE, COUPON_RE: COUPON_RE, REWARDS_MAX: REWARDS_MAX, URL_MAX: URL_MAX,
    ISO_TS: ISO_TS, ISO_DAY: ISO_DAY, LOCAL_TS: LOCAL_TS, realDate: realDate,
    eventDateMs: eventDateMs, fmtLeft: fmtLeft, liveLeft: liveLeft, eventRows: eventRows,
    CLAIM_TIMER_S: CLAIM_TIMER_S, CLAIM_TOAST: CLAIM_TOAST, GROUP_NO_RE: GROUP_NO_RE,
    claimPlace: claimPlace, claimLine: claimLine, claimRows: claimRows, claimBody: claimBody,
    soonestEvent: soonestEvent, localToUtcIso: localToUtcIso, validCode: validCode,
    validRewards: validRewards, validUrl: validUrl, EVENT_FIELDS: EVENT_FIELDS,
    eventFieldsOk: eventFieldsOk, validEventsBody: validEventsBody,
    parseEventForm: parseEventForm, SUGGEST_STATUS: SUGGEST_STATUS,
    suggestedRows: suggestedRows, validLoginRule: validLoginRule, loginDays: loginDays,
    loginDayText: loginDayText, loginTodayText: loginTodayText, loginPill: loginPill,
    loginWeekendText: loginWeekendText, loginTrackBody: loginTrackBody,
    loginMarkBody: loginMarkBody, CUR_NUM_MAX: CUR_NUM_MAX, curNum: curNum, curText: curText,
    currencyRows: currencyRows, currencyText: currencyText, currencyPill: currencyPill,
    currencyWishText: currencyWishText, currencyWishBody: currencyWishBody,
    currencyBalanceBody: currencyBalanceBody, eventGameRows: eventGameRows,
    eventTickBody: eventTickBody, suggestAddBody: suggestAddBody, suggestStatus: suggestStatus,
    validNoticeNo: validNoticeNo, noticeRows: noticeRows, noticeAddBody: noticeAddBody,
    noticeDismissBody: noticeDismissBody, noticeEndText: noticeEndText,
    noticeAutoRows: noticeAutoRows, noticeUndoBody: noticeUndoBody,
    noticeSteamHint: noticeSteamHint, NOTICE_STATUS: NOTICE_STATUS, noticeStatus: noticeStatus });
  return function link() {
    MONTH_NAMES = K.MONTH_NAMES;
    intIn = K.intIn;
  };
});
