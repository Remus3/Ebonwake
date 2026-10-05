"""Deadeye build notes + enhancement plan (plan 007 slice A).

Operator-authored text only: markdown notes per fixed section and an ordered
list of enhancement steps. Nothing here is executed, sent to the game or turned
into input (Game ToS floor); the dashboard renders the markdown as a safe subset.
"""

import datetime as _dt
import re
import threading
import time

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


class DeadeyeService:
    """Store domain `deadeye`: {"notes": {section: {text, updated}}, "plan": [{id,
    item, current, target, note, done}] (operator order), "next_id": int,
    "updated": "<iso>"}."""

    def __init__(self, store, clock=time.time):
        self.store = store
        self.clock = clock
        self._lock = threading.Lock()  # read-modify-write; Store guards each file op
        with self._lock:
            if "notes" not in store.get("deadeye"):
                self._save({"notes": {sid: {"text": "", "updated": None} for sid, _ in SECTIONS},
                            "plan": [], "next_id": 1})

    def _now(self):
        return _dt.datetime.fromtimestamp(self.clock(), _dt.timezone.utc)

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
        return {"notes": notes, "plan": plan, "next_id": max(nxt, top) if ok else top}

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
                "plan": plan, "progress": self._progress(doc["plan"])}

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
