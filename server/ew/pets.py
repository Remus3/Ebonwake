"""Pet roster (plan 043): 5 out, tier, talents, Alpha, special-skill coverage and
the exchange planner.

The roster is operator-typed (store domain `pets`); species skills, feed values,
the T5 Alpha rule and the exchange rule are tracked sourced data
(`data/pets.json`, BDFoundry pets guide + official T5 wiki, via research 0003
section 1.4). Nothing is read from the game. A broken data file never breaks
the card: the view carries `error` and every write is refused.
"""

import datetime as _dt
import json
import re
import threading
import time
from pathlib import Path

from .today import slug

DATA_FILE = Path(__file__).resolve().parent / "data" / "pets.json"
ID_RE = re.compile(r"^[a-z0-9-]{1,40}$")
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIERS = (1, 5)
MAX_NAME = 40
MAX_TALENTS = 5
MAX_TALENT = 40
MAX_ROSTER = 40
NO_TYPE = "other"  # operator catch-all species: no skill, no exchange type
ROW_FIELDS = ("name", "species", "tier", "talents", "alpha", "out")
SOURCED = ("skills", "feeds", "alpha", "exchange")


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _ascii(v, max_len):
    return (isinstance(v, str) and 0 < len(v.strip()) and len(v) <= max_len
            and all(32 <= ord(ch) <= 126 for ch in v))


def _iso(ts):
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).replace(microsecond=0).isoformat()


def _parse_iso(s):
    if not isinstance(s, str):
        return None
    try:
        when = _dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    return when if when.tzinfo is not None else None


# -- data file -------------------------------------------------------------------

def _sourced(doc, key):
    sec = doc.get(key)
    if not (isinstance(sec, dict) and _ascii(sec.get("source"), 200)
            and sec["source"].startswith("https://")
            and isinstance(sec.get("verified"), str) and DATE_RE.match(sec["verified"])):
        raise ValueError(f"pets data: {key} needs an https source and a verified date")
    try:
        _dt.date.fromisoformat(sec["verified"])
    except ValueError:
        raise ValueError(f"pets data: {key} verified is not a date") from None
    return sec


def validate_data(doc):
    """The tracked pets doc, checked; raises ValueError."""
    if not isinstance(doc, dict):
        raise ValueError("pets data must be an object")
    if not _is_int(doc.get("max_out")) or not 1 <= doc["max_out"] <= 10:
        raise ValueError("pets data: max_out must be an int 1..10")
    for key in SOURCED:
        _sourced(doc, key)
    names = doc["skills"].get("names")
    if not (isinstance(names, dict) and names and all(
            isinstance(k, str) and KEY_RE.match(k) and _ascii(v, 60) for k, v in names.items())):
        raise ValueError("pets data: skills.names must map skill ids to names")
    species, seen = doc.get("species"), set()
    if not isinstance(species, list) or not species:
        raise ValueError("pets data: species must be a non-empty list")
    for s in species:
        if not (isinstance(s, dict) and set(s) == {"id", "name", "skills"}
                and isinstance(s["id"], str) and KEY_RE.match(s["id"]) and s["id"] not in seen
                and _ascii(s["name"], MAX_NAME) and isinstance(s["skills"], list)
                and all(k in names for k in s["skills"])):
            raise ValueError(f"pets data: bad species row {s!r}"[:200])
        seen.add(s["id"])
    goals, gseen = doc.get("goals"), set()
    if not isinstance(goals, list):
        raise ValueError("pets data: goals must be a list")
    for g in goals:
        if not (isinstance(g, dict) and set(g) == {"id", "title", "skills"}
                and isinstance(g["id"], str) and KEY_RE.match(g["id"]) and g["id"] not in gseen
                and g["id"] != "loot" and _ascii(g["title"], MAX_NAME)
                and isinstance(g["skills"], list) and g["skills"]
                and all(k in names for k in g["skills"])):
            raise ValueError(f"pets data: bad goal row {g!r}"[:200])
        gseen.add(g["id"])
    items = doc["feeds"].get("items")
    if not (isinstance(items, list) and items and all(
            isinstance(f, dict) and set(f) == {"id", "name", "hunger"}
            and isinstance(f["id"], str) and KEY_RE.match(f["id"]) and _ascii(f["name"], MAX_NAME)
            and _is_int(f["hunger"]) and f["hunger"] > 0 for f in items)):
        raise ValueError("pets data: feeds.items must be [{id, name, hunger}]")
    a = doc["alpha"]
    if not (_ascii(a.get("text"), 600) and _is_int(a.get("loot_bonus_pct"))
            and 0 <= a["loot_bonus_pct"] <= 100):
        raise ValueError("pets data: alpha needs text and loot_bonus_pct")
    ex = doc["exchange"]
    if not (_ascii(ex.get("text"), 600) and all(
            _is_int(ex.get(k)) for k in ("target_tier", "max_pets", "full_chance_pets",
                                         "full_chance_pct"))
            and TIERS[0] < ex["target_tier"] <= TIERS[1] and 2 <= ex["max_pets"] <= 10
            and 2 <= ex["full_chance_pets"] <= ex["max_pets"]
            and 0 < ex["full_chance_pct"] <= 100
            and isinstance(ex.get("cross_species"), list)):
        raise ValueError("pets data: exchange rule malformed")
    return doc


def load_data(path=None):
    """Tracked `data/pets.json` -> validated doc; raises ValueError."""
    try:
        doc = json.loads(Path(path or DATA_FILE).read_text(encoding="ascii"))
    except (OSError, ValueError) as e:
        raise ValueError(f"pets data unreadable: {type(e).__name__}") from e
    return validate_data(doc)


_EMPTY = {"max_out": 5, "species": [], "goals": [], "skills": {"names": {}},
          "feeds": {"items": [], "source": None, "verified": None},
          "alpha": {"text": "", "loot_bonus_pct": 0, "source": None, "verified": None},
          "exchange": {"text": "", "target_tier": 4, "max_pets": 5, "full_chance_pets": 5,
                       "full_chance_pct": 100, "cross_species": [], "source": None,
                       "verified": None}}


# -- service ---------------------------------------------------------------------

class PetService:
    """Store domain `pets`: {"roster": [{id, name, species, tier 1-5, talents,
    alpha, out, fed_at}], "goals": [goal ids], "updated"}. Invariants on every
    write: at most `max_out` out, at most one alpha, alpha only on T5."""

    def __init__(self, store, clock=time.time, data_path=None):
        self.store = store
        self.clock = clock
        try:
            self.data, self.error = load_data(data_path), None
        except ValueError as e:
            self.data, self.error = _EMPTY, str(e)
        self.species = {s["id"]: s for s in self.data["species"]}
        self.goal_rows = {g["id"]: g for g in self.data["goals"]}
        self.skill_names = self.data["skills"]["names"]
        self._lock = threading.Lock()

    # -- load / clean --------------------------------------------------------------

    def _clean_row(self, r):
        if not (isinstance(r, dict) and isinstance(r.get("id"), str) and ID_RE.match(r["id"])
                and _ascii(r.get("name"), MAX_NAME) and r.get("species") in self.species
                and _is_int(r.get("tier")) and TIERS[0] <= r["tier"] <= TIERS[1]):
            return None
        talents = r.get("talents")
        talents = [t for t in talents if _ascii(t, MAX_TALENT)][:MAX_TALENTS] \
            if isinstance(talents, list) else []
        fed = _parse_iso(r.get("fed_at"))
        return {"id": r["id"], "name": r["name"].strip(), "species": r["species"],
                "tier": r["tier"], "talents": talents,
                "alpha": r.get("alpha") is True and r["tier"] == TIERS[1],
                "out": r.get("out") is True, "fed_at": r["fed_at"] if fed else None}

    def _load(self):
        doc = self.store.get("pets")
        raw = doc.get("roster")
        rows, seen, outs, alpha = [], set(), 0, False
        for r in raw if isinstance(raw, list) else []:
            c = self._clean_row(r)
            if c is None or c["id"] in seen or len(rows) >= MAX_ROSTER:
                continue
            seen.add(c["id"])
            if c["out"]:  # a hand-edited store never breaks the invariants
                outs += 1
                c["out"] = outs <= self.data["max_out"]
            if c["alpha"]:
                c["alpha"], alpha = not alpha, True
            rows.append(c)
        goals = doc.get("goals")
        goals = [g for g in dict.fromkeys(goals if isinstance(goals, list) else [])
                 if isinstance(g, str) and g in self.goal_rows]
        return rows, goals

    def _save(self, rows, goals):
        self.store.put("pets", {"roster": rows, "goals": goals, "updated": _iso(self.clock())})

    def _writable(self):
        if self.error:
            raise ValueError(f"pets data unavailable: {self.error}")

    @staticmethod
    def _find(rows, pid):
        if not isinstance(pid, str) or not ID_RE.match(pid):
            raise ValueError("pet id must match ^[a-z0-9-]{1,40}$")
        for r in rows:
            if r["id"] == pid:
                return r
        raise ValueError(f"unknown pet: {pid}")

    # -- validation ------------------------------------------------------------------

    def _fields(self, arg, required):
        """Validated {name?, species?, tier?, talents?, alpha?, out?}."""
        out = {}
        if "name" in arg:
            if not _ascii(arg["name"], MAX_NAME):
                raise ValueError(f"name must be 1..{MAX_NAME} ASCII characters")
            out["name"] = arg["name"].strip()
        if "species" in arg:
            if arg["species"] not in self.species:
                raise ValueError(f"species must be one of {', '.join(self.species)}")
            out["species"] = arg["species"]
        if "tier" in arg:
            if not _is_int(arg["tier"]) or not TIERS[0] <= arg["tier"] <= TIERS[1]:
                raise ValueError(f"tier must be an int {TIERS[0]}..{TIERS[1]}")
            out["tier"] = arg["tier"]
        if "talents" in arg:
            t = arg["talents"]
            if not isinstance(t, list) or len(t) > MAX_TALENTS \
                    or not all(_ascii(x, MAX_TALENT) for x in t):
                raise ValueError(f"talents must be a list of up to {MAX_TALENTS} ASCII names "
                                 f"(1..{MAX_TALENT} characters)")
            out["talents"] = [x.strip() for x in t]
        for k in ("alpha", "out"):
            if k in arg:
                if not isinstance(arg[k], bool):
                    raise ValueError(f"{k} must be a bool")
                out[k] = arg[k]
        missing = [k for k in required if k not in out]
        if missing:
            raise ValueError(f"missing field(s): {', '.join(missing)}")
        return out

    def _check(self, rows, pet):
        """The roster invariants with `pet` (new or edited) in place."""
        if pet["alpha"] and pet["tier"] != TIERS[1]:
            raise ValueError("alpha is for a tier 5 (T5) pet only")
        others = [r for r in rows if r["id"] != pet["id"]]
        if pet["alpha"] and any(r["alpha"] for r in others):
            raise ValueError("at most one alpha; clear the current alpha first")
        if pet["out"] and sum(r["out"] for r in others) >= self.data["max_out"]:
            raise ValueError(f"at most {self.data['max_out']} pets out")

    # -- views -----------------------------------------------------------------------

    def _row_view(self, r, now):
        sp = self.species[r["species"]]
        fed = _parse_iso(r["fed_at"])
        return dict(r, species_name=sp["name"], skills=list(sp["skills"]),
                    skill_names=[self.skill_names[k] for k in sp["skills"]],
                    fed_ago_s=max(0, int(now - fed.timestamp())) if fed else None)

    def _coverage(self, rows, goals):
        out = [r for r in rows if r["out"]]
        alpha = next((r for r in rows if r["alpha"]), None)
        alpha_out = alpha is not None and alpha["out"]
        loot = {"covered": bool(out), "pets": len(out),
                "t4_plus": sum(r["tier"] >= 4 for r in out),
                "alpha": alpha["name"] if alpha_out else None,
                "alpha_bonus_pct": self.data["alpha"]["loot_bonus_pct"] if alpha_out else 0}
        gview, missing = [], [] if out else ["loot"]
        for gid in goals:
            g = self.goal_rows[gid]
            by = [r["name"] for r in out
                  if set(self.species[r["species"]]["skills"]) & set(g["skills"])]
            have = {k for r in out for k in self.species[r["species"]]["skills"]}
            miss = [k for k in g["skills"] if k not in have]
            gview.append({"id": gid, "title": g["title"], "skills": list(g["skills"]),
                          "covered": not miss, "by": by, "missing": miss,
                          "missing_names": [self.skill_names[k] for k in miss]})
            if miss:
                missing.append(gid)
        warnings = []
        if alpha is not None and not alpha_out:
            warnings.append(f"alpha {alpha['name']} is not out: no loot bonus")
        return {"out": len(out), "max_out": self.data["max_out"],
                "free_slots": self.data["max_out"] - len(out), "loot": loot, "goals": gview,
                "missing": missing, "warnings": warnings}

    def _plan(self, rows, species):
        """Exchange plan for one species: candidates below the target tier, never
        the alpha; the first `max_pets` in roster order would be destroyed."""
        ex = self.data["exchange"]
        cands = [r for r in rows if r["species"] == species and r["tier"] < ex["target_tier"]
                 and not r["alpha"]]
        use = cands[:ex["max_pets"]] if len(cands) >= 2 else []
        names = [r["name"] for r in use]
        full = len(use) >= ex["full_chance_pets"]
        return {"species": species, "species_name": self.species[species]["name"],
                "target_tier": ex["target_tier"], "candidates": [r["name"] for r in cands],
                "count": len(cands), "use": len(use), "can_exchange": len(cands) >= 2,
                "chance_pct": ex["full_chance_pct"] if full else None,
                "need_for_full": max(0, ex["full_chance_pets"] - len(cands)),
                "destroyed": names, "out_used": [r["name"] for r in use if r["out"]],
                "warning": ("parents destroyed: " + ", ".join(names)) if names else ""}

    def view(self):
        """GET /api/pets body."""
        with self._lock:
            rows, goals = self._load()
        now = self.clock()
        d = self.data
        species = [s for s in d["species"] if s["id"] != NO_TYPE]
        return {"roster": [self._row_view(r, now) for r in rows], "max_out": d["max_out"],
                "goals": goals,
                "goal_options": [dict(g) for g in d["goals"]],
                "species": [dict(s, skill_names=[self.skill_names[k] for k in s["skills"]])
                            for s in d["species"]],
                "skills": dict(self.skill_names),
                "feeds": [dict(f) for f in d["feeds"]["items"]],
                "feeds_source": {"source": d["feeds"]["source"],
                                 "verified": d["feeds"]["verified"]},
                "skills_source": {"source": d["skills"].get("source"),
                                  "verified": d["skills"].get("verified")},
                "alpha_rule": dict(d["alpha"]), "exchange_rule": dict(d["exchange"]),
                "coverage": self._coverage(rows, goals),
                "exchange": [p for p in (self._plan(rows, s["id"]) for s in species)
                             if p["can_exchange"]],
                "error": self.error}

    def exchange(self, species):
        """GET /api/pets?exchange=<species>: the plan for one species."""
        if species not in self.species or species == NO_TYPE:
            raise ValueError("exchange needs a pet type: "
                             + ", ".join(s for s in self.species if s != NO_TYPE))
        with self._lock:
            rows, _ = self._load()
        return self._plan(rows, species)

    # -- writes (each returns the GET body) -------------------------------------------

    def add(self, arg):
        """{name, species, tier, talents?, alpha?, out?}."""
        self._writable()
        if not isinstance(arg, dict) or set(arg) - set(ROW_FIELDS):
            raise ValueError("add must be {name, species, tier, talents?, alpha?, out?}")
        f = self._fields(arg, ("name", "species", "tier"))
        with self._lock:
            rows, goals = self._load()
            if len(rows) >= MAX_ROSTER:
                raise ValueError(f"at most {MAX_ROSTER} pets")
            base = slug(f["name"]) or "pet"
            pid, n, taken = base, 2, {r["id"] for r in rows}
            while pid in taken:
                suffix = f"-{n}"
                pid, n = base[:40 - len(suffix)].rstrip("-") + suffix, n + 1
            pet = {"id": pid, "name": f["name"], "species": f["species"], "tier": f["tier"],
                   "talents": f.get("talents", []), "alpha": f.get("alpha", False),
                   "out": f.get("out", False), "fed_at": None}
            self._check(rows, pet)
            rows.append(pet)
            self._save(rows, goals)
        return self.view()

    def edit(self, arg):
        """{id, name?|species?|tier?|talents?|alpha?|out?}; id and fed_at kept."""
        self._writable()
        if not isinstance(arg, dict) or "id" not in arg or set(arg) - set(ROW_FIELDS) - {"id"} \
                or not set(arg) & set(ROW_FIELDS):
            raise ValueError("edit must be {id, name?|species?|tier?|talents?|alpha?|out?}")
        f = self._fields(arg, ())
        with self._lock:
            rows, goals = self._load()
            pet = self._find(rows, arg["id"])
            new = dict(pet, **f)
            self._check(rows, new)
            pet.update(new)
            self._save(rows, goals)
        return self.view()

    def remove(self, pid):
        self._writable()
        with self._lock:
            rows, goals = self._load()
            rows.remove(self._find(rows, pid))
            self._save(rows, goals)
        return self.view()

    def feed(self, pid):
        """Stamp fed_at now (feeding itself is the operator's act in game)."""
        self._writable()
        with self._lock:
            rows, goals = self._load()
            self._find(rows, pid)["fed_at"] = _iso(self.clock())
            self._save(rows, goals)
        return self.view()

    def set_goals(self, arg):
        """[goal ids] the operator wants covered beyond loot (replaces the set)."""
        self._writable()
        if not isinstance(arg, list) or len(set(map(repr, arg))) != len(arg) \
                or not all(isinstance(g, str) and g in self.goal_rows for g in arg):
            raise ValueError("goals must be a list of distinct ids: " + ", ".join(self.goal_rows))
        with self._lock:
            rows, _ = self._load()
            self._save(rows, list(arg))
        return self.view()
