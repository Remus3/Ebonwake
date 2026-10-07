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
    spot: null, buffMin: {}, preset: undefined, presetMin: '', dropOpen: false,
    // Plan 012 "Where next": GET /api/spots, refetched with each grind poll.
    recs: null, recsErr: null, goal: 'xp', whatIf: { ap: '', dp: '', level: '' },
    // Plan 039: GET /api/grind/loot for the running (else selected) spot;
    // counts typed per item name survive redraws until a log succeeds.
    loot: null, lootSpot: null, lootErr: null, lootCounts: {},
    // Plan 040: "Import from screenshot" pre-fills lootCounts; `lootLow` marks
    // rows the OCR read with a fuzzy name (shown with "?").
    lootImport: { busy: false, msg: '' }, lootLow: {}, lootTyped: {},
    // Plan 046: GET /api/summary (last game session, today, this week).
    summary: null, summaryErr: null
  };

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
    S.timer = setTimeout(function () { if (!C.pollPaused(S.panel, document)) poll(true); }, POLL_MS);
    getJSON('/api/grind').then(accept, function (e) { S.err = e.message; }).then(draw)
      .then(function () { loadLoot(false); });
    loadRecs();
    loadSummary();
  }

  function loadSummary() {
    getJSON('/api/summary').then(function (d) {
      S.summary = d && typeof d === 'object' && !Array.isArray(d) ? d : null;
      S.summaryErr = S.summary ? null : 'bad summary reply';
    }, function (e) { S.summaryErr = e.message; }).then(drawSummary);
  }

  // Plan 049: an SSE `grind` push re-reads now, or on the next show() while hidden.
  function onBus() {
    if (C.pollPaused(S.panel, document)) S.last = null;
    else poll(true);
  }

  // Plan 039: the loot list follows the running spot, else the picker.
  function lootSpot() {
    const a = active();
    if (a) return a.spot;
    return S.ui && S.ui.form.spot.value ? S.ui.form.spot.value : null;
  }

  function loadLoot(force) {
    if (!S.ui) return;
    const spot = lootSpot();
    if (!force && spot === S.lootSpot) return;
    if (spot !== S.lootSpot) { S.lootCounts = {}; S.lootLow = {}; S.lootTyped = {}; S.lootImport.msg = ''; }
    S.lootSpot = spot;
    if (!spot) { S.loot = null; S.lootErr = null; drawLoot(); return; }
    getJSON('/api/grind/loot?spot=' + encodeURIComponent(spot)).then(function (d) {
      if (spot !== S.lootSpot) return;
      S.loot = d && Array.isArray(d.items) ? d : null;
      S.lootErr = S.loot ? null : 'bad loot reply';
      if (S.loot) applyPrefill(S.loot.prefill);
    }, function (e) { S.lootErr = e.message; }).then(drawLoot);
  }

  // Plan 081: the running session's screenshot counts fill empty (or still
  // prefilled) count boxes; a typed count is never overwritten.
  function applyPrefill(p) {
    const r = C.lootPrefill(p, S.lootCounts, S.lootTyped);
    Object.keys(r.counts).forEach(function (n) { S.lootCounts[n] = r.counts[n]; });
    r.low.forEach(function (n) { S.lootLow[n] = true; });
    if (r.text) S.lootImport.msg = r.text;
  }

  function lootRows() {
    const items = S.loot ? S.loot.items : [];
    return items.filter(function (it) { return it && typeof it.name === 'string'; })
      .map(function (it) { return { name: it.name, count: S.lootCounts[it.name] || '' }; });
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
    return window.EWToast.via(b).post('/api/grind', body).then(function (res) {
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
    const r = C.parseGrindForm('stop', { silver: f.silver.value, trash: f.trash.value, loot: lootRows() });
    if (!r.ok) { msg(r.error); return; }
    send(r.body, 'logged').then(function (ok) {
      if (ok) { f.silver.value = ''; f.trash.value = ''; S.lootCounts = {}; S.lootLow = {}; loadLoot(true); loadSummary(); }
    });
  }

  // Plan 046 "Session ended": end the session at the game-exit time, or keep it.
  function stopAtExit() {
    const f = S.ui.form;
    const r = C.parseGrindForm('stop', { silver: f.silver.value, trash: f.trash.value, loot: lootRows() });
    if (!r.ok) { msg(r.error); return; }
    r.body.stop.at_exit = true;
    send(r.body, 'logged at the exit time').then(function (ok) {
      if (ok) {
        f.silver.value = ''; f.trash.value = ''; S.lootCounts = {}; S.lootLow = {};
        loadLoot(true);
        loadSummary();
      }
    });
  }

  function keepRunning() { send({ keep: true }, 'session kept running'); }

  function logManual() {
    const f = S.ui.form;
    const r = C.parseGrindForm('log', { spot: f.spot.value, minutes: f.minutes.value, silver: f.silver.value, trash: f.trash.value, loot: lootRows() });
    if (!r.ok) { msg(r.error); return; }
    send(r.body, 'logged').then(function (ok) {
      if (ok) { f.minutes.value = ''; f.silver.value = ''; f.trash.value = ''; S.lootCounts = {}; S.lootLow = {}; loadLoot(true); }
    });
  }

  // Plan 040: OCR the newest screenshot (plan 008 list) against this spot's
  // loot names and pre-fill the counts. Nothing is logged: the operator checks
  // the rows and presses Stop + log / Log as usual.
  function importShot() {
    const im = S.lootImport;
    const b = bridge();
    const spot = S.lootSpot;
    if (im.busy || !spot) return;
    if (!b) { im.msg = 'import needs the Ebonwake app window'; drawLoot(); return; }
    im.busy = true;
    im.msg = 'reading the newest screenshot...';
    drawLoot();
    getJSON('/api/game').then(function (d) {
      const g = C.normalizeGame(d);
      const shot = g && g.screenshots.length ? g.screenshots[0].name : null;
      if (!shot) throw new Error('no screenshot yet - take one of the loot window in game');
      const body = { loot: { shot: shot, spot: spot } };
      if (!C.validOcrBody(body)) throw new Error('not a readable screenshot name');
      return window.EWToast.via(b).post('/api/ocr', body).then(function (res) {
        if (!res || !res.ok) throw new Error((res && res.error) || 'unknown error');
        const r = C.normalizeLootOcr(res.data);
        if (!r) throw new Error('bad reply from server');
        if (spot !== S.lootSpot) return;
        const names = (S.loot ? S.loot.items : []).map(function (it) { return it && it.name; });
        const imp = C.lootImportCounts(r.rows, names);
        Object.keys(imp.counts).forEach(function (n) { S.lootCounts[n] = imp.counts[n]; });
        S.lootLow = {};
        imp.low.forEach(function (n) { S.lootLow[n] = true; });
        im.msg = C.lootImportText(shot, imp, r.unmatched);
      });
    }).catch(function (e) { im.msg = 'import failed: ' + (e && e.message || e); })
      .then(function () { im.busy = false; drawLoot(); });
  }

  function addLootItem(lf) {
    const r = C.parseGrindForm('loot_item', { spot: lootSpot(), name: lf.name.value, vendor_price: lf.vendor.value, id: lf.id.value, marketable: lf.market.checked });
    if (!r.ok) { msg(r.error); return; }
    send(r.body, 'loot item saved').then(function (ok) {
      if (ok) { lf.name.value = ''; lf.vendor.value = ''; lf.id.value = ''; loadLoot(true); }
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
    const pill = C.sessionPill(S.data);  // plan 062: 'auto' when login opened it
    ui.sessionPill.textContent = pill.text;
    ui.sessionPill.className = 'ew-pill ' + pill.cls;
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
    const p = C.pendingStop(S.data);
    ui.ended.box.hidden = !p;
    ui.ended.text.textContent = p ? C.pendingStopText(p, spots(), Date.now()) : '';
    clock(Date.now());
  }

  // Plan 046: last game session, today and this week, one block each.
  function drawSummary() {
    const ui = S.ui;
    if (!ui) return;
    const body = ui.summaryBody;
    body.textContent = '';
    if (S.summaryErr) body.appendChild(el('div', 'ew-err', S.summaryErr));
    const d = S.summary || {};
    let any = false;
    [['session', 'Last game session'], ['day', 'Today'], ['week', 'This week']].forEach(function (w) {
      const rows = C.summaryRows(d[w[0]]);
      if (!rows.length) return;
      any = true;
      body.appendChild(el('div', 'ew-muted', w[1]));
      const box = el('div', 'ew-list');
      rows.forEach(function (r) {
        const row = el('div', 'ew-srow');
        row.appendChild(el('span', 'ew-mname', r.label));
        row.appendChild(el('span', 'ew-mprice', r.value));
        box.appendChild(row);
      });
      body.appendChild(box);
    });
    if (!any && !S.summaryErr) body.appendChild(el('div', 'ew-muted', 'nothing logged yet this week'));
  }

  function drawLog() {
    const ui = S.ui;
    const body = ui.logBody;
    body.textContent = '';
    const list = S.data ? S.data.sessions.filter(function (s) { return s && typeof s === 'object'; }) : [];
    ui.logPill.textContent = S.data ? C.zeroPill(list.length, 'session', 'sessions') : '-';
    if (!S.data) { body.appendChild(el('div', 'ew-muted', S.err ? S.err : 'loading...')); return; }
    if (!list.length) { body.appendChild(el('div', 'ew-muted', 'Log a grind to see silver/h.')); return; }
    const box = el('div', 'ew-list' + (S.err ? ' ew-stale' : ''));
    list.forEach(function (s) {
      const r = el('div', 'ew-grow');
      const when = typeof s.started === 'string' ? s.started.slice(5, 16).replace('T', ' ') : '';
      const name = el('span', 'ew-mname', C.spotName(spots(), s.spot));
      name.title = when;
      r.appendChild(name);
      r.appendChild(el('span', 'ew-muted ew-gnum', typeof s.minutes === 'number' ? C.fmtDurationShort(s.minutes) : '?'));
      // Plan 039: a loot-valued session shows its valued silver (typed silver otherwise).
      const silver = C.sessionSilver(s);
      const amt = el('span', 'ew-mprice', C.fmtSilver(silver));
      const pile = C.trashPileText(s);
      if (pile) amt.title = 'loot-valued: ' + pile;
      r.appendChild(amt);
      r.appendChild(el('span', 'ew-mprice ew-gnum', C.fmtSilver(C.silverPerHour(silver, s.minutes)) + '/h'));
      if (pile) r.appendChild(el('span', 'ew-muted', pile));
      const x = el('button', 'ew-tx', 'x');
      x.type = 'button';
      x.title = 'delete (click twice)';
      x.setAttribute('aria-label', x.title);
      x.addEventListener('click', function () {
        if (armed(x, 'x')) send({ delete: s.id }, 'deleted');
      });
      r.appendChild(x);
      box.appendChild(r);
    });
    body.appendChild(box);
  }

  // Plan 039: one row per loot item (count input, sell-vs-vendor hint, x for
  // operator items), the table source, and an add-item row.
  function drawLoot() {
    const ui = S.ui;
    if (!ui || !ui.lootBody) return;
    const body = ui.lootBody;
    body.textContent = '';
    if (!S.lootSpot) return;
    body.appendChild(el('div', 'ew-muted', 'loot (counts value the session)'));
    if (S.lootErr) body.appendChild(el('div', 'ew-err', S.lootErr));
    const items = S.loot ? S.loot.items : [];
    if (items.length) {
      const imp = el('div', 'ew-grow');
      const ib = el('button', 'ew-btn', 'Import from screenshot');
      ib.type = 'button';
      ib.title = 'OCR the newest screenshot (take one of the loot window first); counts are pre-filled, nothing is logged';
      ib.disabled = S.lootImport.busy;
      ib.addEventListener('click', importShot);
      imp.appendChild(ib);
      imp.appendChild(el('span', 'ew-muted', S.lootImport.msg));
      body.appendChild(imp);
    }
    items.forEach(function (it) {
      if (!it || typeof it.name !== 'string') return;
      const r = el('div', 'ew-grow');
      const nm = el('span', 'ew-mname', it.name + (S.lootLow[it.name] ? ' ?' : ''));
      if (S.lootLow[it.name]) nm.title = 'read from a screenshot with a fuzzy name match - check the count';
      r.appendChild(nm);
      const n = el('input');
      n.type = 'text';
      n.inputMode = 'numeric';
      n.maxLength = 8;
      n.placeholder = 'count';
      n.value = S.lootCounts[it.name] || '';
      n.addEventListener('input', function () { S.lootCounts[it.name] = n.value; S.lootTyped[it.name] = true; delete S.lootLow[it.name]; });
      r.appendChild(n);
      const h = el('span', 'ew-muted', C.lootHintText(it.hint));
      const hint = it.hint || {};
      h.title = 'vendor ' + C.fmtSilver(hint.vendor) + ' / market net ' + C.fmtSilver(hint.market_net) + ' per unit';
      r.appendChild(h);
      if (it.origin === 'operator') {
        const x = el('button', 'ew-tx', 'x');
        x.type = 'button';
        x.title = 'forget this item (click twice)';
        x.setAttribute('aria-label', x.title);
        x.addEventListener('click', function () {
          if (!armed(x, 'x')) return;
          send({ loot_forget: { spot: S.lootSpot, name: it.name } }, 'loot item removed')
            .then(function (ok) { if (ok) loadLoot(true); });
        });
        r.appendChild(x);
      }
      body.appendChild(r);
    });
    if (S.loot && S.loot.source) {
      const src = el('div', 'ew-muted', 'table: ' + S.loot.source + (S.loot.verified ? ' (' + S.loot.verified + ')' : ' (unverified)'));
      body.appendChild(src);
    }
    const add = el('div', 'ew-grow');
    const lf = {};
    const box = function (k, ph, max) {
      const i = el('input');
      i.type = 'text';
      i.maxLength = max;
      i.placeholder = ph;
      i.autocomplete = 'off';
      lf[k] = i;
      add.appendChild(i);
    };
    box('name', 'item', 60);
    box('vendor', 'vendor price (12k)', 16);
    box('id', 'item id', 10);
    lf.market = el('input');
    lf.market.type = 'checkbox';
    lf.market.title = 'marketable (sells on the Central Market)';
    add.appendChild(lf.market);
    const b = el('button', 'ew-btn', 'Add item');
    b.type = 'button';
    b.addEventListener('click', function () { addLootItem(lf); });
    add.appendChild(b);
    body.appendChild(add);
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
      r.appendChild(el('span', 'ew-muted ew-gnum', (s.sessions || 0) + 'x ' + C.fmtDurationShort(s.minutes || 0)));
      r.appendChild(el('span', 'ew-mprice', s.silver_per_h === null || s.silver_per_h === undefined ? '-' : C.fmtSilver(s.silver_per_h) + '/h'));
      box.appendChild(r);
    });
    body.appendChild(box);
  }

  // d = a C.buffRows row with left_s folded into `on`, so the ticking clock
  // never changes the reconcile signature (plan 049); the time is set after.
  function buffRow(row) {
    const r = el('div', 'ew-brow' + (row.on ? ' on' : ''));
    r.appendChild(el('span', 'ew-mname', row.name));
    const left = el('span', row.on ? 'ew-mprice' : 'ew-mprice ew-muted', row.on ? '-' : 'off');
    r.appendChild(left);
    if (row.on) r.ewClock = left;
    const min = el('input');
    min.type = 'text';
    min.maxLength = 8;
    min.title = 'duration: 30d, 1h30m or minutes (max 30d)';
    min.value = S.buffMin[row.name.toLowerCase()] || row.minutes;
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
    const go = el('button', 'ew-btn ew-bbtn', row.on ? 're-arm' : 'arm');
    go.type = 'button';
    go.addEventListener('click', function () { arm(row, min, xp); });
    r.appendChild(go);
    const x = el('button', 'ew-tx', 'x');
    x.type = 'button';
    x.title = 'clear';
    x.setAttribute('aria-label', 'clear buff');
    x.disabled = row.id === null;
    x.hidden = !row.on;
    x.addEventListener('click', function () { if (row.id !== null) send({ clear_buff: row.id }, 'cleared'); });
    r.appendChild(x);
    return r;
  }

  function drawBuffs(now) {
    const ui = S.ui;
    const rows = C.buffRows(S.data ? S.data.buffs : [], S.at, now);
    let f = ui.buffFrame;
    if (!f || f.list.parentNode !== ui.buffBody) {
      ui.buffBody.textContent = '';
      f = ui.buffFrame = { err: el('div', 'ew-err', ''), list: el('div', 'ew-list'), presets: el('div') };
      [f.err, f.list, f.presets].forEach(function (n) { ui.buffBody.appendChild(n); });
    }
    f.err.textContent = S.err && !S.data ? S.err : '';
    f.err.hidden = !f.err.textContent;
    f.list.className = 'ew-list' + (S.err ? ' ew-stale' : '');
    C.reconcile(f.list, rows.map(function (row) {
      // Plan 048: armed rows first (buffRows), unarmed 'off' muted, minutes short.
      const v = C.buffRowView(row);
      return { id: row.id, name: row.name, on: v.armed, minutes: v.minutes,
        xp_pct: row.xp_pct, xp_hint: row.xp_hint };
    }), function (d) { return d.name.toLowerCase(); }, buffRow);
    ui.buffClocks = [];
    rows.forEach(function (row, i) {
      const n = f.list.children[i];
      const v = C.buffRowView(row);
      if (!n || !n.ewClock || !v.armed) return;
      n.ewClock.textContent = v.left;
      ui.buffClocks.push({ name: row.name, node: n.ewClock });
    });
    drawPresets();
  }

  // Plan 018 XP buff presets: pick one, set minutes, arm (name + xp% filled).
  function drawPresets() {
    const ui = S.ui;
    const list = C.xpPresets(S.data);
    ui.buffFrame.presets.textContent = '';
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
    min.maxLength = 8;
    min.placeholder = '1h';
    min.title = 'duration: 30d, 1h30m or minutes (max 30d)';
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
    ui.buffFrame.presets.appendChild(f);
  }

  // Plan 038 "Drop rate": active drop buffs against the caps (rate and amount
  // apart), a wasted flag over cap, toggles for passive sources and the
  // Blessing of Agris ROI line (hidden by the server once the scroll is gone).
  function drawDrops() {
    const ui = S.ui;
    const body = ui.dropBody;
    body.textContent = '';
    if (!S.data) { ui.dropPill.textContent = '-'; body.appendChild(el('div', 'ew-muted', S.err ? S.err : 'loading...')); return; }
    const v = C.dropView(S.data);
    ui.dropPill.textContent = v.error ? 'error' : (v.wasted ? 'wasted' : 'ok');
    ui.dropPill.className = 'ew-pill ' + (v.error ? 'bad' : (v.wasted ? 'warn' : 'ok'));
    if (v.error) { body.appendChild(el('div', 'ew-err', 'drop table: ' + v.error)); return; }
    const box = el('div', 'ew-list' + (S.err ? ' ew-stale' : ''));
    const head = el('div', 'ew-srow');
    head.appendChild(el('span', 'ew-mname', 'drop rate'));
    head.appendChild(el('span', v.wasted ? 'ew-mprice ew-err' : 'ew-mprice', v.line));
    box.appendChild(head);
    if (v.amount) box.appendChild(el('div', 'ew-muted', v.amount + ' (separate, not capped)'));
    v.active.forEach(function (a) {
      const r = el('div', 'ew-srow');
      r.appendChild(el('span', 'ew-mname', a.name));
      r.appendChild(el('span', 'ew-muted ew-gnum', a.via));
      r.appendChild(el('span', 'ew-mprice', a.text));
      box.appendChild(r);
    });
    if (!v.active.length) box.appendChild(el('div', 'ew-muted', 'no drop buff active - arm a timer or tick a source'));
    if (v.roi) {
      const roi = el('div', v.roi.worth ? 'ew-num' : 'ew-muted', v.roi.text);
      roi.title = v.roi.dates;
      box.appendChild(roi);
      box.appendChild(el('div', 'ew-muted', v.roi.verdict + ' - ' + v.roi.dates));
    }
    body.appendChild(box);
    const det = el('details', 'ew-ldet');
    det.open = S.dropOpen;
    det.addEventListener('toggle', function () { S.dropOpen = det.open; });
    det.appendChild(el('summary', null, 'sources (tick passive ones)'));
    v.toggles.forEach(function (t) {
      const lab = el('label', 'ew-srow');
      lab.title = t.title;
      const cb = el('input');
      cb.type = 'checkbox';
      cb.checked = t.on;
      cb.addEventListener('change', function () {
        send({ drop_toggle: { id: t.id, on: cb.checked } }, t.name + (cb.checked ? ' on' : ' off'))
          .then(function (ok) { if (!ok) drawDrops(); });
      });
      lab.appendChild(cb);
      lab.appendChild(el('span', 'ew-mname', t.name + (t.timer ? ' (timer)' : '')));
      lab.appendChild(el('span', 'ew-mprice', t.value + (t.unverified ? ' ?' : '') + (t.overridden ? ' *' : '')));
      det.appendChild(lab);
    });
    body.appendChild(det);
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
    drawSummary();
    drawLog();
    drawSpots();
    drawBuffs(Date.now());
    drawDrops();
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
    if (window.EWOverrides) window.EWOverrides.mount(c.card.querySelector('h2'), 'grind'); // plan 079
    const f = {};
    const err = el('div', 'ew-err', '');
    c.body.appendChild(err);
    const head = el('div', 'ew-gsess');
    const spotLabel = el('span', 'ew-mname ew-muted', '');
    const clk = el('span', 'ew-num', '-');
    head.appendChild(spotLabel);
    head.appendChild(clk);
    c.body.appendChild(head);
    // Plan 046 "Session ended": shown while the server holds a pending stop.
    const ended = { box: el('div', 'ew-gended') };
    ended.box.setAttribute('role', 'alertdialog');
    ended.box.hidden = true;
    ended.box.appendChild(el('strong', null, 'Session ended'));
    ended.text = el('div', 'ew-muted', '');
    ended.box.appendChild(ended.text);
    const eb = el('div', 'ew-btns');
    [['Stop at exit time', stopAtExit], ['Keep running', keepRunning]].forEach(function (x) {
      const b = el('button', 'ew-btn', x[0]);
      b.type = 'button';
      b.addEventListener('click', x[1]);
      eb.appendChild(b);
    });
    ended.box.appendChild(eb);
    c.body.appendChild(ended.box);
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
    f.spot.addEventListener('change', function () { S.spot = f.spot.value; loadLoot(false); });
    // Plan 048 quick entry: minutes 90 / 1h30m, silver 1.2b / 850m / 1,234,567.
    f.minutesRow = field('minutes', 'minutes (manual)', text(8));
    f.minutes.placeholder = '1h30m';
    f.minutes.title = 'session length: 90, 1h30m (max 1d)';
    field('silver', 'silver earned', text(20));
    f.silver.placeholder = '1.2b';
    f.silver.title = 'silver: 1.2b, 850m or 1,234,567';
    field('trash', 'trash', text(9));
    f.trash.inputMode = 'numeric';
    // Plan 039: loot counts for the spot's table; values the session when given.
    const loot = el('div', 'ew-gloot');
    form.appendChild(loot);
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
    return { card: c.card, pill: c.pill, form: f, msg: m, clock: clk, activeSpot: spotLabel, err: err, loot: loot,
      ended: ended };
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
    S.panel = panel;
    panel.classList.add('ew-grind');
    const s = sessionCard();
    const w = recCard();
    const l = card('Log');
    const p = card('Spots');
    p.pill.textContent = 'avg silver/h';
    const b = card('Buffs');
    b.pill.textContent = 'tap to arm';
    const d = card('Drop rate');
    const sm = card('Summary');
    sm.pill.textContent = 'session / day / week';
    [s, sm, w, l, p, b, d].forEach(function (x) { panel.appendChild(x.card); });
    S.ui = {
      form: s.form, msg: s.msg, clock: s.clock, activeSpot: s.activeSpot, sessionErr: s.err, lootBody: s.loot,
      ended: s.ended, summaryBody: sm.body,
      sessionPill: s.pill, recBody: w.body, recPill: w.pill,
      logBody: l.body, logPill: l.pill, spotBody: p.body, buffBody: b.body, buffClocks: [],
      dropBody: d.body, dropPill: d.pill
    };
    S.lootSpot = undefined; // fresh loot box: the next loadLoot always fetches
    if (!S.timer) {
      setInterval(tick, 1000);
      if (window.EWBus) window.EWBus.on('grind', onBus);
      poll(false);
    } else {
      draw();
      loadLoot(false);
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
