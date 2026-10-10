/* EW shared pure logic, part `settings` (plan 107 split of ewcore.js):
    Black Spirit dice, Today row marks, Settings.
   Installed in order by ../ewcore.js, which re-exports the public names as
   EWCore. Loaded as a plain script before ewcore.js (window.EWCoreParts) and by
   require() under node --test. No DOM, no Electron. */
(function (root, install) {
  if (typeof module !== 'undefined' && module.exports) module.exports = install;
  else (root.EWCoreParts = root.EWCoreParts || {}).settings = install;
})(typeof self !== 'undefined' ? self : this, function (K) {
  'use strict';

  // from earlier parts
  const { OVERLAY_ANCHORS, OVERLAY_MODES, OVERLAY_OPACITY, OVERLAY_SCALE, WIDGETS,
    fmtDurationShort, isInt, isNum, plainObject, utcClockIn, validAccelerator, validAnchor } = K;

  // ---- Black Spirit's Adventure dice (plan 056) ----
  // GET /api/today `dice` = {earned, max, next_at_min, eta_utc, played_min,
  // logged_in, ...}: dice earned from logged-in minutes since the dice reset.
  // The overlay counts eta_utc down locally; the Today tab only SUGGESTS the
  // tick (the operator ticks; nothing auto-ticks).
  const DICE_ITEM_ID = 'black-spirits-adventure-dice';
  const DICE_ITEM_RE = /^black spirit'?s adventure dice$/i;

  function isDiceItem(it) {
    return plainObject(it) && (it.id === DICE_ITEM_ID ||
      (typeof it.title === 'string' && DICE_ITEM_RE.test(it.title.trim())));
  }

  function normalizeDice(d) {
    if (!plainObject(d) || !isInt(d.max, 1) || !isInt(d.earned, 0) || d.earned > d.max) return null;
    const next = d.next_at_min === null || isInt(d.next_at_min, 0) ? d.next_at_min : undefined;
    if (next === undefined) return null;
    const eta = typeof d.eta_utc === 'string' ? Date.parse(d.eta_utc) : NaN;
    return { earned: d.earned, max: d.max, next_at_min: next, eta: isNaN(eta) ? null : eta,
      played_min: isInt(d.played_min, 0) ? d.played_min : 0 };
  }

  function diceMin(min) { return fmtDurationShort(Math.max(1, min)); }

  // One overlay line: "die 2/3 in 12m" while logged in, "die 2/3 after 12m
  // play" logged out, "dice 0/3 - log in" before the first login, "dice 3/3".
  function diceLine(d, now) {
    const v = normalizeDice(d);
    if (!v) return '-';
    if (v.earned >= v.max || v.next_at_min === null) return 'dice ' + v.earned + '/' + v.max;
    const n = 'die ' + (v.earned + 1) + '/' + v.max;
    if (v.eta !== null) {
      const left = v.eta - now;
      return left <= 0 ? n + ' ready' : n + ' in ' + diceMin(Math.ceil(left / 60000));
    }
    if (v.earned === 0 && v.next_at_min === 0) return 'dice 0/' + v.max + ' - log in';
    return n + ' after ' + diceMin(v.next_at_min - v.played_min) + ' play';
  }

  // Today tab hint on the open dice row; '' when there is nothing to roll.
  function diceSuggest(it, d) {
    if (!isDiceItem(it) || it.done) return '';
    const v = normalizeDice(d);
    if (!v || v.earned < 1) return '';
    return v.earned + '/' + v.max + ' earned - roll, then tick';
  }

  // Plan 068: Today row marks. `auto` = the open period's tick was inferred
  // (login / logged minutes; the checkbox or "undo" unticks it and blocks
  // re-ticking until the next reset). `ready` = a ready_minutes row reached
  // its minutes (the dice roll itself stays an operator tick).
  function todayAutoMark(it) {
    return plainObject(it) && it.done === true && it.by === 'auto';
  }

  function todayReady(it) {
    return plainObject(it) && it.ready === true && it.done !== true;
  }

  function todayAutoTitle(it) {
    if (!plainObject(it) || typeof it.auto !== 'string') return '';
    if (it.auto === 'login') return 'auto-ticks on the first login after reset';
    const m = /^(logged|ready)_minutes:(\d+)$/.exec(it.auto);
    if (!m) return '';
    return (m[1] === 'logged' ? 'auto-ticks after ' : 'shown ready after ') + m[2] +
      ' logged-in minutes since reset';
  }

  // ---- Settings (plan 030) ----
  // Form model for the Settings tab. The server (server/ew/settings.py) is the
  // authority; these mirror its allowlist so the bridge refuses anything else.
  // Every key is a dotted config/local.json path; secrets and loop never appear.
  const THEMES = ['system', 'dark', 'light'];
  const UI_SCALE = [0.9, 1.3];
  // Plan 080: config keys that are no longer settable but may hold a plan 079
  // incident switch from config/local.json - the shell badge can clear one.
  const INCIDENT_KEYS = ['ocr.auto', 'play.auto_session', 'notices.auto_add', 'events.notice_check',
    'coupons.check', 'checklist.auto', 'overlay.auto', 'ocr.auto_commit_min', 'ocr.daily_cap',
    'play.grace_s', 'overlay.idle_min', 'notify.ladder_min', 'notify.quiet_closed',
    'events.maintenance_start_utc'];
  // Plan 078: human names for the overlay widgets, the notification rules and
  // the config keys the onboarding hints cite (as their Settings path).
  const LABELS = {
    grindSession: 'Grind session', grindBuff: 'Grind buffs', eventsSoon: 'Events ending soon',
    leveling: 'Leveling ETA', season: 'Season pass', marketTicker: 'Market ticker',
    worldBoss: 'World boss', dice: 'Adventure dice', whatNow: 'What now',
    marketAlert: 'Market price alerts', buffEnding: 'Buff ending', hotTime: 'Hot Time',
    resetPassed: 'Reset passed', newCoupon: 'New coupon', gameExit: 'Game closed',
    bossSoon: 'World boss soon', resetSoon: 'Reset soon', loginRisk: 'Login day at risk',
    maintLoss: 'Maintenance loss warning', claimDue: 'Reward claim due',
    'profile.family': 'Profile > Family name', 'bdo.install_dir': 'Game folders > BDO install folder',
    'bdo.documents_dir': 'Game folders > BDO Documents folder', 'overlay.anchor': 'Overlay > Anchor'
  };
  const MODE_TEXT = { auto: 'auto', pin: 'always', block: 'never' };
  // Plan 080: notify.mute_until choices -> hours from now ('' = not muted).
  const MUTE_CHOICES = [['', 'off'], ['1h', 'mute 1 h'], ['8h', 'mute 8 h'], ['24h', 'mute 24 h']];
  const MUTE_MAX_MS = 24 * 3600000;
  // Plan 080: no automation switch, notification checkbox, manual overlay
  // layout or tunable - those are fixed (config/local.json values are plan 079
  // incident switches, badged, 24 h). Five groups remain.
  const SETTINGS_GROUPS = [
    // Plan 065: blank = auto-detected (Steam library / Documents); a path = "use other".
    { id: 'game', title: 'Game folders', fields: [
      { key: 'bdo.install_dir', label: 'BDO install folder (blank = auto-detect)', type: 'dir' },
      { key: 'bdo.documents_dir', label: 'BDO Documents folder (blank = auto-detect)', type: 'dir' }
    ] },
    // Plan 078: one row per widget - auto (by context) / always / never,
    // stored as overlay.mode.<w> auto | pin | block (plan 079: 7 d expiry).
    { id: 'overlay', title: 'Overlay', fields: WIDGETS.map(function (w) {
      return { key: 'overlay.mode.' + w, label: LABELS[w], type: 'enum', options: OVERLAY_MODES, text: MODE_TEXT };
    }).concat([
      { key: 'overlay.anchor', label: 'Anchor', type: 'anchor', options: OVERLAY_ANCHORS },
      { key: 'overlay.display', label: 'Display (blank = primary)', type: 'display' },
      { key: 'overlay.scale', label: 'Scale', type: 'number', min: OVERLAY_SCALE[0], max: OVERLAY_SCALE[1], step: 0.05 },
      { key: 'overlay.opacity', label: 'Opacity', type: 'number', min: OVERLAY_OPACITY[0], max: OVERLAY_OPACITY[1], step: 0.05 }
    ]) },
    { id: 'hotkeys', title: 'Hotkeys', fields: [
      { key: 'hotkeys.toggleOverlay', label: 'Toggle overlay', type: 'hotkey' },
      { key: 'hotkeys.showDashboard', label: 'Show dashboard', type: 'hotkey' }
    ] },
    { id: 'profile', title: 'Profile', fields: [
      { key: 'profile.family', label: 'Family name (blank = none)', type: 'family' },
      // Plan 061: self-hosted BDO-REST-API base; blank = profile source off.
      { key: 'profile.base_url', label: 'Self-hosted profile API base (blank = off)', type: 'baseurl' },
      // Off = one character: Tag / alt-only maintenance loss warnings are hidden.
      { key: 'profile.multi_character', label: 'More than one character (Tag / alts)', type: 'bool' },
      // Plan 027 / 079: sale tax facts (an armed Value Pack timer wins over the box).
      { key: 'market.vp', label: 'Value Pack active', type: 'bool' },
      { key: 'market.fame_pct', label: 'Family fame bonus (0-1.5 %)', type: 'number', min: 0, max: 1.5, step: 0.05 }
    ] },
    { id: 'display', title: 'Display', fields: [
      { key: 'ui.theme', label: 'Theme', type: 'enum', options: THEMES },
      { key: 'ui.scale', label: 'Dashboard scale', type: 'number', min: UI_SCALE[0], max: UI_SCALE[1], step: 0.05 },
      // Plan 080: one mute for every alert (badged on the shell while active).
      { key: 'notify.mute_until', label: 'Mute notifications', type: 'mute' }
    ] }
  ];
  const DIR_MAX = 1024;
  const SETTINGS_FIELDS = {};
  SETTINGS_GROUPS.forEach(function (g) { g.fields.forEach(function (f) { SETTINGS_FIELDS[f.key] = f; }); });
  const SETTINGS_KEYS = Object.keys(SETTINGS_FIELDS);

  // Plan 078: the fields a group renders (hidden ones stay allowlisted).
  function settingsRows(g) {
    return plainObject(g) && Array.isArray(g.fields) ? g.fields.filter(function (f) { return !f.hidden; }) : [];
  }

  // An enum field's <option>s: stored value + display text.
  function enumOptions(f) {
    if (!plainObject(f) || !Array.isArray(f.options)) return [];
    return f.options.map(function (o) {
      return { value: o, text: plainObject(f.text) && typeof f.text[o] === 'string' ? f.text[o] : o };
    });
  }

  // A hint naming config keys -> the same hint naming Settings fields.
  function labelHint(s) {
    if (typeof s !== 'string') return '';
    return s.replace(/\b[a-z]+\.[a-z_]+\b/g, function (k) {
      return Object.prototype.hasOwnProperty.call(LABELS, k) ? LABELS[k] : k;
    });
  }
  const FAMILY_RE = /^[A-Za-z0-9_]{2,16}$/;

  // Plan 061 mirror of progress.base_ok: https on any host, http on loopback
  // only; no userinfo, query, fragment, whitespace or non-ASCII; <= 200 chars.
  function validProfileBase(v) {
    if (typeof v !== 'string' || !v || v.length > 200 || !/^[\x21-\x7e]+$/.test(v)) return false;
    const m = /^(https?):\/\/([^/?#@]+)(\/[^?#]*)?$/.exec(v);
    if (!m) return false;
    const hp = /^(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9.-]+)(:\d{1,5})?$/.exec(m[2]);
    if (!hp || (hp[2] && Number(hp[2].slice(1)) > 65535)) return false;
    const host = hp[1].toLowerCase();
    const loop = host === 'localhost' || host === '[::1]' || /^127(\.\d{1,3}){3}$/.test(host);
    return m[1] === 'https' || loop;
  }

  // Plan 080 mirror of settings.valid_mute: an ISO time with an offset, at
  // most 24 h after nowMs (a minute of slack for the round trip).
  function validMute(v, nowMs) {
    if (typeof v !== 'string' || v.length > 40 || !/(Z|[+-]\d\d:\d\d)$/.test(v)) return false;
    const t = Date.parse(v);
    return isNum(t) && t <= nowMs + MUTE_MAX_MS + 60000;
  }

  // The mute select's value -> the stored value: '' / '1h' / '8h' / '24h'
  // (hours from nowMs, ISO UTC) or an already-stored ISO time (kept as is).
  function muteValue(raw, nowMs) {
    if (raw === '') return '';
    const m = typeof raw === 'string' ? /^(1|8|24)h$/.exec(raw) : null;
    if (m) return new Date(nowMs + Number(m[1]) * 3600000).toISOString().replace(/\.\d{3}Z$/, '+00:00');
    return raw;
  }

  // The mute select's options for stored value `v`: an active mute first
  // (kept on save), then the choices.
  function muteOptions(v, nowMs) {
    const out = [];
    const t = typeof v === 'string' && v ? Date.parse(v) : NaN;
    if (isNum(t) && t > nowMs) out.push({ value: v, text: 'muted until ' + new Date(t).toLocaleTimeString() });
    MUTE_CHOICES.forEach(function (c) { out.push({ value: c[0], text: c[1] }); });
    return out;
  }

  function settingValueOk(f, v) {
    switch (f.type) {
      case 'bool': return typeof v === 'boolean';
      case 'anchor': return validAnchor(v) && (typeof v === 'string' || (Math.abs(v.x) <= 100000 && Math.abs(v.y) <= 100000));
      case 'display': return v === null || (Number.isInteger(v) && v >= 0 && v <= 16);
      case 'number': return isNum(v) && v >= f.min && v <= f.max && (!f.int || Number.isInteger(v));
      case 'hotkey': return validAccelerator(v) && v.length <= 64;
      case 'family': return v === '' || (typeof v === 'string' && FAMILY_RE.test(v));
      case 'enum': return typeof v === 'string' && f.options.indexOf(v) >= 0;
      case 'hhmm': return v === '' || (typeof v === 'string' && /^([01][0-9]|2[0-3]):[0-5][0-9]$/.test(v));
      case 'mute': return v === '' || validMute(v, Date.now());
      // Shape only (the server checks the folder exists): '' or an absolute path.
      case 'dir': return v === '' || (typeof v === 'string' && v.length <= DIR_MAX && v === v.trim() &&
        !/[\u0000-\u001f\u007f]/.test(v) && /^([A-Za-z]:[\\/]|[\\/])/.test(v));
      case 'baseurl': return v === '' || validProfileBase(v);
      default: return false;
    }
  }

  // Bridge check for POST /api/settings: {set: {key: value}} over the allowlist,
  // or plan 079 {clear: key} (an allowlisted or plan 080 incident key; the
  // server checks the ledger).
  function validSettingsBody(body) {
    if (plainObject(body) && Object.keys(body).length === 1 && typeof body.clear === 'string') {
      // Plan 083: a gallery pick clears like any override ("Use newest").
      return Object.prototype.hasOwnProperty.call(SETTINGS_FIELDS, body.clear) ||
        INCIDENT_KEYS.indexOf(body.clear) >= 0 ||
        /^portrait\.[A-Za-z][A-Za-z ]{0,39}$/.test(body.clear);
    }
    if (!plainObject(body) || Object.keys(body).length !== 1 || !plainObject(body.set)) return false;
    const keys = Object.keys(body.set);
    return keys.length >= 1 && keys.length <= 64 && keys.every(function (k) {
      return Object.prototype.hasOwnProperty.call(SETTINGS_FIELDS, k) && settingValueOk(SETTINGS_FIELDS[k], body.set[k]);
    });
  }

  // One raw form input -> {value} or {error}. Checkboxes pass a boolean;
  // anchors pass a named anchor or 'x,y'; the mute select passes '' / '1h' /
  // '8h' / '24h' / the stored time (nowMs: the clock, default Date.now());
  // everything else passes a string.
  function parseSettingInput(key, raw, nowMs) {
    const f = SETTINGS_FIELDS[key];
    if (!f) return { error: key + ': unknown setting' };
    let v = raw;
    const s = typeof raw === 'string' ? raw.trim() : raw;
    if (f.type === 'number') v = typeof s === 'string' && /^-?\d+(\.\d+)?$/.test(s) ? Number(s) : NaN;
    else if (f.type === 'mute') v = muteValue(s, isNum(nowMs) ? nowMs : Date.now());
    else if (f.type === 'display') v = s === '' ? null : (typeof s === 'string' && /^\d+$/.test(s) ? Number(s) : NaN);
    else if (f.type === 'anchor' && typeof s === 'string' && s.indexOf(',') >= 0) {
      const m = /^(-?\d+)\s*,\s*(-?\d+)$/.exec(s);
      v = m ? { x: Number(m[1]), y: Number(m[2]) } : null;
    } else if (typeof s === 'string') v = s;
    if (!settingValueOk(f, v)) {
      let hint = 'invalid';
      if (f.type === 'number') hint = 'a number ' + f.min + '-' + f.max;
      else if (f.type === 'hotkey') hint = 'modifier(s) + key, e.g. Control+Alt+E';
      else if (f.type === 'family') hint = '2-16 letters, digits or _';
      else if (f.type === 'anchor') hint = OVERLAY_ANCHORS.join('|') + ' or x,y';
      else if (f.type === 'display') hint = 'blank or a display number 0-16';
      else if (f.type === 'hhmm') hint = 'blank or HH:MM (UTC), e.g. 07:00';
      else if (f.type === 'dir') hint = 'blank (auto-detect) or a full folder path';
      else if (f.type === 'baseurl') hint = 'blank, https://..., or http://127.0.0.1:8001/v1';
      else if (f.type === 'mute') hint = 'off, 1h, 8h or 24h';
      return { error: f.label + ': ' + hint };
    }
    return { value: v };
  }

  // Text shown in an input for a stored value.
  function settingInputText(key, v) {
    const f = SETTINGS_FIELDS[key];
    if (!f) return '';
    if (f.type === 'anchor' && plainObject(v)) return v.x + ',' + v.y;
    if (v === null || v === undefined) return '';
    return String(v);
  }

  // Plan 065: note beside a 'dir' field from GET /api/settings `detected`
  // ({key: path|null}); '' for any other field.
  function settingDetectedNote(key, saved, detected) {
    const f = SETTINGS_FIELDS[key];
    if (!f || f.type !== 'dir') return '';
    const d = plainObject(detected) && typeof detected[key] === 'string' && detected[key] ? detected[key] : null;
    const own = typeof saved === 'string' && saved !== '';
    if (!own) return d ? 'auto-detected: ' + d : 'not detected - type the folder path';
    return d && d !== saved ? 'using this folder (auto-detected: ' + d + ')' : 'using this folder';
  }

  // Plan 078: a stored 'hhmm' (UTC) setting's local equivalent in opts.zone
  // (default local, offset at opts.now) -> {text, title: 'UTC HH:MM'}, or null.
  function settingLocalNote(key, v, opts) {
    const f = SETTINGS_FIELDS[key];
    if (!f || f.type !== 'hhmm') return null;
    const c = utcClockIn(v, opts);
    if (!c) return null;
    const day = c.shift < 0 ? ' (day before)' : (c.shift > 0 ? ' (day after)' : '');
    return { text: '= ' + c.hhmm + ' local' + day, title: 'UTC ' + v };
  }

  function sameSetting(a, b) {
    if (plainObject(a) && plainObject(b)) return a.x === b.x && a.y === b.y;
    return a === b;
  }

  // Edited values that differ from the saved ones, as a POST body (or null).
  function settingsBody(saved, edits) {
    const set = {};
    Object.keys(edits || {}).forEach(function (k) {
      if (!SETTINGS_FIELDS[k]) return;
      if (!saved || !sameSetting(saved[k], edits[k])) set[k] = edits[k];
    });
    return Object.keys(set).length ? { set: set } : null;
  }

  // What the app must redo after a save, from the server's `changed` list.
  function settingsEffects(changed) {
    const ks = Array.isArray(changed) ? changed : [];
    // Plan 067: context keys reach the overlay live over SSE (no recreate).
    const live = function (k) { return k.indexOf('overlay.mode.') === 0; };
    const has = function (p) { return ks.some(function (k) { return typeof k === 'string' && k.indexOf(p) === 0 && !live(k); }); };
    return { overlay: has('overlay.'), shell: has('hotkeys.') || has('ui.scale'), theme: has('ui.theme') };
  }

  // data-theme for ui.theme; 'system' (plan 080: the default, so anything
  // else too) follows prefers-color-scheme, which Electron feeds from nativeTheme.
  function themeAttr(theme, prefersDark) {
    if (theme === 'light' || theme === 'dark') return theme;
    return prefersDark ? 'dark' : 'light';
  }

  // Dashboard zoom factor from config ui.scale (main process).
  function uiScale(config) {
    const u = config && plainObject(config.ui) ? config.ui.scale : undefined;
    return isNum(u) && u >= UI_SCALE[0] && u <= UI_SCALE[1] ? u : 1;
  }

  Object.assign(K, { DICE_ITEM_ID: DICE_ITEM_ID, DICE_ITEM_RE: DICE_ITEM_RE,
    isDiceItem: isDiceItem, normalizeDice: normalizeDice, diceMin: diceMin, diceLine: diceLine,
    diceSuggest: diceSuggest, todayAutoMark: todayAutoMark, todayReady: todayReady,
    todayAutoTitle: todayAutoTitle, THEMES: THEMES, UI_SCALE: UI_SCALE,
    INCIDENT_KEYS: INCIDENT_KEYS, LABELS: LABELS, MODE_TEXT: MODE_TEXT,
    MUTE_CHOICES: MUTE_CHOICES, MUTE_MAX_MS: MUTE_MAX_MS, SETTINGS_GROUPS: SETTINGS_GROUPS,
    DIR_MAX: DIR_MAX, SETTINGS_FIELDS: SETTINGS_FIELDS, SETTINGS_KEYS: SETTINGS_KEYS,
    settingsRows: settingsRows, enumOptions: enumOptions, labelHint: labelHint,
    FAMILY_RE: FAMILY_RE, validProfileBase: validProfileBase, validMute: validMute,
    muteValue: muteValue, muteOptions: muteOptions, settingValueOk: settingValueOk,
    validSettingsBody: validSettingsBody, parseSettingInput: parseSettingInput,
    settingInputText: settingInputText, settingDetectedNote: settingDetectedNote,
    settingLocalNote: settingLocalNote, sameSetting: sameSetting, settingsBody: settingsBody,
    settingsEffects: settingsEffects, themeAttr: themeAttr, uiScale: uiScale });
  return function link() {};
});
