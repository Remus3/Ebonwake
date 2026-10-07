/* EW Today tab (plan 003 slice B): daily + weekly checklists with reset
   countdowns, events with days left, add item. Plan 056: the open dice row
   carries a "n/3 earned" pill from GET /api/today dice (never a tick). Plan 021: an item with its own
   reset rule shows its own countdown; the add form offers reset presets and an
   optional custom reset row. Reads GET /api/today; writes go
   through the dashboard preload (window.ewApi) because the server refuses
   renderer POSTs. Ticks are optimistic and reverted on error. Done-state is
   re-derived locally from ticked_at, so a reset flips the lists without a
   reload. Plan 025 (M8): the Events card keeps existing `event` items but also
   lists Events-tab items ending before the weekly reset (read-only, GET
   /api/events), and the add form no longer offers `event` - events are added
   once, on the Events tab. Plan 032: the World bosses card (bosses.js,
   window.EWBosses) mounts between Events and Add item, followed by plan 053's
   Imperial delivery card (imperial.js, window.EWImperial). Plan 033: a "This
   week" card lists GET /api/today weekly_plan (eligible first, locked rows
   with their level / AP / DP gap) and counts clears with POST
   {weekly_tick|weekly_untick: id}. Plan 068: an inferred tick shows an "auto"
   pill + undo (the normal untick, which blocks re-ticking until the reset); a
   ready_minutes row shows "ready". Every node is built
   with DOM APIs - no HTML from data. */
(function () {
  'use strict';
  const C = window.EWCore;
  const POLL_MS = 60000;
  const S = { data: null, err: null, last: null, timer: null, ui: null, pending: {}, resetKey: null,
    resetEls: [], presets: [], ev: null, evAt: 0, evEls: [], rowErr: {}, weekBusy: false };

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function getJSON(path) {
    return fetch(C.SERVER + path).then(function (r) {
      if (r.status === 404) throw new Error(C.notOnServer(path));
      if (!r.ok) {
        return r.json().catch(function () { return null; }).then(function (b) {
          throw new Error(b && typeof b.error === 'string' ? b.error : 'HTTP ' + r.status);
        });
      }
      return r.json();
    }).catch(function (e) {
      throw new Error(e instanceof TypeError ? 'server offline' : String(e.message || e));
    });
  }

  function bridge() {
    const b = window.ewApi;
    return b && typeof b.post === 'function' ? b : null;
  }

  // Re-apply in-flight optimistic ticks over fresh server data.
  function withPending(data) {
    let d = data;
    Object.keys(S.pending).forEach(function (id) { d = C.withTick(d, id, S.pending[id]); });
    return d;
  }

  function accept(data) {
    if (data && Array.isArray(data.items)) {
      S.data = withPending(data);
      S.err = null;
    }
  }

  // ---- data ----

  function poll(force) {
    const now = Date.now();
    if (!force && !C.pollDue(S.last, now, POLL_MS)) { draw(); return; }
    S.last = now;
    clearTimeout(S.timer);
    S.timer = setTimeout(function () { if (!C.pollPaused(S.panel, document)) poll(true); }, POLL_MS);
    getJSON('/api/today').then(accept, function (e) { S.err = e.message; }).then(draw);
    // Events-tab items for the read-only Events card; a failure keeps the last list.
    getJSON('/api/events').then(function (d) {
      if (d && Array.isArray(d.items)) { S.ev = d.items; S.evAt = Date.now(); }
    }, function () { /* the Events tab reports its own errors */ }).then(draw);
  }

  // Plan 049: an SSE `today` / `events` push re-reads now, or on the next
  // show() while this tab is hidden.
  function onBus() {
    if (C.pollPaused(S.panel, document)) S.last = null;
    else poll(true);
  }

  function msg(text) { if (S.ui) S.ui.form.msg.textContent = text; }

  function toggle(it) {
    if (it.id in S.pending) return;
    const b = bridge();
    if (!b) { msg('ticking needs the Ebonwake app window'); return; }
    const raw = (S.data.items || []).filter(function (x) { return x && x.id === it.id; })[0];
    const prev = raw ? (raw.ticked_at === undefined ? null : raw.ticked_at) : null;
    const next = it.done ? null : new Date().toISOString();
    S.pending[it.id] = next;
    S.data = C.withTick(S.data, it.id, next);
    draw();
    const body = it.done ? { untick: it.id } : { tick: it.id };
    const done = function (res) {
      delete S.pending[it.id];
      if (res && res.ok) {
        delete S.rowErr[it.id];
        accept(res.data);
      } else {
        S.data = C.withTick(S.data, it.id, prev);
        // Plan 026 (M9): the failure shows on the row itself, not only in the form.
        S.rowErr[it.id] = 'tick failed: ' + ((res && res.error) || 'unknown error');
        msg(S.rowErr[it.id]);
      }
      draw();
    };
    window.EWToast.via(b).post('/api/today', body).then(done, function (e) {
      done({ ok: false, error: String(e && e.message || e) });
    });
  }

  function send(body, okText) {
    const b = bridge();
    if (!b) { msg('saving needs the Ebonwake app window'); return Promise.resolve(false); }
    msg('saving...');
    return window.EWToast.via(b).post('/api/today', body).then(function (res) {
      if (res && res.ok) {
        accept(res.data);
        msg(okText);
        draw();
        return true;
      }
      msg('failed: ' + ((res && res.error) || 'unknown error'));
      return false;
    }, function (e) { msg('failed: ' + (e && e.message || e)); return false; });
  }

  function remove(it, btn) {
    // Two clicks within 3 s: a stray click never deletes an item.
    if (btn.dataset.armed !== '1') {
      btn.dataset.armed = '1';
      btn.textContent = 'sure?';
      setTimeout(function () { btn.dataset.armed = ''; btn.textContent = 'x'; }, 3000);
      return;
    }
    send({ remove: it.id }, 'removed');
  }

  function add() {
    const f = S.ui.form;
    const r = C.parseTodayForm({ title: f.title.value, kind: f.kind.value, until: '',
      reset_weekday: f.reset_weekday.value, reset_at: f.reset_at.value });
    if (!r.ok) { msg(r.error); return; }
    send(r.body, 'added').then(function (ok) { if (ok) f.title.value = ''; });
  }

  // ---- render ----

  function row(it, extra) {
    const r = el('div', 'ew-trow' + (it.done ? ' done' : ''));
    const lab = el('label', 'ew-tlabel');
    const cb = el('input');
    cb.type = 'checkbox';
    cb.checked = !!it.done;
    cb.disabled = it.id in S.pending;
    cb.addEventListener('change', function () { toggle(it); });
    lab.appendChild(cb);
    lab.appendChild(el('span', 'ew-mname', String(it.title || it.id)));
    r.appendChild(lab);
    if (extra) r.appendChild(extra);
    const x = el('button', 'ew-tx', 'x');
    x.type = 'button';
    x.title = 'remove (click twice)';
    x.setAttribute('aria-label', x.title);
    x.addEventListener('click', function () { remove(it, x); });
    r.appendChild(x);
    if (S.rowErr[it.id]) r.appendChild(el('div', 'ew-err ew-terr', S.rowErr[it.id]));
    return r;
  }

  // Plan 049: one persistent err / note / list frame per card; rows are keyed
  // (C.reconcile) so a poll keeps focus on rows that did not change.
  function frame(ui) {
    let f = ui.frame;
    if (!f || f.list.parentNode !== ui.body) {
      ui.body.textContent = '';
      f = ui.frame = { err: el('div', 'ew-err', ''), note: el('div', 'ew-muted', ''), list: el('div', 'ew-list') };
      [f.err, f.note, f.list].forEach(function (n) { ui.body.appendChild(n); });
    }
    return f;
  }

  function setText(n, text) { n.textContent = text; n.hidden = !text; }

  // Live countdown nodes ride on their row (row.ewLive) and are re-collected
  // after every reconcile, kept or rebuilt alike.
  function collectLive(list) {
    Array.prototype.forEach.call(list.children, function (r) {
      if (!r.ewLive) return;
      if (r.ewLive.rule) S.resetEls.push(r.ewLive);
      else S.evEls.push(r.ewLive);
    });
  }

  // Plan 078: a custom reset countdown in local time, the stored UTC rule on
  // hover (the UTC text if the local view cannot be built).
  function resetText(r, now) {
    const v = C.resetCountdownView(r.rule, now);
    const text = v ? v.text : C.fmtResetCountdown(r.rule, now);
    if (r.el.textContent !== text) r.el.textContent = text;
    r.el.title = v ? v.title : '';
  }

  // r = { ev } (read-only Events-tab row) or { it, kind, pending, err }.
  function listRow(r) {
    if (r.ev) {
      const ev = r.ev;
      const n = el('div', 'ew-trow');
      n.title = 'from the Events tab (read-only here)';
      n.appendChild(el('span', 'ew-mname', ev.title + (typeof ev.code === 'string' ? ' ' + ev.code : '')));
      const left = el('span', 'ew-muted ew-tdays', '');
      n.appendChild(left);
      n.ewLive = { el: left, id: ev.id };
      return n;
    }
    const it = r.it;
    let extra = null;
    let live = null;
    if (r.kind === 'event') {
      extra = el('span', 'ew-muted ew-tdays', C.fmtDaysLeft(it.days_left));
    } else if (it.reset) {
      extra = el('span', 'ew-muted ew-tdays', '');
      live = { el: extra, rule: it.reset };
      resetText(live, Date.now());
    }
    if (r.dice) { // plan 056: a suggestion only; the operator ticks
      const box = el('span', null, '');
      const pill = el('span', 'ew-pill ok', r.dice);
      pill.title = 'dice earned from logged-in time (plan 056); never auto-ticked';
      box.appendChild(pill);
      if (extra) box.appendChild(extra);
      extra = box;
    }
    if (C.todayAutoMark(it) || C.todayReady(it)) extra = autoBox(it, extra);
    const n = row(it, extra);
    if (live) n.ewLive = live;
    return n;
  }

  // Plan 068: "auto" pill + undo on an inferred tick; "ready" pill on a
  // ready_minutes row. Undo = untick, which blocks re-ticking until the reset.
  function autoBox(it, extra) {
    const box = el('span', null, '');
    const auto = C.todayAutoMark(it);
    const pill = el('span', 'ew-pill ' + (auto ? 'unknown' : 'ok'), auto ? 'auto' : 'ready');
    pill.title = C.todayAutoTitle(it) || (auto ? 'auto-ticked' : 'ready');
    box.appendChild(pill);
    if (auto) {
      const undo = el('button', 'ew-tx', 'undo');
      undo.type = 'button';
      undo.title = 'untick; not auto-ticked again until the next reset';
      undo.disabled = it.id in S.pending;
      undo.addEventListener('click', function () { toggle(it); });
      box.appendChild(undo);
    }
    if (extra) box.appendChild(extra);
    return box;
  }

  function drawList(ui, group, kind) {
    const f = frame(ui);
    ui.count.textContent = S.data ? C.countText(group.done, group.total, 'done') : '-';
    ui.count.className = 'ew-pill ' + (S.data && group.total && group.done === group.total ? 'ok' : 'unknown');
    ui.count.hidden = !ui.count.textContent; // plan 047: no "0/0 done"
    setText(f.err, S.err ? (S.data ? 'last data - ' : '') + S.err : '');
    const week = S.data && kind === 'event' ? C.eventsThisWeek(S.ev, S.evAt, Date.now()) : [];
    const items = S.data ? group.items : [];
    let hint = '';
    if (!S.data) hint = S.err ? '' : 'loading...';
    else if (!items.length && !week.length) {
      hint = kind === 'event' ? 'No events end this week - add events on the Events tab.' : 'Nothing here - add an item.';
    }
    setText(f.note, hint);
    f.list.className = 'ew-list' + (S.err ? ' ew-stale' : '');
    f.list.hidden = !(items.length || week.length);
    const rows = week.map(function (ev) {
      return { ev: { id: ev.id, title: ev.title, code: ev.code } };
    }).concat(items.map(function (it) {
      return { it: it, kind: kind, pending: it.id in S.pending, err: S.rowErr[it.id] || '',
        dice: C.diceSuggest(it, S.data.dice) };
    }));
    C.reconcile(f.list, rows, function (r) { return r.ev ? 'ev:' + r.ev.id : 'it:' + r.it.id; }, listRow);
    collectLive(f.list);
  }

  // Plan 033: "This week" - weekly content gated by level and gear. Eligible
  // rows (then unknown) carry n/per_week with +/- ticks; locked rows show the gap.
  function weekTick(r, delta) {
    if (S.weekBusy) return;
    S.weekBusy = true;
    const body = delta > 0 ? { weekly_tick: r.id } : { weekly_untick: r.id };
    send(body, r.name + (delta > 0 ? ' ticked' : ' unticked')).then(function () {
      S.weekBusy = false;
      draw();
    });
  }

  // d = { r, busy }; the "resets in" text is refreshed after reconcile.
  function weekRow(d) {
    const r = d.r;
    const row = el('div', 'ew-trow ew-wrow ' + r.state + (r.full ? ' done' : ''));
    row.title = r.title;
    row.appendChild(el('span', 'ew-mname', r.name));
    if (r.state !== 'locked') {
      row.appendChild(el('span', 'ew-pill ' + (r.full ? 'ok' : 'unknown'), r.ticks));
      [[-1, '-', r.done === 0], [1, '+', r.full]].forEach(function (b) {
        const btn = el('button', 'ew-tx', b[1]);
        btn.type = 'button';
        btn.disabled = b[2] || d.busy;
        btn.title = b[0] > 0 ? 'count one done this period' : 'undo one';
        btn.addEventListener('click', function () { weekTick(r, b[0]); });
        row.appendChild(btn);
      });
    }
    if (r.gap) row.appendChild(el('div', 'ew-muted ew-tdays', r.gap));
    if (r.reset !== null) {
      row.ewReset = el('span', 'ew-muted ew-tdays', '');
      row.appendChild(row.ewReset);
    }
    return row;
  }

  function drawWeek(ui, now) {
    const f = frame(ui);
    const plan = C.weeklyPlan(S.data, now);
    ui.count.textContent = S.data ? plan.eligible + '/' + plan.total + ' eligible' : '-';
    ui.count.className = 'ew-pill ' + (plan.eligible ? 'ok' : 'unknown');
    setText(f.err, plan.error ? 'weekly content: ' + plan.error : '');
    let hint = '';
    if (!S.data) hint = S.err ? S.err : 'loading...';
    else if (!plan.rows.length) hint = 'No weekly content data.';
    setText(f.note, hint);
    const rows = S.data ? plan.rows : [];
    f.list.className = 'ew-list' + (S.err ? ' ew-stale' : '');
    f.list.hidden = !rows.length;
    C.reconcile(f.list, rows.map(function (r) { return { r: r, busy: !!S.weekBusy }; }),
      function (d) { return d.r.id; }, weekRow);
    rows.forEach(function (r, i) {
      const n = f.list.children[i];
      if (n && n.ewReset) n.ewReset.textContent = 'resets in ' + C.fmtDuration(r.reset - now);
    });
  }

  function draw() {
    const ui = S.ui;
    if (!ui || !ui.daily.body.isConnected) return;
    const now = Date.now();
    const items = S.data ? S.data.items : [];
    S.resetKey = C.resetKey(items, now);
    S.resetEls = [];
    S.evEls = [];
    drawPresets();
    const g = C.groupItems(items, now);
    drawList(ui.daily, g.daily, 'daily');
    drawList(ui.weekly, g.weekly, 'weekly');
    drawWeek(ui.week, now);
    drawList(ui.event, g.event, 'event');
    clock(now);
  }

  function clock(now) {
    const ui = S.ui;
    if (!ui) return;
    ui.daily.clock.textContent = 'reset ' + C.fmtDuration(C.nextDailyReset(now) - now);
    ui.weekly.clock.textContent = 'reset ' + C.fmtDuration(C.nextWeeklyReset(now) - now);
    S.resetEls.forEach(function (r) { resetText(r, now); });
    const left = {};
    C.eventsThisWeek(S.ev, S.evAt, now).forEach(function (ev) { left[ev.id] = ev.left_s; });
    S.evEls.forEach(function (r) { r.el.textContent = r.id in left ? C.fmtLeft(left[r.id]) : 'ended'; });
  }

  // Preset select from GET /api/today reset_presets; rebuilt only on change.
  function drawPresets() {
    const sel = S.ui.form.preset;
    const p = C.resetPresets(S.data);
    if (sel.options.length && JSON.stringify(p) === JSON.stringify(S.presets)) return;
    S.presets = p;
    sel.textContent = '';
    const none = el('option', null, p.length ? '(none)' : '(no presets)');
    none.value = '';
    sel.appendChild(none);
    p.forEach(function (x, n) {
      const o = el('option', null, x.label);
      o.value = String(n);
      sel.appendChild(o);
    });
  }

  function usePreset() {
    const f = S.ui.form;
    const p = f.preset.value ? S.presets[Number(f.preset.value)] : null;
    if (!p) return;
    const v = C.presetForm(p);
    ['title', 'kind', 'reset_weekday', 'reset_at'].forEach(function (k) { f[k].value = v[k]; });
  }

  // Every second: countdowns; when a reset passes, re-derive done-state.
  function tick() {
    const now = Date.now();
    const key = C.resetKey(S.data ? S.data.items : [], now);
    if (S.ui && key !== S.resetKey) draw();
    else clock(now);
  }

  // ---- mount ----

  function listCard(title, withClock) {
    const c = el('section', 'ew-card ew-mcard');
    const h = el('h2', null, title);
    const meta = el('span', 'ew-tmeta');
    const count = el('span', 'ew-pill unknown', '');
    meta.appendChild(count);
    const clk = el('span', 'ew-tclock', '');
    if (withClock) meta.appendChild(clk);
    h.appendChild(meta);
    c.appendChild(h);
    const b = el('div', 'ew-cbody');
    c.appendChild(b);
    return { card: c, body: b, count: count, clock: clk };
  }

  function formCard() {
    const c = el('section', 'ew-card ew-mcard');
    c.appendChild(el('h2', null, 'Add item'));
    const body = el('div', 'ew-cbody');
    const form = el('form', 'ew-form');
    const f = {};
    const field = function (name, text, input) {
      const lab = el('label', null);
      lab.appendChild(el('span', 'ew-muted', text));
      input.name = name;
      lab.appendChild(input);
      form.appendChild(lab);
      f[name] = input;
    };
    const preset = el('select');
    field('preset', 'preset', preset);
    preset.addEventListener('change', usePreset);
    const title = el('input');
    title.type = 'text';
    title.maxLength = 80;
    title.autocomplete = 'off';
    field('title', 'title', title);
    const kind = el('select');
    ['daily', 'weekly'].forEach(function (k) { // plan 025: events are added on the Events tab
      const o = el('option', null, k);
      o.value = k;
      kind.appendChild(o);
    });
    field('kind', 'kind', kind);
    const wd = el('select');
    ['default', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].forEach(function (d, n) {
      const o = el('option', null, d);
      o.value = n ? String(n - 1) : ''; // Mon=0, blank = kind default
      wd.appendChild(o);
    });
    field('reset_weekday', 'custom reset day (weekly)', wd);
    const at = el('input');
    at.type = 'text';
    at.maxLength = 5;
    at.placeholder = 'HH:MM UTC';
    at.autocomplete = 'off';
    field('reset_at', 'custom reset time', at);
    const btns = el('div', 'ew-btns');
    const save = el('button', 'ew-btn', 'Add');
    save.type = 'submit';
    btns.appendChild(save);
    form.appendChild(btns);
    f.msg = el('div', 'ew-muted ew-msg', '');
    form.appendChild(f.msg);
    form.addEventListener('submit', function (ev) { ev.preventDefault(); add(); });
    body.appendChild(form);
    c.appendChild(body);
    return { card: c, form: f };
  }

  function mount(panel) {
    S.panel = panel;
    panel.classList.add('ew-today');
    const d = listCard('Daily', true);
    const w = listCard('Weekly', true);
    const wk = listCard('This week', false);
    const e = listCard('Events', false);
    const fm = formCard();
    [d, w, wk, e].forEach(function (c) { panel.appendChild(c.card); });
    if (window.EWBosses) window.EWBosses.mount(panel); // plan 032: World bosses card
    if (window.EWImperial) window.EWImperial.mount(panel); // plan 053: Imperial delivery card
    panel.appendChild(fm.card);
    S.ui = { daily: d, weekly: w, week: wk, event: e, form: fm.form };
    if (!S.timer) {
      setInterval(tick, 1000);
      if (window.EWBus) { window.EWBus.on('today', onBus); window.EWBus.on('events', onBus); }
      poll(false);
    } else {
      draw();
    }
  }

  function show() {
    poll(false);
    if (window.EWBosses) window.EWBosses.show();
    if (window.EWImperial) window.EWImperial.show();
  }

  window.EWToday = { mount: mount, show: show };
})();
