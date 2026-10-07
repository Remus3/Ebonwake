"""Grind spot recommender (plan 012): where to grind next for an AP/DP/level.

The table is tracked data (`data/grind_spots.json`) seeded from public
community guides in coarse bands; every row carries its source and the date it
was transcribed, and the card says "community recommendation, verify". Nothing
is read from the game. The character comes from plan 004's progress store;
query parameters override it for what-if. Pure ranking below; the service only
gathers inputs.
"""

import json
import re
from pathlib import Path

from .levels import LEVEL_RANGE, MONSTER_LEVEL_RANGE, outlevel_dr
from .today import _parse_iso

DATA_FILE = Path(__file__).resolve().parent / "data" / "grind_spots.json"
FIELDS = ("id", "name", "region", "ap_min", "dp_min", "level_min", "xp_tier",
          "silver_tier", "notes", "source", "verified")
OPTIONAL = ("monster_level", "epoch")  # plan 018: level-gap note, re-verify badge
GOALS = ("xp", "silver")
STAT_RANGE = (0, 999)  # matches progress GS_RANGE
TIER_RANGE = (1, 5)
TOP_N = 3
UNLOCK_N = 2
MAX_TEXT = 200
ID_RE = re.compile(r"^[a-z0-9-]{1,40}$")
DATE_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")
DISCLAIMER = "community recommendation, verify in game"


def _ok_int(v, lo, hi):
    return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


def _ok_text(v, allow_empty=False):
    return (isinstance(v, str) and (allow_empty or v.strip()) and len(v) <= MAX_TEXT
            and all(32 <= ord(ch) < 127 for ch in v))


# -- schema --------------------------------------------------------------------

def validate_row(row):
    """Raise ValueError unless `row` is exactly one well-formed table row."""
    if not isinstance(row, dict) or not set(FIELDS) <= set(row) <= set(FIELDS + OPTIONAL):
        raise ValueError(f"row must have exactly {', '.join(FIELDS)} "
                         f"(optional {', '.join(OPTIONAL)})")
    if not isinstance(row["id"], str) or not ID_RE.match(row["id"]):
        raise ValueError("id must match ^[a-z0-9-]{1,40}$")
    for k in ("name", "region", "source"):
        if not _ok_text(row[k]):
            raise ValueError(f"{row['id']}: {k} must be 1..{MAX_TEXT} printable ASCII")
    if not _ok_text(row["notes"], allow_empty=True):
        raise ValueError(f"{row['id']}: notes must be printable ASCII")
    if not isinstance(row["verified"], str) or not DATE_RE.match(row["verified"]):
        raise ValueError(f"{row['id']}: verified must be a YYYY-MM-DD date")
    for k in ("ap_min", "dp_min"):
        if not _ok_int(row[k], *STAT_RANGE):
            raise ValueError(f"{row['id']}: {k} must be an int {STAT_RANGE[0]}..{STAT_RANGE[1]}")
    if not _ok_int(row["level_min"], *LEVEL_RANGE):
        raise ValueError(f"{row['id']}: level_min must be an int "
                         f"{LEVEL_RANGE[0]}..{LEVEL_RANGE[1]}")
    for k in ("xp_tier", "silver_tier"):
        if not _ok_int(row[k], *TIER_RANGE):
            raise ValueError(f"{row['id']}: {k} must be an int {TIER_RANGE[0]}..{TIER_RANGE[1]}")
    if "monster_level" in row and not _ok_int(row["monster_level"], *MONSTER_LEVEL_RANGE):
        raise ValueError(f"{row['id']}: monster_level must be an int "
                         f"{MONSTER_LEVEL_RANGE[0]}..{MONSTER_LEVEL_RANGE[1]}")
    if "epoch" in row and (not isinstance(row["epoch"], str) or not ID_RE.match(row["epoch"])):
        raise ValueError(f"{row['id']}: epoch must be an epoch id (^[a-z0-9-]{{1,40}}$)")
    return row


def reverify(row, epoch):
    """True when `epoch` (the newest started XP epoch) post-dates the row's
    `verified` date and the row is not tagged with that epoch: its numbers
    were transcribed before the patch. No row is ever deleted for it."""
    if epoch is None or row.get("epoch") == epoch["id"]:
        return False
    return row["verified"] < _parse_iso(epoch["starts_utc"]).date().isoformat()


def validate_table(rows):
    if not isinstance(rows, list):
        raise ValueError("table must be a list of rows")
    seen = set()
    for r in rows:
        validate_row(r)
        if r["id"] in seen:
            raise ValueError(f"duplicate id: {r['id']}")
        seen.add(r["id"])
    return rows


def load_table(path=DATA_FILE):
    """Read and validate the table; ValueError on any problem (bad file too)."""
    try:
        rows = json.loads(Path(path).read_text(encoding="ascii"))
    except (OSError, ValueError) as e:
        raise ValueError(f"grind spot table unreadable: {type(e).__name__}") from e
    return validate_table(rows)


# -- ranking -------------------------------------------------------------------

def rank(table, ap, dp, level, goal, top_n=TOP_N, unlock_n=UNLOCK_N):
    """{"top": [...], "unlocks": [...]} for one character.

    Eligible when ap >= ap_min, dp >= dp_min and level >= level_min. Score is
    the goal's tier (higher first); ties go to the smallest non-negative AP
    headroom (the spot closest to the character's AP), then id.

    Unlocks are ineligible spots, preferring those whose tier is at least the
    best eligible tier (a step up, not sideways), then the smallest AP + DP
    still missing, the fewest levels missing, the higher tier, then id.
    """
    if goal not in GOALS:
        raise ValueError(f"goal must be one of {'|'.join(GOALS)}")
    key = f"{goal}_tier"
    top, locked = [], []
    for r in table:
        out = dict(r, score=r[key])
        if ap >= r["ap_min"] and dp >= r["dp_min"] and level >= r["level_min"]:
            out["ap_headroom"] = ap - r["ap_min"]
            top.append(out)
        else:
            out["need_ap"] = max(0, r["ap_min"] - ap)
            out["need_dp"] = max(0, r["dp_min"] - dp)
            out["need_level"] = max(0, r["level_min"] - level)
            locked.append(out)
    top.sort(key=lambda r: (-r["score"], r["ap_headroom"], r["id"]))
    best = top[0]["score"] if top else 0
    locked.sort(key=lambda r: (r["score"] < best, r["need_ap"] + r["need_dp"],
                               r["need_level"], -r["score"], r["id"]))
    return {"top": top[:top_n], "unlocks": locked[:unlock_n]}


# -- service -------------------------------------------------------------------

def _query_int(q, name, lo, hi):
    vals = q.get(name)
    if vals is None:
        return None
    if len(vals) != 1 or not re.fullmatch(r"\d{1,4}", vals[0]) or not lo <= int(vals[0]) <= hi:
        raise ValueError(f"{name} must be an int {lo}..{hi}")
    return int(vals[0])


def _char_ap(gs):
    """Deadeye fights on whichever of AP / AAP is higher; tables quote one AP."""
    vals = [gs.get(k) for k in ("ap", "aap") if _ok_int(gs.get(k), *STAT_RANGE)]
    return max(vals) if vals else None


class SpotsService:
    """GET /api/spots. `character` returns plan 004's character {level, gs:
    {ap, aap, dp}}; `grind` returns plan 005's GET body (per-spot silver/h);
    `epoch` returns the newest started XP epoch or None (plan 018)."""

    def __init__(self, table, character, grind, error=None, epoch=None):
        self.table = table
        self.character = character
        self.grind = grind
        self.error = error
        self.epoch = epoch

    @classmethod
    def from_file(cls, character, grind, path=DATA_FILE, epoch=None):
        try:
            return cls(load_table(path), character, grind, epoch=epoch)
        except ValueError as e:  # a bad table degrades the card, never the server
            return cls([], character, grind, error=str(e), epoch=epoch)

    def _epoch(self):
        try:
            return self.epoch() if self.epoch is not None else None
        except Exception:  # noqa: BLE001 - the badge is an extra, never fatal
            return None

    def _logged(self):
        """Lower-cased spot name -> silver/h for spots with at least one session."""
        out = {}
        try:
            doc = self.grind()
        except Exception:  # noqa: BLE001 - logged silver/h is an extra, never fatal
            return out
        for s in doc.get("spots", []) if isinstance(doc, dict) else []:
            if (isinstance(s, dict) and isinstance(s.get("name"), str)
                    and _ok_int(s.get("sessions"), 1, 10 ** 9)
                    and _ok_int(s.get("silver_per_h"), 0, 10 ** 18)):
                out[s["name"].strip().lower()] = s["silver_per_h"]
        return out

    def view(self, q):
        """`q` is a parse_qs dict: goal=xp|silver (default xp), ap, dp, level."""
        goals = q.get("goal", ["xp"])
        if len(goals) != 1 or goals[0] not in GOALS:
            raise ValueError(f"goal must be one of {'|'.join(GOALS)}")
        goal = goals[0]
        over = {"ap": _query_int(q, "ap", *STAT_RANGE), "dp": _query_int(q, "dp", *STAT_RANGE),
                "level": _query_int(q, "level", *LEVEL_RANGE)}
        ch = self.character() or {}
        gs = ch.get("gs") if isinstance(ch.get("gs"), dict) else {}
        dp = gs.get("dp")
        lvl = ch.get("level")
        base = {"ap": _char_ap(gs), "dp": dp if _ok_int(dp, *STAT_RANGE) else None,
                "level": lvl if _ok_int(lvl, *LEVEL_RANGE) else None}
        inp, src = {}, {}
        for k in ("ap", "dp", "level"):
            if over[k] is not None:
                inp[k], src[k] = over[k], "query"
            else:
                inp[k], src[k] = base[k], "progress" if base[k] is not None else None
        missing = [k for k in ("ap", "dp", "level") if inp[k] is None]
        res = {"top": [], "unlocks": []}
        if not missing and self.table:
            res = rank(self.table, inp["ap"], inp["dp"], inp["level"], goal)
            logged = self._logged()
            epoch = self._epoch()
            for r in res["top"] + res["unlocks"]:
                r["logged_silver_per_h"] = logged.get(r["name"].lower())
                r["reverify"] = reverify(r, epoch)
                ml = r.get("monster_level")
                r["level_gap"] = inp["level"] - ml if ml is not None else None
                r["outlevel_dr"] = outlevel_dr(inp["level"], ml) if ml is not None else None
        return {"goal": goal, "input": inp, "from": src, "missing": missing,
                "top": res["top"], "unlocks": res["unlocks"], "count": len(self.table),
                "status": "error" if self.error else "ok", "error": self.error,
                "disclaimer": DISCLAIMER}
