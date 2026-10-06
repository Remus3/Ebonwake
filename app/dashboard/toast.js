/* EW toasts + notifications (plan 026). Loaded before the tab modules.
   window.EWToast.toast(level, text, key): one toast in the #toasts region
   (C.toastQueue: max 4, dedupe by key, auto-expire). EWToast.via(bridge)
   .post(route, body): the ew:post bridge call every module uses; its result
   also becomes a toast (C.postToast). The notify loop polls the same GET payloads
   the tabs read (game every 5 s, the rest every 60 s), runs C.notifyRules
   with the operator's prefs (config notify.*, a launch argument), dedupes
   through C.notifyLedger and turns each hit into a toast plus an OS
   notification via the allowlisted ew:notify bridge. Plan 070: GET
   /api/prompts feeds C.promptGate (game-closed quiet) and the 15/5/1 alert
   ladder; a notify toast shows once per key (queue `once`). EW's own data
   only; it never sends anything to the game. Every node is built with DOM APIs. */
(function () {
  'use strict';
  const C = window.EWCore;
  const GAME_MS = 5000;
  const DATA_MS = 60000;
  const SOURCES = [
    ['market', '/api/market/watch'], ['grind', '/api/grind'], ['leveling', '/api/leveling'],
    ['events', '/api/events'], ['today', '/api/today'], ['bosses', '/api/bosses']
  ];
  const queue = C.toastQueue();
  const ledger = C.notifyLedger();
  const docs = {};
  const at = {};
  const last = {};
  let prev = null;
  let timer = null;

  function bridge() {
    const b = window.ewApi;
    return b && typeof b.post === 'function' ? b : null;
  }

  function prefs() {
    const b = window.ewApi;
    return C.notifyPrefsFromArg(b && typeof b.notifyPrefs === 'function' ? b.notifyPrefs() : null);
  }

  function draw() {
    const region = document.getElementById('toasts');
    if (!region) return;
    region.textContent = '';
    queue.items().forEach(function (t) {
      const d = document.createElement('div');
      d.className = 'ew-toast ' + t.level;
      d.textContent = t.text;
      d.title = 'click to dismiss';
      d.addEventListener('click', function () { queue.remove(t.key); draw(); });
      region.appendChild(d);
    });
  }

  // once: true (plan 070) = this key shows once, whichever tab or rule raised it.
  function toast(level, text, key, once) {
    if (!queue.push({ key: key, level: level, text: text, once: once === true }, Date.now())) return false;
    draw();
    if (!timer) {
      timer = setInterval(function () {
        if (queue.expire(Date.now())) draw();
        if (!queue.items().length) { clearInterval(timer); timer = null; }
      }, 500);
    }
    return true;
  }

  // The modules' POST: same reply as bridge.post, plus one toast.
  function post(b, route, body) {
    return b.post(route, body).then(function (res) {
      const t = C.postToast(route, res);
      toast(t.level, t.text, t.key);
      return res;
    }, function (e) {
      const t = C.postToast(route, { ok: false, error: String(e && e.message || e) });
      toast(t.level, t.text, t.key);
      throw e;
    });
  }

  // Each hit -> a toast + an OS notification; resolves to the bridge replies.
  // `again` (the self-test only) skips the show-once memory.
  function fire(hits, again) {
    const b = window.ewApi;
    return Promise.all(hits.map(function (h) {
      if (!toast('warn', h.title + (h.body ? ' - ' + h.body : ''), 'notify:' + h.key, again !== true)) {
        return Promise.resolve({ ok: false, error: 'shown already' });
      }
      if (!b || typeof b.notify !== 'function') return Promise.resolve({ ok: false, error: 'no bridge' });
      return b.notify({ title: h.title, body: h.body }).catch(function (e) {
        return { ok: false, error: String(e && e.message || e) }; // the toast already shows it
      });
    }));
  }

  function getJSON(path) {
    return fetch(C.SERVER + path).then(function (r) {
      if (r.status === 404) throw new Error(C.notOnServer(path));
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    });
  }

  // A failed GET keeps the last good doc, so a blip is never a transition.
  function fetchInto(name, path) {
    return getJSON(path).then(function (d) {
      if (d && typeof d === 'object' && !Array.isArray(d)) { docs[name] = d; at[name] = Date.now(); }
    }, function () { /* keep last */ });
  }

  function round() {
    const now = Date.now();
    const jobs = [fetchInto('game', '/api/game'), fetchInto('prompts', '/api/prompts')];
    SOURCES.forEach(function (s) {
      if (C.pollDue(last[s[0]] === undefined ? null : last[s[0]], now, DATA_MS)) {
        last[s[0]] = now;
        jobs.push(fetchInto(s[0], s[1]));
      }
    });
    return Promise.all(jobs).then(function () {
      const t = Date.now();
      const next = { at: t, market: docs.market || null, grind: docs.grind || null, grindAt: at.grind,
        leveling: docs.leveling || null, levelingAt: at.leveling, events: docs.events || null,
        today: docs.today || null, game: docs.game || null, bosses: docs.bosses || null,
        prompts: docs.prompts || null };
      // Plan 070: game-closed quiet; a ladder step missed while closed is
      // consumed (never fires late), other held hits re-fire at login.
      const g = C.promptGate(C.notifyRules(prev, next, t, prefs()), next.game, next.prompts);
      ledger.take(g.drop, t);
      fire(ledger.take(g.fire, t));
      prev = next;
    });
  }

  // Desktop self-test hook: one synthetic market alert through the engine ->
  // one toast + one ew:notify call. Returns what the bridge answered.
  function selfTest() {
    const item = function (alert) {
      return { id: 0, sid: 0, name: 'Self-test item', price: 1, below: 2, alert: alert };
    };
    const t = Date.now();
    const hits = C.notifyRules({ at: t, market: { items: [item(null)] } },
      { at: t, market: { items: [item('below')] } }, t, { marketAlert: true });
    return fire(hits, true).then(function (r) {
      return { hits: hits.length, toasts: document.querySelectorAll('#toasts .ew-toast').length, notify: r[0] || null };
    });
  }

  // via(bridge).post(route, body): the call form every tab module uses.
  function via(b) {
    return { post: function (route, body) { return post(b, route, body); } };
  }

  // Plan 057: an "open" button for a source link, or null when the url is not
  // on C.EXTERNAL_HOSTS. The click goes through the ew:open-external bridge
  // (main re-checks); a refusal or failure becomes a toast.
  function linkButton(url) {
    if (!C.externalUrl(url)) return null;
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'ew-btn ew-bbtn';
    b.textContent = 'open';
    b.title = 'open in your browser: ' + url;
    b.addEventListener('click', function () {
      const api = window.ewApi;
      if (!api || typeof api.openExternal !== 'function') { toast('warn', 'open: no bridge', 'open'); return; }
      api.openExternal(url).then(function (res) {
        if (!res || !res.ok) toast('warn', 'open failed: ' + ((res && res.error) || 'unknown error'), 'open');
      }, function (e) { toast('warn', 'open failed: ' + String(e && e.message || e), 'open'); });
    });
    return b;
  }

  window.EWToast = { toast: toast, post: post, via: via, linkButton: linkButton, selfTest: selfTest };

  if (bridge()) {
    round();
    setInterval(round, GAME_MS);
  }
})();
