"""Today checklist (plan 003 slice A): NA reset clocks and the `today` store domain.

Operator ticks plus a seed; nothing is read from the game. A tick is a timestamp,
never a boolean: an item is done when `ticked_at >= last reset of its kind`. Reset
is derived on every read, so there is no cron and no rollover job, and a server
that was down over a reset is still correct.
"""

import datetime as _dt
import json
import re
import threading
import time
from pathlib import Path

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


EVERY = ("day", "week")
AT_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
# Plan 021: an item may carry its own rule; one without keeps its kind's default.
DEFAULT_RULES = {"daily": {"every": "day", "at": "00:00"},
                 "weekly": {"every": "week", "weekday": THURSDAY, "at": "00:00"},
                 "event": {"every": "day", "at": "00:00"}}
RULE_KIND = {"day": "daily", "week": "weekly"}
PRESETS_FILE = Path(__file__).resolve().parent / "data" / "reset_rules.json"
PRESET_FIELDS = ("name", "reset", "source", "verified")
# Plan 056: optional play-minute grant points (the dice row: one at login, more
# after 30 / 60 logged-in minutes).
PRESET_OPTIONAL = ("grants_at_min",)
MAX_SOURCE = 200
MAX_GRANTS = 10
DICE_PRESET = "Black Spirit's Adventure dice"
DICE_FALLBACK = {"every": "day", "at": "05:00"}, [0, 30, 60]
# Plan 068: optional auto rule per row (data, not code). `login` ticks on the
# first logged_in seen after the row's reset; `logged_minutes:N` ticks once the
# plan 056 dice clock counts N logged-in minutes since the reset;
# `ready_minutes:N` only marks the row ready (the dice roll stays a tick).
AUTO_RE = re.compile(r"^(login|(logged_minutes|ready_minutes):([1-9]\d{0,3}))$")
AUTO_MAX_MIN = 1440
SEED_AUTO = {"attendance-reward": "login", "black-spirits-adventure-dice": "ready_minutes:60"}


# -- reset clocks (same rule as app/shared/ewcore.js) --------------------------

def _utc(now):
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(_dt.timezone.utc)


def _midnight(now):
    n = _utc(now)
    return _dt.datetime(n.year, n.month, n.day, tzinfo=_dt.timezone.utc)


def last_reset(rule, now):
    """Most recent reset of a validated `rule` at or before `now` (UTC)."""
    hh, mm = rule["at"].split(":")
    at = _dt.timedelta(hours=int(hh), minutes=int(mm))
    day = _midnight(now)
    if rule["every"] == "day":
        when, period = day + at, _DAY
    else:
        when, period = day - _DAY * ((day.weekday() - rule["weekday"]) % 7) + at, 7 * _DAY
    return when - period if when > _utc(now) else when


def next_reset(rule, now):
    return last_reset(rule, now) + (_DAY if rule["every"] == "day" else 7 * _DAY)


def last_daily_reset(now):
    """NA daily reset: the most recent 00:00 UTC at or before `now`."""
    return last_reset(DEFAULT_RULES["daily"], now)


def last_weekly_reset(now):
    """NA weekly reset: the most recent Thursday 00:00 UTC at or before `now`."""
    return last_reset(DEFAULT_RULES["weekly"], now)


def next_daily_reset(now):
    return next_reset(DEFAULT_RULES["daily"], now)


def next_weekly_reset(now):
    return next_reset(DEFAULT_RULES["weekly"], now)


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


def validate_rule(rule):
    """`{every, weekday? (week only, Mon=0), at? (HH:MM UTC, default 00:00)}` ->
    normalised rule; raises ValueError."""
    if not isinstance(rule, dict):
        raise ValueError("reset must be an object {every, weekday?, at?}")
    extra = set(rule) - {"every", "weekday", "at"}
    if extra:
        raise ValueError(f"unknown reset field(s): {', '.join(sorted(extra))}")
    every = rule.get("every")
    if every not in EVERY:
        raise ValueError(f"every must be one of {', '.join(EVERY)}")
    at = rule.get("at", "00:00")
    if not isinstance(at, str) or not AT_RE.match(at):
        raise ValueError("at must be HH:MM 00:00-23:59 (UTC)")
    if every == "day":
        if "weekday" in rule:
            raise ValueError("weekday only with every week")
        return {"every": "day", "at": at}
    wd = rule.get("weekday")
    if not isinstance(wd, int) or isinstance(wd, bool) or not 0 <= wd <= 6:
        raise ValueError("weekday must be an int 0-6 (Mon=0)")
    return {"every": "week", "weekday": wd, "at": at}


def _check_reset(kind, reset):
    """Normalised rule for an item of `kind`, or None for the kind default."""
    if reset is None:
        return None
    if kind == "event":
        raise ValueError("event items take no reset (they last until their end date)")
    rule = validate_rule(reset)
    if RULE_KIND[rule["every"]] != kind:
        raise ValueError("reset every must match kind (daily = day, weekly = week)")
    return rule


def _rule(it):
    return it.get("reset") or DEFAULT_RULES[it["kind"]]


def load_presets(path=None):
    """Tracked seed `data/reset_rules.json` -> [{name, kind, reset, source, verified}].
    Raises ValueError on any malformed row (a test pins the tracked file)."""
    try:
        doc = json.loads(Path(path or PRESETS_FILE).read_text(encoding="ascii"))
    except (OSError, ValueError) as e:
        raise ValueError(f"reset presets unreadable: {type(e).__name__}") from e
    if not isinstance(doc, list):
        raise ValueError("reset presets must be a list")
    out = []
    for row in doc:
        if (not isinstance(row, dict) or not set(PRESET_FIELDS) <= set(row)
                or set(row) - set(PRESET_FIELDS) - set(PRESET_OPTIONAL)):
            raise ValueError(f"preset rows must have exactly {', '.join(PRESET_FIELDS)}"
                             f" (+ optional {', '.join(PRESET_OPTIONAL)})")
        name, source = row["name"], row["source"]
        if not (isinstance(name, str) and 0 < len(name.strip()) <= MAX_TITLE
                and isinstance(source, str) and 0 < len(source.strip()) <= MAX_SOURCE
                and isinstance(row["verified"], bool)):
            raise ValueError("preset name/source must be text and verified a bool")
        rule = validate_rule(row["reset"])
        preset = {"name": name, "kind": RULE_KIND[rule["every"]], "reset": rule,
                  "source": source, "verified": row["verified"]}
        if "grants_at_min" in row:
            preset["grants_at_min"] = _check_grants(row["grants_at_min"])
        out.append(preset)
    return out


def _check_grants(g):
    """Strictly ascending ints 0..1440, 1..MAX_GRANTS of them; raises ValueError."""
    if (not isinstance(g, list) or not 0 < len(g) <= MAX_GRANTS
            or any(not isinstance(m, int) or isinstance(m, bool) or not 0 <= m <= 1440
                   for m in g)
            or any(a >= b for a, b in zip(g, g[1:]))):
        raise ValueError("grants_at_min must be ascending ints 0..1440")
    return list(g)


def dice_preset(path=None):
    """Plan 056: the dice row -> (rule, grants_at_min, verified); a missing or
    malformed seed falls back to the 05:00 / [0, 30, 60] community rule."""
    try:
        rows = load_presets(path)
    except ValueError:
        rows = []
    for r in rows:
        if r["name"] == DICE_PRESET and r["kind"] == "daily" and "grants_at_min" in r:
            return r["reset"], r["grants_at_min"], r["verified"]
    rule, grants = DICE_FALLBACK
    return dict(rule), list(grants), False


def _presets():
    try:
        return load_presets()
    except ValueError:
        return []


def parse_auto(rule):
    """Plan 068 row rule -> (kind, minutes|None); raises ValueError."""
    m = AUTO_RE.match(rule) if isinstance(rule, str) else None
    if m is None or (m.group(3) and int(m.group(3)) > AUTO_MAX_MIN):
        raise ValueError("auto must be login, logged_minutes:N or ready_minutes:N"
                         f" (N 1..{AUTO_MAX_MIN})")
    return (m.group(1), None) if m.group(2) is None else (m.group(2), int(m.group(3)))


def _check_auto(kind, auto):
    if auto is None:
        return None
    parse_auto(auto)
    if kind == "event":
        raise ValueError("event items take no auto rule")
    return auto


def validate_new(entry):
    """`{title, kind, until?, reset?, auto?}` -> normalised dict; raises ValueError."""
    if not isinstance(entry, dict):
        raise ValueError("add must be an object {title, kind, until?, reset?, auto?}")
    extra = set(entry) - {"title", "kind", "until", "reset", "auto"}
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
    out = {"title": title, "kind": kind, "until": _check_until(entry.get("until"))}
    rule = _check_reset(kind, entry.get("reset"))
    if rule is not None:
        out["reset"] = rule
    auto = _check_auto(kind, entry.get("auto"))
    if auto is not None:
        out["auto"] = auto
    return out


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
    out = {"id": iid, "title": title, "kind": kind, "until": until, "order": order}
    try:
        rule = _check_reset(kind, it.get("reset"))
    except ValueError:
        rule = None  # a corrupt rule degrades to the kind default
    if rule is not None:
        out["reset"] = rule
    try:
        auto = _check_auto(kind, it.get("auto"))
    except ValueError:
        auto = None  # a corrupt auto rule degrades to none
    if auto is not None:
        out["auto"] = auto
    return out


def _aux_map(raw, ids, ok):
    return ({k: v for k, v in raw.items() if k in ids and ok(v)}
            if isinstance(raw, dict) else {})


class TodayService:
    """Store domain `today`: {"items": [{id, title, kind, until, order, reset?,
    auto?}], "ticks": {"<id>": "<iso>"}, "by": {"<id>": "auto"} (plan 068: who
    made the current tick), "blocked": {"<id>": "<iso>"} (plan 068: an untick of
    an auto row blocks auto ticks until the row's next reset), "updated"}."""

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
                    if iid in SEED_AUTO:
                        items[-1]["auto"] = SEED_AUTO[iid]
                store.put("today", {"items": items, "ticks": {}, "updated": _iso(self._now())})
            else:
                self._seed_auto(doc)

    def _seed_auto(self, doc):
        """Plan 068: a store seeded before the rule field gets the seed rules once
        (a row that already has an `auto` key, even null, is left alone)."""
        raw = doc.get("items")
        if not isinstance(raw, list):
            return
        changed = False
        for it in raw:
            if isinstance(it, dict) and it.get("id") in SEED_AUTO and "auto" not in it:
                it["auto"] = SEED_AUTO[it["id"]]
                changed = True
        if changed:
            self.store.put("today", doc)

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
        aux = {"by": _aux_map(doc.get("by"), seen, lambda v: v == "auto"),
               "blocked": _aux_map(doc.get("blocked"), seen,
                                   lambda v: _parse_iso(v) is not None)}
        return uniq, ticks, aux

    def _save(self, items, ticks, aux):
        for n, it in enumerate(items):
            it["order"] = n
        self.store.put("today", {"items": items, "ticks": ticks, "by": aux["by"],
                                 "blocked": aux["blocked"], "updated": _iso(self._now())})

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
        today_iso = now.date().isoformat()
        items, ticks, aux = self._load()
        out = []
        for it in items:
            if it["until"] is not None and today_iso > it["until"]:
                continue
            rule = _rule(it)
            when = _parse_iso(ticks.get(it["id"]))
            done = when is not None and when >= last_reset(rule, now)
            row = {"id": it["id"], "title": it["title"], "kind": it["kind"],
                   "until": it["until"], "done": done,
                   "ticked_at": _iso(when) if when is not None else None}
            if "reset" in it:  # plan 021: own rule + own countdown
                row.update(reset=it["reset"], next_reset=_iso(next_reset(rule, now)))
            if "auto" in it:  # plan 068: rule, who ticked, undo block
                blk = _parse_iso(aux["blocked"].get(it["id"]))
                row.update(auto=it["auto"],
                           by="auto" if done and aux["by"].get(it["id"]) == "auto" else None,
                           auto_blocked=blk is not None and blk >= last_reset(rule, now))
            out.append(row)
        return {"now": _iso(now), "daily_reset": _iso(next_daily_reset(now)),
                "weekly_reset": _iso(next_weekly_reset(now)), "items": out,
                "reset_presets": _presets()}

    def source(self):
        """`/api/state` sources.today: {updated, status: "ok"}."""
        upd = _parse_iso(self.store.get("today").get("updated"))
        return {"updated": _iso(upd) if upd is not None else None, "status": "ok"}

    # -- writes (each returns the GET body) -----------------------------------

    def tick(self, iid):
        with self._lock:
            items, ticks, aux = self._load()
            self._find(items, iid)
            ticks[iid] = _iso(self._now())
            aux["by"].pop(iid, None)  # an operator tick
            self._save(items, ticks, aux)
        return self.view()

    def untick(self, iid):
        with self._lock:
            items, ticks, aux = self._load()
            it = items[self._find(items, iid)]
            ticks.pop(iid, None)
            aux["by"].pop(iid, None)
            if "auto" in it:  # plan 068: the undo holds until the row's next reset
                aux["blocked"][iid] = _iso(self._now())
            self._save(items, ticks, aux)
        return self.view()

    def auto_tick(self, iid, at):
        """Plan 068: tick `iid` at epoch `at` as `by: auto` when it has a ticking
        rule, is open for the period in force at `at` and no undo blocks it.
        Returns True when it ticked. Never raises on an unknown id."""
        when = _dt.datetime.fromtimestamp(at, _dt.timezone.utc)
        with self._lock:
            items, ticks, aux = self._load()
            it = next((i for i in items if i["id"] == iid), None)
            if it is None or "auto" not in it or parse_auto(it["auto"])[0] == "ready_minutes":
                return False
            reset = last_reset(_rule(it), when)
            done = _parse_iso(ticks.get(iid))
            blk = _parse_iso(aux["blocked"].get(iid))
            if (done is not None and done >= reset) or (blk is not None and blk >= reset):
                return False
            ticks[iid], aux["by"][iid] = _iso(when), "auto"
            self._save(items, ticks, aux)
        return True

    def auto_rows(self):
        """Plan 068: [(id, rule, reset rule)] for every row with an auto rule."""
        items, _, _ = self._load()
        return [(it["id"], it["auto"], _rule(it)) for it in items if "auto" in it]

    def add(self, entry):
        e = validate_new(entry)
        with self._lock:
            items, ticks, aux = self._load()
            if len(items) >= MAX_ITEMS:
                raise ValueError(f"at most {MAX_ITEMS} items")
            e["id"] = self._unique(slug(e["title"]), {i["id"] for i in items})
            items.append(dict(e, order=len(items)))
            self._save(items, ticks, aux)
        return self.view()

    def remove(self, iid):
        with self._lock:
            items, ticks, aux = self._load()
            del items[self._find(items, iid)]
            ticks.pop(iid, None)
            aux["by"].pop(iid, None)
            aux["blocked"].pop(iid, None)
            self._save(items, ticks, aux)
        return self.view()

    def move(self, arg):
        """`{id, to}`: to = 0-based position in the full list, clamped to the end."""
        if not isinstance(arg, dict) or set(arg) != {"id", "to"}:
            raise ValueError("move must be {id, to}")
        to = arg["to"]
        if not isinstance(to, int) or isinstance(to, bool) or to < 0:
            raise ValueError("to must be a non-negative int")
        with self._lock:
            items, ticks, aux = self._load()
            it = items.pop(self._find(items, arg["id"]))
            items.insert(min(to, len(items)), it)
            self._save(items, ticks, aux)
        return self.view()
