/* EW shared pure logic, part `progress` (plan 107 split of ewcore.js):
    Progress, brackets, tracks, season pass, profile gate + history, life and CP.
   Installed in order by ../ewcore.js, which re-exports the public names as
   EWCore. Loaded as a plain script before ewcore.js (window.EWCoreParts) and by
   require() under node --test. No DOM, no Electron. */
(function (root, install) {
  if (typeof module !== 'undefined' && module.exports) module.exports = install;
  else (root.EWCoreParts = root.EWCoreParts || {}).progress = install;
})(typeof self !== 'undefined' ? self : this, function (K) {
  'use strict';

  // from earlier parts
  const { TITLE_MAX, isInt, isNum, onlyKeys, plainObject, signalRows, sourcePill, validTitle } = K;

  // ---- Progress (plan 004) ----
  // A step is done when it carries a done_at stamp; counts are re-derived on
  // the client so an optimistic toggle updates the bar before the server answers.

  const TRACK_KINDS = ['quest', 'season', 'gear'];
  const ID = /^[a-z0-9-]{1,40}$/;   // server ID_RE
  const SEED_ID = /^[a-z][a-z0-9_]{0,39}$/;   // server SEED_ID_RE (plan 034)
  const STEPS_MAX = 60;              // server MAX_STEPS
  const LEVEL_MAX = 75;              // server levels.LEVEL_MAX (plan 018)
  const LEVEL = [1, LEVEL_MAX];
  const STAT = [0, 999];

  function gsTotal(ap, aap, dp) {
    if (!isNum(ap) || !isNum(aap) || !isNum(dp)) return null;
    return (ap + aap) / 2 + dp;
  }

  // Plan 023: GET /api/progress `brackets` -> [{key, text, cliff, verify}],
  // one line per set stat (DP gives a DR % line and an all-DR line). A null
  // value is a span the tracked table does not hold: said so, never guessed.
  function bracketLine(label, b, fmt, unit, pctUnit) {
    if (!b || typeof b !== 'object' || !isNum(b.x)) return null;
    if (b.value === null) {
      const span = isNum(b.bracket_min) && isNum(b.bracket_max) ? ' (' + b.bracket_min + '-' + b.bracket_max + ')' : '';
      return label + ' ' + b.x + ' -> ' + unit + ' not in table' + span;
    }
    if (!isNum(b.value)) return null;
    let s = label + ' ' + b.x + ' -> ' + fmt(b.value);
    if (!isNum(b.next_min)) return s + ' (top bracket)';
    s += '; +' + (b.next_min - b.x) + ' ' + label + ' to ' + b.next_min;
    return s + (isNum(b.next_gain) ? ' gives +' + b.next_gain + (pctUnit ? '%' : '') : ': next bracket not in table');
  }

  function bracketLines(br) {
    if (!br || typeof br !== 'object') return [];
    const tables = br.tables && typeof br.tables === 'object' ? br.tables : {};
    const verify = function (name) { return !!(tables[name] && tables[name].reverify === true); };
    const bonus = function (v) { return '+' + v + ' bonus'; };
    const out = [];
    const push = function (key, table, text, cliff) {
      if (text) out.push({ key: key, text: text, cliff: cliff === true, verify: verify(table) });
    };
    push('ap', 'ap', bracketLine('AP', br.ap, bonus, 'bonus'), br.ap && br.ap.cliff);
    push('aap', 'ap', bracketLine('AAP', br.aap, bonus, 'bonus'), br.aap && br.aap.cliff);
    if (br.dp && typeof br.dp === 'object') {
      push('dp', 'dp_dr', bracketLine('DP', br.dp, function (v) { return v + '% DR'; }, 'DR', true), br.dp.cliff);
      const all = br.dp.all_dr;
      push('dp_all', 'dp_all_dr', bracketLine('DP', all, function (v) { return 'all-DR +' + v; }, 'all-DR'), false);
    }
    return out;
  }

  // Plan 034: "Add track" seed choices from GET /api/progress `seeds`; an added
  // seed stays listed but disabled (seeding twice is a server no-op anyway).
  function seedOptions(seeds) {
    if (!Array.isArray(seeds)) return [];
    const out = [];
    seeds.forEach(function (s) {
      if (!plainObject(s) || typeof s.id !== 'string' || !SEED_ID.test(s.id)) return;
      const title = typeof s.title === 'string' && s.title ? s.title : s.id;
      const unv = isInt(s.unverified, 1) ? ' (' + s.unverified + ' to verify)' : '';
      out.push({ id: s.id, label: title + (s.added === true ? ' - added' : unv), added: s.added === true });
    });
    return out;
  }

  // Plan 034: gate chips for a track step: [{text, cls, title}]. `ready` is ok,
  // `needs` warn (with the plan 023 bonus-AP hint), unknown stats muted.
  function gateChips(step) {
    const gates = plainObject(step) && Array.isArray(step.gates) ? step.gates : [];
    const unit = { level: 'Lv', ap: 'AP', dp: 'DP' };
    const out = [];
    gates.forEach(function (g) {
      if (!plainObject(g) || !unit[g.stat] || !isNum(g.need)) return;
      const need = unit[g.stat] + ' ' + g.need;
      if (g.state === 'ready') {
        out.push({ text: need + ' ready', cls: 'ok', title: 'gate met' });
      } else if (g.state === 'needs' && isNum(g.gap)) {
        const sfx = g.stat === 'level' ? ' lv' : ' ' + unit[g.stat];
        const bonus = g.stat === 'ap' && isNum(g.bonus_gain) && g.bonus_gain > 0 ? ' (+' + g.bonus_gain + ' bonus AP)' : '';
        out.push({ text: need + ': needs +' + g.gap + sfx + bonus, cls: 'warn', title: 'have ' + g.have });
      } else {
        out.push({ text: need, cls: 'unknown', title: 'set ' + (g.stat === 'level' ? 'level' : unit[g.stat]) + ' on the Character card' });
      }
    });
    return out;
  }

  // Whole percent, floored like the server's pct(), so 100 only when every step is done.
  function trackPct(track) {
    let done = 0;
    let total = 0;
    const steps = track && Array.isArray(track.steps) ? track.steps : [];
    steps.forEach(function (s) {
      if (!plainObject(s)) return;
      total += 1;
      if (s.done_at) done += 1;
    });
    const pct = total ? Math.floor(100 * done / total) : 0;
    return { done: done, total: total, pct: pct };
  }

  // Optimistic update: a copy of the GET body with one step's done_at set (iso)
  // or cleared (null) and that track's counts re-derived. Input never mutated.
  function withStep(data, trackId, stepId, iso) {
    if (!plainObject(data) || !Array.isArray(data.tracks)) return data === undefined ? null : data;
    const copy = Object.assign({}, data);
    copy.tracks = data.tracks.map(function (t) {
      if (!t || t.id !== trackId || !Array.isArray(t.steps)) return t;
      const nt = Object.assign({}, t);
      nt.steps = t.steps.map(function (s) {
        if (!s || s.id !== stepId) return s;
        return Object.assign({}, s, { done_at: iso });
      });
      // Season objectives (plan 013) mirror the step; an untick drops the claim too.
      if (Array.isArray(t.objectives)) {
        nt.objectives = t.objectives.map(function (o) {
          if (!o || o.id !== stepId) return o;
          const patch = { done_at: iso, done: iso !== null };
          if (iso === null) { patch.claimed_at = null; patch.claimed = false; }
          return Object.assign({}, o, patch);
        });
      }
      return Object.assign(nt, trackPct(nt));
    });
    return copy;
  }

  // ---- Season pass by objective (plan 013) ----
  const OBJ_KINDS = ['level', 'gear', 'quest', 'other'];
  const GEAR_TARGET = [1, 20];               // server GEAR_RANGE
  const REWARD_MAX = 80;                     // server MAX_REWARD
  const GEAR_GRADES = ['PRI', 'DUO', 'TRI', 'TET', 'PEN'];
  const NEXT_OPEN = 3;

  function fmtTarget(kind, target) {
    if (kind === 'level' && inRange(target, LEVEL)) return 'Lv ' + target;
    if (kind === 'gear' && inRange(target, GEAR_TARGET)) return target > 15 ? GEAR_GRADES[target - 16] : '+' + target;
    return '';
  }

  // Next open (up to 3, list order), done-but-unclaimed, and counts.
  function seasonGroups(track) {
    const objs = track && Array.isArray(track.objectives) ? track.objectives.filter(plainObject) : [];
    const done = objs.filter(function (o) { return !!o.done_at; });
    return {
      next: objs.filter(function (o) { return !o.done_at; }).slice(0, NEXT_OPEN),
      unclaimed: done.filter(function (o) { return !o.claimed_at; }),
      done: done.length,
      total: objs.length,
      claimed: done.length - done.filter(function (o) { return !o.claimed_at; }).length
    };
  }

  // Overlay one-liner from GET /api/progress `season`: "Pass 23/40 - next: Lv 50 (2 lv)".
  function seasonLine(s) {
    if (!plainObject(s) || typeof s.track !== 'string' || !isNum(s.done) || !isNum(s.total)) {
      return 'no season pass track';
    }
    let line = 'Pass ' + s.done + '/' + s.total;
    const n = Array.isArray(s.next) && plainObject(s.next[0]) ? s.next[0] : null;
    if (!n) line += ' - all done';
    else if (n.kind === 'level' && inRange(n.target, LEVEL)) {
      line += ' - next: Lv ' + n.target + (isNum(n.gap) && n.gap > 0 ? ' (' + n.gap + ' lv)' : '');
    } else line += ' - next: ' + String(n.title || n.id || '?');
    const un = Array.isArray(s.unclaimed) ? s.unclaimed.length : 0;
    if (un > 0) line += ' | claim ' + un;
    return line;
  }

  function validReward(r) {
    return typeof r === 'string' && r.length <= REWARD_MAX && !/[\u0000-\u001f\u007f]/.test(r);
  }

  function validObjFields(v) {
    if (v.title !== undefined && !validTitle(v.title)) return false;
    if (v.kind !== undefined && OBJ_KINDS.indexOf(v.kind) < 0) return false;
    if (v.target !== undefined && v.target !== null && !isInt(v.target, 1)) return false;
    return v.reward === undefined || validReward(v.reward);
  }

  function validObjRef(v, keys) {
    return plainObject(v) && onlyKeys(v, keys) && typeof v.track === 'string' && ID.test(v.track) &&
      typeof v.objective === 'string' && ID.test(v.objective);
  }

  // Objective add form -> {obj_add: {...}} body, or an operator error. Gear
  // target takes a number or a grade (PRI..PEN). With `objective` (an id) the
  // same form edits in place: {obj_edit: {track, objective, title, kind,
  // target, reward}} - done/claim marks and list position are kept.
  function parseObjectiveForm(track, form, objective) {
    const r = parseObjectiveAdd(track, form);
    if (!r.ok || objective === undefined) return r;
    return { ok: true, body: { obj_edit: Object.assign({ objective: objective }, r.body.obj_add) } };
  }

  // Inverse of fmtTarget for the form's target box.
  function targetInput(kind, target) {
    if (kind === 'gear') return fmtTarget(kind, target).replace(/^\+/, '');
    return kind === 'level' && inRange(target, LEVEL) ? String(target) : '';
  }

  function parseObjectiveAdd(track, form) {
    const f = form || {};
    const title = typeof f.title === 'string' ? f.title.trim() : '';
    if (!title) return { ok: false, error: 'title required' };
    if (!validTitle(title)) return { ok: false, error: 'title: up to ' + TITLE_MAX + ' plain characters' };
    if (OBJ_KINDS.indexOf(f.kind) < 0) return { ok: false, error: 'kind must be level, gear, quest or other' };
    const raw = f.target === undefined || f.target === null ? '' : String(f.target).trim().toUpperCase();
    let target = null;
    if (f.kind === 'level') {
      target = wholeIn(raw, LEVEL);
      if (target === null) return { ok: false, error: 'level target must be ' + LEVEL[0] + '-' + LEVEL[1] };
    } else if (f.kind === 'gear' && raw) {
      const g = GEAR_GRADES.indexOf(raw);
      target = g >= 0 ? 16 + g : wholeIn(raw.replace(/^\+/, ''), GEAR_TARGET);
      if (target === null) return { ok: false, error: 'gear target: +1..+20 or PRI..PEN' };
    } else if (raw) return { ok: false, error: f.kind + ' takes no target' };
    const reward = typeof f.reward === 'string' ? f.reward.trim() : '';
    if (!validReward(reward)) return { ok: false, error: 'reward: up to ' + REWARD_MAX + ' plain characters' };
    return { ok: true, body: { obj_add: { track: track, title: title, kind: f.kind, target: target, reward: reward } } };
  }

  function inRange(v, r) { return isInt(v, r[0]) && v <= r[1]; }

  function validCharacter(c) {
    if (!plainObject(c) || !Object.keys(c).length || !onlyKeys(c, ['name', 'cls', 'level', 'gs'])) return false;
    if (c.name !== undefined && !validTitle(c.name)) return false;
    if (c.cls !== undefined && !validTitle(c.cls)) return false;
    if (c.level !== undefined && !inRange(c.level, LEVEL)) return false;
    if (c.gs === undefined) return true;
    return plainObject(c.gs) && Object.keys(c.gs).length > 0 && onlyKeys(c.gs, ['ap', 'aap', 'dp']) &&
      Object.keys(c.gs).every(function (k) { return inRange(c.gs[k], STAT); });
  }

  // Exact shape check for POST /api/progress bodies (main-process IPC guard).
  function validProgressBody(body) {
    if (!plainObject(body)) return false;
    const keys = Object.keys(body);
    if (keys.length !== 1) return false;
    const k = keys[0];
    const v = body[k];
    if (k === 'character') return validCharacter(v);
    if (k === 'remove_track') return typeof v === 'string' && ID.test(v);
    if (k === 'track_seed') return typeof v === 'string' && SEED_ID.test(v);
    if (k === 'step') {
      return plainObject(v) && onlyKeys(v, ['track', 'step', 'done']) && typeof v.track === 'string' &&
        ID.test(v.track) && typeof v.step === 'string' && ID.test(v.step) && typeof v.done === 'boolean';
    }
    if (k === 'add_track') {
      return plainObject(v) && onlyKeys(v, ['title', 'kind', 'steps']) && validTitle(v.title) &&
        TRACK_KINDS.indexOf(v.kind) >= 0 && Array.isArray(v.steps) && v.steps.length <= STEPS_MAX &&
        v.steps.every(validTitle);
    }
    if (k === 'claim') {
      return validObjRef(v, ['track', 'objective', 'claimed']) && typeof v.claimed === 'boolean';
    }
    if (k === 'obj_add') {
      return plainObject(v) && onlyKeys(v, ['track', 'title', 'kind', 'target', 'reward']) &&
        typeof v.track === 'string' && ID.test(v.track) && v.title !== undefined && v.kind !== undefined &&
        validObjFields(v);
    }
    if (k === 'obj_edit') {
      const edits = ['title', 'kind', 'target', 'reward'];
      return validObjRef(v, ['track', 'objective'].concat(edits)) &&
        edits.some(function (e) { return v[e] !== undefined; }) && validObjFields(v);
    }
    if (k === 'obj_del') return validObjRef(v, ['track', 'objective']);
    return false;
  }

  function wholeIn(s, r) {
    const t = s === undefined || s === null ? '' : String(s).trim();
    if (!/^\d+$/.test(t)) return null;
    const v = Number(t);
    return inRange(v, r) ? v : null;
  }

  // Character card inputs -> {character: {level, gs}} body, or an operator error.
  function parseCharacterForm(form) {
    const f = form || {};
    const level = wholeIn(f.level, LEVEL);
    if (level === null) return { ok: false, error: 'level must be a whole number ' + LEVEL[0] + '-' + LEVEL[1] };
    const gs = {};
    for (const k of ['ap', 'aap', 'dp']) {
      const v = wholeIn(f[k], STAT);
      if (v === null) return { ok: false, error: k.toUpperCase() + ' must be a whole number ' + STAT[0] + '-' + STAT[1] };
      gs[k] = v;
    }
    return { ok: true, body: { character: { level: level, gs: gs } } };
  }

  // Add-track form: steps one per line, blank lines dropped.
  function parseTrackForm(form) {
    const f = form || {};
    const title = typeof f.title === 'string' ? f.title.trim() : '';
    if (!title) return { ok: false, error: 'title required' };
    if (!validTitle(title)) return { ok: false, error: 'title: up to ' + TITLE_MAX + ' plain characters' };
    if (TRACK_KINDS.indexOf(f.kind) < 0) return { ok: false, error: 'kind must be quest, season or gear' };
    const steps = String(f.steps || '').split(/\r?\n/).map(function (s) { return s.trim(); })
      .filter(function (s) { return s; });
    if (!steps.length) return { ok: false, error: 'at least one step (one per line)' };
    if (steps.length > STEPS_MAX) return { ok: false, error: 'at most ' + STEPS_MAX + ' steps' };
    if (!steps.every(validTitle)) return { ok: false, error: 'each step: up to ' + TITLE_MAX + ' plain characters' };
    return { ok: true, body: { add_track: { title: title, kind: f.kind, steps: steps } } };
  }

  // Plan 077: one /api/signals row by id (signalRows shape), or null.
  function signalRow(doc, id) {
    return signalRows(doc).filter(function (r) { return r.id === id; })[0] || null;
  }

  // Plan 077: the profile signal row when /api/signals says it is off by design
  // (no family / no base / robots) - the one status truth for the profile and
  // Life & CP cards. Label = the hint's lead clause; text = the full hint.
  function profileSignalOff(signals) {
    const r = signalRow(signals, 'profile');
    if (!r || r.level !== 'off') return null;
    const text = (r.hint || r.detail || 'profile source off').slice(0, 160);
    return { label: text.split(' - ')[0].slice(0, 48), text: text };
  }

  // /api/progress `profile`: null or status "none" = no family configured;
  // "pending" = upstream is fetching and nothing is cached yet (not an error).
  // Plan 077: `signals` (the /api/signals body, optional) wins when its profile
  // row is off - muted, never a red age pill for an expected-off source.
  function profilePill(profile, signals) {
    const sig = profileSignalOff(signals);
    if (sig) {
      return { cls: 'unknown', label: sig.label, text: sig.text, stale: true, error: null,
        none: false, pending: false, off: true, reason: null };
    }
    if (!plainObject(profile) || profile.status === 'none') {
      return { cls: 'unknown', label: 'no profile', stale: true, error: null, none: true, pending: false };
    }
    // Plan 061: profile source off (no self-hosted base, or robots disallow).
    if (profile.state === 'off' || profile.status === 'off') {
      return { cls: 'unknown', label: 'profile off', stale: true, error: null, none: false,
        pending: false, off: true, reason: typeof profile.reason === 'string' ? profile.reason : null };
    }
    if (profile.status === 'pending' && !plainObject(profile.data)) {
      return { cls: 'unknown', label: 'profile fetching', stale: true, error: null, none: false, pending: true };
    }
    const p = sourcePill(profile.freshness, 'profile', 3600);
    p.none = false;
    p.pending = false;
    return p;
  }

  function plainText(v) {
    return (typeof v === 'string' && v.trim() && v.length <= TITLE_MAX) || isNum(v) ? String(v) : null;
  }

  // Served profile {family, region, guild: str|null, characters: [{name, cls,
  // level, main}]} -> [label, value] rows from known keys only.
  function profileRows(data) {
    if (!plainObject(data)) return [];
    const rows = [];
    const push = function (label, v) { const t = plainText(v); if (t !== null) rows.push([label, t]); };
    push('Family', data.family);
    push('Region', data.region);
    push('Guild', data.guild);
    const chars = Array.isArray(data.characters) ? data.characters.filter(plainObject) : [];
    const main = chars.filter(function (c) { return c.main; })[0] || chars[0];
    if (main && plainText(main.name)) {
      const bits = [plainText(main.cls), plainText(main.level)].filter(Boolean).join(' ');
      push('Main', main.name + (bits ? ' - ' + bits : ''));
    }
    if (chars.length) push('Characters', chars.length);
    return rows;
  }

  // ---- Profile history (plan 041) ----
  // GET /api/progress/history body {days, character, series: {field: {points:
  // [{at, v}], first, last, delta}}} -> one row per card field: {field, label,
  // points: [[ms, v]] for sparkPath, text: "62 (+1)" | "640" | "-"}.
  const HISTORY_FIELDS = [['level', 'Level'], ['gs', 'GS'], ['energy', 'Energy'], ['contribution', 'CP']];
  const PROFILE_HISTORY_DAYS = 30;
  const PROFILE_HISTORY_PATH = '/api/progress/history?field=' +
    HISTORY_FIELDS.map(function (f) { return f[0]; }).join(',') + '&days=' + PROFILE_HISTORY_DAYS;

  function historyRows(body) {
    const series = plainObject(body) && plainObject(body.series) ? body.series : {};
    return HISTORY_FIELDS.map(function (f) {
      const s = plainObject(series[f[0]]) ? series[f[0]] : {};
      const points = (Array.isArray(s.points) ? s.points : []).map(function (p) {
        const t = plainObject(p) && typeof p.at === 'string' ? Date.parse(p.at) : NaN;
        return plainObject(p) && isFinite(t) && isNum(p.v) ? [t, p.v] : null;
      }).filter(function (p) { return p !== null; });
      const last = points.length ? points[points.length - 1][1] : null;
      const delta = points.length ? last - points[0][1] : 0;
      const text = last === null ? '-' : String(last) + (delta ? ' (' + (delta > 0 ? '+' : '') + delta + ')' : '');
      return { field: f[0], label: f[1], points: points, text: text };
    });
  }

  // ---- Life & CP card (plan 042) ----
  // /api/progress `lifeskill` {status, character, skills: [{name, rank}] |
  // "hidden", energy: n | "hidden", cp: {value, next: {cp, label, verified},
  // gap, reached} | "hidden"} -> {state: ok | none, character, rows: [label,
  // text], skills: [[name, rank]], skillsText}. Privacy-hidden fields read
  // "hidden (privacy)"; an unverified milestone carries " (verify)".
  const PRIVACY_HIDDEN = 'hidden (privacy)';
  const LIFESKILL_TRENDS = ['energy', 'contribution'];

  function lifeskillView(card) {
    if (!plainObject(card) || card.status !== 'ok') {
      return { state: 'none', character: null, rows: [], skills: [], skillsText: '' };
    }
    const stat = function (v) { return v === 'hidden' ? PRIVACY_HIDDEN : isNum(v) ? String(v) : '-'; };
    const rows = [['Energy', stat(card.energy)]];
    const cp = card.cp;
    if (plainObject(cp) && isNum(cp.value)) {
      rows.push(['CP', String(cp.value)]);
      const n = cp.next;
      if (plainObject(n) && isNum(n.cp) && isNum(cp.gap) && plainText(n.label)) {
        rows.push(['Next CP', n.cp + ' (+' + cp.gap + ') ' + n.label + (n.verified === false ? ' (verify)' : '')]);
      } else {
        rows.push(['Next CP', 'all milestones reached']);
      }
    } else {
      rows.push(['CP', stat(cp)]);
    }
    const skills = Array.isArray(card.skills) ? card.skills.filter(function (s) {
      return plainObject(s) && plainText(s.name) && plainText(s.rank);
    }).map(function (s) { return [String(s.name), String(s.rank)]; }) : [];
    const skillsText = card.skills === 'hidden' ? PRIVACY_HIDDEN : skills.length ? '' : '-';
    return { state: 'ok', character: plainText(card.character), rows: rows, skills: skills, skillsText: skillsText };
  }

  // Plan 041 history rows split between the cards: energy / CP on Life & CP,
  // the rest (level, GS) on Profile.
  function lifeskillTrends(body) {
    return historyRows(body).filter(function (r) { return LIFESKILL_TRENDS.indexOf(r.field) >= 0; });
  }

  function profileTrends(body) {
    return historyRows(body).filter(function (r) { return LIFESKILL_TRENDS.indexOf(r.field) < 0; });
  }

  Object.assign(K, { TRACK_KINDS: TRACK_KINDS, ID: ID, SEED_ID: SEED_ID, STEPS_MAX: STEPS_MAX,
    LEVEL_MAX: LEVEL_MAX, LEVEL: LEVEL, STAT: STAT, gsTotal: gsTotal, bracketLine: bracketLine,
    bracketLines: bracketLines, seedOptions: seedOptions, gateChips: gateChips,
    trackPct: trackPct, withStep: withStep, OBJ_KINDS: OBJ_KINDS, GEAR_TARGET: GEAR_TARGET,
    REWARD_MAX: REWARD_MAX, GEAR_GRADES: GEAR_GRADES, NEXT_OPEN: NEXT_OPEN,
    fmtTarget: fmtTarget, seasonGroups: seasonGroups, seasonLine: seasonLine,
    validReward: validReward, validObjFields: validObjFields, validObjRef: validObjRef,
    parseObjectiveForm: parseObjectiveForm, targetInput: targetInput,
    parseObjectiveAdd: parseObjectiveAdd, inRange: inRange, validCharacter: validCharacter,
    validProgressBody: validProgressBody, wholeIn: wholeIn,
    parseCharacterForm: parseCharacterForm, parseTrackForm: parseTrackForm,
    signalRow: signalRow, profileSignalOff: profileSignalOff, profilePill: profilePill,
    plainText: plainText, profileRows: profileRows, HISTORY_FIELDS: HISTORY_FIELDS,
    PROFILE_HISTORY_DAYS: PROFILE_HISTORY_DAYS, PROFILE_HISTORY_PATH: PROFILE_HISTORY_PATH,
    historyRows: historyRows, PRIVACY_HIDDEN: PRIVACY_HIDDEN,
    LIFESKILL_TRENDS: LIFESKILL_TRENDS, lifeskillView: lifeskillView,
    lifeskillTrends: lifeskillTrends, profileTrends: profileTrends });
  return function link() {};
});
