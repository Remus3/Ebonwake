"""Weekly maintenance slot (plan 059): "before / after maintenance" -> a UTC instant.

Official event notices end "Oct 15, 2026 (Thu) before maintenance" rather than at
a clock time. The slot lives in tracked `data/maintenance.json` (a UTC time, so
no DST rule applies) and is unverified until the operator confirms it; the
plan 030 setting `events.maintenance_start_utc` overrides the start. `resolve`
is pure: "before" = the slot start on that date, "after" = start + duration,
on whatever weekday the date falls (a holiday maintenance is still that date).
"""

import datetime as _dt
import json
import re
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data" / "maintenance.json"
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
EDGES = ("before", "after")
HHMM_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
MAX_DURATION_MIN = 24 * 60
DEFAULT = {"weekday": "thu", "start_utc": "07:00", "duration_min": 180,
           "source": "built-in default", "verified": False}


def valid_hhmm(v):
    return isinstance(v, str) and bool(HHMM_RE.fullmatch(v))


def _clean(doc):
    """A slot dict from a loaded doc; any bad field falls back to DEFAULT."""
    doc = doc if isinstance(doc, dict) else {}
    out = dict(DEFAULT)
    if doc.get("weekday") in WEEKDAYS:
        out["weekday"] = doc["weekday"]
    if valid_hhmm(doc.get("start_utc")):
        out["start_utc"] = doc["start_utc"]
    d = doc.get("duration_min")
    if isinstance(d, int) and not isinstance(d, bool) and 0 < d <= MAX_DURATION_MIN:
        out["duration_min"] = d
    if isinstance(doc.get("source"), str) and doc["source"]:
        out["source"] = doc["source"]
    out["verified"] = doc.get("verified") is True
    return out


def load_slot(path=DATA):
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        doc = None
    return _clean(doc)


def slot(override=None, path=DATA):
    """The effective slot: the data file, its start replaced by a valid
    `override` (HH:MM UTC); `overridden` says which one is in force."""
    out = load_slot(path)
    out["overridden"] = False
    if valid_hhmm(override):
        out["start_utc"] = override
        out["overridden"] = True
    return out


def _as_date(d):
    if isinstance(d, _dt.datetime):
        return d.date()
    if isinstance(d, _dt.date):
        return d
    if isinstance(d, str):
        return _dt.date.fromisoformat(d)
    raise ValueError("date must be a date or YYYY-MM-DD")


def resolve(date, edge, slot_=None):
    """UTC datetime of the maintenance `edge` ("before" | "after") on `date`."""
    if edge not in EDGES:
        raise ValueError("edge must be before or after")
    s = _clean(slot_) if slot_ is not None else DEFAULT
    hh, mm = (int(x) for x in s["start_utc"].split(":"))
    start = _dt.datetime.combine(_as_date(date), _dt.time(hh, mm), _dt.timezone.utc)
    return start if edge == "before" else start + _dt.timedelta(minutes=s["duration_min"])
