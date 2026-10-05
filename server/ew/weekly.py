"""Weekly content planner (plan 033): which weekly content is reachable now.

`data/weekly_content.json` holds sourced rows {id, name, reset (plan 021 rule),
min_level, ap, dp, ap_kind, per_week, source, verified, note}. A requirement of
0 means "no gate"; null means "gate not sourced" and answers `unknown`, never a
guess. `gate()` compares a row with plan 004's character (level + gs) and, when
plan 023's bracket summary is passed, adds the bonus-AP view for an AP gap.

Store domain `weekly`: {"ticks": {"<id>": ["<iso>", ...]}, "updated"}; a row's
done count is its ticks at or after the row's last reset (derived on read, like
plan 003), capped at `per_week`. Static sourced data plus operator-typed level,
gear and ticks; nothing is read from the game.
"""

import datetime as _dt
import json
import re
import threading
import time
from pathlib import Path

from . import today

DATA_FILE = Path(__file__).resolve().parent / "data" / "weekly_content.json"
FIELDS = ("id", "name", "reset", "min_level", "ap", "dp", "ap_kind", "per_week", "source",
          "verified", "note")
AP_KINDS = ("main", "aap", "kutum")
ID_RE = re.compile(r"^[a-z0-9-]{1,40}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LEVEL_MAX = 80
STAT_MAX = 999  # the plan 004 gs range
PER_WEEK_MAX = 7
MAX_TEXT = 200
MAX_ROWS = 50
STATES = ("eligible", "unknown", "locked")


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _text(v, what, allow_empty=False):
    if not isinstance(v, str) or len(v.strip()) > MAX_TEXT or (not v.strip() and not allow_empty):
        raise ValueError(f"{what} must be {0 if allow_empty else 1}..{MAX_TEXT} characters")
    if any(ord(ch) < 32 or ord(ch) > 126 for ch in v):
        raise ValueError(f"{what} must be printable ASCII")
    return v.strip()


def _req(v, what, hi):
    if v is None:
        return None
    if not _is_int(v) or not 0 <= v <= hi:
        raise ValueError(f"{what} must be null or an int in 0..{hi}")
    return v


def validate_row(r):
    """Normalised copy of one data row, or ValueError."""
    if not isinstance(r, dict) or set(r) != set(FIELDS):
        raise ValueError(f"weekly rows must have exactly {', '.join(FIELDS)}")
    if not isinstance(r["id"], str) or not ID_RE.match(r["id"]):
        raise ValueError("id must match ^[a-z0-9-]{1,40}$")
    rule = today.validate_rule(r["reset"])
    if rule["every"] != "week":
        raise ValueError("reset must be a weekly rule")
    if r["ap_kind"] not in AP_KINDS:
        raise ValueError(f"ap_kind must be one of {', '.join(AP_KINDS)}")
    pw = r["per_week"]
    if not _is_int(pw) or not 1 <= pw <= PER_WEEK_MAX:
        raise ValueError(f"per_week must be an int in 1..{PER_WEEK_MAX}")
    ver = r["verified"]
    try:
        ok = isinstance(ver, str) and DATE_RE.match(ver) and _dt.date.fromisoformat(ver)
    except ValueError:
        ok = False
    if not ok:
        raise ValueError("verified must be a YYYY-MM-DD date")
    return {"id": r["id"], "name": _text(r["name"], "name"), "reset": rule,
            "min_level": _req(r["min_level"], "min_level", LEVEL_MAX),
            "ap": _req(r["ap"], "ap", STAT_MAX), "dp": _req(r["dp"], "dp", STAT_MAX),
            "ap_kind": r["ap_kind"], "per_week": pw, "source": _text(r["source"], "source"),
            "verified": ver, "note": _text(r["note"], "note", allow_empty=True)}


def load_content(path=None):
    """The tracked rows; ValueError on a missing or malformed file."""
    try:
        doc = json.loads(Path(path or DATA_FILE).read_text(encoding="ascii"))
    except (OSError, ValueError) as e:
        raise ValueError(f"weekly content unreadable: {type(e).__name__}") from e
    if not isinstance(doc, list) or not 1 <= len(doc) <= MAX_ROWS:
        raise ValueError(f"weekly content must be a list of 1..{MAX_ROWS} rows")
    rows = [validate_row(r) for r in doc]
    if len({r["id"] for r in rows}) != len(rows):
        raise ValueError("weekly content ids must be unique")
    return rows


def _stat(character, key):
    if not isinstance(character, dict):
        return None
    if key == "level":
        v = character.get("level")
    else:
        gs = character.get("gs") if isinstance(character.get("gs"), dict) else {}
        v = gs.get(key)
    return v if _is_int(v) else None


def gate(row, character, bracket_summary=None):
    """{state: eligible|locked|unknown, needs: {level?, ap?, dp?} (gaps > 0),
    unknown: [stat, ...], bracket: {ap, to_next, next_gain} | None}.
    Any known gap locks the row; otherwise an unsourced requirement or an unset
    stat makes it unknown. `ap_kind` aap reads gs.aap; main and kutum read gs.ap
    (Kutum AP is the sheet AP with the Kutum sub equipped)."""
    ap_key = "aap" if row["ap_kind"] == "aap" else "ap"
    needs, unknown = {}, []
    for name, req, key in (("level", row["min_level"], "level"), ("ap", row["ap"], ap_key),
                           ("dp", row["dp"], "dp")):
        if req == 0:
            continue
        have = _stat(character, key)
        if req is None or have is None:
            unknown.append(name)
        elif have < req:
            needs[name] = req - have
    bracket = None
    if "ap" in needs and isinstance(bracket_summary, dict):
        b = bracket_summary.get(ap_key)
        if isinstance(b, dict) and _is_int(b.get("next_min")) and _is_int(b.get("x")):
            bracket = {"ap": b["x"], "to_next": b["next_min"] - b["x"],
                       "next_gain": b.get("next_gain")}
    state = "locked" if needs else ("unknown" if unknown else "eligible")
    return {"state": state, "needs": needs, "unknown": unknown, "bracket": bracket}


def _iso(when):
    return when.astimezone(_dt.timezone.utc).replace(microsecond=0).isoformat()


def _parse(s):
    if not isinstance(s, str):
        return None
    try:
        when = _dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    return when if when.tzinfo is not None else None


class WeeklyService:
    """`character` returns plan 004's character ({level, gs}) with the
    effective level; `brackets` returns plan 023's summary or None. Either
    feed failing degrades to unknown gates, never an error."""

    def __init__(self, store, clock=time.time, character=None, brackets=None, data_file=None):
        self.store = store
        self.clock = clock
        self.character = character
        self.brackets = brackets
        self._lock = threading.Lock()
        try:
            self.rows, self.error = load_content(data_file), None
        except ValueError as e:  # a broken data file never breaks the Today tab
            self.rows, self.error = [], str(e)

    def _now(self):
        return _dt.datetime.fromtimestamp(self.clock(), _dt.timezone.utc)

    @staticmethod
    def _feed(fn):
        try:
            return fn() if fn is not None else None
        except Exception:  # a broken feed never breaks the Today tab
            return None

    def _ticks(self):
        raw = self.store.get("weekly").get("ticks")
        out = {}
        for r in self.rows:
            vals = raw.get(r["id"]) if isinstance(raw, dict) else None
            out[r["id"]] = [w for w in (_parse(v) for v in (vals if isinstance(vals, list) else []))
                            if w is not None]
        return out

    def _in_period(self, row, ticks, now):
        start = today.last_reset(row["reset"], now)
        return [w for w in ticks.get(row["id"], ()) if start <= w <= now]

    def view(self):
        """`weekly_plan` in GET /api/today."""
        now = self._now()
        ch = self._feed(self.character)
        ch = ch if isinstance(ch, dict) else {}
        summ = self._feed(self.brackets)
        ticks = self._ticks()
        rows = []
        for r in self.rows:
            done = min(len(self._in_period(r, ticks, now)), r["per_week"])
            rows.append(dict(r, done=done, next_reset=_iso(today.next_reset(r["reset"], now)),
                             gate=gate(r, ch, summ)))
        return {"rows": rows, "error": self.error,
                "character": {"level": _stat(ch, "level"), "ap": _stat(ch, "ap"),
                              "aap": _stat(ch, "aap"), "dp": _stat(ch, "dp")}}

    def _find(self, rid):
        if not isinstance(rid, str) or not ID_RE.match(rid):
            raise ValueError("id must match ^[a-z0-9-]{1,40}$")
        for r in self.rows:
            if r["id"] == rid:
                return r
        raise ValueError(f"unknown weekly content: {rid}")

    def _write(self, rid, delta):
        row = self._find(rid)
        with self._lock:
            now = self._now()
            ticks = self._ticks()
            # Only the current period is kept: older ticks can never count again.
            ticks = {r["id"]: [_iso(w) for w in self._in_period(r, ticks, now)] for r in self.rows}
            mine = ticks[rid]
            if delta > 0:
                if len(mine) >= row["per_week"]:
                    raise ValueError(f"{row['name']}: already {row['per_week']}/"
                                     f"{row['per_week']} this period")
                mine.append(_iso(now))
            else:
                if not mine:
                    raise ValueError(f"{row['name']}: nothing ticked this period")
                mine.pop()
            self.store.put("weekly", {"ticks": {k: v for k, v in ticks.items() if v},
                                      "updated": _iso(now)})

    def tick(self, rid):
        self._write(rid, 1)
        return self.view()

    def untick(self, rid):
        self._write(rid, -1)
        return self.view()
