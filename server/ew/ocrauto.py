"""Auto-OCR (plan 063): every new screenshot taken while logged in is read
without a button press; confident fields are committed, the rest are queued.

Trigger: a GameWatch listener keeps the logged_in windows; `scan()` enqueues a
watcher-listed shot (plan 008) whose mtime falls inside a window or within
AFTER_LOGOUT_S after it. Shots taken while the game is closed are marked seen
and never read. One worker thread drains the queue through the plan 009
OcrService (same engine chain, same cache), one OCR at a time, at most
`ocr.daily_cap` a local day.

Confidence: every extracted field is {value, conf 0..1, why}. Fields at or over
`ocr.auto_commit_min` are committed (silver sample in store domain `silver`,
buff timer through GrindService) with source `ocr:<file>` and an undo id; the
rest go to store domain `ocr_review` (MAX_REVIEW, oldest dropped) for accept /
fix / discard. Nothing reaches the game: the input is image files the operator
saved with the game's own screenshot key, read after they are written.
"""

import datetime as _dt
import math
import re
import threading
import time

from . import ocr
from .grind import MAX_BUFF_MINUTES, MAX_SILVER, SEED_BUFFS

AUTO_COMMIT_MIN = 0.9
DAILY_CAP = 120
AFTER_LOGOUT_S = 60
MAX_REVIEW = 50
MAX_COMMITS = 200
MAX_SEEN = 500
MAX_SAMPLES = 500
MAX_WINDOWS = 50
VIEW_COMMITS = 20
POLL_S = 2.0
LOGGED_IN = "logged_in"
KINDS = ("silver", "buff")

# Silver confidence (separators between the digit groups of the read amount).
CONF_GROUPED = 0.97      # 1,234,567 - the game's own format
CONF_DOTTED = 0.93       # 1.234.567 - a comma read as a dot keeps the value
CONF_SPACED_SEP = 0.85   # "1, 234 ,567" - separator with OCR spacing
CONF_SPACE_GROUPS = 0.8  # "1 234 567" - a comma read as a space (or two numbers)
CONF_MIXED = 0.6         # 1,234.567 - mixed separators
CONF_SHORT = 0.95        # <= 3 digits: no grouping expected
CONF_UNGROUPED = 0.7     # >= 4 digits and no separator: commas were dropped
CONF_BROKEN = 0.3        # the amount runs on into a separator + glyph: truncated
CONF_DISAGREE = 0.3      # a second engine read another number
IMPLAUSIBLE = 0.6        # x factor when the value is 10x off the last committed
IMPLAUSIBLE_RATIO = 10
# Buff confidence: name match ratio x time-parse validity.
TIME_OK = 1.0
TIME_NEIGHBOUR = 0.85    # the time came from a line beside / below the name
TIME_ODD = 0.6           # units out of order, repeated, or out of range
TIME_STRAY = 0.8         # another bare number sits beside the time
TIME_AT_CAP = 0.7        # clamped to the 30-day maximum

_SEP_RUN = re.compile(r"[,. ]+")
_BROKEN_AFTER = re.compile(r"^ ?[,.] ?[^\s,.]")
_BROKEN_BEFORE = re.compile(r"[^\s,.:] ?[,.] ?$")
_UNIT_ORDER = {"d": 0, "h": 1, "m": 2, "s": 3}
_UNIT_MAX = {"h": 24, "m": 60, "s": 60}


# -- confidence ------------------------------------------------------------------

def _separator_conf(raw):
    """(conf, why) from the digit groups and separators of one amount string."""
    groups = [g for g in _SEP_RUN.split(raw) if g]
    seps = _SEP_RUN.findall(raw)
    if len(groups) == 1:
        if len(groups[0]) <= 3:
            return CONF_SHORT, "short amount"
        return CONF_UNGROUPED, "no digit grouping"
    kinds = {s.strip() or " " for s in seps}
    if kinds == {" "}:
        return CONF_SPACE_GROUPS, "groups split by spaces"
    if len(kinds) > 1:
        return CONF_MIXED, "mixed separators"
    if any(s != s.strip() for s in seps):
        return CONF_SPACED_SEP, "separator with spacing"
    if kinds == {"."}:
        return CONF_DOTTED, "dot grouped"
    return CONF_GROUPED, "comma grouped"


def silver_field(doc, last=None, alt=None):
    """{kind, name, value, conf, why} for the silver amount in an OCR doc, or None.
    `last`: the last committed silver (plausibility); `alt`: another engine's
    read of the same shot (agreement), when one ran."""
    lines = ocr._lines(doc.get("lines") if isinstance(doc, dict) else None)
    hit = ocr._silver_hit(lines)
    if hit is None:
        return None
    value, li, start, end = hit
    text = lines[li]["text"]
    conf, why = _separator_conf(text[start:end])
    whys = [why]
    if _BROKEN_AFTER.match(text[end:]) or _BROKEN_BEFORE.search(text[:start]):
        conf = min(conf, CONF_BROKEN)
        whys.append("amount runs into a broken group")
    if isinstance(last, int) and last > 0 and value >= 0:
        if value > last * IMPLAUSIBLE_RATIO or value * IMPLAUSIBLE_RATIO < last:
            conf *= IMPLAUSIBLE
            whys.append("10x off the last committed silver")
    if alt is not None:
        if alt == value:
            conf = 1 - (1 - conf) / 2
            whys.append("engines agree")
        else:
            conf = min(conf, CONF_DISAGREE)
            whys.append("engines disagree")
    return {"kind": "silver", "name": "silver", "value": value,
            "conf": round(conf, 3), "why": "; ".join(whys)}


def time_conf(text):
    """(minutes or None, conf, why) for the duration in one text run."""
    hits = list(ocr.DURATION.finditer(text if isinstance(text, str) else ""))
    minutes = ocr.duration_minutes(text)
    if minutes is None:
        return None, 0.0, "no time"
    conf, why = TIME_OK, "time parsed"
    units = [m.group(2)[0].lower() for m in hits]
    order = [_UNIT_ORDER[u] for u in units]
    if order != sorted(set(order)):
        conf, why = TIME_ODD, "time units out of order or repeated"
    elif any(int(m.group(1)) >= _UNIT_MAX[u] for m, u in zip(hits, units)
             if u in _UNIT_MAX and units.index(u) > 0):
        conf, why = TIME_ODD, "time field out of range"
    rest = ocr.DURATION.sub(" ", text)
    if re.search(r"\d", rest):
        conf, why = min(conf, TIME_STRAY), "stray number beside the time"
    if minutes >= MAX_BUFF_MINUTES:
        conf, why = min(conf, TIME_AT_CAP), "time at the 30-day cap"
    return minutes, conf, why


def buff_fields(doc, names=SEED_BUFFS):
    """[{kind, name, value (minutes), conf, why}]: one per buff name with a
    time, first sighting wins. conf = name match ratio x time-parse validity;
    an exact (normalised) name is 1.0, else the plan 040 1 - edit distance."""
    lines = ocr._lines(doc.get("lines") if isinstance(doc, dict) else None)
    known = ocr._loot_names(list(names))
    out, seen = [], set()

    def add(name, ratio, minutes, tconf, why):
        if name in seen or minutes is None:
            return
        seen.add(name)
        out.append({"kind": "buff", "name": name, "value": minutes,
                    "conf": round(ratio * tconf, 3),
                    "why": f"name {'exact' if ratio == 1 else f'~{ratio:.2f}'}; {why}"})

    for i, ln in enumerate(lines):
        found = ocr._find_names(ln["text"], names)
        if not found:
            name, d = ocr._best_name(ln["text"], known) if known else (None, None)
            if name is not None:
                minutes, tconf, why = time_conf(ln["text"])
                add(name, round(1 - d, 2), minutes, tconf, why)
            continue
        for name, rest in found:
            minutes, tconf, why = time_conf(rest)
            if minutes is None and len(found) == 1:
                for b in ocr._neighbours(lines, i):
                    if ocr._find_name(b["text"], names)[0] is not None:
                        continue
                    minutes, tconf, why = time_conf(b["text"])
                    if minutes is not None:
                        tconf, why = min(tconf, TIME_NEIGHBOUR), why + " (neighbour line)"
                        break
            add(name, 1, minutes, tconf, why)
    return out


# -- helpers ---------------------------------------------------------------------

def _iso(ts):
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).replace(microsecond=0).isoformat()


def _ts(s):
    if not isinstance(s, str):
        return None
    try:
        when = _dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    return when.timestamp() if when.tzinfo is not None else None


def _ok_int(v, lo, hi):
    return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


def _ok_conf(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1


def _str(v, cap=200):
    return v[:cap] if isinstance(v, str) else ""


def _check_value(kind, v):
    if kind == "silver" and _ok_int(v, 0, MAX_SILVER):
        return v
    if kind == "buff" and _ok_int(v, 1, MAX_BUFF_MINUTES):
        return v
    raise ValueError(f"{kind} value must be an int "
                     f"{'0..' + str(MAX_SILVER) if kind == 'silver' else '1..' + str(MAX_BUFF_MINUTES)}")


class AutoOcr:
    """Store domains: `ocr_auto` {day, count, seen: [key], commits: [{id, file,
    kind, name, value, at, shot_at, prev?, sample?, undone}], next}, `ocr_review`
    {items: [{id, file, kind, name, value, conf, why, at, shot_at}], next},
    `silver` {samples: [{id, at, value, source}]} (oldest first)."""

    def __init__(self, store, game, reader, grind, settings=None, clock=time.time,
                 on_change=None):
        self.store = store
        self.game = game
        self.reader = reader              # OcrService (plan 009): .doc(name) -> {text, lines}
        self.grind = grind                # GrindService: buff / restore_buff
        self.settings = settings or (lambda: {})
        self.clock = clock
        self.on_change = on_change        # fn() after a commit / queue / undo
        self._lock = threading.RLock()
        self._windows = []                # [[start, end|None]] logged_in spans, epoch s
        self._queue = []                  # [(key, shot)]
        self._busy = threading.Lock()     # one OCR at a time
        self._stop = threading.Event()
        self._thread = None

    # -- settings ----------------------------------------------------------------

    def _cfg(self):
        try:
            s = self.settings() or {}
        except Exception:  # noqa: BLE001 - unreadable config = the defaults
            s = {}
        enabled = s.get("ocr.auto", True)
        mn = s.get("ocr.auto_commit_min", AUTO_COMMIT_MIN)
        cap = s.get("ocr.daily_cap", DAILY_CAP)
        return (enabled is not False,
                mn if _ok_conf(mn) and not isinstance(mn, bool) else AUTO_COMMIT_MIN,
                cap if _ok_int(cap, 0, 10 ** 6) else DAILY_CAP)

    # -- trigger -----------------------------------------------------------------

    def on_game(self, prev, new, at):
        """Plan 008 change hook (GameWatch.listeners): logged_in windows."""
        with self._lock:
            if new == LOGGED_IN:
                if not self._windows or self._windows[-1][1] is not None:
                    self._windows.append([at, None])
            elif self._windows and self._windows[-1][1] is None:
                self._windows[-1][1] = at
            del self._windows[:-MAX_WINDOWS]

    def eligible(self, ts):
        """A shot at `ts` falls in a logged_in window or AFTER_LOGOUT_S after it."""
        with self._lock:
            return any(start <= ts and (end is None or ts <= end + AFTER_LOGOUT_S)
                       for start, end in self._windows)

    @staticmethod
    def _key(shot):
        return f"{shot.get('name')}|{shot.get('size')}|{shot.get('mtime')}"

    def _day(self):
        return time.strftime("%Y-%m-%d", time.localtime(self.clock()))

    def _auto(self):
        doc = self.store.get("ocr_auto")
        day = self._day()
        seen = [k for k in doc.get("seen", []) if isinstance(k, str)] \
            if isinstance(doc.get("seen"), list) else []
        commits = [c for c in doc.get("commits", []) if isinstance(c, dict)] \
            if isinstance(doc.get("commits"), list) else []
        count = doc.get("count") if doc.get("day") == day and _ok_int(doc.get("count"), 0, 10 ** 9) \
            else 0
        nxt = doc.get("next") if _ok_int(doc.get("next"), 1, 10 ** 9) else 1
        return {"day": day, "count": count, "seen": seen, "commits": commits, "next": nxt}

    def _save_auto(self, d):
        d["seen"] = d["seen"][-MAX_SEEN:]
        d["commits"] = d["commits"][-MAX_COMMITS:]
        self.store.put("ocr_auto", d)

    def scan(self):
        """Enqueue every new listed shot taken while logged in; mark the rest
        seen. Returns the number enqueued. Safe to call any time (idempotent)."""
        enabled, _, cap = self._cfg()
        if not enabled:
            return 0
        shots = self.game.view().get("screenshots") or []
        added = 0
        with self._lock:
            d = self._auto()
            seen = set(d["seen"]) | {k for k, _ in self._queue}
            changed = False
            for shot in sorted(shots, key=lambda s: str(s.get("mtime"))):
                key = self._key(shot)
                if key in seen:
                    continue
                ts = _ts(shot.get("mtime"))
                seen.add(key)
                if ts is None or not self.eligible(ts):
                    d["seen"].append(key)  # listed, never read
                    changed = True
                    continue
                if d["count"] + len(self._queue) >= cap:
                    d["seen"].append(key)  # over today's cap: the Read button remains
                    changed = True
                    continue
                self._queue.append((key, dict(shot)))
                added += 1
            if changed:
                self._save_auto(d)
        return added

    def drain(self):
        """OCR every queued shot, one at a time. Returns the number read."""
        n = 0
        while True:
            with self._lock:
                if not self._queue:
                    return n
                key, shot = self._queue.pop(0)
            with self._busy:
                self._process(key, shot)
            n += 1

    def _process(self, key, shot):
        enabled, mn, _ = self._cfg()
        with self._lock:
            d = self._auto()
            if key in d["seen"]:
                return
            d["seen"].append(key)
            d["count"] += 1
            self._save_auto(d)
        if not enabled:
            return
        try:
            doc = self.reader.doc(shot["name"])
        except (ocr.OcrError, ValueError, OSError):
            return  # an unreadable shot is skipped; Read re-runs it on demand
        fields = []
        s = silver_field(doc, last=self._last_silver())
        if s is not None:
            fields.append(s)
        fields += buff_fields(doc)
        shot_at = _ts(shot.get("mtime")) or self.clock()
        touched = False
        for f in fields:
            if f["kind"] == "buff" and f["value"] - math.floor(
                    max(0.0, self.clock() - shot_at) / 60) < 1:
                continue  # ran out since the shot: nothing left to arm or review
            if f["conf"] >= mn:
                try:
                    self._commit(shot["name"], f["kind"], f["name"], f["value"], shot_at)
                    touched = True
                    continue
                except ValueError:
                    pass
            self._enqueue_review(shot["name"], f, shot_at)
            touched = True
        if touched:
            self._changed()

    def _changed(self):
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception:  # noqa: BLE001 - a notifier never breaks the worker
                pass

    # -- commits -----------------------------------------------------------------

    def _last_silver(self):
        samples = self.store.get("silver").get("samples")
        for s in reversed(samples if isinstance(samples, list) else []):
            if isinstance(s, dict) and _ok_int(s.get("value"), 0, MAX_SILVER):
                return s["value"]
        return None

    def _buff_state(self, name):
        """Stored {ends, armed} of a grind buff (by name), or None."""
        doc = self.store.get("grind")
        for b in doc.get("buffs", []) if isinstance(doc.get("buffs"), list) else []:
            if isinstance(b, dict) and isinstance(b.get("name"), str) \
                    and isinstance(name, str) and b["name"].lower() == name.lower():
                return {"ends": b.get("ends"), "armed": b.get("armed")}
        return None

    def _commit(self, file, kind, name, value, shot_at, via="auto", at_shot=True):
        """Commit one field to its domain; returns the undo id."""
        value = _check_value(kind, value)
        now = self.clock()
        with self._lock:
            d = self._auto()
            uid = f"u{d['next']}"
            entry = {"id": uid, "file": _str(file, 255), "kind": kind, "name": name,
                     "value": value, "at": _iso(now), "shot_at": _iso(shot_at),
                     "via": via, "undone": False}
            if kind == "silver":
                doc = self.store.get("silver")
                samples = doc.get("samples") if isinstance(doc.get("samples"), list) else []
                samples.append({"id": uid, "at": _iso(shot_at), "value": value,
                                "source": f"ocr:{_str(file, 255)}"})
                self.store.put("silver", {"samples": samples[-MAX_SAMPLES:]})
            else:
                # `value` is minutes left at the shot; a fix is typed as minutes left now.
                left = value - math.floor(max(0.0, now - shot_at) / 60) if at_shot else value
                if left < 1:
                    raise ValueError(f"{name} ran out since the screenshot; "
                                     "fix with the minutes left now, or discard")
                prev = self._buff_state(name)
                entry["prev"] = {"ends": prev["ends"] if prev else None,
                                 "armed": prev.get("armed") if prev else None}
                self.grind.buff({"name": name, "minutes": left})
                now_state = self._buff_state(name)
                entry["ends"] = now_state["ends"] if now_state else None
                entry["minutes_left"] = left
            d["next"] += 1
            d["commits"].append(entry)
            self._save_auto(d)
        return uid

    def undo(self, uid):
        """Reverse one commit: the silver sample is removed, a buff timer goes
        back to what it was before the commit."""
        if not isinstance(uid, str) or not re.fullmatch(r"u[0-9]{1,9}", uid):
            raise ValueError("undo must be an id like u3")
        with self._lock:
            d = self._auto()
            c = next((c for c in d["commits"] if c.get("id") == uid), None)
            if c is None:
                raise ValueError(f"unknown commit: {uid}")
            if c.get("undone"):
                raise ValueError(f"already undone: {uid}")
            if c.get("kind") == "silver":
                doc = self.store.get("silver")
                samples = doc.get("samples") if isinstance(doc.get("samples"), list) else []
                self.store.put("silver", {"samples": [s for s in samples
                                                      if not (isinstance(s, dict)
                                                              and s.get("id") == uid)]})
            else:
                # Only while the timer is still the one this commit wrote: a later
                # commit or a manual re-arm / clear is never overwritten.
                cur = self._buff_state(c.get("name"))
                if cur is None or cur["ends"] != c.get("ends"):
                    raise ValueError(f"{c.get('name')} changed since this commit; "
                                     "set it in the Grind tab")
                prev = c.get("prev") if isinstance(c.get("prev"), dict) else {}
                self.grind.restore_buff(c.get("name"), prev.get("ends"), prev.get("armed"))
            c["undone"] = True
            self._save_auto(d)
        self._changed()
        return self.view()

    # -- review queue ------------------------------------------------------------

    def _review(self):
        doc = self.store.get("ocr_review")
        items = [i for i in doc.get("items", []) if isinstance(i, dict)] \
            if isinstance(doc.get("items"), list) else []
        nxt = doc.get("next") if _ok_int(doc.get("next"), 1, 10 ** 9) else 1
        return {"items": items, "next": nxt}

    def _enqueue_review(self, file, f, shot_at):
        with self._lock:
            r = self._review()
            r["items"].append({"id": f"r{r['next']}", "file": _str(file, 255),
                               "kind": f["kind"], "name": f["name"], "value": f["value"],
                               "conf": f["conf"], "why": _str(f["why"]),
                               "at": _iso(self.clock()), "shot_at": _iso(shot_at)})
            r["next"] += 1
            r["items"] = r["items"][-MAX_REVIEW:]
            self.store.put("ocr_review", r)

    def review(self, arg):
        """`{id, action: accept|discard}` or `{id, action: fix, value}`."""
        if not isinstance(arg, dict) or not isinstance(arg.get("id"), str):
            raise ValueError("review must be {id, action, value?}")
        action = arg.get("action")
        keys = {"id", "action", "value"} if action == "fix" else {"id", "action"}
        if action not in ("accept", "fix", "discard") or set(arg) != keys:
            raise ValueError("review must be {id, action: accept|discard} or {id, action: fix, value}")
        with self._lock:
            r = self._review()
            item = next((i for i in r["items"] if i.get("id") == arg["id"]), None)
            if item is None or item.get("kind") not in KINDS:
                raise ValueError(f"unknown review item: {arg['id']}")
            if action != "discard":
                value = arg["value"] if action == "fix" else item.get("value")
                self._commit(item.get("file"), item["kind"], item.get("name"), value,
                             _ts(item.get("shot_at")) or self.clock(), via=action,
                             at_shot=action == "accept")
            r["items"] = [i for i in r["items"] if i is not item]
            self.store.put("ocr_review", r)
        self._changed()
        return self.view()

    # -- reads -------------------------------------------------------------------

    def view(self):
        """GET /api/ocr/auto body."""
        enabled, mn, cap = self._cfg()
        with self._lock:
            d = self._auto()
            items = self._review()["items"]
            pending = len(self._queue)
        commits = [{k: c.get(k) for k in ("id", "file", "kind", "name", "value", "at", "via")}
                   for c in reversed(d["commits"]) if not c.get("undone")][:VIEW_COMMITS]
        review = [{k: i.get(k) for k in ("id", "file", "kind", "name", "value", "conf", "why",
                                         "shot_at")} for i in reversed(items)]
        return {"enabled": enabled, "commit_min": mn, "daily_cap": cap, "today": d["count"],
                "pending": pending, "review": review, "commits": commits,
                "silver": self._last_silver()}

    # -- thread ------------------------------------------------------------------

    def _run(self, interval):
        while not self._stop.is_set():
            try:
                self.scan()
                self.drain()
            except Exception:  # noqa: BLE001 - a bad tick never kills the worker
                pass
            self._stop.wait(interval)

    def start(self, interval=POLL_S):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(interval,),
                                        name="ew-ocr-auto", daemon=True)
        self._thread.start()

    def stop(self, timeout=2.0):
        self._stop.set()
        t = self._thread
        if t is not None:
            t.join(timeout)
