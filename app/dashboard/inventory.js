/* EW Inventory card (plan 045) on the Progress tab: weight total from the
   operator's LT source checklist (candidate ranges unverified), cheapest next
   +LT, inventory slots, market warehouse VT, per-town storage notes and the
   Value Pack ledger (VP state from the Grind "Value Pack" timer, silver the
   +30 percent earned on logged sales, "+200 LT / +16 slots" reminder when off).
   Reads GET /api/inventory; writes go through the dashboard preload
   (window.ewApi, route /api/inventory) because the server refuses renderer
   POSTs. Everything is operator-typed; nothing is read from the game. Every
   node is built with DOM APIs. */
(function () {
  'use strict';
  const C = window.EWCore;
  const POLL_MS = 60000;
  const S = { data: null, err: null, last: null, timer: null, ui: null };

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
    if (d && typeof d === 'object' && Array.isArray(d.sources)) { S.data = d; S.err = null; return true; }
    return false;
  }

  function poll(force) {
    const now = Date.now();
    if (!force && !C.pollDue(S.last, now, POLL_MS)) { draw(); return; }
    S.last = now;
    clearTimeout(S.timer);
    S.timer = setTimeout(function () { poll(true); }, POLL_MS);
    getJSON('/api/inventory').then(accept, function (e) { S.err = e.message; }).then(draw);
  }

  function msg(text) { if (S.ui) S.ui.msg.textContent = text; }

  function send(body, okText) {
    const b = bridge();
    if (!b) { msg('saving needs the Ebonwake app window'); return Promise.resolve(false); }
    msg('saving...');
    return window.EWToast.via(b).post('/api/inventory', body).then(function (res) {
      if (res && res.ok) {
        if (!accept(res.data)) poll(true);
        msg(okText);
        draw(true);
        return true;
      }
      msg('failed: ' + ((res && res.error) || 'unknown error'));
      return false;
    }, function (e) { msg('failed: ' + (e && e.message || e)); return false; });
  }

  function submit(parsed, okText, after) {
    if (!parsed.ok) { msg(parsed.error); return; }
    send(parsed.body, okText).then(function (ok) { if (ok && after) after(); });
  }

  function button(text, title, fn) {
    const b = el('button', 'ew-tx', text);
    b.type = 'button';
    b.title = title;
    b.addEventListener('click', function () { fn(b); });
    return b;
  }

  function twoClick(btn, fn) {
    // Two clicks within 3 s: a stray click never deletes a row.
    if (btn.dataset.armed !== '1') {
      btn.dataset.armed = '1';
      btn.textContent = 'sure?';
      setTimeout(function () { btn.dataset.armed = ''; btn.textContent = 'x'; }, 3000);
      return;
    }
    fn();
  }

  function input(ph, max, title) {
    const i = el('input');
    i.type = 'text';
    i.placeholder = ph;
    i.maxLength = max;
    i.autocomplete = 'off';
    if (title) i.title = title;
    return i;
  }

  function txt(v) { return v === null || v === undefined ? '' : String(v); }

  // ---- render ----

  function lines(box, rows, empty) {
    box.textContent = '';
    if (!rows.length && empty) box.appendChild(el('div', 'ew-muted', empty));
    rows.forEach(function (l) { box.appendChild(l); });
  }

  function editSource(r) {
    const f = S.ui.src;
    f.id.value = r.id;
    f.lt.value = txt(r.lt);
    f.next_lt.value = txt(r.next_lt);
    f.next_cost.value = txt(r.next_cost);
    f.note.value = r.note;
  }

  function sourceRow(r) {
    const row = el('div', 'ew-trow' + (r.done ? '' : ' done'));
    const name = el('span', 'ew-mname', r.name + ' - ' + r.owned);
    name.title = 'candidate ' + r.range + (r.note ? '; ' + r.note : '');
    row.appendChild(name);
    const meta = el('span', 'ew-tmeta');
    if (r.next) meta.appendChild(el('span', 'ew-muted', r.next));
    if (r.warn) meta.appendChild(el('span', 'ew-err', r.warn));
    meta.appendChild(button('edit', 'edit owned LT and next upgrade', function () { editSource(r); }));
    row.appendChild(meta);
    return row;
  }

  function fillPlanner(force) {
    const d = S.data;
    const f = S.ui.plan;
    if (!d || (!force && f.form.contains(document.activeElement))) return;
    f.base_lt.value = txt(d.base_lt);
    f.slots.value = txt(d.slots && d.slots.base);
    f.slots_used.value = txt(d.slots && d.slots.used);
    f.vp_cost.value = txt(d.ledger && d.ledger.vp_cost);
    f.fame_vt.checked = !!(d.warehouse && d.warehouse.fame === true);
    if (!S.ui.sale.form.contains(document.activeElement)) S.ui.sale.vp.checked = !!(d.vp && d.vp.active === true);
  }

  function drawSourceOptions() {
    const sel = S.ui.src.id;
    const keep = sel.value;
    sel.textContent = '';
    const first = el('option', null, 'source...');
    first.value = '';
    sel.appendChild(first);
    C.invSourceRows(S.data).forEach(function (r) {
      const o = el('option', null, r.name);
      o.value = r.id;
      sel.appendChild(o);
    });
    sel.value = keep;
    if (sel.value !== keep) sel.value = '';
  }

  function draw(force) {
    const ui = S.ui;
    if (!ui || !ui.body.isConnected) return;
    const d = S.data;
    ui.status.textContent = S.err ? (d ? 'last data - ' : '') + S.err : (d && d.error ? 'weight data: ' + d.error : '');
    const sum = C.invSummaryLines(d);
    ui.pill.className = 'ew-pill ' + (d && d.vp ? (d.vp.active === true ? 'ok' : 'warn') : 'unknown');
    ui.pill.textContent = d && d.vp ? (d.vp.active === true ? 'VP on' : 'VP off') : '-';
    lines(ui.summary, sum.map(function (l) { return el('div', 'ew-pill ' + l.cls, l.text); }), d ? '' : 'loading...');
    lines(ui.sources, C.invSourceRows(d).map(sourceRow), d ? 'No weight sources (data file missing).' : '');
    ui.sources.className = 'ew-list' + (S.err ? ' ew-stale' : '');
    lines(ui.next, C.invNextLines(d).map(function (t) { return el('div', 'ew-muted', t); }),
      d ? 'Type a next +LT and its cost on a source to rank upgrades.' : '');
    lines(ui.towns, C.invTownRows(d).map(function (t) {
      const r = el('div', 'ew-trow');
      r.appendChild(el('span', t.full ? 'ew-err' : 'ew-mname', t.text));
      const meta = el('span', 'ew-tmeta');
      meta.appendChild(button('x', 'remove town (click twice)', function (b) {
        twoClick(b, function () { send({ town_del: t.id }, 'town removed'); });
      }));
      r.appendChild(meta);
      return r;
    }), d ? 'No town storage notes yet.' : '');
    lines(ui.sales, C.invSaleRows(d, 5).map(function (s) {
      const r = el('div', 'ew-trow');
      r.appendChild(el('span', 'ew-muted', s.text));
      const meta = el('span', 'ew-tmeta');
      meta.appendChild(button('x', 'remove sale (click twice)', function (b) {
        twoClick(b, function () { send({ sale_del: s.id }, 'sale removed'); });
      }));
      r.appendChild(meta);
      return r;
    }), d ? 'Log collected market sales to see what the VP +30% earns.' : '');
    const wh = d && d.warehouse;
    ui.rules.textContent = wh && wh.source ? 'Warehouse ' + wh.base_vt + ' VT, +' + wh.fame_vt +
      ' with family fame, ' + wh.transfer_vt + ' VT per transfer (official wiki, verified ' + wh.verified + ').' : '';
    ui.rules.title = wh && wh.source ? String(wh.source) : '';
    drawSourceOptions();
    fillPlanner(force === true);
  }

  // ---- mount ----

  function form(nodes, label, onSubmit) {
    const f = el('form', 'ew-form');
    nodes.forEach(function (n) { f.appendChild(n); });
    const b = el('button', 'ew-btn', label);
    b.type = 'submit';
    f.appendChild(b);
    f.addEventListener('submit', function (ev) { ev.preventDefault(); onSubmit(); });
    return f;
  }

  function check(text) {
    const lab = el('label', null);
    const cb = el('input');
    cb.type = 'checkbox';
    lab.appendChild(cb);
    lab.appendChild(el('span', 'ew-muted', text));
    return { label: lab, cb: cb };
  }

  function card() {
    const c = el('section', 'ew-card ew-mcard');
    const h = el('h2', null, 'Inventory');
    const meta = el('span', 'ew-tmeta');
    const pill = el('span', 'ew-pill unknown', '-');
    meta.appendChild(pill);
    h.appendChild(meta);
    c.appendChild(h);
    const body = el('div', 'ew-cbody');
    const status = el('div', 'ew-err', '');
    const summary = el('div', 'ew-brackets');

    const plan = {};
    plan.base_lt = input('base LT', 6, 'weight limit without the sources below');
    plan.slots = input('slots', 3, 'inventory slots without the Value Pack');
    plan.slots_used = input('used', 3, 'inventory slots in use');
    plan.vp_cost = input('VP cost', 20, 'what one Value Pack costs you in silver');
    const fame = check('fame +VT');
    plan.fame_vt = fame.cb;
    plan.form = form([plan.base_lt, plan.slots, plan.slots_used, plan.vp_cost, fame.label], 'Save', function () {
      submit(C.parseInvSetForm({ base_lt: plan.base_lt.value, slots: plan.slots.value, slots_used: plan.slots_used.value,
        vp_cost: plan.vp_cost.value, fame_vt: plan.fame_vt.checked }), 'saved');
    });

    const sources = el('div', 'ew-list');
    const src = {};
    src.id = el('select');
    src.lt = input('owned LT', 4);
    src.next_lt = input('next +LT', 4);
    src.next_cost = input('next cost', 20, 'silver, e.g. 350m');
    src.note = input('note', 120);
    src.form = form([src.id, src.lt, src.next_lt, src.next_cost, src.note], 'Set', function () {
      submit(C.parseInvSourceForm({ id: src.id.value, lt: src.lt.value, next_lt: src.next_lt.value,
        next_cost: src.next_cost.value, note: src.note.value }), 'source saved');
    });
    const next = el('div', null);

    const towns = el('div', 'ew-list');
    const town = {};
    town.name = input('town', 40);
    town.used = input('used', 5);
    town.total = input('slots', 5);
    town.note = input('note', 120);
    town.form = form([town.name, town.used, town.total, town.note], 'Add', function () {
      submit(C.parseInvTownForm({ name: town.name.value, used: town.used.value, total: town.total.value,
        note: town.note.value }), 'town added', function () {
        [town.name, town.used, town.total, town.note].forEach(function (i) { i.value = ''; });
      });
    });

    const sales = el('div', 'ew-list');
    const sale = {};
    sale.price = input('sale price', 20, 'listing price of a collected sale, e.g. 84.5m');
    const vpBox = check('VP');
    sale.vp = vpBox.cb;
    sale.form = form([sale.price, vpBox.label], 'Log', function () {
      submit(C.parseInvSale({ price: sale.price.value, vp: sale.vp.checked }), 'sale logged', function () {
        sale.price.value = '';
      });
    });

    const m = el('div', 'ew-muted ew-msg', '');
    const rules = el('div', 'ew-muted');
    [status, summary, plan.form, el('div', 'ew-muted', 'weight sources'), sources, src.form,
      el('div', 'ew-muted', 'cheapest next +LT'), next, el('div', 'ew-muted', 'town storage'), towns, town.form,
      el('div', 'ew-muted', 'Value Pack ledger'), sales, sale.form, m, rules]
      .forEach(function (n) { body.appendChild(n); });
    c.appendChild(body);
    return { card: c, body: body, pill: pill, status: status, summary: summary, plan: plan, sources: sources,
      src: src, next: next, towns: towns, town: town, sales: sales, sale: sale, msg: m, rules: rules };
  }

  // Appends the card to `panel` (the Progress tab mounts it).
  function mount(panel) {
    S.ui = card();
    panel.appendChild(S.ui.card);
    if (!S.timer) poll(false);
    else draw(true);
  }

  function show() { poll(false); }

  window.EWInventory = { mount: mount, show: show };
})();
