/* EW overlay shell: read-only glance widgets. Never sends input anywhere. */
(function () {
  'use strict';
  const C = window.EWCore;
  let attempt = 0;

  function tick() {
    const now = Date.now();
    document.getElementById('ov-daily').textContent = C.fmtDuration(C.nextDailyReset(now) - now);
    document.getElementById('ov-weekly').textContent = C.fmtDuration(C.nextWeeklyReset(now) - now);
  }

  function connect() {
    const src = new EventSource(C.SERVER + '/events');
    src.onmessage = function () {
      attempt = 0;
      document.getElementById('ov-server').textContent = 'ok';
    };
    src.onerror = function () {
      document.getElementById('ov-server').textContent = 'offline';
      src.close();
      setTimeout(connect, C.backoffMs(attempt++));
    };
  }

  tick();
  setInterval(tick, 1000);
  connect();
})();
