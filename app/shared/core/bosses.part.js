/* EW shared pure logic, part `bosses` (plan 107 split of ewcore.js):
    world bosses, boss drift, Mounts.
   Installed in order by ../ewcore.js, which re-exports the public names as
   EWCore. Loaded as a plain script before ewcore.js (window.EWCoreParts) and by
   require() under node --test. No DOM, no Electron. */
(function (root, install) {
  if (typeof module !== 'undefined' && module.exports) module.exports = install;
  else (root.EWCoreParts = root.EWCoreParts || {}).bosses = install;
})(typeof self !== 'undefined' ? self : this, function (K) {
  'use strict';

  // from earlier parts
  const { ISO_DAY, ISO_TS, exact, fmtDuration, inRange, isNum, onlyKeys, plainObject, realDate,
    validAscii } = K;
  // from later parts, bound by link() once every part is installed
  let ladderHit, ladderStep, promptLadder;

  // ---- World bosses (plan 032) ----
  // Formatters over the GET /api/bosses body (plan 031): {next: [{bosses,
  // at_utc, day, despawn_min}], today: {day, remaining, slots}, looted:
  // {<PT day>: [names]}, garmoth: {looted, cap}}. Countdowns run from at_utc,
  // so they stay right between polls; nothing comes from the game client.

  const BOSS_GARMOTH = 'Garmoth';
  const BOSS_NAME_MAX = 40;

  function bossGarmoth(view) {
    const g = plainObject(view) && plainObject(view.garmoth) ? view.garmoth : null;
    return g && isNum(g.looted) && isNum(g.cap) ? g : null;
  }

  function bossLooted(view, day, name) {
    const l = plainObject(view) && plainObject(view.looted) ? view.looted[day] : null;
    return Array.isArray(l) && l.indexOf(name) >= 0;
  }

  // Plan 068: a screenshot near this spawn while logged in -> "probably done -
  // tick?" (one click). A suggestion only; never shown once looted.
  function bossSuggested(view, day, name) {
    const s = plainObject(view) && plainObject(view.suggested) ? view.suggested[day] : null;
    return Array.isArray(s) && s.indexOf(name) >= 0 && !bossLooted(view, day, name);
  }

  // One spawn -> {key, day, at_ms, left_s, left, names: [{name, label, looted}],
  // text, done}; null for junk. Greyed (looted) = ticked on the spawn's PT day,
  // or Garmoth at its weekly cap; done = every name greyed.
  function fmtBossRow(spawn, view, now) {
    if (!plainObject(spawn) || !Array.isArray(spawn.bosses) || !spawn.bosses.length ||
      !spawn.bosses.every(function (b) { return typeof b === 'string' && b; }) ||
      typeof spawn.at_utc !== 'string' || !ISO_TS.test(spawn.at_utc)) return null;
    const at = Date.parse(spawn.at_utc);
    if (!isFinite(at)) return null;
    const day = typeof spawn.day === 'string' ? spawn.day : '';
    const g = bossGarmoth(view);
    const names = spawn.bosses.map(function (b) {
      const gar = b === BOSS_GARMOTH && g;
      return { name: b, label: gar ? b + ' ' + g.looted + '/' + g.cap : b,
        looted: bossLooted(view, day, b) || !!(gar && g.looted >= g.cap) };
    });
    const leftMs = at - now;
    return { key: spawn.at_utc, day: day, at_ms: at, left_s: Math.floor(leftMs / 1000), left: fmtDuration(leftMs),
      names: names, text: names.map(function (n) { return n.label; }).join(' + '),
      done: names.every(function (n) { return n.looted; }) };
  }

  // The next n spawns still ahead of `now`: once one passes, the following one
  // leads (the server list refreshes on the next poll).
  function bossRows(view, now, n) {
    const next = plainObject(view) && Array.isArray(view.next) ? view.next : [];
    return next.map(function (s) { return fmtBossRow(s, view, now); })
      .filter(function (r) { return r && r.at_ms > now; })
      .sort(function (a, b) { return a.at_ms - b.at_ms; })
      .slice(0, n);
  }

  // Tick targets: each boss that has already spawned today (PT), once, in
  // spawn order. today.slots (plan 032) keeps despawned ones; an older server
  // only has `remaining`, of which the `up` rows count.
  function bossTicks(view, now) {
    const t = plainObject(view) && plainObject(view.today) ? view.today : null;
    if (!t) return [];
    const src = Array.isArray(t.slots) ? t.slots : (Array.isArray(t.remaining) ? t.remaining : []);
    const seen = {};
    const out = [];
    src.forEach(function (s) {
      const at = plainObject(s) && typeof s.at_utc === 'string' ? Date.parse(s.at_utc) : NaN;
      const spawned = Array.isArray(t.slots) ? isFinite(at) && at <= now : s && s.up === true;
      if (!spawned || !Array.isArray(s.bosses) || typeof s.day !== 'string') return;
      s.bosses.forEach(function (b) {
        if (!validAscii(b, BOSS_NAME_MAX) || seen[b]) return;
        seen[b] = true;
        out.push({ name: b, day: s.day, looted: bossLooted(view, s.day, b) });
      });
    });
    return out;
  }

  function bossGarmothText(view) {
    const g = bossGarmoth(view);
    return g ? BOSS_GARMOTH + ' ' + g.looted + '/' + g.cap + ' this week' : '';
  }

  // Plan 072: GET /api/bosses `drift` (or /api/state sources.bossdrift, which
  // carries `status` and no diff) -> {text, lines} only while the public table
  // differs from EW's; ok / unknown / malformed = null (no banner).
  const BOSS_DRIFT_MAX_LINES = 8;
  function bossDriftBanner(drift) {
    if (!plainObject(drift)) return null;
    const state = typeof drift.state === 'string' ? drift.state : drift.status;
    if (state !== 'differs' || typeof drift.banner !== 'string' || !drift.banner) return null;
    const lines = (Array.isArray(drift.diff) ? drift.diff : []).filter(function (d) {
      return plainObject(d) && typeof d.text === 'string' && d.text;
    }).map(function (d) { return d.text; }).slice(0, BOSS_DRIFT_MAX_LINES);
    return { text: drift.banner, lines: lines };
  }

  // POST /api/bosses body: exactly {tick|untick: {boss, day}}.
  function validBossesBody(body) {
    if (!plainObject(body)) return false;
    const keys = Object.keys(body);
    if (keys.length !== 1 || (keys[0] !== 'tick' && keys[0] !== 'untick')) return false;
    const v = body[keys[0]];
    if (!exact(v, ['boss', 'day']) || !validAscii(v.boss, BOSS_NAME_MAX)) return false;
    const m = typeof v.day === 'string' ? ISO_DAY.exec(v.day) : null;
    return !!m && realDate(m[1], m[2], m[3]);
  }

  // Notify rule bossSoon: one hit per alert-ladder step (plan 070: 15 / 5 / 1
  // min, from GET /api/prompts ladder_min) before each spawn that is not already all
  // looted. A state rule (fires on a baseline); the key carries the step so
  // the ledger lets each fire once.
  function bossHits(prev, next, now) {
    const steps = promptLadder(next.prompts);
    return bossRows(next.bosses, now, 3).filter(function (r) { return !r.done; }).map(function (r) {
      const mins = ladderStep(r.at_ms - now, steps);
      if (mins === null) return null;
      return ladderHit('bossSoon:' + r.key + ':' + mins, 'World boss in ' + mins + 'm: ' + r.text,
        r.text + ' spawns in ' + r.left);
    }).filter(Boolean);
  }

  // ---- Mounts (plan 044) ----
  // Formatters over GET /api/mounts: {mounts: [{id, name, kind, tier, level,
  // gender, skills}], materials: [{key, name, have, need, done}], fern: {have,
  // need, left, per_day, days}, failures, odds: {next_pct, expected, by: {50,
  // 90, 99}, guaranteed_in}, t10, unlocks, kinds, data_error}. Operator-typed
  // data plus a sourced rules file; nothing comes from the game.

  const MOUNT_KINDS = ['horse', 'donkey', 'camel', 'elephant'];
  const MOUNT_GENDERS = ['male', 'female'];
  const MOUNT_ID = /^[a-z0-9-]{1,40}$/;
  const MOUNT_MAT_KEY = /^[a-z][a-z_]{0,39}$/;
  const MOUNT_NAME_MAX = 40;
  const MOUNT_SKILLS_MAX = 40;
  const MOUNT_LEVEL = [1, 30];
  const MOUNT_TIER = [1, 10];
  const MOUNT_MAT = [0, 99999];
  const MOUNT_FAILS = [0, 1000];
  const MOUNT_RATE_MAX = 100;
  const MOUNT_FIELDS = ['name', 'kind', 'tier', 'level', 'gender', 'skills'];

  function validMountFields(v, required) {
    if (!plainObject(v) || !onlyKeys(v, MOUNT_FIELDS)) return false;
    if (!required.every(function (k) { return v[k] !== undefined; })) return false;
    if (v.name !== undefined && !validAscii(v.name, MOUNT_NAME_MAX)) return false;
    if (v.kind !== undefined && MOUNT_KINDS.indexOf(v.kind) < 0) return false;
    if (v.tier !== undefined && v.tier !== null && !inRange(v.tier, MOUNT_TIER)) return false;
    if (v.level !== undefined && !inRange(v.level, MOUNT_LEVEL)) return false;
    if (v.gender !== undefined && v.gender !== null && MOUNT_GENDERS.indexOf(v.gender) < 0) return false;
    if (v.skills !== undefined && !(Array.isArray(v.skills) && v.skills.length <= MOUNT_SKILLS_MAX &&
      v.skills.every(function (s) { return validAscii(s, MOUNT_NAME_MAX); }))) return false;
    return true;
  }

  // POST /api/mounts body: exactly one of add|edit|delete|materials|fern_rate|failures.
  function validMountsBody(body) {
    if (!plainObject(body)) return false;
    const keys = Object.keys(body);
    if (keys.length !== 1) return false;
    const v = body[keys[0]];
    switch (keys[0]) {
      case 'add': return validMountFields(v, ['name', 'kind']);
      case 'edit': {
        if (!plainObject(v) || typeof v.id !== 'string' || !MOUNT_ID.test(v.id)) return false;
        const rest = Object.assign({}, v);
        delete rest.id;
        return Object.keys(rest).length > 0 && validMountFields(rest, []);
      }
      case 'delete': return typeof v === 'string' && MOUNT_ID.test(v);
      case 'materials': {
        if (!plainObject(v)) return false;
        const k = Object.keys(v);
        return k.length > 0 && k.length <= 8 && k.every(function (x) {
          return MOUNT_MAT_KEY.test(x) && inRange(v[x], MOUNT_MAT);
        });
      }
      case 'fern_rate': return v === null || (isNum(v) && v > 0 && v <= MOUNT_RATE_MAX);
      case 'failures': return inRange(v, MOUNT_FAILS);
      default: return false;
    }
  }

  // "Snow - horse T9 Lv 27 F" (skills listed separately).
  function mountLabel(m) {
    if (!plainObject(m) || typeof m.name !== 'string') return '';
    const parts = [m.name, '-', MOUNT_KINDS.indexOf(m.kind) >= 0 ? m.kind : '?'];
    if (inRange(m.tier, MOUNT_TIER)) parts.push('T' + m.tier);
    if (inRange(m.level, MOUNT_LEVEL)) parts.push('Lv ' + m.level);
    if (m.gender === 'male') parts.push('M');
    if (m.gender === 'female') parts.push('F');
    return parts.join(' ');
  }

  // "materials 63/100 fern roots, ~10 days at 4/day"; "" when no fern row.
  function mountsFernLine(view) {
    const f = plainObject(view) && plainObject(view.fern) ? view.fern : null;
    if (!f || !isNum(f.have) || !isNum(f.need)) return '';
    const head = 'materials ' + f.have + '/' + f.need + ' fern roots';
    if (f.left === 0) return head + ', done';
    if (!isNum(f.per_day) || !isNum(f.days)) return head + ', type a daily rate for days to go';
    return head + ', ~' + f.days + ' day' + (f.days === 1 ? '' : 's') + ' at ' + f.per_day + '/day';
  }

  // "next try 3.4% | 50% by 21, 90% by 61, 99% by 105 | sure by 486"; "" without odds.
  function mountsOddsLine(view) {
    const o = plainObject(view) && plainObject(view.odds) ? view.odds : null;
    if (!o || !isNum(o.next_pct)) return '';
    const parts = ['next try ' + o.next_pct + '%'];
    const by = plainObject(o.by) ? o.by : {};
    const b = ['50', '90', '99'].filter(function (k) { return isNum(by[k]); })
      .map(function (k) { return k + '% by ' + by[k]; });
    if (b.length) parts.push(b.join(', '));
    if (isNum(o.guaranteed_in)) parts.push('sure by ' + o.guaranteed_in);
    return parts.join(' | ');
  }

  function mountInt(s, r) {
    const t = String(s === undefined || s === null ? '' : s).trim();
    if (!/^\d+$/.test(t)) return null;
    const n = Number(t);
    return n >= r[0] && n <= r[1] ? n : null;
  }

  // Add-mount form strings -> {ok, body: {add}} | {ok: false, error}.
  // Blank tier / gender = unknown (null); blank level = 1; skills comma-separated.
  function parseMountForm(f) {
    const v = plainObject(f) ? f : {};
    const name = String(v.name === undefined ? '' : v.name).trim();
    if (!validAscii(name, MOUNT_NAME_MAX)) return { ok: false, error: 'name: 1-40 plain characters' };
    const kind = MOUNT_KINDS.indexOf(v.kind) >= 0 ? v.kind : null;
    if (!kind) return { ok: false, error: 'pick a kind' };
    const add = { name: name, kind: kind };
    const tierS = String(v.tier === undefined ? '' : v.tier).trim();
    if (tierS) {
      const t = mountInt(tierS, MOUNT_TIER);
      if (t === null) return { ok: false, error: 'tier: 1-10 or blank' };
      add.tier = t;
    }
    const lvS = String(v.level === undefined ? '' : v.level).trim();
    if (lvS) {
      const l = mountInt(lvS, MOUNT_LEVEL);
      if (l === null) return { ok: false, error: 'level: 1-30' };
      add.level = l;
    }
    if (v.gender) {
      if (MOUNT_GENDERS.indexOf(v.gender) < 0) return { ok: false, error: 'gender: male, female or blank' };
      add.gender = v.gender;
    }
    const skills = String(v.skills === undefined ? '' : v.skills).split(',')
      .map(function (s) { return s.trim(); }).filter(Boolean);
    if (skills.length > MOUNT_SKILLS_MAX || !skills.every(function (s) { return validAscii(s, MOUNT_NAME_MAX); })) {
      return { ok: false, error: 'skills: up to 40 comma-separated names' };
    }
    if (skills.length) add.skills = skills;
    return { ok: true, body: { add: add } };
  }

  // Materials form {key: string} -> {ok, body: {materials}}; blank keys are skipped.
  function parseMaterialsForm(f) {
    const v = plainObject(f) ? f : {};
    const out = {};
    const keys = Object.keys(v).filter(function (k) { return MOUNT_MAT_KEY.test(k); });
    for (let i = 0; i < keys.length; i++) {
      const s = String(v[keys[i]] === undefined ? '' : v[keys[i]]).trim();
      if (!s) continue;
      const n = mountInt(s, MOUNT_MAT);
      if (n === null) return { ok: false, error: keys[i].replace(/_/g, ' ') + ': whole number 0-99999' };
      out[keys[i]] = n;
    }
    if (!Object.keys(out).length) return { ok: false, error: 'type at least one count' };
    return { ok: true, body: { materials: out } };
  }

  // Fern roots per day: blank = clear (null); else a number in (0, 100].
  function parseFernRate(s) {
    const t = String(s === undefined || s === null ? '' : s).trim();
    if (!t) return { ok: true, body: { fern_rate: null } };
    if (!/^\d+(\.\d{1,2})?$/.test(t) || !(Number(t) > 0 && Number(t) <= MOUNT_RATE_MAX)) {
      return { ok: false, error: 'fern roots/day: a number above 0, up to 100' };
    }
    return { ok: true, body: { fern_rate: Number(t) } };
  }

  Object.assign(K, { BOSS_GARMOTH: BOSS_GARMOTH, BOSS_NAME_MAX: BOSS_NAME_MAX,
    bossGarmoth: bossGarmoth, bossLooted: bossLooted, bossSuggested: bossSuggested,
    fmtBossRow: fmtBossRow, bossRows: bossRows, bossTicks: bossTicks,
    bossGarmothText: bossGarmothText, BOSS_DRIFT_MAX_LINES: BOSS_DRIFT_MAX_LINES,
    bossDriftBanner: bossDriftBanner, validBossesBody: validBossesBody, bossHits: bossHits,
    MOUNT_KINDS: MOUNT_KINDS, MOUNT_GENDERS: MOUNT_GENDERS, MOUNT_ID: MOUNT_ID,
    MOUNT_MAT_KEY: MOUNT_MAT_KEY, MOUNT_NAME_MAX: MOUNT_NAME_MAX,
    MOUNT_SKILLS_MAX: MOUNT_SKILLS_MAX, MOUNT_LEVEL: MOUNT_LEVEL, MOUNT_TIER: MOUNT_TIER,
    MOUNT_MAT: MOUNT_MAT, MOUNT_FAILS: MOUNT_FAILS, MOUNT_RATE_MAX: MOUNT_RATE_MAX,
    MOUNT_FIELDS: MOUNT_FIELDS, validMountFields: validMountFields,
    validMountsBody: validMountsBody, mountLabel: mountLabel, mountsFernLine: mountsFernLine,
    mountsOddsLine: mountsOddsLine, mountInt: mountInt, parseMountForm: parseMountForm,
    parseMaterialsForm: parseMaterialsForm, parseFernRate: parseFernRate });
  return function link() {
    ladderHit = K.ladderHit;
    ladderStep = K.ladderStep;
    promptLadder = K.promptLadder;
  };
});
