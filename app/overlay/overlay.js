/* EW overlay shell: read-only glance widgets. Never sends input anywhere.
   Today line (plan 003): GET /api/today every 60 s and on each SSE heartbeat;
   done counts are re-derived locally each second so a reset flips them;
   offline keeps the last value, muted.
   Grind (plan 005): GET /api/grind on the same cadence; the session clock and
   soonest buff countdown run locally each second. Each widget is opt-in via
   the query main.js passes (config overlay.widgets, default on).
   Events (plan 006): eventsSoon shows the soonest "ending soon" item from
   GET /api/events (title + time left), same cadence, countdown local.
   Game (plan 008): header dot from GET /api/game (grey not running, amber
   running, green logged in, red disconnected), same cadence plus a re-GET on
   each SSE `game` event. Always shown; offline keeps the last state, muted.
   Leveling (plan 011): opt-in (default off) one line from GET /api/leveling,
   same cadence plus each SSE `leveling` event; ETA and Hot Time count down
   locally. Plan 024: a pill when a level-gated deadline is tight or late.
   Season (plan 013): opt-in (default off) one line from GET /api/progress
   `season` ("Pass 23/40 - next: Lv 50 (2 lv)"), same cadence plus each SSE
   `leveling` event (a new XP sample can auto-tick a level objective).
   Plan 022: scale / opacity from the query set two CSS variables; rows with
   nothing to say (C.ovQuiet) are hidden; the server row (and a red header
   dot) shows only when the server is not ok; after each layout change the
   content height goes one-way to main (window.ewOverlay.reportSize).
   Market ticker (plan 029): opt-in (default off) up to 5 watched prices from
   GET /api/market/watch every 60 s (no heartbeat re-GET), alert hits first,
   arrow vs the previous different price (C.tickerTrack, overlay memory only),
   net after tax and a pre-order badge when present; a row mutes once its
   source age passes the TTL. Offline keeps the last rows under a muted
   `offline` header; each row mutes once its own data passes the TTL.
   World boss (plan 032): opt-in (default off) next spawn name(s) and
   countdown from GET /api/bosses (same cadence as Today), the following
   spawn muted below; a boss ticked looted for that PT day (or Garmoth at its
   weekly cap) is greyed; Garmoth shows n/3. Countdowns run locally from
   at_utc; once a spawn passes the next one leads. Offline keeps the rows,
   muted. Nothing is read from the game's own boss notice.
   Dice (plan 056): opt-in (default off) one line from the GET /api/today
   `dice` block ("die 2/3 in 12m"), counted down locally from eta_utc; a
   re-GET on each SSE `game` event. Offline keeps the last line, muted.
   What now (plan 069): default on, the top row - the server-ranked next best
   action from GET /api/whatnow (same cadence as Today) or the full view an
   SSE `whatnow` event carries; its due counts down locally ("Kzarka spawns -
   in 8m"), "All clear - play" when nothing is due. Offline keeps the line,
   muted. */
(function () {
  'use strict';
  const C = window.EWCore;
  const TODAY_MS = 60000;
  const TODAY_MIN_MS = 10000; // heartbeat-driven refreshes, throttled
  const W = C.widgetsFromQuery(window.location.search);
  const GRIND_ON = W.grindSession || W.grindBuff;
  let attempt = 0;
  let today = null;
  let todayStale = false;
  let lastToday = null;
  let grind = null;
  let grindAt = 0;
  let grindStale = false;
  let events = null;
  let eventsAt = 0;
  let eventsStale = false;
  let game = null;
  let gameStale = false;
  let lev = null;
  let levAt = 0;
  let levStale = false;
  let season = null;
  let seasonStale = false;
  const TICKER_MS = 60000;
  let ticker = null;
  let tickerStale = false;
  let tickerMem = null;
  let tickerSig = null;
  let boss = null;
  let bossStale = false;
  let bossSig = null;
  let wn = null;
  let wnStale = false;

  // Plan 022: a row shows only when its widget is on and it has something to say.
  function showRow(id, enabled, text) {
    document.getElementById(id).hidden = !enabled || C.ovQuiet(text);
  }

  function setServer(text) {
    document.getElementById('ov-server').textContent = text;
    const hide = C.ovServerRowHidden(text);
    document.getElementById('ov-server-row').hidden = hide;
    document.getElementById('ov-server-dot').hidden = hide;
  }

  // One-way content height to main, only when it changed. Fractional (not
  // scrollHeight, which may round down) so main's ceil never clips a line.
  let lastSize = -1;
  function reportSize() {
    const h = document.body.getBoundingClientRect().height;
    if (h === lastSize || !window.ewOverlay) return;
    lastSize = h;
    window.ewOverlay.reportSize(h);
  }

  function drawToday(now) {
    const t = document.getElementById('ov-today');
    if (!today) {
      t.textContent = todayStale ? 'offline' : '-';
      t.className = todayStale ? 'ew-stale' : '';
      showRow('ov-today-row', true, t.textContent);
      return;
    }
    const g = C.groupItems(today.items, now);
    t.textContent = 'daily ' + g.daily.done + '/' + g.daily.total + '  weekly ' + g.weekly.done + '/' + g.weekly.total;
    t.className = todayStale ? 'ew-stale' : '';
    showRow('ov-today-row', true, t.textContent);
  }

  function drawDice(now) {
    const v = document.getElementById('ov-dice');
    v.textContent = today ? C.diceLine(today.dice, now) : (todayStale ? 'offline' : '-');
    v.className = 'ew-ov-val' + (todayStale ? ' ew-stale' : '');
    showRow('ov-dice-row', true, v.textContent);
  }

  // Offline keeps the last values (clock still running), muted.
  function drawGrind(now) {
    const cls = grindStale ? 'ew-stale' : '';
    if (W.grindSession) {
      const s = document.getElementById('ov-grind');
      const secs = grind ? C.liveElapsed(grind.active, grindAt, now) : null;
      if (!grind) s.textContent = grindStale ? 'offline' : '-';
      else if (secs === null) s.textContent = 'idle';
      else s.textContent = C.spotName(grind.spots, grind.active.spot) + ' ' + C.fmtElapsed(secs);
      s.className = cls;
      showRow('ov-grind-row', true, s.textContent);
    }
    if (W.grindBuff) {
      const b = document.getElementById('ov-buff');
      const n = document.getElementById('ov-buff-name');
      const soon = grind ? C.soonestBuff(grind.buffs, grindAt, now) : null;
      n.textContent = soon ? String(soon.name) : 'Buff';
      if (soon) b.textContent = C.fmtDuration(soon.left_s * 1000);
      else b.textContent = grind ? 'none' : (grindStale ? 'offline' : '-');
      b.className = cls;
      showRow('ov-buff-row', true, b.textContent);
    }
  }

  function drawEvents(now) {
    const v = document.getElementById('ov-events');
    const n = document.getElementById('ov-events-name');
    const soon = events ? C.soonestEvent(events.items, eventsAt, now) : null;
    n.textContent = soon ? soon.title : 'Ending soon';
    n.title = n.textContent;
    if (soon) v.textContent = C.fmtLeft(soon.left_s);
    else v.textContent = events ? 'none' : (eventsStale ? 'offline' : '-');
    v.className = eventsStale ? 'ew-stale' : '';
    showRow('ov-events-row', true, v.textContent);
  }

  function drawGame() {
    const st = !game && gameStale ? C.gameStateLabel('offline') : C.gameStateLabel(game ? game.state : null);
    const v = document.getElementById('ov-game');
    document.getElementById('ov-game-dot').className = 'ew-dot ' + st.cls;
    v.textContent = game || gameStale ? st.label : '-';
    v.className = gameStale ? 'ew-stale' : '';
  }

  function drawLeveling(now) {
    const v = document.getElementById('ov-leveling');
    v.textContent = lev ? C.levelingLine(lev, levAt, now) : (levStale ? 'offline' : '-');
    v.title = v.textContent;
    v.className = 'ew-ov-val ew-mname' + (levStale ? ' ew-stale' : '');
    // Plan 024: deadline pill only when a level-gated deadline is tight or late.
    const p = document.getElementById('ov-deadline');
    const a = lev ? C.deadlineAlert(lev.deadlines) : null;
    p.hidden = !a;
    p.textContent = a ? a.text : '';
    p.className = 'ew-pill ' + (a ? a.cls : 'unknown') + (levStale ? ' ew-stale' : '');
    showRow('ov-leveling-row', true, v.textContent);
  }

  function drawSeason() {
    const v = document.getElementById('ov-season');
    v.textContent = season ? C.seasonLine(season.season) : (seasonStale ? 'offline' : '-');
    v.title = v.textContent;
    v.className = 'ew-ov-val ew-mname' + (seasonStale ? ' ew-stale' : '');
    showRow('ov-season-row', true, v.textContent);
  }

  function tickerLine(r) {
    const row = document.createElement('div');
    row.className = 'ew-ov-row';
    const lab = document.createElement('span');
    lab.className = 'ew-ov-lab ew-mname' + (r.stale ? ' ew-stale' : '');
    lab.textContent = r.name;
    lab.title = r.name;
    const val = document.createElement('span');
    val.className = r.cls;
    if (r.arrow) {
      const a = document.createElement('span');
      a.className = 'ew-tick-' + r.arrow;
      a.textContent = r.arrowText + ' ';
      val.appendChild(a);
    }
    val.appendChild(document.createTextNode(r.price));
    if (r.net) {
      const n = document.createElement('span');
      n.className = 'ew-tick-net';
      n.textContent = 'net ' + r.net;
      val.appendChild(n);
    }
    if (r.badge) {
      const b = document.createElement('span');
      b.className = 'ew-badge ' + r.badge.cls;
      b.textContent = r.badge.label;
      b.title = r.badge.title;
      val.appendChild(b);
    }
    row.appendChild(lab);
    row.appendChild(val);
    return row;
  }

  // Rebuilt only when what it shows changed (staleness moves with the clock).
  function drawTicker(now) {
    const rows = ticker ? C.tickerRows(ticker.items, now, 5, tickerMem && tickerMem.base) : [];
    const state = tickerStale ? 'offline' : '';
    const sig = JSON.stringify([state, rows]);
    if (sig === tickerSig) return;
    tickerSig = sig;
    const st = document.getElementById('ov-ticker-state');
    st.textContent = state;
    st.className = 'ew-ov-val' + (tickerStale ? ' ew-stale' : '');
    const box = document.getElementById('ov-ticker');
    while (box.firstChild) box.removeChild(box.firstChild);
    rows.forEach(function (r) { box.appendChild(tickerLine(r)); });
    document.getElementById('ov-ticker-row').hidden = !rows.length && !tickerStale;
  }

  // Names rebuilt only when they change; greyed names carry ew-boss-looted.
  function bossNames(lab, r, fallback) {
    while (lab.firstChild) lab.removeChild(lab.firstChild);
    if (!r) { lab.textContent = fallback; lab.title = ''; return; }
    r.names.forEach(function (n, i) {
      if (i) lab.appendChild(document.createTextNode(' + '));
      const s = document.createElement('span');
      if (n.looted) s.className = 'ew-boss-looted';
      s.textContent = n.label;
      lab.appendChild(s);
    });
    lab.title = r.text;
  }

  function drawBoss(now) {
    const rows = boss ? C.bossRows(boss, now, 2) : [];
    const first = rows[0] || null;
    const second = rows[1] || null;
    const sig = JSON.stringify([bossStale, rows.map(function (r) { return r.names; })]);
    if (sig !== bossSig) {
      bossSig = sig;
      bossNames(document.getElementById('ov-boss-name'), first, 'World boss');
      bossNames(document.getElementById('ov-boss-next-name'), second, 'then');
    }
    const v = document.getElementById('ov-boss');
    if (first) v.textContent = first.left;
    else v.textContent = boss ? 'none' : (bossStale ? 'offline' : '-');
    v.className = 'ew-ov-val' + (bossStale || (first && first.done) ? ' ew-stale' : '');
    showRow('ov-boss-row', true, v.textContent);
    const n = document.getElementById('ov-boss-next');
    n.textContent = second ? second.left : '';
    n.className = 'ew-ov-val ew-stale';
    showRow('ov-boss-next-row', true, n.textContent);
  }

  function drawWhatNow(now) {
    const v = document.getElementById('ov-whatnow');
    v.textContent = wn ? C.whatNowLine(wn, now) : (wnStale ? 'offline' : '-');
    v.title = v.textContent;
    v.className = 'ew-ov-val ew-mname' + (wnStale ? ' ew-stale' : '');
    showRow('ov-whatnow-row', true, v.textContent);
  }

  function tick() {
    const now = Date.now();
    if (W.whatNow) drawWhatNow(now);
    document.getElementById('ov-daily').textContent = C.fmtDuration(C.nextDailyReset(now) - now);
    document.getElementById('ov-weekly').textContent = C.fmtDuration(C.nextWeeklyReset(now) - now);
    drawToday(now);
    if (W.dice) drawDice(now);
    if (GRIND_ON) drawGrind(now);
    if (W.eventsSoon) drawEvents(now);
    if (W.leveling) drawLeveling(now);
    if (W.marketTicker) drawTicker(now);
    if (W.worldBoss) drawBoss(now);
  }

  function getJSON(path) {
    return fetch(C.SERVER + path).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    });
  }

  function loadGrind() {
    getJSON('/api/grind').then(function (d) {
      if (!d || typeof d !== 'object' || Array.isArray(d)) throw new Error('bad body');
      grind = d;
      grindAt = Date.now();
      grindStale = false;
    }).catch(function () {
      grindStale = true;
    }).then(function () { drawGrind(Date.now()); });
  }

  function loadEvents() {
    getJSON('/api/events').then(function (d) {
      if (!d || typeof d !== 'object' || !Array.isArray(d.items)) throw new Error('bad body');
      events = d;
      eventsAt = Date.now();
      eventsStale = false;
    }).catch(function () {
      eventsStale = true;
    }).then(function () { drawEvents(Date.now()); });
  }

  function loadLeveling() {
    getJSON('/api/leveling').then(function (d) {
      if (!C.normalizeLeveling(d)) throw new Error('bad body');
      lev = d;
      levAt = Date.now();
      levStale = false;
    }).catch(function () {
      levStale = true;
    }).then(function () { drawLeveling(Date.now()); });
  }

  function loadSeason() {
    getJSON('/api/progress').then(function (d) {
      if (!d || typeof d !== 'object' || !Array.isArray(d.tracks)) throw new Error('bad body');
      season = d;
      seasonStale = false;
    }).catch(function () {
      seasonStale = true;
    }).then(drawSeason);
  }

  function loadTicker() {
    getJSON('/api/market/watch').then(function (d) {
      if (!d || typeof d !== 'object' || !Array.isArray(d.items)) throw new Error('bad body');
      ticker = d;
      tickerMem = C.tickerTrack(tickerMem, d.items);
      tickerStale = false;
    }).catch(function () {
      tickerStale = true;
    }).then(function () { drawTicker(Date.now()); });
  }

  function loadBoss() {
    getJSON('/api/bosses').then(function (d) {
      if (!d || typeof d !== 'object' || !Array.isArray(d.next)) throw new Error('bad body');
      boss = d;
      bossStale = false;
    }).catch(function () {
      bossStale = true;
    }).then(function () { drawBoss(Date.now()); });
  }

  function setWhatNow(d) {
    if (!d || typeof d !== 'object' || Array.isArray(d) || !Array.isArray(d.next)) return false;
    wn = d;
    wnStale = false;
    return true;
  }

  function loadWhatNow() {
    getJSON('/api/whatnow').then(function (d) {
      if (!setWhatNow(d)) throw new Error('bad body');
    }).catch(function () {
      wnStale = true;
    }).then(function () { drawWhatNow(Date.now()); });
  }

  function loadGame() {
    getJSON('/api/game').then(function (d) {
      const g = C.normalizeGame(d);
      if (!g) throw new Error('bad body');
      game = g;
      gameStale = false;
    }).catch(function () {
      gameStale = true;
    }).then(drawGame);
  }

  function loadToday(force) {
    const now = Date.now();
    if (!force && !C.pollDue(lastToday, now, TODAY_MIN_MS)) return;
    lastToday = now;
    getJSON('/api/today').then(function (d) {
      if (!d || !Array.isArray(d.items)) throw new Error('bad body');
      today = d;
      todayStale = false;
    }).catch(function () {
      todayStale = true;
    }).then(function () {
      drawToday(Date.now());
      if (W.dice) drawDice(Date.now());
    });
    if (GRIND_ON) loadGrind();
    if (W.eventsSoon) loadEvents();
    if (W.leveling) loadLeveling();
    if (W.season) loadSeason();
    if (W.worldBoss) loadBoss();
    if (W.whatNow) loadWhatNow();
    loadGame();
  }

  function connect() {
    const src = new EventSource(C.SERVER + '/events');
    src.onmessage = function () {
      attempt = 0;
      setServer('ok');
      loadToday(false);
    };
    src.addEventListener('game', function () {
      if (W.dice) loadToday(true); // also re-GETs the game state
      else loadGame();
    });
    src.addEventListener('leveling', function () {
      if (W.leveling) loadLeveling();
      if (W.season) loadSeason();
    });
    src.addEventListener('whatnow', function (ev) {
      if (!W.whatNow) return;
      let d = null;
      try { d = JSON.parse(ev.data); } catch (e) { d = null; }
      if (setWhatNow(d)) drawWhatNow(Date.now());
      else loadWhatNow();
    });
    src.onerror = function () {
      setServer('offline');
      todayStale = true;
      grindStale = true;
      drawToday(Date.now());
      if (W.dice) drawDice(Date.now());
      if (GRIND_ON) drawGrind(Date.now());
      eventsStale = true;
      if (W.eventsSoon) drawEvents(Date.now());
      levStale = true;
      if (W.leveling) drawLeveling(Date.now());
      seasonStale = true;
      if (W.season) drawSeason();
      tickerStale = true;
      if (W.marketTicker) drawTicker(Date.now());
      bossStale = true;
      if (W.worldBoss) drawBoss(Date.now());
      wnStale = true;
      if (W.whatNow) drawWhatNow(Date.now());
      gameStale = true;
      drawGame();
      src.close();
      setTimeout(connect, C.backoffMs(attempt++));
    };
  }

  const S = C.overlayStyleFromQuery(window.location.search);
  document.documentElement.style.setProperty('--ew-overlay-scale', String(S.scale));
  document.documentElement.style.setProperty('--ew-overlay-alpha', Math.round(S.opacity * 100) + '%');
  // Rows start hidden (index.html) and appear once they have something to say.
  if (typeof ResizeObserver === 'function') {
    new ResizeObserver(reportSize).observe(document.getElementById('ov-panel'));
  }
  tick();
  reportSize();
  setInterval(tick, 1000);
  setInterval(function () { loadToday(true); }, TODAY_MS);
  loadToday(true);
  if (W.marketTicker) {
    setInterval(loadTicker, TICKER_MS);
    loadTicker();
  }
  connect();
})();
