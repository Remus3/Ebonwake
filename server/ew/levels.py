"""Level cap and XP patch data (plan 018): one level range, patch epochs, buff presets.

`LEVEL_RANGE` is the one place the max level lives; progress, leveling and
spots import it (a test fails on any other assignment). The XP epochs
(`data/xp_epochs.json`) mark patches that rescale combat XP: rates are only
measured from samples on or after the newest epoch that has started. The XP
buff presets (`data/xp_buffs.json`) are community / patch-note values, each
row carrying its source. Both files are tracked data, operator-verified;
nothing is read from the game.
"""

import json
import re
from pathlib import Path

from .today import _iso, _parse_iso

LEVEL_MAX = 75
LEVEL_RANGE = (1, LEVEL_MAX)
MONSTER_LEVEL_RANGE = (1, 99)
OUTLEVEL_DR_PER_LEVEL = 3  # patch 2026-10: +3 monster DR per level out-levelled
OUTLEVEL_DR_MAX = 9
XP_PCT_RANGE = (0, 1000)  # matches grind / leveling XP_PCT_RANGE
DATA_DIR = Path(__file__).resolve().parent / "data"
EPOCHS_FILE = DATA_DIR / "xp_epochs.json"
BUFFS_FILE = DATA_DIR / "xp_buffs.json"
EPOCH_FIELDS = ("id", "starts_utc", "label", "source", "verified")
CAP_FIELDS = ("level_min", "level_max", "note")
PRESET_FIELDS = ("id", "name", "xp_pct", "pre_patch_xp_pct", "notes", "source", "verified")
ID_RE = re.compile(r"^[a-z0-9-]{1,40}$")
DATE_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")
MAX_LABEL = 40
MAX_TEXT = 200
MAX_CAPS = 20
PRE_PATCH_HINT = "pre-patch value?"


def _ok_int(v, lo, hi):
    return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


def _ok_text(v, most, allow_empty=False):
    return (isinstance(v, str) and (allow_empty or bool(v.strip())) and len(v) <= most
            and all(32 <= ord(ch) < 127 for ch in v))


def _read(path, what):
    try:
        return json.loads(Path(path).read_text(encoding="ascii"))
    except (OSError, ValueError) as e:
        raise ValueError(f"{what} unreadable: {type(e).__name__}") from e


# -- epochs --------------------------------------------------------------------

def validate_epoch(row, tracked=False):
    """Normalised copy of one epoch row, or ValueError. A tracked row may also
    carry `kill_xp_cap` [{level_min, level_max, note}]; an operator row may not."""
    extra = {"kill_xp_cap"} if tracked else set()
    if not isinstance(row, dict) or not set(EPOCH_FIELDS) <= set(row) <= set(EPOCH_FIELDS) | extra:
        raise ValueError(f"epoch must be {{{', '.join(EPOCH_FIELDS)}}}")
    if not isinstance(row["id"], str) or not ID_RE.match(row["id"]):
        raise ValueError("epoch id must match ^[a-z0-9-]{1,40}$")
    when = _parse_iso(row["starts_utc"])
    if when is None:
        raise ValueError(f"{row['id']}: starts_utc must be an ISO time with a UTC offset")
    try:
        starts = _iso(when)
    except (OverflowError, ValueError, OSError):
        raise ValueError(f"{row['id']}: starts_utc out of range") from None
    if not _ok_text(row["label"], MAX_LABEL):
        raise ValueError(f"{row['id']}: label must be 1..{MAX_LABEL} printable ASCII")
    if not _ok_text(row["source"], MAX_TEXT):
        raise ValueError(f"{row['id']}: source must be 1..{MAX_TEXT} printable ASCII")
    if not isinstance(row["verified"], bool):
        raise ValueError(f"{row['id']}: verified must be true or false")
    out = {"id": row["id"], "starts_utc": starts, "label": row["label"].strip(),
           "source": row["source"].strip(), "verified": row["verified"]}
    if "kill_xp_cap" in row:
        caps = row["kill_xp_cap"]
        if not isinstance(caps, list) or len(caps) > MAX_CAPS:
            raise ValueError(f"{row['id']}: kill_xp_cap must be a list of at most {MAX_CAPS}")
        for c in caps:
            if (not isinstance(c, dict) or set(c) != set(CAP_FIELDS)
                    or not _ok_int(c["level_min"], *LEVEL_RANGE)
                    or not _ok_int(c["level_max"], *LEVEL_RANGE)
                    or c["level_min"] > c["level_max"] or not _ok_text(c["note"], MAX_TEXT)):
                raise ValueError(f"{row['id']}: kill_xp_cap rows must be "
                                 f"{{{', '.join(CAP_FIELDS)}}} with a level band in range")
        out["kill_xp_cap"] = [dict(c) for c in caps]
    return out


def load_epochs(path=EPOCHS_FILE):
    rows = _read(path, "xp epoch table")
    if not isinstance(rows, list):
        raise ValueError("xp epoch table must be a list")
    out, seen = [], set()
    for r in rows:
        c = validate_epoch(r, tracked=True)
        if c["id"] in seen:
            raise ValueError(f"duplicate epoch id: {c['id']}")
        seen.add(c["id"])
        out.append(c)
    return sort_epochs(out)


def sort_epochs(rows):
    return sorted(rows, key=lambda r: (_parse_iso(r["starts_utc"]), r["id"]))


def merge_epochs(tracked, added, deleted):
    """Effective epochs: tracked rows minus deleted ids, with an operator row of
    the same id replacing a tracked one (its kill_xp_cap kept), plus new ones."""
    by_id = {r["id"]: dict(r) for r in tracked if r["id"] not in deleted}
    base = {r["id"]: r for r in tracked}
    for a in added:
        row = dict(a)
        if "kill_xp_cap" in base.get(a["id"], {}):
            row["kill_xp_cap"] = base[a["id"]]["kill_xp_cap"]
        by_id[a["id"]] = row
    return sort_epochs(by_id.values())


def active_epoch(epochs, now):
    """The newest epoch whose start is <= now, or None."""
    live = [e for e in epochs if _parse_iso(e["starts_utc"]) <= now]
    return live[-1] if live else None


def next_epoch(epochs, now):
    ahead = [e for e in epochs if _parse_iso(e["starts_utc"]) > now]
    return ahead[0] if ahead else None


def kill_cap_note(epoch, level):
    """The per-kill XP cap note for `level` in `epoch`, or None (info only)."""
    if epoch is None or not _ok_int(level, *LEVEL_RANGE):
        return None
    for c in epoch.get("kill_xp_cap", []):
        if c["level_min"] <= level <= c["level_max"]:
            return c["note"]
    return None


def outlevel_dr(level, monster_level):
    """Monster DR bonus from out-levelling a monster (0 at or below its level)."""
    if not _ok_int(level, *LEVEL_RANGE) or not _ok_int(monster_level, *MONSTER_LEVEL_RANGE):
        return None
    return min(OUTLEVEL_DR_MAX, max(0, level - monster_level) * OUTLEVEL_DR_PER_LEVEL)


# -- XP buff presets -------------------------------------------------------------

def validate_preset(row):
    if not isinstance(row, dict) or set(row) != set(PRESET_FIELDS):
        raise ValueError(f"preset must have exactly {', '.join(PRESET_FIELDS)}")
    if not isinstance(row["id"], str) or not ID_RE.match(row["id"]):
        raise ValueError("preset id must match ^[a-z0-9-]{1,40}$")
    for k in ("name", "source"):
        if not _ok_text(row[k], MAX_TEXT if k == "source" else 60):
            raise ValueError(f"{row['id']}: {k} must be printable ASCII")
    if not _ok_text(row["notes"], MAX_TEXT, allow_empty=True):
        raise ValueError(f"{row['id']}: notes must be printable ASCII")
    for k in ("xp_pct", "pre_patch_xp_pct"):
        if not _ok_int(row[k], *XP_PCT_RANGE):
            raise ValueError(f"{row['id']}: {k} must be an int "
                             f"{XP_PCT_RANGE[0]}..{XP_PCT_RANGE[1]}")
    if row["xp_pct"] == row["pre_patch_xp_pct"]:
        raise ValueError(f"{row['id']}: xp_pct must differ from pre_patch_xp_pct")
    if not isinstance(row["verified"], str) or not DATE_RE.match(row["verified"]):
        raise ValueError(f"{row['id']}: verified must be a YYYY-MM-DD date")
    return row


def load_buff_presets(path=BUFFS_FILE):
    rows = _read(path, "xp buff presets")
    if not isinstance(rows, list):
        raise ValueError("xp buff presets must be a list")
    seen = set()
    for r in rows:
        validate_preset(r)
        key = r["name"].strip().lower()
        if r["id"] in seen or key in seen:
            raise ValueError(f"duplicate preset: {r['id']}")
        seen.update((r["id"], key))
    return rows


def buff_hint(name, xp_pct, presets):
    """PRE_PATCH_HINT when a buff named like a preset still carries the
    pre-patch xp_pct; None otherwise. Stored buffs are never rewritten."""
    if not isinstance(name, str) or not _ok_int(xp_pct, *XP_PCT_RANGE):
        return None
    n = name.strip().lower()
    for p in presets:
        if p["name"].strip().lower() == n and xp_pct == p["pre_patch_xp_pct"]:
            return PRE_PATCH_HINT
    return None
