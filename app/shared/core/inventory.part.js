/* EW shared pure logic, part `inventory` (plan 107 split of ewcore.js):
    Pets, inventory / weight / storage + Value Pack, cooking / alchemy margins.
   Installed in order by ../ewcore.js, which re-exports the public names as
   EWCore. Loaded as a plain script before ewcore.js (window.EWCoreParts) and by
   require() under node --test. No DOM, no Electron. */
(function (root, install) {
  if (typeof module !== 'undefined' && module.exports) module.exports = install;
  else (root.EWCoreParts = root.EWCoreParts || {}).inventory = install;
})(typeof self !== 'undefined' ? self : this, function (K) {
  'use strict';

  // from earlier parts
  const { SLUG, derivedText, fmtDuration, fmtSilver, fmtSilverExact, intIn, isId, isInt, isNum,
    onlyKeys, parseSilver, plainObject, validAscii } = K;

  // ---- Pets (plan 043) ----
  // GET /api/pets -> {roster: [{id, name, species, species_name, tier, talents,
  // skill_names, alpha, out, fed_at, fed_ago_s}], species, goals, goal_options,
  // coverage, exchange, alpha_rule, exchange_rule}. The roster is operator-typed;
  // the server enforces 5 out, one alpha, alpha on T5 only.

  const PET_NAME_MAX = 40;
  const PET_TALENTS_MAX = 5;
  const PET_TALENT_MAX = 40;
  const PET_TIER = [1, 5];
  const PET_KEY = /^[a-z][a-z0-9_]{0,39}$/;
  const PET_FIELDS = ['name', 'species', 'tier', 'talents', 'alpha', 'out'];

  function validPetFields(v, required) {
    if (!plainObject(v) || !onlyKeys(v, PET_FIELDS)) return false;
    if (!required.every(function (k) { return k in v; })) return false;
    if ('name' in v && !validAscii(v.name, PET_NAME_MAX)) return false;
    if ('species' in v && !(typeof v.species === 'string' && PET_KEY.test(v.species))) return false;
    if ('tier' in v && !intIn(v.tier, PET_TIER[0], PET_TIER[1])) return false;
    if ('talents' in v && !(Array.isArray(v.talents) && v.talents.length <= PET_TALENTS_MAX &&
      v.talents.every(function (t) { return validAscii(t, PET_TALENT_MAX); }))) return false;
    return ['alpha', 'out'].every(function (k) { return !(k in v) || typeof v[k] === 'boolean'; });
  }

  // POST /api/pets body: exactly one of {add|edit|remove|feed|goals}.
  function validPetsBody(body) {
    if (!plainObject(body)) return false;
    const keys = Object.keys(body);
    if (keys.length !== 1) return false;
    const k = keys[0];
    const v = body[k];
    if (k === 'add') return validPetFields(v, ['name', 'species', 'tier']);
    if (k === 'edit') {
      if (!plainObject(v) || typeof v.id !== 'string' || !SLUG.test(v.id)) return false;
      const rest = Object.assign({}, v);
      delete rest.id;
      return Object.keys(rest).length > 0 && validPetFields(rest, []);
    }
    if (k === 'remove' || k === 'feed') return typeof v === 'string' && SLUG.test(v);
    if (k === 'goals') {
      return Array.isArray(v) && v.length <= 20 && v.every(function (g) { return typeof g === 'string' && PET_KEY.test(g); }) &&
        v.filter(function (g, i) { return v.indexOf(g) === i; }).length === v.length;
    }
    return false;
  }

  // Add / edit form -> {ok, body} or {ok: false, error}. Talents: comma list.
  function parsePetForm(f, editId) {
    const name = String(f && f.name || '').trim();
    if (!validAscii(name, PET_NAME_MAX)) return { ok: false, error: 'name must be 1..' + PET_NAME_MAX + ' ASCII characters' };
    const species = String(f.species || '');
    if (!PET_KEY.test(species)) return { ok: false, error: 'pick a pet type' };
    const tier = /^\d+$/.test(String(f.tier || '').trim()) ? Number(f.tier) : NaN;
    if (!intIn(tier, PET_TIER[0], PET_TIER[1])) return { ok: false, error: 'tier must be 1..5' };
    const talents = String(f.talents || '').split(',').map(function (t) { return t.trim(); })
      .filter(function (t) { return t; });
    if (talents.length > PET_TALENTS_MAX || !talents.every(function (t) { return validAscii(t, PET_TALENT_MAX); })) {
      return { ok: false, error: 'at most ' + PET_TALENTS_MAX + ' talents, each 1..' + PET_TALENT_MAX + ' ASCII characters' };
    }
    const alpha = f.alpha === true;
    if (alpha && tier !== PET_TIER[1]) return { ok: false, error: 'alpha needs a T5 pet' };
    const fields = { name: name, species: species, tier: tier, talents: talents, out: f.out === true, alpha: alpha };
    if (typeof editId === 'string') return { ok: true, body: { edit: Object.assign({ id: editId }, fields) } };
    return { ok: true, body: { add: fields } };
  }

  function strList(a) {
    return Array.isArray(a) ? a.filter(function (x) { return typeof x === 'string'; }) : [];
  }

  // Roster rows -> display rows; junk dropped.
  function petRows(view) {
    const r = plainObject(view) && Array.isArray(view.roster) ? view.roster : [];
    return r.filter(function (p) {
      return plainObject(p) && typeof p.id === 'string' && SLUG.test(p.id) && typeof p.name === 'string' &&
        intIn(p.tier, PET_TIER[0], PET_TIER[1]);
    }).map(function (p) {
      const skills = strList(p.skill_names);
      const talents = strList(p.talents);
      return { id: p.id, name: p.name, species: typeof p.species === 'string' ? p.species : '',
        label: (typeof p.species_name === 'string' ? p.species_name : String(p.species || '?')) + ' T' + p.tier +
          (p.alpha === true ? ' Alpha' : ''),
        skills: skills.length ? skills.join(', ') : 'no special skill',
        talents: talents.join(', '), talentList: talents, tier: p.tier,
        out: p.out === true, alpha: p.alpha === true,
        fed: isNum(p.fed_ago_s) ? 'fed ' + fmtDuration(p.fed_ago_s * 1000) + ' ago' : 'not fed yet' };
    });
  }

  // Coverage -> [{text, cls: ok|warn|bad}]: loot first, then each goal, then warnings.
  function petCoverageLines(view) {
    const c = plainObject(view) && plainObject(view.coverage) ? view.coverage : null;
    if (!c || !plainObject(c.loot)) return [];
    const head = 'out ' + c.out + '/' + c.max_out;
    const out = [];
    if (c.loot.covered !== true) {
      out.push({ text: head + ' - no pets out: no loot', cls: 'bad' });
    } else {
      const parts = [head + ' - loot ok'];
      if (isNum(c.loot.t4_plus) && c.loot.t4_plus > 0) parts.push(c.loot.t4_plus + ' at T4+');
      if (typeof c.loot.alpha === 'string') parts.push('Alpha ' + c.loot.alpha + ' +' + c.loot.alpha_bonus_pct + '% loot speed');
      out.push({ text: parts.join(', '), cls: 'ok' });
    }
    (Array.isArray(c.goals) ? c.goals : []).forEach(function (g) {
      if (!plainObject(g) || typeof g.title !== 'string') return;
      if (g.covered === true) out.push({ text: g.title + ': ' + strList(g.by).join(', '), cls: 'ok' });
      else out.push({ text: g.title + ': missing ' + strList(g.missing_names).join(', '), cls: 'warn' });
    });
    strList(c.warnings).forEach(function (w) { out.push({ text: w, cls: 'warn' }); });
    return out;
  }

  // Exchange plans -> [{text, warn}]; the warning names every parent destroyed.
  function petExchangeLines(view) {
    const ex = plainObject(view) && Array.isArray(view.exchange) ? view.exchange : [];
    return ex.filter(function (e) { return plainObject(e) && isNum(e.use) && isNum(e.target_tier); }).map(function (e) {
      const head = String(e.species_name || e.species) + ' x' + e.use + ' -> T' + e.target_tier + ': ';
      const chance = isNum(e.chance_pct) ? e.chance_pct + '%' :
        'chance not sourced, ' + e.need_for_full + ' more for 100%';
      const outUsed = strList(e.out_used);
      const warn = String(e.warning || '') + (outUsed.length ? ' (' + outUsed.join(', ') + (outUsed.length > 1 ? ' are' : ' is') + ' out)' : '');
      return { text: head + chance, warn: warn };
    });
  }

  // Species select options from the server list.
  function petSpeciesOptions(view) {
    const s = plainObject(view) && Array.isArray(view.species) ? view.species : [];
    return s.filter(function (x) { return plainObject(x) && typeof x.id === 'string' && PET_KEY.test(x.id) && typeof x.name === 'string'; })
      .map(function (x) {
        const sk = strList(x.skill_names);
        return { id: x.id, label: x.name + (sk.length ? ' (' + sk.join(', ') + ')' : '') };
      });
  }

  // ---- Inventory / weight / storage + Value Pack ledger (plan 045) ----
  // GET /api/inventory -> {sources: [{id, name, min, max, verified, owned_lt,
  // next_lt, next_cost, note, warn}], base_lt, lt_owned, vp_lt, lt_total,
  // next_cheapest, slots, warehouse, towns, town_slots, vp, ledger, error}.
  // Operator-typed; the server owns every total.

  const INV_KEY = /^[a-z][a-z0-9_]{0,39}$/;
  const INV_SALE_ID = /^s[1-9][0-9]{0,8}$/;
  const INV_NOTE_MAX = 120;
  const INV_NAME_MAX = 40;
  const INV_SILVER_MAX = 1e13;
  const INV_LIMITS = { base_lt: [0, 20000], slots: [1, 400], slots_used: [0, 400], vp_cost: [0, INV_SILVER_MAX],
    lt: [0, 5000], next_lt: [1, 5000], next_cost: [0, INV_SILVER_MAX], used: [0, 10000], total: [1, 10000] };

  function invNum(v, key) { return v === null || intIn(v, INV_LIMITS[key][0], INV_LIMITS[key][1]); }

  function invFields(v, keys, need) {
    if (!plainObject(v) || !onlyKeys(v, keys)) return false;
    if (!keys.some(function (k) { return k in v && need.indexOf(k) >= 0; })) return false;
    return keys.every(function (k) {
      if (!(k in v)) return true;
      if (k === 'id') return typeof v.id === 'string' && (INV_KEY.test(v.id) || SLUG.test(v.id));
      if (k === 'note') return validAscii(v.note, INV_NOTE_MAX, true);
      if (k === 'name') return validAscii(v.name, INV_NAME_MAX);
      if (k === 'fame_vt' || k === 'vp') return typeof v[k] === 'boolean';
      if (k === 'price') return intIn(v.price, 1, INV_SILVER_MAX);
      return invNum(v[k], k);
    });
  }

  // POST /api/inventory body: exactly one of
  // {set|source|town_add|town_edit|town_del|sale|sale_del}.
  function validInventoryBody(body) {
    if (!plainObject(body)) return false;
    const keys = Object.keys(body);
    if (keys.length !== 1) return false;
    const k = keys[0];
    const v = body[k];
    const setKeys = ['base_lt', 'slots', 'slots_used', 'fame_vt', 'vp_cost'];
    const srcKeys = ['lt', 'next_lt', 'next_cost', 'note'];
    const townKeys = ['name', 'used', 'total', 'note'];
    if (k === 'set') return invFields(v, setKeys, setKeys);
    if (k === 'source') return plainObject(v) && typeof v.id === 'string' && INV_KEY.test(v.id) &&
      invFields(v, ['id'].concat(srcKeys), srcKeys);
    if (k === 'town_add') return invFields(v, townKeys, ['name']) && 'name' in v;
    if (k === 'town_edit') return plainObject(v) && typeof v.id === 'string' && SLUG.test(v.id) &&
      invFields(v, ['id'].concat(townKeys), townKeys);
    if (k === 'town_del') return typeof v === 'string' && SLUG.test(v);
    if (k === 'sale') return invFields(v, ['price', 'vp'], ['price']) && 'price' in v;
    if (k === 'sale_del') return typeof v === 'string' && INV_SALE_ID.test(v);
    return false;
  }

  // One typed number field: '' -> null (clears), digits or silver ("1.5b") -> int.
  function invInput(text, key) {
    const t = String(text === undefined || text === null ? '' : text).trim();
    if (!t) return { ok: true, value: null };
    const v = parseSilver(t);
    if (v === null || !invNum(v, key)) {
      return { ok: false, error: key.replace('_', ' ') + ' must be ' + INV_LIMITS[key][0] + '..' + INV_LIMITS[key][1] };
    }
    return { ok: true, value: v };
  }

  function invForm(f, keys, wrap) {
    const out = {};
    for (let i = 0; i < keys.length; i++) {
      const r = invInput(f[keys[i]], keys[i]);
      if (!r.ok) return r;
      out[keys[i]] = r.value;
    }
    return { ok: true, body: wrap(out) };
  }

  // Planner form {base_lt, slots, slots_used, vp_cost, fame_vt} -> {set: ...}.
  function parseInvSetForm(f) {
    return invForm(f || {}, ['base_lt', 'slots', 'slots_used', 'vp_cost'], function (o) {
      o.fame_vt = !!(f && f.fame_vt === true);
      return { set: o };
    });
  }

  // Source form {id, lt, next_lt, next_cost, note} -> {source: ...}.
  function parseInvSourceForm(f) {
    if (!f || typeof f.id !== 'string' || !INV_KEY.test(f.id)) return { ok: false, error: 'pick a weight source' };
    const note = String(f.note || '').trim();
    if (!validAscii(note, INV_NOTE_MAX, true)) return { ok: false, error: 'note must be ASCII, at most ' + INV_NOTE_MAX };
    return invForm(f, ['lt', 'next_lt', 'next_cost'], function (o) {
      return { source: Object.assign({ id: f.id }, o, { note: note }) };
    });
  }

  // Town form {name, used, total, note} -> {town_add: ...}.
  function parseInvTownForm(f) {
    const name = String(f && f.name || '').trim();
    if (!validAscii(name, INV_NAME_MAX)) return { ok: false, error: 'town name must be 1..' + INV_NAME_MAX + ' ASCII characters' };
    const note = String(f.note || '').trim();
    if (!validAscii(note, INV_NOTE_MAX, true)) return { ok: false, error: 'note must be ASCII, at most ' + INV_NOTE_MAX };
    const r = invForm(f, ['used', 'total'], function (o) { return o; });
    if (!r.ok) return r;
    if (r.body.used !== null && r.body.total !== null && r.body.used > r.body.total) {
      return { ok: false, error: 'used must not exceed total' };
    }
    return { ok: true, body: { town_add: { name: name, used: r.body.used, total: r.body.total, note: note } } };
  }

  // Sale form {price, vp} -> {sale: {price, vp}}.
  function parseInvSale(f) {
    const p = parseSilver(f && f.price);
    if (p === null || !intIn(p, 1, INV_SILVER_MAX)) return { ok: false, error: 'price must be silver, e.g. 84,500,000 or 84.5m' };
    return { ok: true, body: { sale: { price: p, vp: f.vp === true } } };
  }

  // Plan 081: [{text, stale}] - where base LT, slots, slots used, the weight
  // now and the fame bonus come from (screenshot / typed) and how old they are.
  function invDerivedLines(view) {
    if (!plainObject(view)) return [];
    const out = [];
    const inp = plainObject(view.inputs) ? view.inputs : {};
    [['base_lt', 'base LT', view.base_lt], ['slots', 'slots', plainObject(view.slots) ? view.slots.base : null],
      ['slots_used', 'slots used', plainObject(view.slots) ? view.slots.used : null]].forEach(function (r) {
      const src = derivedText(inp[r[0]]);
      if (src.text && src.text !== 'default' && isNum(r[2])) out.push({ text: r[1] + ' ' + r[2] + ' (' + src.text + ')', stale: src.stale });
    });
    const w = view.weight_now;
    if (plainObject(w) && isNum(w.used) && isNum(w.max)) {
      const src = derivedText(w);
      out.push({ text: 'weight ' + w.used + ' / ' + w.max + ' LT (' + src.text + ')', stale: src.stale });
    }
    const f = view.fame;
    if (plainObject(f) && isNum(f.value) && f.source !== 'default') {
      const src = derivedText(f);
      out.push({ text: 'fame bonus ' + f.value + '% (' + src.text + ')', stale: src.stale });
    }
    return out;
  }

  // Summary -> [{text, cls}]: weight, slots, warehouse, VP, ledger.
  function invSummaryLines(view) {
    if (!plainObject(view) || !Array.isArray(view.sources)) return [];
    const out = [];
    const vp = plainObject(view.vp) ? view.vp : {};
    const lt = isNum(view.lt_total) ? fmtSilverExact(view.lt_total) + ' LT' : 'type base LT';
    out.push({ text: 'weight ' + lt + ' (sources +' + (isNum(view.lt_owned) ? view.lt_owned : 0) +
      (view.vp_lt > 0 ? ', VP +' + view.vp_lt : '') + ')', cls: isNum(view.lt_total) ? 'ok' : 'unknown' });
    const s = plainObject(view.slots) ? view.slots : {};
    if (isNum(s.total)) {
      const free = isNum(s.free) ? ', ' + s.free + ' free' : '';
      out.push({ text: 'inventory ' + (isNum(s.used) ? s.used + '/' : '') + s.total + ' slots' + free,
        cls: isNum(s.free) && s.free <= 0 ? 'bad' : (isNum(s.free) && s.free < 8 ? 'warn' : 'ok') });
    }
    const w = plainObject(view.warehouse) ? view.warehouse : {};
    if (isNum(w.vt)) out.push({ text: 'market warehouse ' + fmtSilverExact(w.vt) + ' VT, ' + w.transfer_vt + ' VT per transfer', cls: 'ok' });
    if (vp.active === true) {
      const left = isNum(vp.left_s) ? ' - ' + fmtDuration(vp.left_s * 1000) + ' left' : ' (settings; arm the Grind timer for expiry)';
      out.push({ text: 'Value Pack on' + left, cls: isNum(vp.left_s) && vp.left_s < 86400 ? 'warn' : 'ok' });
    } else if (typeof vp.reminder === 'string') {
      out.push({ text: vp.reminder, cls: 'warn' });
    }
    const l = plainObject(view.ledger) ? view.ledger : null;
    if (l && isNum(l.count) && l.count > 0) {
      let t = 'VP +30% earned ' + fmtSilver(l.gain_30d) + ' in 30 d (' + fmtSilver(l.gain_total) + ' total, ' + l.count + ' sales)';
      if (isNum(l.net_30d)) t += ', net of VP cost ' + fmtSilver(l.net_30d);
      out.push({ text: t, cls: isNum(l.net_30d) && l.net_30d < 0 ? 'warn' : 'ok' });
    }
    return out;
  }

  // Source checklist rows; junk dropped.
  function invSourceRows(view) {
    const r = plainObject(view) && Array.isArray(view.sources) ? view.sources : [];
    return r.filter(function (x) { return plainObject(x) && typeof x.id === 'string' && INV_KEY.test(x.id) && typeof x.name === 'string'; })
      .map(function (x) {
        const own = isNum(x.owned_lt) ? '+' + x.owned_lt + ' LT' : 'not set';
        const next = isNum(x.next_lt) ? 'next +' + x.next_lt + (isNum(x.next_cost) ? ' for ' + fmtSilver(x.next_cost) : '') : '';
        return { id: x.id, name: x.name, owned: own, next: next, note: typeof x.note === 'string' ? x.note : '',
          warn: typeof x.warn === 'string' ? x.warn : '', done: isNum(x.owned_lt) && x.owned_lt > 0,
          range: x.min + '..' + x.max + ' LT' + (x.verified === true ? '' : ' (unverified)'),
          lt: isNum(x.owned_lt) ? x.owned_lt : null, next_lt: isNum(x.next_lt) ? x.next_lt : null,
          next_cost: isNum(x.next_cost) ? x.next_cost : null };
      });
  }

  // Cheapest next +LT, silver per LT ascending (server order kept).
  function invNextLines(view) {
    const n = plainObject(view) && Array.isArray(view.next_cheapest) ? view.next_cheapest : [];
    return n.filter(function (x) { return plainObject(x) && isNum(x.next_lt) && isNum(x.cost_per_lt); }).map(function (x) {
      return String(x.name) + ': +' + x.next_lt + ' LT for ' + fmtSilver(x.next_cost) + ' (' + fmtSilver(x.cost_per_lt) + '/LT)';
    });
  }

  function invTownRows(view) {
    const t = plainObject(view) && Array.isArray(view.towns) ? view.towns : [];
    return t.filter(function (x) { return plainObject(x) && typeof x.id === 'string' && SLUG.test(x.id) && typeof x.name === 'string'; })
      .map(function (x) {
        const slots = isNum(x.total) ? (isNum(x.used) ? x.used + '/' : '') + x.total + (isNum(x.free) ? ' (' + x.free + ' free)' : '') : 'slots not set';
        return { id: x.id, text: x.name + ' - ' + slots + (x.note ? ' - ' + x.note : ''),
          full: isNum(x.free) && x.free <= 0 };
      });
  }

  function invSaleRows(view, max) {
    const l = plainObject(view) && plainObject(view.ledger) && Array.isArray(view.ledger.sales) ? view.ledger.sales : [];
    return l.filter(function (x) { return plainObject(x) && typeof x.id === 'string' && INV_SALE_ID.test(x.id) && isNum(x.price); })
      .slice(0, isNum(max) ? max : 5).map(function (x) {
        return { id: x.id, text: String(x.at || '').slice(0, 10) + ' sold ' + fmtSilver(x.price) + ' -> ' + fmtSilver(x.net) +
          (x.vp === true ? ' (VP +' + fmtSilver(x.gain) + ')' : ' (no VP)') };
      });
  }

  // ---- Cooking / alchemy margin calculator (plan 054) ----
  // GET /api/crafting -> {recipes: [{id, name, kind, inputs: [{id, name, qty,
  // vendor_price, label, unit}], outputs / procs: [{id, name, qty_avg, label,
  // unit, net_unit}], margin: {cost, gross, net, profit, profit_1000,
  // margin_pct, complete, missing}}], tax: {vp, fame_pct}, max_recipes}.
  // The server owns every number; the card only formats.
  const CRAFT_KINDS = ['cooking', 'alchemy'];
  const CRAFT_LIMITS = { inputs: 20, outputs: 10, procs: 10, name: 60, line: 40, qty: 9999,
    qty_avg: 1000, silver: 1e13 };

  function craftText(v, max) {
    return typeof v === 'string' && v.trim() !== '' && v.length <= max && /^[\x20-\x7e]+$/.test(v);
  }

  function craftInputOk(x) {
    if (!plainObject(x) || Object.keys(x).some(function (k) {
      return ['id', 'name', 'qty', 'vendor_price'].indexOf(k) < 0;
    })) return false;
    const hasId = x.id !== undefined && x.id !== null;
    const hasName = x.name !== undefined && x.name !== null;
    if (!hasId && !hasName) return false;
    if (hasId && !isId(x.id)) return false;
    if (hasName && !craftText(x.name, CRAFT_LIMITS.line)) return false;
    if (!isInt(x.qty, 1) || x.qty > CRAFT_LIMITS.qty) return false;
    const vp = x.vendor_price;
    return vp === undefined || vp === null || (isInt(vp, 0) && vp <= CRAFT_LIMITS.silver);
  }

  function craftOutputOk(x) {
    if (!plainObject(x) || Object.keys(x).some(function (k) {
      return ['id', 'name', 'qty_avg'].indexOf(k) < 0;
    })) return false;
    if (!isId(x.id)) return false;
    if (x.name !== undefined && x.name !== null && !craftText(x.name, CRAFT_LIMITS.line)) return false;
    return isNum(x.qty_avg) && x.qty_avg > 0 && x.qty_avg <= CRAFT_LIMITS.qty_avg;
  }

  function craftList(v, lo, hi, ok) {
    return Array.isArray(v) && v.length >= lo && v.length <= hi && v.every(ok);
  }

  function validRecipe(r, withId) {
    if (!plainObject(r)) return false;
    const allowed = ['name', 'kind', 'inputs', 'outputs', 'procs'].concat(withId ? ['id'] : []);
    if (Object.keys(r).some(function (k) { return allowed.indexOf(k) < 0; })) return false;
    if (withId && !(typeof r.id === 'string' && SLUG.test(r.id))) return false;
    if (!craftText(r.name, CRAFT_LIMITS.name)) return false;
    if (r.kind !== undefined && CRAFT_KINDS.indexOf(r.kind) < 0) return false;
    return craftList(r.inputs, 1, CRAFT_LIMITS.inputs, craftInputOk) &&
      craftList(r.outputs, 1, CRAFT_LIMITS.outputs, craftOutputOk) &&
      (r.procs === undefined || craftList(r.procs, 0, CRAFT_LIMITS.procs, craftOutputOk));
  }

  // POST /api/crafting body: exactly one of {add: recipe}, {edit: recipe + id},
  // {delete: id}.
  function validCraftingBody(body) {
    if (!plainObject(body)) return false;
    const keys = Object.keys(body);
    if (keys.length !== 1) return false;
    const v = body[keys[0]];
    if (keys[0] === 'add') return validRecipe(v, false);
    if (keys[0] === 'edit') return validRecipe(v, true);
    if (keys[0] === 'delete') return typeof v === 'string' && SLUG.test(v);
    return false;
  }

  // One textarea -> recipe lines, one per non-empty line.
  //   input:  "<qty> <#id | name> [@ <vendor price>]"   e.g. "5 #9001", "2 Leavening Agent @20"
  //   output: "<qty_avg> #<id> [name]"                   e.g. "2.5 #9213 Beer"
  // Vendor prices use the silver parser ("1.5k").
  function parseCraftLines(text, side) {
    const out = [];
    const rows = String(text === undefined || text === null ? '' : text).split(/\r?\n/);
    for (let i = 0; i < rows.length; i++) {
      const t = rows[i].trim();
      if (!t) continue;
      const where = side + ' line ' + (out.length + 1);
      if (side === 'input') {
        const m = /^(\d{1,4})\s*x?\s+(#\d{1,10}|[^@#]*[^@#\s])\s*(?:@\s*(.+))?$/i.exec(t);
        if (!m) return { ok: false, error: where + ': use "qty #id" or "qty name [@ vendor price]"' };
        const line = { qty: Number(m[1]) };
        if (m[2][0] === '#') line.id = Number(m[2].slice(1));
        else line.name = m[2];
        if (m[3] !== undefined) {
          const v = parseSilver(m[3]);
          if (v === null) return { ok: false, error: where + ': vendor price is not silver' };
          line.vendor_price = v;
        }
        if (!craftInputOk(line)) return { ok: false, error: where + ': qty 1..9999, id or name up to 40 chars' };
        out.push(line);
      } else {
        const m = /^(\d{1,4}(?:\.\d{1,3})?)\s*x?\s+#(\d{1,10})(?:\s+(.+))?$/i.exec(t);
        if (!m) return { ok: false, error: where + ': use "avg #id [name]"' };
        const line = { id: Number(m[2]), qty_avg: Number(m[1]) };
        if (m[3] !== undefined) line.name = m[3].trim();
        if (!craftOutputOk(line)) return { ok: false, error: where + ': avg in (0, 1000], id, name up to 40 chars' };
        out.push(line);
      }
    }
    return { ok: true, lines: out };
  }

  // Form fields {name, kind, inputs, outputs, procs} (texts) -> {ok, body} for
  // add (no id) or edit (id), or {ok: false, error}.
  function parseCraftForm(f, id) {
    const src = plainObject(f) ? f : {};
    const name = String(src.name === undefined || src.name === null ? '' : src.name).trim();
    if (!craftText(name, CRAFT_LIMITS.name)) return { ok: false, error: 'name: 1..60 printable ASCII chars' };
    const kind = CRAFT_KINDS.indexOf(src.kind) >= 0 ? src.kind : 'cooking';
    const recipe = { name: name, kind: kind };
    const sides = [['inputs', 'input', 1], ['outputs', 'output', 1], ['procs', 'proc', 0]];
    for (let i = 0; i < sides.length; i++) {
      const r = parseCraftLines(src[sides[i][0]], sides[i][1]);
      if (!r.ok) return r;
      if (r.lines.length < sides[i][2]) return { ok: false, error: sides[i][0] + ': at least one line' };
      if (r.lines.length > CRAFT_LIMITS[sides[i][0]]) {
        return { ok: false, error: sides[i][0] + ': at most ' + CRAFT_LIMITS[sides[i][0]] + ' lines' };
      }
      recipe[sides[i][0]] = r.lines;
    }
    if (id !== undefined && id !== null) {
      recipe.id = id;
      return validRecipe(recipe, true) ? { ok: true, body: { edit: recipe } } : { ok: false, error: 'bad recipe' };
    }
    return validRecipe(recipe, false) ? { ok: true, body: { add: recipe } } : { ok: false, error: 'bad recipe' };
  }

  // A stored recipe -> the form texts parseCraftForm reads back.
  function craftFormOf(r) {
    if (!plainObject(r)) return { name: '', kind: 'cooking', inputs: '', outputs: '', procs: '' };
    const inLine = function (x) {
      return x.qty + ' ' + (isId(x.id) ? '#' + x.id : x.name) +
        (isInt(x.vendor_price, 0) ? ' @' + x.vendor_price : '');
    };
    const outLine = function (x) { return x.qty_avg + ' #' + x.id + (x.name ? ' ' + x.name : ''); };
    const lines = function (v, fn) { return Array.isArray(v) ? v.filter(plainObject).map(fn).join('\n') : ''; };
    return { name: typeof r.name === 'string' ? r.name : '',
      kind: CRAFT_KINDS.indexOf(r.kind) >= 0 ? r.kind : 'cooking',
      inputs: lines(r.inputs, inLine), outputs: lines(r.outputs, outLine), procs: lines(r.procs, outLine) };
  }

  // One recipe row's margin -> display strings.
  function craftSummary(r) {
    const m = plainObject(r) && plainObject(r.margin) ? r.margin : null;
    if (!m) return { cost: '-', net: '-', profit: '-', per1000: '-', pct: '', loss: true, missing: '' };
    const miss = Array.isArray(m.missing) ? m.missing.filter(plainObject).map(function (x) {
      return x.side + ' ' + (isId(x.id) ? '#' + x.id : String(x.name));
    }) : [];
    const p = m.complete === true && isNum(m.profit) ? m.profit : null;
    return {
      cost: fmtSilver(m.cost), net: fmtSilver(m.net),
      profit: p === null ? '-' : fmtSilver(p),
      per1000: m.complete === true && isNum(m.profit_1000) ? fmtSilver(m.profit_1000) : '-',
      pct: isNum(m.margin_pct) ? m.margin_pct + '%' : '',
      loss: p === null || p < 0,
      missing: miss.length ? 'no price: ' + miss.join(', ') : ''
    };
  }

  Object.assign(K, { PET_NAME_MAX: PET_NAME_MAX, PET_TALENTS_MAX: PET_TALENTS_MAX,
    PET_TALENT_MAX: PET_TALENT_MAX, PET_TIER: PET_TIER, PET_KEY: PET_KEY,
    PET_FIELDS: PET_FIELDS, validPetFields: validPetFields, validPetsBody: validPetsBody,
    parsePetForm: parsePetForm, strList: strList, petRows: petRows,
    petCoverageLines: petCoverageLines, petExchangeLines: petExchangeLines,
    petSpeciesOptions: petSpeciesOptions, INV_KEY: INV_KEY, INV_SALE_ID: INV_SALE_ID,
    INV_NOTE_MAX: INV_NOTE_MAX, INV_NAME_MAX: INV_NAME_MAX, INV_SILVER_MAX: INV_SILVER_MAX,
    INV_LIMITS: INV_LIMITS, invNum: invNum, invFields: invFields,
    validInventoryBody: validInventoryBody, invInput: invInput, invForm: invForm,
    parseInvSetForm: parseInvSetForm, parseInvSourceForm: parseInvSourceForm,
    parseInvTownForm: parseInvTownForm, parseInvSale: parseInvSale,
    invDerivedLines: invDerivedLines, invSummaryLines: invSummaryLines,
    invSourceRows: invSourceRows, invNextLines: invNextLines, invTownRows: invTownRows,
    invSaleRows: invSaleRows, CRAFT_KINDS: CRAFT_KINDS, CRAFT_LIMITS: CRAFT_LIMITS,
    craftText: craftText, craftInputOk: craftInputOk, craftOutputOk: craftOutputOk,
    craftList: craftList, validRecipe: validRecipe, validCraftingBody: validCraftingBody,
    parseCraftLines: parseCraftLines, parseCraftForm: parseCraftForm, craftFormOf: craftFormOf,
    craftSummary: craftSummary });
  return function link() {};
});
