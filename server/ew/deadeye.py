"""Deadeye build notes + enhancement plan (plan 007 slice A).

Operator-authored text only: markdown notes per fixed section and an ordered
list of enhancement steps. Nothing here is executed, sent to the game or turned
into input (Game ToS floor); the dashboard renders the markdown as a safe subset.

Plan 036 adds the operator-typed stack inventory beside the plan: stored
failstacks (`fs_bank`), Agris Essence pity stacks (`agris`) and crons on hand
(`crons`), plus advice computed on the plan 035 rate rows: the stored FS for the
next open step closest to its soft cap without exceeding it, "guaranteed in N
fails" per Agris row, and owned vs expected crons for every open step.

Plan 055 adds two read-only calculators over tracked, sourced data files:
`weeks_to_reform` (Jetina weekly boss-crystal exchange, `data/boss_crystal.json`)
and `caphras_cost` (stones per range, `data/caphras.json`, priced from the plan
002 cache for the Caphras Stone; refused on grades the data marks not allowed).
"""

import datetime as _dt
import json
import math
import re
import threading
import time
from pathlib import Path

from . import enhance
from .today import _iso, _parse_iso

SECTIONS = (("addons", "Skill add-ons"), ("crystals", "Crystals"), ("artifacts", "Artifacts"),
            ("lightstones", "Lightstones"), ("rotation", "PvE rotation"), ("misc", "Misc"))
LEVELS = tuple(f"+{n}" for n in range(16)) + ("PRI", "DUO", "TRI", "TET", "PEN")
MAX_TEXT = 20000
MAX_ITEM = 60
MAX_NOTE = 200
MAX_STEPS = 100
STEP_RE = re.compile(r"^d[0-9]{1,9}$")
_SECTION_IDS = {sid for sid, _ in SECTIONS}
# Plan 036: Advice of Valks, a saved (stored) stack, Valks' Cry. Order = tie-break.
FS_KINDS = ("advice", "saved", "cry")
MAX_FS_ROWS = 60
MAX_FS_COUNT = 999
MAX_AGRIS_ROWS = 60
MAX_AGRIS_STACKS = 1000
MAX_CRONS = 10 ** 9
MAX_CRONS_WEEKLY = 10 ** 7


# -- validation ----------------------------------------------------------------

def _fields(arg, what, required, optional=()):
    if (not isinstance(arg, dict) or not set(required) <= set(arg)
            or not set(arg) <= set(required) | set(optional)):
        allowed = list(required) + [f"{k}?" for k in optional]
        raise ValueError(f"{what} must be {{{', '.join(allowed)}}}")
    return arg


def _line(v, name, lo, hi):
    """Single-line text: stripped, lo..hi chars, no control characters."""
    if not isinstance(v, str):
        raise ValueError(f"{name} must be a string")
    v = v.strip()
    if not lo <= len(v) <= hi:
        raise ValueError(f"{name} must be {lo}..{hi} characters")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in v):
        raise ValueError(f"{name} must not contain control characters")
    return v


def _level(v, name):
    if not isinstance(v, str) or v not in LEVELS:
        raise ValueError(f"{name} must be one of {', '.join(LEVELS)}")
    return v


def _span(current, target):
    if LEVELS.index(target) <= LEVELS.index(current):
        raise ValueError("target must be above current")


def _int(v, name, lo, hi):
    if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
        raise ValueError(f"{name} must be an int in {lo}..{hi}")
    return v


def _text(v):
    if not isinstance(v, str):
        raise ValueError("text must be a string")
    v = v.replace("\r\n", "\n")
    if len(v) > MAX_TEXT:
        raise ValueError(f"text must be 0..{MAX_TEXT} characters")
    return v


# -- stored-entry cleaning (corrupt docs degrade, never raise) -----------------

def _iso_or_none(s):
    when = _parse_iso(s)
    if when is None:
        return None
    try:
        return _iso(when)
    except (OverflowError, ValueError):
        return None


def _clean_step(it):
    if not (isinstance(it, dict) and isinstance(it.get("id"), str) and STEP_RE.match(it["id"])
            and isinstance(it.get("item"), str) and it["item"]
            and it.get("current") in LEVELS and it.get("target") in LEVELS
            and LEVELS.index(it["target"]) > LEVELS.index(it["current"])
            and isinstance(it.get("note", ""), str) and isinstance(it.get("done"), bool)):
        return None
    return {"id": it["id"], "item": it["item"], "current": it["current"],
            "target": it["target"], "note": it.get("note", ""), "done": it["done"]}


def _ok_int(v, lo, hi):
    return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


def _clean_bank(raw):
    out, seen = [], set()
    for r in raw if isinstance(raw, list) else []:
        if (isinstance(r, dict) and r.get("kind") in FS_KINDS
                and _ok_int(r.get("value"), 1, enhance.MAX_FS)
                and _ok_int(r.get("count"), 1, MAX_FS_COUNT)
                and (r["kind"], r["value"]) not in seen):
            seen.add((r["kind"], r["value"]))
            out.append({"kind": r["kind"], "value": r["value"], "count": r["count"]})
    return out[:MAX_FS_ROWS]


def _clean_agris(raw):
    out, seen = [], set()
    for r in raw if isinstance(raw, list) else []:
        if (isinstance(r, dict) and isinstance(r.get("family"), str)
                and enhance.FAMILY_RE.fullmatch(r["family"]) and r.get("step") in enhance.STEPS
                and _ok_int(r.get("stacks"), 1, MAX_AGRIS_STACKS)
                and (r["family"], r["step"]) not in seen):
            seen.add((r["family"], r["step"]))
            out.append({"family": r["family"], "step": r["step"], "stacks": r["stacks"]})
    return out[:MAX_AGRIS_ROWS]


def _clean_crons(raw):
    raw = raw if isinstance(raw, dict) else {}
    owned, weekly = raw.get("owned"), raw.get("weekly_income")
    return {"owned": owned if _ok_int(owned, 0, MAX_CRONS) else 0,
            "weekly_income": weekly if _ok_int(weekly, 0, MAX_CRONS_WEEKLY) else 0}


def _family_guess(item, families):
    """Same rule as ewcore.enhanceFamilyGuess: the first family (sorted) whose
    name, underscores as spaces, appears in the item text."""
    low = item.lower()
    for f in families:
        if f.replace("_", " ") in low:
            return f
    return None


def _sub_steps(current, target):
    """Levels a plan step climbs through, each the level one attempt reaches."""
    return LEVELS[LEVELS.index(current) + 1:LEVELS.index(target) + 1]


def _fails_left(threshold, stacks):
    return None if threshold is None else max(0, threshold - stacks)


# -- plan 055 calculators --------------------------------------------------------

DATA_DIR = Path(__file__).resolve().parent / "data"
MAX_CRYSTALS = 10 ** 7
MAX_PER_LEVEL = 10 ** 5
MAX_PRICE = 10 ** 12
_tables = {}


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _sourced(part, what):
    if not (isinstance(part, dict) and isinstance(part.get("source"), str) and part["source"]
            and isinstance(part.get("verified"), bool)):
        raise ValueError(f"{what} needs a non-empty source and a bool verified")
    return part


def validate_crystal(doc):
    """Returns `doc` when the boss-crystal table is well formed, else ValueError."""
    w = _sourced(doc.get("weekly"), "weekly")
    if not (_is_int(w.get("crystals")) and w["crystals"] > 0 and _is_int(w.get("auras"))
            and w["auras"] > 0 and isinstance(w.get("reset_weekday"), str)):
        raise ValueError("weekly must be {crystals > 0, auras > 0, reset_weekday}")
    c = _sourced(doc.get("reform_cost"), "reform_cost")
    if not (_is_int(c.get("min")) and _is_int(c.get("max")) and 0 < c["min"] <= c["max"]):
        raise ValueError("reform_cost must be ints 0 < min <= max")
    if not (_is_int(doc.get("max_levels")) and doc["max_levels"] > 0):
        raise ValueError("max_levels must be an int > 0")
    return doc


def validate_caphras(doc):
    """Returns `doc` when the Caphras table is well formed, else ValueError: every
    slot's ranges run contiguous from 0 to max_level with stones > 0."""
    top = doc.get("max_level")
    if not (_is_int(top) and top > 0 and _is_int(doc.get("item_id"))):
        raise ValueError("max_level and item_id must be ints")
    grades = doc.get("allowed_grades")
    if not isinstance(grades, dict) or not grades:
        raise ValueError("allowed_grades must be a non-empty object")
    for name, g in grades.items():
        if not isinstance(_sourced(g, f"grade {name}").get("allowed"), bool):
            raise ValueError(f"grade {name} needs a bool allowed")
    for s in doc.get("slots") or []:
        if not (isinstance(s, dict) and isinstance(s.get("id"), str)
                and grades.get(s.get("grade"), {}).get("allowed") is True):
            raise ValueError("slot needs an id and an allowed grade")
        at = 0
        for r in s.get("ranges") or []:
            _sourced(r, f"slot {s['id']} range")
            if not (r.get("from") == at and _is_int(r.get("to")) and r["to"] > at
                    and _is_int(r.get("stones")) and r["stones"] > 0):
                raise ValueError(f"slot {s['id']} ranges must be contiguous with stones > 0")
            at = r["to"]
        if at != top:
            raise ValueError(f"slot {s['id']} ranges must end at {top}")
    return doc


def _load(name, check):
    if name not in _tables:
        _tables[name] = check(json.loads((DATA_DIR / name).read_text(encoding="utf-8")))
    return _tables[name]


def load_crystal():
    """Tracked `boss_crystal.json`, read once and validated (a bad file raises)."""
    return _load("boss_crystal.json", validate_crystal)


def load_caphras():
    """Tracked `caphras.json`, read once and validated (a bad file raises)."""
    return _load("caphras.json", validate_caphras)


def weeks_to_reform(crystals_on_hand, target_levels, per_level=None):
    """Weeks of Jetina exchanges (one per Thursday reset) to afford `target_levels`
    reform levels. The sources give a 60-120 band per level, so the answer is a
    min / max band; an operator-typed `per_level` cost makes it exact."""
    t = load_crystal()
    _int(crystals_on_hand, "on_hand", 0, MAX_CRYSTALS)
    _int(target_levels, "levels", 1, t["max_levels"])
    if per_level is None:
        lo, hi = t["reform_cost"]["min"], t["reform_cost"]["max"]
    else:
        lo = hi = _int(per_level, "per_level", 1, MAX_PER_LEVEL)
    weekly = t["weekly"]["crystals"]
    needed = {"min": lo * target_levels, "max": hi * target_levels}
    short = {k: max(0, v - crystals_on_hand) for k, v in needed.items()}
    return {"kind": "crystal", "levels": target_levels, "on_hand": crystals_on_hand,
            "per_level": {"min": lo, "max": hi}, "exact": per_level is not None,
            "needed": needed, "short": short,
            "weeks": {k: -(-v // weekly) for k, v in short.items()},
            "weekly": weekly, "auras_per_week": t["weekly"]["auras"],
            "reset": t["weekly"]["reset_weekday"],
            "verified": per_level is not None or t["reform_cost"]["verified"]}


def caphras_cost(slot, frm, to, price=None, grade=None):
    """Caphras Stones (and silver at `price` each) from level `frm` to `to` on
    `slot`. A range crossed in full is exact; a partial range is prorated
    linearly (`approx`). `grade` (default the slot's) must be allowed."""
    t = load_caphras()
    row = next((s for s in t["slots"] if s["id"] == slot), None)
    if row is None:
        raise ValueError(f"slot must be one of {', '.join(s['id'] for s in t['slots'])}")
    grade = row["grade"] if grade is None else grade
    g = t["allowed_grades"].get(grade)
    if g is None:
        raise ValueError(f"grade must be one of {', '.join(t['allowed_grades'])}")
    if not g["allowed"]:
        raise ValueError(f"Caphras is not usable on {grade.capitalize()} gear")
    top = t["max_level"]
    _int(frm, "from", 0, top)
    _int(to, "to", frm, top)
    if price is not None:
        _int(price, "price", 0, MAX_PRICE)
    stones, approx, verified, parts = 0.0, False, True, []
    for r in row["ranges"]:
        lo, hi = max(frm, r["from"]), min(to, r["to"])
        if hi <= lo:
            continue
        span = r["to"] - r["from"]
        part = r["stones"] * (hi - lo) / span
        approx = approx or (hi - lo) != span
        verified = verified and r["verified"]
        stones += part
        parts.append({"from": lo, "to": hi, "stones": round(part), "verified": r["verified"],
                      "source": r["source"]})
    stones = round(stones)
    return {"kind": "caphras", "slot": slot, "name": row["name"], "grade": grade,
            "from": frm, "to": to, "stones": stones, "approx": approx, "verified": verified,
            "ranges": parts, "item_id": t["item_id"], "price": price,
            "silver": None if price is None else stones * price}


def _q_int(q, key, required=True):
    v = q.get(key)
    v = v[0] if v else None
    if v is None:
        if required:
            raise ValueError(f"{key} is required")
        return None
    if not re.fullmatch(r"[0-9]{1,13}", v):
        raise ValueError(f"{key} must be a non-negative int")
    return int(v)


class DeadeyeService:
    """Store domain `deadeye`: {"notes": {section: {text, updated}}, "plan": [{id,
    item, current, target, note, done}] (operator order), "next_id": int,
    "fs_bank": [{kind, value, count}], "agris": [{family, step, stacks}],
    "crons": {owned, weekly_income}, "updated": "<iso>"}.

    `rates()` returns the effective plan 035 rate rows (the app passes the
    enhance service's merged rows; default the tracked table). `price(item_id)`
    is the plan 002 cache lookup (never fetches) for the plan 055 calculators."""

    def __init__(self, store, clock=time.time, rates=None, price=None):
        self.store = store
        self.clock = clock
        self.rates = rates or (lambda: enhance.load_table()["rows"])
        self.price = price or (lambda item_id: None)
        self._lock = threading.Lock()  # read-modify-write; Store guards each file op
        with self._lock:
            if "notes" not in store.get("deadeye"):
                self._save({"notes": {sid: {"text": "", "updated": None} for sid, _ in SECTIONS},
                            "plan": [], "next_id": 1})

    def _now(self):
        return _dt.datetime.fromtimestamp(self.clock(), _dt.timezone.utc)

    def calc(self, q):
        """GET /api/deadeye/calc (plan 055): parsed query (parse_qs) -> one
        calculator reply, or with no `kind` the tables the card's form needs."""
        kind = (q.get("kind") or [None])[0]
        if kind is None:
            caphras = load_caphras()
            return {"crystal": load_crystal(),
                    "caphras": {"item_id": caphras["item_id"], "max_level": caphras["max_level"],
                                "min_enhance": caphras["min_enhance"],
                                "grades": caphras["allowed_grades"],
                                "slots": [{"id": s["id"], "name": s["name"], "grade": s["grade"]}
                                          for s in caphras["slots"]],
                                "price": self.price(caphras["item_id"])}}
        if kind == "crystal":
            return weeks_to_reform(_q_int(q, "on_hand"), _q_int(q, "levels"),
                                   _q_int(q, "per_level", required=False))
        if kind == "caphras":
            slot = (q.get("slot") or [None])[0]
            grade = (q.get("grade") or [None])[0]
            price, src = _q_int(q, "price", required=False), "operator"
            if price is None:
                price, src = self.price(load_caphras()["item_id"]), "cache"
            out = caphras_cost(slot, _q_int(q, "from"), _q_int(q, "to"),
                               price=price, grade=grade)
            return dict(out, price_source=None if price is None else src)
        raise ValueError("kind must be crystal or caphras")

    def _load(self):
        doc = self.store.get("deadeye")
        raw = doc.get("notes") if isinstance(doc.get("notes"), dict) else {}
        notes = {}
        for sid, _ in SECTIONS:
            n = raw.get(sid)
            if isinstance(n, dict) and isinstance(n.get("text"), str):
                notes[sid] = {"text": n["text"], "updated": _iso_or_none(n.get("updated"))}
            else:
                notes[sid] = {"text": "", "updated": None}
        plan, seen = [], set()
        for c in (_clean_step(i) for i in (doc["plan"] if isinstance(doc.get("plan"), list)
                                           else [])):
            if c is not None and c["id"] not in seen:
                seen.add(c["id"])
                plan.append(c)
        nxt = doc.get("next_id")
        top = max((int(s["id"][1:]) for s in plan), default=0) + 1
        ok = isinstance(nxt, int) and not isinstance(nxt, bool) and 1 <= nxt < 10 ** 9
        return {"notes": notes, "plan": plan, "next_id": max(nxt, top) if ok else top,
                "fs_bank": _clean_bank(doc.get("fs_bank")), "agris": _clean_agris(doc.get("agris")),
                "crons": _clean_crons(doc.get("crons"))}

    def _save(self, doc):
        doc["updated"] = _iso(self._now())
        self.store.put("deadeye", doc)

    @staticmethod
    def _find(doc, sid):
        for i, s in enumerate(doc["plan"]):
            if s["id"] == sid:
                return i
        raise ValueError(f"unknown step: {sid}")

    # -- reads -----------------------------------------------------------------

    @staticmethod
    def _progress(plan):
        return {"done": sum(1 for s in plan if s["done"]), "total": len(plan)}

    def view(self):
        """GET /api/deadeye body."""
        doc = self._load()
        sections = [{"id": sid, "title": title, "text": doc["notes"][sid]["text"],
                     "updated": doc["notes"][sid]["updated"]} for sid, title in SECTIONS]
        plan = [dict(s, steps=LEVELS.index(s["target"]) - LEVELS.index(s["current"]))
                for s in doc["plan"]]
        return {"now": _iso(self._now()), "levels": list(LEVELS), "sections": sections,
                "plan": plan, "progress": self._progress(doc["plan"]), "stacks": self._stacks(doc)}

    # -- plan 036 advice ---------------------------------------------------------

    def _stacks(self, doc):
        rows = {(r["family"], r["step"]): r for r in self.rates()}
        families = sorted({f for f, _ in rows})
        pity = {(a["family"], a["step"]): a["stacks"] for a in doc["agris"]}
        bank = sorted(doc["fs_bank"], key=lambda r: (FS_KINDS.index(r["kind"]), r["value"]))
        agris = [dict(a, threshold=t, fails_to_guarantee=_fails_left(t, a["stacks"]))
                 for a in sorted(doc["agris"], key=lambda a: (a["family"],
                                                              enhance.STEPS.index(a["step"])))
                 for t in [rows.get((a["family"], a["step"]), {}).get("agris_threshold")]]
        open_steps = [s for s in doc["plan"] if not s["done"]]
        return {"fs_bank": bank, "agris": agris, "crons": doc["crons"],
                "advice": self._advice(open_steps[:1], rows, families, pity, bank),
                "budget": self._budget(open_steps, rows, families, pity, doc["crons"])}

    @staticmethod
    def _advice(first, rows, families, pity, bank):
        out = {"step_id": None, "item": None, "family": None, "level": None, "softcap_fs": None,
               "suggest": None, "agris": None, "reason": None}
        if not first:
            return dict(out, reason="no open plan step")
        s = first[0]
        out.update(step_id=s["id"], item=s["item"])
        fam = _family_guess(s["item"], families)
        if fam is None:
            return dict(out, reason="no gear family named in the item")
        out["family"] = fam
        # The first level of the step that has a rate row: an accessory at +0
        # goes straight to PRI because its family has no +1..+15 rows.
        level = next((lv for lv in _sub_steps(s["current"], s["target"]) if (fam, lv) in rows),
                     None)
        if level is None:
            return dict(out, reason=f"no {fam} rate rows for {s['current']} -> {s['target']}")
        row = rows[(fam, level)]
        stacks = pity.get((fam, level), 0)
        t = row.get("agris_threshold")
        out.update(level=level, softcap_fs=row.get("softcap_fs"),
                   agris={"stacks": stacks, "threshold": t,
                          "fails_to_guarantee": _fails_left(t, stacks)})
        soft = row.get("softcap_fs")
        if soft is None:
            return dict(out, reason="no soft cap for this level")
        if not bank:
            return dict(out, reason="no stored stacks")
        under = [b for b in bank if b["value"] <= soft]
        if not under:
            return dict(out, reason=f"every stored stack is above the soft cap ({soft})")
        best = max(b["value"] for b in under)
        pick = next(b for b in under if b["value"] == best)  # bank is in FS_KINDS order
        return dict(out, suggest={"kind": pick["kind"], "value": pick["value"]})

    @staticmethod
    def _budget(open_steps, rows, families, pity, crons):
        """Expected crons for every open step's levels, each attempt at the row's
        soft-cap FS, the Agris threshold reduced by stacks already on hand."""
        lines, unknown = [], []
        for s in open_steps:
            fam = _family_guess(s["item"], families)
            if fam is None:
                continue
            for lv in _sub_steps(s["current"], s["target"]):
                row = rows.get((fam, lv))
                if row is None or not row.get("crons_per_attempt"):
                    continue
                pct = (enhance.chance(row, row["softcap_fs"])["pct"]
                       if row.get("softcap_fs") is not None else None)
                if pct is None:
                    unknown.append({"step_id": s["id"], "family": fam, "level": lv})
                    continue
                t = row.get("agris_threshold")
                if t is not None:
                    t = max(0, t - pity.get((fam, lv), 0))
                a = enhance.attempts(pct / 100, t)
                lines.append({"step_id": s["id"], "family": fam, "level": lv,
                              "fs": row["softcap_fs"], "chance_pct": pct,
                              "crons_per_attempt": row["crons_per_attempt"],
                              "crons_mean": row["crons_per_attempt"] * a["crons_attempts"]})
        needed = sum(ln["crons_mean"] for ln in lines)
        gap = max(0, needed - crons["owned"])
        weekly = crons["weekly_income"]
        weeks = 0 if gap <= 0 else (math.ceil(gap / weekly) if weekly else None)
        return {"needed": needed, "owned": crons["owned"], "gap": gap, "weekly_income": weekly,
                "weeks": weeks, "lines": lines, "unknown": unknown}

    def source(self):
        """`/api/state` sources.deadeye: {done, total}."""
        return self._progress(self._load()["plan"])

    # -- writes (each returns the GET body) -----------------------------------

    def note(self, arg):
        """`{section, text}`: replaces that section's markdown, stamps updated."""
        arg = _fields(arg, "note", ("section", "text"))
        if not isinstance(arg["section"], str) or arg["section"] not in _SECTION_IDS:
            raise ValueError(f"unknown section: {arg['section']}")
        text = _text(arg["text"])
        with self._lock:
            doc = self._load()
            doc["notes"][arg["section"]] = {"text": text, "updated": _iso(self._now())}
            self._save(doc)
        return self.view()

    def add_step(self, arg):
        """`{item, current, target, note?}` -> appended step `d<N>` (never reused)."""
        arg = _fields(arg, "add_step", ("item", "current", "target"), ("note",))
        item = _line(arg["item"], "item", 1, MAX_ITEM)
        current = _level(arg["current"], "current")
        target = _level(arg["target"], "target")
        _span(current, target)
        note = _line(arg.get("note", ""), "note", 0, MAX_NOTE)
        with self._lock:
            doc = self._load()
            if len(doc["plan"]) >= MAX_STEPS:
                raise ValueError(f"at most {MAX_STEPS} steps")
            doc["plan"].append({"id": f"d{doc['next_id']}", "item": item, "current": current,
                                "target": target, "note": note, "done": False})
            doc["next_id"] += 1
            self._save(doc)
        return self.view()

    def edit_step(self, arg):
        """`{id, item?, current?, target?, note?}` (at least one); the merged step
        must still have target above current."""
        arg = _fields(arg, "edit_step", ("id",), ("item", "current", "target", "note"))
        if len(arg) < 2:
            raise ValueError("edit_step needs at least one of item, current, target, note")
        new = {}
        if "item" in arg:
            new["item"] = _line(arg["item"], "item", 1, MAX_ITEM)
        if "note" in arg:
            new["note"] = _line(arg["note"], "note", 0, MAX_NOTE)
        for k in ("current", "target"):
            if k in arg:
                new[k] = _level(arg[k], k)
        with self._lock:
            doc = self._load()
            i = self._find(doc, arg["id"])
            step = dict(doc["plan"][i], **new)
            _span(step["current"], step["target"])
            doc["plan"][i] = step
            self._save(doc)
        return self.view()

    def step_done(self, arg):
        """`{id, done: bool}`."""
        arg = _fields(arg, "step_done", ("id", "done"))
        if not isinstance(arg["done"], bool):
            raise ValueError("done must be true or false")
        with self._lock:
            doc = self._load()
            doc["plan"][self._find(doc, arg["id"])]["done"] = arg["done"]
            self._save(doc)
        return self.view()

    def delete_step(self, sid):
        with self._lock:
            doc = self._load()
            del doc["plan"][self._find(doc, sid)]
            self._save(doc)
        return self.view()

    def move_step(self, arg):
        """`{id, dir}`, dir -1 (up) or 1 (down); past either end is a no-op."""
        arg = _fields(arg, "move_step", ("id", "dir"))
        d = arg["dir"]
        if not isinstance(d, int) or isinstance(d, bool) or d not in (-1, 1):
            raise ValueError("dir must be -1 or 1")
        with self._lock:
            doc = self._load()
            i = self._find(doc, arg["id"])
            j = i + d
            if 0 <= j < len(doc["plan"]):
                plan = doc["plan"]
                plan[i], plan[j] = plan[j], plan[i]
                self._save(doc)
        return self.view()

    # -- plan 036 writes ---------------------------------------------------------

    @staticmethod
    def _fs_arg(arg, op):
        arg = _fields(arg, op, ("kind", "value"), ("count",))
        if arg["kind"] not in FS_KINDS:
            raise ValueError(f"kind must be one of {', '.join(FS_KINDS)}")
        return (arg["kind"], _int(arg["value"], "value", 1, enhance.MAX_FS),
                _int(arg.get("count", 1), "count", 1, MAX_FS_COUNT))

    def fs_add(self, arg):
        """`{kind, value, count?=1}`: adds to the matching (kind, value) row."""
        kind, value, n = self._fs_arg(arg, "fs_add")
        with self._lock:
            doc = self._load()
            row = next((r for r in doc["fs_bank"] if (r["kind"], r["value"]) == (kind, value)),
                       None)
            if row is None:
                if len(doc["fs_bank"]) >= MAX_FS_ROWS:
                    raise ValueError(f"at most {MAX_FS_ROWS} stored-stack rows")
                doc["fs_bank"].append({"kind": kind, "value": value, "count": n})
            elif row["count"] + n > MAX_FS_COUNT:
                raise ValueError(f"count would exceed {MAX_FS_COUNT}")
            else:
                row["count"] += n
            self._save(doc)
        return self.view()

    def fs_use(self, arg):
        """`{kind, value, count?=1}`: takes stacks out; a row at 0 is removed."""
        kind, value, n = self._fs_arg(arg, "fs_use")
        with self._lock:
            doc = self._load()
            row = next((r for r in doc["fs_bank"] if (r["kind"], r["value"]) == (kind, value)),
                       None)
            if row is None:
                raise ValueError(f"no stored {kind} {value}")
            if row["count"] < n:
                raise ValueError(f"only {row['count']} stored {kind} {value}")
            row["count"] -= n
            if not row["count"]:
                doc["fs_bank"].remove(row)
            self._save(doc)
        return self.view()

    def agris_set(self, arg):
        """`{family, step, stacks}`: pity stacks after that many failures; 0 clears."""
        arg = _fields(arg, "agris_set", ("family", "step", "stacks"))
        if not isinstance(arg["family"], str) or not enhance.FAMILY_RE.fullmatch(arg["family"]):
            raise ValueError("family must be 1-24 of a-z 0-9 _ (starting a-z)")
        if arg["step"] not in enhance.STEPS:
            raise ValueError(f"step must be one of {', '.join(enhance.STEPS)}")
        stacks = _int(arg["stacks"], "stacks", 0, MAX_AGRIS_STACKS)
        key = (arg["family"], arg["step"])
        with self._lock:
            doc = self._load()
            rest = [a for a in doc["agris"] if (a["family"], a["step"]) != key]
            if stacks and len(rest) >= MAX_AGRIS_ROWS:
                raise ValueError(f"at most {MAX_AGRIS_ROWS} Agris rows")
            doc["agris"] = rest + ([{"family": key[0], "step": key[1], "stacks": stacks}]
                                   if stacks else [])
            self._save(doc)
        return self.view()

    def crons_set(self, arg):
        """`{owned?, weekly_income?}` (at least one)."""
        arg = _fields(arg, "crons_set", (), ("owned", "weekly_income"))
        if not arg:
            raise ValueError("crons_set needs owned and/or weekly_income")
        new = {}
        if "owned" in arg:
            new["owned"] = _int(arg["owned"], "owned", 0, MAX_CRONS)
        if "weekly_income" in arg:
            new["weekly_income"] = _int(arg["weekly_income"], "weekly_income", 0,
                                        MAX_CRONS_WEEKLY)
        with self._lock:
            doc = self._load()
            doc["crons"].update(new)
            self._save(doc)
        return self.view()
