/* EW Events tab (plan 006 slice B): coupon codes, in-game events and Twitch
   drops with live countdowns, plus the official Sources list. Reads
   GET /api/events; writes go through the dashboard preload (window.ewApi)
   because the server refuses renderer POSTs. Countdowns tick locally each
   second; the lists are only rebuilt when an item changes status (inputs keep
   focus). Sources are plain text with a copy button and, for allowlisted
   https hosts, an open button (plan 057: the operator's browser via the
   ew:open-external bridge) - the dashboard itself never navigates. Every node is built with DOM APIs - no HTML from data.
   Plan 014: a Suggested coupons card lists codes the server found on the
   official news page (robots.txt-gated); each needs one click to add. */
(function () {
  'use strict';
  const C = window.EWCore;
  const POLL_MS = 60000;
  const S = { data: null, at: 0, err: null, last: null, timer: null, ui: null, busy: false, sig: '' };

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
    return !!d && typeof d === 'object' && !Array.isArray(d) && Array.isArray(d.items);
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
    getJSON('/api/events').then(accept, function (e) { S.err = e.message; }).then(draw);
  }

  function msg(text) { if (S.ui) S.ui.msg.textContent = text; }

  // One POST at a time. Every op's reply is an item or a count, not the GET
  // body, so a successful write re-polls.
  function send(body, okText) {
    const b = bridge();
    if (!b) { msg('saving needs the Ebonwake app window'); return Promise.resolve(false); }
    if (S.busy) return Promise.resolve(false);
    S.busy = true;
    msg('saving...');
    return window.EWToast.via(b).post('/api/events', body).then(function (res) {
      S.busy = false;
      if (res && res.ok) {
        if (!accept(res.data)) poll(true);
        const n = res.data && typeof res.data.purged === 'number' ? res.data.purged
          : (res.data && typeof res.data.count === 'number' ? res.data.count : null);
        msg(n === null ? okText : okText + ' (' + n + ')');
        draw();
        return true;
      }
      msg('failed: ' + ((res && res.error) || 'unknown error'));
      return false;
    }, function (e) { S.busy = false; msg('failed: ' + (e && e.message || e)); return false; });
  }

  function rows(now) { return C.eventRows(S.data ? S.data.items : [], S.at, now); }

  // ---- actions ----

  // Two clicks within 3 s: a stray click never deletes or purges.
  function armed(btn, label) {
    if (btn.dataset.armed === '1') return true;
    btn.dataset.armed = '1';
    btn.textContent = 'sure?';
    setTimeout(function () { btn.dataset.armed = ''; btn.textContent = label; }, 3000);
    return false;
  }

  function copy(text, what) {
    const cb = navigator.clipboard;
    if (!cb || typeof cb.writeText !== 'function') { msg('clipboard not available'); return; }
    cb.writeText(text).then(function () { msg(what + ' copied'); }, function () { msg('copy failed'); });
  }

  function add() {
    const f = S.ui.form;
    const r = C.parseEventForm({
      kind: f.kind.value, code: f.code.value, title: f.title.value,
      rewards: f.rewards.value, ends: f.ends.value, url: f.url.value
    });
    if (!r.ok) { msg(r.error); return; }
    send(r.body, 'added').then(function (ok) {
      if (ok) ['code', 'title', 'rewards', 'ends', 'url'].forEach(function (k) { f[k].value = ''; });
    });
  }

  function kindChanged() {
    const f = S.ui.form;
    f.codeRow.hidden = f.kind.value !== 'coupon';
  }

  // ---- render ----

  function doneBox(r, label) {
    const lab = el('label', 'ew-tlabel ew-gnum');
    const box = el('input');
    box.type = 'checkbox';
    box.checked = r.done === true;
    box.title = label;
    box.addEventListener('change', function () {
      send({ done: { id: r.id, done: box.checked } }, box.checked ? label : 'reopened');
    });
    lab.appendChild(box);
    lab.appendChild(el('span', 'ew-muted', label));
    return lab;
  }

  function delBtn(r) {
    const x = el('button', 'ew-tx', 'x');
    x.type = 'button';
    x.title = 'delete (click twice)';
    x.setAttribute('aria-label', x.title);
    x.addEventListener('click', function () { if (armed(x, 'x')) send({ delete: r.id }, 'deleted'); });
    return x;
  }

  function left(r) {
    const n = el('span', 'ew-mprice ew-gnum', r.status === 'done' ? '-' : C.fmtLeft(r.left_s));
    if (r.status === 'upcoming') n.title = 'not started yet';
    if (r.status !== 'done' && r.left_s !== null) S.ui.clocks.push({ id: r.id, node: n });
    return n;
  }

  function rowCls(r) {
    return 'ew-erow ' + r.status + (r.soon ? ' soon' : '');
  }

  function info(r) {
    const parts = [];
    if (typeof r.rewards === 'string' && r.rewards) parts.push(r.rewards);
    if (typeof r.url === 'string' && r.url) parts.push(r.url);
    if (typeof r.ends === 'string') {
      // Plan 048: local time first, the stored UTC beside it.
      const t = C.fmtLocal(r.ends);
      parts.push('ends ' + (t ? t.text + ' (' + t.title + ')' : r.ends));
    }
    return parts.join('\n');
  }

  function empty(body, text) {
    body.appendChild(el('div', S.err && !S.data ? 'ew-err' : 'ew-muted', S.err && !S.data ? S.err : (S.data ? text : 'loading...')));
  }

  function drawCoupons(list) {
    const ui = S.ui;
    const body = ui.couponBody;
    body.textContent = '';
    const open = list.filter(function (r) { return r.status !== 'done' && r.status !== 'expired'; }).length;
    ui.couponPill.textContent = S.data ? open + ' open' : '-';
    if (!list.length) { empty(body, 'No coupons yet.'); return; }
    const box = el('div', 'ew-list' + (S.err ? ' ew-stale' : ''));
    list.forEach(function (r) {
      const row = el('div', rowCls(r));
      const head = el('div', 'ew-ehead');
      const code = el('span', 'ew-ecode', typeof r.code === 'string' ? r.code : '?');
      head.appendChild(code);
      const cp = el('button', 'ew-btn ew-bbtn', 'copy');
      cp.type = 'button';
      cp.disabled = typeof r.code !== 'string';
      cp.addEventListener('click', function () { copy(r.code, 'code'); });
      head.appendChild(cp);
      head.appendChild(left(r));
      head.appendChild(doneBox(r, 'redeemed'));
      head.appendChild(delBtn(r));
      row.appendChild(head);
      const t = el('div', 'ew-mname ew-muted ew-gnum', r.title + (r.rewards ? ' - ' + r.rewards : ''));
      t.title = info(r);
      row.appendChild(t);
      box.appendChild(row);
    });
    body.appendChild(box);
  }

  // Plan 014: suggested codes from the official news list (server side,
  // robots.txt-gated). One click posts a plain coupon add; nothing automatic.
  function drawSuggested() {
    const ui = S.ui;
    const body = ui.suggestBody;
    body.textContent = '';
    const sug = S.data ? S.data.suggested : null;
    const st = C.suggestStatus(sug);
    const list = C.suggestedRows(sug, S.data ? S.data.items : []);
    ui.suggestPill.textContent = S.data ? (list.length ? list.length + ' new' : st.status) : '-';
    ui.suggestPill.className = 'ew-pill ' + (list.length ? 'warn' : 'unknown');
    if (!list.length) { empty(body, st.status === 'ok' ? 'No new codes on the news page.' : st.text); return; }
    const box = el('div', 'ew-list' + (S.err || st.status === 'stale' ? ' ew-stale' : ''));
    list.forEach(function (c) {
      const row = el('div', 'ew-erow suggested');
      const head = el('div', 'ew-ehead');
      head.appendChild(el('span', 'ew-ecode', c.code));
      if (st.status === 'stale') head.appendChild(el('span', 'ew-badge', 'stale'));
      const addB = el('button', 'ew-btn ew-bbtn', 'add');
      addB.type = 'button';
      addB.title = 'add as a coupon entry';
      addB.addEventListener('click', function () {
        const b = C.suggestAddBody(c);
        if (b) send(b, 'added ' + c.code);
      });
      head.appendChild(addB);
      const cp = el('button', 'ew-btn ew-bbtn', 'copy');
      cp.type = 'button';
      cp.addEventListener('click', function () { copy(c.code, 'code'); });
      head.appendChild(cp);
      row.appendChild(head);
      const t = el('div', 'ew-mname ew-muted ew-gnum', (c.date ? c.date + ' - ' : '') + c.title);
      t.title = c.url;
      row.appendChild(t);
      box.appendChild(row);
    });
    body.appendChild(box);
  }

  function deadlines() {
    const raw = S.data && Array.isArray(S.data.deadlines) ? S.data.deadlines : [];
    return raw.filter(function (d) { return C.deadlineBrief(d) !== null && typeof d.left_s === 'number'; });
  }

  function drawEvents(list) {
    const ui = S.ui;
    const body = ui.eventBody;
    body.textContent = '';
    const dls = deadlines();
    const soon = list.filter(function (r) { return r.soon; }).length + dls.length;
    ui.eventPill.textContent = S.data ? (soon ? soon + ' ending soon' : list.length + ' items') : '-';
    ui.eventPill.className = 'ew-pill ' + (soon ? 'warn' : 'unknown');
    if (!list.length && !dls.length) { empty(body, 'No events or drops yet.'); return; }
    const box = el('div', 'ew-list' + (S.err ? ' ew-stale' : ''));
    // Plan 024: level-gated deadlines (Olvia Academy) closing within 14 days,
    // read-only: edited on the Leveling card's data, never stored as events.
    dls.forEach(function (d) {
      const row = el('div', 'ew-erow active soon');
      const head = el('div', 'ew-ehead');
      const t = el('span', 'ew-mname', C.deadlineLine(d));
      t.title = 'closes ' + d.enrol_by_utc;
      head.appendChild(t);
      head.appendChild(el('span', 'ew-badge', 'deadline'));
      head.appendChild(el('span', 'ew-mprice ew-gnum', C.fmtLeft(d.left_s - Math.max(0, (Date.now() - S.at) / 1000))));
      const p = C.deadlinePill(d);
      head.appendChild(el('span', 'ew-pill ' + p.cls, p.text));
      row.appendChild(head);
      box.appendChild(row);
    });
    list.forEach(function (r) {
      const row = el('div', rowCls(r));
      const head = el('div', 'ew-ehead');
      const t = el('span', 'ew-mname', r.title);
      t.title = info(r);
      head.appendChild(t);
      head.appendChild(el('span', 'ew-badge', r.kind === 'drop' ? 'drop' : (r.status === 'upcoming' ? 'soon to start' : 'event')));
      head.appendChild(left(r));
      head.appendChild(doneBox(r, 'done'));
      head.appendChild(delBtn(r));
      row.appendChild(head);
      if (r.rewards) row.appendChild(el('div', 'ew-mname ew-muted ew-gnum', r.rewards));
      box.appendChild(row);
    });
    body.appendChild(box);
  }

  function drawSources() {
    const body = S.ui.sourceBody;
    body.textContent = '';
    const list = S.data && Array.isArray(S.data.sources) ? S.data.sources : [];
    if (!list.length) { empty(body, 'No sources listed.'); return; }
    const st = C.suggestStatus(S.data.suggested);
    body.appendChild(el('div', st.status === 'off' || st.status === 'error' ? 'ew-err' : 'ew-muted', st.text));
    const box = el('div', 'ew-list');
    list.forEach(function (s) {
      if (!s || typeof s.name !== 'string' || typeof s.url !== 'string') return;
      const row = el('div', 'ew-srcrow');
      const txt = el('div', 'ew-srctxt');
      txt.appendChild(el('div', 'ew-mname', s.name));
      const u = el('div', 'ew-mname ew-muted ew-gnum ew-srcurl', s.url);
      u.title = s.url;
      txt.appendChild(u);
      row.appendChild(txt);
      const cp = el('button', 'ew-btn ew-bbtn', 'copy');
      cp.type = 'button';
      cp.addEventListener('click', function () { copy(s.url, 'link'); });
      const open = window.EWToast && window.EWToast.linkButton(s.url);
      if (open) row.appendChild(open);
      row.appendChild(cp);
      box.appendChild(row);
    });
    body.appendChild(box);
  }

  function signature(list) {
    return list.map(function (r) { return r.id + ':' + r.status + ':' + r.soon; }).join('|');
  }

  function draw() {
    const ui = S.ui;
    if (!ui || !ui.couponBody.isConnected) return;
    const list = rows(Date.now());
    ui.clocks = [];
    S.sig = signature(list);
    ui.err.textContent = S.err ? (S.data ? 'last data - ' : '') + S.err : '';
    drawCoupons(list.filter(function (r) { return r.kind === 'coupon'; }));
    drawSuggested();
    drawEvents(list.filter(function (r) { return r.kind !== 'coupon'; }));
    drawSources();
  }

  // Every second: countdowns only; a status / soon change rebuilds the lists.
  function tick() {
    const ui = S.ui;
    if (!ui || !ui.couponBody.isConnected || !S.data) return;
    const list = rows(Date.now());
    if (signature(list) !== S.sig) { draw(); return; }
    const byId = {};
    list.forEach(function (r) { byId[r.id] = r; });
    ui.clocks.forEach(function (c) { if (byId[c.id]) c.node.textContent = C.fmtLeft(byId[c.id].left_s); });
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

  function addCard() {
    const c = card('Add');
    c.pill.textContent = 'you type it';
    const f = {};
    const err = el('div', 'ew-err', '');
    c.body.appendChild(err);
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
    const text = function (max, ph) {
      const i = el('input');
      i.type = 'text';
      i.maxLength = max;
      i.autocomplete = 'off';
      if (ph) i.placeholder = ph;
      return i;
    };
    const kind = el('select');
    [['coupon', 'Coupon'], ['event', 'Event'], ['drop', 'Twitch drop']].forEach(function (k) {
      const o = el('option', null, k[1]);
      o.value = k[0];
      kind.appendChild(o);
    });
    field('kind', 'kind', kind);
    kind.addEventListener('change', kindChanged);
    f.codeRow = field('code', 'code', text(40, 'ABCD-1234'));
    f.code.spellcheck = false;
    field('title', 'title', text(80));
    field('rewards', 'rewards', text(200, 'optional'));
    const ends = el('input');
    ends.type = 'datetime-local';
    ends.title = 'local time; saved as UTC';
    field('ends', 'ends (local)', ends);
    field('url', 'url', text(300, 'https:// optional'));
    const btns = el('div', 'ew-btns');
    const btn = function (label, fn) {
      const b = el('button', 'ew-btn', label);
      b.type = 'button';
      b.addEventListener('click', fn);
      btns.appendChild(b);
      return b;
    };
    btn('Add', add);
    const purge = btn('Purge expired', function () {
      if (armed(purge, 'Purge expired')) send({ purge_expired: true }, 'purged');
    });
    purge.title = 'removes every expired item (click twice)';
    form.appendChild(btns);
    const m = el('div', 'ew-muted ew-msg', '');
    form.appendChild(m);
    form.addEventListener('submit', function (ev) { ev.preventDefault(); add(); });
    c.body.appendChild(form);
    return { card: c.card, form: f, msg: m, err: err };
  }

  function mount(panel) {
    S.panel = panel;
    panel.classList.add('ew-events');
    const a = addCard();
    const cp = card('Coupons');
    const sg = card('Suggested coupons');
    const ev = card('Events and drops');
    const src = card('Sources');
    src.pill.textContent = 'official';
    [a, cp, sg, ev, src].forEach(function (x) { panel.appendChild(x.card); });
    S.ui = {
      form: a.form, msg: a.msg, err: a.err,
      couponBody: cp.body, couponPill: cp.pill, suggestBody: sg.body, suggestPill: sg.pill,
      eventBody: ev.body, eventPill: ev.pill,
      sourceBody: src.body, clocks: []
    };
    kindChanged();
    if (!S.timer) {
      setInterval(tick, 1000);
      // Plan 049: an SSE `events` push re-reads now, or on the next show() while hidden.
      if (window.EWBus) window.EWBus.on('events', function () {
        if (C.pollPaused(S.panel, document)) S.last = null;
        else poll(true);
      });
      poll(false);
    } else {
      draw();
    }
  }

  function show() { poll(false); }

  window.EWEvents = { mount: mount, show: show };
})();
