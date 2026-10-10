/* EW shared pure logic, part `deadeye` (plan 107 split of ewcore.js):
    Deadeye gear, enhancement EV, stacks, shopping list, calculators.
   Installed in order by ../ewcore.js, which re-exports the public names as
   EWCore. Loaded as a plain script before ewcore.js (window.EWCoreParts) and by
   require() under node --test. No DOM, no Electron. */
(function (root, install) {
  if (typeof module !== 'undefined' && module.exports) module.exports = install;
  else (root.EWCoreParts = root.EWCoreParts || {}).deadeye = install;
})(typeof self !== 'undefined' ? self : this, function (K) {
  'use strict';

  // from earlier parts
  const { NAME_MAX, exact, fmtSilver, fmtSilverExact, isInt, isNum, onlyKeys, parseDuration,
    parseSilver, plainObject, validName, validRef } = K;

  // ---- Deadeye (plan 007) ----
  // Operator build notes (markdown, rendered as a safe subset) and an ordered
  // enhancement plan. Text only: nothing here is executed or sent to the game.

  const DEADEYE_LEVELS = [];
  for (let i = 0; i <= 15; i++) DEADEYE_LEVELS.push('+' + i);
  DEADEYE_LEVELS.push('PRI', 'DUO', 'TRI', 'TET', 'PEN');
  const DEADEYE_SECTIONS = ['addons', 'crystals', 'artifacts', 'lightstones', 'rotation', 'misc'];
  const NOTE_MAX = 20000;            // server: note text 0-20000 chars after \r\n -> \n
  const STEP_NOTE_MAX = 200;
  const STEP_ID_RE = /^d[0-9]{1,9}$/;

  function levelIndex(v) { return typeof v === 'string' ? DEADEYE_LEVELS.indexOf(v) : -1; }

  function escHtml(s) {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  // Inline markup on already-escaped text: `code` spans are left literal,
  // then **bold**, then *em* (not inside words, not around spaces).
  function mdInline(s) {
    return s.split(/(`[^`\n]+`)/).map(function (part, i) {
      if (i % 2) return '<code>' + part.slice(1, -1) + '</code>';
      return part.replace(/\*\*(?=\S)([^*]*?\S)\*\*/g, '<strong>$1</strong>')
        .replace(/(^|[^*\w])\*(?=\S)([^*]*?\S)\*(?![*\w])/g, '$1<em>$2</em>');
    }).join('');
  }

  // Markdown -> HTML, safe subset. ALL of & < > " ' are escaped first; the
  // only markup added afterwards is fixed attribute-less tags (h1-h3, p,
  // ul/ol/li, strong, em, code, pre). No links, images or raw HTML ever.
  function renderMarkdown(text) {
    if (typeof text !== 'string') return '';
    const lines = escHtml(text.replace(/\r\n?/g, '\n')).split('\n');
    const out = [];
    let para = [];
    let list = null;
    const flush = function () {
      if (para.length) out.push('<p>' + mdInline(para.join('\n')) + '</p>');
      para = [];
      if (list) out.push('</' + list + '>');
      list = null;
    };
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      if (/^\s*```/.test(line)) {
        flush();
        const code = [];
        for (i++; i < lines.length && !/^\s*```\s*$/.test(lines[i]); i++) code.push(lines[i]);
        out.push('<pre><code>' + code.join('\n') + '</code></pre>');
        continue;
      }
      if (/^\s*$/.test(line)) { flush(); continue; }
      const h = /^(#{1,3})[ \t]+(\S.*)$/.exec(line);
      if (h) {
        flush();
        out.push('<h' + h[1].length + '>' + mdInline(h[2].trim()) + '</h' + h[1].length + '>');
        continue;
      }
      const ul = /^\s*[-*][ \t]+(.*)$/.exec(line);
      const ol = ul ? null : /^\s*\d{1,9}\.[ \t]+(.*)$/.exec(line);
      if (ul || ol) {
        const kind = ul ? 'ul' : 'ol';
        if (para.length || list !== kind) { flush(); out.push('<' + kind + '>'); list = kind; }
        out.push('<li>' + mdInline((ul || ol)[1].trim()) + '</li>');
        continue;
      }
      if (list) flush();
      para.push(line.trim());
    }
    flush();
    return out.join('');
  }

  function plainLine(v, max) {
    return typeof v === 'string' && v.length <= max && !/[\u0000-\u001f\u007f]/.test(v);
  }

  function validNoteText(v) {
    return typeof v === 'string' && v.replace(/\r\n/g, '\n').length <= NOTE_MAX &&
      !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(v);
  }

  const STEP_CHECKS = {
    item: validName, current: function (v) { return levelIndex(v) >= 0; },
    target: function (v) { return levelIndex(v) >= 0; },
    note: function (v) { return plainLine(v, STEP_NOTE_MAX); }
  };

  function stepFieldsOk(o) {
    for (const k of Object.keys(STEP_CHECKS)) {
      if (k in o && !STEP_CHECKS[k](o[k])) return false;
    }
    return !('current' in o && 'target' in o) || levelIndex(o.target) > levelIndex(o.current);
  }

  // Exact shape check for POST /api/deadeye bodies (main-process IPC guard).
  // What the client cannot know (an edit's order against the stored level,
  // unknown ids, the 100-step cap) is the server's to refuse.
  function validDeadeyeBody(body) {
    if (!plainObject(body)) return false;
    const keys = Object.keys(body);
    if (keys.length !== 1) return false;
    const k = keys[0];
    const v = body[k];
    if (k === 'note') {
      return exact(v, ['section', 'text']) && DEADEYE_SECTIONS.indexOf(v.section) >= 0 && validNoteText(v.text);
    }
    if (k === 'delete_step') return validRef(v, STEP_ID_RE);
    if (k === 'step_done') return exact(v, ['id', 'done']) && validRef(v.id, STEP_ID_RE) && typeof v.done === 'boolean';
    if (k === 'move_step') return exact(v, ['id', 'dir']) && validRef(v.id, STEP_ID_RE) && (v.dir === -1 || v.dir === 1);
    if (k === 'shop_set') return validShopSet(v);
    if (k === 'shop_step') return validShopStep(v);
    if (k === 'add_step') {
      return plainObject(v) && onlyKeys(v, ['item', 'current', 'target', 'note']) &&
        'item' in v && 'current' in v && 'target' in v && stepFieldsOk(v);
    }
    if (k === 'edit_step') {
      return plainObject(v) && onlyKeys(v, ['id', 'item', 'current', 'target', 'note']) &&
        validRef(v.id, STEP_ID_RE) && Object.keys(v).length >= 2 && stepFieldsOk(v);
    }
    const st = validStacksOp(k, v); // plan 036
    return st === null ? false : st;
  }

  // Add-step form strings -> {add_step} body, or an error for the operator.
  function parseStepForm(form) {
    const f = form || {};
    const s = function (k) { return typeof f[k] === 'string' ? f[k].trim() : ''; };
    const item = s('item');
    if (!validName(item)) return { ok: false, error: 'item: 1-' + NAME_MAX + ' plain characters' };
    const cur = levelIndex(f.current);
    const tgt = levelIndex(f.target);
    if (cur < 0 || tgt < 0) return { ok: false, error: 'pick a level for current and target' };
    if (tgt <= cur) return { ok: false, error: 'target must be above current' };
    const add = { item: item, current: f.current, target: f.target };
    if (s('note')) {
      if (!plainLine(s('note'), STEP_NOTE_MAX)) return { ok: false, error: 'note: up to ' + STEP_NOTE_MAX + ' plain characters' };
      add.note = s('note');
    }
    return { ok: true, body: { add_step: add } };
  }

  // ---- Enhancement EV (plan 035) ----
  // GET /api/deadeye/enhance?family=&step=&fs=&crons=0|1 -> {chance_pct, approx,
  // attempts_mean, attempts_p90, pity_cap, crons_mean, cost_mean_silver, ...}.
  // Math on sourced tables and cached prices only.

  const ENHANCE_MAX_FS = 999;

  // Typed FS -> int 0..999, or null.
  function parseFs(text) {
    const s = typeof text === 'string' ? text.trim() : (isNum(text) ? String(text) : '');
    if (!/^[0-9]{1,3}$/.test(s)) return null;
    const n = Number(s);
    return n <= ENHANCE_MAX_FS ? n : null;
  }

  // Levels a plan 007 step climbs through: (current, target], each the level
  // one attempt reaches (the server's `step` key).
  function enhanceSubSteps(current, target) {
    const a = levelIndex(current);
    const b = levelIndex(target);
    return a >= 0 && b > a ? DEADEYE_LEVELS.slice(a + 1, b + 1) : [];
  }

  // A gear family named inside the operator's item text, else null.
  function enhanceFamilyGuess(item, families) {
    if (typeof item !== 'string' || !Array.isArray(families)) return null;
    const low = item.toLowerCase();
    for (const f of families) {
      if (typeof f === 'string' && f && low.indexOf(f.replace(/_/g, ' ')) >= 0) return f;
    }
    return null;
  }

  function enhancePath(family, step, fs, crons) {
    if (typeof family !== 'string' || !/^[a-z][a-z0-9_]{0,23}$/.test(family)) return null;
    if (typeof step !== 'string' || !step || parseFs(fs) === null) return null;
    return '/api/deadeye/enhance?family=' + encodeURIComponent(family) + '&step=' +
      encodeURIComponent(step) + '&fs=' + parseFs(fs) + '&crons=' + (crons ? 1 : 0);
  }

  function fmtAttempts(n) {
    if (!isNum(n)) return '-';
    return n < 100 ? n.toFixed(1) : fmtSilver(n);
  }

  // One EV reply -> display strings; any missing number shows '-'.
  function fmtEv(b) {
    const o = b && typeof b === 'object' ? b : {};
    const pct = isNum(o.chance_pct) ? (o.approx ? '~' : '') + o.chance_pct.toFixed(2) + '%' : '-';
    return {
      step: typeof o.step === 'string' ? o.step : '-',
      chance: pct,
      attempts: fmtAttempts(o.attempts_mean),
      p90: isNum(o.attempts_p90) ? String(o.attempts_p90) : '-',
      pity: isNum(o.pity_cap) ? String(o.pity_cap) : '-',
      crons: isNum(o.crons_mean) && o.crons_mean > 0 ? fmtSilver(o.crons_mean) : '-',
      silver: isNum(o.cost_mean_silver) ? fmtSilver(o.cost_mean_silver) : '-',
      note: typeof o.cost_note === 'string' ? o.cost_note : '',
      // unverified_used: preview fields this result rests on (a crons-off result
      // ignores a preview cron count); older replies fall back to `verified`.
      unverified: Array.isArray(o.unverified_used) ? o.unverified_used.length > 0 : o.verified === false
    };
  }

  function evLine(b) {
    const f = fmtEv(b);
    return f.step + '  ' + f.chance + '  ' + f.attempts + ' tries (p90 ' + f.p90 + ', pity ' + f.pity +
      ')  crons ' + f.crons + '  silver ' + f.silver + (f.unverified ? '  [unverified]' : '');
  }

  // ---- Stacks: failstack bank, Agris pity, cron budget (plan 036) ----
  // GET /api/deadeye `stacks` block; writes are POST /api/deadeye fs_add /
  // fs_use / agris_set / crons_set. Operator-typed inventory only.

  const FS_KINDS = ['advice', 'saved', 'cry'];
  const FS_KIND_LABELS = { advice: 'Advice of Valks', saved: 'saved stack', cry: "Valks' Cry" };
  const FS_COUNT_MAX = 999;
  const AGRIS_STACKS_MAX = 1000;
  const CRONS_MAX = 1e9;
  const CRONS_WEEKLY_MAX = 1e7;
  const ENHANCE_STEPS = [];
  for (let i = 1; i <= 15; i++) ENHANCE_STEPS.push('+' + i);
  ENHANCE_STEPS.push('PRI', 'DUO', 'TRI', 'TET', 'PEN', 'HEX', 'SEP', 'OCT', 'NOV', 'DEC');
  const GEAR_FAMILY_RE = /^[a-z][a-z0-9_]{0,23}$/;

  function intIn(v, lo, hi) { return isInt(v, lo) && v <= hi; }

  function validFsArg(v) {
    return plainObject(v) && onlyKeys(v, ['kind', 'value', 'count']) && 'kind' in v && 'value' in v &&
      FS_KINDS.indexOf(v.kind) >= 0 && intIn(v.value, 1, ENHANCE_MAX_FS) &&
      (!('count' in v) || intIn(v.count, 1, FS_COUNT_MAX));
  }

  // Shape check for the plan 036 ops inside validDeadeyeBody; null = not a stacks op.
  function validStacksOp(k, v) {
    if (k === 'fs_add' || k === 'fs_use') return validFsArg(v);
    if (k === 'agris_set') {
      return exact(v, ['family', 'step', 'stacks']) && typeof v.family === 'string' && GEAR_FAMILY_RE.test(v.family) &&
        ENHANCE_STEPS.indexOf(v.step) >= 0 && intIn(v.stacks, 0, AGRIS_STACKS_MAX);
    }
    if (k === 'crons_set') {
      return plainObject(v) && onlyKeys(v, ['owned', 'weekly_income']) && Object.keys(v).length >= 1 &&
        (!('owned' in v) || intIn(v.owned, 0, CRONS_MAX)) &&
        (!('weekly_income' in v) || intIn(v.weekly_income, 0, CRONS_WEEKLY_MAX));
    }
    return null;
  }

  // Typed whole number lo..hi -> int, or null.
  function parseWhole(text, lo, hi) {
    const s = typeof text === 'string' ? text.trim().replace(/,/g, '') : (isNum(text) ? String(text) : '');
    if (!/^[0-9]{1,10}$/.test(s)) return null;
    const n = Number(s);
    return n >= lo && n <= hi ? n : null;
  }

  // Bank form {kind, value, count} strings -> {fs_add|fs_use: ...}; `use` picks the op.
  function parseFsForm(form, use) {
    const f = form || {};
    if (FS_KINDS.indexOf(f.kind) < 0) return { ok: false, error: 'pick a stack kind' };
    const value = parseWhole(f.value, 1, ENHANCE_MAX_FS);
    if (value === null) return { ok: false, error: 'FS: a whole number 1-' + ENHANCE_MAX_FS };
    const blank = typeof f.count !== 'string' || !f.count.trim();
    const count = blank ? 1 : parseWhole(f.count, 1, FS_COUNT_MAX);
    if (count === null) return { ok: false, error: 'count: a whole number 1-' + FS_COUNT_MAX };
    const arg = { kind: f.kind, value: value };
    if (count !== 1) arg.count = count;
    const body = {};
    body[use ? 'fs_use' : 'fs_add'] = arg;
    return { ok: true, body: body };
  }

  function parseAgrisForm(form) {
    const f = form || {};
    if (typeof f.family !== 'string' || !GEAR_FAMILY_RE.test(f.family)) return { ok: false, error: 'pick a gear family' };
    if (ENHANCE_STEPS.indexOf(f.step) < 0) return { ok: false, error: 'pick a level' };
    const stacks = parseWhole(f.stacks, 0, AGRIS_STACKS_MAX);
    if (stacks === null) return { ok: false, error: 'stacks: a whole number 0-' + AGRIS_STACKS_MAX };
    return { ok: true, body: { agris_set: { family: f.family, step: f.step, stacks: stacks } } };
  }

  // Blank fields are left out; at least one is needed.
  function parseCronsForm(form) {
    const f = form || {};
    const arg = {};
    const fields = [['owned', CRONS_MAX], ['weekly_income', CRONS_WEEKLY_MAX]];
    for (const pair of fields) {
      const raw = f[pair[0]];
      if (typeof raw !== 'string' || !raw.trim()) continue;
      const n = parseWhole(raw, 0, pair[1]);
      if (n === null) return { ok: false, error: pair[0].replace('_', ' ') + ': a whole number 0-' + fmtSilverExact(pair[1]) };
      arg[pair[0]] = n;
    }
    if (!Object.keys(arg).length) return { ok: false, error: 'type crons owned and/or weekly income' };
    return { ok: true, body: { crons_set: arg } };
  }

  function fmtCrons(n) { return isNum(n) ? fmtSilverExact(Math.ceil(n - 1e-9)) : '-'; }

  function fsKind(k) { return FS_KIND_LABELS[k] || String(k); }

  function pityText(fails) {
    if (!isNum(fails)) return 'no Agris threshold';
    return fails === 0 ? 'next attempt guaranteed' : 'guaranteed in ' + fails + (fails === 1 ? ' fail' : ' fails');
  }

  // stacks block -> display strings (never HTML).
  function fmtStacks(s) {
    const o = plainObject(s) ? s : {};
    const bank = (Array.isArray(o.fs_bank) ? o.fs_bank : []).filter(plainObject).map(function (r) {
      return { kind: r.kind, value: r.value, text: fsKind(r.kind) + ' ' + r.value + ' x' + r.count };
    });
    const agris = (Array.isArray(o.agris) ? o.agris : []).filter(plainObject).map(function (r) {
      return { family: r.family, step: r.step, text: r.family + ' ' + r.step + ': ' + r.stacks + ' stacks, ' + pityText(r.fails_to_guarantee) };
    });
    const a = plainObject(o.advice) ? o.advice : {};
    let advice;
    if (typeof a.level !== 'string') {
      advice = typeof a.reason === 'string' ? a.reason : '-';
    } else {
      const head = (typeof a.item === 'string' ? a.item + ' ' : '') + '-> ' + a.level +
        (isNum(a.softcap_fs) ? ' (soft cap ' + a.softcap_fs + ')' : '');
      const pick = plainObject(a.suggest) ? 'use ' + fsKind(a.suggest.kind) + ' ' + a.suggest.value :
        (typeof a.reason === 'string' ? a.reason : '-');
      const pity = plainObject(a.agris) && isNum(a.agris.threshold) ? '; Agris ' + pityText(a.agris.fails_to_guarantee) : '';
      advice = head + ': ' + pick + pity;
    }
    const b = plainObject(o.budget) ? o.budget : {};
    let budget = 'crons ' + fmtCrons(b.owned) + ' owned / ' + fmtCrons(b.needed) + ' expected';
    if (isNum(b.gap) && b.gap > 0) {
      budget += ', short ' + fmtCrons(b.gap) + (isNum(b.weeks) ? ' (~' + b.weeks + (b.weeks === 1 ? ' week)' : ' weeks)') : ' (no weekly income set)');
    } else if (isNum(b.needed)) {
      budget += ', covered';
    }
    const unk = (Array.isArray(b.unknown) ? b.unknown : []).filter(plainObject).map(function (u) { return u.family + ' ' + u.level; });
    return { bank: bank, agris: agris, advice: advice, budget: budget,
      unknown: unk.length ? 'no chance data (not counted): ' + unk.join(', ') : '' };
  }

  // ---- Shopping list (plan 037) ----
  // GET /api/deadeye/shopping -> {lines: [{id, name, qty, expected, unit, total,
  // preorder, watched, note}], total, priced_total, missing_prices,
  // can_afford_by, afford: {need, silver_per_h, hours_per_day, per_day, days,
  // reason}, steps, families, settings: {silver_on_hand, hours_per_day}}.
  // Cached read-only prices; "watch" only edits EW's own watchlist.

  const SHOP_MAX_SILVER = 1e15;
  const SHOP_MAX_HOURS = 24;
  // GEAR_FAMILY_RE: shared with the plan 036 stacks block above.

  function validShopSet(v) {
    if (!plainObject(v) || !Object.keys(v).length || !onlyKeys(v, ['silver_on_hand', 'hours_per_day'])) return false;
    if ('silver_on_hand' in v && !(isInt(v.silver_on_hand, 0) && v.silver_on_hand <= SHOP_MAX_SILVER)) return false;
    return !('hours_per_day' in v) || (isNum(v.hours_per_day) && v.hours_per_day > 0 && v.hours_per_day <= SHOP_MAX_HOURS);
  }

  function validShopStep(v) {
    if (!plainObject(v) || !onlyKeys(v, ['id', 'family', 'fs', 'crons']) || Object.keys(v).length < 2) return false;
    if (!validRef(v.id, STEP_ID_RE)) return false;
    if ('family' in v && v.family !== null && !(typeof v.family === 'string' && GEAR_FAMILY_RE.test(v.family))) return false;
    if ('fs' in v && !(isInt(v.fs, 0) && v.fs <= ENHANCE_MAX_FS)) return false;
    return !('crons' in v) || typeof v.crons === 'boolean';
  }

  // Settings form strings -> {shop_set} body, or an error for the operator.
  // Silver takes quick entry (plan 048: 1.2b, 1,234,567); hours a decimal or
  // a duration (2h30m).
  function parseShopForm(form) {
    const f = form || {};
    const silver = String(f.silver === undefined || f.silver === null ? '' : f.silver).trim();
    const hours = String(f.hours === undefined || f.hours === null ? '' : f.hours).trim();
    const body = {};
    if (silver !== '') {
      const n = parseSilver(silver);
      if (n === null || n > SHOP_MAX_SILVER) {
        return { ok: false, error: 'silver on hand: 0-' + fmtSilver(SHOP_MAX_SILVER) + ', e.g. 1.2b or 1,234,567' };
      }
      body.silver_on_hand = n;
    }
    if (hours !== '') {
      const dur = /[a-z]/i.test(hours) ? parseDuration(hours) : null;
      const h = dur !== null ? Math.round(dur / 0.6) / 100 : (/^[0-9]{1,2}(\.[0-9]{1,2})?$/.test(hours) ? Number(hours) : NaN);
      if (!(h > 0 && h <= SHOP_MAX_HOURS)) return { ok: false, error: 'hours per day: a number above 0, up to 24' };
      body.hours_per_day = h;
    }
    if (!Object.keys(body).length) return { ok: false, error: 'enter silver on hand or hours per day' };
    return { ok: true, body: { shop_set: body } };
  }

  // One shopping line -> display strings; any missing number shows '-'.
  function fmtShopLine(ln) {
    const o = plainObject(ln) ? ln : {};
    const pre = { capped: 'pre-order (capped)', no_stock: 'pre-order (no stock)' };
    return {
      name: typeof o.name === 'string' && o.name ? o.name : (isInt(o.id, 0) ? '#' + o.id : '-'),
      qty: isInt(o.qty, 0) ? fmtSilver(o.qty) : '-',
      unit: isNum(o.unit) ? fmtSilver(o.unit) : '-',
      total: isNum(o.total) ? fmtSilver(o.total) : '-',
      preorder: pre[o.preorder] || '',
      note: typeof o.note === 'string' ? o.note : ''
    };
  }

  // The list's summary -> {total, afford} strings.
  function fmtShopping(b) {
    const o = plainObject(b) ? b : {};
    const a = plainObject(o.afford) ? o.afford : {};
    const missing = Array.isArray(o.missing_prices) ? o.missing_prices.length : 0;
    let total = isNum(o.total) ? fmtSilver(o.total) : '-';
    if (!isNum(o.total) && missing) {
      total = (isNum(o.priced_total) && o.priced_total > 0 ? '>= ' + fmtSilver(o.priced_total) + ' ' : '') +
        '(' + missing + ' unpriced)';
    }
    let afford;
    if (typeof o.can_afford_by === 'string' && a.days === 0) afford = 'covered by silver on hand';
    else if (typeof o.can_afford_by === 'string') {
      afford = 'can afford by ' + o.can_afford_by + ' (' + a.days + ' d at ' + fmtSilver(a.silver_per_h) +
        '/h x ' + a.hours_per_day + ' h/day)';
    } else afford = 'can afford by: - ' + (typeof a.reason === 'string' && a.reason ? '(' + a.reason + ')' : '');
    return { total: total, afford: afford.trim() };
  }

  // "watch" button -> plan 002 POST /api/market/watch body (EW's own list only).
  function shopWatchBody(id) {
    return isInt(id, 1) ? { add: { id: id, sid: 0 } } : null;
  }

  // ---- Calculators (plan 055) ----
  // GET /api/deadeye/calc?kind=crystal&on_hand&levels[&per_level] ->
  // {needed, short, weeks: {min, max}, exact, weekly, reset, ...};
  // kind=caphras&slot&from&to[&grade][&price] -> {stones, silver, approx,
  // verified, price_source, ...}. Read-only GET over sourced static data.

  const CALC_SLOT_RE = /^[a-z][a-z0-9_]{0,39}$/;

  function calcInt(s, lo, hi) {
    const t = String(s === undefined || s === null ? '' : s).trim().replace(/,/g, '');
    if (!/^[0-9]{1,9}$/.test(t)) return null;
    const n = Number(t);
    return n >= lo && n <= hi ? n : null;
  }

  // Form strings -> {ok, path} for the calc GET, or {ok: false, error}.
  function calcQuery(kind, form) {
    const f = form || {};
    const blank = function (v) { return v === undefined || v === null || String(v).trim() === ''; };
    if (kind === 'crystal') {
      const onHand = calcInt(f.on_hand || '0', 0, 1e7);
      if (onHand === null) return { ok: false, error: 'crystals on hand: 0-10,000,000' };
      const levels = calcInt(f.levels, 1, 20);
      if (levels === null) return { ok: false, error: 'reform levels: 1-20' };
      let q = 'kind=crystal&on_hand=' + onHand + '&levels=' + levels;
      if (!blank(f.per_level)) {
        const per = calcInt(f.per_level, 1, 1e5);
        if (per === null) return { ok: false, error: 'crystals per level: 1-100,000' };
        q += '&per_level=' + per;
      }
      return { ok: true, path: '/api/deadeye/calc?' + q };
    }
    if (kind === 'caphras') {
      if (!(typeof f.slot === 'string' && CALC_SLOT_RE.test(f.slot))) return { ok: false, error: 'pick a slot' };
      const from = calcInt(f.from || '0', 0, 20);
      const to = calcInt(f.to, 0, 20);
      if (from === null || to === null || to < from) return { ok: false, error: 'Caphras levels: 0 <= from <= to <= 20' };
      let q = 'kind=caphras&slot=' + f.slot + '&from=' + from + '&to=' + to;
      if (!blank(f.grade)) {
        if (!CALC_SLOT_RE.test(String(f.grade))) return { ok: false, error: 'pick a grade' };
        q += '&grade=' + f.grade;
      }
      if (!blank(f.price)) {
        const p = parseSilver(f.price);
        if (p === null || p > 1e12) return { ok: false, error: 'stone price: e.g. 2.1m or 2,100,000' };
        q += '&price=' + p;
      }
      return { ok: true, path: '/api/deadeye/calc?' + q };
    }
    return { ok: false, error: 'unknown calculator' };
  }

  // One calc reply -> {main, sub} display strings.
  function fmtCalc(r) {
    const o = plainObject(r) ? r : {};
    const band = function (b, unit) {
      if (!plainObject(b) || !isInt(b.min, 0) || !isInt(b.max, 0)) return '-';
      return (b.min === b.max ? fmtSilverExact(b.min) : fmtSilverExact(b.min) + '-' + fmtSilverExact(b.max)) + unit;
    };
    if (o.kind === 'crystal') {
      const w = plainObject(o.weeks) ? o.weeks : {};
      const main = w.max === 0 ? 'covered by crystals on hand' : band(o.weeks, ' wk') + ' of Jetina exchanges';
      const sub = 'need ' + band(o.needed, '') + ', short ' + band(o.short, '') + '; ' +
        (isInt(o.weekly, 1) ? o.weekly : '-') + '/wk, ' + (isInt(o.auras_per_week, 1) ? o.auras_per_week : '-') +
        ' auras, resets ' + (typeof o.reset === 'string' ? o.reset : '-') +
        (o.exact === true ? '' : ' (60-120/level band)');
      return { main: main, sub: sub };
    }
    if (o.kind === 'caphras') {
      const stones = isInt(o.stones, 0) ? fmtSilverExact(o.stones) : '-';
      const silver = isNum(o.silver) ? fmtSilver(o.silver) + ' silver' : 'no price (watch the stone or type one)';
      const flags = [];
      if (o.approx === true) flags.push('prorated inside a range');
      if (o.verified === false) flags.push('unverified data');
      if (o.price_source === 'cache') flags.push('cached price');
      return { main: (o.approx === true ? '~' : '') + stones + ' stones, ' + silver,
        sub: 'C' + o.from + ' -> C' + o.to + (flags.length ? ' (' + flags.join(', ') + ')' : '') };
    }
    return { main: '-', sub: '' };
  }

  Object.assign(K, { DEADEYE_LEVELS: DEADEYE_LEVELS, DEADEYE_SECTIONS: DEADEYE_SECTIONS,
    NOTE_MAX: NOTE_MAX, STEP_NOTE_MAX: STEP_NOTE_MAX, STEP_ID_RE: STEP_ID_RE,
    levelIndex: levelIndex, escHtml: escHtml, mdInline: mdInline,
    renderMarkdown: renderMarkdown, plainLine: plainLine, validNoteText: validNoteText,
    STEP_CHECKS: STEP_CHECKS, stepFieldsOk: stepFieldsOk, validDeadeyeBody: validDeadeyeBody,
    parseStepForm: parseStepForm, ENHANCE_MAX_FS: ENHANCE_MAX_FS, parseFs: parseFs,
    enhanceSubSteps: enhanceSubSteps, enhanceFamilyGuess: enhanceFamilyGuess,
    enhancePath: enhancePath, fmtAttempts: fmtAttempts, fmtEv: fmtEv, evLine: evLine,
    FS_KINDS: FS_KINDS, FS_KIND_LABELS: FS_KIND_LABELS, FS_COUNT_MAX: FS_COUNT_MAX,
    AGRIS_STACKS_MAX: AGRIS_STACKS_MAX, CRONS_MAX: CRONS_MAX,
    CRONS_WEEKLY_MAX: CRONS_WEEKLY_MAX, ENHANCE_STEPS: ENHANCE_STEPS,
    GEAR_FAMILY_RE: GEAR_FAMILY_RE, intIn: intIn, validFsArg: validFsArg,
    validStacksOp: validStacksOp, parseWhole: parseWhole, parseFsForm: parseFsForm,
    parseAgrisForm: parseAgrisForm, parseCronsForm: parseCronsForm, fmtCrons: fmtCrons,
    fsKind: fsKind, pityText: pityText, fmtStacks: fmtStacks, SHOP_MAX_SILVER: SHOP_MAX_SILVER,
    SHOP_MAX_HOURS: SHOP_MAX_HOURS, validShopSet: validShopSet, validShopStep: validShopStep,
    parseShopForm: parseShopForm, fmtShopLine: fmtShopLine, fmtShopping: fmtShopping,
    shopWatchBody: shopWatchBody, CALC_SLOT_RE: CALC_SLOT_RE, calcInt: calcInt,
    calcQuery: calcQuery, fmtCalc: fmtCalc });
  return function link() {};
});
