/* EW shared pure logic, part `notify` (plan 107 split of ewcore.js):
    toasts, prompt hygiene, notification rules, external links, note drafts.
   Installed in order by ../ewcore.js, which re-exports the public names as
   EWCore. Loaded as a plain script before ewcore.js (window.EWCoreParts) and by
   require() under node --test. No DOM, no Electron. */
(function (root, install) {
  if (typeof module !== 'undefined' && module.exports) module.exports = install;
  else (root.EWCoreParts = root.EWCoreParts || {}).notify = install;
})(typeof self !== 'undefined' ? self : this, function (K) {
  'use strict';

  // from earlier parts
  const { CLAIM_TOAST, ISO_DAY, MARKET_ALERTS, NOTE_MAX, bossHits, buffsLive, claimLine,
    claimRows, fmtDuration, fmtSilver, hotLive, isNum, itemRule, lastResetOf, loginDayText,
    loginDays, normalizeGame, notOnServer, pendingStop, plainObject, suggestedRows } = K;

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
  const NOTIFY_PRINTABLE = /^[\x20-\x7e]*$/;

  // ---- Prompt hygiene (plan 070) ----
  // Mirrors server/ew/prompts.py + data/prompt_ttl.json (a node test pins the
  // values). GET /api/prompts {quiet_closed, quiet_states, allow_closed,
  // ladder_min, timers} rides in the notify snapshot as `prompts`.
  const TOAST_ONCE_MS = 600000; // prompt_ttl.json ttl_s.toast
  const LADDER_MIN = [15, 5, 1];
  const QUIET_STATES = ['not_running'];
  const QUIET_ALLOW = ['marketAlert', 'couponExpiry', 'gameExit', 'loginRisk'];  // plan 075: loginRisk

  function validLadder(v) {
    return Array.isArray(v) && v.length >= 1 && v.length <= 6 && v.every(function (m, i) {
      return Number.isInteger(m) && m >= 1 && m <= 120 && (i === 0 || v[i - 1] > m);
    });
  }

  // Settings form text "15,5,1" -> [15, 5, 1], or null.
  function parseLadder(s) {
    if (typeof s !== 'string' || !s || s.length > 32) return null;
    const parts = s.split(',').map(function (p) { return p.trim(); });
    if (!parts.every(function (p) { return /^[0-9]+$/.test(p); })) return null;
    const v = parts.map(Number);
    return validLadder(v) ? v : null;
  }

  function promptLadder(p) {
    return plainObject(p) && validLadder(p.ladder_min) ? p.ladder_min : LADDER_MIN;
  }

  // The step due for an event leftMs away: the smallest whose mark passed, or null.
  function ladderStep(leftMs, steps) {
    if (!isNum(leftMs) || leftMs <= 0) return null;
    const due = (validLadder(steps) ? steps : LADDER_MIN).filter(function (m) { return leftMs <= m * 60000; });
    return due.length ? due[due.length - 1] : null;
  }

  function ladderHit(key, title, body) { return { key: key, title: title, body: body, ladder: true }; }

  // Hits -> {fire, drop} under the game-closed quiet: while the game is in a
  // quiet state only allow_closed rules fire; a held ladder hit is stale (its
  // mark passed while closed) and goes to drop, which the caller feeds to the
  // ledger so it never fires late; any other held hit waits for a later round
  // (its rule re-fires at login if still current).
  // Plan 080: while p.muted_until (GET /api/prompts) is after nowMs every hit
  // is dropped (consumed, so nothing fires late when the mute ends).
  function promptGate(hits, game, p, nowMs) {
    const list = Array.isArray(hits) ? hits : [];
    const q = plainObject(p) ? p : {};
    const mute = typeof q.muted_until === 'string' ? Date.parse(q.muted_until) : NaN;
    if (isNum(mute) && mute > (isNum(nowMs) ? nowMs : Date.now())) {
      return { fire: [], drop: list.filter(function (h) { return plainObject(h); }) };
    }
    const g = normalizeGame(game);
    const states = Array.isArray(q.quiet_states) ? q.quiet_states : QUIET_STATES;
    if (q.quiet_closed === false || !g || states.indexOf(g.state) < 0) return { fire: list.slice(), drop: [] };
    const allow = Array.isArray(q.allow_closed) ? q.allow_closed : QUIET_ALLOW;
    const fire = [];
    const drop = [];
    list.forEach(function (h) {
      if (!plainObject(h)) return;
      if (allow.indexOf(h.rule) >= 0 || h.closed === true) fire.push(h); // plan 074 T-24 h / T-1 h
      else if (h.ladder === true) drop.push(h);
    });
    return { fire: fire, drop: drop };
  }

  // A toast pushed with once: true shows once per key: a repeat within
  // TOAST_ONCE_MS of its first show (from any tab or the notify loop) is
  // ignored, even after it expired or was dismissed. push returns whether it showed.
  function toastQueue() {
    let list = [];
    let seq = 0;
    let shown = {};
    return {
      push: function (t, now) {
        if (!plainObject(t)) return false;
        const level = Object.prototype.hasOwnProperty.call(TOAST_MS, t.level) ? t.level : 'warn';
        const key = typeof t.key === 'string' && t.key ? t.key : 'toast:' + (++seq);
        if (t.once === true) {
          const kept = {};
          Object.keys(shown).forEach(function (k) { if (now - shown[k] < TOAST_ONCE_MS) kept[k] = shown[k]; });
          shown = kept;
          if (Object.prototype.hasOwnProperty.call(shown, key)) return false;
          shown[key] = now;
        }
        list = list.filter(function (x) { return x.key !== key; });
        list.push({ key: key, level: level, text: String(t.text === undefined ? '' : t.text).slice(0, TOAST_TEXT_MAX),
          until: now + TOAST_MS[level] });
        if (list.length > TOAST_MAX) list = list.slice(list.length - TOAST_MAX);
        return true;
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
    '/api/leveling': 'Leveling', '/api/settings': 'Settings', '/api/bosses': 'World bosses',
    '/api/pets': 'Pets', '/api/inventory': 'Inventory', '/api/mounts': 'Mounts',
    '/api/onboarding': 'Get started', '/api/crafting': 'Crafting',
    '/api/imperial': 'Imperial delivery', '/api/maint/digest': 'Maintenance warning',
    '/api/portraits': 'Portraits'
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
      if (!plainObject(it) || MARKET_ALERTS.indexOf(it.alert) < 0) return;
      const k = it.id + ':' + (it.sid || 0);
      if (was[k] === it.alert) return;
      const name = typeof it.name === 'string' && it.name ? it.name : 'item ' + it.id;
      const limit = it.alert === 'below' ? 'at or below ' + fmtSilver(it.below)
        : it.alert === 'above' ? 'at or above ' + fmtSilver(it.above)
          : 'under its 90-day p20 ' + fmtSilver(it.bands && it.bands.p20); // plan 052
      out.push(hit('marketAlert:' + k + ':' + it.alert, 'Market alert: ' + name,
        name + ' at ' + fmtSilver(it.price) + ', ' + limit));
    });
    return out;
  }

  // Plan 070: one hit per ladder step before the end (was once at 5 min).
  function buffHits(prev, next, now) {
    if (!plainObject(next.grind)) return [];
    const at = isNum(next.grindAt) ? next.grindAt : next.at;
    const steps = promptLadder(next.prompts);
    return buffsLive(next.grind.buffs, at, now).filter(function (b) {
      return typeof b.name === 'string' && ladderStep(b.left_s * 1000, steps) !== null;
    }).map(function (b) {
      // One key per arming and step: the server's ends, else the end minute.
      const end = typeof b.ends === 'string' ? b.ends : String(Math.round((now + b.left_s * 1000) / 60000));
      const id = b.id === undefined || b.id === null ? b.name : b.id;
      return ladderHit('buffEnding:' + id + ':' + end + ':' + ladderStep(b.left_s * 1000, steps),
        'Buff ending: ' + b.name, b.name + ' ends in ' + fmtDuration(b.left_s * 1000));
    });
  }

  function hotActive(s, now) {
    if (!s || !plainObject(s.leveling)) return null;
    return hotLive(s.leveling.hot, 0, isNum(s.levelingAt) ? s.levelingAt : s.at, now).active;
  }

  function hotHits(prev, next, now) {
    const was = hotActive(prev, isNum(prev && prev.at) ? prev.at : now);
    const cur = hotActive(next, now);
    // Plan 070: Hot Time end on the alert ladder (a state part: fires on a baseline).
    const steps = promptLadder(next.prompts);
    const ending = (cur || []).map(function (a) {
      const m = ladderStep(a.ends_in_s * 1000, steps);
      if (m === null) return null;
      const label = typeof a.label === 'string' && a.label ? a.label : 'Hot Time';
      return ladderHit('hotTime:end:' + a.id + ':' + lastResetOf({ every: 'day', at: '00:00' }, now) + ':' + m,
        'Hot Time ends in ' + m + 'm: ' + label, label + ' ends in ' + fmtDuration(a.ends_in_s * 1000));
    }).filter(Boolean);
    if (!was || !cur) return ending;
    const ids = was.map(function (a) { return a.id; });
    return ending.concat(cur.filter(function (a) { return ids.indexOf(a.id) < 0; }).map(function (a) {
      const label = typeof a.label === 'string' && a.label ? a.label : 'Hot Time';
      return hit('hotTime:' + a.id + ':' + lastResetOf({ every: 'day', at: '00:00' }, now),
        'Hot Time started: ' + label,
        label + (isNum(a.pct) ? ' +' + a.pct + '% XP' : '') + ' for ' + fmtDuration(a.ends_in_s * 1000));
    }));
  }

  // Plan 070 resetSoon: daily / weekly reset and maintenance start on the
  // alert ladder, from the server's GET /api/prompts timers [{key, at, title}].
  function resetSoonHits(prev, next, now) {
    const p = plainObject(next.prompts) ? next.prompts : null;
    const steps = promptLadder(p);
    return (p && Array.isArray(p.timers) ? p.timers : []).map(function (t) {
      if (!plainObject(t) || typeof t.key !== 'string' || typeof t.at !== 'string') return null;
      const left = Date.parse(t.at) - now;
      const m = ladderStep(left, steps);
      if (m === null) return null;
      const title = typeof t.title === 'string' && t.title ? t.title : 'Reset';
      return ladderHit(t.key + ':' + m, title + ' in ' + m + 'm', title + ' in ' + fmtDuration(left));
    }).filter(Boolean);
  }

  // Plan 074 maintLoss: unacked loss warnings of the before-maintenance digest
  // (GET /api/prompts maint_loss [{key, at, title, text}]) on the alert ladder,
  // plus one T-24 h and one T-1 h toast that pass the game-closed quiet.
  const MAINT_LOSS_TOASTS = [[24, 23], [1, 0]]; // [hours mark, window floor in hours]

  function maintLossHits(prev, next, now) {
    const p = plainObject(next.prompts) ? next.prompts : null;
    const steps = promptLadder(p);
    const out = [];
    (p && Array.isArray(p.maint_loss) ? p.maint_loss : []).forEach(function (t) {
      if (!plainObject(t) || typeof t.key !== 'string' || typeof t.at !== 'string') return;
      const left = Date.parse(t.at) - now;
      if (isNaN(left) || left <= 0) return;
      const text = typeof t.text === 'string' && t.text ? t.text : 'Unclaimed loss at maintenance';
      MAINT_LOSS_TOASTS.forEach(function (w) {
        if (left <= w[0] * 3600000 && left > w[1] * 3600000) {
          const h = Object.assign(hit(t.key + ':' + w[0] + 'h', 'Before maintenance (in ' + fmtDuration(left) + ')', text),
            { closed: true });
          out.push(h);
        }
      });
      const m = ladderStep(left, steps);
      if (m !== null) out.push(ladderHit(t.key + ':' + m, 'Before maintenance in ' + m + 'm', text));
    });
    return out;
  }

  // Plan 086 claimDue: one T-24 h toast per open claim window (one per notice);
  // a day-scale deadline, so no 15/5/1 ladder. It passes the game-closed
  // quiet: the operator has to start the game to claim.
  function claimDueHits(prev, next, now) {
    if (!plainObject(next.events)) return [];
    return claimRows(next.events.items, null, now).filter(function (r) {
      const left = r.claim_left_s * 1000;
      return left <= CLAIM_TOAST[0] * 3600000 && left > CLAIM_TOAST[1] * 3600000;
    }).map(function (r) {
      const c = claimLine(r);
      const k = typeof r.url === 'string' ? r.url : r.title;
      // keyed by the UTC date: a re-resolved time that day (notice import) never re-fires
      return Object.assign(hit('claimDue:' + k + ':' + r.claim_until.slice(0, 10), 'Claim rewards: ' + r.title,
        c.text + ' - ' + c.place + ', ' + fmtDuration(c.left_s * 1000) + ' left'), { closed: true });
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
    // Plan 046: the server's grind.pending_stop is the exit signal; its time
    // keys the hit (same as game.since), so the transition path below dedupes.
    const p = plainObject(next.grind) ? pendingStop(next.grind) : null;
    if (p) {
      return [hit('gameExit:' + Date.parse(p.at), 'Game exited',
        'A grind session is still running - stop it at the exit time in the Grind tab')];
    }
    const a = prev ? normalizeGame(prev.game) : null;
    const b = normalizeGame(next.game);
    if (!a || !b || GAME_UP.indexOf(a.state) < 0 || b.state !== 'not_running') return [];
    if (!plainObject(next.grind) || !plainObject(next.grind.active)) return [];
    return [hit('gameExit:' + (b.since === null ? now : b.since), 'Game exited',
      'A grind session is still running - stop it in the Grind tab')];
  }

  // Plan 075: one toast per at-risk login-day event per UTC day (key carries the date).
  function loginRiskHits(prev, next) {
    if (!plainObject(next.events) || !plainObject(next.events.login_days)) return [];
    const day = next.events.login_days.today;
    if (typeof day !== 'string' || !ISO_DAY.test(day)) return [];
    const rows = loginDays(next.events.login_days).byId;
    return Object.keys(rows).filter(function (id) { return rows[id].at_risk === true; }).map(function (id) {
      const r = rows[id];
      const title = typeof r.title === 'string' && r.title ? r.title : id;
      return hit('loginRisk:' + id + ':' + day, 'Log in today: ' + title, loginDayText(r));
    });
  }

  // The rule table. Names are stable; later plans push
  // {name, defaultOn, fire(prev, next, nowMs) -> [{key, title, body}]}.
  // Plan 080: every rule is on (no per-rule switch); game-closed quiet, the
  // ladder and notify.mute_until (promptGate) govern volume.
  const NOTIFY_RULES = [
    { name: 'marketAlert', defaultOn: true, fire: marketHits },
    { name: 'buffEnding', defaultOn: true, fire: buffHits },
    { name: 'hotTime', defaultOn: true, fire: hotHits },
    { name: 'resetPassed', defaultOn: true, fire: resetHits },
    { name: 'newCoupon', defaultOn: true, fire: couponHits },
    { name: 'gameExit', defaultOn: true, fire: gameHits },
    { name: 'bossSoon', defaultOn: true, fire: bossHits },
    { name: 'resetSoon', defaultOn: true, fire: resetSoonHits },
    { name: 'loginRisk', defaultOn: true, fire: loginRiskHits },
    { name: 'maintLoss', defaultOn: true, fire: maintLossHits },
    { name: 'claimDue', defaultOn: true, fire: claimDueHits }  // plan 086 (plan 080: every rule on)
  ];

  // {rule: bool} from each rule's defaultOn. Plan 080: a config/local.json
  // `notify.<rule>` boolean is ignored - a forgotten switch never silences a rule.
  function notifyPrefs(cfg) {
    const out = {};
    NOTIFY_RULES.forEach(function (r) { out[r.name] = !!r.defaultOn; });
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
        const o = { key: h.key, rule: r.name, title: notifyText(h.title, NOTIFY_TITLE_MAX) || r.name,
          body: notifyText(h.body, NOTIFY_BODY_MAX) };
        if (h.ladder === true) o.ladder = true; // plan 070: promptGate drops a stale one
        if (h.closed === true) o.closed = true; // plan 074: fires while the game is closed
        out.push(o);
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

  // Plan 057: the hosts a source link may open in the operator's browser
  // (ew:open-external -> shell.openExternal). https only, no credentials, no
  // port, exact host match; in-app navigation stays blocked.
  const EXTERNAL_HOSTS = Object.freeze(['naeu.playblackdesert.com', 'www.naeu.playblackdesert.com',
    'www.blackdesertfoundry.com', 'api.arsha.io', 'github.com']);
  const EXTERNAL_URL_MAX = 2048;
  const OPEN_RATE = { max: 6, windowMs: 60000 };

  // The normalized href to open, or null when the url is not allowlisted.
  function externalUrl(url) {
    if (typeof url !== 'string' || url.length > EXTERNAL_URL_MAX) return null;
    if (!/^https:\/\/[\x21-\x7e]+$/.test(url)) return null;
    let u;
    try { u = new URL(url); } catch (e) { return null; }
    if (u.protocol !== 'https:' || u.username !== '' || u.password !== '' || u.port !== '') return null;
    if (EXTERNAL_HOSTS.indexOf(u.hostname) < 0) return null;
    return u.href;
  }

  // ew:open-external payload check (main process): exactly {url}, allowlisted.
  function validOpenExternal(b) {
    if (!plainObject(b)) return false;
    const keys = Object.keys(b);
    return keys.length === 1 && keys[0] === 'url' && externalUrl(b.url) !== null;
  }

  // Plan 057: Deadeye note drafts autosave to localStorage per section
  // ({v: 1, text, at}); a stored draft that differs from the saved text is
  // offered back on load.
  const DEADEYE_DRAFT_MS = 2000;
  const DRAFT_AT = /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z$/;

  function draftKey(id) {
    return typeof id === 'string' && /^[A-Za-z0-9_-]{1,40}$/.test(id) ? 'ew.deadeye.draft.' + id : null;
  }

  function draftEncode(text, at) {
    return JSON.stringify({ v: 1, text: text, at: at });
  }

  function draftDecode(raw) {
    if (typeof raw !== 'string' || !raw) return null;
    let d;
    try { d = JSON.parse(raw); } catch (e) { return null; }
    if (!plainObject(d) || d.v !== 1 || typeof d.text !== 'string' || d.text.length > NOTE_MAX) return null;
    if (typeof d.at !== 'string' || !DRAFT_AT.test(d.at)) return null;
    return { text: d.text, at: d.at };
  }

  function draftPending(d, saved) {
    if (!d || typeof d.text !== 'string') return false;
    const norm = function (s) { return s.replace(/\r\n/g, '\n'); };
    return norm(d.text) !== norm(typeof saved === 'string' ? saved : '');
  }

  // {sectionId: draft} for stored drafts worth offering. get(key) reads the
  // store (it may throw: a denied store offers nothing); a live in-memory
  // edit of a section always wins over its stored draft.
  function draftsToRestore(sections, get, live) {
    const out = {};
    (Array.isArray(sections) ? sections : []).forEach(function (s) {
      const key = s && draftKey(s.id);
      if (!key || (live && typeof live[s.id] === 'string')) return;
      let raw = null;
      try { raw = get(key); } catch (e) { return; }
      const d = draftDecode(raw);
      if (draftPending(d, s.text)) out[s.id] = d;
    });
    return out;
  }

  Object.assign(K, { TOAST_MAX: TOAST_MAX, TOAST_TEXT_MAX: TOAST_TEXT_MAX, TOAST_MS: TOAST_MS,
    NOTIFY_TITLE_MAX: NOTIFY_TITLE_MAX, NOTIFY_BODY_MAX: NOTIFY_BODY_MAX,
    NOTIFY_RATE: NOTIFY_RATE, NOTIFY_PRINTABLE: NOTIFY_PRINTABLE, TOAST_ONCE_MS: TOAST_ONCE_MS,
    LADDER_MIN: LADDER_MIN, QUIET_STATES: QUIET_STATES, QUIET_ALLOW: QUIET_ALLOW,
    validLadder: validLadder, parseLadder: parseLadder, promptLadder: promptLadder,
    ladderStep: ladderStep, ladderHit: ladderHit, promptGate: promptGate,
    toastQueue: toastQueue, POST_LABELS: POST_LABELS, postToast: postToast,
    notifyText: notifyText, hit: hit, marketHits: marketHits, buffHits: buffHits,
    hotActive: hotActive, hotHits: hotHits, resetSoonHits: resetSoonHits,
    MAINT_LOSS_TOASTS: MAINT_LOSS_TOASTS, maintLossHits: maintLossHits,
    claimDueHits: claimDueHits, resetHits: resetHits, couponHits: couponHits, GAME_UP: GAME_UP,
    gameHits: gameHits, loginRiskHits: loginRiskHits, NOTIFY_RULES: NOTIFY_RULES,
    notifyPrefs: notifyPrefs, notifySilent: notifySilent, notifyArg: notifyArg,
    notifyPrefsFromArg: notifyPrefsFromArg, notifyRules: notifyRules,
    notifyLedger: notifyLedger, validNotify: validNotify, rateLimiter: rateLimiter,
    EXTERNAL_HOSTS: EXTERNAL_HOSTS, EXTERNAL_URL_MAX: EXTERNAL_URL_MAX, OPEN_RATE: OPEN_RATE,
    externalUrl: externalUrl, validOpenExternal: validOpenExternal,
    DEADEYE_DRAFT_MS: DEADEYE_DRAFT_MS, DRAFT_AT: DRAFT_AT, draftKey: draftKey,
    draftEncode: draftEncode, draftDecode: draftDecode, draftPending: draftPending,
    draftsToRestore: draftsToRestore });
  return function link() {};
});
