/* EW Market tab (plan 002 slice B): watchlist, item detail (sparkline + order
   book depth), add/edit, hot list. Plan 027: net-after-tax column, buy/sell
   pair calculator and a pre-order badge for capped / stock-0 queues. Reads only the local EW server; writes go
   through the dashboard preload (window.ewApi) because the server refuses
   renderer POSTs. Every node is built with DOM APIs - no HTML from data. */
(function () {
  'use strict';
  const C = window.EWCore;
  const POLL_MS = 60000;
  const HOT_MS = 600000;
  const SVGNS = 'http://www.w3.org/2000/svg';
  const S = {
    watch: null, watchErr: null, hot: null, hotErr: null,
    item: null, itemErr: null, sel: null,
    last: null, lastHot: null, timer: null, ui: null
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

  function key(x) { return x ? x.id + ':' + (x.sid || 0) : ''; }

  function label(it) {
    const n = it && it.name ? String(it.name) : '#' + (it && it.id);
    return it && it.sid ? n + ' [' + it.sid + ']' : n;
  }

  function alertOf(it) {
    const a = 'alert' in it ? it.alert : C.alertFor(it.price, it.below, it.above);
    return a === 'below' || a === 'above' ? a : null;
  }

  function pillEl(fr) {
    const p = C.marketPill(fr);
    const s = el('span', 'ew-pill ' + p.cls, p.label);
    if (p.error) s.title = p.error;
    return s;
  }

  function note(body, cls, text) { body.appendChild(el('div', cls, text)); }

  // Plan 027: rates from /api/market/watch `tax`; defaults until it loads.
  function taxOpts() { return S.watch && S.watch.tax ? S.watch.tax : { vp: false, fame_pct: 0 }; }

  function netEl(it) {
    const n = 'net' in it ? it.net : C.netProceeds(it.price, taxOpts());
    const s = el('span', 'ew-mprice ew-mnet', C.fmtSilver(n));
    s.title = n === null || n === undefined ? 'no price' : 'net after tax ' + C.fmtSilverExact(n);
    return s;
  }

  function badgeEl(it) {
    const b = C.preorderBadge(C.preorderState(it));
    if (!b) return null;
    const s = el('span', 'ew-badge ' + b.cls, b.label);
    s.title = b.title;
    return s;
  }

  // List cell: short silver, exact value on hover (plan 028).
  function priceEl(n) {
    const s = el('span', 'ew-mprice', C.fmtSilver(n));
    if (typeof n === 'number') s.title = C.fmtSilverExact(n) + ' silver';
    return s;
  }

  // ---- data ----

  function poll(force) {
    const now = Date.now();
    if (!force && !C.pollDue(S.last, now, POLL_MS)) { draw(); return; }
    S.last = now;
    // One timer, re-armed after every poll: polls stay >= POLL_MS apart.
    clearTimeout(S.timer);
    S.timer = setTimeout(function () { if (!C.pollPaused(S.panel, document)) poll(true); }, POLL_MS);
    getJSON('/api/market/watch').then(function (d) {
      S.watch = d;
      S.watchErr = null;
    }).catch(function (e) { S.watchErr = e.message; }).then(draw);
    if (C.pollDue(S.lastHot, now, S.hot && !S.hotErr ? HOT_MS : POLL_MS)) {
      S.lastHot = now;
      getJSON('/api/market/hot').then(function (d) {
        S.hot = d;
        S.hotErr = null;
      }).catch(function (e) { S.hotErr = e.message; }).then(draw);
    }
    if (S.sel) loadItem();
  }

  // Plan 049: an SSE `market` push (a watchlist write) re-reads now, or on the
  // next show() while this tab is hidden.
  function onBus() {
    if (C.pollPaused(S.panel, document)) S.last = null;
    else poll(true);
  }

  function loadItem() {
    const sel = S.sel;
    getJSON('/api/market/item?id=' + encodeURIComponent(sel.id) + '&sid=' + encodeURIComponent(sel.sid || 0))
      .then(function (d) {
        if (key(sel) !== key(S.sel)) return;
        S.item = { key: key(sel), data: d };
        S.itemErr = null;
      }).catch(function (e) {
        if (key(sel) === key(S.sel)) S.itemErr = e.message;
      }).then(draw);
  }

  function choose(it) {
    const k = key(it);
    if (k !== key(S.sel)) { S.item = null; S.itemErr = null; }
    S.sel = { id: it.id, sid: it.sid || 0 };
    const f = S.ui && S.ui.form;
    if (f) {
      f.id.value = it.id;
      f.sid.value = it.sid || 0;
      f.q.value = it.name || '';
      f.hits.textContent = '';
      f.below.value = it.below === null || it.below === undefined ? '' : it.below;
      f.above.value = it.above === null || it.above === undefined ? '' : it.above;
    }
    loadItem();
    draw();
  }

  function send(action) {
    const f = S.ui.form;
    const r = C.parseWatchForm({
      id: f.id.value, sid: f.sid.value, below: f.below.value, above: f.above.value
    }, action);
    if (!r.ok) { f.msg.textContent = r.error; return; }
    const bridge = window.ewApi;
    if (!bridge || typeof bridge.post !== 'function') {
      f.msg.textContent = 'saving needs the Ebonwake app window';
      return;
    }
    f.msg.textContent = action === 'add' ? 'saving...' : 'removing...';
    window.EWToast.via(bridge).post('/api/market/watch', r.body).then(function (res) {
      if (res && res.ok) {
        f.msg.textContent = action === 'add' ? 'saved' : 'removed';
        if (action === 'remove' && key(r.body.remove) === key(S.sel)) { S.sel = null; S.item = null; }
        poll(true); // operator action, not a timer
      } else {
        f.msg.textContent = 'failed: ' + ((res && res.error) || 'unknown error');
      }
    }, function (e) { f.msg.textContent = 'failed: ' + (e && e.message || e); });
  }

  // ---- render ----

  // r = { it, tax } with `freshness` left out of `it`: its age_s changes on
  // every GET and would rebuild every row. The pill, stale and sel classes are
  // applied after the reconcile (paintWatchRow), kept rows included.
  function watchRow(r) {
    const it = r.it;
    const row = el('div', 'ew-mrow ew-wrow');
    row.tabIndex = 0;
    row.setAttribute('role', 'button');
    row.appendChild(el('span', 'ew-mname', label(it)));
    row.appendChild(priceEl(it.price));
    row.appendChild(netEl(it));
    const a = alertOf(it);
    const badges = el('span', 'ew-badges');
    badges.appendChild(el('span', a ? 'ew-badge ' + a : 'ew-badge', a || ''));
    const pre = badgeEl(it);
    if (pre) badges.appendChild(pre);
    row.appendChild(badges);
    row.ewPill = el('span', 'ew-pill');
    row.appendChild(row.ewPill);
    row.addEventListener('click', function () { choose(it); });
    row.addEventListener('keydown', function (ev) { if (ev.key === 'Enter') choose(it); });
    return row;
  }

  function paintWatchRow(row, it) {
    const p = C.marketPill(it.freshness);
    row.className = 'ew-mrow ew-wrow' + (p.stale ? ' ew-stale' : '') + (key(it) === key(S.sel) ? ' sel' : '');
    row.ewPill.className = 'ew-pill ' + p.cls;
    row.ewPill.textContent = p.label;
    if (p.error) row.ewPill.title = p.error;
    else row.ewPill.removeAttribute('title');
  }

  // Plan 049: the list node persists and rows are keyed (C.reconcile), so a
  // poll keeps focus and scroll on rows that did not change.
  function drawWatch(body) {
    const items = S.watch && Array.isArray(S.watch.items) ? S.watch.items : null;
    let w = S.ui.wl;
    if (!w || w.list.parentNode !== body) {
      body.textContent = '';
      w = S.ui.wl = { err: el('div', 'ew-err', ''), note: el('div', 'ew-muted', ''), list: el('div', 'ew-list') };
      body.appendChild(w.err);
      body.appendChild(w.note);
      body.appendChild(w.list);
    }
    w.err.textContent = S.watchErr ? (items ? 'last data - ' : '') + S.watchErr : '';
    w.err.hidden = !S.watchErr;
    let hint = '';
    if (!items) hint = S.watchErr ? '' : 'loading...';
    else if (!items.length) hint = 'Watchlist empty - add an item id.';
    w.note.textContent = hint;
    w.note.hidden = !hint;
    w.list.className = 'ew-list' + (S.watchErr ? ' ew-stale' : '');
    w.list.hidden = !(items && items.length);
    const tax = taxOpts();
    const live = (items || []).filter(Boolean);
    const rows = live.map(function (it) {
      const rest = Object.assign({}, it);
      delete rest.freshness;
      return { it: rest, tax: tax };
    });
    C.reconcile(w.list, rows, function (r) { return key(r.it); }, watchRow);
    const byKey = {};
    live.forEach(function (it) { if (!(key(it) in byKey)) byKey[key(it)] = it; });
    Array.prototype.forEach.call(w.list.children, function (n) {
      if (byKey[n.ewKey]) paintWatchRow(n, byKey[n.ewKey]);
    });
  }

  function sparkline(history) {
    const svg = document.createElementNS(SVGNS, 'svg');
    svg.setAttribute('class', 'ew-spark');
    svg.setAttribute('viewBox', '0 0 300 60');
    svg.setAttribute('preserveAspectRatio', 'none');
    const path = document.createElementNS(SVGNS, 'path');
    path.setAttribute('d', C.sparkPath(history, 300, 60));
    path.setAttribute('vector-effect', 'non-scaling-stroke');
    svg.appendChild(path);
    return svg;
  }

  function depth(orders) {
    const bars = C.depthBars(orders, 5);
    const box = el('div', 'ew-depth');
    [['sell', bars.sell], ['buy', bars.buy]].forEach(function (s) {
      const col = el('div', 'ew-dcol');
      col.appendChild(el('div', 'ew-muted', s[0] + ' orders'));
      if (!s[1].length) col.appendChild(el('div', 'ew-muted', '-'));
      s[1].forEach(function (l) {
        const r = el('div', 'ew-drow');
        const bar = el('span', 'ew-dbar ' + s[0]);
        bar.style.width = Math.round(l.w * 100) + '%'; // CSSOM, allowed by the CSP
        r.appendChild(bar);
        r.appendChild(el('span', 'ew-dtxt', C.fmtSilverExact(l.price) + '  x' + l.count));
        col.appendChild(r);
      });
      box.appendChild(col);
    });
    return box;
  }

  function drawItem(body) {
    body.textContent = '';
    if (!S.sel) { note(body, 'ew-muted', 'Select a watchlist row.'); return; }
    const d = S.item && S.item.key === key(S.sel) ? S.item.data : null;
    if (S.itemErr) note(body, 'ew-err', (d ? 'last data - ' : '') + S.itemErr);
    if (!d) { if (!S.itemErr) note(body, 'ew-muted', 'loading...'); return; }
    const sub = Array.isArray(d.sub) ? d.sub[0] : d.sub;
    const fr = C.itemFreshness(d.freshness);
    const p = C.marketPill(fr);
    const wrap = el('div', 'ew-detail' + (p.stale || S.itemErr ? ' ew-stale' : ''));
    const head = el('div', 'ew-mrow');
    head.appendChild(el('span', 'ew-mname', label(sub || S.sel)));
    head.appendChild(pillEl(fr));
    wrap.appendChild(head);
    const st = C.historyStats(d.history);
    if (st.last === null) wrap.appendChild(el('div', 'ew-muted', 'no 90-day history'));
    else wrap.appendChild(sparkline(d.history));
    const last = sub && typeof sub.lastSoldPrice === 'number' ? sub.lastSoldPrice : st.last;
    const stats = el('div', 'ew-stats');
    [['min', st.min], ['max', st.max], ['last', last]].forEach(function (s) {
      const x = el('span', null);
      x.appendChild(el('span', 'ew-muted', s[0] + ' '));
      x.appendChild(el('span', 'ew-mprice', C.fmtSilverExact(s[1])));
      stats.appendChild(x);
    });
    wrap.appendChild(stats);
    wrap.appendChild(depth(d.orders));
    body.appendChild(wrap);
  }

  function drawHot(body, pillBox) {
    body.textContent = '';
    pillBox.textContent = '';
    const items = S.hot && Array.isArray(S.hot.items) ? S.hot.items : null;
    if (S.hot) pillBox.appendChild(pillEl(S.hot.freshness));
    if (S.hotErr) note(body, 'ew-err', (items ? 'last data - ' : '') + S.hotErr);
    if (!items) { if (!S.hotErr) note(body, 'ew-muted', 'loading...'); return; }
    if (!items.length) { note(body, 'ew-muted', 'Hot list empty.'); return; }
    const stale = S.hotErr || C.marketPill(S.hot.freshness).stale;
    const list = el('div', 'ew-list' + (stale ? ' ew-stale' : ''));
    items.forEach(function (it) {
      if (!it) return;
      const row = el('button', 'ew-mrow ew-rowbtn');
      row.type = 'button';
      row.title = 'fill the add form';
      row.appendChild(el('span', 'ew-mname', label(it)));
      const price = typeof it.lastSoldPrice === 'number' ? it.lastSoldPrice : it.basePrice;
      row.appendChild(priceEl(price));
      // L6: a pre-order queue gets the badge instead of a bare "stock 0".
      const pre = badgeEl(it);
      row.appendChild(pre || el('span', 'ew-muted', 'stock ' + C.fmtSilver(it.currentStock)));
      row.addEventListener('click', function () { pick(it); });
      list.appendChild(row);
    });
    body.appendChild(list);
  }

  function draw() {
    const ui = S.ui;
    if (!ui || !ui.watch.isConnected) return;
    drawWatch(ui.watch);
    drawItem(ui.item);
    drawHot(ui.hot, ui.hotPill);
    ui.calc(); // tax settings may have arrived with the watch poll
  }

  // ---- mount ----

  function card(title, extra) {
    const c = el('section', 'ew-card ew-mcard');
    const h = el('h2', null, title);
    if (extra) h.appendChild(extra);
    c.appendChild(h);
    const b = el('div', 'ew-cbody');
    c.appendChild(b);
    return { card: c, body: b };
  }

  // Plan 027 pair calculator. Built once (outside the redrawn detail body) so
  // a 60 s poll never wipes what the operator is typing.
  function calcBox() {
    const box = el('div', 'ew-calc');
    box.appendChild(el('div', 'ew-muted', 'buy at X, sell at Y -> profit after tax'));
    const f = {};
    [['buy', 'buy at'], ['sell', 'sell at']].forEach(function (x) {
      const lab = el('label', null);
      lab.appendChild(el('span', 'ew-muted', x[1] + ' '));
      const inp = el('input');
      inp.type = 'text';
      inp.autocomplete = 'off';
      inp.placeholder = x[0] === 'buy' ? '80m' : '100m';
      inp.name = 'calc-' + x[0];
      lab.appendChild(inp);
      box.appendChild(lab);
      f[x[0]] = inp;
    });
    const out = el('div', 'ew-calc-out ew-muted', '');
    box.appendChild(out);
    const update = function () {
      if (!f.buy.value.trim() && !f.sell.value.trim()) { out.textContent = ''; out.title = ''; return; }
      const opts = taxOpts();
      const r = C.pairProfit(f.buy.value, f.sell.value, opts);
      if (!r.ok) { out.textContent = r.error; out.title = ''; return; }
      out.textContent = 'net ' + C.fmtSilver(r.net) + ' -> profit ' + C.fmtSilver(r.profit);
      out.title = 'net ' + C.fmtSilverExact(r.net) + ', profit ' + C.fmtSilverExact(r.profit) +
        ' (tax ' + Math.round((typeof opts.tax === 'number' ? opts.tax : 0.35) * 100) + '%' + (opts.vp ? ', Value Pack' : '') + (opts.fame_pct ? ', fame +' + opts.fame_pct + '%' : '') + ')';
    };
    f.buy.addEventListener('input', update);
    f.sell.addEventListener('input', update);
    return { box: box, update: update };
  }

  // ---- name typeahead (plan 028) ----

  // Fill the form from a search hit or hot row; raw id/sid stay editable.
  function pick(it) {
    const f = S.ui.form;
    f.id.value = it.id;
    f.sid.value = it.sid || 0;
    if (it.name) f.q.value = it.name;
    f.hits.textContent = '';
    f.msg.textContent = label(it) + ' #' + it.id + ' - set thresholds, then Save';
  }

  function drawHits(f, rows) {
    f.hits.textContent = '';
    rows.forEach(function (r) {
      const row = el('div', 'ew-mrow ew-hit', r.label);
      row.tabIndex = 0;
      row.setAttribute('role', 'option');
      row.addEventListener('click', function () { pick(r); });
      row.addEventListener('keydown', function (ev) { if (ev.key === 'Enter') { ev.preventDefault(); pick(r); } });
      f.hits.appendChild(row);
    });
  }

  function search(f) {
    const q = C.searchQuery(f.q.value);
    const seq = ++f.seq;
    if (q === null) { f.hits.textContent = ''; return; }
    getJSON(C.searchPath(q)).then(function (d) {
      if (seq !== f.seq) return; // a newer keystroke won
      const rows = C.searchRows(d);
      drawHits(f, rows);
      if (!rows.length) f.hits.appendChild(el('div', 'ew-muted', 'no match - use advanced id / sid'));
    }).catch(function (e) {
      if (seq === f.seq) { f.hits.textContent = ''; f.hits.appendChild(el('div', 'ew-err', e.message)); }
    });
  }

  function formCard() {
    const c = card('Add / edit');
    const form = el('form', 'ew-form');
    const f = { seq: 0, timer: null };
    function field(parent, name, text, numeric) {
      const lab = el('label', null);
      lab.appendChild(el('span', 'ew-muted', text));
      const inp = el('input');
      inp.type = 'text';
      if (numeric) inp.inputMode = 'numeric';
      inp.autocomplete = 'off';
      inp.name = name;
      lab.appendChild(inp);
      parent.appendChild(lab);
      f[name] = inp;
    }
    field(form, 'q', 'item name', false);
    f.q.placeholder = 'e.g. cron, or an id';
    f.q.addEventListener('input', function () {
      clearTimeout(f.timer);
      f.timer = setTimeout(function () { search(f); }, C.SEARCH_DEBOUNCE_MS);
    });
    f.q.addEventListener('keydown', function (ev) { if (ev.key === 'Enter') ev.preventDefault(); });
    f.hits = el('div', 'ew-list ew-hits');
    f.hits.setAttribute('role', 'listbox');
    form.appendChild(f.hits);
    const adv = el('details', 'ew-adv');
    adv.appendChild(el('summary', 'ew-muted', 'advanced: raw id / sid'));
    field(adv, 'id', 'item id', true);
    field(adv, 'sid', 'enhance (sid)', true);
    form.appendChild(adv);
    field(form, 'below', 'alert below', true);
    field(form, 'above', 'alert above', true);
    const btns = el('div', 'ew-btns');
    const save = el('button', 'ew-btn', 'Save');
    save.type = 'submit';
    const rm = el('button', 'ew-btn', 'Remove');
    rm.type = 'button';
    btns.appendChild(save);
    btns.appendChild(rm);
    form.appendChild(btns);
    f.msg = el('div', 'ew-muted ew-msg', '');
    form.appendChild(f.msg);
    form.addEventListener('submit', function (ev) { ev.preventDefault(); send('add'); });
    rm.addEventListener('click', function () { send('remove'); });
    c.body.appendChild(form);
    return { card: c.card, form: f };
  }

  function mount(panel) {
    S.panel = panel;
    panel.classList.add('ew-market');
    const w = card('Watchlist');
    const it = card('Item detail');
    const calc = calcBox();
    it.card.appendChild(calc.box);
    const fm = formCard();
    const hotPill = el('span', 'ew-hpill');
    const hot = card('Hot list', hotPill);
    [w, it, fm, hot].forEach(function (c) { panel.appendChild(c.card); });
    S.ui = { watch: w.body, item: it.body, hot: hot.body, hotPill: hotPill, form: fm.form,
      calc: calc.update };
    if (!S.timer) {
      if (window.EWBus) window.EWBus.on('market', onBus);
      poll(false);
    } else draw();
  }

  function show() { poll(false); }

  window.EWMarket = { mount: mount, show: show };
})();
