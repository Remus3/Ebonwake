/* EW overlay shell: read-only glance widgets. Never sends input anywhere.
   Today line (plan 003): GET /api/today every 60 s and on each SSE heartbeat;
   done counts are re-derived locally each second so a reset flips them;
   offline keeps the last value, muted. */
(function () {
  'use strict';
  const C = window.EWCore;
  const TODAY_MS = 60000;
  const TODAY_MIN_MS = 10000; // heartbeat-driven refreshes, throttled
  let attempt = 0;
  let today = null;
  let todayStale = false;
  let lastToday = null;

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

  function tick() {
    const now = Date.now();
    document.getElementById('ov-daily').textContent = C.fmtDuration(C.nextDailyReset(now) - now);
    document.getElementById('ov-weekly').textContent = C.fmtDuration(C.nextWeeklyReset(now) - now);
    drawToday(now);
  }

  function loadToday(force) {
    const now = Date.now();
    if (!force && !C.pollDue(lastToday, now, TODAY_MIN_MS)) return;
    lastToday = now;
    fetch(C.SERVER + '/api/today').then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    }).then(function (d) {
      if (!d || !Array.isArray(d.items)) throw new Error('bad body');
      today = d;
      todayStale = false;
    }).catch(function () {
      todayStale = true;
    }).then(function () { drawToday(Date.now()); });
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
      drawToday(Date.now());
      src.close();
      setTimeout(connect, C.backoffMs(attempt++));
    };
  }

  tick();
  setInterval(tick, 1000);
  setInterval(function () { loadToday(true); }, TODAY_MS);
  loadToday(true);
  connect();
})();
