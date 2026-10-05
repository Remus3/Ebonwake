/* EW dashboard shell: tabs from /api/state, server pill, reset clocks.
   Plan 001 renders placeholders; plans 002+ fill each tab. */
(function () {
  'use strict';
  const C = window.EWCore;
  let attempt = 0;
  let active = null;

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function card(title, body) {
    const c = el('section', 'ew-card');
    c.appendChild(el('h2', null, title));
    if (body) c.appendChild(body);
    return c;
  }

  function select(id) {
    active = id;
    document.querySelectorAll('.ew-tab').forEach(function (b) {
      b.setAttribute('aria-selected', String(b.dataset.tab === id));
    });
    document.querySelectorAll('.ew-panel').forEach(function (p) {
      p.classList.toggle('active', p.dataset.tab === id);
    });
    try { localStorage.setItem('ew.tab', id); } catch (e) { /* storage optional */ }
  }

  function render(state) {
    const tabs = C.normalizeTabs(state);
    const nav = document.getElementById('tabs');
    const panels = document.getElementById('panels');
    nav.textContent = '';
    panels.textContent = '';
    tabs.forEach(function (t) {
      const b = el('button', 'ew-tab', t.title);
      b.dataset.tab = t.id;
      b.setAttribute('role', 'tab');
      b.addEventListener('click', function () { select(t.id); });
      nav.appendChild(b);
      const p = el('div', 'ew-panel');
      p.dataset.tab = t.id;
      if (t.id === 'today') {
        p.appendChild(card('Daily reset', el('div', 'ew-num', '-')));
        p.lastChild.lastChild.id = 'daily-reset';
        p.appendChild(card('Weekly reset', el('div', 'ew-num', '-')));
        p.lastChild.lastChild.id = 'weekly-reset';
      } else if (t.id === 'system') {
        const v = el('pre', 'ew-muted', JSON.stringify({ version: state && state.version }, null, 1));
        p.appendChild(card('Server', v));
      } else {
        p.appendChild(card(t.title, el('p', 'ew-muted', 'Arrives in plan ' + (t.plan || '?') + '.')));
      }
      panels.appendChild(p);
    });
    let saved = null;
    try { saved = localStorage.getItem('ew.tab'); } catch (e) { /* storage optional */ }
    const ids = tabs.map(function (t) { return t.id; });
    select(active && ids.indexOf(active) >= 0 ? active : (ids.indexOf(saved) >= 0 ? saved : ids[0]));
  }

  function pill(cls, text) {
    const p = document.getElementById('server-pill');
    p.className = 'ew-pill ' + cls;
    p.textContent = text;
  }

  function load() {
    fetch(C.SERVER + '/api/state').then(function (r) { return r.json(); }).then(function (s) {
      attempt = 0;
      pill('ok', 'server ok');
      render(s);
    }).catch(function () {
      pill('bad', 'server offline');
      if (!document.querySelector('.ew-tab')) render(null);
      setTimeout(load, C.backoffMs(attempt++));
    });
  }

  function tick() {
    const now = Date.now();
    const d = document.getElementById('daily-reset');
    const w = document.getElementById('weekly-reset');
    if (d) d.textContent = C.fmtDuration(C.nextDailyReset(now) - now);
    if (w) w.textContent = C.fmtDuration(C.nextWeeklyReset(now) - now);
  }

  load();
  setInterval(tick, 1000);
})();
