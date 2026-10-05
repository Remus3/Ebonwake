/* EW Grind tab (plan 005 slice B): session start/stop with a live clock,
   manual log, recent sessions with two-click delete, per-spot averages and buff
   timers. Reads GET /api/grind; writes go through the dashboard preload
   (window.ewApi) because the server refuses renderer POSTs. Clocks tick locally
   each second without rebuilding the cards (inputs keep focus). Every node is
   built with DOM APIs - no HTML from data. */
(function () {
  'use strict';
  const C = window.EWCore;
  const POLL_MS = 60000;
  const S = {
    data: null, at: 0, err: null, last: null, timer: null, ui: null, busy: false,
    spot: null, buffMin: {}, preset: undefined, presetMin: '',
    // Plan 012 "Where next": GET /api/spots, refetched with each grind poll.
    recs: null, recsErr: null, goal: 'xp', whatIf: { ap: '', dp: '', level: '' }
  };

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function getJSON(path) {
    return fetch(C.SERVER + path).then(function (r) {
      if (r.status === 404) throw new Error('grind API not on this server yet');
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

  function valid(d) {
    return !!d && typeof d === 'object' && !Array.isArray(d) && Array.isArray(d.sessions);
  }

  function accept(d) {
    if (!valid(d)) return false;
    S.data = d;
    S.at = Date.now();
    S.err = null;
    return true;
  }

  // ---- data ----

  function poll(force) {
    const now = Date.now();
    if (!force && !C.pollDue(S.last, now, POLL_MS)) { draw(); return; }
    S.last = now;
    clearTimeout(S.timer);
    S.timer = setTimeout(function () { poll(true); }, POLL_MS);
    getJSON('/api/grind').then(accept, function (e) { S.err = e.message; }).then(draw);
    loadRecs();
  }

  function loadRecs() {
    const p = C.spotsPath(S.goal, S.whatIf);
    if (!p.ok) { S.recsErr = p.error; drawRecs(); return; }
    getJSON(p.path).then(function (d) { S.recs = C.spotRecs(d); S.recsErr = null; },
      function (e) { S.recsErr = e.message; }).then(drawRecs);
  }

  function msg(text) { if (S.ui) S.ui.msg.textContent = text; }

  // One POST at a time; a reply without the GET body triggers a re-poll.
  function send(body, okText) {
    const b = bridge();
    if (!b) { msg('saving needs the Ebonwake app window'); return Promise.resolve(false); }
    if (S.busy) return Promise.resolve(false);
    S.busy = true;
    msg('saving...');
    return b.post('/api/grind', body).then(function (res) {
      S.busy = false;
      if (res && res.ok) {
        if (!accept(res.data)) poll(true);
        msg(okText);
        draw();
        return true;
      }
      msg('failed: ' + ((res && res.error) || 'unknown error'));
      return false;
    }, function (e) { S.busy = false; msg('failed: ' + (e && e.message || e)); return false; });
  }

  function spots() { return S.data && Array.isArray(S.data.spots) ? S.data.spots : []; }

  function active() {
    const a = S.data && S.data.active;
    return a && typeof a === 'object' && !Array.isArray(a) ? a : null;
  }

  function spotRef(s) { return s.id === undefined || s.id === null ? s.name : s.id; }

  // ---- actions ----

  function start() {
    const f = S.ui.form;
    if (!f.spot.value) { msg('pick or add a spot first'); return; }
    send({ start: f.spot.value }, 'started');
  }

  function stop() {
    const f = S.ui.form;
    const r = C.parseGrindForm('stop', { silver: f.silver.value, trash: f.trash.value });
    if (!r.ok) { msg(r.error); return; }
    send(r.body, 'logged').then(function (ok) { if (ok) { f.silver.value = ''; f.trash.value = ''; } });
  }

  function logManual() {
    const f = S.ui.form;
    const r = C.parseGrindForm('log', { spot: f.spot.value, minutes: f.minutes.value, silver: f.silver.value, trash: f.trash.value });
    if (!r.ok) { msg(r.error); return; }
    send(r.body, 'logged').then(function (ok) {
      if (ok) { f.minutes.value = ''; f.silver.value = ''; f.trash.value = ''; }
    });
  }

  function addSpot() {
    const f = S.ui.form;
    const r = C.parseGrindForm('spot', { name: f.newSpot.value });
    if (!r.ok) { msg(r.error); return; }
    send(r.body, 'spot added').then(function (ok) { if (ok) f.newSpot.value = ''; });
  }

  // A recommended spot click: select it when already logged, else put its name
  // in the "new spot" field. Nothing is posted.
  function pickRec(name) {
    const f = S.ui.form;
    const id = C.matchSpot(spots(), name);
    if (id !== null && !active()) {
      f.spot.value = id;
      S.spot = id;
      msg(name + ' selected');
    } else if (id === null) {
      f.newSpot.value = name;
      msg('not logged yet - press Add spot');
    } else {
      msg('a session is running; stop it first');
    }
  }

  // Two clicks within 3 s: a stray click never deletes a session.
  function armed(btn, label) {
    if (btn.dataset.armed === '1') return true;
    btn.dataset.armed = '1';
    btn.textContent = 'sure?';
    setTimeout(function () { btn.dataset.armed = ''; btn.textContent = label; }, 3000);
    return false;
  }

  // xp: optional XP % (plan 011); blank keeps the stored value on re-arm.
  function arm(row, input, xp) {
    const r = C.parseGrindForm('buff', { name: row.name, minutes: input.value, xp_pct: xp.value });
    if (!r.ok) { msg(r.error); return; }
    S.buffMin[row.name.toLowerCase()] = input.value;
    send(r.body, row.name + ' armed');
  }

  // ---- render ----

  function drawSession() {
    const ui = S.ui;
    const f = ui.form;
    const a = active();
    ui.sessionPill.textContent = a ? 'running' : (S.data ? 'idle' : '-');
    ui.sessionPill.className = 'ew-pill ' + (a ? 'ok' : 'unknown');
    ui.sessionErr.textContent = S.err ? (S.data ? 'last data - ' : '') + S.err : '';
    // Spot picker: keep the operator's choice across redraws.
    const cur = f.spot.value || S.spot;
    f.spot.textContent = '';
    spots().forEach(function (s) {
      if (!s || typeof s.name !== 'string') return;
      const o = el('option', null, s.name);
      o.value = String(spotRef(s));
      f.spot.appendChild(o);
    });
    if (cur && Array.prototype.some.call(f.spot.options, function (o) { return o.value === cur; })) f.spot.value = cur;
    S.spot = f.spot.value || null;
    ui.activeSpot.textContent = a ? C.spotName(spots(), a.spot) : (spots().length ? 'no session running' : 'add a spot to start');
    f.spotRow.hidden = !!a;
    f.minutesRow.hidden = !!a;
    f.start.hidden = !!a;
    f.log.hidden = !!a;
    f.stop.hidden = !a;
    clock(Date.now());
  }

  function drawLog() {
    const ui = S.ui;
    const body = ui.logBody;
    body.textContent = '';
    const list = S.data ? S.data.sessions.filter(function (s) { return s && typeof s === 'object'; }) : [];
    ui.logPill.textContent = S.data ? list.length + ' sessions' : '-';
    if (!S.data) { body.appendChild(el('div', 'ew-muted', S.err ? S.err : 'loading...')); return; }
    if (!list.length) { body.appendChild(el('div', 'ew-muted', 'No sessions yet.')); return; }
    const box = el('div', 'ew-list' + (S.err ? ' ew-stale' : ''));
    list.forEach(function (s) {
      const r = el('div', 'ew-grow');
      const when = typeof s.started === 'string' ? s.started.slice(5, 16).replace('T', ' ') : '';
      const name = el('span', 'ew-mname', C.spotName(spots(), s.spot));
      name.title = when;
      r.appendChild(name);
      r.appendChild(el('span', 'ew-muted ew-gnum', (typeof s.minutes === 'number' ? s.minutes : '?') + 'm'));
      r.appendChild(el('span', 'ew-mprice', C.fmtSilver(s.silver)));
      r.appendChild(el('span', 'ew-mprice ew-gnum', C.fmtSilver(C.silverPerHour(s.silver, s.minutes)) + '/h'));
      const x = el('button', 'ew-tx', 'x');
      x.type = 'button';
      x.title = 'delete (click twice)';
      x.addEventListener('click', function () {
        if (armed(x, 'x')) send({ delete: s.id }, 'deleted');
      });
      r.appendChild(x);
      box.appendChild(r);
    });
    body.appendChild(box);
  }

  function drawSpots() {
    const body = S.ui.spotBody;
    body.textContent = '';
    if (!S.data) { body.appendChild(el('div', 'ew-muted', S.err ? S.err : 'loading...')); return; }
    const list = C.sortSpots(spots());
    if (!list.length) { body.appendChild(el('div', 'ew-muted', 'No spots yet.')); return; }
    const box = el('div', 'ew-list' + (S.err ? ' ew-stale' : ''));
    list.forEach(function (s) {
      const r = el('div', 'ew-srow');
      r.appendChild(el('span', 'ew-mname', String(s.name || s.id)));
      r.appendChild(el('span', 'ew-muted ew-gnum', (s.sessions || 0) + 'x ' + (s.minutes || 0) + 'm'));
      r.appendChild(el('span', 'ew-mprice', s.silver_per_h === null || s.silver_per_h === undefined ? '-' : C.fmtSilver(s.silver_per_h) + '/h'));
      box.appendChild(r);
    });
    body.appendChild(box);
  }

  function drawBuffs(now) {
    const ui = S.ui;
    const rows = C.buffRows(S.data ? S.data.buffs : [], S.at, now);
    ui.buffBody.textContent = '';
    ui.buffClocks = [];
    if (S.err && !S.data) ui.buffBody.appendChild(el('div', 'ew-err', S.err));
    const box = el('div', 'ew-list' + (S.err ? ' ew-stale' : ''));
    rows.forEach(function (row) {
      const r = el('div', 'ew-brow' + (row.left_s === null ? '' : ' on'));
      r.appendChild(el('span', 'ew-mname', row.name));
      const left = el('span', 'ew-mprice', row.left_s === null ? '-' : C.fmtDuration(row.left_s * 1000));
      r.appendChild(left);
      if (row.left_s !== null) ui.buffClocks.push({ name: row.name, node: left });
      const min = el('input');
      min.type = 'text';
      min.inputMode = 'numeric';
      min.maxLength = 5;
      min.title = 'minutes (1-43200)';
      min.value = S.buffMin[row.name.toLowerCase()] || String(row.minutes);
      min.addEventListener('input', function () { S.buffMin[row.name.toLowerCase()] = min.value; });
      r.appendChild(min);
      const xp = el('input');
      xp.type = 'text';
      xp.inputMode = 'numeric';
      xp.maxLength = 4;
      xp.placeholder = 'xp%';
      xp.title = 'XP bonus % (0-1000, optional; counted in the Leveling XP stack)';
      xp.value = typeof row.xp_pct === 'number' ? String(row.xp_pct) : '';
      r.appendChild(xp);
      // Plan 018: a preset-named buff still on its pre-patch xp% (never rewritten).
      if (row.xp_hint) {
        const h = el('span', 'ew-muted', row.xp_hint);
        h.title = 'the Lv 75 patch changed this buff; check its XP % in game';
        r.appendChild(h);
      }
      const go = el('button', 'ew-btn ew-bbtn', row.left_s === null ? 'arm' : 're-arm');
      go.type = 'button';
      go.addEventListener('click', function () { arm(row, min, xp); });
      r.appendChild(go);
      const x = el('button', 'ew-tx', 'x');
      x.type = 'button';
      x.title = 'clear';
      x.disabled = row.id === null;
      x.hidden = row.left_s === null;
      x.addEventListener('click', function () { if (row.id !== null) send({ clear_buff: row.id }, 'cleared'); });
      r.appendChild(x);
      box.appendChild(r);
    });
    ui.buffBody.appendChild(box);
    drawPresets();
  }

  // Plan 018 XP buff presets: pick one, set minutes, arm (name + xp% filled).
  function drawPresets() {
    const ui = S.ui;
    const list = C.xpPresets(S.data);
    if (!list.length) return;
    const f = el('form', 'ew-brow');
    const sel = el('select');
    list.forEach(function (p, i) {
      const o = el('option', null, p.name + ' +' + p.xp_pct + '%');
      o.value = String(i);
      o.title = p.title;
      sel.appendChild(o);
    });
    sel.title = 'XP buff preset (community/patch-note value, verify)';
    if (S.preset !== undefined && S.preset < list.length) sel.value = String(S.preset);
    sel.addEventListener('change', function () { S.preset = Number(sel.value); });
    f.appendChild(sel);
    const min = el('input');
    min.type = 'text';
    min.inputMode = 'numeric';
    min.maxLength = 5;
    min.placeholder = 'min';
    min.title = 'minutes (1-43200)';
    min.value = S.presetMin || '';
    min.addEventListener('input', function () { S.presetMin = min.value; });
    f.appendChild(min);
    const go = el('button', 'ew-btn ew-bbtn', 'arm preset');
    go.type = 'submit';
    f.appendChild(go);
    f.addEventListener('submit', function (ev) {
      ev.preventDefault();
      const p = list[Number(sel.value)];
      const r = C.parseGrindForm('buff', { name: p.name, minutes: min.value, xp_pct: String(p.xp_pct) });
      if (!r.ok) { msg(r.error); return; }
      send(r.body, p.name + ' armed');
    });
    ui.buffBody.appendChild(f);
  }

  function recRow(r, unlock) {
    const b = el('button', 'ew-grow ew-rrow');
    b.type = 'button';
    b.title = (r.region ? r.region + ' - ' : '') + 'AP ' + r.ap_min + ' / DP ' + r.dp_min +
      ' / lvl ' + r.level_min + (r.notes ? ' - ' + r.notes : '') + (r.source ? ' (' + r.source + ')' : '');
    b.appendChild(el('span', 'ew-mname', r.name));
    b.appendChild(el('span', 'ew-muted ew-gnum', S.goal + ' ' + r.score + '/5'));
    // Plan 018: level gap vs the spot's monsters, and rows sourced pre-patch.
    const gap = C.spotGapText(r);
    if (gap) b.appendChild(el('span', 'ew-muted', gap));
    if (r.reverify === true) {
      const badge = el('span', 'ew-pill unknown', 're-verify after patch');
      badge.title = 'transcribed ' + r.verified + ', before the newest XP patch';
      b.appendChild(badge);
    }
    if (unlock) {
      b.appendChild(el('span', 'ew-mprice', C.spotNeedText(r)));
    } else {
      const logged = r.logged_silver_per_h;
      b.appendChild(el('span', 'ew-mprice', typeof logged === 'number' ? 'you: ' + C.fmtSilver(logged) + '/h' : '-'));
    }
    b.addEventListener('click', function () { pickRec(r.name); });
    return b;
  }

  function drawRecs() {
    const ui = S.ui;
    if (!ui || !ui.recBody.isConnected) return;
    const body = ui.recBody;
    body.textContent = '';
    const R = S.recs;
    ui.recPill.textContent = 'community, verify';
    if (S.recsErr) body.appendChild(el('div', 'ew-err', S.recsErr));
    if (!R) { if (!S.recsErr) body.appendChild(el('div', 'ew-muted', 'loading...')); return; }
    if (R.error) body.appendChild(el('div', 'ew-err', 'spot table: ' + R.error));
    if (R.missing.length) {
      body.appendChild(el('div', 'ew-muted', 'set ' + R.missing.join(', ') + ' in Progress, or type a what-if above'));
      return;
    }
    const box = el('div', 'ew-list' + (S.recsErr ? ' ew-stale' : ''));
    if (!R.top.length) box.appendChild(el('div', 'ew-muted', 'no spot fits yet'));
    R.top.forEach(function (r) { box.appendChild(recRow(r, false)); });
    if (R.unlocks.length) {
      box.appendChild(el('div', 'ew-muted', 'next unlocks'));
      R.unlocks.forEach(function (r) { box.appendChild(recRow(r, true)); });
    }
    body.appendChild(box);
  }

  function draw() {
    const ui = S.ui;
    if (!ui || !ui.logBody.isConnected) return;
    drawRecs();
    drawSession();
    drawLog();
    drawSpots();
    drawBuffs(Date.now());
  }

  function clock(now) {
    const ui = S.ui;
    const a = active();
    const secs = a ? C.liveElapsed(a, S.at, now) : null;
    ui.clock.textContent = secs === null ? '-' : C.fmtElapsed(secs);
  }

  // Every second: the session clock and buff countdowns; the buffs card is only
  // rebuilt when a buff expires.
  function tick() {
    const ui = S.ui;
    if (!ui || !ui.logBody.isConnected) return;
    const now = Date.now();
    clock(now);
    const live = C.buffsLive(S.data ? S.data.buffs : [], S.at, now);
    const left = {};
    live.forEach(function (b) { left[b.name] = b.left_s; });
    if (ui.buffClocks.some(function (c) { return !(c.name in left); })) { drawBuffs(now); return; }
    ui.buffClocks.forEach(function (c) { c.node.textContent = C.fmtDuration(left[c.name] * 1000); });
  }

  // ---- mount ----

  function card(title) {
    const c = el('section', 'ew-card ew-mcard');
    const h = el('h2', null, title);
    const pill = el('span', 'ew-pill unknown', '');
    h.appendChild(pill);
    c.appendChild(h);
    const b = el('div', 'ew-cbody');
    c.appendChild(b);
    return { card: c, body: b, pill: pill };
  }

  function sessionCard() {
    const c = card('Session');
    const f = {};
    const err = el('div', 'ew-err', '');
    c.body.appendChild(err);
    const head = el('div', 'ew-gsess');
    const spotLabel = el('span', 'ew-mname ew-muted', '');
    const clk = el('span', 'ew-num', '-');
    head.appendChild(spotLabel);
    head.appendChild(clk);
    c.body.appendChild(head);
    const form = el('form', 'ew-form');
    const field = function (name, text, input) {
      const lab = el('label', null);
      lab.appendChild(el('span', 'ew-muted', text));
      input.name = name;
      lab.appendChild(input);
      form.appendChild(lab);
      f[name] = input;
      return lab;
    };
    const text = function (max) {
      const i = el('input');
      i.type = 'text';
      i.maxLength = max;
      i.autocomplete = 'off';
      return i;
    };
    f.spotRow = field('spot', 'spot', el('select'));
    f.spot.addEventListener('change', function () { S.spot = f.spot.value; });
    f.minutesRow = field('minutes', 'minutes (manual)', text(4));
    f.minutes.inputMode = 'numeric';
    field('silver', 'silver earned', text(14));
    f.silver.inputMode = 'numeric';
    field('trash', 'trash', text(7));
    f.trash.inputMode = 'numeric';
    const btns = el('div', 'ew-btns');
    const btn = function (label, fn) {
      const b = el('button', 'ew-btn', label);
      b.type = 'button';
      b.addEventListener('click', fn);
      btns.appendChild(b);
      return b;
    };
    f.start = btn('Start', start);
    f.stop = btn('Stop + log', stop);
    f.log = btn('Log manual', logManual);
    form.appendChild(btns);
    const add = el('label', null);
    f.newSpot = text(60);
    f.newSpot.placeholder = 'new spot';
    add.appendChild(f.newSpot);
    const addBtn = el('button', 'ew-btn', 'Add spot');
    addBtn.type = 'button';
    addBtn.addEventListener('click', addSpot);
    add.appendChild(addBtn);
    form.appendChild(add);
    const m = el('div', 'ew-muted ew-msg', '');
    form.appendChild(m);
    form.addEventListener('submit', function (ev) { ev.preventDefault(); });
    c.body.appendChild(form);
    return { card: c.card, pill: c.pill, form: f, msg: m, clock: clk, activeSpot: spotLabel, err: err };
  }

  // Goal toggle + what-if AP / DP / level (blank = the Progress character).
  function recCard() {
    const c = card('Where next');
    const form = el('form', 'ew-form ew-rform');
    const goal = el('select');
    C.SPOT_GOALS.forEach(function (g) { const o = el('option', null, g); o.value = g; goal.appendChild(o); });
    goal.value = S.goal;
    goal.addEventListener('change', function () { S.goal = goal.value; loadRecs(); });
    form.appendChild(goal);
    ['ap', 'dp', 'level'].forEach(function (k) {
      const i = el('input');
      i.type = 'text';
      i.inputMode = 'numeric';
      i.maxLength = 3;
      i.placeholder = k;
      i.title = k + ' what-if (blank = Progress)';
      i.value = S.whatIf[k];
      i.addEventListener('input', function () { S.whatIf[k] = i.value; });
      form.appendChild(i);
    });
    const go = el('button', 'ew-btn', 'rank');
    go.type = 'button';
    go.addEventListener('click', loadRecs);
    form.appendChild(go);
    form.addEventListener('submit', function (ev) { ev.preventDefault(); loadRecs(); });
    c.body.appendChild(form);
    const list = el('div', null);
    c.body.appendChild(list);
    return { card: c.card, pill: c.pill, body: list };
  }

  function mount(panel) {
    panel.classList.add('ew-grind');
    const s = sessionCard();
    const w = recCard();
    const l = card('Log');
    const p = card('Spots');
    p.pill.textContent = 'avg silver/h';
    const b = card('Buffs');
    b.pill.textContent = 'tap to arm';
    [s, w, l, p, b].forEach(function (x) { panel.appendChild(x.card); });
    S.ui = {
      form: s.form, msg: s.msg, clock: s.clock, activeSpot: s.activeSpot, sessionErr: s.err,
      sessionPill: s.pill, recBody: w.body, recPill: w.pill,
      logBody: l.body, logPill: l.pill, spotBody: p.body, buffBody: b.body, buffClocks: []
    };
    if (!S.timer) {
      setInterval(tick, 1000);
      poll(false);
    } else {
      draw();
    }
  }

  function show() { poll(false); }

  // Plan 009: an OCR'd silver amount goes into the stop form's silver field,
  // only while a session runs and the form is mounted. Nothing is posted - the
  // operator still presses "Stop + log". Returns whether it was filled.
  function prefillSilver(text) {
    if (!S.ui || !active() || typeof text !== 'string') return false;
    S.ui.form.silver.value = text;
    msg('silver filled from screenshot - check, then Stop + log');
    return true;
  }

  // Re-read after another card wrote to /api/grind (the OCR "arm buff").
  function refresh() { if (S.ui) poll(true); }

  window.EWGrind = { mount: mount, show: show, prefillSilver: prefillSilver, refresh: refresh };
})();
