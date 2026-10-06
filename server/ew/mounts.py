"""Mounts (plan 044): operator-typed horses and other mounts (tier, level,
gender, skills), the Tier 10 breed materials with a Royal Fern Root
days-to-go counter, and the T10 breed pity calculator.

Static rules come from the tracked, sourced `data/mounts.json` (BDFoundry
2026-07-03: success 3 percent base, +0.2 percent per failure). Pity math runs
on exact fractions so "attempts for p" never flips on a float edge. Nothing is
read from the game; a broken data file degrades the card, never the server.
"""

import datetime as _dt
import json
import math
import re
import threading
import time
from fractions import Fraction
from pathlib import Path

from .today import slug

DATA_PATH = Path(__file__).resolve().parent / "data" / "mounts.json"
KINDS = ("horse", "donkey", "camel", "elephant")
GENDERS = ("male", "female")
ID_RE = re.compile(r"^[a-z0-9-]{1,40}$")
KEY_RE = re.compile(r"^[a-z][a-z_]{0,39}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LEVEL_MAX = 30
TIER_RANGE = (1, 10)
MAX_MOUNTS = 40
MAX_NAME = 40
MAX_SKILLS = 40
MAX_SKILL = 40
MAT_MAX = 99999
RATE_MAX = 100
MAX_FAILURES = 1000
TARGETS = ("50", "90", "99")
MOUNT_FIELDS = {"name", "kind", "tier", "level", "gender", "skills"}


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v):
    return (_is_int(v) or isinstance(v, float)) and not isinstance(v, bool) and math.isfinite(v)


def _ascii(v, max_len, allow_empty=False):
    return (isinstance(v, str) and len(v) <= max_len and (allow_empty or v.strip() != "")
            and all(32 <= ord(ch) <= 126 for ch in v))


# -- data file -------------------------------------------------------------------

def _row_ok(r):
    v = r.get("verified")
    date_ok = v is False or (isinstance(v, str) and DATE_RE.match(v) is not None)
    if isinstance(v, str) and date_ok:
        try:
            _dt.date.fromisoformat(v)
        except ValueError:
            date_ok = False
    src = r.get("source")
    return date_ok and _ascii(src, 200) and src.startswith("https://")


def validate_data(doc):
    """The tracked mounts.json, checked; ValueError on any malformed row."""
    if not isinstance(doc, dict):
        raise ValueError("mounts data must be an object")
    kinds = doc.get("kinds")
    if not isinstance(kinds, list) or not kinds:
        raise ValueError("kinds must be a non-empty list")
    seen = set()
    for k in kinds:
        if not isinstance(k, dict) or k.get("kind") not in KINDS or k["kind"] in seen \
                or not _is_int(k.get("max_level")) or not 1 <= k["max_level"] <= LEVEL_MAX:
            raise ValueError(f"bad kind row: {k!r}"[:200])
        seen.add(k["kind"])
    t = doc.get("t10")
    if not isinstance(t, dict):
        raise ValueError("t10 must be an object")
    if not isinstance(t.get("names"), list) or not all(_ascii(n, MAX_NAME) for n in t["names"]):
        raise ValueError("t10.names must be ASCII names")
    if not _is_int(t.get("parent_tier")) or not TIER_RANGE[0] <= t["parent_tier"] <= TIER_RANGE[1] \
            or not _is_int(t.get("parent_level")) or not 1 <= t["parent_level"] <= LEVEL_MAX:
        raise ValueError("t10 parent_tier / parent_level out of range")
    if not _ascii(t.get("parents_note"), 200, allow_empty=True) or not _row_ok(t):
        raise ValueError("t10 parents_note / source / verified invalid")
    mats = t.get("materials")
    if not isinstance(mats, list) or not mats:
        raise ValueError("t10.materials must be a non-empty list")
    keys = set()
    for m in mats:
        if not isinstance(m, dict) or not isinstance(m.get("key"), str) \
                or not KEY_RE.match(m["key"]) or m["key"] in keys:
            raise ValueError(f"bad material key: {m!r}"[:200])
        keys.add(m["key"])
        if not _ascii(m.get("name"), MAX_NAME) or not _is_int(m.get("need")) \
                or not 1 <= m["need"] <= MAT_MAX or not _ascii(m.get("note"), 200, True) \
                or not _row_ok(m):
            raise ValueError(f"material {m['key']}: name / need / note / source / verified invalid")
    pa = t.get("per_attempt")
    if not isinstance(pa, dict) or not _ascii(pa.get("note"), 200, True) or not _row_ok(pa):
        raise ValueError("t10.per_attempt invalid")
    p = t.get("pity")
    if not isinstance(p, dict) or not _row_ok(p) or not _ascii(p.get("note"), 200, True):
        raise ValueError("t10.pity needs note / source / verified")
    base, step, cap = p.get("base_pct"), p.get("per_fail_pct"), p.get("cap_pct")
    if not all(_is_num(x) for x in (base, step, cap)) or not 0 < base <= cap <= 100 \
            or not 0 < step <= 100:
        raise ValueError("t10.pity needs 0 < base_pct <= cap_pct <= 100 and per_fail_pct > 0")
    unlocks = doc.get("unlocks")
    if not isinstance(unlocks, list):
        raise ValueError("unlocks must be a list")
    for u in unlocks:
        if not isinstance(u, dict) or u.get("kind") not in KINDS or not _ascii(u.get("name"), MAX_NAME) \
                or not _is_int(u.get("min_level")) or not 1 <= u["min_level"] <= 99 \
                or not _ascii(u.get("how"), 200) or not _row_ok(u):
            raise ValueError(f"bad unlock row: {u!r}"[:200])
    return doc


def load_data(path=None):
    p = Path(DATA_PATH if path is None else path)
    try:
        doc = json.loads(p.read_text(encoding="ascii"))
    except (OSError, ValueError) as e:  # ValueError covers UnicodeDecodeError
        raise ValueError(f"{p.name}: {e}"[:200]) from e
    return validate_data(doc)


# -- pity math ------------------------------------------------------------------

def _pity(pity):
    """(base, step, cap) as exact fractions of 1."""
    def frac(v):
        return Fraction(str(v)) / 100
    return frac(pity["base_pct"]), frac(pity["per_fail_pct"]), frac(pity["cap_pct"])


def _check_failures(f):
    if not _is_int(f) or f < 0:
        raise ValueError("failures must be an int >= 0")
    return f


def _chance(failures, pity):
    base, step, cap = _pity(pity)
    return min(cap, base + step * failures)


def breed_odds(failures, pity):
    """Chance (0..1) that the next T10 breed attempt succeeds after `failures`."""
    return float(_chance(_check_failures(failures), pity))


def _cum_frac(failures, attempts, pity):
    miss = Fraction(1)
    for i in range(attempts):
        miss *= 1 - _chance(failures + i, pity)
    return 1 - miss


def cumulative(failures, attempts, pity):
    """Chance of at least one success within the next `attempts` tries."""
    _check_failures(failures)
    if not _is_int(attempts) or attempts < 0:
        raise ValueError("attempts must be an int >= 0")
    return float(_cum_frac(failures, attempts, pity))


def attempts_for(p_target, failures, pity):
    """Smallest number of further attempts whose success chance reaches p_target."""
    if not _is_num(p_target) or not 0 < p_target <= 1:
        raise ValueError("p_target must be a number in (0, 1]")
    _check_failures(failures)
    target = Fraction(p_target)
    cap = _pity(pity)[2]
    if target == 1 and cap < 1:
        raise ValueError("success is never certain when the pity cap is under 100 percent")
    miss, k = Fraction(1), 0
    while 1 - miss < target:
        miss *= 1 - _chance(failures + k, pity)
        k += 1
    return k


def expected_attempts(failures, pity):
    """Mean number of further attempts to the first success."""
    _check_failures(failures)
    cap = _pity(pity)[2]
    exp, miss, k = Fraction(0), Fraction(1), 0
    while miss > 0:
        p = _chance(failures + k, pity)
        if p >= cap:  # constant from here: geometric tail miss / cap
            exp += miss / cap
            break
        exp += miss
        miss *= 1 - p
        k += 1
    return float(exp)


def _guaranteed_in(failures, pity):
    base, step, cap = _pity(pity)
    if cap < 1:
        return None
    need = (1 - base) / step  # failures at which the chance reaches 100
    return max(0, math.ceil(need) - failures) + 1


def odds_view(failures, pity):
    return {"next_pct": round(breed_odds(failures, pity) * 100, 2),
            "expected": round(expected_attempts(failures, pity), 2),
            "by": {t: attempts_for(int(t) / 100, failures, pity) for t in TARGETS},
            "guaranteed_in": _guaranteed_in(failures, pity)}


# -- store -----------------------------------------------------------------------

def _unique(base, taken):
    if base not in taken:
        return base
    n = 2
    while True:
        suffix = f"-{n}"
        cand = base[:40 - len(suffix)].rstrip("-") + suffix
        if cand not in taken:
            return cand
        n += 1


def _clean_skills(raw):
    out = []
    for s in raw if isinstance(raw, list) else []:
        if _ascii(s, MAX_SKILL) and s.strip() not in out:
            out.append(s.strip())
    return out[:MAX_SKILLS]


def _iso_now(clock):
    return _dt.datetime.fromtimestamp(clock(), _dt.timezone.utc).replace(
        microsecond=0).isoformat()


class MountsService:
    """Store domain `mounts`: {"mounts": [{id, name, kind, tier, level, gender,
    skills}], "materials": {key: count}, "fern_per_day": number | null,
    "failures": int, "updated"}. Every write returns the GET body."""

    def __init__(self, store, clock=time.time, data_path=None):
        self.store = store
        self.clock = clock
        self._lock = threading.Lock()
        try:
            self.data, self.data_error = load_data(data_path), None
        except ValueError as e:  # a broken data file never breaks the card
            self.data, self.data_error = None, str(e)
        caps = {k["kind"]: k["max_level"] for k in self.data["kinds"]} if self.data else {}
        self.caps = {k: caps.get(k, LEVEL_MAX) for k in KINDS}

    # -- load / save -----------------------------------------------------------

    def _clean_mount(self, m):
        if not (isinstance(m, dict) and isinstance(m.get("id"), str) and ID_RE.match(m["id"])
                and _ascii(m.get("name"), MAX_NAME) and m.get("kind") in KINDS):
            return None
        tier, level, gender = m.get("tier"), m.get("level"), m.get("gender")
        return {"id": m["id"], "name": m["name"].strip(), "kind": m["kind"],
                "tier": tier if _is_int(tier) and TIER_RANGE[0] <= tier <= TIER_RANGE[1] else None,
                "level": level if _is_int(level) and 1 <= level <= self.caps[m["kind"]] else 1,
                "gender": gender if gender in GENDERS else None,
                "skills": _clean_skills(m.get("skills"))}

    def _load(self):
        doc = self.store.get("mounts")
        rows, seen = [], set()
        for m in doc.get("mounts") if isinstance(doc.get("mounts"), list) else []:
            c = self._clean_mount(m)
            if c is not None and c["id"] not in seen:
                seen.add(c["id"])
                rows.append(c)
        mats = doc.get("materials") if isinstance(doc.get("materials"), dict) else {}
        mats = {k: v for k, v in mats.items()
                if isinstance(k, str) and KEY_RE.match(k) and _is_int(v) and 0 <= v <= MAT_MAX}
        rate = doc.get("fern_per_day")
        rate = rate if _is_num(rate) and 0 < rate <= RATE_MAX else None
        fails = doc.get("failures")
        fails = fails if _is_int(fails) and 0 <= fails <= MAX_FAILURES else 0
        return {"mounts": rows[:MAX_MOUNTS], "materials": mats, "fern_per_day": rate,
                "failures": fails}

    def _save(self, doc):
        self.store.put("mounts", dict(doc, updated=_iso_now(self.clock)))

    def _write(self, fn):
        with self._lock:
            doc = self._load()
            fn(doc)
            self._save(doc)
        return self.view()

    # -- validation ------------------------------------------------------------

    def _fields(self, arg, required):
        extra = set(arg) - MOUNT_FIELDS
        if extra:
            raise ValueError(f"unknown field(s): {', '.join(sorted(extra))}")
        out = {}
        if "name" in arg:
            if not _ascii(arg["name"], MAX_NAME):
                raise ValueError(f"name must be 1..{MAX_NAME} printable ASCII characters")
            out["name"] = arg["name"].strip()
        if "kind" in arg:
            if arg["kind"] not in KINDS:
                raise ValueError(f"kind must be one of {', '.join(KINDS)}")
            out["kind"] = arg["kind"]
        if "tier" in arg:
            t = arg["tier"]
            if t is not None and (not _is_int(t) or not TIER_RANGE[0] <= t <= TIER_RANGE[1]):
                raise ValueError(f"tier must be null or an int in {TIER_RANGE[0]}..{TIER_RANGE[1]}")
            out["tier"] = t
        if "level" in arg:
            if not _is_int(arg["level"]) or not 1 <= arg["level"] <= LEVEL_MAX:
                raise ValueError(f"level must be an int in 1..{LEVEL_MAX}")
            out["level"] = arg["level"]
        if "gender" in arg:
            if arg["gender"] is not None and arg["gender"] not in GENDERS:
                raise ValueError("gender must be male, female or null")
            out["gender"] = arg["gender"]
        if "skills" in arg:
            s = arg["skills"]
            if not isinstance(s, list) or len(s) > MAX_SKILLS \
                    or not all(_ascii(x, MAX_SKILL) for x in s):
                raise ValueError(f"skills must be a list of up to {MAX_SKILLS} names "
                                 f"(1..{MAX_SKILL} ASCII characters)")
            out["skills"] = _clean_skills(s)
        missing = [k for k in required if k not in out]
        if missing:
            raise ValueError(f"missing field(s): {', '.join(missing)}")
        return out

    def _check_level(self, m):
        cap = self.caps[m["kind"]]
        if m["level"] > cap:
            raise ValueError(f"{m['kind']} level must be 1..{cap}")

    @staticmethod
    def _find(rows, mid):
        if not isinstance(mid, str) or not ID_RE.match(mid):
            raise ValueError("id must match ^[a-z0-9-]{1,40}$")
        for m in rows:
            if m["id"] == mid:
                return m
        raise ValueError(f"unknown mount: {mid}")

    def _need_data(self):
        if self.data is None:
            raise ValueError(f"mount data unavailable: {self.data_error}")
        return self.data

    # -- writes ----------------------------------------------------------------

    def add(self, arg):
        """{name, kind, tier?, level?, gender?, skills?}."""
        if not isinstance(arg, dict):
            raise ValueError("add must be {name, kind, tier?, level?, gender?, skills?}")
        f = self._fields(arg, ("name", "kind"))
        m = dict({"tier": None, "level": 1, "gender": None, "skills": []}, **f)
        self._check_level(m)

        def go(doc):
            if len(doc["mounts"]) >= MAX_MOUNTS:
                raise ValueError(f"at most {MAX_MOUNTS} mounts")
            mid = _unique(slug(m["name"]), {x["id"] for x in doc["mounts"]})
            doc["mounts"].append({"id": mid, "name": m["name"], "kind": m["kind"],
                                  "tier": m["tier"], "level": m["level"],
                                  "gender": m["gender"], "skills": m["skills"]})
        return self._write(go)

    def edit(self, arg):
        """{id, name?|kind?|tier?|level?|gender?|skills?}."""
        if not isinstance(arg, dict) or "id" not in arg or len(arg) < 2:
            raise ValueError("edit must be {id, name?|kind?|tier?|level?|gender?|skills?}")
        f = self._fields({k: v for k, v in arg.items() if k != "id"}, ())

        def go(doc):
            m = self._find(doc["mounts"], arg["id"])
            new = dict(m, **f)
            self._check_level(new)
            m.update(new)
        return self._write(go)

    def delete(self, mid):
        def go(doc):
            doc["mounts"].remove(self._find(doc["mounts"], mid))
        return self._write(go)

    def materials(self, arg):
        """{material key: count, ...}: partial set of the T10 material counts."""
        data = self._need_data()
        keys = [m["key"] for m in data["t10"]["materials"]]
        if not isinstance(arg, dict) or not arg:
            raise ValueError(f"materials must be an object of {', '.join(keys)}")
        for k, v in arg.items():
            if k not in keys:
                raise ValueError(f"unknown material: {k}"[:200])
            if not _is_int(v) or not 0 <= v <= MAT_MAX:
                raise ValueError(f"{k} must be an int in 0..{MAT_MAX}")
        return self._write(lambda doc: doc["materials"].update(arg))

    def fern_rate(self, rate):
        """Royal Fern Roots gained per day (operator's own average), or null."""
        if rate is not None and (not _is_num(rate) or not 0 < rate <= RATE_MAX):
            raise ValueError(f"fern_rate must be null or a number in (0, {RATE_MAX}]")
        val = None if rate is None else round(float(rate), 2)
        if val is not None and val == int(val):
            val = int(val)
        return self._write(lambda doc: doc.update(fern_per_day=val))

    def failures(self, n):
        """Failed T10 breed attempts since the last success (drives the pity)."""
        if not _is_int(n) or not 0 <= n <= MAX_FAILURES:
            raise ValueError(f"failures must be an int in 0..{MAX_FAILURES}")
        return self._write(lambda doc: doc.update(failures=n))

    # -- read ------------------------------------------------------------------

    def view(self):
        with self._lock:
            doc = self._load()
        out = {"mounts": doc["mounts"], "failures": doc["failures"],
               "materials": [], "fern": None, "odds": None, "t10": None,
               "unlocks": [], "kinds": dict(self.caps),
               "data_error": self.data_error, "source": None, "verified": None}
        if self.data is None:
            out["fern"] = {"have": doc["materials"].get("royal_fern_root", 0), "need": None,
                           "left": None, "per_day": doc["fern_per_day"], "days": None}
            return out
        t = self.data["t10"]
        mats = []
        for m in t["materials"]:
            have = doc["materials"].get(m["key"], 0)
            mats.append({"key": m["key"], "name": m["name"], "have": have, "need": m["need"],
                         "done": have >= m["need"], "note": m["note"],
                         "source": m["source"], "verified": m["verified"]})
        out["materials"] = mats
        fern = next((m for m in mats if m["key"] == "royal_fern_root"), None)
        if fern is not None:
            left = max(0, fern["need"] - fern["have"])
            rate = doc["fern_per_day"]
            days = 0 if left == 0 else (math.ceil(left / rate) if rate else None)
            out["fern"] = {"have": fern["have"], "need": fern["need"], "left": left,
                           "per_day": rate, "days": days}
        out["odds"] = odds_view(doc["failures"], t["pity"])
        parents = [m for m in doc["mounts"] if m["kind"] == "horse"
                   and m["tier"] == t["parent_tier"] and m["level"] >= t["parent_level"]]
        genders = {m["gender"] for m in parents}
        out["t10"] = {"names": t["names"], "parent_tier": t["parent_tier"],
                      "parent_level": t["parent_level"], "parents_note": t["parents_note"],
                      "parents": [m["id"] for m in parents if m["gender"] in GENDERS],
                      "ready": {"materials": all(m["done"] for m in mats),
                                "parents": set(GENDERS) <= genders},
                      "per_attempt": t["per_attempt"], "pity": t["pity"]}
        out["unlocks"] = self.data["unlocks"]
        out["source"], out["verified"] = t["source"], t["verified"]
        return out
