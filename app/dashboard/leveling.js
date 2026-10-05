/* EW Leveling card (plan 011) on the Progress tab: level + XP % quick entry
   (one row, Enter submits), XP rate, next-level ETA, next milestone, Hot Time
   now / next with a local countdown and the XP stack total. The Hot Time
   window editor, milestones and recent samples sit in a details row that is
   collapsed by default (the tab must fit 1280x800 with no page scroll).
   Plan 024: one line + pill per level-gated deadline (Olvia Academy).
   Reads GET /api/leveling every 60 s and on each SSE `leveling` event; writes
   go through the dashboard preload (window.ewApi). Operator-typed data only;
   nothing is read from or sent to the game. Every node is built with DOM APIs -
   no HTML from data. */
(function () {
  'use strict';
  const C = window.EWCore;
  const POLL_MS = 60000;
  const DUE_MIN_MS = 5000; // a window that flipped locally re-polls, throttled
  const S = { data: null, at: 0, err: null, last: null, timer: null, ui: null, src: null, attempt: 0, dueAt: null };

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function getJSON(path) {
    return fetch(C.SERVER + path).then(function (r) {
      if (r.status === 404) throw new Error(C.notOnServer(path));
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    }).catch(function (e) {
      throw new Error(e instanceof TypeError ? 'server offline' : String(e.message || e));
    });
  }

  function bridge() {
    const b = window.ewApi;
    return b && typeof b.post === 'function' ? b : null;
  }

  function accept(d) {
    const n = C.normalizeLeveling(d);
    if (!n) return false;
    S.data = n;
    S.at = Date.now();
    S.err = null;
    return true;
  }

  function poll(force) {
    const now = Date.now();
    if (!force && !C.pollDue(S.last, now, POLL_MS)) { draw(); return; }
    S.last = now;
    clearTimeout(S.timer);
    S.timer = setTimeout(function () { poll(true); }, POLL_MS);
    getJSON('/api/leveling').then(function (d) {
      if (!accept(d)) throw new Error('bad reply from server');
    }).catch(function (e) { S.err = e.message; }).then(draw);
  }

  // SSE `leveling` carries the full GET body; a bad one just triggers a re-read.
  function connect() {
    if (typeof EventSource !== 'function') return;
    const src = new EventSource(C.SERVER + '/events');
    S.src = src;
    src.onmessage = function () { S.attempt = 0; };
    src.addEventListener('leveling', function (ev) {
      let d = null;
      try { d = JSON.parse(ev.data); } catch (e) { d = null; }
      if (accept(d)) draw();
      else poll(true);
    });
    src.onerror = function () {
      src.close();
      S.src = null;
      setTimeout(connect, C.backoffMs(S.attempt++));
    };
  }

  function msg(text) { if (S.ui) S.ui.msg.textContent = text; }

  function send(body, okText) {
    const b = bridge();
    if (!b) { msg('saving needs the Ebonwake app window'); return Promise.resolve(false); }
    msg('saving...');
    return b.post('/api/leveling', body).then(function (res) {
      if (res && res.ok) {
        if (!accept(res.data)) poll(true);
        msg(okText);
        draw();
        return true;
      }
      msg('failed: ' + ((res && res.error) || 'unknown error'));
      return false;
    }, function (e) { msg('failed: ' + (e && e.message || e)); return false; });
  }

  // ---- render ----

  function stat(box, key) {
    box.appendChild(el('span', 'ew-muted', key));
    const v = el('span', 'ew-mprice', '-');
    box.appendChild(v);
    return v;
  }

  function drawLive(now) {
    const ui = S.ui;
    const d = S.data;
    if (!d) {
      ui.lvl.textContent = S.err ? S.err : 'loading...';
      return;
    }
    const since = Math.max(0, (now - S.at) / 1000);
    ui.lvl.textContent = d.level === null ? 'no XP sample yet' :
      'Lv ' + d.level + '  ' + d.pct + '%';
    ui.rate.textContent = C.fmtRate(d.rate_pct_h);
    ui.eta.textContent = d.eta_next_s === null ? '-' : C.fmtEta(d.eta_next_s - since);
    ui.mile.textContent = d.next_milestone === null ? '-' : 'Lv ' + d.next_milestone +
      (d.next_milestone_label ? ' ' + d.next_milestone_label : '') + (d.milestones_seed ? ' (seed, verify)' : '');
    // Plan 018: the XP patch epoch the rate is measured from, and the per-kill
    // XP cap of the current level band (info only, no prediction).
    const ep = d.epoch_next || d.epoch;
    ui.epoch.textContent = d.epoch_error ? 'epoch table: ' + d.epoch_error :
      (ep ? 'XP patch: ' + C.epochText(Object.assign({}, ep, { starts_in_s: ep.starts_in_s - since })) +
        (d.epoch ? ' - rate from post-patch samples' : '') : '');
    ui.epoch.hidden = !ui.epoch.textContent;
    ui.cap.textContent = d.kill_xp_cap ? 'Lv ' + d.level + ': ' + d.kill_xp_cap : '';
    ui.cap.hidden = !d.kill_xp_cap;
    const h = C.hotLive(d.hot, d.xp_stack_pct, S.at, now);
    if (h.active.length) {
      const a = h.active[0];
      ui.hot.textContent = 'ON ' + C.fmtEta(a.ends_in_s) + ' left (+' + a.pct + '%)';
    } else if (h.next) {
      ui.hot.textContent = 'next in ' + C.fmtEta(h.next.starts_in_s) + ' (+' + h.next.pct + '%)';
    } else {
      ui.hot.textContent = d.hot_windows.length ? '-' : 'no windows set';
    }
    ui.stack.textContent = '+' + h.stack + '%';
    ui.pill.textContent = h.stack > 0 ? 'XP +' + h.stack + '%' : 'no bonus';
    ui.pill.className = 'ew-pill ' + (h.active.length ? 'ok' : 'unknown');
    ui.lvl.className = 'ew-mname' + (S.err ? ' ew-stale' : '');
    if (h.due && C.pollDue(S.dueAt, now, DUE_MIN_MS)) {
      S.dueAt = now;
      poll(true);
    }
  }

  function drawEditor() {
    const ui = S.ui;
    const d = S.data;
    ui.wins.textContent = '';
    ui.samples.textContent = '';
    if (!d) return;
    if (!d.hot_windows.length) ui.wins.appendChild(el('div', 'ew-muted', 'No Hot Time windows. Copy them from the event notice (UTC).'));
    d.hot_windows.forEach(function (w) {
      const r = el('div', 'ew-lrow');
      const t = el('span', 'ew-mname', String(w.label || w.id));
      t.title = String(w.label || '');
      r.appendChild(t);
      r.appendChild(el('span', 'ew-muted ew-gnum', C.fmtDays(w.days) + ' ' + w.start + '-' + w.end));
      r.appendChild(el('span', 'ew-mprice ew-gnum', '+' + w.pct + '%'));
      const x = el('button', 'ew-tx', 'x');
      x.type = 'button';
      x.title = 'delete window';
      x.addEventListener('click', function () { send({ hot_del: w.id }, 'window deleted'); });
      r.appendChild(x);
      ui.wins.appendChild(r);
    });
    if (!ui.mdirty) ui.miles.value = d.milestones.join(', ');
    ui.seed.hidden = !d.milestones_seed;
    ui.mlabels.textContent = d.milestones.filter(function (m) { return typeof d.milestone_labels[String(m)] === 'string'; })
      .map(function (m) { return m + ' ' + d.milestone_labels[String(m)]; }).join(', ');
    ui.epochs.textContent = '';
    d.epochs.forEach(function (e) {
      const r = el('div', 'ew-lrow');
      const t = el('button', 'ew-tx ew-mname', e.label);
      t.type = 'button';
      t.title = 'edit: ' + e.id + (e.source ? ' - ' + e.source : '');
      t.addEventListener('click', function () { fillEpoch(e); });
      r.appendChild(t);
      r.appendChild(el('span', 'ew-muted ew-gnum', e.starts_utc.slice(0, 16).replace('T', ' ') + ' UTC'));
      r.appendChild(el('span', 'ew-muted', e.verified ? 'verified' : 'verify'));
      const x = el('button', 'ew-tx', 'x');
      x.type = 'button';
      x.title = 'delete epoch';
      x.addEventListener('click', function () { send({ epoch_del: e.id }, 'epoch deleted'); });
      r.appendChild(x);
      ui.epochs.appendChild(r);
    });
    d.samples.forEach(function (s) {
      if (typeof s.ts !== 'string') return;
      const r = el('div', 'ew-lrow' + (s.pre_patch === true ? ' ew-stale' : ''));
      r.appendChild(el('span', 'ew-muted ew-gnum', s.ts.slice(5, 16).replace('T', ' ')));
      r.appendChild(el('span', 'ew-mprice ew-gnum', 'Lv ' + s.level));
      r.appendChild(el('span', 'ew-mprice ew-gnum', s.pct + '%'));
      if (s.pre_patch === true) r.appendChild(el('span', 'ew-muted', 'pre-patch'));
      const x = el('button', 'ew-tx', 'x');
      x.type = 'button';
      x.title = 'delete sample';
      x.addEventListener('click', function () { send({ sample_del: s.ts }, 'sample deleted'); });
      r.appendChild(x);
      ui.samples.appendChild(r);
    });
  }

  function fillEpoch(e) {
    const f = S.ui.ep;
    f.id.value = e.id;
    f.start.value = e.starts_utc.slice(0, 16).replace('T', ' ');
    f.label.value = e.label;
    f.source.value = e.source;
    f.verified.checked = e.verified;
    msg('editing epoch ' + e.id + ' - fix the date, tick verified, Save');
  }

  // Plan 024: one line per level-gated deadline (Olvia Academy) with a pill;
  // red when the level ETA lands after enrolment closes.
  function drawDeadlines() {
    const ui = S.ui;
    ui.deadlines.textContent = '';
    const list = S.data ? S.data.deadlines : [];
    list.forEach(function (dl) {
      const r = el('div', 'ew-lrow ew-lnote');
      const p = C.deadlinePill(dl);
      r.appendChild(el('span', 'ew-pill ' + p.cls, p.text));
      const t = el('span', 'ew-mname', C.deadlineLine(dl));
      t.title = t.textContent;
      r.appendChild(t);
      ui.deadlines.appendChild(r);
    });
    ui.deadlines.hidden = !list.length;
  }

  function draw() {
    const ui = S.ui;
    if (!ui || !ui.card.isConnected) return;
    drawLive(Date.now());
    drawDeadlines();
    drawEditor();
  }

  // ---- mount ----

  function input(cls, max, ph, title) {
    const i = el('input', cls);
    i.type = 'text';
    i.maxLength = max;
    i.placeholder = ph;
    i.autocomplete = 'off';
    if (title) i.title = title;
    return i;
  }

  function button(text, type) {
    const b = el('button', 'ew-btn ew-bbtn', text);
    b.type = type || 'button';
    return b;
  }

  function editor(ui) {
    const det = el('details', 'ew-ldet');
    det.appendChild(el('summary', 'ew-muted', 'Hot Time windows, milestones, XP patch epochs, samples'));
    ui.wins = el('div', 'ew-list');
    det.appendChild(ui.wins);
    // Add window: day toggles + UTC times + label + pct, one compact block.
    const f = el('form', 'ew-lform ew-lhot');
    const days = el('div', 'ew-ldays');
    ui.dayBoxes = C.DAY_NAMES.map(function (n, i) {
      const lab = el('label', 'ew-lday');
      const cb = el('input');
      cb.type = 'checkbox';
      cb.value = String(i);
      lab.appendChild(cb);
      lab.appendChild(el('span', null, n.slice(0, 2)));
      days.appendChild(lab);
      return cb;
    });
    f.appendChild(days);
    const start = input('ew-lnum', 5, 'HH:MM', 'start, UTC');
    const end = input('ew-lnum', 5, 'HH:MM', 'end, UTC (may wrap past midnight)');
    const label = input('ew-lname', 40, 'label', 'label, e.g. Hot Time');
    label.value = 'Hot Time';
    const pct = input('ew-lnum', 4, 'XP %', 'combat XP bonus % (0-1000)');
    [start, end, label, pct].forEach(function (i) { f.appendChild(i); });
    f.appendChild(el('span', 'ew-muted', 'UTC'));
    f.appendChild(button('Add', 'submit'));
    f.addEventListener('submit', function (ev) {
      ev.preventDefault();
      const r = C.parseHotForm({
        days: ui.dayBoxes.filter(function (b) { return b.checked; }).map(function (b) { return b.value; }),
        start: start.value, end: end.value, label: label.value, pct: pct.value
      });
      if (!r.ok) { msg(r.error); return; }
      send(r.body, 'window added');
    });
    det.appendChild(f);
    // Milestones (seeded; the operator verifies against the season notice).
    const mf = el('form', 'ew-lform');
    mf.appendChild(el('span', 'ew-muted', 'milestones'));
    ui.miles = input('ew-lname', 100, '50, 56, 57', 'milestone levels');
    ui.miles.addEventListener('input', function () { ui.mdirty = true; });
    mf.appendChild(ui.miles);
    mf.appendChild(button('Save', 'submit'));
    mf.addEventListener('submit', function (ev) {
      ev.preventDefault();
      const r = C.parseMilestones(ui.miles.value);
      if (!r.ok) { msg(r.error); return; }
      send(r.body, 'milestones saved').then(function (ok) { if (ok) ui.mdirty = false; });
    });
    det.appendChild(mf);
    ui.seed = el('div', 'ew-muted ew-lnote', 'seed, verify against the current patch notes');
    det.appendChild(ui.seed);
    ui.mlabels = el('div', 'ew-muted ew-lnote', '');
    det.appendChild(ui.mlabels);
    // XP patch epochs (plan 018): the rate counts only samples since the newest
    // started one. Click a row to correct it (same id), x deletes it.
    det.appendChild(el('div', 'ew-muted ew-lnote', 'XP patch epochs (UTC)'));
    ui.epochs = el('div', 'ew-list');
    det.appendChild(ui.epochs);
    const ef = el('form', 'ew-lform');
    ui.ep = {
      id: input('ew-lnum', 40, 'id', 'epoch id (blank = from label; an existing id corrects it)'),
      start: input('ew-lname', 16, 'YYYY-MM-DD HH:MM', 'live maintenance end, UTC'),
      label: input('ew-lname', 40, 'label', 'label, e.g. Lv 75 patch'),
      source: input('ew-lname', 200, 'source', 'where the date comes from (patch notes)')
    };
    ['id', 'start', 'label', 'source'].forEach(function (k) { ef.appendChild(ui.ep[k]); });
    const vlab = el('label', 'ew-lday');
    ui.ep.verified = el('input');
    ui.ep.verified.type = 'checkbox';
    vlab.appendChild(ui.ep.verified);
    vlab.appendChild(el('span', null, 'verified'));
    ef.appendChild(vlab);
    ef.appendChild(button('Save', 'submit'));
    ef.addEventListener('submit', function (ev) {
      ev.preventDefault();
      const r = C.parseEpochForm({ id: ui.ep.id.value, start: ui.ep.start.value, label: ui.ep.label.value,
        source: ui.ep.source.value, verified: ui.ep.verified.checked });
      if (!r.ok) { msg(r.error); return; }
      send(r.body, 'epoch saved');
    });
    det.appendChild(ef);
    det.appendChild(el('div', 'ew-muted ew-lnote', 'recent samples'));
    ui.samples = el('div', 'ew-list');
    det.appendChild(ui.samples);
    return det;
  }

  function mount(panel) {
    const c = el('section', 'ew-card ew-mcard ew-leveling');
    const h = el('h2', null, 'Leveling');
    const pill = el('span', 'ew-pill unknown', '-');
    h.appendChild(pill);
    c.appendChild(h);
    const body = el('div', 'ew-cbody');
    const ui = { card: c, pill: pill, mdirty: false };
    // Quick entry: level + XP %, Enter submits.
    const q = el('form', 'ew-lform');
    const lvl = input('ew-lnum', 2, 'Lv', 'level 1-' + C.LEVEL_MAX);
    lvl.inputMode = 'numeric';
    const pct = input('ew-lnum', 8, 'XP %', 'XP % 0-100, up to 3 decimals');
    pct.inputMode = 'decimal';
    q.appendChild(lvl);
    q.appendChild(pct);
    q.appendChild(button('Log', 'submit'));
    q.addEventListener('submit', function (ev) {
      ev.preventDefault();
      const r = C.parseSampleForm({ level: lvl.value, pct: pct.value });
      if (!r.ok) { msg(r.error); return; }
      send(r.body, 'logged').then(function (ok) { if (ok) pct.value = ''; });
    });
    body.appendChild(q);
    ui.lvl = el('div', 'ew-mname', 'loading...');
    body.appendChild(ui.lvl);
    const kv = el('div', 'ew-kv');
    ui.rate = stat(kv, 'rate');
    ui.eta = stat(kv, 'next level');
    ui.mile = stat(kv, 'milestone');
    ui.hot = stat(kv, 'Hot Time');
    ui.stack = stat(kv, 'XP stack');
    body.appendChild(kv);
    ui.deadlines = el('div', 'ew-list');
    ui.deadlines.hidden = true;
    body.appendChild(ui.deadlines);
    ui.epoch =el('div', 'ew-muted ew-lnote', '');
    ui.epoch.hidden = true;
    body.appendChild(ui.epoch);
    ui.cap = el('div', 'ew-muted ew-lnote', '');
    ui.cap.hidden = true;
    body.appendChild(ui.cap);
    ui.msg = el('div', 'ew-muted ew-msg', '');
    body.appendChild(ui.msg);
    body.appendChild(editor(ui));
    c.appendChild(body);
    // Second card on the tab, right after Character.
    panel.insertBefore(c, panel.children[1] || null);
    S.ui = ui;
    if (!S.timer) {
      poll(false);
      setInterval(function () { if (S.ui && S.ui.card.isConnected) drawLive(Date.now()); }, 1000);
    } else draw();
    if (!S.src) connect();
  }

  function show() { poll(false); }

  window.EWLeveling = { mount: mount, show: show };
})();
