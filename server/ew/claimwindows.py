"""Reward claim windows (plan 086): claim-by deadlines that outlive the event.

Pure. Plan 064 already GETs each notice's Detail page; `claim_lines` keeps the
sentences of that page matching a tracked pattern (`data/claim_patterns.json`,
data not code) that also hold a date, at most MAX_CLAIMS per notice, each cut
to MAX_TEXT ASCII chars. A date is kept raw ({date, edge, time}, the shape of
plan 059's window points: "Oct 15, 2026 (Thu) before maintenance", "the Oct 29,
2026 (Thu) maintenance" = that maintenance's start, or an explicit HH:MM UTC)
and resolved on every read through plan 064's maintenance notices, so a changed
maintenance setting needs no refetch. A sentence with no date is dropped.

`latest` picks a notice's claim deadline; `keep` says whether it adds anything
to an event item (only when later than the item's own end, or it has none).
No new GET, no new host; nothing here reads or touches the game.
"""

import datetime as _dt
import json
import re
from pathlib import Path

from .maintdigest import ascii_clip, sentences

PATTERNS_FILE = Path(__file__).resolve().parent / "data" / "claim_patterns.json"
MAX_CLAIMS = 3
MAX_TEXT = 200
MAX_PATTERNS = 50
EDGES = ("after", "before")
_MONTHS = {m: n for n, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_HHMM_RE = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
# One date point; "<date> maintenance" with no before / after is that
# maintenance itself, i.e. its start (= "before maintenance").
_POINT_RE = re.compile(
    r"\b(?P<mon>[A-Za-z]{3,9})\.?\s+(?P<day>[0-9]{1,2})(?:st|nd|rd|th)?\b"
    r"(?:,?\s+(?P<year>[0-9]{4}))?(?:\s*\([A-Za-z]{3,4}\.?\))?"
    r"(?:\s*,?\s*(?:(?P<edge>after|before)\s+(?:the\s+)?maintenance"
    r"|(?P<maint>maintenance)\b"
    r"|(?:at\s+)?(?P<hh>[0-9]{1,2}):(?P<mm>[0-9]{2})\s*\(?UTC\)?))?", re.IGNORECASE)


def load_patterns(path=None):
    """Compiled patterns of the tracked file; a bad file raises ValueError."""
    try:
        doc = json.loads(Path(path or PATTERNS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ValueError(f"claim_patterns.json unreadable: {e}") from e
    raw = doc.get("patterns") if isinstance(doc, dict) else None
    if not isinstance(raw, list) or not 0 < len(raw) <= MAX_PATTERNS:
        raise ValueError("claim_patterns.json: patterns must be a non-empty list")
    out = []
    for p in raw:
        if not (isinstance(p, str) and p.strip() and p.isascii()):
            raise ValueError("claim_patterns.json: each pattern is a non-empty ASCII string")
        try:
            out.append(re.compile(p, re.IGNORECASE))
        except re.error as e:
            raise ValueError(f"claim_patterns.json: bad pattern {p!r}: {e}") from e
    return out


def _near(mon, day, ref):
    """A yearless month/day in ref.year - 1 .. + 1, the one nearest `ref`."""
    best = None
    for y in (ref.year - 1, ref.year, ref.year + 1):
        try:
            d = _dt.date(y, mon, day)
        except ValueError:
            continue
        if best is None or abs((d - ref).days) < abs((best - ref).days):
            best = d
    return best


def points(sentence, ref_year, ref_month=None):
    """Every real date point of one sentence -> [{date, edge, time}]."""
    ref = _dt.date(ref_year, ref_month or 7, 15)
    out = []
    for m in _POINT_RE.finditer(sentence):
        mon = _MONTHS.get(m.group("mon")[:3].lower())
        if mon is None or (len(m.group("mon")) > 3 and not _full_month(m.group("mon"))):
            continue
        day = int(m.group("day"))
        try:
            d = _dt.date(int(m.group("year")), mon, day) if m.group("year") else _near(mon, day, ref)
        except ValueError:
            d = None
        if d is None:
            continue
        t = None
        if m.group("hh") is not None:
            hh, mm = int(m.group("hh")), int(m.group("mm"))
            if hh > 23 or mm > 59:
                continue
            t = f"{hh:02d}:{mm:02d}"
        edge = m.group("edge").lower() if m.group("edge") else ("before" if m.group("maint") else None)
        out.append({"date": d.isoformat(), "edge": None if t else edge, "time": t})
    return out


_FULL = ("january", "february", "march", "april", "may", "june", "july", "august", "september",
         "sept", "october", "november", "december")


def _full_month(word):
    w = word.lower()
    return any(f.startswith(w) for f in _FULL)


def _sort_key(pt):
    """Order of raw points under any slot: date, then edge / time."""
    rank = {"before": 1, None: 2, "after": 3}
    return (pt["date"], pt["time"] or "", rank[pt["edge"]] if pt["time"] is None else 0)


def extract(lines, ref_year, ref_month, patterns):
    """Sentences of `lines` matching any pattern that hold a date -> up to
    MAX_CLAIMS {text, until}; `until` is the latest point of the sentence."""
    out = []
    for s in sentences(lines):
        if not any(p.search(s) for p in patterns):
            continue
        pts = points(s, ref_year, ref_month)
        if not pts:
            continue  # a claim sentence without a date says nothing we can count down
        text = ascii_clip(s, MAX_TEXT)
        if not text or any(c["text"] == text for c in out):
            continue
        out.append({"text": text, "until": max(pts, key=_sort_key)})
        if len(out) >= MAX_CLAIMS:
            break
    return out


def claim_lines(lines, ref_year, ref_month=None, path=None):
    """`extract` with the tracked patterns; an unreadable file yields nothing
    (the notice read itself never fails on it - a test pins the file)."""
    try:
        patterns = load_patterns(path)
    except ValueError:
        return []
    return extract(lines, ref_year, ref_month, patterns)


def _clean_point(p):
    if not (isinstance(p, dict) and set(p) == {"date", "edge", "time"}):
        return None
    if not (isinstance(p["date"], str) and _DATE_RE.match(p["date"])):
        return None
    try:
        _dt.date.fromisoformat(p["date"])
    except ValueError:
        return None
    if p["edge"] not in (None,) + EDGES:
        return None
    if p["time"] is not None and not (isinstance(p["time"], str) and _HHMM_RE.match(p["time"])):
        return None
    return dict(p)


def clean_claims(raw):
    """A cached `claims` list re-validated (a corrupt cache never reaches the UI)."""
    out = []
    for c in raw if isinstance(raw, list) else []:
        if not isinstance(c, dict) or set(c) != {"text", "until"}:
            continue
        t, u = c["text"], _clean_point(c["until"])
        if u is None or not (isinstance(t, str) and 0 < len(t) <= MAX_TEXT and t.isascii()
                             and t.isprintable()):
            continue
        if all(x["text"] != t for x in out):
            out.append({"text": t, "until": u})
    return out[:MAX_CLAIMS]


def latest(claims, resolve):
    """(until UTC datetime, text) of the latest claim deadline, or None.
    `resolve(point)` -> UTC datetime (raises on a point it cannot place)."""
    best = None
    for c in clean_claims(claims):
        try:
            when = resolve(c["until"])
        except (ValueError, OverflowError, TypeError):
            continue
        if when is None:
            continue
        if best is None or when > best[0]:
            best = (when, c["text"])
    return best


def keep(claim_until, ends):
    """A claim window adds something only when it outlives the event's end
    (or the event has none); a same-moment claim adds nothing."""
    if claim_until is None:
        return False
    return ends is None or claim_until > ends
