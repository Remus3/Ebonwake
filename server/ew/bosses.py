"""World boss schedule (plan 031): NA table as sourced data, DST-correct next spawns.

The table is tracked static data (`data/world_bosses_na.json`, Pacific Time,
transcribed from the official Adventurer's Guide via research 0003); nothing reads
the in-game boss notification. Pacific time is computed with the US rule (DST from
the second Sunday of March 02:00 to the first Sunday of November 02:00 local), not
`zoneinfo`, which has no tz data on Windows without a package. Loot ticks are
operator clicks stored in the `bosses` store domain.
"""

import datetime as _dt
import json
import re
import threading
import time
from pathlib import Path

from .today import DATE_RE, last_weekly_reset, next_weekly_reset

TABLE_FILE = Path(__file__).resolve().parent / "data" / "world_bosses_na.json"
AT_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
GARMOTH = "Garmoth"
TICK_WINDOW_DAYS = 7  # a tick may name today's PT day or up to this many days back
DST_ASSUMPTION = ("spawns assumed to stay on PT wall clock across DST "
                  "(unverified; re-check after 2026-11-01)")
_DAY = _dt.timedelta(days=1)
_UTC = _dt.timezone.utc


# -- Pacific time (US rule) ---------------------------------------------------

def _nth_sunday(year, month, n):
    first = _dt.date(year, month, 1)
    return first + _dt.timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))


def _dst_bounds_local(year):
    """DST [start, end) in PT wall clock: 2nd Sun Mar 02:00 .. 1st Sun Nov 02:00."""
    two = _dt.time(2, 0)
    return (_dt.datetime.combine(_nth_sunday(year, 3, 2), two),
            _dt.datetime.combine(_nth_sunday(year, 11, 1), two))


def _utc(now):
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(_UTC)


def pt_offset_hours(now):
    """UTC offset of Pacific time at instant `now`: -7 (PDT) or -8 (PST)."""
    n = _utc(now)
    start, end = _dst_bounds_local(n.year)
    start_utc = (start + _dt.timedelta(hours=8)).replace(tzinfo=_UTC)  # 02:00 PST
    end_utc = (end + _dt.timedelta(hours=7)).replace(tzinfo=_UTC)      # 02:00 PDT
    return -7 if start_utc <= n < end_utc else -8


def utc_to_pt(now):
    """Instant -> naive PT wall-clock datetime."""
    n = _utc(now)
    return (n + _dt.timedelta(hours=pt_offset_hours(n))).replace(tzinfo=None)


def pt_to_utc(local):
    """Naive PT wall-clock datetime -> aware UTC. The repeated 01:xx hour in
    November resolves to PDT (first occurrence); the skipped 02:xx hour in March
    resolves to PDT too. No table slot falls in either hour."""
    if local.tzinfo is not None:
        raise ValueError("local must be a naive PT datetime")
    start, end = _dst_bounds_local(local.year)
    off = 7 if start <= local < end else 8
    return (local + _dt.timedelta(hours=off)).replace(tzinfo=_UTC)


def _iso(when):
    return when.astimezone(_UTC).replace(microsecond=0).isoformat()


def _iso_pt(local):
    off = _dt.timezone(_dt.timedelta(hours=pt_offset_hours(pt_to_utc(local))))
    return local.replace(tzinfo=off).isoformat()


def _parse_iso(s):
    if not isinstance(s, str):
        return None
    try:
        when = _dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    return when if when.tzinfo is not None else None


# -- table -----------------------------------------------------------------------

def load_table(path=None):
    """Tracked `data/world_bosses_na.json` -> validated doc; raises ValueError."""
    try:
        doc = json.loads(Path(path or TABLE_FILE).read_text(encoding="ascii"))
    except (OSError, ValueError) as e:
        raise ValueError(f"boss table unreadable: {type(e).__name__}") from e
    if not isinstance(doc, dict) or doc.get("tz") != "PT":
        raise ValueError("boss table must be an object with tz PT")
    for key in ("source", "verified", "note"):
        if not isinstance(doc.get(key), str) or not doc[key]:
            raise ValueError(f"boss table {key} must be text")
    rules = doc.get("rules")
    if not (isinstance(rules, dict) and isinstance(rules.get("despawn_min"), int)
            and isinstance(rules.get("short_despawn"), dict)
            and all(isinstance(v, int) for v in rules["short_despawn"].values())
            and isinstance(rules.get("garmoth_loot_per_week"), int)):
        raise ValueError("boss table rules malformed")
    slots = doc.get("slots")
    if not isinstance(slots, list) or not slots:
        raise ValueError("boss table slots must be a non-empty list")
    for s in slots:
        if not (isinstance(s, dict) and set(s) == {"weekday", "at", "bosses"}):
            raise ValueError("slot must have exactly weekday, at, bosses")
        wd = s["weekday"]
        if not isinstance(wd, int) or isinstance(wd, bool) or not 0 <= wd <= 6:
            raise ValueError("slot weekday must be an int 0-6 (Mon=0)")
        if not isinstance(s["at"], str) or not AT_RE.match(s["at"]):
            raise ValueError("slot at must be HH:MM")
        if not (isinstance(s["bosses"], list) and s["bosses"]
                and all(isinstance(b, str) and b for b in s["bosses"])):
            raise ValueError("slot bosses must be a non-empty list of names")
    return doc


def _by_weekday(table):
    out = {d: [] for d in range(7)}
    for s in table["slots"]:
        out[s["weekday"]].append(s)
    for rows in out.values():
        rows.sort(key=lambda s: s["at"])
    return out


def _spawn(table, slot, day):
    hh, mm = slot["at"].split(":")
    local = _dt.datetime.combine(day, _dt.time(int(hh), int(mm)))
    rules = table["rules"]
    at = pt_to_utc(local)
    despawn = max(rules["short_despawn"].get(b, rules["despawn_min"]) for b in slot["bosses"])
    return at, {"bosses": list(slot["bosses"]), "at_utc": _iso(at), "at_pt": _iso_pt(local),
                "day": day.isoformat(), "despawn_min": despawn}


def next_spawns(now_utc, n=3, table=None):
    """The next `n` spawns at or after `now_utc`: [{bosses, at_utc, at_pt, day,
    despawn_min}] in time order. `day` is the PT table day."""
    now = _utc(now_utc)
    table = table or load_table()
    week = _by_weekday(table)
    out, day = [], utc_to_pt(now).date()
    for _ in range(7 * (n // max(1, len(table["slots"])) + 2)):
        for slot in week[day.weekday()]:
            at, row = _spawn(table, slot, day)
            if at >= now:
                out.append(row)
                if len(out) >= n:
                    return out
        day += _DAY
    return out


# -- service ---------------------------------------------------------------------

class BossService:
    """Store domain `bosses`: {"looted": {"<PT day>|<boss>": "<iso ticked_at>"},
    "updated": "<iso>"}."""

    def __init__(self, store, clock=time.time, table=None):
        self.store = store
        self.clock = clock
        self.table = table or load_table()
        self._week = _by_weekday(self.table)
        self._lock = threading.Lock()

    def _now(self):
        return _dt.datetime.fromtimestamp(self.clock(), _UTC)

    def _load(self):
        raw = self.store.get("bosses").get("looted")
        out = {}
        for k, v in (raw.items() if isinstance(raw, dict) else ()):
            day, _, boss = k.partition("|") if isinstance(k, str) else ("", "", "")
            if DATE_RE.match(day) and boss and _parse_iso(v) is not None:
                out[k] = v
        return out

    def _check(self, arg, now):
        if not isinstance(arg, dict) or set(arg) != {"boss", "day"}:
            raise ValueError("tick must be {boss, day}")
        boss, day = arg["boss"], arg["day"]
        if not isinstance(day, str) or not DATE_RE.match(day):
            raise ValueError("day must be an ISO date YYYY-MM-DD (PT)")
        try:
            d = _dt.date.fromisoformat(day)
        except ValueError:
            raise ValueError("day must be a real date") from None
        today = utc_to_pt(now).date()
        if not today - TICK_WINDOW_DAYS * _DAY <= d <= today:
            raise ValueError(f"day must be today (PT) or up to {TICK_WINDOW_DAYS} days back")
        if not isinstance(boss, str) or not any(
                boss in s["bosses"] for s in self._week[d.weekday()]):
            raise ValueError(f"boss must be one that spawns on {day} (PT)")
        return f"{day}|{boss}"

    def _save(self, looted, now):
        floor = (utc_to_pt(now).date() - TICK_WINDOW_DAYS * _DAY).isoformat()
        looted = {k: v for k, v in looted.items() if k.split("|", 1)[0] >= floor}
        self.store.put("bosses", {"looted": looted, "updated": _iso(now)})

    def view(self):
        """GET /api/bosses body."""
        now = self._now()
        today = utc_to_pt(now).date()
        remaining = []
        for slot in self._week[today.weekday()]:
            at, row = _spawn(self.table, slot, today)
            if at + _dt.timedelta(minutes=row["despawn_min"]) > now:
                remaining.append(dict(row, up=at <= now))
        looted_raw = self._load()
        looted = {}
        for k in sorted(looted_raw):
            day, _, boss = k.partition("|")
            looted.setdefault(day, []).append(boss)
        reset = last_weekly_reset(now)
        garmoth = sum(1 for k, v in looted_raw.items()
                      if k.endswith("|" + GARMOTH) and _parse_iso(v) >= reset)
        return {"now": _iso(now), "tz": self.table["tz"], "source": self.table["source"],
                "verified": self.table["verified"], "dst_assumption": DST_ASSUMPTION,
                "rules": self.table["rules"],
                "next": next_spawns(now, 3, self.table),
                "today": {"day": today.isoformat(), "remaining": remaining},
                "looted": looted,
                "garmoth": {"looted": garmoth,
                            "cap": self.table["rules"]["garmoth_loot_per_week"],
                            "week_reset": _iso(next_weekly_reset(now))}}

    def tick(self, arg):
        with self._lock:
            now = self._now()
            key = self._check(arg, now)
            looted = self._load()
            looted.setdefault(key, _iso(now))
            self._save(looted, now)
        return self.view()

    def untick(self, arg):
        with self._lock:
            now = self._now()
            key = self._check(arg, now)
            looted = self._load()
            looted.pop(key, None)
            self._save(looted, now)
        return self.view()
