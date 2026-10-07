/* EW Deadeye tab (plan 007 slice B): operator build notes per section
   (markdown editor + rendered preview) and an ordered enhancement plan. Reads
   GET /api/deadeye; writes go through the dashboard preload (window.ewApi)
   because the server refuses renderer POSTs. Text only: nothing here is ever
   executed or sent to the game. Every node is built with DOM APIs; the one
   HTML sink is the preview, fed only by C.renderMarkdown (escapes first, safe
   tag subset, no links / images / raw HTML). Unsaved drafts live per section
   and survive polls. Plan 057: each draft also autosaves (debounced 2 s) to
   localStorage; a stored draft that differs from the saved text is offered
   back (Restore / Discard), and unload is held while a note is unsaved.
   Plan 083: the first card, Portraits, shows the current class image (newest
   in-game portrait, or a gallery pick = plan 079 override with "Use newest"),
   a strip of archived portraits + Deadeye screenshots, and a collapsed
   unknown-character group with an optional class select. */
(function () {
  'use strict';
  const C = window.EWCore;
  const POLL_MS = 60000;
  const NOTE_MAX = 20000; // server note limit (C.NOTE_MAX)
  const S = {
    data: null, err: null, last: null, timer: null, ui: null, busy: false,
    section: null, drafts: {}, preview: false,
    // Plan 057: per-section autosave timers and stored drafts offered back.
    draftTimers: {}, offers: null,
    // Plan 035 EV panel: open step id, rate table (GET once), per-step inputs and replies.
    ev: null, table: null, evIn: {}, evOut: {},
    // Plan 037 shopping list: GET body (cached prices only) and its error.
    shop: null, shopErr: null,
    // Plan 083 Portraits card: GET /api/portraits?cls=Deadeye body and its error.
    gallery: null, galleryErr: null, galleryBus: false
  };

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function button(cls, label, title, fn) {
    const b = el('button', cls, label);
    b.type = 'button';
    if (title) b.title = title;
    b.addEventListener('click', fn);
    return b;
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
    return !!d && typeof d === 'object' && !Array.isArray(d) && Array.isArray(d.sections) && Array.isArray(d.plan);
  }

  function accept(d) {
    if (!valid(d)) return false;
    S.data = d;
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
    getJSON('/api/deadeye').then(accept, function (e) { S.err = e.message; }).then(draw);
    loadShop();
  }

  function loadShop() {
    getJSON('/api/deadeye/shopping').then(function (d) {
      if (d && Array.isArray(d.lines)) { S.shop = d; S.shopErr = null; }
    }, function (e) { S.shopErr = e.message; }).then(drawShop);
  }

  function msg(text) { if (S.ui) S.ui.msg.textContent = text; }

  // One POST at a time. A reply in the GET shape is taken as is; any other
  // reply (a step, a note) re-polls.
  function send(body, okText) {
    const b = bridge();
    if (!b) { msg('saving needs the Ebonwake app window'); return Promise.resolve(false); }
    if (S.busy) return Promise.resolve(false);
    S.busy = true;
    msg('saving...');
    return window.EWToast.via(b).post('/api/deadeye', body).then(function (res) {
      S.busy = false;
      if (res && res.ok) {
        if (accept(res.data)) { draw(); loadShop(); } else if (res.data && Array.isArray(res.data.lines)) {
          S.shop = res.data; // a shop_set / shop_step reply is the shopping GET body
          S.shopErr = null;
          drawShop();
        } else poll(true);
        msg(okText);
        return true;
      }
      msg('failed: ' + ((res && res.error) || 'unknown error'));
      return false;
    }, function (e) { S.busy = false; msg('failed: ' + (e && e.message || e)); return false; });
  }

  function sections() {
    return (S.data ? S.data.sections : []).filter(function (s) {
      return s && typeof s.id === 'string' && typeof s.title === 'string';
    });
  }

  function current() {
    const list = sections();
    return list.filter(function (s) { return s.id === S.section; })[0] || list[0] || null;
  }

  function savedText(sec) { return sec && typeof sec.text === 'string' ? sec.text : ''; }

  function dirty(sec) {
    return !!sec && typeof S.drafts[sec.id] === 'string' && S.drafts[sec.id] !== savedText(sec);
  }

  // ---- notes ----

  function noteText(sec) {
    return typeof S.drafts[sec.id] === 'string' ? S.drafts[sec.id] : savedText(sec);
  }

  // ---- plan 057: draft autosave (localStorage may be denied: never fatal) ----

  function readStore(key) {
    try { return localStorage.getItem(key); } catch (e) { return null; }
  }

  function dropDraft(id) {
    clearTimeout(S.draftTimers[id]);
    delete S.draftTimers[id];
    const key = C.draftKey(id);
    if (key) { try { localStorage.removeItem(key); } catch (e) { /* store denied */ } }
  }

  function storeDraft(id) {
    clearTimeout(S.draftTimers[id]);
    delete S.draftTimers[id];
    const key = C.draftKey(id);
    const sec = sections().filter(function (x) { return x.id === id; })[0];
    if (!key || !sec) return;
    if (!dirty(sec)) { dropDraft(id); return; }
    const raw = C.draftEncode(S.drafts[id], new Date().toISOString());
    try { localStorage.setItem(key, raw); } catch (e) { /* store denied: memory draft only */ }
  }

  function scheduleDraft(id) {
    clearTimeout(S.draftTimers[id]);
    S.draftTimers[id] = setTimeout(function () { storeDraft(id); }, C.DEADEYE_DRAFT_MS);
  }

  function flushDrafts() {
    Object.keys(S.draftTimers).forEach(storeDraft);
  }

  // Once, on the first good GET: stored drafts that differ from the saved text.
  function loadOffers() {
    if (S.offers || !S.data) return;
    S.offers = C.draftsToRestore(sections(), readStore, S.drafts);
  }

  function offer(sec) {
    const o = sec && S.offers ? S.offers[sec.id] : null;
    if (!o) return null;
    if (dirty(sec) || !C.draftPending(o, savedText(sec))) { delete S.offers[sec.id]; return null; }
    return o;
  }

  function restoreDraft() {
    const sec = current();
    const o = offer(sec);
    if (!o) return;
    S.drafts[sec.id] = o.text;
    delete S.offers[sec.id];
    S.ui.editor.value = o.text;
    drawNotes();
  }

  function discardDraft() {
    const sec = current();
    if (!sec || !S.offers) return;
    delete S.offers[sec.id];
    dropDraft(sec.id);
    drawNoteMeta();
  }

  function drawOffer(sec) {
    const ui = S.ui;
    const o = offer(sec);
    ui.restore.hidden = !o;
    if (!o) return;
    const at = C.fmtLocal(o.at);
    ui.restoreText.textContent = 'Unsaved draft from ' + (at ? at.text : o.at) + ' differs from the saved note.';
    ui.restoreText.title = at ? at.title : '';
  }

  function drawNoteMeta() {
    const ui = S.ui;
    const sec = current();
    loadOffers();
    drawOffer(sec);
    const n = sec ? noteText(sec).replace(/\r\n/g, '\n').length : 0;
    ui.count.textContent = n + ' / ' + NOTE_MAX;
    ui.count.className = 'ew-muted ew-gnum' + (n > NOTE_MAX ? ' ew-over' : '');
    ui.dirty.hidden = !dirty(sec);
    ui.save.disabled = !sec || !dirty(sec) || n > NOTE_MAX;
    // Plan 048: local time, UTC on hover.
    const saved = sec && typeof sec.updated === 'string' && sec.updated ? C.fmtLocal(sec.updated) : null;
    ui.updated.textContent = saved ? 'saved ' + saved.text :
      (sec && typeof sec.updated === 'string' && sec.updated ? 'saved ' + sec.updated.slice(0, 16).replace('T', ' ') + ' UTC' : '');
    ui.updated.title = saved ? saved.title : '';
    ui.secTabs.forEach(function (b) {
      const s = sections().filter(function (x) { return x.id === b.dataset.sec; })[0];
      b.classList.toggle('dirty', dirty(s));
    });
  }

  function drawPreview() {
    const ui = S.ui;
    const sec = current();
    ui.mode.textContent = S.preview ? 'Edit' : 'Preview';
    ui.editor.hidden = S.preview;
    ui.preview.hidden = !S.preview;
    if (S.preview) {
      ui.preview.innerHTML = C.renderMarkdown(sec ? noteText(sec) : '');
      if (!ui.preview.firstChild) ui.preview.appendChild(el('p', 'ew-muted', 'Nothing written yet.'));
    }
  }

  function drawNotes() {
    const ui = S.ui;
    const list = sections();
    const sec = current();
    S.section = sec ? sec.id : null;
    ui.noteErr.textContent = S.err ? (S.data ? 'last data - ' : '') + S.err : '';
    ui.tabs.textContent = '';
    ui.secTabs = list.map(function (s) {
      const b = button('ew-dtab', s.title, null, function () { pick(s.id); });
      b.dataset.sec = s.id;
      b.setAttribute('aria-selected', String(s.id === S.section));
      ui.tabs.appendChild(b);
      return b;
    });
    ui.editor.disabled = !sec;
    ui.editor.placeholder = sec ? 'markdown: # heading, - list, **bold**, *em*, `code`, ``` fenced ```' :
      (S.err && !S.data ? S.err : 'loading...');
    // Never clobber what the operator is typing: only reset the textarea on a
    // section switch or when it holds a stale saved copy.
    if (sec && (ui.editor.dataset.sec !== sec.id || document.activeElement !== ui.editor)) {
      const want = noteText(sec);
      if (ui.editor.value !== want) ui.editor.value = want;
      ui.editor.dataset.sec = sec.id;
    }
    drawNoteMeta();
    drawPreview();
  }

  function pick(id) {
    S.section = id;
    drawNotes();
  }

  function edited() {
    const sec = current();
    if (!sec) return;
    S.drafts[sec.id] = S.ui.editor.value;
    scheduleDraft(sec.id);
    drawNoteMeta();
  }

  function save() {
    const sec = current();
    if (!sec || !dirty(sec)) return;
    const text = S.drafts[sec.id];
    send({ note: { section: sec.id, text: text } }, 'saved').then(function (ok) {
      // Keep the draft only if the operator typed more while saving.
      if (ok && S.drafts[sec.id] === text) { delete S.drafts[sec.id]; dropDraft(sec.id); }
      if (ok && S.data) {
        const s = sections().filter(function (x) { return x.id === sec.id; })[0];
        if (s && s.text !== text) { s.text = text; }
      }
      drawNotes();
    });
  }

  // ---- plan ----

  // Two clicks within 3 s: a stray click never deletes.
  function armed(btn, label) {
    if (btn.dataset.armed === '1') return true;
    btn.dataset.armed = '1';
    btn.textContent = 'sure?';
    setTimeout(function () { btn.dataset.armed = ''; btn.textContent = label; }, 3000);
    return false;
  }

  function steps(r) {
    if (typeof r.steps === 'number') return r.steps;
    const a = C.levelIndex(r.current);
    const b = C.levelIndex(r.target);
    return a >= 0 && b >= 0 ? b - a : null;
  }

  function planRow(r, i, n) {
    const row = el('div', 'ew-prow' + (r.done === true ? ' done' : ''));
    const lab = el('label', 'ew-tlabel');
    const box = el('input');
    box.type = 'checkbox';
    box.checked = r.done === true;
    box.title = 'done';
    box.addEventListener('change', function () {
      send({ step_done: { id: r.id, done: box.checked } }, box.checked ? 'done' : 'reopened');
    });
    lab.appendChild(box);
    const name = el('span', 'ew-mname', r.item);
    if (typeof r.note === 'string' && r.note) name.title = r.note;
    lab.appendChild(name);
    row.appendChild(lab);
    row.appendChild(el('span', 'ew-mprice ew-gnum', String(r.current) + ' -> ' + String(r.target)));
    const k = steps(r);
    row.appendChild(el('span', 'ew-muted ew-gnum', k === null ? '-' : k + (k === 1 ? ' step' : ' steps')));
    const up = button('ew-tx', '^', 'move up', function () { send({ move_step: { id: r.id, dir: -1 } }, 'moved'); });
    up.disabled = i === 0;
    row.appendChild(up);
    const dn = button('ew-tx', 'v', 'move down', function () { send({ move_step: { id: r.id, dir: 1 } }, 'moved'); });
    dn.disabled = i === n - 1;
    row.appendChild(dn);
    const x = button('ew-tx', 'x', 'delete (click twice)', function () {
      if (armed(x, 'x')) send({ delete_step: r.id }, 'deleted');
    });
    row.appendChild(x);
    row.appendChild(button('ew-tx', 'EV', 'expected attempts and cost', function () {
      S.ev = S.ev === r.id ? null : r.id;
      if (S.ev && !S.table) loadTable();
      drawPlan();
    }));
    if (typeof r.note === 'string' && r.note) row.appendChild(el('div', 'ew-pnote ew-muted ew-gnum', r.note));
    if (S.ev === r.id) row.appendChild(evPanel(r));
    return row;
  }

  // ---- EV panel (plan 035) ----

  function loadTable() {
    getJSON('/api/deadeye/enhance').then(function (d) {
      if (d && Array.isArray(d.families) && Array.isArray(d.rows)) S.table = d;
    }, function (e) { S.table = { err: e.message, families: [], rows: [] }; }).then(draw);
  }

  function evInputs(r) {
    if (!S.evIn[r.id]) {
      const fams = S.table ? S.table.families : [];
      S.evIn[r.id] = { family: C.enhanceFamilyGuess(r.item, fams) || fams[0] || '', fs: '0', crons: false };
    }
    return S.evIn[r.id];
  }

  function tableSteps(family, r) {
    const have = (S.table ? S.table.rows : []).filter(function (x) { return x && x.family === family; })
      .map(function (x) { return x.step; });
    return C.enhanceSubSteps(r.current, r.target).filter(function (s) { return have.indexOf(s) >= 0; });
  }

  function calc(r) {
    const inp = evInputs(r);
    const steps = tableSteps(inp.family, r);
    if (C.parseFs(inp.fs) === null) { S.evOut[r.id] = { err: 'FS: a whole number 0-' + C.ENHANCE_MAX_FS }; drawPlan(); return; }
    if (!steps.length) { S.evOut[r.id] = { err: 'no ' + inp.family + ' rate rows for ' + r.current + ' -> ' + r.target }; drawPlan(); return; }
    S.evOut[r.id] = { busy: true };
    drawPlan();
    Promise.all(steps.map(function (s) {
      return getJSON(C.enhancePath(inp.family, s, inp.fs, inp.crons)).catch(function (e) { return { step: s, cost_note: e.message }; });
    })).then(function (rows) { S.evOut[r.id] = { rows: rows }; drawPlan(); });
  }

  function evPanel(r) {
    const box = el('div', 'ew-pnote ew-dev');
    if (!S.table) { box.appendChild(el('div', 'ew-muted', 'loading rate table...')); return box; }
    if (S.table.err) { box.appendChild(el('div', 'ew-err', S.table.err)); return box; }
    const inp = evInputs(r);
    const line = el('div', 'ew-dline');
    const fam = el('select');
    fam.title = 'gear family';
    S.table.families.forEach(function (f) {
      const o = el('option', null, f);
      o.value = f;
      fam.appendChild(o);
    });
    fam.value = inp.family;
    fam.addEventListener('change', function () { inp.family = fam.value; });
    const fs = el('input');
    fs.type = 'text';
    fs.maxLength = 3;
    fs.size = 4;
    fs.value = inp.fs;
    fs.title = 'failstack';
    fs.addEventListener('input', function () { inp.fs = fs.value; });
    const cl = el('label', 'ew-tlabel');
    const cr = el('input');
    cr.type = 'checkbox';
    cr.checked = inp.crons;
    cr.addEventListener('change', function () { inp.crons = cr.checked; });
    cl.appendChild(cr);
    cl.appendChild(el('span', null, 'crons'));
    [fam, el('span', 'ew-muted', 'FS'), fs, cl,
      button('ew-btn ew-bbtn', 'Calc', 'expected attempts and silver per level', function () { calc(r); }),
      button('ew-btn ew-bbtn', 'Use in list', 'shopping list uses this family, FS and crons', function () {
        const n = C.parseFs(inp.fs);
        if (n === null) { msg('FS: a whole number 0-' + C.ENHANCE_MAX_FS); return; }
        send({ shop_step: { id: r.id, family: inp.family || null, fs: n, crons: inp.crons } }, 'shopping list updated');
      })]
      .forEach(function (x) { line.appendChild(x); });
    box.appendChild(line);
    const out = S.evOut[r.id];
    if (out && out.busy) box.appendChild(el('div', 'ew-muted', 'calculating...'));
    if (out && out.err) box.appendChild(el('div', 'ew-err', out.err));
    if (out && out.rows) {
      out.rows.forEach(function (b) {
        const f = C.fmtEv(b);
        box.appendChild(el('div', 'ew-gnum', C.evLine(b)));
        if (f.note) box.appendChild(el('div', 'ew-muted', f.step + ': ' + f.note));
      });
      box.appendChild(el('div', 'ew-muted', '~ = formula estimate between table points; constant FS; ' +
        'a failure\'s downgrade re-climb is not counted.'));
    }
    return box;
  }

  function drawPlan() {
    const ui = S.ui;
    const body = ui.planBody;
    body.textContent = '';
    const list = (S.data ? S.data.plan : []).filter(function (r) {
      return r && typeof r.id === 'string' && typeof r.item === 'string';
    });
    const pr = S.data && S.data.progress && typeof S.data.progress.total === 'number' ? S.data.progress : null;
    ui.planPill.textContent = pr ? pr.done + ' / ' + pr.total + ' done' : '-';
    ui.planPill.className = 'ew-pill ' + (pr && pr.total && pr.done === pr.total ? 'ok' : 'unknown');
    if (!list.length) {
      const bad = S.err && !S.data;
      body.appendChild(el('div', bad ? 'ew-err' : 'ew-muted', bad ? S.err : (S.data ? 'No steps yet.' : 'loading...')));
      return;
    }
    const box = el('div', 'ew-list' + (S.err ? ' ew-stale' : ''));
    list.forEach(function (r, i) { box.appendChild(planRow(r, i, list.length)); });
    body.appendChild(box);
  }

  // ---- shopping list (plan 037) ----

  function watchLine(ln) {
    const b = bridge();
    const body = C.shopWatchBody(ln.id);
    if (!b) { msg('watching needs the Ebonwake app window'); return; }
    if (!body) return;
    window.EWToast.via(b).post('/api/market/watch', body).then(function (res) {
      if (res && res.ok) { msg('watching ' + C.fmtShopLine(ln).name); loadShop(); } else msg('failed: ' + ((res && res.error) || 'unknown error'));
    }, function (e) { msg('failed: ' + (e && e.message || e)); });
  }

  function shopRow(ln) {
    const f = C.fmtShopLine(ln);
    const row = el('div', 'ew-prow');
    const name = el('span', 'ew-mname', f.name);
    if (f.preorder) name.title = f.preorder;
    row.appendChild(name);
    row.appendChild(el('span', 'ew-muted ew-gnum', 'x' + f.qty));
    row.appendChild(el('span', 'ew-muted ew-gnum', '@ ' + f.unit));
    row.appendChild(el('span', 'ew-mprice ew-gnum', f.total));
    const w = button('ew-tx', ln.watched === true ? 'watched' : 'watch', 'add to the market watchlist (EW only)', function () { watchLine(ln); });
    w.disabled = ln.watched === true;
    row.appendChild(w);
    const extra = [f.note, f.preorder].filter(Boolean).join('; ');
    if (extra) row.appendChild(el('div', 'ew-pnote ew-muted', extra));
    return row;
  }

  function drawShop() {
    const ui = S.ui;
    if (!ui || !ui.shopBody.isConnected) return;
    const body = ui.shopBody;
    body.textContent = '';
    const d = S.shop;
    const s = C.fmtShopping(d);
    ui.shopPill.textContent = d ? s.total : '-';
    ui.shopPill.className = 'ew-pill ' + (d && d.total !== null ? 'ok' : 'unknown');
    if (!d) {
      body.appendChild(el('div', S.shopErr ? 'ew-err' : 'ew-muted', S.shopErr || 'loading...'));
      return;
    }
    if (S.shopErr) body.appendChild(el('div', 'ew-err', 'last data - ' + S.shopErr));
    const f = ui.shopForm;
    const st = d.settings || {};
    if (document.activeElement !== f.silver && document.activeElement !== f.hours) {
      f.silver.placeholder = 'silver on hand (' + C.fmtSilver(st.silver_on_hand) + ')';
      f.hours.placeholder = 'h/day (' + st.hours_per_day + ')';
    }
    body.appendChild(el('div', 'ew-gnum', 'total ' + s.total));
    body.appendChild(el('div', 'ew-muted ew-gnum', s.afford));
    // Plan 081: where silver on hand and h/day come from, and how old they are.
    const inp = d.inputs || {};
    [['silver on hand ' + C.fmtSilver(st.silver_on_hand), inp.silver_on_hand],
      ['h/day ' + st.hours_per_day, inp.hours_per_day]].forEach(function (p) {
      const src = C.derivedText(p[1]);
      if (src.text) body.appendChild(el('div', 'ew-muted ew-gnum' + (src.stale ? ' ew-stale' : ''), p[0] + ' (' + src.text + ')'));
    });
    if (!d.lines.length) {
      body.appendChild(el('div', 'ew-muted', 'Nothing to buy: no open step has priced materials ' +
        '(turn crons on with EV -> Use in list).'));
    } else {
      const box = el('div', 'ew-list');
      d.lines.forEach(function (ln) { if (ln && typeof ln.id === 'number') box.appendChild(shopRow(ln)); });
      body.appendChild(box);
    }
    (Array.isArray(d.steps) ? d.steps : []).forEach(function (x) {
      if (x && typeof x.note === 'string' && x.note) body.appendChild(el('div', 'ew-muted', x.item + ': ' + x.note));
    });
    body.appendChild(el('div', 'ew-muted', 'expected quantities (mean attempts, constant FS), cached prices; ' +
      'materials beyond crons come from rate rows that list them.'));
  }

  function saveShop() {
    const f = S.ui.shopForm;
    const r = C.parseShopForm({ silver: f.silver.value, hours: f.hours.value });
    if (!r.ok) { msg(r.error); return; }
    send(r.body, 'saved').then(function (ok) {
      if (ok) { f.silver.value = ''; f.hours.value = ''; }
    });
  }

  function shopCard() {
    const c = card('Shopping list', 'ew-dshop');
    const body = el('div', 'ew-cbody');
    c.card.appendChild(body);
    const form = el('form', 'ew-form ew-dform');
    const silver = el('input');
    silver.type = 'text';
    silver.maxLength = 22;
    silver.autocomplete = 'off';
    silver.placeholder = 'silver on hand';
    silver.title = 'silver on hand: 1.2b, 850m or 1,234,567';
    const hours = el('input');
    hours.type = 'text';
    hours.maxLength = 6;
    hours.title = 'grind hours per day: 2.5 or 2h30m';
    hours.size = 6;
    hours.autocomplete = 'off';
    hours.placeholder = 'h/day';
    const line = el('div', 'ew-dline');
    [silver, hours, button('ew-btn ew-bbtn', 'Save', 'silver on hand and grind hours per day', saveShop)]
      .forEach(function (x) { line.appendChild(x); });
    form.appendChild(line);
    form.addEventListener('submit', function (ev) { ev.preventDefault(); saveShop(); });
    c.card.appendChild(form);
    return { card: c.card, body: body, pill: c.pill, form: { silver: silver, hours: hours } };
  }

  function addStep() {
    const f = S.ui.form;
    const r = C.parseStepForm({ item: f.item.value, current: f.current.value, target: f.target.value, note: f.note.value });
    if (!r.ok) { msg(r.error); return; }
    send(r.body, 'added').then(function (ok) {
      if (ok) { f.item.value = ''; f.note.value = ''; }
    });
  }

  function levels() {
    const l = S.data && Array.isArray(S.data.levels) ? S.data.levels.filter(function (v) { return C.levelIndex(v) >= 0; }) : [];
    return l.length ? l : C.DEADEYE_LEVELS;
  }

  // Rebuilt only when the served LEVELS differ, so a poll never closes an
  // open dropdown or loses the operator's pick.
  function fillSelect(sel, pick) {
    const want = levels();
    if (Array.prototype.map.call(sel.options, function (o) { return o.value; }).join() === want.join()) return;
    const keep = sel.value || pick;
    sel.textContent = '';
    want.forEach(function (v) {
      const o = el('option', null, v);
      o.value = v;
      sel.appendChild(o);
    });
    sel.value = want.indexOf(keep) >= 0 ? keep : want[0];
  }

  // ---- Stacks card (plan 036) ----

  function fillOptions(sel, values, labels) {
    if (Array.prototype.map.call(sel.options, function (o) { return o.value; }).join() === values.join()) return;
    const keep = sel.value;
    sel.textContent = '';
    values.forEach(function (v) {
      const o = el('option', null, labels ? labels[v] || v : v);
      o.value = v;
      sel.appendChild(o);
    });
    if (values.indexOf(keep) >= 0) sel.value = keep;
  }

  function stacksForm(parse, okText, after) {
    const r = parse();
    if (!r.ok) { S.ui.stMsg.textContent = r.error; return; }
    S.ui.stMsg.textContent = '';
    send(r.body, okText).then(function (ok) { if (ok && after) after(); });
  }

  function drawStacks() {
    const ui = S.ui;
    const body = ui.stBody;
    body.textContent = '';
    const fams = S.table && Array.isArray(S.table.families) ? S.table.families : [];
    fillOptions(ui.st.family, fams);
    const s = S.data && S.data.stacks;
    if (!s) {
      body.appendChild(el('div', 'ew-muted', S.data ? 'stacks need a newer server' : 'loading...'));
      return;
    }
    const f = C.fmtStacks(s);
    body.appendChild(el('div', 'ew-gnum', 'next: ' + f.advice));
    body.appendChild(el('div', 'ew-gnum', f.budget));
    if (f.unknown) body.appendChild(el('div', 'ew-muted', f.unknown));
    const list = el('div', 'ew-list');
    f.bank.forEach(function (b) {
      const row = el('div', 'ew-dline');
      row.appendChild(el('span', 'ew-gnum', b.text));
      row.appendChild(button('ew-tx', 'use', 'take one out of the bank', function () {
        send({ fs_use: { kind: b.kind, value: b.value } }, 'used');
      }));
      list.appendChild(row);
    });
    f.agris.forEach(function (a) {
      const row = el('div', 'ew-dline');
      row.appendChild(el('span', 'ew-gnum', a.text));
      row.appendChild(button('ew-tx', 'x', 'clear these pity stacks', function () {
        send({ agris_set: { family: a.family, step: a.step, stacks: 0 } }, 'cleared');
      }));
      list.appendChild(row);
    });
    if (!f.bank.length && !f.agris.length) list.appendChild(el('div', 'ew-muted', 'No stored stacks or Agris stacks yet.'));
    body.appendChild(list);
  }

  function input(ph, max, size) {
    const i = el('input');
    i.type = 'text';
    i.maxLength = max;
    i.size = size;
    i.placeholder = ph;
    i.autocomplete = 'off';
    return i;
  }

  function stacksCard() {
    const c = card('Stacks', 'ew-dstacks');
    c.pill.textContent = 'FS / Agris / crons';
    const body = el('div', 'ew-cbody ew-dev');
    c.card.appendChild(body);
    const st = {};
    const form = el('div', 'ew-form ew-dform');
    st.kind = el('select');
    st.kind.title = 'stack kind';
    fillOptions(st.kind, C.FS_KINDS, { advice: 'Advice', saved: 'Saved', cry: 'Cry' });
    st.value = input('FS', 3, 4);
    st.count = input('count', 3, 4);
    const fsForm = function () { return C.parseFsForm({ kind: st.kind.value, value: st.value.value, count: st.count.value }); };
    const l1 = el('div', 'ew-dline');
    [st.kind, st.value, st.count,
      button('ew-btn ew-bbtn', 'Add', 'store failstacks', function () {
        stacksForm(fsForm, 'stored', function () { st.value.value = ''; st.count.value = ''; });
      }),
      button('ew-btn ew-bbtn', 'Use', 'take failstacks out', function () {
        stacksForm(function () { return C.parseFsForm({ kind: st.kind.value, value: st.value.value, count: st.count.value }, true); }, 'used');
      })].forEach(function (x) { l1.appendChild(x); });
    form.appendChild(l1);
    st.family = el('select');
    st.family.title = 'gear family';
    st.step = el('select');
    st.step.title = 'level the attempt reaches';
    fillOptions(st.step, C.ENHANCE_STEPS);
    st.step.value = 'PRI';
    st.stacks = input('Agris stacks', 4, 6);
    const l2 = el('div', 'ew-dline');
    [st.family, st.step, st.stacks,
      button('ew-btn ew-bbtn', 'Set', 'Agris pity stacks (0 clears)', function () {
        stacksForm(function () { return C.parseAgrisForm({ family: st.family.value, step: st.step.value, stacks: st.stacks.value }); },
          'set', function () { st.stacks.value = ''; });
      })].forEach(function (x) { l2.appendChild(x); });
    form.appendChild(l2);
    st.owned = input('crons owned', 13, 9);
    st.weekly = input('crons / week', 10, 9);
    const l3 = el('div', 'ew-dline');
    [st.owned, st.weekly,
      button('ew-btn ew-bbtn', 'Save', 'crons on hand and weekly income (blank = unchanged)', function () {
        stacksForm(function () { return C.parseCronsForm({ owned: st.owned.value, weekly_income: st.weekly.value }); },
          'saved', function () { st.owned.value = ''; st.weekly.value = ''; });
      })].forEach(function (x) { l3.appendChild(x); });
    form.appendChild(l3);
    const m = el('div', 'ew-muted ew-msg', '');
    form.appendChild(m);
    c.card.appendChild(form);
    c.card.appendChild(el('div', 'ew-muted', 'Typed by you; budget assumes each attempt at the level\'s soft-cap FS.'));
    return { card: c.card, body: body, st: st, msg: m };
  }

  function draw() {
    const ui = S.ui;
    if (!ui || !ui.planBody.isConnected) return;
    fillSelect(ui.form.current, '+15');
    fillSelect(ui.form.target, 'PRI');
    drawNotes();
    drawPlan();
    drawStacks();
    drawShop();
  }

  // ---- mount ----

  function card(title, cls) {
    const c = el('section', 'ew-card ew-mcard ' + cls);
    const h = el('h2', null, title);
    const pill = el('span', 'ew-pill unknown', '');
    h.appendChild(pill);
    c.appendChild(h);
    return { card: c, pill: pill };
  }

  function notesCard() {
    const c = card('Build notes', 'ew-dnotes');
    c.pill.textContent = 'markdown';
    const err = el('div', 'ew-err', '');
    c.card.appendChild(err);
    const tabs = el('div', 'ew-dtabs');
    tabs.setAttribute('role', 'tablist');
    c.card.appendChild(tabs);
    const bar = el('div', 'ew-dtools');
    const mode = button('ew-btn ew-bbtn', 'Preview', 'toggle editor / rendered preview', function () {
      S.preview = !S.preview;
      drawPreview();
    });
    const saveBtn = button('ew-btn ew-bbtn', 'Save', 'save this section', save);
    const mark = el('span', 'ew-ddirty', 'unsaved');
    mark.hidden = true;
    const updated = el('span', 'ew-muted ew-gnum ew-dupd', '');
    const count = el('span', 'ew-muted ew-gnum', '0 / ' + NOTE_MAX);
    [mode, saveBtn, mark, updated, count].forEach(function (x) { bar.appendChild(x); });
    c.card.appendChild(bar);
    const restore = el('div', 'ew-drestore');
    restore.hidden = true;
    const restoreText = el('span', 'ew-muted', '');
    restore.appendChild(restoreText);
    restore.appendChild(button('ew-btn ew-bbtn', 'Restore', 'put the draft back in the editor (not saved yet)', restoreDraft));
    restore.appendChild(button('ew-btn ew-bbtn', 'Discard', 'forget the stored draft', discardDraft));
    c.card.appendChild(restore);
    const editor = el('textarea', 'ew-deditor');
    editor.spellcheck = false;
    editor.maxLength = NOTE_MAX;
    editor.addEventListener('input', edited);
    editor.addEventListener('keydown', function (ev) {
      if (ev.ctrlKey && (ev.key === 's' || ev.key === 'S')) { ev.preventDefault(); save(); }
    });
    c.card.appendChild(editor);
    const preview = el('div', 'ew-md ew-cbody');
    preview.hidden = true;
    c.card.appendChild(preview);
    return {
      card: c.card, noteErr: err, tabs: tabs, secTabs: [], mode: mode, save: saveBtn, dirty: mark,
      updated: updated, count: count, editor: editor, preview: preview, restore: restore, restoreText: restoreText
    };
  }

  function planCard() {
    const c = card('Enhancement plan', 'ew-dplan');
    const body = el('div', 'ew-cbody');
    c.card.appendChild(body);
    const f = {};
    const form = el('form', 'ew-form ew-dform');
    const item = el('input');
    item.type = 'text';
    item.maxLength = 60;
    item.placeholder = 'item';
    item.autocomplete = 'off';
    f.item = item;
    const cur = el('select');
    cur.title = 'current';
    const tgt = el('select');
    tgt.title = 'target';
    f.current = cur;
    f.target = tgt;
    const note = el('input');
    note.type = 'text';
    note.maxLength = 200;
    note.placeholder = 'note (optional)';
    note.autocomplete = 'off';
    f.note = note;
    const row1 = el('div', 'ew-dline');
    row1.appendChild(item);
    row1.appendChild(cur);
    row1.appendChild(el('span', 'ew-muted', '->'));
    row1.appendChild(tgt);
    form.appendChild(row1);
    const row2 = el('div', 'ew-dline');
    row2.appendChild(note);
    row2.appendChild(button('ew-btn ew-bbtn', 'Add', 'add a step to the plan', addStep));
    form.appendChild(row2);
    const m = el('div', 'ew-muted ew-msg', '');
    form.appendChild(m);
    form.addEventListener('submit', function (ev) { ev.preventDefault(); addStep(); });
    c.card.appendChild(form);
    return { card: c.card, body: body, pill: c.pill, form: f, msg: m };
  }

  // ---- calculators (plan 055) ----
  // Read-only GETs over sourced static data; the stone price comes from the
  // market cache unless typed. Nothing is posted.

  function calcInput(ph, title, max) {
    const i = el('input');
    i.type = 'text';
    i.maxLength = max;
    i.autocomplete = 'off';
    i.placeholder = ph;
    i.title = title;
    return i;
  }

  function runCalc(kind) {
    const f = S.ui.calc;
    const form = kind === 'crystal'
      ? { on_hand: f.onHand.value, levels: f.levels.value, per_level: f.perLevel.value }
      : { slot: f.slot.value, from: f.from.value, to: f.to.value, grade: f.grade.value, price: f.price.value };
    const out = kind === 'crystal' ? f.crystalOut : f.caphrasOut;
    const q = C.calcQuery(kind, form);
    out.textContent = '';
    if (!q.ok) { out.appendChild(el('div', 'ew-err', q.error)); return; }
    getJSON(q.path).then(function (r) {
      const s = C.fmtCalc(r);
      out.textContent = '';
      out.appendChild(el('div', 'ew-gnum', s.main));
      out.appendChild(el('div', 'ew-muted ew-gnum', s.sub));
    }, function (e) { out.textContent = ''; out.appendChild(el('div', 'ew-err', e.message)); });
  }

  function loadCalcTables(f, pill) {
    getJSON('/api/deadeye/calc').then(function (d) {
      const cap = d && d.caphras;
      if (!cap || !Array.isArray(cap.slots)) return;
      f.slot.textContent = '';
      cap.slots.forEach(function (s) {
        const o = el('option', null, s.name);
        o.value = s.id;
        f.slot.appendChild(o);
      });
      f.grade.textContent = '';
      const dflt = el('option', null, 'slot grade');
      dflt.value = '';
      f.grade.appendChild(dflt);
      Object.keys(cap.grades || {}).forEach(function (g) {
        const info = cap.grades[g] || {};
        const o = el('option', null, g + (info.allowed ? '' : ' (no Caphras)') + (info.verified ? '' : ' ?'));
        o.value = g;
        f.grade.appendChild(o);
      });
      pill.textContent = typeof cap.price === 'number' ? 'stone ' + C.fmtSilver(cap.price) : 'no stone price';
      pill.className = 'ew-pill ' + (typeof cap.price === 'number' ? 'ok' : 'unknown');
    }, function (e) { pill.textContent = 'error'; pill.title = e.message; });
  }

  function calcCard() {
    const c = card('Calculators', 'ew-dcalc');
    const f = {};
    const crystal = el('form', 'ew-form ew-dform');
    crystal.appendChild(el('div', 'ew-muted', 'Jetina boss crystals: weeks to reform'));
    const row1 = el('div', 'ew-dline');
    f.onHand = calcInput('crystals on hand', 'Concentrated Boss Crystals on hand', 12);
    f.levels = calcInput('levels', 'reform levels wanted (1-20)', 2);
    f.perLevel = calcInput('per level (opt)', 'crystals per level if known (sources say 60-120)', 6);
    [f.onHand, f.levels, f.perLevel].forEach(function (i) { row1.appendChild(i); });
    row1.appendChild(button('ew-btn ew-bbtn', 'Weeks', 'weeks of Jetina exchanges', function () { runCalc('crystal'); }));
    crystal.appendChild(row1);
    f.crystalOut = el('div', 'ew-cbody');
    crystal.appendChild(f.crystalOut);
    crystal.addEventListener('submit', function (ev) { ev.preventDefault(); runCalc('crystal'); });
    c.card.appendChild(crystal);
    const caphras = el('form', 'ew-form ew-dform');
    caphras.appendChild(el('div', 'ew-muted', 'Caphras: stones and silver (not on Blackstar)'));
    const row2 = el('div', 'ew-dline');
    f.slot = el('select');
    f.slot.title = 'slot';
    f.grade = el('select');
    f.grade.title = 'gear grade (guard)';
    f.from = calcInput('from', 'current Caphras level (0-20)', 2);
    f.to = calcInput('to', 'target Caphras level (0-20)', 2);
    f.price = calcInput('stone price (opt)', 'Caphras Stone price, e.g. 2.1m; blank = cached market price', 16);
    [f.slot, f.grade, f.from, f.to, f.price].forEach(function (i) { row2.appendChild(i); });
    row2.appendChild(button('ew-btn ew-bbtn', 'Cost', 'Caphras stones and silver', function () { runCalc('caphras'); }));
    caphras.appendChild(row2);
    f.caphrasOut = el('div', 'ew-cbody');
    caphras.appendChild(f.caphrasOut);
    caphras.addEventListener('submit', function (ev) { ev.preventDefault(); runCalc('caphras'); });
    c.card.appendChild(caphras);
    loadCalcTables(f, c.pill);
    return { card: c.card, form: f };
  }

  // ---- plan 083: Portraits card (current image, history + screenshots strip,
  // optional pick = plan 079 override, "Use newest", unknown-character group) ----

  const GALLERY_CLS = 'Deadeye';

  function loadGallery() {
    getJSON('/api/portraits?cls=' + encodeURIComponent(GALLERY_CLS)).then(function (v) {
      S.gallery = v;
      S.galleryErr = null;
    }, function (e) { S.galleryErr = e.message; }).then(drawGallery);
  }

  function sendPortraits(body) {
    const b = bridge();
    const g = S.ui && S.ui.gallery;
    if (!b) { if (g) g.msg.textContent = 'picking needs the Ebonwake app window'; return; }
    window.EWToast.via(b).post('/api/portraits', body).then(function (res) {
      if (res && res.ok && res.data && res.data.classes) { S.gallery = res.data; drawGallery(); }
      else if (g) g.msg.textContent = 'failed: ' + ((res && res.error) || 'unknown error');
    }, function (e) { if (g) g.msg.textContent = 'failed: ' + (e && e.message || e); });
  }

  function thumb(item) {
    const img = el('img', 'ew-gthumb');
    img.src = item.src;
    img.alt = item.alt;
    img.loading = 'lazy';
    img.width = 48;
    img.height = 62;
    return img;
  }

  function drawGallery() {
    const g = S.ui && S.ui.gallery;
    if (!g) return;
    const m = C.portraitGallery(S.gallery, GALLERY_CLS);
    g.err.textContent = S.galleryErr || '';
    g.body.textContent = '';
    g.msg.textContent = '';
    if (m.empty) {
      g.body.appendChild(el('div', 'ew-muted', m.emptyText));
    } else {
      const top = el('div', 'ew-gtop');
      if (m.current) {
        const img = el('img', 'ew-gcur');
        img.src = m.current.src;
        img.alt = m.current.alt;
        img.width = 156;
        img.height = 201;
        top.appendChild(img);
      }
      const src = el('div', 'ew-gsrc');
      if (m.current && m.current.pinned) {
        if (m.current.badge) {
          const p = el('span', 'ew-pill warn ' + m.current.badge.cls, m.current.badge.text);
          p.title = m.current.badge.title;
          src.appendChild(p);
        }
        src.appendChild(el('span', 'ew-muted', ' ' + m.current.source + ' '));
        const key = m.current.key;
        src.appendChild(button('ew-btn ew-bbtn', 'Use newest', 'back to the newest in-game portrait',
          function () { sendPortraits({ clear: key }); }));
      } else if (m.current) {
        src.appendChild(el('span', 'ew-muted', m.current.source));
      }
      top.appendChild(src);
      g.body.appendChild(top);
      const strip = el('div', 'ew-gstrip');
      strip.setAttribute('role', 'group');
      strip.setAttribute('aria-label', GALLERY_CLS + ' portraits and screenshots');
      const btns = m.items.map(function (it) {
        const b = button('ew-gitem', '', it.alt, function () {
          sendPortraits({ pick: { cls: GALLERY_CLS, id: it.id } });
        });
        b.setAttribute('aria-pressed', it.pressed ? 'true' : 'false');
        b.tabIndex = it.tabindex;
        b.appendChild(thumb(it));
        strip.appendChild(b);
        return b;
      });
      strip.addEventListener('keydown', function (ev) {
        const i = btns.indexOf(document.activeElement);
        const next = C.galleryMove(i, ev.key, btns.length);
        if (next === null || i < 0) return;
        ev.preventDefault();
        btns.forEach(function (b, k) { b.tabIndex = k === next ? 0 : -1; });
        btns[next].focus();
      });
      g.body.appendChild(strip);
    }
    if (m.unknown.length) {
      const det = el('details', 'ew-gunknown');
      det.appendChild(el('summary', null, 'Unknown character (' + m.unknown.length + ')'));
      const names = Object.keys(S.gallery && S.gallery.classes ? S.gallery.classes : {});
      if (names.indexOf(GALLERY_CLS) < 0) names.unshift(GALLERY_CLS);
      m.unknown.forEach(function (it) {
        const row = el('div', 'ew-gurow');
        row.appendChild(thumb(it));
        const sel = el('select');
        sel.title = 'optional: whose ' + (it.kind === 'shot' ? 'screenshot' : 'portrait') + ' is this?';
        sel.setAttribute('aria-label', 'class for ' + it.alt);
        sel.appendChild(el('option', null, 'class (optional)')).value = '';
        names.forEach(function (n) { sel.appendChild(el('option', null, n)).value = n; });
        sel.addEventListener('change', function () {
          if (sel.value) sendPortraits({ bind: { id: it.id, cls: sel.value } });
        });
        row.appendChild(sel);
        det.appendChild(row);
      });
      g.body.appendChild(det);
    }
  }

  function galleryCard() {
    const c = card('Portraits', 'ew-dgallery');
    c.pill.hidden = true;
    const err = el('div', 'ew-err', '');
    const body = el('div', 'ew-gbody');
    const m = el('div', 'ew-muted ew-msg', '');
    c.card.appendChild(err);
    c.card.appendChild(body);
    c.card.appendChild(m);
    return { card: c.card, err: err, body: body, msg: m };
  }

  function mount(panel) {
    S.panel = panel;
    panel.classList.add('ew-deadeye');
    const gal = galleryCard();
    panel.appendChild(gal.card);
    const n = notesCard();
    const p = planCard();
    const k = stacksCard();
    const sh = shopCard();
    const ca = calcCard();
    panel.appendChild(n.card);
    panel.appendChild(p.card);
    panel.appendChild(k.card);
    panel.appendChild(sh.card);
    panel.appendChild(ca.card);
    S.ui = Object.assign(n, {
      planBody: p.body, planPill: p.pill, form: p.form, msg: p.msg,
      stBody: k.body, st: k.st, stMsg: k.msg,
      shopBody: sh.body, shopPill: sh.pill, shopForm: sh.form, calc: ca.form, gallery: gal
    });
    if (!S.table) loadTable(); // plan 036: families for the Agris picker
    loadGallery();
    if (window.EWBus && !S.galleryBus) { // a new archive / binding / pick re-reads the gallery
      S.galleryBus = true;
      window.EWBus.on('portraits', loadGallery);
    }
    if (!S.timer) poll(false);
    else draw();
  }

  function show() { poll(false); loadGallery(); }

  // Plan 057: store every pending draft now; hold the unload while a note is
  // unsaved (main asks Leave / Stay - the draft survives either way).
  window.addEventListener('beforeunload', function (ev) {
    flushDrafts();
    if (!sections().some(dirty)) return;
    ev.preventDefault();
    ev.returnValue = '';
  });

  window.EWDeadeye = { mount: mount, show: show };
})();
