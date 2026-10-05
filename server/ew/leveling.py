"""Leveling tracker (plan 011): XP rate, next-level ETA, Hot Time windows, XP stack.

All operator input; nothing is read from the game. Samples are typed level +
XP percent; Hot Time windows are recurring weekly UTC windows copied from the
official event notice (no seeded times: stale times are worse than none). The
math below is pure; the service injects the clock, so every rate, countdown
and window state is derived on read and a server that was down is still right.
"""

import datetime as _dt
import math
import re
import statistics
import threading
import time

from .today import _iso, _parse_iso

LEVEL_RANGE = (1, 70)
PCT_RANGE = (0, 100)
XP_PCT_RANGE = (0, 1000)  # hot window pct and grind buff xp_pct
MAX_SAMPLES = 500
VIEW_SAMPLES = 20
MAX_HOT = 30
MAX_LABEL = 40
MAX_MILESTONES = 20
RATE_DELTAS = 5
RATE_MIN_SPAN_S = 120
SEED_MILESTONES = [50, 56, 57, 58, 60, 61]  # seed, verify against the season notice
HOT_ID_RE = re.compile(r"^h[0-9]{1,9}$")
HHMM_RE = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])$")
_UTC = _dt.timezone.utc


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _ok_int(v, lo, hi):
    return _is_int(v) and lo <= v <= hi


def _ok_pct(v):
    """0..100 with at most 3 decimals (BDO shows e.g. 37.512 %)."""
    if _is_int(v):
        return PCT_RANGE[0] <= v <= PCT_RANGE[1]
    if not isinstance(v, float) or not math.isfinite(v):
        return False
    return PCT_RANGE[0] <= v <= PCT_RANGE[1] and abs(v * 1000 - round(v * 1000)) < 1e-6


def _norm_pct(v):
    return v if _is_int(v) else round(v, 3)


# -- pure math -----------------------------------------------------------------

def _points(samples):
    """Usable samples -> [(utc datetime, cumulative pct)] oldest first; a level
    rollover counts +100 pct per level."""
    pts = []
    for s in samples if isinstance(samples, list) else []:
        if not isinstance(s, dict):
            continue
        when = _parse_iso(s.get("ts"))
        if (when is None or not _ok_int(s.get("level"), *LEVEL_RANGE)
                or not _ok_pct(s.get("pct"))):
            continue
        pts.append((when, s["level"] * 100 + s["pct"]))
    pts.sort(key=lambda p: p[0])
    return pts


def rate_pct_h(samples):
    """Level-percent per hour: median of the last RATE_DELTAS deltas, each
    spanning >= RATE_MIN_SPAN_S (a sample closer than that to the newer end is
    skipped, so bursts of entries merge). None with fewer than 2 usable samples
    or a rate <= 0."""
    pts = _points(samples)
    deltas = []
    i = len(pts) - 1
    while i > 0 and len(deltas) < RATE_DELTAS:
        t1, v1 = pts[i]
        j = i - 1
        while j >= 0 and (t1 - pts[j][0]).total_seconds() < RATE_MIN_SPAN_S:
            j -= 1
        if j < 0:
            break
        secs = (t1 - pts[j][0]).total_seconds()
        deltas.append((v1 - pts[j][1]) * 3600 / secs)
        i = j
    if not deltas:
        return None
    rate = statistics.median(deltas)
    return rate if rate > 0 else None


def eta_next_s(pct, rate):
    """Whole seconds to the next level, or None without a positive rate."""
    if pct is None or rate is None or rate <= 0:
        return None
    return int((100 - pct) / rate * 3600)


def next_milestone(level, milestones):
    """Smallest milestone above `level` (any milestone when level is unknown)."""
    above = [m for m in milestones if level is None or m > level]
    return min(above) if above else None


def _minutes(hhmm):
    m = HHMM_RE.match(hhmm)
    return int(m.group(1)) * 60 + int(m.group(2))


def hot_status(windows, now):
    """{active: [{id, label, pct, ends_in_s}], next: {id, label, pct,
    starts_in_s}|None} at `now`, all in UTC. Start inclusive, end exclusive;
    an end at or before the start wraps past midnight into the next day."""
    now = now.astimezone(_UTC)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    active, nxt = [], None
    for w in windows:
        start = _minutes(w["start"])
        dur = (_minutes(w["end"]) - start) % 1440
        for back in range(-1, 8):  # yesterday (a wrap still running) .. same day next week
            day = midnight + _dt.timedelta(days=back)
            if day.weekday() not in w["days"]:
                continue
            st = day + _dt.timedelta(minutes=start)
            en = st + _dt.timedelta(minutes=dur)
            base = {"id": w["id"], "label": w["label"], "pct": w["pct"]}
            if st <= now < en:
                active.append(dict(base, ends_in_s=int((en - now).total_seconds())))
            elif st > now:
                secs = int((st - now).total_seconds())
                if nxt is None or secs < nxt["starts_in_s"]:
                    nxt = dict(base, starts_in_s=secs)
    active.sort(key=lambda a: a["ends_in_s"])
    return {"active": active, "next": nxt}


def _live_xp_buffs(buffs):
    out = []
    for b in buffs if isinstance(buffs, list) else []:
        if (isinstance(b, dict) and _ok_int(b.get("left_s"), 1, 10 ** 12)
                and _ok_int(b.get("xp_pct"), *XP_PCT_RANGE)):
            out.append(b)
    return out


def xp_stack(active, buffs):
    """Sum of active hot window pct + armed grind buffs that carry an xp_pct."""
    return (sum(a["pct"] for a in active)
            + sum(b["xp_pct"] for b in _live_xp_buffs(buffs)))


# -- validation ----------------------------------------------------------------

def _fields(arg, what, keys):
    if not isinstance(arg, dict) or set(arg) != set(keys):
        raise ValueError(f"{what} must be {{{', '.join(keys)}}}")
    return arg


def _check_days(v):
    if (not isinstance(v, list) or not v or not all(_ok_int(d, 0, 6) for d in v)
            or len(set(v)) != len(v)):
        raise ValueError("days must be a non-empty list of unique ints 0..6 (Monday=0)")
    return sorted(v)


def _check_hhmm(v, what):
    if not isinstance(v, str) or not HHMM_RE.match(v):
        raise ValueError(f"{what} must be HH:MM (UTC, 00:00..23:59)")
    return v


def _check_label(v):
    if not isinstance(v, str):
        raise ValueError("label must be a string")
    v = v.strip()
    if not v or len(v) > MAX_LABEL:
        raise ValueError(f"label must be 1..{MAX_LABEL} characters")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in v):
        raise ValueError("label must not contain control characters")
    return v


def _check_xp(v, what):
    if not _ok_int(v, *XP_PCT_RANGE):
        raise ValueError(f"{what} must be an int {XP_PCT_RANGE[0]}..{XP_PCT_RANGE[1]}")
    return v


def _check_milestones(v):
    if (not isinstance(v, list) or len(v) > MAX_MILESTONES
            or not all(_ok_int(m, *LEVEL_RANGE) for m in v) or len(set(v)) != len(v)):
        raise ValueError(f"milestones must be a list of at most {MAX_MILESTONES} unique "
                         f"ints {LEVEL_RANGE[0]}..{LEVEL_RANGE[1]}")
    return sorted(v)


# -- stored-entry cleaning (corrupt docs degrade, never raise) -----------------

def _clean_sample(s):
    if not isinstance(s, dict):
        return None
    when = _parse_iso(s.get("ts"))
    if when is None or not _ok_int(s.get("level"), *LEVEL_RANGE) or not _ok_pct(s.get("pct")):
        return None
    try:
        ts = _iso(when)
    except (OverflowError, ValueError):
        return None
    return {"ts": ts, "level": s["level"], "pct": _norm_pct(s["pct"])}


def _clean_window(w):
    if not isinstance(w, dict) or not isinstance(w.get("id"), str) \
            or not HOT_ID_RE.match(w["id"]):
        return None
    try:
        return {"id": w["id"], "days": _check_days(w.get("days")),
                "start": _check_hhmm(w.get("start"), "start"),
                "end": _check_hhmm(w.get("end"), "end"),
                "label": _check_label(w.get("label")), "pct": _check_xp(w.get("pct"), "pct")}
    except ValueError:
        return None


class LevelingService:
    """Store domain `leveling`: {"samples": [{ts, level, pct}] (oldest first),
    "hot_windows": [{id, days, start, end, label, pct}], "milestones": [int],
    "next_id": int, "updated": "<iso>"}. `buffs` returns the grind view's buff
    list (plan 005) for the XP stack; `seq` bumps on every write (SSE)."""

    def __init__(self, store, clock=time.time, buffs=None):
        self.store = store
        self.clock = clock
        self.buffs = buffs
        self.seq = 0
        self._lock = threading.Lock()  # read-modify-write; Store guards each file op
        with self._lock:
            if "milestones" not in store.get("leveling"):
                # seed milestones only; keep any samples / windows already
                # stored (a hand-edited file lacking the key; refute r1 minor 2)
                doc = self._load()
                doc["milestones"] = list(SEED_MILESTONES)
                self._save(doc, bump=False)

    def _now(self):
        return _dt.datetime.fromtimestamp(self.clock(), _UTC)

    def _load(self):
        doc = self.store.get("leveling")
        by_ts = {}
        for s in doc.get("samples") if isinstance(doc.get("samples"), list) else []:
            c = _clean_sample(s)
            if c is not None:
                by_ts[c["ts"]] = c
        samples = sorted(by_ts.values(), key=lambda s: _parse_iso(s["ts"]))
        windows, seen = [], set()
        for w in doc.get("hot_windows") if isinstance(doc.get("hot_windows"), list) else []:
            c = _clean_window(w)
            if c is not None and c["id"] not in seen:
                seen.add(c["id"])
                windows.append(c)
        raw = doc.get("milestones")
        ms = sorted({m for m in (raw if isinstance(raw, list) else [])
                     if _ok_int(m, *LEVEL_RANGE)})[:MAX_MILESTONES]
        nxt = doc.get("next_id")
        top = max((int(w["id"][1:]) for w in windows), default=0) + 1
        nxt = max(nxt, top) if _ok_int(nxt, 1, 10 ** 9 - 1) else top
        return {"samples": samples, "hot_windows": windows, "milestones": ms, "next_id": nxt}

    def _save(self, doc, bump=True):
        doc["samples"] = doc["samples"][-MAX_SAMPLES:]
        doc["updated"] = _iso(self._now())
        self.store.put("leveling", doc)
        if bump:
            self.seq += 1

    # -- reads -----------------------------------------------------------------

    def view(self):
        """GET /api/leveling body."""
        now = self._now()
        doc = self._load()
        samples = doc["samples"]
        last = samples[-1] if samples else None
        level = last["level"] if last else None
        pct = last["pct"] if last else None
        rate = rate_pct_h(samples)
        hot = hot_status(doc["hot_windows"], now)
        buffs = _live_xp_buffs(self.buffs() if self.buffs is not None else [])
        parts = ([{"name": a["label"], "pct": a["pct"]} for a in hot["active"]]
                 + [{"name": b.get("name"), "pct": b["xp_pct"]} for b in buffs])
        return {"now": _iso(now), "level": level, "pct": pct,
                "rate_pct_h": None if rate is None else round(rate, 3),
                "eta_next_s": eta_next_s(pct, rate),
                "next_milestone": next_milestone(level, doc["milestones"]),
                "hot": hot, "xp_stack_pct": xp_stack(hot["active"], buffs), "xp_parts": parts,
                "milestones": doc["milestones"],
                "milestones_seed": doc["milestones"] == SEED_MILESTONES,
                "hot_windows": doc["hot_windows"],
                "samples": list(reversed(samples[-VIEW_SAMPLES:]))}

    def source(self):
        """`/api/state` sources.leveling: {updated, status: "ok"}."""
        when = _parse_iso(self.store.get("leveling").get("updated"))
        return {"updated": _iso(when) if when else None, "status": "ok"}

    # -- writes (each returns the GET body) -----------------------------------

    def sample(self, arg):
        """{level, pct}; ts = now (a second sample in the same second replaces it)."""
        arg = _fields(arg, "sample", ("level", "pct"))
        if not _ok_int(arg["level"], *LEVEL_RANGE):
            raise ValueError(f"level must be an int {LEVEL_RANGE[0]}..{LEVEL_RANGE[1]}")
        if not _ok_pct(arg["pct"]):
            raise ValueError("pct must be a number 0..100 with at most 3 decimals")
        with self._lock:
            doc = self._load()
            ts = _iso(self._now())
            doc["samples"] = [s for s in doc["samples"] if s["ts"] != ts]
            doc["samples"].append({"ts": ts, "level": arg["level"],
                                   "pct": _norm_pct(arg["pct"])})
            self._save(doc)
        return self.view()

    def sample_del(self, ts):
        when = _parse_iso(ts)
        if when is None:
            raise ValueError("sample_del must be a sample ts")
        try:
            key = _iso(when)
        except (OverflowError, ValueError, OSError):  # refute r1 minor 1
            raise ValueError("sample_del must be a sample ts") from None
        with self._lock:
            doc = self._load()
            keep = [s for s in doc["samples"] if s["ts"] != key]
            if len(keep) == len(doc["samples"]):
                raise ValueError(f"unknown sample: {ts}")
            doc["samples"] = keep
            self._save(doc)
        return self.view()

    def hot_add(self, arg):
        """{days, start, end, label, pct}; UTC, end may wrap past midnight."""
        arg = _fields(arg, "hot_add", ("days", "start", "end", "label", "pct"))
        days = _check_days(arg["days"])
        start = _check_hhmm(arg["start"], "start")
        end = _check_hhmm(arg["end"], "end")
        if start == end:
            raise ValueError("start and end must differ")
        label = _check_label(arg["label"])
        pct = _check_xp(arg["pct"], "pct")
        with self._lock:
            doc = self._load()
            if len(doc["hot_windows"]) >= MAX_HOT:
                raise ValueError(f"at most {MAX_HOT} hot windows")
            wid = f"h{doc['next_id']}"
            doc["next_id"] += 1
            doc["hot_windows"].append({"id": wid, "days": days, "start": start, "end": end,
                                       "label": label, "pct": pct})
            self._save(doc)
        return self.view()

    def hot_del(self, wid):
        if not isinstance(wid, str) or not HOT_ID_RE.match(wid):
            raise ValueError("hot_del must be a window id like h3")
        with self._lock:
            doc = self._load()
            keep = [w for w in doc["hot_windows"] if w["id"] != wid]
            if len(keep) == len(doc["hot_windows"]):
                raise ValueError(f"unknown hot window: {wid}")
            doc["hot_windows"] = keep
            self._save(doc)
        return self.view()

    def set_milestones(self, arg):
        ms = _check_milestones(arg)
        with self._lock:
            doc = self._load()
            doc["milestones"] = ms
            self._save(doc)
        return self.view()
