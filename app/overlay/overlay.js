/* EW overlay shell: read-only glance widgets. Never sends input anywhere.
   Today line (plan 003): GET /api/today every 60 s and on each SSE heartbeat;
   done counts are re-derived locally each second so a reset flips them;
   offline keeps the last value, muted.
   Grind (plan 005): GET /api/grind on the same cadence; the session clock and
   soonest buff countdown run locally each second. Each widget is opt-in via
   the query main.js passes (config overlay.widgets, default on). */
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

  function tick() {
    const now = Date.now();
    document.getElementById('ov-daily').textContent = C.fmtDuration(C.nextDailyReset(now) - now);
    document.getElementById('ov-weekly').textContent = C.fmtDuration(C.nextWeeklyReset(now) - now);
    drawToday(now);
    if (GRIND_ON) drawGrind(now);
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
  }

  function connect() {
    const src = new EventSource(C.SERVER + '/events');
    src.onmessage = function () {
      attempt = 0;
      document.getElementById('ov-server').textContent = 'ok';
      loadToday(false);
    };
    src.onerror = function () {
      document.getElementById('ov-server').textContent = 'offline';
      todayStale = true;
      grindStale = true;
      drawToday(Date.now());
      if (GRIND_ON) drawGrind(Date.now());
      src.close();
      setTimeout(connect, C.backoffMs(attempt++));
    };
  }

  document.getElementById('ov-grind-row').hidden = !W.grindSession;
  document.getElementById('ov-buff-row').hidden = !W.grindBuff;
  tick();
  setInterval(tick, 1000);
  setInterval(function () { loadToday(true); }, TODAY_MS);
  loadToday(true);
  connect();
})();
