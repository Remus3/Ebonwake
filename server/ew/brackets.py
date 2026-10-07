"""AP/DP brackets (plan 023): sheet AP / AAP -> bonus AP, DP -> damage
reduction rate and all damage reduction.

`data/brackets.json` holds three tables {source, verified, note, rows: [{min,
value}]}: a row's bracket runs from its `min` to the next row's `min` - 1 (the
last row is open-ended), so the rows cannot leave a gap. A `value` of null is a
span the lane did not transcribe: lookups there answer "unknown", never a
guess. Operator overrides (store domain `brackets_override`, same table shape)
replace a whole table. Static sourced data plus operator-typed gear score;
nothing is read from the game.
"""

import datetime as _dt
import json
import re
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parent / "data" / "brackets.json"
TABLES = ("ap", "dp_dr", "dp_all_dr")
FIELDS = {"source", "verified", "note", "rows"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MIN_RANGE = (0, 999)  # the plan 004 gs range
VALUE_MAX = 1000
MAX_ROWS = 120
MAX_TEXT = 200
CLIFF_SPAN = 16


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _text(v, what, allow_empty=False):
    if not isinstance(v, str) or len(v.strip()) > MAX_TEXT or (not v.strip() and not allow_empty):
        raise ValueError(f"{what} must be {0 if allow_empty else 1}..{MAX_TEXT} characters")
    if any(ord(ch) < 32 or ord(ch) > 126 for ch in v):
        raise ValueError(f"{what} must be printable ASCII")
    return v.strip()


def validate_table(t):
    """Normalised copy of one table, or ValueError."""
    if not isinstance(t, dict) or set(t) != FIELDS:
        raise ValueError(f"table must be {{{', '.join(sorted(FIELDS))}}}")
    ok = isinstance(t["verified"], str) and DATE_RE.match(t["verified"])
    try:
        ok = ok and _dt.date.fromisoformat(t["verified"])
    except ValueError:
        ok = False
    if not ok:
        raise ValueError("verified must be a YYYY-MM-DD date")
    rows = t["rows"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ROWS:
        raise ValueError(f"rows must be a list of 1..{MAX_ROWS} {{min, value}}")
    out, last_min, last_val = [], -1, None
    for r in rows:
        if not isinstance(r, dict) or set(r) != {"min", "value"}:
            raise ValueError("each row must be {min, value}")
        lo, v = r["min"], r["value"]
        if not _is_int(lo) or not MIN_RANGE[0] <= lo <= MIN_RANGE[1]:
            raise ValueError(f"min must be an int in {MIN_RANGE[0]}..{MIN_RANGE[1]}")
        if lo <= last_min:
            raise ValueError("row mins must strictly increase")
        if v is not None:
            if not _is_int(v) or not 0 <= v <= VALUE_MAX:
                raise ValueError(f"value must be null or an int in 0..{VALUE_MAX}")
            if last_val is not None and v < last_val:
                raise ValueError("known values must not decrease")
            last_val = v
        out.append({"min": lo, "value": v})
        last_min = lo
    if out[-1]["value"] is None:
        raise ValueError("the last (open-ended) row needs a value")
    return {"source": _text(t["source"], "source"), "verified": t["verified"],
            "note": _text(t["note"], "note", allow_empty=True), "rows": out}


def load_tracked(path=DATA_FILE):
    """The tracked tables; ValueError on a missing or malformed file."""
    try:
        doc = json.loads(Path(path).read_text(encoding="ascii"))
    except (OSError, ValueError) as e:
        raise ValueError(f"bracket table: {e}"[:200]) from e
    if not isinstance(doc, dict) or set(doc) != set(TABLES):
        raise ValueError(f"bracket table must hold {', '.join(TABLES)}")
    return {name: validate_table(doc[name]) for name in TABLES}


def clean_overrides(doc):
    """Store doc -> {table: validated table}; a corrupt entry is dropped."""
    out = {}
    for name in TABLES:
        if not isinstance(doc, dict) or name not in doc:
            continue
        try:
            out[name] = validate_table(doc[name])
        except ValueError:
            continue
    return out


def load(overrides=None, tracked=None):
    """Effective tables: tracked rows with operator overrides on top. Each
    table carries `override` (bool)."""
    base = load_tracked() if tracked is None else tracked
    over = clean_overrides(overrides or {})
    return {name: dict(over.get(name, base.get(name)), override=name in over)
            for name in TABLES if name in over or name in base}


def lookup(rows, x):
    """{x, bracket_min, bracket_max, value, next_min, next_gain} for `x` in
    `rows`. Below the first row the value is 0; next_gain is None when either
    side is unknown or there is no next bracket."""
    i = -1
    for n, r in enumerate(rows):
        if r["min"] <= x:
            i = n
    if i < 0:
        cur_min, value = None, 0
    else:
        cur_min, value = rows[i]["min"], rows[i]["value"]
    nxt = rows[i + 1] if i + 1 < len(rows) else None
    gain = None
    if nxt is not None and value is not None and nxt["value"] is not None:
        gain = nxt["value"] - value
    return {"x": x, "bracket_min": cur_min,
            "bracket_max": nxt["min"] - 1 if nxt else None, "value": value,
            "next_min": nxt["min"] if nxt else None, "next_gain": gain}


def cliff(rows, x):
    """True when the next CLIFF_SPAN points gain more than the previous
    CLIFF_SPAN did; False when either side is unknown."""
    here = lookup(rows, x)["value"]
    ahead = lookup(rows, x + CLIFF_SPAN)["value"]
    back = lookup(rows, max(0, x - CLIFF_SPAN))["value"]
    if None in (here, ahead, back):
        return False
    return ahead - here > here - back


def _reverify(table, epoch):
    """Plan 018 pattern: the newest started XP epoch post-dates `verified`."""
    if not isinstance(epoch, dict) or not isinstance(epoch.get("starts_utc"), str):
        return False
    try:
        start = _dt.datetime.fromisoformat(epoch["starts_utc"]).date().isoformat()
    except ValueError:
        return False
    return table["verified"] < start


def summary(gs, tables, epoch=None):
    """{ap, aap, dp, tables} for a plan 004 gs dict, or None when no stat is
    set. ap / aap use the `ap` table; dp is the `dp_dr` lookup plus `all_dr`."""
    if not isinstance(gs, dict) or all(not _is_int(gs.get(k)) for k in ("ap", "aap", "dp")):
        return None

    def stat(key, name):
        x, t = gs.get(key), tables.get(name)
        if not _is_int(x) or t is None:
            return None
        return dict(lookup(t["rows"], x), cliff=cliff(t["rows"], x))

    dp = stat("dp", "dp_dr")
    if dp is not None and "dp_all_dr" in tables:
        dp["all_dr"] = lookup(tables["dp_all_dr"]["rows"], gs["dp"])
    return {"ap": stat("ap", "ap"), "aap": stat("aap", "ap"), "dp": dp,
            "tables": {name: {"source": t["source"], "verified": t["verified"],
                              "note": t["note"], "override": t.get("override", False),
                              "reverify": _reverify(t, epoch)}
                       for name, t in tables.items()}}
