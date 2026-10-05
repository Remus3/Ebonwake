"""Today checklist (plan 003 slice A): NA reset clocks and the `today` store domain.

Operator ticks plus a seed; nothing is read from the game. A tick is a timestamp,
never a boolean: an item is done when `ticked_at >= last reset of its kind`. Reset
is derived on every read, so there is no cron and no rollover job, and a server
that was down over a reset is still correct.
"""

import datetime as _dt
import re
import threading
import time

KINDS = ("daily", "weekly", "event")
ID_RE = re.compile(r"^[a-z0-9-]{1,40}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_ID_LEN = 40
MAX_TITLE = 80
MAX_ITEMS = 200
THURSDAY = 3  # datetime.weekday()
_DAY = _dt.timedelta(days=1)

SEED = [
    ("Attendance reward", "daily"),
    ("Black Spirit's Adventure dice", "daily"),
    ("Daily Challenges (Y)", "daily"),
    ("Barter run", "daily"),
    ("Guild mission", "daily"),
    ("Black Spirit weekly quests", "weekly"),
    ("Weekly boss rewards", "weekly"),
    ("Pearl Shop weekly free item", "weekly"),
]


# -- reset clocks (same rule as app/shared/ewcore.js) --------------------------

def _utc(now):
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(_dt.timezone.utc)


def last_daily_reset(now):
    """NA daily reset: the most recent 00:00 UTC at or before `now`."""
    n = _utc(now)
    return _dt.datetime(n.year, n.month, n.day, tzinfo=_dt.timezone.utc)


def last_weekly_reset(now):
    """NA weekly reset: the most recent Thursday 00:00 UTC at or before `now`."""
    day = last_daily_reset(now)
    return day - _DAY * ((day.weekday() - THURSDAY) % 7)


def next_daily_reset(now):
    return last_daily_reset(now) + _DAY


def next_weekly_reset(now):
    return last_weekly_reset(now) + 7 * _DAY


def _iso(when):
    return when.astimezone(_dt.timezone.utc).replace(microsecond=0).isoformat()


def _parse_iso(s):
    if not isinstance(s, str):
        return None
    try:
        when = _dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    return when if when.tzinfo is not None else None


# -- validation ----------------------------------------------------------------

def slug(title):
    """Lowercase ASCII slug: apostrophes dropped, other runs of non [a-z0-9] -> '-'."""
    s = re.sub(r"[^a-z0-9]+", "-", title.lower().replace("'", "")).strip("-")
    return s[:MAX_ID_LEN].strip("-") or "item"


def _check_id(iid):
    if not isinstance(iid, str) or not ID_RE.match(iid):
        raise ValueError("id must match ^[a-z0-9-]{1,40}$")
    return iid


def _check_until(until):
    if until is None:
        return None
    if not isinstance(until, str) or not DATE_RE.match(until):
        raise ValueError("until must be an ISO date YYYY-MM-DD")
    try:
        _dt.date.fromisoformat(until)
    except ValueError:
        raise ValueError("until must be a real date") from None
    return until


def validate_new(entry):
    """`{title, kind, until?}` -> normalised dict; raises ValueError."""
    if not isinstance(entry, dict):
        raise ValueError("add must be an object {title, kind, until?}")
    extra = set(entry) - {"title", "kind", "until"}
    if extra:
        raise ValueError(f"unknown field(s): {', '.join(sorted(extra))}")
    title = entry.get("title")
    if not isinstance(title, str):
        raise ValueError("title must be a string")
    title = title.strip()
    if not title or len(title) > MAX_TITLE:
        raise ValueError(f"title must be 1..{MAX_TITLE} characters")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in title):
        raise ValueError("title must not contain control characters")
    kind = entry.get("kind")
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    return {"title": title, "kind": kind, "until": _check_until(entry.get("until"))}


def _clean_item(it):
    """A stored item if well-formed, else None (corrupt docs degrade, never raise)."""
    if not isinstance(it, dict):
        return None
    iid, title, kind = it.get("id"), it.get("title"), it.get("kind")
    if not (isinstance(iid, str) and ID_RE.match(iid) and isinstance(title, str)
            and kind in KINDS):
        return None
    try:
        until = _check_until(it.get("until"))
    except ValueError:
        until = None
    order = it.get("order")
    if not isinstance(order, int) or isinstance(order, bool):
        order = 0
    return {"id": iid, "title": title, "kind": kind, "until": until, "order": order}


class TodayService:
    """Store domain `today`: {"items": [{id, title, kind, until, order}],
    "ticks": {"<id>": "<iso>"}, "updated": "<iso>"}."""

    def __init__(self, store, clock=time.time):
        self.store = store
        self.clock = clock
        self._lock = threading.Lock()  # read-modify-write; Store guards each file op
        with self._lock:
            doc = store.get("today")
            if "items" not in doc:
                items, seen = [], set()
                for n, (title, kind) in enumerate(SEED):
                    iid = self._unique(slug(title), seen)
                    seen.add(iid)
                    items.append({"id": iid, "title": title, "kind": kind, "until": None,
                                  "order": n})
                store.put("today", {"items": items, "ticks": {}, "updated": _iso(self._now())})

    def _now(self):
        return _dt.datetime.fromtimestamp(self.clock(), _dt.timezone.utc)

    @staticmethod
    def _unique(base, taken):
        if base not in taken:
            return base
        n = 2
        while True:
            suffix = f"-{n}"
            cand = base[:MAX_ID_LEN - len(suffix)].rstrip("-") + suffix
            if cand not in taken:
                return cand
            n += 1

    def _load(self):
        doc = self.store.get("today")
        raw = doc.get("items")
        items = [c for c in (_clean_item(i) for i in (raw if isinstance(raw, list) else []))
                 if c is not None]
        seen, uniq = set(), []
        for it in sorted(items, key=lambda i: i["order"]):
            if it["id"] not in seen:
                seen.add(it["id"])
                uniq.append(it)
        ticks = doc.get("ticks")
        ticks = {k: v for k, v in ticks.items() if k in seen} if isinstance(ticks, dict) else {}
        return uniq, ticks

    def _save(self, items, ticks):
        for n, it in enumerate(items):
            it["order"] = n
        self.store.put("today", {"items": items, "ticks": ticks, "updated": _iso(self._now())})

    @staticmethod
    def _find(items, iid):
        _check_id(iid)
        for n, it in enumerate(items):
            if it["id"] == iid:
                return n
        raise ValueError(f"unknown id: {iid}")

    # -- reads -----------------------------------------------------------------

    def view(self):
        """GET /api/today body. Expired events (today's UTC date > until) excluded."""
        now = self._now()
        resets = {"daily": last_daily_reset(now), "weekly": last_weekly_reset(now)}
        resets["event"] = resets["daily"]
        today_iso = now.date().isoformat()
        items, ticks = self._load()
        out = []
        for it in items:
            if it["until"] is not None and today_iso > it["until"]:
                continue
            when = _parse_iso(ticks.get(it["id"]))
            done = when is not None and when >= resets[it["kind"]]
            out.append({"id": it["id"], "title": it["title"], "kind": it["kind"],
                        "until": it["until"], "done": done,
                        "ticked_at": _iso(when) if when is not None else None})
        return {"now": _iso(now), "daily_reset": _iso(next_daily_reset(now)),
                "weekly_reset": _iso(next_weekly_reset(now)), "items": out}

    def source(self):
        """`/api/state` sources.today: {updated, status: "ok"}."""
        upd = _parse_iso(self.store.get("today").get("updated"))
        return {"updated": _iso(upd) if upd is not None else None, "status": "ok"}

    # -- writes (each returns the GET body) -----------------------------------

    def tick(self, iid):
        with self._lock:
            items, ticks = self._load()
            self._find(items, iid)
            ticks[iid] = _iso(self._now())
            self._save(items, ticks)
        return self.view()

    def untick(self, iid):
        with self._lock:
            items, ticks = self._load()
            self._find(items, iid)
            ticks.pop(iid, None)
            self._save(items, ticks)
        return self.view()

    def add(self, entry):
        e = validate_new(entry)
        with self._lock:
            items, ticks = self._load()
            if len(items) >= MAX_ITEMS:
                raise ValueError(f"at most {MAX_ITEMS} items")
            e["id"] = self._unique(slug(e["title"]), {i["id"] for i in items})
            items.append(dict(e, order=len(items)))
            self._save(items, ticks)
        return self.view()

    def remove(self, iid):
        with self._lock:
            items, ticks = self._load()
            del items[self._find(items, iid)]
            ticks.pop(iid, None)
            self._save(items, ticks)
        return self.view()

    def move(self, arg):
        """`{id, to}`: to = 0-based position in the full list, clamped to the end."""
        if not isinstance(arg, dict) or set(arg) != {"id", "to"}:
            raise ValueError("move must be {id, to}")
        to = arg["to"]
        if not isinstance(to, int) or isinstance(to, bool) or to < 0:
            raise ValueError("to must be a non-negative int")
        with self._lock:
            items, ticks = self._load()
            it = items.pop(self._find(items, arg["id"]))
            items.insert(min(to, len(items)), it)
            self._save(items, ticks)
        return self.view()
