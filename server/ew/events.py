"""Events tracker (plan 006 slice A): coupon codes, in-game events, Twitch drops.

All operator input; nothing is read from the game and nothing is scraped. The
only seed is SOURCES, a fixed list of official page links. Countdowns, status and
`soon` are derived from the injected clock on every read, so there is no timer
thread and a server that was down is still correct.
"""

import datetime as _dt
import re
import threading
import time

from . import logindays
from .today import _iso, _parse_iso

KINDS = ("coupon", "event", "drop")
MAX_TITLE = 80
MAX_REWARDS = 200
MAX_URL = 300
MAX_ITEMS = 300
SOON_S = 172800  # 48 h
DEADLINE_SOON_S = 14 * 86400  # plan 024: level-gated deadlines show 14 days out
WINDOW = _dt.timedelta(days=730)  # starts/ends within 2 years of now, either side
ID_RE = re.compile(r"^e[0-9]{1,9}$")
CODE_RE = re.compile(r"^[A-Za-z0-9-]{4,40}$")
URL_RE = re.compile(r"^https://[^\s/?#]+[^\s]*$")
DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
STAMP_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(:[0-9]{2}(\.[0-9]{1,6})?)?"
                      r"(Z|[+-][0-9]{2}:[0-9]{2})$")
OPTIONAL = ("code", "rewards", "starts", "ends", "url", "login_rule")  # plan 075: login_rule
EDITABLE = ("title",) + OPTIONAL

SOURCES = [
    {"name": "Black Desert NA/EU news", "url": "https://www.naeu.playblackdesert.com/en-US/News"},
    {"name": "Black Desert NA/EU events",
     "url": "https://www.naeu.playblackdesert.com/en-US/News/Notice?boardType=3"},
    {"name": "Black Desert NA/EU official coupon list",
     "url": "https://www.naeu.playblackdesert.com/en-US/News/Detail?groupContentNo=5676"},
    {"name": "Twitch drops campaigns", "url": "https://www.twitch.tv/drops/campaigns"},
]


# -- validation ----------------------------------------------------------------

def _text(v, name, lo, hi):
    if not isinstance(v, str):
        raise ValueError(f"{name} must be a string")
    v = v.strip()
    if not lo <= len(v) <= hi:
        raise ValueError(f"{name} must be {lo}..{hi} characters")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in v):
        raise ValueError(f"{name} must not contain control characters")
    return v


def _title(v):
    return _text(v, "title", 1, MAX_TITLE)


def _rewards(v):
    if v is None:
        return None
    return _text(v, "rewards", 0, MAX_REWARDS) or None


def _code(v):
    if not isinstance(v, str) or not CODE_RE.match(v):
        raise ValueError("code must match ^[A-Za-z0-9-]{4,40}$")
    return v.upper()


def _url(v):
    if v is None:
        return None
    if not isinstance(v, str) or len(v) > MAX_URL or not v.isascii() or not URL_RE.match(v):
        raise ValueError(f"url must be https://... and at most {MAX_URL} characters")
    return v


def _when(v, name, now, end_of_day):
    """ISO 8601 with offset or Z, or YYYY-MM-DD (UTC 23:59:59 for ends, 00:00 for
    starts); within WINDOW of now; returned as a UTC datetime, or None for None."""
    if v is None:
        return None
    if not isinstance(v, str) or not (DATE_RE.match(v) or STAMP_RE.match(v)):
        raise ValueError(f"{name} must be ISO 8601 with offset or Z, or YYYY-MM-DD")
    try:
        if DATE_RE.match(v):
            d = _dt.date.fromisoformat(v)
            t = _dt.time(23, 59, 59) if end_of_day else _dt.time(0, 0, 0)
            when = _dt.datetime.combine(d, t, _dt.timezone.utc)
        else:
            when = _dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
        if abs(when - now) > WINDOW:
            raise ValueError
        return when.astimezone(_dt.timezone.utc).replace(microsecond=0)
    except (ValueError, OverflowError):
        raise ValueError(f"{name} must be a real date within 2 years of now") from None


def _item_id(v):
    if not isinstance(v, str) or not ID_RE.match(v):
        raise ValueError("id must be an event id like e12")
    return v


def _keys(arg, what, required, allowed):
    if not isinstance(arg, dict) or not set(required) <= set(arg) or not set(arg) <= set(allowed):
        raise ValueError(f"{what} must be {{{', '.join(allowed)}}} with {', '.join(required)}")
    return arg


# -- stored-entry cleaning (corrupt docs degrade, never raise) -----------------

def _iso_or_none(s):
    when = _parse_iso(s)
    if when is None:
        return None
    try:
        return _iso(when)
    except (OverflowError, ValueError):
        return None


def _str_or_none(v, hi):
    return v if isinstance(v, str) and 0 < len(v) <= hi else None


def _clean_item(it):
    if not (isinstance(it, dict) and isinstance(it.get("id"), str) and ID_RE.match(it["id"])
            and it.get("kind") in KINDS
            and isinstance(it.get("title"), str) and 0 < len(it["title"]) <= MAX_TITLE):
        return None
    code = it.get("code")
    code = code.upper() if it["kind"] == "coupon" and isinstance(code, str) \
        and CODE_RE.match(code) else None
    url = it.get("url")
    url = url if isinstance(url, str) and len(url) <= MAX_URL and URL_RE.match(url) else None
    out = {"id": it["id"], "kind": it["kind"], "title": it["title"], "code": code,
           "rewards": _str_or_none(it.get("rewards"), MAX_REWARDS),
           "starts": _iso_or_none(it.get("starts")), "ends": _iso_or_none(it.get("ends")),
           "url": url, "done": it.get("done") is True}
    # Plan 046: when a done item was claimed (session summary); absent before 046.
    done_at = _iso_or_none(it.get("done_at")) if out["done"] else None
    if done_at is not None:
        out["done_at"] = done_at
    if it.get("auto") is True:  # plan 064: added from an official notice
        out["auto"] = True
    rule = logindays.clean_rule(it.get("login_rule"))
    if rule is not None:  # plan 075: qualifying login days are tracked
        out["login_rule"] = rule
    return out


def _num(iid):
    return int(iid[1:])


class EventsService:
    """Store domain `events`: {"items": [{id, kind, title, code, rewards, starts,
    ends, url, done}], "next_id": int, "updated": "<iso>"}. Ids are e<N> from a
    never-reused counter. `deadlines` (plan 024) returns the leveling view's
    decorated deadline rows; those within DEADLINE_SOON_S of enrolment closing
    (level not yet reached) are listed read-only, never stored here."""

    def __init__(self, store, clock=time.time, deadlines=None):
        self.store = store
        self.clock = clock
        self.deadlines = deadlines
        self._lock = threading.Lock()  # read-modify-write; Store guards each file op
        with self._lock:
            if "items" not in self.store.get("events"):
                self._save({"items": [], "next_id": 1})

    def _now(self):
        return _dt.datetime.fromtimestamp(self.clock(), _dt.timezone.utc)

    def _load(self):
        doc = self.store.get("events")
        raw = doc.get("items")
        items, seen = [], set()
        for c in (_clean_item(i) for i in (raw if isinstance(raw, list) else [])):
            if c is not None and c["id"] not in seen:
                seen.add(c["id"])
                items.append(c)
        nxt = doc.get("next_id")
        top = max((_num(i["id"]) for i in items), default=0) + 1
        ok = isinstance(nxt, int) and not isinstance(nxt, bool) and 1 <= nxt < 10 ** 9
        return {"items": items, "next_id": max(nxt, top) if ok else top}

    def _save(self, doc):
        doc["updated"] = _iso(self._now())
        self.store.put("events", doc)

    @staticmethod
    def _find(doc, iid):
        _item_id(iid)
        for it in doc["items"]:
            if it["id"] == iid:
                return it
        raise ValueError(f"unknown event: {iid}")

    @staticmethod
    def _check_code_free(doc, code, self_id=None):
        if any(i["code"] == code and i["id"] != self_id for i in doc["items"]):
            raise ValueError(f"code exists: {code}")

    @staticmethod
    def _check_order(starts, ends):
        if starts is not None and ends is not None and _parse_iso(starts) > _parse_iso(ends):
            raise ValueError("starts must be <= ends")

    # -- reads -----------------------------------------------------------------

    @staticmethod
    def _decorate(it, now):
        ends = _parse_iso(it["ends"])
        starts = _parse_iso(it["starts"])
        left = None if ends is None else max(0, int((ends - now).total_seconds()))
        if it["done"]:
            status = "done"
        elif ends is not None and ends <= now:
            status = "expired"
        elif starts is not None and starts > now:
            status = "upcoming"
        else:
            status = "active"
        soon = status in ("active", "upcoming") and left is not None and left <= SOON_S
        return dict(it, left_s=left, status=status, soon=soon)

    def _items(self, doc, now):
        rows = [self._decorate(i, now) for i in doc["items"]]
        far = _dt.datetime.max.replace(tzinfo=_dt.timezone.utc)

        def by_ends(r):
            e = _parse_iso(r["ends"])
            return (e is None, e or far, _num(r["id"]))

        open_ = sorted((r for r in rows if r["status"] in ("active", "upcoming")), key=by_ends)
        done = sorted((r for r in rows if r["status"] == "done"), key=by_ends)
        expired = sorted((r for r in rows if r["status"] == "expired"),
                         key=lambda r: (_parse_iso(r["ends"]), _num(r["id"])), reverse=True)
        return open_ + done + expired

    def view(self):
        """GET /api/events body."""
        now = self._now()
        items = self._items(self._load(), now)
        counts = {k: 0 for k in KINDS}
        for r in items:
            if r["status"] in ("active", "upcoming"):
                counts[r["kind"]] += 1
        return {"now": _iso(now), "sources": [dict(s) for s in SOURCES], "counts": counts,
                "items": items, "deadlines": self._deadlines_soon(now)}

    def _deadlines_soon(self, now):
        """Read-only "Ending soon" rows: {id, label, needs_level, enrol_by_utc,
        state, reach_utc, verified, left_s} closing within DEADLINE_SOON_S, not done."""
        if self.deadlines is None:
            return []
        out = []
        for d in self.deadlines():
            ends = _parse_iso(d.get("enrol_by_utc"))
            if ends is None or d.get("state") == "done":
                continue
            left = int((ends - now).total_seconds())
            if 0 < left <= DEADLINE_SOON_S:
                out.append({"id": d["id"], "label": d["label"], "needs_level": d["needs_level"],
                            "enrol_by_utc": d["enrol_by_utc"], "state": d["state"],
                            "reach_utc": d["reach_utc"], "verified": d.get("verified") is True,
                            "left_s": left})
        return out

    def source(self):
        """`/api/state` sources.events: {updated, status: "ok", open, soonest}."""
        now = self._now()
        open_ = [r for r in self._items(self._load(), now) if r["status"] in ("active", "upcoming")]
        soonest = next((r["ends"] for r in open_ if r["ends"] is not None), None)
        return {"updated": _iso_or_none(self.store.get("events").get("updated")), "status": "ok",
                "open": len(open_), "soonest": soonest}

    # -- writes (each returns the GET body) -----------------------------------

    def add(self, arg, auto=False):
        """`{kind, title, code?, rewards?, starts?, ends?, url?, login_rule?}`; body adds `item`.
        `auto` (plan 064, server side only) marks a notice import."""
        arg = _keys(arg, "add", ("kind", "title"), ("kind", "title") + OPTIONAL)
        kind = arg["kind"]
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}")
        title = _title(arg["title"])
        if kind == "coupon":
            code = _code(arg.get("code"))
        elif arg.get("code") is not None:
            raise ValueError("code is only for coupons")
        else:
            code = None
        rewards = _rewards(arg.get("rewards"))
        url = _url(arg.get("url"))
        rule = logindays.validate_rule(arg.get("login_rule"))
        with self._lock:
            now = self._now()
            starts = _when(arg.get("starts"), "starts", now, False)
            ends = _when(arg.get("ends"), "ends", now, True)
            starts, ends = (None if starts is None else _iso(starts),
                            None if ends is None else _iso(ends))
            self._check_order(starts, ends)
            doc = self._load()
            if len(doc["items"]) >= MAX_ITEMS:
                raise ValueError(f"at most {MAX_ITEMS} items")
            if code is not None:
                self._check_code_free(doc, code)
            iid = f"e{doc['next_id']}"
            doc["next_id"] += 1
            doc["items"].append(dict({"id": iid, "kind": kind, "title": title, "code": code,
                                      "rewards": rewards, "starts": starts, "ends": ends,
                                      "url": url, "done": False}, **({"auto": True} if auto else {}),
                                     **({"login_rule": rule} if rule is not None else {})))
            self._save(doc)
            out = self.view()
        out["item"] = next(i for i in out["items"] if i["id"] == iid)
        return out

    def edit(self, arg):
        """`{id, ...any of title/code/rewards/starts/ends/url}`; null clears an
        optional field. kind is fixed at add (delete and re-add to change it)."""
        arg = _keys(arg, "edit", ("id",), ("id",) + EDITABLE)
        _item_id(arg["id"])
        with self._lock:
            now = self._now()
            doc = self._load()
            it = dict(self._find(doc, arg["id"]))
            if "title" in arg:
                it["title"] = _title(arg["title"])
            if "code" in arg:
                if it["kind"] != "coupon":
                    raise ValueError("code is only for coupons")
                it["code"] = _code(arg["code"])
                self._check_code_free(doc, it["code"], it["id"])
            if "rewards" in arg:
                it["rewards"] = _rewards(arg["rewards"])
            if "url" in arg:
                it["url"] = _url(arg["url"])
            if "login_rule" in arg:  # plan 075: null stops tracking
                rule = logindays.validate_rule(arg["login_rule"])
                it.pop("login_rule", None)
                if rule is not None:
                    it["login_rule"] = rule
            for key, eod in (("starts", False), ("ends", True)):
                if key in arg:
                    when = _when(arg[key], key, now, eod)
                    it[key] = None if when is None else _iso(when)
            self._check_order(it["starts"], it["ends"])
            doc["items"] = [it if i["id"] == it["id"] else i for i in doc["items"]]
            self._save(doc)
            return self.view()

    def done(self, arg):
        """`{id, done: bool}`."""
        arg = _keys(arg, "done", ("id", "done"), ("id", "done"))
        if not isinstance(arg["done"], bool):
            raise ValueError("done must be true or false")
        _item_id(arg["id"])
        with self._lock:
            doc = self._load()
            it = self._find(doc, arg["id"])
            if arg["done"] and not it["done"]:
                it["done_at"] = _iso(self._now())
            elif not arg["done"]:
                it.pop("done_at", None)
            it["done"] = arg["done"]
            self._save(doc)
            return self.view()

    def delete(self, iid):
        _item_id(iid)
        with self._lock:
            doc = self._load()
            self._find(doc, iid)
            doc["items"] = [i for i in doc["items"] if i["id"] != iid]
            self._save(doc)
            return self.view()

    def purge_expired(self, arg):
        """`true`: remove every item whose ends <= now, done or not; body adds `purged`."""
        if arg is not True:
            raise ValueError("purge_expired must be true")
        with self._lock:
            now = self._now()
            doc = self._load()
            keep = [i for i in doc["items"]
                    if i["ends"] is None or _parse_iso(i["ends"]) > now]
            n = len(doc["items"]) - len(keep)
            doc["items"] = keep
            self._save(doc)
            out = self.view()
        out["purged"] = n
        return out
