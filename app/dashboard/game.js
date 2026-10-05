/* EW game card (plan 008 slice B) on the System tab: session-log state, since,
   last event and the operator's recent screenshots, from GET /api/game.
   It never touches the game. Polls every 10 s; the "since" age ticks locally.
   Offline keeps the last value, muted. Plan 009 slice B: "Read" per screenshot
   asks the server to OCR that file (POST /api/ocr via the dashboard preload);
   the result panel shows the text, silver and buffs. Nothing is applied on its
   own: "use silver" pre-fills the Grind stop form (or copies the number) and
   "arm buff" posts a normal grind buff. Every node is built with DOM APIs -
   no HTML from data. */
(function () {
  'use strict';
  const C = window.EWCore;
  const POLL_MS = 2000; // exit must show within 5 s (operator QA 2026-10-05)
  const SHOTS_SHOWN = 10;
  const S = {
    data: null, err: null, last: null, timer: null, ui: null,
    ocr: { file: null, res: null, err: null, busy: false, msg: '' }
  };

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function getJSON(path) {
    return fetch(C.SERVER + path).then(function (r) {
      if (r.status === 404) throw new Error('game API not on this server yet');
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    }).catch(function (e) {
      throw new Error(e instanceof TypeError ? 'server offline' : String(e.message || e));
    });
  }

  function poll(force) {
    const now = Date.now();
    if (!force && !C.pollDue(S.last, now, POLL_MS)) { draw(); return; }
    S.last = now;
    clearTimeout(S.timer);
    S.timer = setTimeout(function () { poll(true); }, POLL_MS);
    getJSON('/api/game').then(function (d) {
      const g = C.normalizeGame(d);
      if (!g) throw new Error('bad reply from server');
      S.data = g;
      S.err = null;
    }).catch(function (e) { S.err = e.message; }).then(draw);
  }

  function kv(key, val) {
    const row = el('div', 'ew-grow');
    row.appendChild(el('span', 'ew-muted', key));
    const v = el('span', 'ew-mname ew-gnum', val);
    v.title = val;
    row.appendChild(v);
    return row;
  }

  function draw() {
    const ui = S.ui;
    if (!ui || !ui.body.isConnected) return;
    const now = Date.now();
    const g = S.data;
    const st = S.err && !g ? C.gameStateLabel('offline') : C.gameStateLabel(g ? g.state : null);
    ui.pill.className = 'ew-pill ' + st.cls;
    ui.pill.textContent = g || S.err ? st.label : '-';
    ui.err.textContent = S.err ? (g ? 'last data - ' : '') + S.err : '';
    const body = ui.body;
    body.textContent = '';
    ui.since = null;
    if (!g) {
      body.appendChild(el('div', 'ew-muted', S.err ? 'no game data' : 'loading...'));
      return;
    }
    const box = el('div', 'ew-list' + (S.err ? ' ew-stale' : ''));
    const head = el('div', 'ew-grow');
    const dot = el('span', 'ew-dot ' + st.cls);
    head.appendChild(dot);
    head.appendChild(el('span', 'ew-mname', st.label));
    box.appendChild(head);
    if (g.state === 'unconfigured') {
      box.appendChild(el('div', 'ew-muted ew-ghint', C.GAME_CONFIG_HINT));
      body.appendChild(box);
      return;
    }
    const since = kv('since', C.gameSinceText(g.since, now));
    ui.since = since.lastChild;
    box.appendChild(since);
    box.appendChild(kv('last event', g.last_event || '-'));
    box.appendChild(kv('log', g.log_file || '-'));
    body.appendChild(box);

    const shots = el('div', 'ew-list ew-gshots' + (S.err ? ' ew-stale' : ''));
    shots.appendChild(el('div', 'ew-muted', 'Screenshots since server start (' + g.screenshots.length + ')'));
    if (!g.screenshots.length) shots.appendChild(el('div', 'ew-muted', 'none yet'));
    g.screenshots.slice(0, SHOTS_SHOWN).forEach(function (s) {
      const row = el('div', 'ew-grow');
      const n = el('span', 'ew-mname', s.name);
      n.title = s.name;
      row.appendChild(n);
      row.appendChild(el('span', 'ew-mprice ew-gnum', C.fmtClock(s.mtime, now)));
      const rb = btn('Read', function () { readShot(s.name); });
      rb.disabled = S.ocr.busy;
      row.appendChild(rb);
      shots.appendChild(row);
    });
    if (g.screenshots.length > SHOTS_SHOWN) {
      shots.appendChild(el('div', 'ew-muted', '+' + (g.screenshots.length - SHOTS_SHOWN) + ' more'));
    }
    body.appendChild(shots);
  }

  // ---- OCR (plan 009) ----

  function btn(label, fn) {
    const b = el('button', 'ew-btn ew-bbtn', label);
    b.type = 'button';
    b.addEventListener('click', fn);
    return b;
  }

  function bridge() {
    const b = window.ewApi;
    return b && typeof b.post === 'function' ? b : null;
  }

  function ocrMsg(text) { S.ocr.msg = text; drawOcr(); }

  // One OCR at a time; the panel keeps the last result until the next Read.
  function readShot(name) {
    const o = S.ocr;
    const b = bridge();
    if (o.busy) return;
    if (!b) { o.file = name; o.res = null; o.err = 'reading needs the Ebonwake app window'; drawOcr(); return; }
    const body = { file: name };
    if (!C.validOcrBody(body)) { o.file = name; o.res = null; o.err = 'not a readable file name'; drawOcr(); return; }
    o.file = name; o.res = null; o.err = null; o.busy = true; o.msg = '';
    drawOcr();
    draw();
    b.post('/api/ocr', body).then(function (res) {
      if (res && res.ok) {
        const r = C.normalizeOcr(res.data);
        if (r) o.res = r;
        else o.err = 'bad reply from server';
      } else {
        o.err = res && res.status === 404 ? 'OCR not on this server yet' : 'failed: ' + ((res && res.error) || 'unknown error');
      }
    }, function (e) { o.err = 'failed: ' + (e && e.message || e); }).then(function () {
      o.busy = false;
      drawOcr();
      draw();
    });
  }

  function useSilver(n) {
    const s = C.ocrSilverInput(n);
    if (s === null) return;
    const g = window.EWGrind;
    if (g && typeof g.prefillSilver === 'function' && g.prefillSilver(s)) {
      ocrMsg('filled the Grind stop form - check it there, then Stop + log');
      return;
    }
    const cb = navigator.clipboard;
    if (!cb || typeof cb.writeText !== 'function') { ocrMsg('no session running; clipboard not available'); return; }
    cb.writeText(s).then(function () { ocrMsg('no session running - copied ' + s); },
      function () { ocrMsg('copy failed'); });
  }

  function armBuff(b) {
    const body = C.ocrBuffBody(b);
    const api = bridge();
    if (!body) { ocrMsg('cannot arm ' + b.name); return; }
    if (!api) { ocrMsg('arming needs the Ebonwake app window'); return; }
    ocrMsg('arming ' + b.name + '...');
    api.post('/api/grind', body).then(function (res) {
      if (res && res.ok) {
        ocrMsg(b.name + ' armed for ' + b.minutes + 'm');
        if (window.EWGrind && typeof window.EWGrind.refresh === 'function') window.EWGrind.refresh();
      } else {
        ocrMsg('failed: ' + ((res && res.error) || 'unknown error'));
      }
    }, function (e) { ocrMsg('failed: ' + (e && e.message || e)); });
  }

  // The OCR panel lives outside the polled body so the 10 s redraw never
  // resets its scroll; it is rebuilt only when the OCR state changes.
  function drawOcr() {
    const ui = S.ui;
    if (!ui || !ui.ocr.isConnected) return;
    const o = S.ocr;
    const box = ui.ocr;
    box.textContent = '';
    box.hidden = !o.file;
    if (!o.file) return;
    const head = el('div', 'ew-grow');
    const n = el('span', 'ew-mname', 'OCR: ' + o.file);
    n.title = o.file;
    head.appendChild(n);
    head.appendChild(btn('close', function () {
      if (o.busy) return;
      o.file = null; o.res = null; o.err = null; o.msg = '';
      drawOcr();
    }));
    box.appendChild(head);
    if (o.busy) { box.appendChild(el('div', 'ew-muted', 'reading...')); return; }
    if (o.err) { box.appendChild(el('div', 'ew-err', o.err)); return; }
    const r = o.res;
    if (!r) return;
    const sv = el('div', 'ew-grow');
    sv.appendChild(el('span', 'ew-muted', 'silver'));
    sv.appendChild(el('span', 'ew-mprice ew-gnum', r.silver === null ? 'not found' : C.fmtSilver(r.silver)));
    if (r.silver !== null) sv.appendChild(btn('use silver', function () { useSilver(r.silver); }));
    box.appendChild(sv);
    if (!r.buffs.length) box.appendChild(el('div', 'ew-muted', 'no buffs found'));
    r.buffs.forEach(function (b) {
      const row = el('div', 'ew-grow');
      const bn = el('span', 'ew-mname', C.ocrBuffLabel(b));
      bn.title = bn.textContent;
      row.appendChild(bn);
      row.appendChild(btn('arm buff', function () { armBuff(b); }));
      box.appendChild(row);
    });
    const pre = el('pre', 'ew-ocrtext', r.text || '(no text)');
    box.appendChild(pre);
    box.appendChild(el('div', 'ew-muted ew-msg', o.msg));
  }

  // Every second: only the "since" age; the card is rebuilt on each poll.
  function tick() {
    const ui = S.ui;
    if (!ui || !ui.since || !ui.since.isConnected || !S.data) return;
    ui.since.textContent = C.gameSinceText(S.data.since, Date.now());
  }

  function mount(panel) {
    panel.classList.add('ew-game');
    const c = el('section', 'ew-card');
    const h = el('h2', null, 'Game');
    const pill = el('span', 'ew-pill unknown', '-');
    h.appendChild(pill);
    c.appendChild(h);
    const err = el('div', 'ew-err', '');
    c.appendChild(err);
    const body = el('div', 'ew-cbody');
    c.appendChild(body);
    const ocr = el('div', 'ew-list ew-ocr');
    ocr.hidden = true;
    c.appendChild(ocr);
    panel.appendChild(c);
    S.ui = { pill: pill, err: err, body: body, ocr: ocr, since: null };
    if (!S.timer) {
      setInterval(tick, 1000);
      poll(false);
    } else {
      draw();
    }
    drawOcr();
  }

  function show() { poll(false); }

  window.EWGame = { mount: mount, show: show };
})();
