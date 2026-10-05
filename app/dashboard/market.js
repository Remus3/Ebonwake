/* EW Market tab (plan 002 slice B): watchlist, item detail (sparkline + order
   book depth), add/edit, hot list. Reads only the local EW server; writes go
   through the dashboard preload (window.ewMarket) because the server refuses
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

  // ---- data ----

  function poll(force) {
    const now = Date.now();
    if (!force && !C.pollDue(S.last, now, POLL_MS)) { draw(); return; }
    S.last = now;
    // One timer, re-armed after every poll: polls stay >= POLL_MS apart.
    clearTimeout(S.timer);
    S.timer = setTimeout(function () { poll(true); }, POLL_MS);
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
    const bridge = window.ewMarket;
    if (!bridge || typeof bridge.watch !== 'function') {
      f.msg.textContent = 'saving needs the Ebonwake app window';
      return;
    }
    f.msg.textContent = action === 'add' ? 'saving...' : 'removing...';
    bridge.watch(r.body).then(function (res) {
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

  function drawWatch(body) {
    body.textContent = '';
    const items = S.watch && Array.isArray(S.watch.items) ? S.watch.items : null;
    if (S.watchErr) note(body, 'ew-err', (items ? 'last data - ' : '') + S.watchErr);
    if (!items) { if (!S.watchErr) note(body, 'ew-muted', 'loading...'); return; }
    if (!items.length) { note(body, 'ew-muted', 'Watchlist empty - add an item id.'); return; }
    const list = el('div', 'ew-list' + (S.watchErr ? ' ew-stale' : ''));
    items.forEach(function (it) {
      if (!it) return;
      const p = C.marketPill(it.freshness);
      const row = el('div', 'ew-mrow' + (p.stale ? ' ew-stale' : '') + (key(it) === key(S.sel) ? ' sel' : ''));
      row.tabIndex = 0;
      row.setAttribute('role', 'button');
      row.appendChild(el('span', 'ew-mname', label(it)));
      row.appendChild(el('span', 'ew-mprice', C.fmtSilver(it.price)));
      const a = alertOf(it);
      row.appendChild(el('span', a ? 'ew-badge ' + a : 'ew-badge', a || ''));
      row.appendChild(pillEl(it.freshness));
      row.addEventListener('click', function () { choose(it); });
      row.addEventListener('keydown', function (ev) { if (ev.key === 'Enter') choose(it); });
      list.appendChild(row);
    });
    body.appendChild(list);
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
        r.appendChild(el('span', 'ew-dtxt', C.fmtSilver(l.price) + '  x' + l.count));
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
      x.appendChild(el('span', 'ew-mprice', C.fmtSilver(s[1])));
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
      const row = el('div', 'ew-mrow');
      row.title = 'click to fill the add form';
      row.appendChild(el('span', 'ew-mname', label(it)));
      const price = typeof it.lastSoldPrice === 'number' ? it.lastSoldPrice : it.basePrice;
      row.appendChild(el('span', 'ew-mprice', C.fmtSilver(price)));
      row.appendChild(el('span', 'ew-muted', 'stock ' + C.fmtSilver(it.currentStock)));
      row.addEventListener('click', function () {
        const f = S.ui.form;
        f.id.value = it.id;
        f.sid.value = it.sid || 0;
        f.msg.textContent = 'set thresholds, then Save';
      });
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

  function formCard() {
    const c = card('Add / edit');
    const form = el('form', 'ew-form');
    const f = {};
    [['id', 'item id'], ['sid', 'enhance (sid)'], ['below', 'alert below'], ['above', 'alert above']]
      .forEach(function (x) {
        const lab = el('label', null);
        lab.appendChild(el('span', 'ew-muted', x[1]));
        const inp = el('input');
        inp.type = 'text';
        inp.inputMode = 'numeric';
        inp.autocomplete = 'off';
        inp.name = x[0];
        lab.appendChild(inp);
        form.appendChild(lab);
        f[x[0]] = inp;
      });
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
    panel.classList.add('ew-market');
    const w = card('Watchlist');
    const it = card('Item detail');
    const fm = formCard();
    const hotPill = el('span', 'ew-hpill');
    const hot = card('Hot list', hotPill);
    [w, it, fm, hot].forEach(function (c) { panel.appendChild(c.card); });
    S.ui = { watch: w.body, item: it.body, hot: hot.body, hotPill: hotPill, form: fm.form };
    if (!S.timer) poll(false);
    else draw();
  }

  function show() { poll(false); }

  window.EWMarket = { mount: mount, show: show };
})();
