/* EW shared pure logic, part `home` (plan 107 split of ewcore.js):
    Home / Now, timers, session-end summary, first-run checklist, what now,
   maintenance digest, collapsible cards, density, overlay context.
   Installed in order by ../ewcore.js, which re-exports the public names as
   EWCore. Loaded as a plain script before ewcore.js (window.EWCoreParts) and by
   require() under node --test. No DOM, no Electron. */
(function (root, install) {
  if (typeof module !== 'undefined' && module.exports) module.exports = install;
  else (root.EWCoreParts = root.EWCoreParts || {}).home = install;
})(typeof self !== 'undefined' ? self : this, function (K) {
  'use strict';

  // from earlier parts
  const { CLAIM_TIMER_S, alertFor, bossGarmothText, bossRows, buffsLive, claimLine, claimRows,
    deadlineAlert, eventDateMs, eventRows, fmtDuration, fmtElapsed, fmtEta, fmtLeft, fmtRate,
    fmtResetRule, fmtSilver, groupItems, hotLive, isNum, itemRule, liveElapsed, nextDailyReset,
    nextResetOf, nextWeeklyReset, normalizeLeveling, plainObject, sinceFetch, spotName,
    suggestedRows, validTitle } = K;
  // from later parts, bound by link() once every part is installed
  let hit, labelHint;

  // ---- Home / Now (plan 025) ----
  // One glance screen composed from the existing GET payloads. snapshots:
  // {today, grind, leveling, progress, events, market (GET /api/market/watch),
  // at: {<key>: fetchedMs}}; a missing or malformed payload (404 on an old
  // server) drops its card, never the screen. Read-only; the input is never
  // mutated. Cards: {id, title, tab, meta, rows: [{label, value, note, cls,
  // tick}], empty}; `tick` is a /api/today item id (the one Home write).

  const BUFF_WARN_S = 300;

  function nowCard(id, title, tab, rows, empty) {
    return { id: id, title: title, tab: tab, meta: '', rows: rows, empty: rows.length ? null : empty };
  }

  function nowRow(label, value, note, cls) {
    return { label: label, value: value || '', note: note || '', cls: cls || '', tick: null };
  }

  // ---- Timers (plan 076) ----
  // One card for every countdown Home shows: daily / weekly / custom resets,
  // the next 3 world bosses and events ending within 48 h, soonest first (a
  // due tie keeps resets, bosses, events order; plan 086: + reward claim
  // windows closing within 7 days), at most TIMERS_MAX rows. A row
  // What now already lists (same source, same due minute) is dropped, so one
  // fact appears once. Rows carry {source, due} (ms) for that match.
  const TIMERS_MAX = 6;
  const TIMERS_DEDUPE = ['reset', 'boss', 'coupon', 'maint', 'claim'];

  function timerRow(label, value, note, cls, source, due) {
    const r = nowRow(label, value, note, cls);
    r.source = source;
    r.due = due;
    return r;
  }

  function dueKey(source, due) { return source + '@' + Math.floor(due / 60000); }

  // Daily + weekly resets, then each distinct custom item rule, soonest first.
  function timerResets(today, now) {
    const daily = nextDailyReset(now);
    const weekly = nextWeeklyReset(now);
    const rows = [timerRow('Daily reset', fmtDuration(daily - now), '', '', 'reset', daily),
      timerRow('Weekly reset', fmtDuration(weekly - now), '', '', 'reset', weekly)];
    const rules = {};
    (today ? today.items : []).forEach(function (it) {
      const rule = plainObject(it) && validTitle(it.title) ? itemRule(it.kind, it.reset) : null;
      if (!rule) return;
      const k = fmtResetRule(rule);
      if (!rules[k]) rules[k] = { next: nextResetOf(rule, now), titles: [] };
      rules[k].titles.push(it.title);
    });
    Object.keys(rules).map(function (k) { return [k, rules[k]]; })
      .sort(function (a, b) { return a[1].next - b[1].next || (a[0] < b[0] ? -1 : 1); })
      .forEach(function (p) {
        rows.push(timerRow(p[1].titles.join(', '), fmtDuration(p[1].next - now), p[0], '', 'reset', p[1].next));
      });
    return rows;
  }

  // Plan 032: next 3 world bosses, read-only here (ticks live on Today). The
  // Garmoth n/3 count is the card meta only, so the row carries plain names.
  function timerBosses(view, now) {
    return bossRows(view, now, 3).map(function (r) {
      return timerRow(r.names.map(function (n) { return n.name; }).join(' + '), r.left, 'world boss',
        r.done ? 'ew-stale' : '', 'boss', r.at_ms);
    });
  }

  function timerEnding(events, at, now) {
    return eventRows(events.items, at, now).filter(function (r) { return r.soon; }).map(function (r) {
      const end = eventDateMs(r.ends, true);
      return timerRow(r.title, fmtLeft(r.left_s), typeof r.code === 'string' ? r.code : '', 'warn',
        r.kind === 'coupon' ? 'coupon' : 'event', end !== null ? end : now + r.left_s * 1000);
    });
  }

  // Plan 086: claim windows closing within CLAIM_TIMER_S, one per notice.
  function timerClaims(events, at, now) {
    return claimRows(events.items, at, now).filter(function (r) {
      return r.claim_left_s <= CLAIM_TIMER_S;
    }).map(function (r) {
      const c = claimLine(r);
      return timerRow('Claim ' + r.title, c.left, c.text + ' (' + c.place + ')', 'warn', 'claim', c.at);
    });
  }

  // bosses / events: the GET payloads or null (absent, 404); at: the events
  // fetch time; whatnow: the GET /api/whatnow view or null.
  function nowTimers(today, bosses, events, at, now, whatnow) {
    let rows = timerResets(today, now);
    if (bosses) rows = rows.concat(timerBosses(bosses, now));
    if (events) rows = rows.concat(timerEnding(events, at, now), timerClaims(events, at, now));
    const shown = {};
    whatNowActions(whatnow).forEach(function (a) {
      if (a.due !== null && TIMERS_DEDUPE.indexOf(a.source) >= 0) shown[dueKey(a.source, a.due)] = true;
    });
    rows = rows.filter(function (r) { return !shown[dueKey(r.source, r.due)]; })
      .sort(function (a, b) { return a.due - b.due; }).slice(0, TIMERS_MAX);
    const c = nowCard('timers', 'Timers', 'today', rows, 'no timers due');
    c.meta = bosses ? bossGarmothText(bosses) : '';
    return c;
  }

  function nowDailies(today, now) {
    const g = groupItems(today.items, now).daily;
    const rows = g.items.filter(function (it) { return !it.done; }).map(function (it) {
      const r = nowRow(validTitle(it.title) ? it.title : it.id, '');
      r.tick = it.id;
      return r;
    });
    const c = nowCard('dailies', 'Dailies left', 'today', rows,
      g.total ? 'all ' + g.total + ' dailies done' : 'no dailies - add them on Today');
    c.meta = countText(g.done, g.total, 'done');
    return c;
  }

  function nowBuffs(grind, at, now) {
    const rows = buffsLive(grind.buffs, at, now).filter(function (b) { return typeof b.name === 'string'; })
      .map(function (b) {
        return nowRow(b.name, fmtDuration(b.left_s * 1000), '', b.left_s <= BUFF_WARN_S ? 'warn' : '');
      });
    return nowCard('buffs', 'Buffs', 'grind', rows, 'no buffs running');
  }

  function nowSession(grind, at, now) {
    const el = liveElapsed(grind.active, at, now);
    const rows = [];
    if (el !== null) {
      const ref = grind.active.spot;
      rows.push(nowRow(spotName(grind.spots, ref), fmtElapsed(el)));
      const sp = (Array.isArray(grind.spots) ? grind.spots : []).filter(function (s) {
        return plainObject(s) && s.id === ref;
      })[0];
      const sph = sp && isNum(sp.silver_per_h) ? fmtSilver(sp.silver_per_h) + '/h' : '-';
      rows.push(nowRow('avg silver here', sph));
    }
    return nowCard('session', 'Grind session', 'grind', rows, 'no session - log a grind to see silver/h');
  }

  // ---- Session-end summary (plan 046) ----
  // GET /api/grind pending_stop {at, started, spot, minutes} (game exited with
  // a session open; never auto-stopped) and GET /api/summary {session, day,
  // week} windows, each {since, until, grind, xp, buffs, dailies, events, empty}.

  const ISO_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/;

  function pendingStop(grind) {
    const p = plainObject(grind) ? grind.pending_stop : null;
    if (!plainObject(p) || !plainObject(grind.active)) return null;
    if (typeof p.at !== 'string' || !ISO_RE.test(p.at) || !isNum(p.minutes) || p.minutes < 1) return null;
    return { at: p.at, spot: typeof p.spot === 'string' ? p.spot : grind.active.spot, minutes: p.minutes };
  }

  function pendingStopText(p, spots, nowMs) {
    if (!p) return '';
    const ago = Date.parse(p.at);
    const since = isNum(ago) && isNum(nowMs) ? ' (' + fmtDuration(nowMs - ago) + ' ago)' : '';
    return 'Game exited' + since + ' while ' + spotName(spots, p.spot) + ' was running. Stop at the exit time (' +
      fmtDuration(p.minutes * 60000) + ') or keep it running?';
  }

  // Plan 062: Grind tab session pill from GET /api/grind {active, session:
  // {auto}}: an auto session (opened by login, closed by exit) reads 'auto'.
  function sessionPill(grind) {
    if (!plainObject(grind)) return { text: '-', cls: 'unknown' };
    if (!plainObject(grind.active)) return { text: 'idle', cls: 'unknown' };
    const auto = plainObject(grind.session) ? grind.session.auto === true : grind.active.auto === true;
    return auto ? { text: 'auto', cls: 'ok' } : { text: 'running', cls: 'ok' };
  }

  function sumList(v) { return Array.isArray(v) ? v : []; }

  // Rows [{label, value}] for one summary window; [] for a bad or empty one.
  function summaryRows(s) {
    if (!plainObject(s) || s.empty === true || !plainObject(s.grind)) return [];
    const g = s.grind;
    const rows = [];
    if (isNum(g.sessions) && g.sessions > 0) {
      rows.push({ label: 'grind', value: g.sessions + (g.sessions === 1 ? ' session, ' : ' sessions, ') +
        fmtDuration((isNum(g.minutes) ? g.minutes : 0) * 60000) });
      rows.push({ label: 'silver', value: fmtSilver(g.silver) + ' (' + fmtSilver(g.silver_per_h) + '/h)' });
      const top = sumList(g.spots).filter(plainObject)[0];
      if (top && typeof top.name === 'string') {
        rows.push({ label: 'best spot', value: top.name + ' ' + fmtSilver(top.silver_per_h) + '/h' });
      }
    }
    if (plainObject(s.xp) && isNum(s.xp.gained_pct)) {
      const to = plainObject(s.xp.to) ? s.xp.to : {};
      rows.push({ label: 'XP', value: '+' + (Math.round(s.xp.gained_pct * 10) / 10) + '%' +
        (isNum(to.level) ? ' (now Lv ' + to.level + ')' : '') });
    }
    const names = function (v, k) {
      return sumList(v).filter(function (r) { return plainObject(r) && typeof r[k] === 'string'; })
        .map(function (r) { return r[k]; });
    };
    const buffs = names(s.buffs, 'name');
    if (buffs.length) rows.push({ label: 'buffs', value: buffs.join(', ') });
    const dailies = names(s.dailies, 'title');
    if (dailies.length) rows.push({ label: 'dailies', value: dailies.length + ' ticked' });
    const evs = names(s.events, 'title');
    if (evs.length) rows.push({ label: 'events', value: evs.length + ' claimed' });
    return rows;
  }

  // ---- First-run checklist (plan 051) ----
  // GET /api/onboarding {steps: [{id, title, hint, link: {tab, field?}, done}],
  // done, total, complete, dismissed, show}. The Home card lists the steps
  // still open, each with an `open` link to its tab (and Settings field).
  const ONBOARD_ID = /^[a-z]{1,20}$/;
  const ONBOARD_FIELD = /^[a-z][A-Za-z0-9_.]{0,63}$/;

  function validOnboardingBody(b) {
    return plainObject(b) && Object.keys(b).length === 1 &&
      (b.dismiss === true || b.restore === true);
  }

  function onboardGo(link) {
    if (!plainObject(link) || typeof link.tab !== 'string' || !ONBOARD_ID.test(link.tab)) return null;
    const go = { tab: link.tab };
    if (typeof link.field === 'string' && ONBOARD_FIELD.test(link.field)) go.field = link.field;
    return go;
  }

  // Open steps in server order; junk steps are dropped.
  function onboardingRows(ob) {
    const steps = plainObject(ob) && Array.isArray(ob.steps) ? ob.steps : [];
    return steps.filter(function (s) {
      return plainObject(s) && s.done === false && typeof s.id === 'string' && ONBOARD_ID.test(s.id) &&
        validTitle(s.title);
    }).map(function (s) {
      const r = nowRow(s.title, 'to do', typeof s.hint === 'string' ? labelHint(s.hint.slice(0, 160)) : '', 'warn');
      r.step = s.id;
      r.go = onboardGo(s.link);
      return r;
    });
  }

  // Home: the "Get started" card while the server says show (not every step
  // done, not dismissed); null otherwise.
  function nowOnboarding(ob) {
    if (!plainObject(ob) || ob.show !== true) return null;
    const rows = onboardingRows(ob);
    if (!rows.length) return null;
    const c = nowCard('onboarding', 'Get started', 'settings', rows, null);
    c.meta = countText(ob.done, ob.total, 'done');
    c.dismiss = true;
    return c;
  }

  // Home: the last game session's summary when present.
  function nowSummary(sum) {
    const s = plainObject(sum) ? sum.session : null;
    const rows = summaryRows(s).map(function (r) { return nowRow(r.label, r.value); });
    return nowCard('summary', 'Last session', 'grind', rows, 'no game session recorded yet');
  }

  function nowLeveling(d, at, now) {
    const el = sinceFetch(at, now);
    const rows = [];
    if (d.level !== null && d.pct !== null) {
      rows.push(nowRow('Lv ' + d.level + ' ' + (Math.floor(d.pct * 10) / 10).toFixed(1) + '%',
        'ETA ' + (d.eta_next_s === null ? '-' : fmtEta(d.eta_next_s - el)),
        d.rate_pct_h === null ? '' : fmtRate(d.rate_pct_h)));
    }
    const h = hotLive(d.hot, d.xp_stack_pct, at, now);
    if (h.active.length) {
      const ends = Math.min.apply(null, h.active.map(function (a) { return a.ends_in_s; }));
      rows.push(nowRow('Hot Time +' + h.stack + '%', 'ends ' + fmtEta(ends), '', 'ok'));
    } else if (h.next) {
      rows.push(nowRow('Next Hot Time' + (isNum(h.next.pct) ? ' +' + h.next.pct + '%' : ''),
        'in ' + fmtEta(h.next.starts_in_s)));
    }
    const dl = deadlineAlert(d.deadlines);  // plan 024: only a tight or late deadline
    if (dl) rows.push(nowRow('Deadline', dl.text, '', dl.cls));
    return nowCard('leveling', 'Level ETA', 'progress', rows, 'no XP sample yet');
  }

  function nowAlerts(market) {
    const rows = [];
    market.items.forEach(function (it) {
      if (!plainObject(it)) return;
      const hit = alertFor(it.price, it.below, it.above);
      if (!hit) return;
      const name = typeof it.name === 'string' && it.name ? it.name : '#' + it.id;
      rows.push(nowRow(name, fmtSilver(it.price), hit + ' ' + fmtSilver(hit === 'below' ? it.below : it.above), 'ok'));
    });
    return nowCard('alerts', 'Market alerts', 'market', rows,
      market.items.length ? 'no alert hits' : 'watch an item on Market to get alerts');
  }

  function nowCoupons(events) {
    const rows = suggestedRows(events.suggested, events.items).map(function (c) {
      return nowRow(c.code, c.date || '', c.title);
    });
    return nowCard('coupons', 'New coupons', 'events', rows, 'no new coupons');
  }

  // ---- What now (plan 069) ----
  // GET /api/whatnow {top, next, empty, empty_text, errors}: each action
  // {text, why, due (ISO or null), source}. The server ranks; clients only
  // count `due` down. Junk actions are dropped.
  const WHATNOW_TABS = { boss: 'today', reset: 'today', buff: 'grind', hot: 'progress',
    maint: 'events', coupon: 'events', dice: 'today', market: 'market', deadline: 'events',
    ocr: 'system', maint_loss: 'events', event: 'events', claim: 'events' }; // 086: claim
  const WHATNOW_EMPTY = 'All clear - play';

  function whatNowAction(a) {
    if (!plainObject(a) || !validTitle(a.text) || !Object.prototype.hasOwnProperty.call(WHATNOW_TABS, a.source)) {
      return null;
    }
    const due = typeof a.due === 'string' && ISO_RE.test(a.due) ? Date.parse(a.due) : NaN;
    return { text: a.text, why: typeof a.why === 'string' ? a.why.slice(0, 160) : '',
      due: isNaN(due) ? null : due, source: a.source, tab: WHATNOW_TABS[a.source] };
  }

  // Top first, then up to 2 next; [] when the view is bad or empty.
  function whatNowActions(view) {
    if (!plainObject(view)) return [];
    const list = [view.top].concat(Array.isArray(view.next) ? view.next.slice(0, 2) : []);
    return list.map(whatNowAction).filter(Boolean);
  }

  // Countdown for one action: 'now' once due, '' when undated.
  function whatNowLeft(a, now) {
    if (!a || a.due === null || !isNum(now)) return '';
    const s = Math.floor((a.due - now) / 1000);
    return s <= 0 ? 'now' : 'in ' + fmtEta(s);
  }

  // One overlay line: "Kzarka spawns - in 8m", or the all-clear text.
  function whatNowLine(view, now) {
    if (!plainObject(view)) return '-';
    const a = whatNowActions(view)[0];
    if (!a) return WHATNOW_EMPTY;
    const left = whatNowLeft(a, now);
    return a.text + (left ? ' - ' + left : '');
  }

  // Home: the What now card (top action highlighted), null without a payload.
  // Plan 076: full width (`wide`); rows keep {source, due} for the Timers dedupe.
  function nowWhatNow(view, now) {
    if (!plainObject(view)) return null;
    const acts = whatNowActions(view);
    const rows = acts.map(function (a, i) {
      const r = nowRow(a.text, whatNowLeft(a, now), a.why, i === 0 ? 'warn' : '');
      r.go = { tab: a.tab };
      r.source = a.source;
      r.due = a.due;
      return r;
    });
    const c = nowCard('whatnow', 'What now', acts.length ? acts[0].tab : 'today', rows, WHATNOW_EMPTY);
    c.wide = true;
    return c;
  }

  // ---- Before-maintenance digest (plan 074) ----
  // GET /api/maint/digest {maint: {start_utc, end_utc, source}, show, warnings
  // [{key, text, notice_no, url, due_utc}], ending [{kind, title, ends}], acked}.
  // The server decides `show` (T-24 h); the card counts down locally.
  const MAINT_KEY_RE = /^[0-9]{1,9}:[0-9a-f]{12}$/;
  const MAINT_TEXT_MAX = 200;

  // "in 18 h" (whole hours from 1 h), "in 40 m" below an hour, 'now' once due.
  function maintInText(ms) {
    if (!isNum(ms)) return '';
    if (ms <= 0) return 'now';
    if (ms >= 3600000) return 'in ' + Math.floor(ms / 3600000) + ' h';
    return 'in ' + Math.max(1, Math.floor(ms / 60000)) + ' m';
  }

  function nowMaintDigest(view, now) {
    if (!plainObject(view) || view.show !== true || !plainObject(view.maint)) return null;
    const start = typeof view.maint.start_utc === 'string' ? Date.parse(view.maint.start_utc) : NaN;
    if (isNaN(start)) return null;
    const rows = [];
    (Array.isArray(view.warnings) ? view.warnings : []).forEach(function (w) {
      if (!plainObject(w) || typeof w.text !== 'string' || !w.text || w.text.length > MAINT_TEXT_MAX ||
          typeof w.key !== 'string' || !MAINT_KEY_RE.test(w.key)) return;
      const r = nowRow(w.text, '', 'official notice ' + w.notice_no, 'warn');
      r.ack = w.key;
      rows.push(r);
    });
    (Array.isArray(view.ending) ? view.ending : []).forEach(function (e) {
      if (!plainObject(e) || !validTitle(e.title) || typeof e.ends !== 'string') return;
      const ends = Date.parse(e.ends);
      if (isNaN(ends)) return;
      rows.push(nowRow(e.title, fmtDuration(ends - now), typeof e.kind === 'string' ? e.kind + ' ends' : 'ends'));
    });
    return nowCard('maintdigest', 'Before maintenance (' + maintInText(start - now) + ')', 'events', rows,
      'nothing ends at this maintenance');
  }

  function validMaintDigestBody(b) {
    return plainObject(b) && Object.keys(b).length === 1 && typeof b.ack === 'string' && MAINT_KEY_RE.test(b.ack);
  }

  // Plan 076: the quiet-line name of a card with nothing to show.
  const QUIET_NAMES = { dailies: 'dailies', timers: 'timers', buffs: 'buffs', session: 'grind',
    leveling: 'level ETA', alerts: 'alerts', coupons: 'coupons', summary: 'last session' };

  // {cards, quiet}: cards in Home order - What now, Get started (while
  // shown), Before maintenance (plan 074, from T-24 h), Dailies left, Timers,
  // the content cards, Last session last. A card with no rows leaves the grid
  // for `quiet` [{title, tab}] (one muted line); What now and Before
  // maintenance stay even when empty.
  function composeNow(snapshots, nowMs) {
    const s = plainObject(snapshots) ? snapshots : {};
    const at = function (k) { return plainObject(s.at) && isNum(s.at[k]) ? s.at[k] : nowMs; };
    const has = function (k, list) { return plainObject(s[k]) && (!list || Array.isArray(s[k][list])); };
    const today = has('today', 'items') ? s.today : null;
    const events = has('events', 'items') ? s.events : null;
    const wnView = has('whatnow') ? s.whatnow : null;
    const all = [];
    if (today) all.push(nowDailies(today, nowMs));
    all.push(nowTimers(today, has('bosses', 'next') ? s.bosses : null, events, at('events'), nowMs, wnView));
    if (has('grind')) {
      all.push(nowBuffs(s.grind, at('grind'), nowMs));
      all.push(nowSession(s.grind, at('grind'), nowMs));
    }
    const lv = has('leveling') ? normalizeLeveling(s.leveling) : null;
    if (lv) all.push(nowLeveling(lv, at('leveling'), nowMs));
    if (has('market', 'items')) all.push(nowAlerts(s.market));
    if (events && plainObject(events.suggested)) all.push(nowCoupons(events));
    if (has('summary') && plainObject(s.summary.session)) all.push(nowSummary(s.summary));
    const quiet = [];
    const cards = all.filter(function (c) {
      if (c.rows.length) return true;
      quiet.push({ title: QUIET_NAMES[c.id] || c.title, tab: c.tab });
      return false;
    });
    // Plan 074: from T-24 h, right under What now / first run; never quiet
    // (its empty text says nothing ends at this maintenance).
    const md = has('maint') ? nowMaintDigest(s.maint, nowMs) : null;
    if (md) cards.unshift(md);
    const ob = has('onboarding', 'steps') ? nowOnboarding(s.onboarding) : null;
    if (ob) cards.unshift(ob);  // plan 051: first-run card leads until done or dismissed
    // Plan 069: What now on top; an all-clear card yields to a first-run card.
    const wn = wnView ? nowWhatNow(wnView, nowMs) : null;
    if (wn && wn.rows.length) cards.unshift(wn);
    else if (wn) cards.splice(ob ? 1 : 0, 0, wn);
    return { cards: cards, quiet: quiet };
  }

  // ---- Collapsible cards (plan 076) ----
  // Key "tab/card". Storage (localStorage, read by the shell) keeps the keys
  // the operator EXPANDED, so a fresh or corrupt value means every
  // collapsible card starts collapsed. Pure; inputs are never mutated.
  const COLLAPSE_KEY = /^[a-z]{1,20}\/[a-z]{1,20}$/;

  function collapsedKey(tab, card) { return tab + '/' + card; }

  // Stored JSON -> list of valid keys; anything else -> [] (the default).
  function parseCollapsed(raw) {
    let v;
    try { v = JSON.parse(raw); } catch (e) { return []; }
    if (!Array.isArray(v)) return [];
    return v.filter(function (k, i) {
      return typeof k === 'string' && COLLAPSE_KEY.test(k) && v.indexOf(k) === i;
    });
  }

  // List -> stored JSON; the inverse of parseCollapsed (invalid keys dropped).
  function serializeCollapsed(list) {
    return JSON.stringify(parseCollapsed(JSON.stringify(Array.isArray(list) ? list : [])));
  }

  // Flip one card: a new list with `key` added (expanded) or removed (collapsed).
  function toggleCollapsed(list, key) {
    const cur = Array.isArray(list) ? list : [];
    return cur.indexOf(key) >= 0 ? cur.filter(function (k) { return k !== key; }) : cur.concat([key]);
  }

  function isCollapsed(list, key) {
    return !Array.isArray(list) || list.indexOf(key) < 0;
  }

  // ---- Density + accessibility (plan 047) ----

  // "n/total suffix", or '' when there is nothing to count (zero states).
  function countText(done, total, suffix) {
    if (!isNum(done) || !isNum(total) || total <= 0) return '';
    return done + '/' + total + (suffix ? ' ' + suffix : '');
  }

  // Plan 078: a count pill ('3 sessions', '2 open') that is '' at zero, like
  // countText's zero state; `many` defaults to `one`.
  function zeroPill(n, one, many) {
    if (!isNum(n) || n <= 0) return '';
    return n + ' ' + (n === 1 ? one : (many || one));
  }

  // Tab badges from the Home snapshots already polled: dailies left on Today,
  // events ending within 48 h on Events. Zero counts give no badge.
  function tabBadges(snapshots, nowMs) {
    const s = plainObject(snapshots) ? snapshots : {};
    const at = function (k) { return plainObject(s.at) && isNum(s.at[k]) ? s.at[k] : nowMs; };
    const out = {};
    if (plainObject(s.today) && Array.isArray(s.today.items)) {
      const g = groupItems(s.today.items, nowMs).daily;
      if (g.total - g.done > 0) out.today = (g.total - g.done) + ' left';
    }
    if (plainObject(s.events) && Array.isArray(s.events.items)) {
      const n = eventRows(s.events.items, at('events'), nowMs).filter(function (r) { return r.soon; }).length;
      if (n > 0) out.events = n + ' ending';
    }
    return out;
  }

  // Tab-strip keys -> the tab id to select, or null. Left/Right wrap and
  // Home/End act with no modifier (focus on the strip); Ctrl+1..9 picks the
  // n-th tab from anywhere in the dashboard window.
  function tabKey(ids, current, ev) {
    if (!Array.isArray(ids) || !ids.length || !ev || typeof ev.key !== 'string') return null;
    const mods = !!(ev.altKey || ev.shiftKey || ev.metaKey);
    if (ev.ctrlKey) {
      if (mods || !/^[1-9]$/.test(ev.key)) return null;
      return ids[+ev.key - 1] || null;
    }
    if (mods) return null;
    const i = ids.indexOf(current);
    const n = ids.length;
    if (ev.key === 'ArrowRight') return ids[(i + 1) % n];
    if (ev.key === 'ArrowLeft') return ids[i < 0 ? n - 1 : (i - 1 + n) % n];
    if (ev.key === 'Home') return ids[0];
    if (ev.key === 'End') return ids[n - 1];
    return null;
  }

  // System is a maintenance tab: moved last and drawn as a right-aligned icon.
  function orderTabs(tabs) {
    const list = Array.isArray(tabs) ? tabs : [];
    const rest = list.filter(function (t) { return t.id !== 'system'; }).map(function (t) { return Object.assign({}, t); });
    const sys = list.filter(function (t) { return t.id === 'system'; }).map(function (t) {
      return Object.assign({}, t, { edge: true });
    });
    return rest.concat(sys);
  }

  // Plan 025 M8: open Events-tab items that end before the next weekly reset,
  // soonest first (the Today tab's read-only Events card).
  function eventsThisWeek(items, fetchedMs, now) {
    const limit = Math.floor((nextWeeklyReset(now) - now) / 1000);
    return eventRows(items, fetchedMs, now).filter(function (r) {
      return (r.status === 'active' || r.status === 'upcoming') && r.left_s !== null && r.left_s <= limit;
    });
  }

  // Overlay widgets (spec section 3). Main reads config.overlay.widgets and
  // hands the overlay a query string. Default on (only a literal false turns
  // one off), except the WIDGETS_OPT_IN ones: default off, only a literal true
  // turns them on (plan 011 leveling, plan 013 season, plan 029 marketTicker,
  // plan 032 worldBoss, plan 056 dice). Plan 069 whatNow is default on.
  const WIDGETS = ['grindSession', 'grindBuff', 'eventsSoon', 'leveling', 'season', 'marketTicker', 'worldBoss', 'dice',
    'whatNow'];
  const WIDGETS_OPT_IN = ['leveling', 'season', 'marketTicker', 'worldBoss', 'dice'];

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

  // Plan 067: context-aware overlay. The server derives the context (game
  // state, clock, notices) and sends {context, active, widgets, hidden, auto,
  // maint_at} on GET /api/overlay/context and SSE `overlay_context`; the
  // overlay re-renders in place (rows shown / hidden / reordered, no window
  // recreate). `maintenance` is a countdown line, not a settings widget.
  const OVERLAY_MODES = ['auto', 'pin', 'block'];
  const OVERLAY_WIDGETS = WIDGETS.concat(['maintenance']);
  const OVERLAY_ROWS = {
    grindSession: ['ov-grind-row'], grindBuff: ['ov-buff-row'], eventsSoon: ['ov-events-row'],
    leveling: ['ov-leveling-row'], season: ['ov-season-row'], marketTicker: ['ov-ticker-row'],
    worldBoss: ['ov-boss-row', 'ov-boss-next-row'], dice: ['ov-dice-row'], whatNow: ['ov-whatnow-row'],
    maintenance: ['ov-maint-row']
  };

  // Payload -> {context, active, hidden, auto, maintAt (ms|null), widgets
  // {name: bool}, order [names]} or null when the body is not a context.
  function overlayContext(d) {
    if (!plainObject(d) || typeof d.context !== 'string' || !Array.isArray(d.widgets)) return null;
    const order = [];
    d.widgets.forEach(function (w) {
      if (typeof w === 'string' && OVERLAY_WIDGETS.indexOf(w) >= 0 && order.indexOf(w) < 0) order.push(w);
    });
    const widgets = {};
    OVERLAY_WIDGETS.forEach(function (k) { widgets[k] = order.indexOf(k) >= 0; });
    const t = typeof d.maint_at === 'string' ? Date.parse(d.maint_at) : NaN;
    return {
      context: d.context,
      active: Array.isArray(d.active) ? d.active.filter(function (c) { return typeof c === 'string'; }) : [d.context],
      hidden: d.hidden === true,
      auto: d.auto !== false,
      maintAt: isFinite(t) ? t : null,
      widgets: widgets,
      order: order
    };
  }

  // Widgets that turn on going from `prev` to `next` ({name: bool}); the
  // overlay loads their data at once instead of waiting for the next poll.
  function widgetsTurnedOn(prev, next) {
    return OVERLAY_WIDGETS.filter(function (k) { return !!(next && next[k]) && !(prev && prev[k]); });
  }

  // Row ids in display order: shown widgets in the context's order, then the
  // rest (hidden) in the default order. appendChild moves nodes, so applying
  // this list reorders the rows in place.
  function overlayRowOrder(order) {
    const names = (Array.isArray(order) ? order : []).filter(function (w) { return OVERLAY_ROWS[w]; });
    OVERLAY_WIDGETS.forEach(function (w) { if (names.indexOf(w) < 0) names.push(w); });
    const out = [];
    names.forEach(function (w) { OVERLAY_ROWS[w].forEach(function (id) { out.push(id); }); });
    return out;
  }

  // Maintenance countdown line: 'in 42m' / 'now' / '' (none).
  function maintLine(maintAt, now) {
    if (!isNum(maintAt)) return '';
    const left = Math.floor((maintAt - now) / 1000);
    return left > 0 ? 'in ' + fmtLeft(left) : 'now';
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
  // still shows (it is news). Plan 076: a Today line with no daily and no
  // weekly items ("daily 0/0  weekly 0/0") says nothing either.
  const OV_QUIET = ['', '-', '?', 'none', 'idle'];
  const OV_TODAY_EMPTY = /^daily 0\/0\s+weekly 0\/0$/;
  function ovQuiet(text) {
    return text === null || text === undefined || OV_QUIET.indexOf(String(text)) >= 0 ||
      OV_TODAY_EMPTY.test(String(text));
  }

  function ovServerRowHidden(text) {
    return text === 'ok' || ovQuiet(text);
  }

  Object.assign(K, { BUFF_WARN_S: BUFF_WARN_S, nowCard: nowCard, nowRow: nowRow,
    TIMERS_MAX: TIMERS_MAX, TIMERS_DEDUPE: TIMERS_DEDUPE, timerRow: timerRow, dueKey: dueKey,
    timerResets: timerResets, timerBosses: timerBosses, timerEnding: timerEnding,
    timerClaims: timerClaims, nowTimers: nowTimers, nowDailies: nowDailies, nowBuffs: nowBuffs,
    nowSession: nowSession, ISO_RE: ISO_RE, pendingStop: pendingStop,
    pendingStopText: pendingStopText, sessionPill: sessionPill, sumList: sumList,
    summaryRows: summaryRows, ONBOARD_ID: ONBOARD_ID, ONBOARD_FIELD: ONBOARD_FIELD,
    validOnboardingBody: validOnboardingBody, onboardGo: onboardGo,
    onboardingRows: onboardingRows, nowOnboarding: nowOnboarding, nowSummary: nowSummary,
    nowLeveling: nowLeveling, nowAlerts: nowAlerts, nowCoupons: nowCoupons,
    WHATNOW_TABS: WHATNOW_TABS, WHATNOW_EMPTY: WHATNOW_EMPTY, whatNowAction: whatNowAction,
    whatNowActions: whatNowActions, whatNowLeft: whatNowLeft, whatNowLine: whatNowLine,
    nowWhatNow: nowWhatNow, MAINT_KEY_RE: MAINT_KEY_RE, MAINT_TEXT_MAX: MAINT_TEXT_MAX,
    maintInText: maintInText, nowMaintDigest: nowMaintDigest,
    validMaintDigestBody: validMaintDigestBody, QUIET_NAMES: QUIET_NAMES,
    composeNow: composeNow, COLLAPSE_KEY: COLLAPSE_KEY, collapsedKey: collapsedKey,
    parseCollapsed: parseCollapsed, serializeCollapsed: serializeCollapsed,
    toggleCollapsed: toggleCollapsed, isCollapsed: isCollapsed, countText: countText,
    zeroPill: zeroPill, tabBadges: tabBadges, tabKey: tabKey, orderTabs: orderTabs,
    eventsThisWeek: eventsThisWeek, WIDGETS: WIDGETS, WIDGETS_OPT_IN: WIDGETS_OPT_IN,
    optIn: optIn, overlayWidgets: overlayWidgets, widgetsQuery: widgetsQuery,
    widgetsFromQuery: widgetsFromQuery, OVERLAY_MODES: OVERLAY_MODES,
    OVERLAY_WIDGETS: OVERLAY_WIDGETS, OVERLAY_ROWS: OVERLAY_ROWS,
    overlayContext: overlayContext, widgetsTurnedOn: widgetsTurnedOn,
    overlayRowOrder: overlayRowOrder, maintLine: maintLine, OVERLAY_ANCHORS: OVERLAY_ANCHORS,
    OVERLAY_DEFAULT: OVERLAY_DEFAULT, OVERLAY_SCALE: OVERLAY_SCALE,
    OVERLAY_OPACITY: OVERLAY_OPACITY, OVERLAY_SIZE: OVERLAY_SIZE,
    OVERLAY_HEIGHT: OVERLAY_HEIGHT, MINIMAP_ZONE: MINIMAP_ZONE, clampNum: clampNum,
    validAnchor: validAnchor, overlayConfig: overlayConfig, validRect: validRect,
    overlayBounds: overlayBounds, minimapZone: minimapZone, rectInside: rectInside,
    rectsIntersect: rectsIntersect, overlayHeight: overlayHeight,
    overlayStyleQuery: overlayStyleQuery, overlayStyleFromQuery: overlayStyleFromQuery,
    OV_QUIET: OV_QUIET, OV_TODAY_EMPTY: OV_TODAY_EMPTY, ovQuiet: ovQuiet,
    ovServerRowHidden: ovServerRowHidden });
  return function link() {
    hit = K.hit;
    labelHint = K.labelHint;
  };
});
