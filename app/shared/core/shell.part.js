/* EW shared pure logic, part `shell` (plan 107 split of ewcore.js):
    Imperial delivery, the bridge POST validators, command palette, class chip,
   character card, portraits.
   Installed in order by ../ewcore.js, which re-exports the public names as
   EWCore. Loaded as a plain script before ewcore.js (window.EWCoreParts) and by
   require() under node --test. No DOM, no Electron. */
(function (root, install) {
  if (typeof module !== 'undefined' && module.exports) module.exports = install;
  else (root.EWCoreParts = root.EWCoreParts || {}).shell = install;
})(typeof self !== 'undefined' ? self : this, function (K) {
  'use strict';

  // from earlier parts
  const { ISO_TS, SERVER, SLUG, buffRows, derivedText, exact, fmtDuration, fmtDurationShort,
    fmtLocal, fmtSilver, inRange, isInt, isNum, overrideBadge, parseDuration, parseGrindForm,
    parseSampleForm, parseSilver, parseWatchForm, plainObject, searchRows, validAscii,
    validBossesBody, validCraftingBody, validDeadeyeBody, validEventsBody, validGrindBody,
    validInventoryBody, validLevelingBody, validMaintDigestBody, validMountsBody, validName,
    validOcrBody, validOnboardingBody, validPetsBody, validProgressBody, validSettingsBody,
    validTodayBody, validWatchBody } = K;

  // ---- Imperial delivery (plan 053) ----
  // Formatters over GET /api/imperial: {day, reset: {next_utc, left_s}, cp:
  // {value, source, typed}, types: [{type, cap, delivered, left, done,
  // mastery_pct}], boxes: [{id, type, name, items, payout, cost, ratio,
  // missing, origin}], best: {cooking: [ids], alchemy: [ids]}, rules,
  // data_error}. Bounds mirror server/ew/imperial.py. Operator ticks and
  // cached market prices only; nothing comes from the game.

  const IMP_TYPES = ['cooking', 'alchemy'];
  const IMP_BOX_ID = /^(cooking|alchemy)-[a-z0-9-]{1,40}$/;
  const IMP_NAME_MAX = 40;
  const IMP_ITEMS_MAX = 10;
  const IMP_ITEM_ID = [1, 2147483647];
  const IMP_QTY = [1, 9999];
  const IMP_CP = [0, 10000];
  const IMP_COUNT_MAX = 10000;
  const IMP_MASTERY_MAX = 500;

  function impTitle(t) { return t.charAt(0).toUpperCase() + t.slice(1); }

  function validImpItems(v) {
    if (!Array.isArray(v) || !v.length || v.length > IMP_ITEMS_MAX) return false;
    const seen = {};
    return v.every(function (it) {
      if (!exact(it, ['id', 'qty']) || !inRange(it.id, IMP_ITEM_ID) || !inRange(it.qty, IMP_QTY) || seen[it.id]) return false;
      seen[it.id] = true;
      return true;
    });
  }

  // POST /api/imperial body: exactly one of deliver|cp|mastery|box_add|box_del.
  function validImperialBody(body) {
    if (!plainObject(body)) return false;
    const keys = Object.keys(body);
    if (keys.length !== 1) return false;
    const v = body[keys[0]];
    switch (keys[0]) {
      case 'deliver': return exact(v, ['type', 'add']) && IMP_TYPES.indexOf(v.type) >= 0 &&
        isInt(v.add, -IMP_COUNT_MAX) && v.add <= IMP_COUNT_MAX && v.add !== 0;
      case 'cp': return v === null || inRange(v, IMP_CP);
      case 'mastery': return exact(v, ['type', 'pct']) && IMP_TYPES.indexOf(v.type) >= 0 &&
        isNum(v.pct) && v.pct >= 0 && v.pct <= IMP_MASTERY_MAX;
      case 'box_add': return exact(v, ['type', 'name', 'items']) && IMP_TYPES.indexOf(v.type) >= 0 &&
        validAscii(v.name, IMP_NAME_MAX) && validImpItems(v.items);
      case 'box_del': return typeof v === 'string' && IMP_BOX_ID.test(v);
      default: return false;
    }
  }

  // One row per type: {type, label, delivered, cap, left, done, text, mastery}.
  // Without CP the cap is unknown: the text asks for it instead of guessing.
  function imperialRows(view) {
    const types = plainObject(view) && Array.isArray(view.types) ? view.types : [];
    return types.filter(function (t) {
      return plainObject(t) && IMP_TYPES.indexOf(t.type) >= 0 && isNum(t.delivered);
    }).map(function (t) {
      const cap = isNum(t.cap) ? t.cap : null;
      const left = cap === null ? null : Math.max(0, cap - t.delivered);
      return { type: t.type, label: impTitle(t.type), delivered: t.delivered, cap: cap, left: left,
        done: left === 0,
        text: cap === null ? t.delivered + ' delivered (set CP for the cap)' : left + ' / ' + cap + ' left',
        mastery: isNum(t.mastery_pct) ? t.mastery_pct : 0 };
    });
  }

  // Countdown to the next reset from reset.next_utc (live between polls).
  function imperialReset(view, now) {
    const r = plainObject(view) && plainObject(view.reset) ? view.reset : null;
    const at = r && typeof r.next_utc === 'string' && ISO_TS.test(r.next_utc) ? Date.parse(r.next_utc) : NaN;
    return isFinite(at) ? 'resets in ' + fmtDuration(at - now) : '';
  }

  function imperialCpText(view) {
    const c = plainObject(view) && plainObject(view.cp) ? view.cp : null;
    if (!c || !isNum(c.value)) return 'CP ?';
    if (c.source === 'ocr') return 'CP ' + c.value + ' (' + derivedText(c).text + ')';  // plan 081
    return 'CP ' + c.value + (c.source === 'operator' ? ' (typed)' : '');
  }

  // Best boxes of one type in server order: [{id, name, text, cls}].
  function imperialBest(view, type) {
    if (!plainObject(view) || !plainObject(view.best) || !Array.isArray(view.boxes)) return [];
    const ids = Array.isArray(view.best[type]) ? view.best[type] : [];
    return ids.map(function (id) {
      const b = view.boxes.filter(function (x) { return plainObject(x) && x.id === id; })[0];
      if (!b || !isNum(b.payout) || typeof b.name !== 'string') return null;
      return { id: id, name: b.name, cls: '',
        text: fmtSilver(b.payout) + ' for ' + fmtSilver(b.cost) + (isNum(b.ratio) ? ' (x' + b.ratio + ')' : '') };
    }).filter(Boolean);
  }

  // Boxes no price could value (shown so the operator can watch the items).
  function imperialUnpriced(view) {
    const boxes = plainObject(view) && Array.isArray(view.boxes) ? view.boxes : [];
    return boxes.filter(function (b) { return plainObject(b) && Array.isArray(b.missing) && b.missing.length; })
      .map(function (b) { return String(b.name) + ': no cached price for ' + b.missing.join(', '); });
  }

  // "9213 x 10, 5961 x 2" -> [{id, qty}] or null.
  function parseImpItems(text) {
    if (typeof text !== 'string' || !text.trim()) return null;
    const out = [];
    const parts = text.split(',');
    for (let i = 0; i < parts.length; i++) {
      const m = /^\s*(\d{1,10})\s*[xX*]\s*(\d{1,4})\s*$/.exec(parts[i]);
      if (!m) return null;
      out.push({ id: Number(m[1]), qty: Number(m[2]) });
    }
    return validImpItems(out) ? out : null;
  }

  // Add-box form {type, name, items} -> {ok, body} | {ok: false, error}.
  function parseImperialBox(f) {
    const name = f && typeof f.name === 'string' ? f.name.trim() : '';
    if (!f || IMP_TYPES.indexOf(f.type) < 0) return { ok: false, error: 'pick cooking or alchemy' };
    if (!validAscii(name, IMP_NAME_MAX)) return { ok: false, error: 'name: 1-40 plain characters' };
    const items = parseImpItems(f.items);
    if (!items) return { ok: false, error: 'items: "item id x qty", comma separated, up to 10' };
    return { ok: true, body: { box_add: { type: f.type, name: name, items: items } } };
  }

  // CP input: blank clears the typed value.
  function parseImperialCp(text) {
    const t = typeof text === 'string' ? text.trim().replace(/,/g, '') : '';
    if (!t) return { ok: true, body: { cp: null } };
    if (!/^\d{1,5}$/.test(t) || !inRange(Number(t), IMP_CP)) return { ok: false, error: 'CP: a whole number 0-10000' };
    return { ok: true, body: { cp: Number(t) } };
  }

  // The only routes the dashboard bridge forwards, each with its body check.
  const POST_VALIDATORS = {
    '/api/market/watch': validWatchBody, '/api/today': validTodayBody, '/api/progress': validProgressBody,
    '/api/grind': validGrindBody, '/api/events': validEventsBody, '/api/deadeye': validDeadeyeBody,
    '/api/ocr': validOcrBody, '/api/leveling': validLevelingBody, '/api/settings': validSettingsBody,
    '/api/bosses': validBossesBody, '/api/pets': validPetsBody, '/api/inventory': validInventoryBody,
    '/api/mounts': validMountsBody, '/api/onboarding': validOnboardingBody,
    '/api/crafting': validCraftingBody, '/api/imperial': validImperialBody,
    '/api/maint/digest': validMaintDigestBody, '/api/portraits': validPortraitsBody
  };
  const POST_ROUTES = Object.keys(POST_VALIDATORS);

  function validPost(route, body) {
    return typeof route === 'string' && Object.prototype.hasOwnProperty.call(POST_VALIDATORS, route) &&
      POST_VALIDATORS[route](body);
  }

  // ---- Command palette (plan 050) ----
  // parseCommand(text, catalog): one typed line -> {ok, route, body, label}
  // for an existing POST route, {ok, tab, label} for `go`, or {ok: false,
  // error}. catalog holds the GET bodies the dashboard already polls:
  // {today, grind, items (searchRows), tabs, events, deadeye}; any may be
  // missing. Names are matched fuzzily and an ambiguous match is an error,
  // never a guess. Silver and durations use the plan 048 parsers.
  const PALETTE_COMMANDS = [
    { verb: 'tick', usage: 'tick <daily>', route: '/api/today' },
    { verb: 'arm', usage: 'arm <buff> [30m]', route: '/api/grind' },
    { verb: 'start grind', usage: 'start grind <spot>', route: '/api/grind' },
    { verb: 'stop grind', usage: 'stop grind [1.2b]', route: '/api/grind' },
    { verb: 'log xp', usage: 'log xp <level> <pct>', route: '/api/leveling' },
    { verb: 'watch', usage: 'watch <item name>', route: '/api/market/watch' },
    { verb: 'go', usage: 'go <tab>', route: null }
  ];
  const PALETTE_ROUTES = PALETTE_COMMANDS.map(function (c) { return c.route; })
    .filter(function (r, i, a) { return r !== null && a.indexOf(r) === i; });
  const PALETTE_MAX = 200;

  // Plan 078: hover title of the top-bar palette button.
  function paletteTitle() {
    return 'Command palette (Ctrl+K): ' + PALETTE_COMMANDS.map(function (c) { return c.verb; }).join(', ') +
      ', / to search';
  }

  // Plan 082: top-left class chip. The class is the active class tab, else the
  // class of the loaded characterNo, else the Progress class. A class with no
  // bound portrait gets an EMPTY chip - never another class's image.
  const PORTRAIT_HINT = 'Take your character portrait in game; EW picks it up automatically.';

  function classKey(s) { return String(s).toLowerCase().replace(/[^a-z0-9]/g, ''); }

  // Tab id -> class name known to the portraits view (e.g. 'deadeye' -> 'Deadeye'), or null.
  function classOfTab(tabId, view) {
    if (typeof tabId !== 'string' || !tabId || !view || typeof view !== 'object') return null;
    const names = Object.keys(view.classes && typeof view.classes === 'object' ? view.classes : {});
    [view.loaded_cls, view.progress_cls].forEach(function (c) {
      if (typeof c === 'string' && c && names.indexOf(c) < 0) names.push(c);
    });
    const want = classKey(tabId);
    for (let i = 0; i < names.length; i++) if (classKey(names[i]) === want) return names[i];
    return null;
  }

  // Class name -> the tab id showing it, or null.
  function tabOfClass(cls, tabIds) {
    if (typeof cls !== 'string' || !cls || !Array.isArray(tabIds)) return null;
    const want = classKey(cls);
    for (let i = 0; i < tabIds.length; i++) if (classKey(tabIds[i]) === want) return tabIds[i];
    return null;
  }

  function portraitChip(view, activeCls, opts) {
    const v = view && typeof view === 'object' ? view : {};
    const pick = [activeCls, v.loaded_cls, v.progress_cls].filter(function (c) {
      return typeof c === 'string' && c;
    });
    const cls = pick.length ? pick[0] : null;
    const name = cls || 'Character';
    const row = cls && v.classes && typeof v.classes === 'object' ? v.classes[cls] : null;
    const cur = row && row.current && typeof row.current === 'object' ? row.current : null;
    if (!cur || !portraitId(cur.id)) {
      return { cls: cls, src: null, alt: name + ': no portrait yet', title: PORTRAIT_HINT, empty: true };
    }
    const f = typeof cur.at === 'string' ? fmtLocal(cur.at, opts) : null;
    const when = f ? f.text : '';
    // Plan 083: a gallery pick (plan 079 override) shows its image + an accent dot.
    const pinned = cur.from === 'override';
    const p = pinned && typeof cur.set_at === 'string' ? fmtLocal(cur.set_at, opts) : null;
    return { cls: cls, src: portraitSrc(cur, 's'), alt: name + ' portrait',
      title: name + ' portrait' + (when ? ', taken ' + when : '') +
        (pinned ? '; pinned' + (p ? ' ' + p.text.slice(0, 10) : '') + ' (override)' : ''),
      empty: false, pinned: pinned };
  }

  // Plan 084: left-rail character card (End Game card style). Class choice as
  // portraitChip; src is the 312 x 402 `m` thumb. The card block values
  // (leveling, Life & CP, Imperial CP) belong to the Progress character, so
  // another class shows its class line only. Hidden / missing values are
  // omitted - never "0" for unknown, never "?".
  function cardCount(v) { return typeof v === 'number' && Number.isInteger(v) && v >= 0; }

  function portraitCard(view, activeCls, opts) {
    const v = view && typeof view === 'object' ? view : {};
    const chip = portraitChip(v, activeCls, opts);
    const cls = chip.cls;
    const mine = !!cls && (typeof v.progress_cls !== 'string' || !v.progress_cls || v.progress_cls === cls);
    const card = mine && plainObject(v.card) ? v.card : {};
    const age = function (src, at) {
      const f = typeof at === 'string' ? fmtLocal(at, opts) : null;
      const bits = [typeof src === 'string' && src ? src : null, f ? f.text : null].filter(Boolean);
      return bits.length ? ' (' + bits.join(', ') + ')' : '';
    };
    const lines = [];
    const info = [];
    if (cardCount(card.level) && card.level > 0) {
      lines.push({ k: 'level', text: 'Lv.' + card.level });
      info.push('Lv.' + card.level + age(card.level_source, card.level_at));
    }
    if (cls) lines.push({ k: 'cls', text: cls });
    if (typeof card.name === 'string' && card.name) lines.push({ k: 'name', text: card.name });
    const over = [];
    if (cardCount(card.energy)) {
      over.push({ k: 'energy', text: 'Energy ' + card.energy });
      info.push('Energy ' + card.energy + age('profile', card.energy_at));
    }
    if (cardCount(card.cp)) {
      over.push({ k: 'cp', text: 'CP ' + card.cp });
      info.push('CP ' + card.cp + age(card.cp_src, card.cp_at));
    }
    const head = (cls ? cls + ' character card; ' : '') + chip.title;
    return { cls: cls, src: chip.empty ? null : chip.src.replace(/\?size=s$/, '?size=m'),
      alt: chip.alt, title: [head].concat(info).join('; '), empty: chip.empty,
      pinned: !!chip.pinned, lines: lines, over: chip.empty ? [] : over };
  }

  // Plan 083: index ids only - an archived portrait `<char_no>-<mtime>` or a
  // screenshot `s<16 hex>`; anything else never becomes a src.
  const PORTRAIT_ID_RE = /^\d{6,20}-\d{1,12}$/;
  const SHOT_ID_RE = /^s[0-9a-f]{16}$/;
  function portraitId(id) {
    return typeof id === 'string' && (PORTRAIT_ID_RE.test(id) || SHOT_ID_RE.test(id));
  }
  // A screenshot is served as its original (no stdlib JPEG thumbnail).
  function portraitSrc(item, size) {
    if (!item || !portraitId(item.id)) return null;
    const base = SERVER + '/api/portraits/img/' + encodeURIComponent(item.id);
    return SHOT_ID_RE.test(item.id) ? base : base + '?size=' + size;
  }

  function galleryAlt(cls, item, opts) {
    const f = typeof item.at === 'string' ? fmtLocal(item.at, opts) : null;
    return (cls || 'Character') + (item.kind === 'shot' ? ' screenshot' : ' portrait') + (f ? ', ' + f.text : '');
  }

  // Plan 083: Deadeye tab "Portraits" card model from GET /api/portraits?cls=.
  // {cls, empty, emptyText, current: {id, src, alt, pinned, source, badge, key}
  // | null, items: [{id, kind, src, alt, pressed, tabindex}] (history, then
  // screenshots), unknown: [{id, kind, src, alt}]}.
  function portraitGallery(view, cls, opts) {
    const v = plainObject(view) ? view : {};
    const name = typeof cls === 'string' && cls ? cls : (typeof v.cls === 'string' ? v.cls : null);
    const row = name && plainObject(v.classes) && plainObject(v.classes[name]) ? v.classes[name] : null;
    const cur = row && plainObject(row.current) && portraitId(row.current.id) ? row.current : null;
    const list = function (a) { return Array.isArray(a) ? a.filter(function (i) { return plainObject(i) && portraitId(i.id); }) : []; };
    const rows = list(v.history).map(function (i) { return Object.assign({}, i, { kind: 'portrait' }); })
      .concat(list(v.shots).map(function (i) { return Object.assign({}, i, { kind: 'shot' }); }));
    const curId = cur ? cur.id : null;
    let focus = rows.findIndex(function (i) { return i.id === curId; });
    if (focus < 0) focus = 0;
    const items = rows.map(function (i, k) {
      return { id: i.id, kind: i.kind, src: portraitSrc(i, 's'), alt: galleryAlt(name, i, opts),
        pressed: i.id === curId, tabindex: k === focus ? 0 : -1 };
    });
    const unknown = list(v.unknown_items).map(function (i) {
      return { id: i.id, kind: i.kind === 'shot' ? 'shot' : 'portrait', src: portraitSrc(i, 's'),
        alt: galleryAlt('Unknown character', i, opts) };
    });
    let current = null;
    if (cur) {
      const pinned = cur.from === 'override';
      const at = fmtLocal(cur.at, opts);
      const set = pinned ? fmtLocal(cur.set_at, opts) : null;
      current = { id: cur.id, src: portraitSrc(cur, 'm'), alt: galleryAlt(name, cur, opts), pinned: pinned,
        source: pinned ? 'pinned ' + (set ? set.text.slice(0, 10) : '') : 'auto - newest portrait' + (at ? ', ' + at.text : ''),
        badge: pinned ? overrideBadge(cur.entry) : null, key: 'portrait.' + name };
    }
    return { cls: name, empty: !current && !items.length,
      emptyText: 'No ' + (name || 'Deadeye') + ' portrait yet', current: current, items: items, unknown: unknown };
  }

  // Roving tabindex: the next focused index for a key, or null (not handled).
  function galleryMove(index, key, n) {
    if (!(n > 0)) return null;
    const i = Number.isInteger(index) ? Math.min(Math.max(index, 0), n - 1) : 0;
    if (key === 'ArrowRight' || key === 'ArrowDown') return Math.min(i + 1, n - 1);
    if (key === 'ArrowLeft' || key === 'ArrowUp') return Math.max(i - 1, 0);
    if (key === 'Home') return 0;
    if (key === 'End') return n - 1;
    return null;
  }

  // POST /api/portraits bodies (dashboard bridge).
  const PORTRAIT_CLS_RE = /^[A-Za-z][A-Za-z ]{0,39}$/;
  function validPortraitsBody(body) {
    if (!plainObject(body) || Object.keys(body).length !== 1) return false;
    if (typeof body.clear === 'string') return /^portrait\.[A-Za-z][A-Za-z ]{0,39}$/.test(body.clear);
    const p = plainObject(body.pick) ? body.pick : (plainObject(body.bind) ? body.bind : null);
    return !!p && Object.keys(p).length === 2 && typeof p.cls === 'string' && PORTRAIT_CLS_RE.test(p.cls) &&
      portraitId(p.id);
  }

  function squash(s) { return String(s).toLowerCase().replace(/\s+/g, ' ').trim(); }

  // Match tier of query q in name n (both squashed): 4 exact, 3 prefix,
  // 2 substring, 1 every query word starts a name word, 0.5 the letters in
  // order (initials), 0 none.
  function fuzzyScore(q, n) {
    if (!q || !n) return 0;
    if (n === q) return 4;
    if (n.indexOf(q) === 0) return 3;
    if (n.indexOf(q) > 0) return 2;
    const words = n.split(/[^a-z0-9]+/).filter(Boolean);
    if (q.split(' ').every(function (w) { return words.some(function (x) { return x.indexOf(w) === 0; }); })) return 1;
    const letters = q.replace(/ /g, '');
    let j = 0;
    for (let i = 0; i < n.length && j < letters.length; i++) if (n[i] === letters[j]) j++;
    return j === letters.length ? 0.5 : 0;
  }

  // Rows matching q, best tier first, list order kept within a tier.
  function fuzzyRank(q, rows, nameOf) {
    const s = squash(q);
    return rows.map(function (r, i) { return { r: r, i: i, s: fuzzyScore(s, squash(nameOf(r))) }; })
      .filter(function (x) { return x.s > 0; })
      .sort(function (a, b) { return (b.s - a.s) || (a.i - b.i); });
  }

  // One row for q, or an error naming the tied candidates.
  function fuzzyPick(q, rows, nameOf, what) {
    const ranked = fuzzyRank(q, rows, nameOf);
    if (!ranked.length) return { error: 'no ' + what + ' matches "' + q + '"' };
    const top = ranked.filter(function (x) { return x.s === ranked[0].s; });
    const names = top.map(function (x) { return nameOf(x.r); })
      .filter(function (n, i, a) { return a.indexOf(n) === i; });
    if (top.length > 1) {
      return { error: 'ambiguous: ' + names.slice(0, 4).join(', ') + (names.length > 4 ? ', ...' : '') + ' - type more' };
    }
    return { row: ranked[0].r };
  }

  function listOf(doc, key) {
    return plainObject(doc) && Array.isArray(doc[key]) ? doc[key].filter(plainObject) : null;
  }

  // Copies of rows with a typeable unique name `_n`: name, plus the row's key
  // when two rows share a name (so a fill-in always parses to one row).
  // Rows repeating an earlier key are dropped.
  function uniqueNamed(rows, nameOf, keyOf, tag) {
    const keys = [];
    const out = (rows || []).filter(function (r) {
      const n = nameOf(r);
      const k = keyOf(r);
      if (typeof n !== 'string' || n.trim() === '' || keys.indexOf(k) >= 0) return false;
      keys.push(k);
      return true;
    });
    const count = {};
    out.forEach(function (r) { const n = squash(nameOf(r)); count[n] = (count[n] || 0) + 1; });
    return out.map(function (r) {
      const n = nameOf(r);
      return Object.assign({}, r, { _n: count[squash(n)] > 1 ? n + ' ' + tag(r) : n });
    });
  }

  function paletteLists(cat) {
    const c = plainObject(cat) ? cat : {};
    const g = plainObject(c.grind) ? c.grind : null;
    const byId = function (r) { return '(' + r.id + ')'; };
    const id = function (r) { return String(r.id); };
    const today = (listOf(c.today, 'items') || []).filter(function (r) { return typeof r.id === 'string' && SLUG.test(r.id); });
    const tabs = (Array.isArray(c.tabs) ? c.tabs : []).filter(function (t) {
      return plainObject(t) && typeof t.id === 'string' && /^[a-z0-9-]+$/.test(t.id) && typeof t.title === 'string';
    });
    // Items: an enhancement level (sid > 0) is typed as "name [sid]".
    const items = searchRows({ items: Array.isArray(c.items) ? c.items : [] }).map(function (r) {
      return Object.assign({}, r, { tname: r.name + (r.sid ? ' [' + r.sid + ']' : '') });
    });
    return {
      today: uniqueNamed(today, function (r) { return r.title; }, id, byId),
      todayLoaded: listOf(c.today, 'items') !== null,
      spots: uniqueNamed(listOf(g, 'spots'), function (r) { return r.name; }, function (r) { return String(spotRefOf(r)); },
        function (r) { return '(' + spotRefOf(r) + ')'; }),
      buffs: uniqueNamed(buffRows(g ? g.buffs : [], 0, 0), function (r) { return r.name; },
        function (r) { return r.name.toLowerCase(); }, byId),
      grind: g,
      items: uniqueNamed(items, function (r) { return r.tname; }, function (r) { return r.id + ':' + r.sid; },
        function (r) { return '#' + r.id; }),
      tabs: uniqueNamed(tabs, function (r) { return r.title; }, id, byId)
    };
  }

  function pName(r) { return r._n; }

  // Text -> {cmd, arg} (arg '' when only the verb), or null for no verb.
  function paletteVerb(text) {
    const t = squash(text);
    const cmds = PALETTE_COMMANDS.slice().sort(function (a, b) { return b.verb.length - a.verb.length; });
    for (let i = 0; i < cmds.length; i++) {
      const v = cmds[i].verb;
      if (t === v) return { cmd: cmds[i], arg: '' };
      if (t.indexOf(v + ' ') === 0) return { cmd: cmds[i], arg: t.slice(v.length + 1) };
    }
    return null;
  }

  function spotRefOf(s) { return typeof s.id === 'string' && validName(s.id) ? s.id : s.name; }

  // arm arg -> {name, minutes} where a trailing duration token is the minutes,
  // unless the whole arg equals or starts a buff's name ("tier 2" names Tier 2
  // Elixir, it is not Tier for 2m).
  function armArgs(arg, buffs) {
    const whole = squash(arg);
    if (buffs.some(function (b) { return fuzzyScore(whole, squash(b._n)) >= 3; })) return { name: arg, minutes: null };
    const toks = arg.split(' ').filter(Boolean);
    if (toks.length > 1 && parseDuration(toks[toks.length - 1]) !== null) {
      return { name: toks.slice(0, -1).join(' '), minutes: toks[toks.length - 1] };
    }
    return { name: toks.join(' '), minutes: null };
  }

  function parseCommand(text, catalog) {
    if (typeof text !== 'string' || !text.trim()) return { ok: false, error: 'type a command, or / to search' };
    if (text.length > PALETTE_MAX) return { ok: false, error: 'too long (max ' + PALETTE_MAX + ' characters)' };
    const v = paletteVerb(text);
    if (!v) {
      return { ok: false, error: 'unknown command - try ' + PALETTE_COMMANDS.map(function (c) { return c.verb; }).join(', ') };
    }
    const L = paletteLists(catalog);
    const verb = v.cmd.verb;
    const arg = v.arg;
    const usage = 'usage: ' + v.cmd.usage;
    const write = function (body, label) {
      return validPost(v.cmd.route, body) ? { ok: true, route: v.cmd.route, body: body, label: label }
        : { ok: false, error: 'not a valid ' + verb + ' - ' + usage };
    };
    const fail = function (e) { return { ok: false, error: e }; };
    const running = L.grind && plainObject(L.grind.active);
    if (verb === 'tick') {
      if (!arg) return fail(usage);
      if (!L.todayLoaded) return fail('no Today items loaded yet');
      const p = fuzzyPick(arg, L.today, pName, 'Today item');
      return p.error ? fail(p.error) : write({ tick: p.row.id }, 'tick ' + p.row.title);
    }
    if (verb === 'arm') {
      if (!arg) return fail(usage);
      const a = armArgs(arg, L.buffs);
      if (!a.name) return fail('name a buff - ' + usage);
      const p = fuzzyPick(a.name, L.buffs, pName, 'buff');
      if (p.error) return fail(p.error);
      const r = parseGrindForm('buff', { name: p.row.name, minutes: a.minutes === null ? String(p.row.minutes) : a.minutes });
      if (!r.ok) return fail(r.error);
      return write(r.body, 'arm ' + p.row.name + ' for ' + fmtDurationShort(r.body.buff.minutes));
    }
    if (verb === 'start grind') {
      if (!arg) return fail(usage);
      if (!L.spots.length) return fail('no spots loaded yet - add one on the Grind tab');
      if (running) return fail('a grind is already running - stop grind first');
      const p = fuzzyPick(arg, L.spots, pName, 'spot');
      return p.error ? fail(p.error) : write({ start: spotRefOf(p.row) }, 'start grind at ' + p.row.name);
    }
    if (verb === 'stop grind') {
      if (L.grind && !running) return fail('no grind session running');
      if (arg.indexOf(' ') >= 0 && parseSilver(arg) === null) return fail(usage);
      const r = parseGrindForm('stop', { silver: arg, trash: '' });
      if (!r.ok) return fail(r.error);
      return write(r.body, 'stop grind, ' + fmtSilver(r.body.stop.silver) + ' silver');
    }
    if (verb === 'log xp') {
      const toks = arg.split(' ').filter(Boolean);
      if (toks.length !== 2) return fail(usage);
      const r = parseSampleForm({ level: toks[0], pct: toks[1] });
      if (!r.ok) return fail(r.error);
      return write(r.body, 'log XP ' + r.body.sample.level + ' at ' + r.body.sample.pct + '%');
    }
    if (verb === 'watch') {
      if (!arg) return fail(usage);
      if (!L.items.length) return fail('no item matches yet - keep typing the name');
      const p = fuzzyPick(arg, L.items, pName, 'item');
      if (p.error) return fail(p.error);
      const r = parseWatchForm({ id: String(p.row.id), sid: String(p.row.sid) }, 'add');
      return r.ok ? write(r.body, 'watch ' + p.row._n) : fail(r.error);
    }
    // go
    if (!arg) return fail(usage);
    const p = fuzzyPick(arg, L.tabs, pName, 'tab');
    return p.error ? fail(p.error) : { ok: true, tab: p.row.id, label: 'go to ' + p.row.title };
  }

  // Suggestions while typing: matching verbs until one is complete, then the
  // names its argument can take. [{text (fills the input), label}].
  function paletteSuggest(text, catalog, max) {
    if (typeof text !== 'string' || text.length > PALETTE_MAX) return [];
    const n = isInt(max, 1) ? max : 8;
    const v = paletteVerb(text);
    if (!v) {
      const t = squash(text);
      return PALETTE_COMMANDS.filter(function (c) { return c.verb.indexOf(t) === 0; }).slice(0, n)
        .map(function (c) { return { text: c.verb + ' ', label: c.usage }; });
    }
    const L = paletteLists(catalog);
    const verb = v.cmd.verb;
    const names = function (rows, nameOf, labelOf, q, tail) {
      const ranked = q ? fuzzyRank(q, rows, nameOf) : rows.map(function (r) { return { r: r }; });
      return ranked.slice(0, n).map(function (x) {
        return { text: verb + ' ' + nameOf(x.r) + (tail || ''), label: labelOf ? labelOf(x.r) : nameOf(x.r) };
      });
    };
    if (verb === 'arm') {
      const a = armArgs(v.arg, L.buffs);
      return names(L.buffs, pName, function (r) { return r.name + ' - ' + fmtDurationShort(r.minutes); },
        a.name, a.minutes === null ? '' : ' ' + a.minutes);
    }
    if (verb === 'tick') return names(L.today, pName, null, v.arg);
    if (verb === 'start grind') return names(L.spots, pName, null, v.arg);
    if (verb === 'watch') return names(L.items, pName, null, v.arg);
    if (verb === 'go') return names(L.tabs, pName, null, v.arg);
    return [{ text: squash(text), label: v.cmd.usage }];
  }

  function paletteMode(text) {
    const t = typeof text === 'string' ? text.trim() : '';
    if (t.charAt(0) === '/') return { mode: 'search', query: t.slice(1).trim() };
    return { mode: 'command', text: t };
  }

  // Search mode index: [{kind, label, tab, find}], `find` is the row text the
  // dashboard looks for on that tab. Today items, events (coupons by code),
  // market items (the plan 028 index rows), Deadeye notes (one entry per
  // line), enhancement steps and grind spots.
  function paletteIndex(catalog) {
    const c = plainObject(catalog) ? catalog : {};
    const out = [];
    const str = function (s) { return typeof s === 'string' && s.trim() !== ''; };
    (listOf(c.today, 'items') || []).forEach(function (r) {
      if (str(r.title)) out.push({ kind: 'today', label: r.title, tab: 'today', find: r.title });
    });
    (listOf(c.events, 'items') || []).forEach(function (r) {
      if (!str(r.title)) return;
      if (r.kind === 'coupon' && str(r.code)) out.push({ kind: 'coupon', label: r.code + ' - ' + r.title, tab: 'events', find: r.code });
      else out.push({ kind: 'event', label: r.title, tab: 'events', find: r.title });
    });
    searchRows({ items: Array.isArray(c.items) ? c.items : [] }).forEach(function (r) {
      out.push({ kind: 'item', label: r.label, tab: 'market', find: r.name });
    });
    (listOf(c.deadeye, 'sections') || []).forEach(function (s) {
      if (!str(s.title) || typeof s.text !== 'string') return;
      s.text.split('\n').map(function (l) { return l.trim(); }).filter(Boolean).forEach(function (l) {
        out.push({ kind: 'note', label: s.title + ': ' + l, tab: 'deadeye', find: l });
      });
    });
    (listOf(c.deadeye, 'plan') || []).forEach(function (r) {
      if (!str(r.item)) return;
      const lv = str(r.current) && str(r.target) ? ' ' + r.current + ' -> ' + r.target : '';
      out.push({ kind: 'step', label: r.item + lv, tab: 'deadeye', find: r.item });
    });
    (listOf(c.grind, 'spots') || []).forEach(function (r) {
      if (str(r.name)) out.push({ kind: 'spot', label: r.name, tab: 'grind', find: r.name });
    });
    return out;
  }

  function paletteSearch(query, index, max) {
    const q = typeof query === 'string' ? query.trim() : '';
    if (!q || q.length > PALETTE_MAX || !Array.isArray(index)) return [];
    return fuzzyRank(q, index.filter(plainObject), function (e) { return e.label; })
      .slice(0, isInt(max, 1) ? max : 8).map(function (x) { return x.r; });
  }

  // Ctrl+K and nothing else (the dashboard window's own keydown).
  function paletteKey(ev) {
    return !!ev && ev.ctrlKey === true && !ev.altKey && !ev.shiftKey && !ev.metaKey &&
      typeof ev.key === 'string' && ev.key.toLowerCase() === 'k';
  }

  Object.assign(K, { IMP_TYPES: IMP_TYPES, IMP_BOX_ID: IMP_BOX_ID, IMP_NAME_MAX: IMP_NAME_MAX,
    IMP_ITEMS_MAX: IMP_ITEMS_MAX, IMP_ITEM_ID: IMP_ITEM_ID, IMP_QTY: IMP_QTY, IMP_CP: IMP_CP,
    IMP_COUNT_MAX: IMP_COUNT_MAX, IMP_MASTERY_MAX: IMP_MASTERY_MAX, impTitle: impTitle,
    validImpItems: validImpItems, validImperialBody: validImperialBody,
    imperialRows: imperialRows, imperialReset: imperialReset, imperialCpText: imperialCpText,
    imperialBest: imperialBest, imperialUnpriced: imperialUnpriced,
    parseImpItems: parseImpItems, parseImperialBox: parseImperialBox,
    parseImperialCp: parseImperialCp, POST_VALIDATORS: POST_VALIDATORS,
    POST_ROUTES: POST_ROUTES, validPost: validPost, PALETTE_COMMANDS: PALETTE_COMMANDS,
    PALETTE_ROUTES: PALETTE_ROUTES, PALETTE_MAX: PALETTE_MAX, paletteTitle: paletteTitle,
    PORTRAIT_HINT: PORTRAIT_HINT, classKey: classKey, classOfTab: classOfTab,
    tabOfClass: tabOfClass, portraitChip: portraitChip, cardCount: cardCount,
    portraitCard: portraitCard, PORTRAIT_ID_RE: PORTRAIT_ID_RE, SHOT_ID_RE: SHOT_ID_RE,
    portraitId: portraitId, portraitSrc: portraitSrc, galleryAlt: galleryAlt,
    portraitGallery: portraitGallery, galleryMove: galleryMove,
    PORTRAIT_CLS_RE: PORTRAIT_CLS_RE, validPortraitsBody: validPortraitsBody, squash: squash,
    fuzzyScore: fuzzyScore, fuzzyRank: fuzzyRank, fuzzyPick: fuzzyPick, listOf: listOf,
    uniqueNamed: uniqueNamed, paletteLists: paletteLists, pName: pName,
    paletteVerb: paletteVerb, spotRefOf: spotRefOf, armArgs: armArgs,
    parseCommand: parseCommand, paletteSuggest: paletteSuggest, paletteMode: paletteMode,
    paletteIndex: paletteIndex, paletteSearch: paletteSearch, paletteKey: paletteKey });
  return function link() {};
});
