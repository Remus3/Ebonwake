/* EW game card (plan 008 slice B) on the System tab: session-log state, since,
   last event and the operator's recent screenshots, from GET /api/game.
   Read-only - the card never writes and never touches the game. Polls every
   10 s; the "since" age ticks locally. Offline keeps the last value, muted.
   Every node is built with DOM APIs - no HTML from data. */
(function () {
  'use strict';
  const C = window.EWCore;
  const POLL_MS = 10000;
  const SHOTS_SHOWN = 10;
  const S = { data: null, err: null, last: null, timer: null, ui: null };

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
      shots.appendChild(row);
    });
    if (g.screenshots.length > SHOTS_SHOWN) {
      shots.appendChild(el('div', 'ew-muted', '+' + (g.screenshots.length - SHOTS_SHOWN) + ' more'));
    }
    body.appendChild(shots);
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
    panel.appendChild(c);
    S.ui = { pill: pill, err: err, body: body, since: null };
    if (!S.timer) {
      setInterval(tick, 1000);
      poll(false);
    } else {
      draw();
    }
  }

  function show() { poll(false); }

  window.EWGame = { mount: mount, show: show };
})();
