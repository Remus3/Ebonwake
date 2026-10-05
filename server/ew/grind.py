"""Grind log (plan 005 slice A): sessions, per-spot silver/h, buff timers.

All operator input; nothing is read from the game (OCR is plan 009). Elapsed and
left times are derived from the injected clock on every read, so there is no
timer thread and a server that was down is still correct.
"""

import datetime as _dt
import re
import threading
import time

from . import levels
from .today import _iso, _parse_iso, slug

MAX_MINUTES = 1440
MAX_BUFF_MINUTES = 43200  # 30 days: Value Pack / Kamasylve blessing (see plan 005)
MAX_SILVER = 10 ** 13
MAX_TRASH = 10 ** 6
MAX_NAME = 60
MAX_SPOTS = 100
MAX_BUFFS = 50
XP_PCT_RANGE = (0, 1000)  # optional buff XP bonus, counted by the leveling XP stack (plan 011)
MAX_SESSIONS = 2000  # stored; GET shows the newest VIEW_SESSIONS
VIEW_SESSIONS = 200
ID_RE = re.compile(r"^[a-z0-9-]{1,40}$")
SID_RE = re.compile(r"^s[0-9]{1,9}$")

SEED_BUFFS = ("XP scroll", "Drop rate scroll", "Hot Time", "Value Pack", "Old Moon book",
              "Kamasylve blessing")


def silver_per_hour(silver, minutes):
    """silver * 60 / minutes, floored to an int; 0 when minutes is 0."""
    return silver * 60 // minutes if minutes > 0 else 0


# -- validation ----------------------------------------------------------------

def _int(v, name, lo, hi):
    if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
        raise ValueError(f"{name} must be an int {lo}..{hi}")
    return v


def _name(v):
    if not isinstance(v, str):
        raise ValueError("name must be a string")
    v = v.strip()
    if not v or len(v) > MAX_NAME:
        raise ValueError(f"name must be 1..{MAX_NAME} characters")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in v):
        raise ValueError("name must not contain control characters")
    return v


def _fields(arg, what, keys):
    if not isinstance(arg, dict) or set(arg) != set(keys):
        raise ValueError(f"{what} must be {{{', '.join(keys)}}}")
    return arg


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


# -- stored-entry cleaning (corrupt docs degrade, never raise) -----------------

def _iso_or_none(s):
    """Normalised UTC stamp, or None when unparseable or out of datetime range
    once shifted to UTC (e.g. "0001-01-01T00:00:00+01:00")."""
    when = _parse_iso(s)
    if when is None:
        return None
    try:
        return _iso(when)
    except (OverflowError, ValueError):
        return None


def _ok_int(v, lo, hi):
    return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


def _clean_named(it):
    if not (isinstance(it, dict) and isinstance(it.get("id"), str) and ID_RE.match(it["id"])
            and isinstance(it.get("name"), str) and it["name"]):
        return None
    return it


def _clean_session(it):
    if not (isinstance(it, dict) and isinstance(it.get("id"), str) and SID_RE.match(it["id"])
            and isinstance(it.get("spot"), str) and _parse_iso(it.get("started"))
            and _ok_int(it.get("minutes"), 1, MAX_MINUTES)
            and _ok_int(it.get("silver"), 0, MAX_SILVER)
            and _ok_int(it.get("trash"), 0, MAX_TRASH)):
        return None
    return {k: it[k] for k in ("id", "spot", "started", "minutes", "silver", "trash")}


class GrindService:
    """Store domain `grind`: {"spots": [{id, name}], "sessions": [{id, spot, started,
    minutes, silver, trash}] (oldest first), "active": {spot, started}|null,
    "buffs": [{id, name, ends, xp_pct?}], "next_sid": int, "updated": "<iso>"}."""

    def __init__(self, store, clock=time.time, presets=None, epoch=None):
        self.store = store
        self.clock = clock
        # `epoch` returns the newest started XP epoch or None (plan 018): until
        # one has started the presets offer the pre-patch value and no hint shows.
        self.epoch = epoch
        # Plan 018 XP buff presets (tracked data); a bad file degrades to none.
        self.presets_error = None
        if presets is None:
            try:
                presets = levels.load_buff_presets()
            except ValueError as e:
                presets, self.presets_error = [], str(e)
        self.presets = presets
        self._lock = threading.Lock()  # read-modify-write; Store guards each file op
        with self._lock:
            if "spots" not in store.get("grind"):
                seen, buffs = set(), []
                for name in SEED_BUFFS:
                    bid = _unique(slug(name), seen)
                    seen.add(bid)
                    buffs.append({"id": bid, "name": name, "ends": None})
                self._save({"spots": [], "sessions": [], "active": None, "buffs": buffs,
                            "next_sid": 1})

    def _now(self):
        return _dt.datetime.fromtimestamp(self.clock(), _dt.timezone.utc)

    def _load(self):
        doc = self.store.get("grind")

        def lst(key, clean):
            raw = doc.get(key)
            out, seen = [], set()
            for c in (clean(i) for i in (raw if isinstance(raw, list) else [])):
                if c is not None and c["id"] not in seen:
                    seen.add(c["id"])
                    out.append(c)
            return out

        spots = [{"id": s["id"], "name": s["name"]} for s in lst("spots", _clean_named)]
        buffs = [{"id": b["id"], "name": b["name"],
                  "ends": _iso_or_none(b.get("ends")),
                  "xp_pct": b.get("xp_pct") if _ok_int(b.get("xp_pct"), *XP_PCT_RANGE) else None}
                 for b in lst("buffs", _clean_named)]
        sessions = lst("sessions", _clean_session)
        act = doc.get("active")
        active = None
        if (isinstance(act, dict) and act.get("spot") in {s["id"] for s in spots}
                and _parse_iso(act.get("started"))):
            active = {"spot": act["spot"], "started": act["started"]}
        nxt = doc.get("next_sid")
        top = max((int(s["id"][1:]) for s in sessions), default=0) + 1
        nxt = max(nxt, top) if _ok_int(nxt, 1, 10 ** 9 - 1) else top
        return {"spots": spots, "sessions": sessions, "active": active, "buffs": buffs,
                "next_sid": nxt}

    def _save(self, doc):
        doc["sessions"] = doc["sessions"][-MAX_SESSIONS:]
        doc["updated"] = _iso(self._now())
        self.store.put("grind", doc)

    @staticmethod
    def _spot_id(doc, sid):
        if not isinstance(sid, str) or sid not in {s["id"] for s in doc["spots"]}:
            raise ValueError(f"unknown spot: {sid}")
        return sid

    @staticmethod
    def _amounts(arg):
        return (_int(arg["silver"], "silver", 0, MAX_SILVER),
                _int(arg["trash"], "trash", 0, MAX_TRASH))

    def _add_session(self, doc, spot, started, minutes, silver, trash):
        sid = f"s{doc['next_sid']}"
        doc["next_sid"] += 1
        doc["sessions"].append({"id": sid, "spot": spot, "started": started, "minutes": minutes,
                                "silver": silver, "trash": trash})

    # -- reads -----------------------------------------------------------------

    def view(self):
        """GET /api/grind body."""
        now = self._now()
        doc = self._load()
        active = doc["active"]
        if active is not None:
            elapsed = int((now - _parse_iso(active["started"])).total_seconds())
            active = dict(active, elapsed_s=max(0, elapsed))
        totals = {}
        for s in doc["sessions"]:
            t = totals.setdefault(s["spot"], [0, 0, 0])
            t[0] += 1
            t[1] += s["minutes"]
            t[2] += s["silver"]
        spots = []
        for sp in doc["spots"]:
            n, minutes, silver = totals.get(sp["id"], (0, 0, 0))
            spots.append({"id": sp["id"], "name": sp["name"], "sessions": n, "minutes": minutes,
                          "silver_per_h": silver_per_hour(silver, minutes)})
        patched = self._patched()
        buffs = []
        for b in doc["buffs"]:
            ends = _parse_iso(b["ends"])
            left = int((ends - now).total_seconds()) if ends is not None else 0
            hint = levels.buff_hint(b["name"], b["xp_pct"], self.presets) if patched else None
            if left > 0:
                buffs.append({"id": b["id"], "name": b["name"], "ends": b["ends"],
                              "left_s": left, "xp_pct": b["xp_pct"], "xp_hint": hint})
            else:  # unarmed or expired: listed so it can be re-armed in one tap
                buffs.append({"id": b["id"], "name": b["name"], "ends": None, "left_s": None,
                              "xp_pct": b["xp_pct"], "xp_hint": hint})
        sessions = list(reversed(doc["sessions"][-VIEW_SESSIONS:]))
        presets = [{"name": p["name"],
                    "xp_pct": p["xp_pct"] if patched else p["pre_patch_xp_pct"],
                    "patched": patched, "notes": p["notes"], "source": p["source"],
                    "verified": p["verified"]} for p in self.presets]
        return {"now": _iso(now), "active": active, "sessions": sessions, "spots": spots,
                "buffs": buffs, "xp_presets": presets, "xp_presets_error": self.presets_error}

    def _patched(self):
        """True once an XP epoch has started (refute r1 minor 1); without an
        epoch source (bare service) the tracked post-patch values apply."""
        if self.epoch is None:
            return True
        try:
            return self.epoch() is not None
        except Exception:  # noqa: BLE001 - the hint is an extra, never fatal
            return True

    def source(self):
        """`/api/state` sources.grind: {updated, status: "ok"}."""
        return {"updated": _iso_or_none(self.store.get("grind").get("updated")), "status": "ok"}

    # -- writes (each returns the GET body) -----------------------------------

    def add_spot(self, name):
        name = _name(name)
        with self._lock:
            doc = self._load()
            if any(s["name"].lower() == name.lower() for s in doc["spots"]):
                raise ValueError(f"spot exists: {name}")
            if len(doc["spots"]) >= MAX_SPOTS:
                raise ValueError(f"at most {MAX_SPOTS} spots")
            sid = _unique(slug(name), {s["id"] for s in doc["spots"]})
            doc["spots"].append({"id": sid, "name": name})
            self._save(doc)
        return self.view()

    def start(self, spot):
        with self._lock:
            doc = self._load()
            self._spot_id(doc, spot)
            if doc["active"] is not None:
                raise ValueError("a session is already active; stop it first")
            doc["active"] = {"spot": spot, "started": _iso(self._now())}
            self._save(doc)
        return self.view()

    def stop(self, arg):
        """`{silver, trash}`; minutes from active.started, clamped to 1..MAX_MINUTES."""
        silver, trash = self._amounts(_fields(arg, "stop", ("silver", "trash")))
        with self._lock:
            doc = self._load()
            act = doc["active"]
            if act is None:
                raise ValueError("no active session")
            secs = (self._now() - _parse_iso(act["started"])).total_seconds()
            minutes = min(MAX_MINUTES, max(1, int(secs // 60)))
            self._add_session(doc, act["spot"], act["started"], minutes, silver, trash)
            doc["active"] = None
            self._save(doc)
        return self.view()

    def log(self, arg):
        """Manual `{spot, minutes, silver, trash}`; started = now - minutes."""
        arg = _fields(arg, "log", ("spot", "minutes", "silver", "trash"))
        minutes = _int(arg["minutes"], "minutes", 1, MAX_MINUTES)
        silver, trash = self._amounts(arg)
        with self._lock:
            doc = self._load()
            spot = self._spot_id(doc, arg["spot"])
            started = _iso(self._now() - _dt.timedelta(minutes=minutes))
            self._add_session(doc, spot, started, minutes, silver, trash)
            self._save(doc)
        return self.view()

    def delete(self, sid):
        if not isinstance(sid, str) or not SID_RE.match(sid):
            raise ValueError("delete must be a session id like s12")
        with self._lock:
            doc = self._load()
            keep = [s for s in doc["sessions"] if s["id"] != sid]
            if len(keep) == len(doc["sessions"]):
                raise ValueError(f"unknown session: {sid}")
            doc["sessions"] = keep
            self._save(doc)
        return self.view()

    def buff(self, arg):
        """`{name, minutes, xp_pct?}`: ends = now + minutes. Same name
        (case-insensitive) re-arms in place, keeping a stored xp_pct unless one
        is given; a new name is added. xp_pct (0-1000, plan 011) is optional."""
        has_xp = isinstance(arg, dict) and "xp_pct" in arg
        arg = _fields(arg, "buff", ("name", "minutes", "xp_pct") if has_xp
                      else ("name", "minutes"))
        name = _name(arg["name"])
        minutes = _int(arg["minutes"], "minutes", 1, MAX_BUFF_MINUTES)
        xp = _int(arg["xp_pct"], "xp_pct", *XP_PCT_RANGE) if has_xp else None
        with self._lock:
            doc = self._load()
            ends = _iso(self._now() + _dt.timedelta(minutes=minutes))
            for b in doc["buffs"]:
                if b["name"].lower() == name.lower():
                    b["ends"] = ends
                    if has_xp:
                        b["xp_pct"] = xp
                    break
            else:
                if len(doc["buffs"]) >= MAX_BUFFS:
                    raise ValueError(f"at most {MAX_BUFFS} buffs")
                bid = _unique(slug(name), {b["id"] for b in doc["buffs"]})
                doc["buffs"].append({"id": bid, "name": name, "ends": ends, "xp_pct": xp})
            self._save(doc)
        return self.view()

    def clear_buff(self, bid):
        """Disarm a buff timer; the name stays listed for one-tap re-arming."""
        with self._lock:
            doc = self._load()
            for b in doc["buffs"]:
                if b["id"] == bid:
                    b["ends"] = None
                    break
            else:
                raise ValueError(f"unknown buff: {bid}")
            self._save(doc)
        return self.view()
