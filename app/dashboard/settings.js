/* EW Settings tab (plan 030): a grouped form over GET /api/settings. Save
   POSTs only the changed allowlisted keys through the dashboard preload
   (POST /api/settings, checked by C.validSettingsBody), then asks main to
   recreate the overlay (ew:reload-overlay) and/or re-register hotkeys and
   re-apply the zoom (ew:reload-shell; a hotkey conflict is reported back).
   Both IPCs carry no payload: main re-reads config/local.json itself.
   The page theme follows ui.theme (system = prefers-color-scheme) from load,
   on every tab. Every node is built with DOM APIs - no HTML from data. */
(function () {
  'use strict';
  const C = window.EWCore;
  const S = { panel: null, saved: null, defaults: null, restartKeys: [], err: null, msg: null,
    msgCls: 'ok', busy: false, inputs: {}, theme: 'system' };

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function getJSON(path) {
    return fetch(C.SERVER + path).then(function (r) {
      if (r.status === 404) throw new Error(C.notOnServer(path));
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    }).catch(function (e) {
      throw new Error(e instanceof TypeError ? 'server offline' : String(e.message || e));
    });
  }

  function bridge() {
    const b = window.ewApi;
    return b && typeof b.post === 'function' ? b : null;
  }

  // ---- theme ----

  const dark = typeof window.matchMedia === 'function' ? window.matchMedia('(prefers-color-scheme: dark)') : null;

  function applyTheme(theme) {
    S.theme = theme || S.theme;
    document.documentElement.setAttribute('data-theme', C.themeAttr(S.theme, dark ? dark.matches : true));
  }
  if (dark && typeof dark.addEventListener === 'function') {
    dark.addEventListener('change', function () { applyTheme(); });
  }

  // ---- data ----

  function take(doc) {
    if (!doc || typeof doc.settings !== 'object') return;
    S.saved = doc.settings;
    S.defaults = doc.defaults || null;
    S.restartKeys = Array.isArray(doc.restart_keys) ? doc.restart_keys : [];
    S.detected = doc.detected && typeof doc.detected === 'object' ? doc.detected : null;
    S.err = typeof doc.error === 'string' ? doc.error : null;
    applyTheme(S.saved['ui.theme']);
  }

  function load() {
    return getJSON('/api/settings').then(function (doc) { take(doc); }, function (e) {
      S.err = e.message;
    }).then(draw);
  }

  // ---- save ----

  function readForm() {
    const edits = {};
    const errors = [];
    Object.keys(S.inputs).forEach(function (k) {
      const n = S.inputs[k];
      const r = C.parseSettingInput(k, n.type === 'checkbox' ? n.checked : n.value);
      if (r.error) errors.push(r.error);
      else edits[k] = r.value;
    });
    return { edits: edits, errors: errors };
  }

  function say(cls, text) {
    S.msgCls = cls;
    S.msg = text;
  }

  function after(changed) {
    const fx = C.settingsEffects(changed);
    const b = window.ewApi || {};
    const notes = [];
    const jobs = [];
    if (fx.theme) applyTheme(S.saved['ui.theme']);
    if (fx.overlay && typeof b.reloadOverlay === 'function') {
      jobs.push(b.reloadOverlay().then(function (r) {
        if (!r || !r.ok) notes.push('overlay reload failed');
      }, function () { notes.push('overlay reload failed'); }));
    }
    if (fx.shell && typeof b.reloadShell === 'function') {
      jobs.push(b.reloadShell().then(function (r) {
        if (r && Array.isArray(r.conflicts) && r.conflicts.length) {
          notes.push('hotkey in use by another app, kept the old one: ' + r.conflicts.join(', '));
        } else if (!r || !r.ok) notes.push('hotkey / scale apply failed');
      }, function () { notes.push('hotkey / scale apply failed'); }));
    }
    return Promise.all(jobs).then(function () { return notes; });
  }

  function save() {
    if (S.busy || !S.saved) return;
    const b = bridge();
    if (!b) { say('bad', 'saving needs the Ebonwake app window'); draw(); return; }
    const f = readForm();
    if (f.errors.length) { say('bad', f.errors.join(' - ')); draw(); return; }
    const body = C.settingsBody(S.saved, f.edits);
    if (!body) { say('ok', 'nothing changed'); draw(); return; }
    S.busy = true;
    say('ok', 'saving...');
    draw();
    window.EWToast.via(b).post('/api/settings', body).then(function (res) {
      if (!res || !res.ok) throw new Error((res && res.error) || 'unknown error');
      take(res.data);
      if (window.EWOverrides) window.EWOverrides.refresh(true); // plan 080: mute / scale badges
      const changed = res.data && Array.isArray(res.data.changed) ? res.data.changed : [];
      const restart = res.data && Array.isArray(res.data.restart) ? res.data.restart : [];
      return after(changed).then(function (notes) {
        if (restart.length) notes.push('restart the server to apply ' + restart.join(', '));
        say(notes.length ? 'warn' : 'ok', 'saved ' + changed.length + ' setting' +
          (changed.length === 1 ? '' : 's') + (notes.length ? ' - ' + notes.join(' - ') : ''));
      });
    }).catch(function (e) {
      say('bad', 'save failed: ' + String(e && e.message || e));
    }).then(function () {
      S.busy = false;
      draw();
    });
  }

  // ---- render ----

  function field(f) {
    const row = el('label', 'ew-srow');
    const v = S.saved[f.key];
    row.appendChild(el('span', 'ew-slabel', f.label));
    let input;
    if (f.type === 'bool') {
      input = el('input');
      input.type = 'checkbox';
      input.checked = v === true;
    } else if (f.type === 'enum' || f.type === 'mute') {
      input = el('select');
      // Plan 078: display text may differ from the stored value (pin = always).
      // Plan 080: the mute select keeps an active mute as its first option.
      (f.type === 'mute' ? C.muteOptions(v, Date.now()) : C.enumOptions(f)).forEach(function (o) {
        const opt = el('option', null, o.text);
        opt.value = o.value;
        input.appendChild(opt);
      });
      input.value = v;
    } else {
      input = el('input', 'ew-sinput');
      input.type = 'text';
      input.value = C.settingInputText(f.key, v);
      if (f.type === 'anchor') {
        input.setAttribute('list', 'ew-anchors');
        input.placeholder = C.OVERLAY_ANCHORS.join(' ') + ' or x,y';
      }
      if (f.type === 'number') input.placeholder = f.min + '-' + f.max;
      if (f.type === 'dir') input.placeholder = (S.detected && S.detected[f.key]) || 'auto-detect';
    }
    input.dataset.key = f.key;
    if (S.restartKeys.indexOf(f.key) >= 0) row.title = 'applies after a server restart';
    S.inputs[f.key] = input;
    row.appendChild(input);
    const loc = C.settingLocalNote(f.key, v);
    if (f.type === 'dir') {
      row.appendChild(el('span', 'ew-muted ew-sdef', C.settingDetectedNote(f.key, v, S.detected)));
    } else if (loc) { // plan 078: stored UTC clock, local time beside it
      const n = el('span', 'ew-muted ew-sdef', loc.text);
      n.title = loc.title;
      row.appendChild(n);
    } else if (S.defaults && f.key in S.defaults && f.type !== 'bool') {
      row.appendChild(el('span', 'ew-muted ew-sdef', 'default ' + (C.settingInputText(f.key, S.defaults[f.key]) || 'blank')));
    }
    return row;
  }

  function draw() {
    const p = S.panel;
    if (!p) return;
    p.textContent = '';
    S.inputs = {};
    if (!S.saved) {
      const c = el('section', 'ew-card');
      c.appendChild(el('h2', null, 'Settings'));
      c.appendChild(el(S.err ? 'div' : 'p', S.err ? 'ew-err' : 'ew-muted', S.err || 'loading...'));
      p.appendChild(c);
      return;
    }
    const dl = el('datalist');
    dl.id = 'ew-anchors';
    C.OVERLAY_ANCHORS.forEach(function (a) { const o = el('option'); o.value = a; dl.appendChild(o); });
    p.appendChild(dl);
    C.SETTINGS_GROUPS.forEach(function (g) {
      const sec = el('section', 'ew-card ew-sgroup');
      sec.dataset.group = g.id;
      sec.appendChild(el('h2', null, g.title));
      C.settingsRows(g).forEach(function (f) { sec.appendChild(field(f)); });
      p.appendChild(sec);
    });
    const bar = el('div', 'ew-sbar');
    const btn = el('button', 'ew-btn', S.busy ? 'Saving...' : 'Save');
    btn.type = 'button';
    btn.disabled = S.busy;
    btn.addEventListener('click', save);
    bar.appendChild(btn);
    const rel = el('button', 'ew-btn', 'Revert');
    rel.type = 'button';
    rel.disabled = S.busy;
    rel.addEventListener('click', function () { S.msg = null; load(); });
    bar.appendChild(rel);
    if (S.err) bar.appendChild(el('span', 'ew-err', S.err));
    if (S.msg) bar.appendChild(el('span', 'ew-pill ' + S.msgCls + ' ew-stoast', S.msg));
    p.appendChild(bar);
  }

  // ---- mount ----

  function mount(panel) {
    panel.classList.add('ew-settings');
    S.panel = panel;
    draw();
  }

  function show() { if (!S.busy) load(); }

  // Theme applies from load, whichever tab opens first.
  load();

  window.EWSettings = { mount: mount, show: show };
})();
