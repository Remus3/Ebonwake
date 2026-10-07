"""Login-day reward tracker (plan 075): qualifying login days per event.

A GameWatch listener (like plan 056 `DiceClock`) over the plan 008 state only;
nothing is read from or sent to the game. Logged-in wall-clock minutes are
summed per UTC calendar date (an interval crossing 00:00 UTC is split); the
last KEEP_DAYS dates are kept. An open interval survives an EW restart only
when the first poll still reads logged_in; any other first state closes it at
`seen`, the last poll that saw it logged in. On first run the history is
backfilled once from the stored plan 062 play window and plan 005 grind
sessions; before the earliest known date the history is unknown ("counting
since"). The operator may mark a past date "I logged in that day".

An Events item (plan 006) may carry `login_rule` {days_needed, min_minutes,
weekend_minutes?}; `progress` is the pure math over the history. Days are UTC
calendar dates (every login notice read in research 0008 resets 00:00 UTC).

Store domain `logindays`: {"dates": {"YYYY-MM-DD": minutes}, "marked":
["YYYY-MM-DD"], "open": <iso>|null, "seen": <iso>|null, "since":
"YYYY-MM-DD"|null, "backfilled": bool}.
"""

import datetime as _dt
import json
import re
import threading
import time
from pathlib import Path

from .gamewatch import _iso, _parse

DOMAIN = "logindays"
KEEP_DAYS = 120
SEEN_S = 60  # tick() refreshes `seen` at most once a minute while logged in
DAYS_RANGE = (1, 60)
MINUTES_RANGE = (0, 600)
RULE_KEYS = ("days_needed", "min_minutes")
RULE_OPTIONAL = ("weekend_minutes",)
PATTERNS_FILE = Path(__file__).resolve().parent / "data" / "login_rule_patterns.json"
DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_UTC = _dt.timezone.utc


def _date(s):
    if not isinstance(s, str) or not DATE_RE.match(s):
        return None
    try:
        return _dt.date.fromisoformat(s)
    except ValueError:
        return None


def _utc_date(ts):
    return _dt.datetime.fromtimestamp(ts, _UTC).date()


def _minutes(v):
    ok = isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1440
    return float(v) if ok else None


# -- history (pure) ------------------------------------------------------------------

def split_minutes(start, end):
    """Epoch interval -> {"YYYY-MM-DD": minutes}, split at 00:00 UTC."""
    out = {}
    t = start
    while t < end:
        day = _utc_date(t)
        nxt = _dt.datetime.combine(day + _dt.timedelta(days=1), _dt.time(0), _UTC).timestamp()
        stop = min(end, nxt)
        out[day.isoformat()] = round(out.get(day.isoformat(), 0.0) + (stop - t) / 60.0, 3)
        t = stop
    return out


def trim(dates, today):
    """Valid dates within the last KEEP_DAYS (today included); later dates dropped."""
    first = today - _dt.timedelta(days=KEEP_DAYS - 1)
    out = {}
    for k, v in (dates.items() if isinstance(dates, dict) else ()):
        d, m = _date(k), _minutes(v)
        if d is not None and m is not None and first <= d <= today:
            out[k] = v
    return out


def _merge(dates, add):
    for k, v in add.items():
        dates[k] = round(min(1440.0, dates.get(k, 0.0) + v), 3)
    return dates


# -- rule ----------------------------------------------------------------------------

def _int_in(v, lo, hi):
    return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


def validate_rule(v):
    """`login_rule` from an API write -> clean dict, None for None; ValueError."""
    if v is None:
        return None
    if (not isinstance(v, dict) or not set(RULE_KEYS) <= set(v)
            or not set(v) <= set(RULE_KEYS + RULE_OPTIONAL)):
        raise ValueError("login_rule must be {days_needed, min_minutes, weekend_minutes?}")
    if not _int_in(v["days_needed"], *DAYS_RANGE):
        raise ValueError(f"login_rule.days_needed must be {DAYS_RANGE[0]}..{DAYS_RANGE[1]}")
    for k in ("min_minutes",) + RULE_OPTIONAL:
        if k in v and not _int_in(v[k], *MINUTES_RANGE):
            raise ValueError(f"login_rule.{k} must be {MINUTES_RANGE[0]}..{MINUTES_RANGE[1]}")
    out = {"days_needed": v["days_needed"], "min_minutes": v["min_minutes"]}
    if "weekend_minutes" in v:
        out["weekend_minutes"] = v["weekend_minutes"]
    return out


def clean_rule(v):
    """Stored `login_rule` -> clean dict or None (a corrupt rule is not tracked)."""
    try:
        return validate_rule(v)
    except ValueError:
        return None


# -- progress (pure) ---------------------------------------------------------------------

def _qualifies(dates, d, need, marked=()):
    m = dates.get(d.isoformat())
    return d.isoformat() in marked or (m is not None and m >= max(1, need))


def progress(rule, starts, ends, dates, today, marked=()):
    """Rule over a UTC date window [starts, ends] (starts None = open) at `today`."""
    marked = set(marked)
    lo = starts if starts is not None else min(
        (d for d in (_date(k) for k in dates) if d is not None), default=today)
    need, min_m = rule["days_needed"], rule["min_minutes"]
    days, d = [], lo
    while d <= min(ends, today):
        days.append(d)
        d += _dt.timedelta(days=1)
    credited = min(need, sum(1 for d in days if _qualifies(dates, d, min_m, marked)))
    today_in = lo <= today <= ends
    today_done = today_in and _qualifies(dates, today, min_m, marked)
    if today > ends:
        left = 0
    else:
        left = (ends - max(today, lo)).days + 1
    remaining = need - credited
    complete = remaining <= 0
    future = left - (1 if today_done else 0)
    lost = not complete and remaining > future
    at_risk = not complete and not lost and not today_done and today_in and remaining >= left
    weekend = None
    wk = rule.get("weekend_minutes")
    if wk is not None:
        ends_w = [d for d in days if d.weekday() >= 5]
        weekend = {"credited": sum(1 for d in ends_w if _qualifies(dates, d, wk)),
                   "total": len(ends_w), "minutes": wk}
    return {"needed": need, "credited": credited, "days_left": left, "today_done": today_done,
            "at_risk": at_risk, "lost": lost, "complete": complete,
            "today_minutes": int(dates.get(today.isoformat(), 0.0)) if today_in else 0,
            "min_minutes": min_m, "weekend": weekend}


def label(row):
    """"Login days 6/14 - 22 days left" (mirrors ewcore.js loginDayText)."""
    left = row["days_left"]
    tail = "ended" if left <= 0 else f"{left} day{'' if left == 1 else 's'} left"
    return f"Login days {row['credited']}/{row['needed']} - {tail}"


# -- suggestion patterns (plan 064 notice text) ----------------------------------------------

def load_patterns(path=None):
    """{gate, weekend, numbers, rules: [{field, re, scale, weekend}]} compiled.
    A bad file raises ValueError (tracked data; a test catches a bad edit)."""
    try:
        doc = json.loads(Path(path or PATTERNS_FILE).read_text(encoding="utf-8"))
        rules = [{"field": r["field"], "re": re.compile(r["re"], re.IGNORECASE),
                  "scale": int(r["scale"]), "weekend": r["weekend"] is True}
                 for r in doc["rules"]]
        if not all(r["field"] in RULE_KEYS + RULE_OPTIONAL for r in rules):
            raise ValueError("unknown field")
        return {"gate": re.compile(doc["gate"], re.IGNORECASE),
                "weekend": re.compile(doc["weekend"], re.IGNORECASE),
                "numbers": {str(k).lower(): int(v) for k, v in doc["numbers"].items()},
                "rules": rules}
    except (OSError, KeyError, TypeError, re.error) as e:
        raise ValueError(f"login_rule_patterns.json: {e}") from None


_PATTERNS = None


def _patterns():
    global _PATTERNS
    if _PATTERNS is None:
        _PATTERNS = load_patterns()
    return _PATTERNS


def suggest_rule(lines, patterns=None):
    """Notice lines -> {days_needed: int|None, min_minutes, weekend_minutes?} or
    None (not a login notice / nothing countable). Only ever a suggestion."""
    p = patterns or _patterns()
    lines = [ln for ln in (lines or ()) if isinstance(ln, str)]
    if not any(p["gate"].search(ln) for ln in lines):
        return None
    found = {}
    for ln in lines:
        wk = bool(p["weekend"].search(ln))
        for r in p["rules"]:
            if r["weekend"] != wk or r["field"] in found:
                continue
            m = r["re"].search(ln)
            if m is None:
                continue
            n = m.group("n").lower()
            n = int(n) if n.isdigit() else p["numbers"].get(n)
            if n is not None:
                found[r["field"]] = n * r["scale"]
    out = {"days_needed": found.get("days_needed"), "min_minutes": found.get("min_minutes", 0)}
    if "weekend_minutes" in found:
        out["weekend_minutes"] = found["weekend_minutes"]
    if out["days_needed"] is None and not out["min_minutes"]:
        return None
    return clean_suggestion(out)


def clean_suggestion(v):
    """A stored suggestion (days_needed may be None) -> clean dict or None."""
    if not isinstance(v, dict) or "days_needed" not in v:
        return None
    days = v["days_needed"]
    probe = dict(v, days_needed=1 if days is None else days)
    rule = clean_rule(probe)
    if rule is None:
        return None
    rule["days_needed"] = days
    return rule


# -- service ---------------------------------------------------------------------------

class LoginDays:
    """`backfill()` -> [(start, end) epoch pairs] of already-stored play time,
    read once on first run (default: the store's playsession `last`, summary
    `last_game` and grind sessions)."""

    def __init__(self, store, clock=time.time, backfill=None):
        self.store = store
        self.clock = clock
        self.backfill = backfill or self._stored_play
        self._lock = threading.Lock()

    # -- store ---------------------------------------------------------------------

    def _stored_play(self):
        out = []
        last = self.store.get("playsession").get("last")
        if isinstance(last, dict):
            out.append((_parse(last.get("start")), _parse(last.get("end"))))
        lg = self.store.get("summary").get("last_game")
        if isinstance(lg, dict):
            out.append((_parse(lg.get("since")), _parse(lg.get("until"))))
        for s in self.store.get("grind").get("sessions") or ():
            if not isinstance(s, dict):
                continue
            start, mins = _parse(s.get("started")), s.get("minutes")
            if start is not None and _int_in(mins, 1, 1440):
                out.append((start, start + mins * 60))
        return [(a, b) for a, b in out if a is not None and b is not None and a < b]

    def _load(self, now):
        doc = self.store.get(DOMAIN)
        today = _utc_date(now)
        d = {"dates": trim(doc.get("dates"), today),
             "marked": sorted({m for m in doc.get("marked") or () if _date(m) is not None}),
             "open": _parse(doc.get("open")), "seen": _parse(doc.get("seen")),
             "since": doc.get("since") if _date(doc.get("since")) else None,
             "backfilled": doc.get("backfilled") is True}
        if not d["backfilled"]:
            seen = {}
            try:
                pairs = self.backfill()
            except Exception:  # noqa: BLE001 - a bad backfill source never blocks tracking
                pairs = []
            for a, b in pairs:
                _merge(seen, split_minutes(a, min(b, now)))
            for k, v in trim(seen, today).items():
                d["dates"][k] = max(d["dates"].get(k, 0.0), v)
            first = min(d["dates"], default=today.isoformat())
            d["since"] = min(first, d["since"] or first)
            d["backfilled"] = True
            self._save(d)
        if d["since"] is None:
            d["since"] = today.isoformat()
        return d

    def _save(self, d):
        self.store.put(DOMAIN, {
            "dates": d["dates"], "marked": d["marked"],
            "open": _iso(d["open"]) if d["open"] is not None else None,
            "seen": _iso(d["seen"]) if d["seen"] is not None else None,
            "since": d["since"], "backfilled": d["backfilled"]})

    @staticmethod
    def _close(d, end, now):
        if end > d["open"]:
            _merge(d["dates"], split_minutes(d["open"], end))
            d["dates"] = trim(d["dates"], _utc_date(now))
        d["open"] = None

    # -- hooks -----------------------------------------------------------------------

    def on_game(self, prev, new, at):
        """Plan 008 change hook (GameWatch.listeners)."""
        with self._lock:
            d = self._load(at)
            if d["open"] is not None and prev != "logged_in" and new != "logged_in":
                # Orphan from a previous EW run: close where EW last saw it.
                seen = d["seen"] if d["seen"] is not None else d["open"]
                self._close(d, min(seen, at), at)
            elif d["open"] is not None and new != "logged_in":
                self._close(d, at, at)
            elif d["open"] is None and new == "logged_in":
                d["open"] = at
            d["seen"] = at if d["open"] is not None else None
            self._save(d)

    def tick(self, state, now):
        """Every poll (GameWatch.pollers): refresh `seen` while logged in."""
        with self._lock:
            d = self._load(now)
            if d["open"] is None or state != "logged_in":
                return
            if d["seen"] is None or now - d["seen"] >= SEEN_S:
                d["seen"] = now
                self._save(d)

    def mark(self, arg):
        """`{date: "YYYY-MM-DD", on: bool}`: the operator logged in that past day."""
        if not isinstance(arg, dict) or set(arg) != {"date", "on"} or not isinstance(
                arg["on"], bool):
            raise ValueError("login_mark must be {date: YYYY-MM-DD, on: true|false}")
        day = _date(arg["date"])
        now = self.clock()
        today = _utc_date(now)
        if day is None or not today - _dt.timedelta(days=KEEP_DAYS - 1) <= day < today:
            raise ValueError(f"login_mark.date must be a past UTC date within {KEEP_DAYS} days")
        with self._lock:
            d = self._load(now)
            marked = set(d["marked"])
            (marked.add if arg["on"] else marked.discard)(day.isoformat())
            d["marked"] = sorted(marked)
            self._save(d)

    # -- reads -----------------------------------------------------------------------

    def history(self, now=None):
        """{dates (open interval included), marked, since, logged_in, today}."""
        now = self.clock() if now is None else now
        with self._lock:
            d = self._load(now)
        dates = dict(d["dates"])
        if d["open"] is not None and now > d["open"]:
            _merge(dates, split_minutes(d["open"], now))
        return {"dates": dates, "marked": list(d["marked"]), "since": d["since"],
                "logged_in": d["open"] is not None, "today": _utc_date(now).isoformat()}

    def rows(self, items, suggestions=None):
        """`login_days` of GET /api/events: {since, today, logged_in, rows, suggest}.
        Rows: open (active / upcoming, not done) items carrying a login_rule and
        an end date. Suggest: such items without a rule whose url has a notice
        suggestion (`suggestions` {url: rule}); missing days = the window length."""
        h = self.history()
        today = _dt.date.fromisoformat(h["today"])
        since = _dt.date.fromisoformat(h["since"])
        rows, suggest = [], []
        for it in items or ():
            if not isinstance(it, dict) or it.get("done") is True:
                continue
            if it.get("status") not in ("active", "upcoming"):
                continue
            ends = _parse(it.get("ends"))
            if ends is None:
                continue
            ends = _utc_date(ends)
            st = _parse(it.get("starts"))
            starts = _utc_date(st) if st is not None else None
            rule = clean_rule(it.get("login_rule"))
            if rule is None:
                sug = clean_suggestion((suggestions or {}).get(it.get("url")))
                if sug is not None:
                    if sug["days_needed"] is None:
                        span = (ends - (starts or today)).days + 1
                        sug["days_needed"] = max(DAYS_RANGE[0], min(DAYS_RANGE[1], span))
                    suggest.append({"id": it["id"], "title": it["title"], "rule": sug,
                                    "label": f"track logins? ({sug['days_needed']} days)"})
                continue
            p = progress(rule, starts, ends, h["dates"], today, h["marked"])
            row = dict(p, id=it["id"], title=it["title"],
                       starts=starts.isoformat() if starts else None, ends=ends.isoformat(),
                       counting_since=h["since"] if starts is None or since > starts else None)
            row["label"] = label(row)
            rows.append(row)
        return {"since": h["since"], "today": h["today"], "logged_in": h["logged_in"],
                "rows": rows, "suggest": suggest}
