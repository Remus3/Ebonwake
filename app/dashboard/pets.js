/* EW Pets card (plan 043) on the Progress tab: special-skill coverage of the
   out pets (loot always, plus operator-picked goals), the roster (out toggle,
   fed mark, edit, remove), an add / edit form, the exchange planner (chance to
   T4, parents-destroyed warning) and the sourced Alpha / exchange rules.
   Reads GET /api/pets; writes go through the dashboard preload (window.ewApi,
   route /api/pets) because the server refuses renderer POSTs. The server
   enforces 5 out, one alpha, alpha on T5 only. The roster is operator-typed;
   nothing is read from the game. Every node is built with DOM APIs. */
(function () {
  'use strict';
  const C = window.EWCore;
  const POLL_MS = 60000;
  const S = { data: null, err: null, last: null, timer: null, ui: null, editing: null };

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
    if (d && typeof d === 'object' && Array.isArray(d.roster)) { S.data = d; S.err = null; return true; }
    return false;
  }

  function poll(force) {
    const now = Date.now();
    if (!force && !C.pollDue(S.last, now, POLL_MS)) { draw(); return; }
    S.last = now;
    clearTimeout(S.timer);
    S.timer = setTimeout(function () { if (!C.pollPaused(S.panel, document)) poll(true); }, POLL_MS);
    getJSON('/api/pets').then(accept, function (e) { S.err = e.message; }).then(draw);
  }

  function msg(text) { if (S.ui) S.ui.msg.textContent = text; }

  function send(body, okText) {
    const b = bridge();
    if (!b) { msg('saving needs the Ebonwake app window'); return Promise.resolve(false); }
    msg('saving...');
    return window.EWToast.via(b).post('/api/pets', body).then(function (res) {
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

  function button(text, title, fn) {
    const b = el('button', 'ew-tx', text);
    b.type = 'button';
    b.title = title;
    b.addEventListener('click', function () { fn(b); });
    return b;
  }

  function removePet(p, btn) {
    // Two clicks within 3 s: a stray click never deletes a pet.
    if (btn.dataset.armed !== '1') {
      btn.dataset.armed = '1';
      btn.textContent = 'sure?';
      setTimeout(function () { btn.dataset.armed = ''; btn.textContent = 'x'; }, 3000);
      return;
    }
    send({ remove: p.id }, 'removed');
  }

  function startEdit(p) {
    const f = S.ui.form;
    S.editing = p.id;
    f.name.value = p.name;
    f.species.value = p.species;
    f.tier.value = String(p.tier);
    f.talents.value = p.talentList.join(', ');
    f.out.checked = p.out;
    f.alpha.checked = p.alpha;
    f.save.textContent = 'Save';
    f.cancel.hidden = false;
  }

  function stopEdit() {
    const f = S.ui.form;
    S.editing = null;
    f.name.value = '';
    f.talents.value = '';
    f.out.checked = false;
    f.alpha.checked = false;
    f.save.textContent = 'Add';
    f.cancel.hidden = true;
  }

  function submit() {
    const f = S.ui.form;
    const r = C.parsePetForm({ name: f.name.value, species: f.species.value, tier: f.tier.value,
      talents: f.talents.value, out: f.out.checked, alpha: f.alpha.checked }, S.editing || undefined);
    if (!r.ok) { msg(r.error); return; }
    send(r.body, S.editing ? 'saved' : 'added').then(function (ok) { if (ok) stopEdit(); });
  }

  // ---- render ----

  function lines(box, rows, empty) {
    box.textContent = '';
    if (!rows.length && empty) box.appendChild(el('div', 'ew-muted', empty));
    rows.forEach(function (l) { box.appendChild(l); });
  }

  function drawGoals() {
    const box = S.ui.goals;
    box.textContent = '';
    const opts = S.data && Array.isArray(S.data.goal_options) ? S.data.goal_options : [];
    const on = S.data && Array.isArray(S.data.goals) ? S.data.goals : [];
    opts.forEach(function (g) {
      if (!g || typeof g.id !== 'string' || typeof g.title !== 'string') return;
      const lab = el('label', null);
      const cb = el('input');
      cb.type = 'checkbox';
      cb.checked = on.indexOf(g.id) >= 0;
      cb.addEventListener('change', function () {
        const next = on.filter(function (x) { return x !== g.id; });
        if (cb.checked) next.push(g.id);
        send({ goals: next }, 'goals saved');
      });
      lab.appendChild(cb);
      lab.appendChild(el('span', null, g.title));
      box.appendChild(lab);
    });
  }

  function drawSpecies() {
    const sel = S.ui.form.species;
    const keep = sel.value;
    sel.textContent = '';
    const first = el('option', null, 'type...');
    first.value = '';
    sel.appendChild(first);
    C.petSpeciesOptions(S.data).forEach(function (o) {
      const op = el('option', null, o.label);
      op.value = o.id;
      sel.appendChild(op);
    });
    sel.value = keep;
    if (sel.value !== keep) sel.value = '';
  }

  function petRow(p) {
    const r = el('div', 'ew-trow' + (p.out ? '' : ' done'));
    const lab = el('label', 'ew-tlabel');
    const cb = el('input');
    cb.type = 'checkbox';
    cb.checked = p.out;
    cb.title = 'out (at most 5)';
    cb.addEventListener('change', function () { send({ edit: { id: p.id, out: cb.checked } }, cb.checked ? 'out' : 'in'); });
    lab.appendChild(cb);
    const name = el('span', 'ew-mname', p.name + ' - ' + p.label);
    name.title = 'skill: ' + p.skills + (p.talents ? '; talents: ' + p.talents : '');
    lab.appendChild(name);
    r.appendChild(lab);
    const meta = el('span', 'ew-tmeta');
    meta.appendChild(el('span', 'ew-muted', p.skills));
    meta.appendChild(button('fed', p.fed + ' (click after feeding in game)', function () { send({ feed: p.id }, 'fed'); }));
    meta.appendChild(button('edit', 'edit tier, talents, alpha', function () { startEdit(p); }));
    meta.appendChild(button('x', 'remove pet (click twice)', function (b) { removePet(p, b); }));
    r.appendChild(meta);
    return r;
  }

  function draw() {
    const ui = S.ui;
    if (!ui || !ui.body.isConnected) return;
    const d = S.data;
    ui.status.textContent = S.err ? (d ? 'last data - ' : '') + S.err : (d && d.error ? 'pet data: ' + d.error : '');
    const cov = C.petCoverageLines(d);
    ui.pill.className = 'ew-pill ' + (cov.length ? cov.reduce(function (a, l) {
      return a === 'bad' || l.cls === 'bad' ? 'bad' : (a === 'warn' || l.cls === 'warn' ? 'warn' : 'ok');
    }, 'ok') : 'unknown');
    ui.pill.textContent = d && d.coverage ? d.coverage.out + '/' + d.coverage.max_out + ' out' : '-';
    lines(ui.coverage, cov.map(function (l) { return el('div', 'ew-pill ' + l.cls, l.text); }), d ? '' : 'loading...');
    drawGoals();
    if (!S.editing) drawSpecies();
    const rows = C.petRows(d);
    lines(ui.roster, rows.map(petRow), d ? 'No pets yet - add one below.' : '');
    ui.roster.className = 'ew-list' + (S.err ? ' ew-stale' : '');
    lines(ui.exchange, C.petExchangeLines(d).map(function (x) {
      const row = el('div', null);
      row.appendChild(el('div', 'ew-mname', x.text));
      if (x.warn) row.appendChild(el('div', 'ew-err', x.warn));
      return row;
    }), d ? 'No type with 2+ exchangeable pets.' : '');
    const rules = [];
    [['Alpha', d && d.alpha_rule], ['Exchange', d && d.exchange_rule]].forEach(function (pair) {
      const r = pair[1];
      if (!r || typeof r.text !== 'string' || !r.text) return;
      const n = el('div', 'ew-muted', pair[0] + ': ' + r.text);
      n.title = 'source ' + String(r.source || '?') + ', verified ' + String(r.verified || '?');
      const open = window.EWToast && window.EWToast.linkButton(r.source); // plan 057
      if (open) { n.appendChild(document.createTextNode(' ')); n.appendChild(open); }
      rules.push(n);
    });
    lines(ui.rules, rules, '');
  }

  // ---- mount ----

  function card() {
    const c = el('section', 'ew-card ew-mcard');
    const h = el('h2', null, 'Pets');
    const meta = el('span', 'ew-tmeta');
    const pill = el('span', 'ew-pill unknown', '-');
    meta.appendChild(pill);
    h.appendChild(meta);
    c.appendChild(h);
    const body = el('div', 'ew-cbody');
    const status = el('div', 'ew-err', '');
    const coverage = el('div', 'ew-brackets');
    const goals = el('div', 'ew-bticks');
    goals.title = 'roles to cover besides loot';
    const roster = el('div', 'ew-list');
    const form = el('form', 'ew-form');
    const f = {};
    f.name = el('input');
    f.name.type = 'text';
    f.name.maxLength = 40;
    f.name.placeholder = 'name';
    f.name.autocomplete = 'off';
    f.species = el('select');
    f.tier = el('select');
    [1, 2, 3, 4, 5].forEach(function (t) {
      const o = el('option', null, 'T' + t);
      o.value = String(t);
      f.tier.appendChild(o);
    });
    f.talents = el('input');
    f.talents.type = 'text';
    f.talents.maxLength = 210;
    f.talents.placeholder = 'talents, comma separated';
    f.talents.autocomplete = 'off';
    [f.name, f.species, f.tier, f.talents].forEach(function (i) { form.appendChild(i); });
    const check = function (text) {
      const lab = el('label', null);
      const cb = el('input');
      cb.type = 'checkbox';
      lab.appendChild(cb);
      lab.appendChild(el('span', 'ew-muted', text));
      form.appendChild(lab);
      return cb;
    };
    f.out = check('out');
    f.alpha = check('alpha (T5)');
    f.save = el('button', 'ew-btn', 'Add');
    f.save.type = 'submit';
    form.appendChild(f.save);
    f.cancel = el('button', 'ew-tx', 'cancel');
    f.cancel.type = 'button';
    f.cancel.hidden = true;
    f.cancel.addEventListener('click', function () { stopEdit(); draw(); });
    form.appendChild(f.cancel);
    form.addEventListener('submit', function (ev) { ev.preventDefault(); submit(); });
    const m = el('div', 'ew-muted ew-msg', '');
    const exchange = el('div', 'ew-list');
    const rules = el('div', null);
    [status, coverage, goals, roster, form, m, el('div', 'ew-muted', 'exchange planner'), exchange, rules]
      .forEach(function (n) { body.appendChild(n); });
    c.appendChild(body);
    return { card: c, body: body, pill: pill, status: status, coverage: coverage, goals: goals, roster: roster,
      form: f, msg: m, exchange: exchange, rules: rules };
  }

  // Appends the card to `panel` (the Progress tab mounts it).
  function mount(panel) {
    S.panel = panel;
    S.ui = card();
    S.editing = null;
    panel.appendChild(S.ui.card);
    if (!S.timer) poll(false);
    else draw();
  }

  function show() { poll(false); }

  window.EWPets = { mount: mount, show: show };
})();
