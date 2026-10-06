/* EW Crafting margin card (plan 054) on the Market tab: operator-typed cooking /
   alchemy recipes with cost, net after tax (plan 027 rates, VP / fame from the
   market settings), profit per craft and per 1,000 crafts. Inputs priced by a
   typed vendor price or the server's market cache; a line with no price is
   listed and the profit stays blank. Reads GET /api/crafting; writes go through
   the dashboard preload (window.ewApi, route /api/crafting) because the server
   refuses renderer POSTs. Nothing is read from the game. Every node is built
   with DOM APIs. */
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
    if (d && typeof d === 'object' && Array.isArray(d.recipes)) { S.data = d; S.err = null; return true; }
    return false;
  }

  function poll(force) {
    const now = Date.now();
    if (!force && !C.pollDue(S.last, now, POLL_MS)) { draw(); return; }
    S.last = now;
    clearTimeout(S.timer);
    S.timer = setTimeout(function () { if (!C.pollPaused(S.panel, document)) poll(true); }, POLL_MS);
    getJSON('/api/crafting').then(accept, function (e) { S.err = e.message; }).then(draw);
  }

  function msg(text) { if (S.ui) S.ui.msg.textContent = text; }

  function send(body, okText) {
    const b = bridge();
    if (!b) { msg('saving needs the Ebonwake app window'); return Promise.resolve(false); }
    msg('saving...');
    return window.EWToast.via(b).post('/api/crafting', body).then(function (res) {
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

  function twoClick(btn, fn) {
    // Two clicks within 3 s: a stray click never deletes a recipe.
    if (btn.dataset.armed !== '1') {
      btn.dataset.armed = '1';
      btn.textContent = 'sure?';
      setTimeout(function () { btn.dataset.armed = ''; btn.textContent = 'x'; }, 3000);
      return;
    }
    fn();
  }

  // ---- render ----

  function fill(r) {
    const f = S.ui.form;
    const v = C.craftFormOf(r);
    S.editing = r ? r.id : null;
    f.name.value = v.name;
    f.kind.value = v.kind;
    f.inputs.value = v.inputs;
    f.outputs.value = v.outputs;
    f.procs.value = v.procs;
    f.save.textContent = r ? 'Save ' + r.name : 'Add';
  }

  function lineText(x, sold) {
    return (sold ? x.qty_avg : x.qty) + ' x ' + x.label + ' @ ' + C.fmtSilver(x.unit) +
      (sold ? ' (net ' + C.fmtSilver(x.net_unit) + ')' : x.vendor_price !== null && x.vendor_price !== undefined ? ' vendor' : '');
  }

  function recipeRow(r) {
    const s = C.craftSummary(r);
    const box = el('div', 'ew-trow');
    const name = el('span', 'ew-mname', r.name + ' (' + r.kind + ')');
    const tip = [];
    (r.inputs || []).forEach(function (x) { tip.push('in: ' + lineText(x, false)); });
    (r.outputs || []).forEach(function (x) { tip.push('out: ' + lineText(x, true)); });
    (r.procs || []).forEach(function (x) { tip.push('proc: ' + lineText(x, true)); });
    name.title = tip.join('\n');
    box.appendChild(name);
    const meta = el('span', 'ew-tmeta');
    meta.appendChild(el('span', 'ew-muted', 'cost ' + s.cost + ' / net ' + s.net));
    const p = el('span', s.loss ? 'ew-err' : 'ew-mprice', s.profit + ' /craft, ' + s.per1000 + ' /1k' +
      (s.pct ? ' (' + s.pct + ')' : ''));
    if (s.missing) p.title = s.missing;
    meta.appendChild(p);
    meta.appendChild(button('edit', 'load this recipe into the form', function () { fill(r); }));
    meta.appendChild(button('x', 'remove recipe (click twice)', function (b) {
      twoClick(b, function () { send({ delete: r.id }, 'recipe removed'); });
    }));
    box.appendChild(meta);
    if (s.missing) {
      const w = el('div', 'ew-muted', s.missing + ' - watch the item on the Market tab to price it');
      box.appendChild(w);
    }
    return box;
  }

  function draw() {
    const ui = S.ui;
    if (!ui || !ui.body.isConnected) return;
    const d = S.data;
    ui.status.textContent = S.err ? (d ? 'last data - ' : '') + S.err : '';
    const t = d && d.tax;
    ui.pill.className = 'ew-pill ' + (t ? (t.vp === true ? 'ok' : 'warn') : 'unknown');
    ui.pill.textContent = t ? (t.vp === true ? 'VP' : 'no VP') + (t.fame_pct ? ' +' + t.fame_pct + '% fame' : '') : '-';
    ui.list.textContent = '';
    const rows = d ? d.recipes : [];
    if (!rows.length) {
      ui.list.appendChild(el('div', 'ew-muted', d ? 'No recipes yet. Type one below.' : 'loading...'));
    }
    rows.forEach(function (r) { ui.list.appendChild(recipeRow(r)); });
    ui.list.className = 'ew-list' + (S.err ? ' ew-stale' : '');
  }

  // ---- mount ----

  function area(ph, rows, title) {
    const a = el('textarea');
    a.placeholder = ph;
    a.rows = rows;
    a.title = title;
    a.spellcheck = false;
    return a;
  }

  function card() {
    const c = el('section', 'ew-card ew-mcard');
    const h = el('h2', null, 'Crafting margin');
    const meta = el('span', 'ew-tmeta');
    const pill = el('span', 'ew-pill unknown', '-');
    meta.appendChild(pill);
    h.appendChild(meta);
    c.appendChild(h);
    const body = el('div', 'ew-cbody');
    const status = el('div', 'ew-err', '');
    const list = el('div', 'ew-list');

    const f = {};
    f.name = el('input');
    f.name.type = 'text';
    f.name.placeholder = 'recipe name';
    f.name.maxLength = 60;
    f.name.autocomplete = 'off';
    f.kind = el('select');
    ['cooking', 'alchemy'].forEach(function (k) {
      const o = el('option', null, k);
      o.value = k;
      f.kind.appendChild(o);
    });
    f.inputs = area('inputs, one per line: 5 #9001  or  2 Leavening Agent @20', 4,
      'qty then #market id, or qty then a name with @ vendor price');
    f.outputs = area('outputs: 2.5 #9213 Beer', 2, 'average yield per craft then #market id');
    f.procs = area('procs (optional): 0.1 #9214', 2, 'average proc yield per craft then #market id');
    const form = el('form', 'ew-form');
    [f.name, f.kind, f.inputs, f.outputs, f.procs].forEach(function (n) { form.appendChild(n); });
    f.save = el('button', 'ew-btn', 'Add');
    f.save.type = 'submit';
    form.appendChild(f.save);
    form.appendChild(button('new', 'clear the form', function () { fill(null); }));
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      const parsed = C.parseCraftForm({ name: f.name.value, kind: f.kind.value, inputs: f.inputs.value,
        outputs: f.outputs.value, procs: f.procs.value }, S.editing);
      if (!parsed.ok) { msg(parsed.error); return; }
      send(parsed.body, S.editing ? 'recipe saved' : 'recipe added').then(function (ok) { if (ok) fill(null); });
    });
    f.form = form;

    const m = el('div', 'ew-muted ew-msg', '');
    const rules = el('div', 'ew-muted', 'Net = sale price after market tax at the Market settings (VP, family fame); ' +
      'per 1k = 1,000 crafts at the average yield.');
    [status, list, form, m, rules].forEach(function (n) { body.appendChild(n); });
    c.appendChild(body);
    return { card: c, body: body, pill: pill, status: status, list: list, form: f, msg: m };
  }

  // Appends the card to `panel` (the Market tab mounts it).
  function mount(panel) {
    S.panel = panel;
    S.ui = card();
    panel.appendChild(S.ui.card);
    if (!S.timer) {
      if (window.EWBus) {
        window.EWBus.on('market', function () {
          if (C.pollPaused(S.panel, document)) S.last = null;
          else poll(true);
        });
      }
      poll(false);
    } else draw();
  }

  function show() { poll(false); }

  window.EWCrafting = { mount: mount, show: show };
})();
