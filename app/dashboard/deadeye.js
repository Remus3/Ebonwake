/* EW Deadeye tab (plan 007 slice B): operator build notes per section
   (markdown editor + rendered preview) and an ordered enhancement plan. Reads
   GET /api/deadeye; writes go through the dashboard preload (window.ewApi)
   because the server refuses renderer POSTs. Text only: nothing here is ever
   executed or sent to the game. Every node is built with DOM APIs; the one
   HTML sink is the preview, fed only by C.renderMarkdown (escapes first, safe
   tag subset, no links / images / raw HTML). Unsaved drafts live per section
   and survive polls. */
(function () {
  'use strict';
  const C = window.EWCore;
  const POLL_MS = 60000;
  const NOTE_MAX = 20000; // server note limit (C.NOTE_MAX)
  const S = {
    data: null, err: null, last: null, timer: null, ui: null, busy: false,
    section: null, drafts: {}, preview: false,
    // Plan 035 EV panel: open step id, rate table (GET once), per-step inputs and replies.
    ev: null, table: null, evIn: {}, evOut: {}
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
    S.timer = setTimeout(function () { poll(true); }, POLL_MS);
    getJSON('/api/deadeye').then(accept, function (e) { S.err = e.message; }).then(draw);
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
        if (accept(res.data)) draw();
        else poll(true);
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

  function drawNoteMeta() {
    const ui = S.ui;
    const sec = current();
    const n = sec ? noteText(sec).replace(/\r\n/g, '\n').length : 0;
    ui.count.textContent = n + ' / ' + NOTE_MAX;
    ui.count.className = 'ew-muted ew-gnum' + (n > NOTE_MAX ? ' ew-over' : '');
    ui.dirty.hidden = !dirty(sec);
    ui.save.disabled = !sec || !dirty(sec) || n > NOTE_MAX;
    ui.updated.textContent = sec && typeof sec.updated === 'string' && sec.updated ? 'saved ' + sec.updated.slice(0, 16).replace('T', ' ') + ' UTC' : '';
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
    drawNoteMeta();
  }

  function save() {
    const sec = current();
    if (!sec || !dirty(sec)) return;
    const text = S.drafts[sec.id];
    send({ note: { section: sec.id, text: text } }, 'saved').then(function (ok) {
      // Keep the draft only if the operator typed more while saving.
      if (ok && S.drafts[sec.id] === text) delete S.drafts[sec.id];
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
    }, function (e) { S.table = { err: e.message, families: [], rows: [] }; }).then(drawPlan);
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
      button('ew-btn ew-bbtn', 'Calc', 'expected attempts and silver per level', function () { calc(r); })]
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

  function draw() {
    const ui = S.ui;
    if (!ui || !ui.planBody.isConnected) return;
    fillSelect(ui.form.current, '+15');
    fillSelect(ui.form.target, 'PRI');
    drawNotes();
    drawPlan();
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
      updated: updated, count: count, editor: editor, preview: preview
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

  function mount(panel) {
    panel.classList.add('ew-deadeye');
    const n = notesCard();
    const p = planCard();
    panel.appendChild(n.card);
    panel.appendChild(p.card);
    S.ui = Object.assign(n, { planBody: p.body, planPill: p.pill, form: p.form, msg: p.msg });
    if (!S.timer) poll(false);
    else draw();
  }

  function show() { poll(false); }

  window.EWDeadeye = { mount: mount, show: show };
})();
