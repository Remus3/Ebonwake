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
   locally.
   Season (plan 013): opt-in (default off) one line from GET /api/progress
   `season` ("Pass 23/40 - next: Lv 50 (2 lv)"), same cadence plus each SSE
   `leveling` event (a new XP sample can auto-tick a level objective). */
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

  function drawToday(now) {
    const t = document.getElementById('ov-today');
    if (!today) {
      t.textContent = todayStale ? 'offline' : '-';
      t.className = todayStale ? 'ew-stale' : '';
      return;
    }
    const g = C.groupItems(today.items, now);
    t.textContent = 'daily ' + g.daily.done + '/' + g.daily.total + '  weekly ' + g.weekly.done + '/' + g.weekly.total;
    t.className = todayStale ? 'ew-stale' : '';
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
    }
    if (W.grindBuff) {
      const b = document.getElementById('ov-buff');
      const n = document.getElementById('ov-buff-name');
      const soon = grind ? C.soonestBuff(grind.buffs, grindAt, now) : null;
      n.textContent = soon ? String(soon.name) : 'Buff';
      if (soon) b.textContent = C.fmtDuration(soon.left_s * 1000);
      else b.textContent = grind ? 'none' : (grindStale ? 'offline' : '-');
      b.className = cls;
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
    v.textContent = lev ? C.levelingLine(lev, levAt, now) : (levStale ? 'Leveling offline' : 'Leveling -');
    v.title = v.textContent;
    v.className = 'ew-mname' + (levStale ? ' ew-stale' : '');
  }

  function drawSeason() {
    const v = document.getElementById('ov-season');
    v.textContent = season ? C.seasonLine(season.season) : (seasonStale ? 'Pass offline' : 'Pass -');
    v.title = v.textContent;
    v.className = 'ew-mname' + (seasonStale ? ' ew-stale' : '');
  }

  function tick() {
    const now = Date.now();
    document.getElementById('ov-daily').textContent = C.fmtDuration(C.nextDailyReset(now) - now);
    document.getElementById('ov-weekly').textContent = C.fmtDuration(C.nextWeeklyReset(now) - now);
    drawToday(now);
    if (GRIND_ON) drawGrind(now);
    if (W.eventsSoon) drawEvents(now);
    if (W.leveling) drawLeveling(now);
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
    }).then(function () { drawToday(Date.now()); });
    if (GRIND_ON) loadGrind();
    if (W.eventsSoon) loadEvents();
    if (W.leveling) loadLeveling();
    if (W.season) loadSeason();
    loadGame();
  }

  function connect() {
    const src = new EventSource(C.SERVER + '/events');
    src.onmessage = function () {
      attempt = 0;
      document.getElementById('ov-server').textContent = 'ok';
      loadToday(false);
    };
    src.addEventListener('game', function () { loadGame(); });
    src.addEventListener('leveling', function () {
      if (W.leveling) loadLeveling();
      if (W.season) loadSeason();
    });
    src.onerror = function () {
      document.getElementById('ov-server').textContent = 'offline';
      todayStale = true;
      grindStale = true;
      drawToday(Date.now());
      if (GRIND_ON) drawGrind(Date.now());
      eventsStale = true;
      if (W.eventsSoon) drawEvents(Date.now());
      levStale = true;
      if (W.leveling) drawLeveling(Date.now());
      seasonStale = true;
      if (W.season) drawSeason();
      gameStale = true;
      drawGame();
      src.close();
      setTimeout(connect, C.backoffMs(attempt++));
    };
  }

  document.getElementById('ov-grind-row').hidden = !W.grindSession;
  document.getElementById('ov-buff-row').hidden = !W.grindBuff;
  document.getElementById('ov-events-row').hidden = !W.eventsSoon;
  document.getElementById('ov-leveling-row').hidden = !W.leveling;
  document.getElementById('ov-season-row').hidden = !W.season;
  tick();
  setInterval(tick, 1000);
  setInterval(function () { loadToday(true); }, TODAY_MS);
  loadToday(true);
  connect();
})();
