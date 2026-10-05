/* EW Progress tab (plan 004 slice B): character card (level, AP/AAP/DP, GS,
   editable inline), BDO-REST-API profile card with freshness pill, one card per
   track (progress bar + scrollable step list, click toggles), add track. Reads
   GET /api/progress; writes go through the dashboard preload (window.ewApi)
   because the server refuses renderer POSTs. Step toggles are optimistic and
   reverted on error. Every node is built with DOM APIs - no HTML from data.
   Season tracks (plan 013) render as a season card: n/N, done-but-unclaimed
   highlighted with a claim mark (claiming itself stays the operator's act in
   game), next 3 open objectives, the full list, and an add-objective row. */
(function () {
  'use strict';
  const C = window.EWCore;
  const POLL_MS = 60000;
  const KIND_LABEL = { quest: 'quest', season: 'season', gear: 'gear' };
  const S = { data: null, err: null, last: null, timer: null, ui: null, pending: {}, dirty: false, editing: null };

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

  function key(t, s) { return t + '/' + s; }

  // Re-apply in-flight optimistic toggles over fresh server data.
  function withPending(data) {
    let d = data;
    Object.keys(S.pending).forEach(function (k) {
      const p = S.pending[k];
      d = C.withStep(d, p.track, p.step, p.iso);
    });
    return d;
  }

  // A POST answer that is not a full GET body just triggers a re-read.
  function accept(data) {
    if (data && Array.isArray(data.tracks)) {
      S.data = withPending(data);
      S.err = null;
      return true;
    }
    return false;
  }

  // ---- data ----

  function poll(force) {
    const now = Date.now();
    if (!force && !C.pollDue(S.last, now, POLL_MS)) { draw(); return; }
    S.last = now;
    clearTimeout(S.timer);
    S.timer = setTimeout(function () { poll(true); }, POLL_MS);
    getJSON('/api/progress').then(accept, function (e) { S.err = e.message; }).then(function () { draw(); });
  }

  function msg(where, text) { if (S.ui) S.ui[where].textContent = text; }

  function send(body, where, okText) {
    // Acting on another row abandons an open objective edit so cards redraw.
    if (S.editing && !body.obj_edit) S.editing = null;
    const b = bridge();
    if (!b) { msg(where, 'saving needs the Ebonwake app window'); return Promise.resolve(false); }
    msg(where, 'saving...');
    return b.post('/api/progress', body).then(function (res) {
      if (res && res.ok) {
        if (!accept(res.data)) poll(true);
        msg(where, okText);
        draw();
        return true;
      }
      msg(where, 'failed: ' + ((res && res.error) || 'unknown error'));
      return false;
    }, function (e) { msg(where, 'failed: ' + (e && e.message || e)); return false; });
  }

  function toggle(track, step) {
    const k = key(track.id, step.id);
    if (k in S.pending) return;
    S.editing = null;
    const b = bridge();
    if (!b) { msg('addMsg', 'toggling needs the Ebonwake app window'); return; }
    const prev = step.done_at || null;
    const iso = prev ? null : new Date().toISOString();
    S.pending[k] = { track: track.id, step: step.id, iso: iso };
    S.data = C.withStep(S.data, track.id, step.id, iso);
    draw();
    const finish = function (res) {
      delete S.pending[k];
      if (res && res.ok) {
        if (!accept(res.data)) poll(true);
      } else {
        S.data = C.withStep(S.data, track.id, step.id, prev);
        msg('addMsg', 'toggle failed: ' + ((res && res.error) || 'unknown error'));
      }
      draw();
    };
    b.post('/api/progress', { step: { track: track.id, step: step.id, done: !prev } }).then(finish, function (e) {
      finish({ ok: false, error: String(e && e.message || e) });
    });
  }

  function removeTrack(track, btn) {
    // Two clicks within 3 s: a stray click never deletes a track.
    if (btn.dataset.armed !== '1') {
      btn.dataset.armed = '1';
      btn.textContent = 'sure?';
      setTimeout(function () { btn.dataset.armed = ''; btn.textContent = 'x'; }, 3000);
      return;
    }
    send({ remove_track: track.id }, 'addMsg', 'removed');
  }

  function saveCharacter() {
    const f = S.ui.char;
    const r = C.parseCharacterForm({ level: f.level.value, ap: f.ap.value, aap: f.aap.value, dp: f.dp.value });
    if (!r.ok) { msg('charMsg', r.error); return; }
    send(r.body, 'charMsg', 'saved').then(function (ok) { if (ok) S.dirty = false; });
  }

  function addTrack() {
    const f = S.ui.add;
    const r = C.parseTrackForm({ title: f.title.value, kind: f.kind.value, steps: f.steps.value });
    if (!r.ok) { msg('addMsg', r.error); return; }
    send(r.body, 'addMsg', 'added').then(function (ok) {
      if (ok) { f.title.value = ''; f.steps.value = ''; }
    });
  }

  // ---- render ----

  function liveGs() {
    const f = S.ui.char;
    const n = function (i) { return /^\d+$/.test(i.value.trim()) ? Number(i.value) : null; };
    const gs = C.gsTotal(n(f.ap), n(f.aap), n(f.dp));
    f.gs.textContent = gs === null ? '-' : String(Math.floor(gs));
  }

  function drawCharacter() {
    const f = S.ui.char;
    const c = (S.data && S.data.character) || {};
    const who = [c.name, c.cls || 'Deadeye'].filter(function (x) { return typeof x === 'string' && x; }).join(' - ');
    f.who.textContent = who;
    f.status.textContent = S.err ? (S.data ? 'last data - ' : '') + S.err : '';
    // Never clobber what the operator is typing.
    if (!S.dirty && S.data) {
      const gs = c.gs || {};
      const put = function (input, v) { input.value = typeof v === 'number' && isFinite(v) ? String(v) : ''; };
      put(f.level, c.level);
      put(f.ap, gs.ap);
      put(f.aap, gs.aap);
      put(f.dp, gs.dp);
    }
    liveGs();
    drawBrackets();
  }

  // Plan 023: what the stored AP / AAP / DP are worth, and the next bracket.
  function drawBrackets() {
    const box = S.ui.char.brackets;
    box.textContent = '';
    C.bracketLines(S.data && S.data.brackets).forEach(function (l) {
      const row = el('div', 'ew-stats');
      row.appendChild(el('span', 'ew-num', l.text));
      if (l.cliff) row.appendChild(el('span', 'ew-badge', 'cliff'));
      if (l.verify) {
        const badge = el('span', 'ew-pill unknown', 'verify');
        badge.title = 'bracket table predates the newest XP patch; re-check the source';
        row.appendChild(badge);
      }
      box.appendChild(row);
    });
  }

  function drawProfile() {
    const ui = S.ui.profile;
    const prof = S.data ? S.data.profile : undefined;
    const p = C.profilePill(prof);
    ui.pill.className = 'ew-pill ' + p.cls;
    ui.pill.textContent = p.label;
    const body = ui.body;
    body.textContent = '';
    if (!S.data) {
      body.appendChild(el('div', S.err ? 'ew-err' : 'ew-muted', S.err || 'loading...'));
      return;
    }
    if (p.none) {
      body.appendChild(el('div', 'ew-muted', 'No family configured. Set profile.family in config/local.json.'));
      return;
    }
    if (p.pending) {
      body.appendChild(el('div', 'ew-muted', 'Profile is being fetched upstream - check back in a minute.'));
      return;
    }
    if (p.error) body.appendChild(el('div', 'ew-err', p.error));
    const rows = C.profileRows(prof && prof.data);
    if (!rows.length) {
      if (!p.error) body.appendChild(el('div', 'ew-muted', 'No profile data yet.'));
      return;
    }
    const dl = el('div', 'ew-kv' + (p.stale ? ' ew-stale' : ''));
    rows.forEach(function (r) {
      dl.appendChild(el('span', 'ew-muted', r[0]));
      dl.appendChild(el('span', 'ew-mname', r[1]));
    });
    body.appendChild(dl);
  }

  function trackCard(t) {
    const c = el('section', 'ew-card ew-mcard ew-track');
    const h = el('h2', null);
    h.appendChild(el('span', 'ew-mname', String(t.title || t.id)));
    const meta = el('span', 'ew-tmeta');
    const pc = C.trackPct(t);
    meta.appendChild(el('span', 'ew-muted ew-tdays', KIND_LABEL[t.kind] || ''));
    meta.appendChild(el('span', 'ew-pill ' + (pc.total && pc.done === pc.total ? 'ok' : 'unknown'),
      pc.done + '/' + pc.total + ' ' + pc.pct + '%'));
    const x = el('button', 'ew-tx', 'x');
    x.type = 'button';
    x.title = 'remove track (click twice)';
    x.addEventListener('click', function () { removeTrack(t, x); });
    meta.appendChild(x);
    h.appendChild(meta);
    c.appendChild(h);
    const bar = el('div', 'ew-bar');
    const fill = el('div', 'ew-bar-fill');
    fill.style.width = pc.pct + '%';
    bar.appendChild(fill);
    c.appendChild(bar);
    const body = el('div', 'ew-cbody');
    const steps = Array.isArray(t.steps) ? t.steps : [];
    if (!steps.length) body.appendChild(el('div', 'ew-muted', 'No steps.'));
    const list = el('div', 'ew-list' + (S.err ? ' ew-stale' : ''));
    steps.forEach(function (s) {
      if (!s || typeof s.id !== 'string') return;
      const done = !!s.done_at;
      const r = el('div', 'ew-trow' + (done ? ' done' : ''));
      const lab = el('label', 'ew-tlabel');
      const cb = el('input');
      cb.type = 'checkbox';
      cb.checked = done;
      cb.disabled = key(t.id, s.id) in S.pending;
      cb.addEventListener('change', function () { toggle(t, s); });
      lab.appendChild(cb);
      lab.appendChild(el('span', 'ew-mname', String(s.title || s.id)));
      r.appendChild(lab);
      list.appendChild(r);
    });
    body.appendChild(list);
    c.appendChild(body);
    return c;
  }

  // ---- season pass by objective (plan 013) ----

  function claim(t, o, btn) {
    btn.disabled = true;
    send({ claim: { track: t.id, objective: o.id, claimed: !o.claimed_at } }, 'addMsg', o.claimed_at ? 'unclaimed' : 'claimed');
  }

  function removeObjective(t, o, btn) {
    if (btn.dataset.armed !== '1') {
      btn.dataset.armed = '1';
      btn.textContent = 'sure?';
      setTimeout(function () { btn.dataset.armed = ''; btn.textContent = 'x'; }, 3000);
      return;
    }
    send({ obj_del: { track: t.id, objective: o.id } }, 'addMsg', 'removed');
  }

  function objLabel(o) {
    const tg = C.fmtTarget(o.kind, o.target);
    return o.kind === 'level' && tg ? tg : String(o.title || o.id);
  }

  function objRow(t, o, opts) {
    const done = !!o.done_at;
    const r = el('div', 'ew-trow' + (done ? ' done' : '') + (opts.unclaimed ? ' ew-unclaimed' : ''));
    const lab = el('label', 'ew-tlabel');
    if (opts.check) {
      const cb = el('input');
      cb.type = 'checkbox';
      cb.checked = done;
      // A reached level objective is ticked by the level feed; fix the level to untick.
      cb.disabled = key(t.id, o.id) in S.pending || (done && o.auto === true);
      if (o.auto === true) cb.title = 'auto: level reached';
      cb.addEventListener('change', function () { toggle(t, o); });
      lab.appendChild(cb);
    }
    lab.appendChild(el('span', 'ew-mname', opts.short ? objLabel(o) : String(o.title || o.id)));
    r.appendChild(lab);
    const meta = el('span', 'ew-tmeta');
    const tg = C.fmtTarget(o.kind, o.target);
    if (opts.short && o.kind === 'level' && typeof o.gap === 'number' && o.gap > 0) {
      meta.appendChild(el('span', 'ew-muted', o.gap + ' lv'));
    } else if (!opts.short) {
      meta.appendChild(el('span', 'ew-muted', o.kind + (tg && o.kind !== 'level' ? ' ' + tg : '')));
    }
    if (o.reward) meta.appendChild(el('span', 'ew-muted ew-mname', String(o.reward)));
    if (done && (opts.unclaimed || !opts.short)) {
      const b = el('button', 'ew-tx', o.claimed_at ? 'claimed' : 'claim');
      b.type = 'button';
      b.title = o.claimed_at ? 'marked claimed (click to unmark)' : 'mark claimed after claiming in game';
      b.addEventListener('click', function () { claim(t, o, b); });
      meta.appendChild(b);
    }
    if (opts.del) {
      const e = el('button', 'ew-tx', 'edit');
      e.type = 'button';
      e.title = 'edit title, kind, target, reward';
      e.addEventListener('click', function () { S.editing = key(t.id, o.id); draw(true); });
      meta.appendChild(e);
      const x = el('button', 'ew-tx', 'x');
      x.type = 'button';
      x.title = 'remove objective (click twice)';
      x.addEventListener('click', function () { removeObjective(t, o, x); });
      meta.appendChild(x);
    }
    r.appendChild(meta);
    return r;
  }

  // Add form (no `o`) or inline edit form for objective `o` (obj_edit keeps
  // its done/claim marks and list position).
  function objectiveForm(t, o) {
    const form = el('form', 'ew-form');
    const text = function (max, ph, v) {
      const i = el('input');
      i.type = 'text';
      i.maxLength = max;
      i.placeholder = ph;
      i.autocomplete = 'off';
      i.value = v || '';
      return i;
    };
    const title = text(80, 'title', o ? String(o.title || '') : '');
    const kind = el('select');
    C.OBJ_KINDS.forEach(function (k) {
      const op = el('option', null, k);
      op.value = k;
      kind.appendChild(op);
    });
    if (o) kind.value = o.kind;
    const target = text(3, 'target', o ? C.targetInput(o.kind, o.target) : '');
    const reward = text(80, 'reward', o ? String(o.reward || '') : '');
    [title, kind, target, reward].forEach(function (i) { form.appendChild(i); });
    const save = el('button', 'ew-btn', o ? 'Save' : 'Add');
    save.type = 'submit';
    form.appendChild(save);
    if (o) {
      const cancel = el('button', 'ew-tx', 'cancel');
      cancel.type = 'button';
      cancel.addEventListener('click', function () { S.editing = null; draw(); });
      form.appendChild(cancel);
    }
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      const r = C.parseObjectiveForm(t.id, { title: title.value, kind: kind.value, target: target.value, reward: reward.value },
        o ? o.id : undefined);
      if (!r.ok) { msg('addMsg', r.error); return; }
      send(r.body, 'addMsg', o ? 'objective saved' : 'objective added').then(function (ok) {
        if (ok && o) { S.editing = null; draw(); }
      });
    });
    if (o) return form;
    const d = el('details', 'ew-objadd');
    d.appendChild(el('summary', 'ew-muted', 'add objective'));
    d.appendChild(form);
    return d;
  }

  function seasonCard(t) {
    const c = el('section', 'ew-card ew-mcard ew-track ew-season');
    const h = el('h2', null);
    h.appendChild(el('span', 'ew-mname', String(t.title || t.id)));
    const meta = el('span', 'ew-tmeta');
    const g = C.seasonGroups(t);
    meta.appendChild(el('span', 'ew-muted ew-tdays', 'season'));
    meta.appendChild(el('span', 'ew-pill ' + (g.total && g.done === g.total ? 'ok' : 'unknown'), g.done + '/' + g.total));
    if (g.unclaimed.length) meta.appendChild(el('span', 'ew-pill warn', g.unclaimed.length + ' to claim'));
    const x = el('button', 'ew-tx', 'x');
    x.type = 'button';
    x.title = 'remove track (click twice)';
    x.addEventListener('click', function () { removeTrack(t, x); });
    meta.appendChild(x);
    h.appendChild(meta);
    c.appendChild(h);
    const bar = el('div', 'ew-bar');
    const fill = el('div', 'ew-bar-fill');
    fill.style.width = C.trackPct(t).pct + '%';
    bar.appendChild(fill);
    c.appendChild(bar);
    const body = el('div', 'ew-cbody');
    if (t.seed === true) body.appendChild(el('div', 'ew-muted', 'seed, verify against the in-game pass'));
    const list = el('div', 'ew-list' + (S.err ? ' ew-stale' : ''));
    if (g.unclaimed.length) {
      list.appendChild(el('div', 'ew-muted', 'done - claim in game'));
      g.unclaimed.forEach(function (o) { list.appendChild(objRow(t, o, { short: true, unclaimed: true })); });
    }
    list.appendChild(el('div', 'ew-muted', g.next.length ? 'next' : 'all objectives done'));
    g.next.forEach(function (o) { list.appendChild(objRow(t, o, { short: true, check: true })); });
    list.appendChild(el('div', 'ew-muted', 'all objectives'));
    (Array.isArray(t.objectives) ? t.objectives : []).forEach(function (o) {
      if (!o || typeof o.id !== 'string') return;
      if (S.editing === key(t.id, o.id)) list.appendChild(objectiveForm(t, o));
      else list.appendChild(objRow(t, o, { check: true, del: true }));
    });
    body.appendChild(list);
    body.appendChild(objectiveForm(t));
    c.appendChild(body);
    return c;
  }

  // Track cards keep their list scroll position across redraws.
  function drawTracks() {
    const ui = S.ui;
    const scroll = {};
    ui.tracks.forEach(function (c) {
      const b = c.querySelector('.ew-cbody');
      if (b) scroll[c.dataset.track] = b.scrollTop;
      c.remove();
    });
    ui.tracks = [];
    const tracks = S.data && Array.isArray(S.data.tracks) ? S.data.tracks : [];
    tracks.forEach(function (t) {
      if (!t || typeof t.id !== 'string') return;
      const c = t.kind === 'season' && Array.isArray(t.objectives) ? seasonCard(t) : trackCard(t);
      c.dataset.track = t.id;
      ui.panel.insertBefore(c, ui.addCard);
      const b = c.querySelector('.ew-cbody');
      if (b && scroll[t.id]) b.scrollTop = scroll[t.id];
      ui.tracks.push(c);
    });
  }

  // While an objective edit form is open, background redraws (poll, saves
  // elsewhere) leave the track cards alone so typing is never clobbered;
  // opening, cancelling or saving the edit redraws with force.
  function draw(force) {
    const ui = S.ui;
    if (!ui || !ui.panel.isConnected) return;
    drawCharacter();
    drawProfile();
    if (!S.editing || force === true) drawTracks();
  }

  // ---- mount ----

  function field(form, text, input, name) {
    const lab = el('label', null);
    lab.appendChild(el('span', 'ew-muted', text));
    input.name = name;
    lab.appendChild(input);
    form.appendChild(lab);
    return input;
  }

  function numInput(max) {
    const i = el('input');
    i.type = 'text';
    i.inputMode = 'numeric';
    i.maxLength = String(max).length;
    i.autocomplete = 'off';
    return i;
  }

  function characterCard() {
    const c = el('section', 'ew-card ew-mcard');
    c.appendChild(el('h2', null, 'Character'));
    const body = el('div', 'ew-cbody');
    const f = {};
    f.who = el('div', 'ew-mname', '');
    body.appendChild(f.who);
    f.status = el('div', 'ew-err', '');
    body.appendChild(f.status);
    const gsRow = el('div', 'ew-stats');
    gsRow.appendChild(el('span', 'ew-muted', 'GS'));
    f.gs = el('span', 'ew-num', '-');
    gsRow.appendChild(f.gs);
    body.appendChild(gsRow);
    f.brackets = el('div', 'ew-brackets');
    body.appendChild(f.brackets);
    const form = el('form', 'ew-form ew-cform');
    f.level = field(form, 'level', numInput(C.LEVEL_MAX), 'level');
    f.ap = field(form, 'AP', numInput(999), 'ap');
    f.aap = field(form, 'AAP', numInput(999), 'aap');
    f.dp = field(form, 'DP', numInput(999), 'dp');
    [f.level, f.ap, f.aap, f.dp].forEach(function (i) {
      i.addEventListener('input', function () { S.dirty = true; liveGs(); });
    });
    const btns = el('div', 'ew-btns');
    const save = el('button', 'ew-btn', 'Save');
    save.type = 'submit';
    btns.appendChild(save);
    form.appendChild(btns);
    form.addEventListener('submit', function (ev) { ev.preventDefault(); saveCharacter(); });
    body.appendChild(form);
    const m = el('div', 'ew-muted ew-msg', '');
    body.appendChild(m);
    c.appendChild(body);
    return { card: c, form: f, msg: m };
  }

  function profileCard() {
    const c = el('section', 'ew-card ew-mcard');
    const h = el('h2', null, 'Profile');
    const pill = el('span', 'ew-pill unknown', 'profile ?');
    h.appendChild(pill);
    c.appendChild(h);
    const body = el('div', 'ew-cbody');
    c.appendChild(body);
    return { card: c, pill: pill, body: body };
  }

  function addCard() {
    const c = el('section', 'ew-card ew-mcard');
    c.appendChild(el('h2', null, 'Add track'));
    const body = el('div', 'ew-cbody');
    const form = el('form', 'ew-form');
    const f = {};
    const title = el('input');
    title.type = 'text';
    title.maxLength = 80;
    title.autocomplete = 'off';
    f.title = field(form, 'title', title, 'title');
    const kind = el('select');
    ['quest', 'season', 'gear'].forEach(function (k) {
      const o = el('option', null, k);
      o.value = k;
      kind.appendChild(o);
    });
    f.kind = field(form, 'kind', kind, 'kind');
    const steps = el('textarea', 'ew-steps');
    steps.rows = 3;
    steps.placeholder = 'one step per line';
    steps.name = 'steps';
    form.appendChild(steps);
    f.steps = steps;
    const btns = el('div', 'ew-btns');
    const save = el('button', 'ew-btn', 'Add');
    save.type = 'submit';
    btns.appendChild(save);
    form.appendChild(btns);
    const m = el('div', 'ew-muted ew-msg', '');
    form.appendChild(m);
    form.addEventListener('submit', function (ev) { ev.preventDefault(); addTrack(); });
    body.appendChild(form);
    c.appendChild(body);
    return { card: c, form: f, msg: m };
  }

  function mount(panel) {
    panel.classList.add('ew-progress');
    const ch = characterCard();
    const pr = profileCard();
    const ad = addCard();
    [ch, pr, ad].forEach(function (c) { panel.appendChild(c.card); });
    S.dirty = false;
    S.editing = null;
    S.ui = {
      panel: panel, char: ch.form, charMsg: ch.msg, profile: pr, add: ad.form, addMsg: ad.msg,
      addCard: ad.card, tracks: []
    };
    if (!S.timer) poll(false);
    else draw();
  }

  function show() { poll(false); }

  window.EWProgress = { mount: mount, show: show };
})();
