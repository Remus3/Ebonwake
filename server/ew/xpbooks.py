"""Combat Secret Book ledger (plan 060): books per activity, observed XP, books-to-level.

`data/xp_books.json` holds the sourced book sizes {id, name, pct_at: {level:
pct}, min_level, source, verified} and the activities that award them
{activity, name, weekly_row (plan 033 row id or null), win: {size, n}, loss:
{size, n} | null}. Values are data, never code.

Store domain `xpbooks`: {"owned": {size: n}, "used": [{at, size, level,
pct_before, pct_after}] (oldest first), "added": [{at, size, n, activity}],
"updated"}. A book's worth is the median observed gain of used books of that
size at that level; without one it falls back to the tracked value at the
nearest level, flagged "Lv <n> value, verify". Operator-typed data and the
tracked file only; nothing is read from the game.
"""

import datetime as _dt
import json
import math
import re
import statistics
import threading
import time
from pathlib import Path

from .leveling import _is_int, _norm_pct, _ok_pct, eta_next_s
from .levels import LEVEL_RANGE
from .today import _iso, _parse_iso

DATA_FILE = Path(__file__).resolve().parent / "data" / "xp_books.json"
SIZES = ("small", "medium", "large", "xl")
SIZE_FIELDS = ("id", "name", "pct_at", "min_level", "source", "verified")
SOURCE_FIELDS = ("activity", "name", "weekly_row", "win", "loss")
MIN_LEVEL = 60
ID_RE = re.compile(r"^[a-z0-9-]{1,40}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_TEXT = 200
MAX_N = 99
MAX_OWNED = 9999
MAX_USED = 200
MAX_ADDED = 200
VIEW_USED = 20
WEEK_H = 168
PCT_MAX = 100
_UTC = _dt.timezone.utc


def _text(v, what):
    if (not isinstance(v, str) or not v.strip() or len(v) > MAX_TEXT
            or any(ord(ch) < 32 or ord(ch) > 126 for ch in v)):
        raise ValueError(f"{what} must be 1..{MAX_TEXT} printable ASCII characters")
    return v.strip()


def _reward(v, what, allow_null):
    if v is None and allow_null:
        return None
    if (not isinstance(v, dict) or set(v) != {"size", "n"} or v["size"] not in SIZES
            or not _is_int(v["n"]) or not 1 <= v["n"] <= MAX_N):
        raise ValueError(f"{what} must be {{size, n}} with size in {', '.join(SIZES)} "
                         f"and n 1..{MAX_N}")
    return {"size": v["size"], "n": v["n"]}


def _size_row(r):
    if not isinstance(r, dict) or set(r) != set(SIZE_FIELDS):
        raise ValueError(f"book sizes must have exactly {', '.join(SIZE_FIELDS)}")
    if r["id"] not in SIZES:
        raise ValueError(f"size id must be one of {', '.join(SIZES)}")
    raw = r["pct_at"]
    if not isinstance(raw, dict) or not raw:
        raise ValueError("pct_at must be a non-empty {level: pct} object")
    pct_at = {}
    for k, v in raw.items():
        if not isinstance(k, str) or not k.isdigit() or not LEVEL_RANGE[0] <= int(k) <= LEVEL_RANGE[1]:
            raise ValueError("pct_at keys must be levels")
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) \
                or not 0 < v <= PCT_MAX:
            raise ValueError("pct_at values must be numbers in (0, 100]")
        pct_at[int(k)] = float(v)
    if not _is_int(r["min_level"]) or not LEVEL_RANGE[0] <= r["min_level"] <= LEVEL_RANGE[1]:
        raise ValueError("min_level must be a level")
    if not isinstance(r["verified"], bool):
        raise ValueError("verified must be a boolean")
    return {"id": r["id"], "name": _text(r["name"], "name"), "pct_at": pct_at,
            "min_level": r["min_level"], "source": _text(r["source"], "source"),
            "verified": r["verified"]}


def _source_row(r):
    if not isinstance(r, dict) or set(r) != set(SOURCE_FIELDS):
        raise ValueError(f"book sources must have exactly {', '.join(SOURCE_FIELDS)}")
    if not isinstance(r["activity"], str) or not ID_RE.match(r["activity"]):
        raise ValueError("activity must match ^[a-z0-9-]{1,40}$")
    wr = r["weekly_row"]
    if wr is not None and (not isinstance(wr, str) or not ID_RE.match(wr)):
        raise ValueError("weekly_row must be null or a weekly content id")
    return {"activity": r["activity"], "name": _text(r["name"], "name"), "weekly_row": wr,
            "win": _reward(r["win"], "win", False), "loss": _reward(r["loss"], "loss", True)}


def validate_books(doc):
    """{source, read, note, sizes: {id: row}, sources: {activity: row}} or ValueError."""
    if not isinstance(doc, dict) or set(doc) != {"source", "read", "note", "sizes", "sources"}:
        raise ValueError("xp books must be {source, read, note, sizes, sources}")
    read = doc["read"]
    try:
        ok = isinstance(read, str) and DATE_RE.match(read) and _dt.date.fromisoformat(read)
    except ValueError:
        ok = False
    if not ok:
        raise ValueError("read must be a YYYY-MM-DD date")
    if not isinstance(doc["sizes"], list) or not isinstance(doc["sources"], list):
        raise ValueError("sizes and sources must be lists")
    sizes = [_size_row(r) for r in doc["sizes"]]
    if sorted(s["id"] for s in sizes) != sorted(SIZES):
        raise ValueError(f"sizes must be exactly {', '.join(SIZES)}")
    sources = [_source_row(r) for r in doc["sources"]]
    if len({s["activity"] for s in sources}) != len(sources):
        raise ValueError("source activities must be unique")
    return {"source": _text(doc["source"], "source"), "read": read,
            "note": _text(doc["note"], "note"),
            "sizes": {s["id"]: s for s in sizes},
            "sources": {s["activity"]: s for s in sources}}


def load_books(path=None):
    try:
        doc = json.loads(Path(path or DATA_FILE).read_text(encoding="ascii"))
    except (OSError, ValueError) as e:
        raise ValueError(f"xp books unreadable: {type(e).__name__}") from e
    return validate_books(doc)


# -- pure math -----------------------------------------------------------------

def gain(before, after):
    """Level-percent a book gave; an `after` below `before` crossed a level."""
    return after - before if after >= before else 100 - before + after


def observed_pct(used, size, level):
    """Median gain of used books of `size` at `level`, or None without one."""
    gains = [gain(u["pct_before"], u["pct_after"]) for u in used
             if u["size"] == size and u["level"] == level]
    return statistics.median(gains) if gains else None


def book_pct(data, used, size, level):
    """{pct, observed, uses, flag}: observed median, else the tracked value at
    the nearest level (ties pick the lower level) flagged for verification."""
    uses = sum(1 for u in used if u["size"] == size and u["level"] == level)
    obs = observed_pct(used, size, level)
    if obs is not None:
        return {"pct": round(obs, 3), "observed": True, "uses": uses, "flag": None}
    pct_at = data["sizes"][size]["pct_at"]
    at = min(pct_at, key=lambda lv: (abs(lv - level), lv))
    return {"pct": pct_at[at], "observed": False, "uses": 0, "flag": f"Lv {at} value, verify"}


def pct_from_owned(data, used, owned, level):
    return round(sum(owned.get(s, 0) * book_pct(data, used, s, level)["pct"] for s in SIZES), 3)


def books_to_next(data, used, level, pct):
    """{size: books of that size alone to finish the level}, None without a pct."""
    if pct is None:
        return None
    left = max(0, 100 - pct)
    return {s: math.ceil(round(left / book_pct(data, used, s, level)["pct"], 9))
            for s in SIZES}


def weekly_expectation(data, used, level, counts, added, now):
    """{rows, logged, pct_week}. rows: activities mapped to a plan 033 weekly
    row (expected = per_week x books per run; done = this period's ticks x
    books per run). logged: other activities' operator-ticked books over the
    trailing 7 days, netted per (activity, size); a net <= 0 is dropped."""
    rows = []
    for s in data["sources"].values():
        c = counts.get(s["weekly_row"]) if s["weekly_row"] else None
        if not isinstance(c, dict):
            continue
        n = s["win"]["n"]
        per = book_pct(data, used, s["win"]["size"], level)["pct"]
        rows.append({"activity": s["activity"], "name": s["name"], "size": s["win"]["size"],
                     "expected": c["per_week"] * n, "done": c["done"] * n,
                     "pct_week": round(c["per_week"] * n * per, 3)})
    cut = now - _dt.timedelta(days=7)
    agg = {}
    for a in added:
        src = data["sources"].get(a["activity"]) if a["activity"] else None
        if src is None or src["weekly_row"] or _parse_iso(a["at"]) < cut:
            continue
        key = (src["activity"], a["size"])
        agg[key] = agg.get(key, 0) + a["n"]  # a negative n nets out a typo
    logged = []
    for (act, size), n in agg.items():
        if n <= 0:
            continue
        logged.append({"activity": act, "name": data["sources"][act]["name"], "size": size,
                       "n": n, "pct_week": round(n * book_pct(data, used, size, level)["pct"], 3)})
    total = sum(r["pct_week"] for r in rows) + sum(r["pct_week"] for r in logged)
    return {"rows": rows, "logged": logged, "pct_week": round(total, 3)}


# -- ledger cleaning (corrupt docs degrade, never raise) -----------------------

def _clean_use(u):
    if not isinstance(u, dict) or u.get("size") not in SIZES:
        return None
    when = _parse_iso(u.get("at"))
    lv = u.get("level")
    if (when is None or not _is_int(lv) or not LEVEL_RANGE[0] <= lv <= LEVEL_RANGE[1]
            or not _ok_pct(u.get("pct_before")) or not _ok_pct(u.get("pct_after"))
            or u["pct_before"] == u["pct_after"]):
        return None
    out = {"at": _iso(when), "size": u["size"], "level": lv,
           "pct_before": _norm_pct(u["pct_before"]), "pct_after": _norm_pct(u["pct_after"])}
    if u.get("from_owned") is True:
        out["from_owned"] = True
    return out


def _clean_added(a, sources):
    if not isinstance(a, dict) or a.get("size") not in SIZES:
        return None
    when = _parse_iso(a.get("at"))
    act = a.get("activity")
    if when is None or not _is_int(a.get("n")) or not -MAX_N <= a["n"] <= MAX_N or a["n"] == 0 \
            or (act is not None and act not in sources):
        return None
    return {"at": _iso(when), "size": a["size"], "n": a["n"], "activity": act}


class XpBooksService:
    """`weekly` returns plan 033 counts {row_id: {done, per_week}} (a failing
    feed means no weekly rows). A bad tracked file degrades to an unavailable
    view carrying the error."""

    def __init__(self, store, clock=time.time, weekly=None, data=None, data_file=None):
        self.store = store
        self.clock = clock
        self.weekly = weekly
        self.error = None
        if data is None:
            try:
                data = load_books(data_file)
            except ValueError as e:
                data, self.error = None, str(e)
        self.data = data
        self._lock = threading.Lock()

    def _now(self):
        return _dt.datetime.fromtimestamp(self.clock(), _UTC)

    def _load(self):
        doc = self.store.get("xpbooks")
        raw = doc.get("owned") if isinstance(doc.get("owned"), dict) else {}
        owned = {s: raw[s] if _is_int(raw.get(s)) and 0 <= raw[s] <= MAX_OWNED else 0
                 for s in SIZES}
        used = [c for c in (_clean_use(u) for u in (doc.get("used") if isinstance(
            doc.get("used"), list) else [])) if c is not None][-MAX_USED:]
        sources = self.data["sources"] if self.data else {}
        added = [c for c in (_clean_added(a, sources) for a in (doc.get("added") if isinstance(
            doc.get("added"), list) else [])) if c is not None][-MAX_ADDED:]
        return {"owned": owned, "used": used, "added": added}

    def _save(self, doc):
        doc["used"] = doc["used"][-MAX_USED:]
        doc["added"] = doc["added"][-MAX_ADDED:]
        doc["updated"] = _iso(self._now())
        self.store.put("xpbooks", doc)

    def _counts(self):
        try:
            c = self.weekly() if self.weekly is not None else {}
        except Exception:  # a broken feed never breaks the Leveling card
            return {}
        return c if isinstance(c, dict) else {}

    def _ready(self):
        if self.data is None:
            raise ValueError(f"book table unavailable: {self.error}")

    # -- reads -----------------------------------------------------------------

    def view(self, level, pct, rate, deadlines=()):
        """`books` in GET /api/leveling. Below Lv 60 (or level unknown) only
        {available: False, min_level, note, deadline} (the Lv 60 deadline row,
        e.g. Olvia Academy, when set)."""
        if self.data is None or level is None or level < MIN_LEVEL:
            dl = next((d for d in deadlines if isinstance(d, dict)
                       and d.get("needs_level") == MIN_LEVEL and d.get("state") != "done"), None)
            return {"available": False, "min_level": MIN_LEVEL, "error": self.error,
                    "note": f"books from Lv {MIN_LEVEL}",
                    "deadline": None if dl is None else {
                        "label": dl["label"], "needs_level": dl["needs_level"],
                        "enrol_by_utc": dl["enrol_by_utc"]}}
        now = self._now()
        doc = self._load()
        used = doc["used"]
        per = {s: book_pct(self.data, used, s, level) for s in SIZES}
        week = weekly_expectation(self.data, used, level, self._counts(), doc["added"], now)
        bph = week["pct_week"] / WEEK_H
        combined = (rate or 0) + bph
        return {"available": True, "min_level": MIN_LEVEL, "error": None,
                "owned": doc["owned"], "per_book": per,
                "flagged": any(p["flag"] for p in per.values()),
                "owned_pct": pct_from_owned(self.data, used, doc["owned"], level),
                "to_next": books_to_next(self.data, used, level, pct),
                "weekly": week, "pct_week": week["pct_week"],
                "books_pct_h": round(bph, 6),
                "rate_with_books_pct_h": round(combined, 3) if combined > 0 else None,
                "eta_next_with_books_s": eta_next_s(pct, combined if combined > 0 else None),
                "sizes": [{"id": s, "name": self.data["sizes"][s]["name"]} for s in SIZES],
                "activities": [{"activity": a["activity"], "name": a["name"]}
                               for a in self.data["sources"].values()],
                "source": self.data["source"], "read": self.data["read"],
                "used": [dict(u, index=i, gain=round(gain(u["pct_before"], u["pct_after"]), 3))
                         for i, u in reversed(list(enumerate(used)))][:VIEW_USED]}

    def recent(self, level, since):
        """Plan 066: {size: % of one book at `level`} for sizes still owned with
        a gain (n > 0) added at or after epoch `since`; {} without the table."""
        if self.data is None or level is None:
            return {}
        doc = self._load()
        sizes = {a["size"] for a in doc["added"] if a["n"] > 0
                 and _parse_iso(a["at"]).timestamp() >= since}
        return {s: book_pct(self.data, doc["used"], s, level)["pct"]
                for s in SIZES if s in sizes and doc["owned"][s] > 0}

    def used_index(self, row):
        """Index of the newest used entry equal to `row` on at / size / level /
        pct_before / pct_after, or None (plan 066 undo)."""
        keys = ("at", "size", "level", "pct_before", "pct_after")
        used = self._load()["used"]
        for i in range(len(used) - 1, -1, -1):
            if all(used[i].get(k) == row.get(k) for k in keys):
                return i
        return None

    # -- writes ----------------------------------------------------------------

    def add(self, arg):
        """{size, n[, activity]}: n books gained (negative n corrects a typo)."""
        self._ready()
        if not isinstance(arg, dict) or not {"size", "n"} <= set(arg) \
                or not set(arg) <= {"size", "n", "activity"}:
            raise ValueError("book_add must be {size, n[, activity]}")
        if arg["size"] not in SIZES:
            raise ValueError(f"size must be one of {', '.join(SIZES)}")
        n = arg["n"]
        if not _is_int(n) or not -MAX_N <= n <= MAX_N or n == 0:
            raise ValueError(f"n must be a non-zero int -{MAX_N}..{MAX_N}")
        act = arg.get("activity")
        if act is not None and act not in self.data["sources"]:
            raise ValueError(f"unknown activity: {act}")
        with self._lock:
            doc = self._load()
            have = doc["owned"][arg["size"]] + n
            if have < 0:
                raise ValueError(f"only {doc['owned'][arg['size']]} {arg['size']} owned")
            doc["owned"][arg["size"]] = min(have, MAX_OWNED)
            doc["added"].append({"at": _iso(self._now()), "size": arg["size"], "n": n,
                                 "activity": act})
            self._save(doc)

    def use(self, arg, level):
        """{size, pct_before, pct_after} at the current `level` (Lv 60+): one
        owned book (if any) is spent and the gain recorded."""
        self._ready()
        if not isinstance(arg, dict) or set(arg) != {"size", "pct_before", "pct_after"}:
            raise ValueError("book_use must be {size, pct_before, pct_after}")
        if arg["size"] not in SIZES:
            raise ValueError(f"size must be one of {', '.join(SIZES)}")
        for k in ("pct_before", "pct_after"):
            if not _ok_pct(arg[k]):
                raise ValueError(f"{k} must be a number 0..100 with at most 3 decimals")
        if arg["pct_before"] == arg["pct_after"]:
            raise ValueError("pct_after must differ from pct_before")
        if level is None or level < MIN_LEVEL:
            raise ValueError(f"books need Lv {MIN_LEVEL}+ (log a level sample first)")
        with self._lock:
            doc = self._load()
            row = {"at": _iso(self._now()), "size": arg["size"], "level": level,
                   "pct_before": _norm_pct(arg["pct_before"]),
                   "pct_after": _norm_pct(arg["pct_after"])}
            if doc["owned"][arg["size"]] > 0:
                doc["owned"][arg["size"]] -= 1
                row["from_owned"] = True
            doc["used"].append(row)
            self._save(doc)
        return dict(row)

    def delete(self, idx):
        """Remove used entry `idx` (view `index`); a book spent from the owned
        count goes back."""
        self._ready()
        if not _is_int(idx) or idx < 0:
            raise ValueError("book_del must be a used-book index")
        with self._lock:
            doc = self._load()
            if idx >= len(doc["used"]):
                raise ValueError(f"unknown used book: {idx}")
            row = doc["used"].pop(idx)
            if row.get("from_owned"):
                doc["owned"][row["size"]] = min(doc["owned"][row["size"]] + 1, MAX_OWNED)
            self._save(doc)
