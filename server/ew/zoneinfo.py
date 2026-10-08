"""Monster Zone Info OCR (plan 088): per-kill EXP and recommended level per zone.

Since the 2026-10-08 patch the in-game Monster Zone Info panel lists each
zone's monsters with the Combat EXP a kill gives and a recommended level before
the zone name. A screenshot of that panel the operator saved with the game's own
key is one more plan 063 auto-OCR kind, `zone_xp`: detected by the panel title
(hint list `titles.zone_info` in `data/ocr_regions.json`), parsed here into
{zone_name, recommended_level, monsters: [{name, xp_per_kill_pct}]}. Numbers go
through a digit guard (a dropped decimal point or comma, or digit groups inside
a percent, sink the confidence so the read waits in the review queue).

Derived numbers are pure: the per-kill cap of the operator's level band (plan
018 `kill_xp_cap`), the buffed per-kill EXP capped at it, `cap_bound` when the
unbuffed kill already reaches the cap, and kills to the next level. The table
lives in store domain `zone_xp`. Nothing reaches the game: input is saved image
files only, output is dashboard text.
"""

import datetime as _dt
import json
import math
import re
import threading
import time
from pathlib import Path

from . import ocr
from .levels import LEVEL_RANGE

REGIONS_FILE = Path(__file__).resolve().parent / "data" / "ocr_regions.json"
TITLES = ("Monster Zone Info",)
KIND = "zone_xp"
MAX_ZONES = 200
MAX_MONSTERS = 20
MAX_NAME = 60
FUZZY_MAX = 0.2          # normalised edit distance for a zone-name match

# Confidence of one read.
CONF_PCT_DOT = 0.95      # 0.0125% - the game's own format
CONF_PCT_COMMA = 0.85    # 0,0125% - a dot misread as a comma
CONF_PCT_WHOLE = 0.9     # 2% - a whole percent (low levels)
CONF_GUARD = 0.3         # digit guard tripped: dropped separator / digit groups
CONF_ZONE_LINE = 0.97
CONF_NO_MONSTERS = 0.6   # a zone line without monster rows: level only
CONF_NEIGHBOUR = 0.88    # the percent came from a cell beside the name

ZONE_RE = re.compile(
    r"^\W*(?:rec(?:ommended)?\.?\s*)?(?:lv|lvl|level)\s*\.?\s*(\d{1,3})\s*\]?\s*[-:|]?\s*"
    r"([A-Za-z][A-Za-z0-9' .&()-]{1,80}?)\s*$", re.IGNORECASE)
PCT_TOK = r"\d[\d.,]*"
MONSTER_RE = re.compile(
    rf"^\W*([A-Za-z][A-Za-z' .()-]{{0,{MAX_NAME}}}?)\s*[:|-]?\s*"
    rf"(?:(?:combat\s*)?(?:exp|xp)\s*:?\s*)?\+?\s*({PCT_TOK})\s*%\s*$", re.IGNORECASE)
LEVEL_ONLY = re.compile(r"^\W*(?:rec(?:ommended)?\.?\s*)?(?:lv|lvl|level)\s*\.?\s*(\d{1,3})\W*$",
                        re.IGNORECASE)
NAME_ONLY = re.compile(rf"^\W*([A-Za-z][A-Za-z' .()-]{{1,{MAX_NAME}}}?)\W*$")
PCT_ONLY = re.compile(rf"^\s*(?:(?:combat\s*)?(?:exp|xp)\s*:?\s*)?\+?\s*({PCT_TOK})\s*%\s*$",
                      re.IGNORECASE)
LABEL_TAIL = re.compile(r"\s*(?:combat\s*)?(?:exp|xp)\s*$", re.IGNORECASE)
CAP_NOTE = re.compile(r"~?\s*(\d+(?:\.\d+)?)\s*%")


def _iso(ts):
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).replace(microsecond=0).isoformat()


def _ok_int(v, lo, hi):
    return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


def _ok_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _ok_text(v, most=MAX_NAME):
    return isinstance(v, str) and bool(v.strip()) and len(v) <= most \
        and all(32 <= ord(ch) < 127 for ch in v)


def norm(name):
    """Case / punctuation-insensitive form of a zone or monster name."""
    return ocr._norm(name).strip() if isinstance(name, str) else ""


# -- titles ------------------------------------------------------------------------

def load_titles(path=None):
    """Title hints of the panel from the tracked region file (`titles.zone_info`),
    else the built-in TITLES."""
    try:
        doc = json.loads(Path(path or REGIONS_FILE).read_text(encoding="ascii"))
    except (OSError, ValueError):
        return TITLES
    rows = (doc.get("titles") or {}).get("zone_info") if isinstance(doc, dict) else None
    rows = [t for t in rows if _ok_text(t)] if isinstance(rows, list) else []
    return tuple(rows) or TITLES


def is_zone_info(doc, titles=TITLES):
    """True when one OCR line holds a panel title (case-insensitive)."""
    hints = [norm(t) for t in titles if norm(t)]
    for ln in ocr._lines(doc.get("lines") if isinstance(doc, dict) else None):
        t = " " + norm(ln["text"]) + " "
        if any(" " + h + " " in t for h in hints):
            return True
    return False


# -- digit guard -------------------------------------------------------------------

def pct_guard(tok):
    """(value, conf, why) of one percent token, or None when unreadable. The
    plan 016 digit-group rule applied to a percent: one decimal separator is
    the game's format; a leading zero without one (a dropped point or comma),
    over 100, or digit groups inside a percent trip the guard (CONF_GUARD)."""
    if not isinstance(tok, str) or not re.fullmatch(PCT_TOK, tok):
        return None
    seps = [ch for ch in tok if ch in ",."]
    if not seps:
        v = int(tok)
        if len(tok) > 1 and tok[0] == "0":
            return v, CONF_GUARD, "leading zero: decimal point or comma dropped"
        if v > 100:
            return v, CONF_GUARD, "over 100%: decimal point dropped"
        return v, CONF_PCT_WHOLE, "whole percent"
    if len(seps) > 1 or tok[-1] in ",.":
        try:
            v = float(re.sub(r"[,.](?=.*[,.])", "", tok).replace(",", ".").rstrip("."))
        except ValueError:
            return None
        return round(v, 6), CONF_GUARD, "digit groups in a percent"
    head, frac = re.split(r"[,.]", tok)
    v = round(int(head) + int(frac) / 10 ** len(frac), 6)
    if v > 100:
        return v, CONF_GUARD, "over 100%"
    if seps[0] == ",":
        return v, CONF_PCT_COMMA, "decimal comma"
    return v, CONF_PCT_DOT, "decimals"


# -- parser ------------------------------------------------------------------------

def _clean_name(s):
    s = LABEL_TAIL.sub("", s or "").strip(" .:-|")
    return re.sub(r"\s+", " ", s)[:MAX_NAME]


def parse(doc, titles=TITLES):
    """[{kind: zone_xp, name: zone_name, value: {zone_name, recommended_level,
    monsters: [{name, xp_per_kill_pct}]}, conf, why}] for every zone on a
    Monster Zone Info shot, in line order; [] when the panel title is absent.
    A monster row ("Naga Fighter 0.0125%", or the name with the percent in a
    cell beside it on the same row) belongs to the zone line above it."""
    if not is_zone_info(doc, titles):
        return []
    lines = sorted(ocr._lines(doc.get("lines") if isinstance(doc, dict) else None),
                   key=lambda ln: (ln["y"], ln["x"]))  # stable: no geometry keeps OCR order
    hints = [norm(t) for t in titles]
    zones, cur, used = [], None, set()
    for i, ln in enumerate(lines):
        if i in used:
            continue
        text = ln["text"]
        if norm(text) in hints:
            continue
        z = ZONE_RE.match(text) if "%" not in text else None
        lv = LEVEL_ONLY.match(text)
        if z is None and lv is not None:  # "Lv. 56" and the name in two cells on one row
            for j, b in enumerate(lines):
                if j != i and j not in used and ocr._same_row(ln, b) and b["x"] > ln["x"] \
                        and NAME_ONLY.match(b["text"]):
                    used.add(j)
                    z = ZONE_RE.match(f"{text} {b['text']}")
                    break
        if z is not None:
            level = int(z.group(1))
            name = _clean_name(z.group(2))
            if LEVEL_RANGE[0] <= level <= LEVEL_RANGE[1] and name:
                cur = {"zone_name": name, "recommended_level": level, "monsters": [],
                       "confs": [CONF_ZONE_LINE], "whys": ["recommended level before the name"]}
                zones.append(cur)
            continue
        if cur is None or PCT_ONLY.match(text):
            continue  # a bare value cell ("Combat EXP 0.0450%") is claimed by its name
        hit = None
        m = MONSTER_RE.match(text)
        if m is not None:
            hit = (_clean_name(m.group(1)), m.group(2), None)
        else:
            n = NAME_ONLY.match(text)
            if n is not None and not ZONE_RE.match(text):
                for j, b in ((j, b) for j, b in enumerate(lines) if j != i and j not in used):
                    if not (ocr._same_row(ln, b) and b["x"] > ln["x"]):
                        continue
                    p = PCT_ONLY.match(b["text"])
                    if p is not None:
                        used.add(j)
                        hit = (_clean_name(n.group(1)), p.group(1), "cell beside the name")
                        break
        if hit is None or not hit[0] or len(cur["monsters"]) >= MAX_MONSTERS:
            continue
        name, tok, where = hit
        g = pct_guard(tok)
        if g is None:
            continue
        v, conf, why = g
        if where is not None:
            conf, why = min(conf, CONF_NEIGHBOUR), f"{why} ({where})"
        cur["monsters"].append({"name": name, "xp_per_kill_pct": v})
        cur["confs"].append(conf)
        if conf < CONF_PCT_DOT:
            cur["whys"].append(f"{name}: {why}")
    out = []
    for z in zones:
        confs, whys = z.pop("confs"), z.pop("whys")
        if not z["monsters"]:
            confs.append(CONF_NO_MONSTERS)
            whys.append("no monster rows")
        else:
            whys.append(f"{len(z['monsters'])} monsters")
        out.append({"kind": KIND, "name": z["zone_name"], "value": z,
                    "conf": round(min(confs), 3), "why": "; ".join(whys)})
    return out


def check_value(v):
    """Normalised copy of a zone read value, or ValueError."""
    if not (isinstance(v, dict) and set(v) == {"zone_name", "recommended_level", "monsters"}):
        raise ValueError("zone_xp value must be {zone_name, recommended_level, monsters}")
    if not _ok_text(v["zone_name"], 100):
        raise ValueError("zone_name must be 1..100 printable ASCII")
    if not _ok_int(v["recommended_level"], *LEVEL_RANGE):
        raise ValueError(f"recommended_level must be an int {LEVEL_RANGE[0]}..{LEVEL_RANGE[1]}")
    ms = v["monsters"]
    if not isinstance(ms, list) or len(ms) > MAX_MONSTERS:
        raise ValueError(f"monsters must be a list of at most {MAX_MONSTERS}")
    out = []
    for m in ms:
        if not (isinstance(m, dict) and set(m) == {"name", "xp_per_kill_pct"}
                and _ok_text(m["name"]) and _ok_num(m["xp_per_kill_pct"])
                and 0 < m["xp_per_kill_pct"] <= 100):
            raise ValueError("each monster must be {name, xp_per_kill_pct 0 < pct <= 100}")
        out.append({"name": m["name"].strip(), "xp_per_kill_pct": m["xp_per_kill_pct"]})
    return {"zone_name": v["zone_name"].strip(), "recommended_level": v["recommended_level"],
            "monsters": out}


# -- zone ids ----------------------------------------------------------------------

def match_zone(name, table):
    """(spot id, how) of the grind spot table row named `name`: exact, then the
    normalised name, then the closest normalised name within FUZZY_MAX; else
    (None, None)."""
    if not isinstance(name, str):
        return None, None
    rows = [r for r in table if isinstance(r, dict) and isinstance(r.get("name"), str)]
    for r in rows:
        if r["name"].strip().lower() == name.strip().lower():
            return r["id"], "exact"
    n = norm(name)
    for r in rows:
        if norm(r["name"]) == n:
            return r["id"], "normalised"
    best = None
    for r in rows:
        d = ocr.name_distance(norm(r["name"]), n)
        if d <= FUZZY_MAX and (best is None or d < best[1]):
            best = (r["id"], d)
    return (best[0], "fuzzy") if best else (None, None)


def zone_key(name, table):
    zid, _ = match_zone(name, table)
    return (zid, False) if zid is not None else ("name:" + norm(name), True)


# -- derived numbers ---------------------------------------------------------------

def band_cap(band):
    """Per-kill cap (% of a level) of one kill_xp_cap band: a numeric
    `cap_pct` when present, else the percent in its note ("~2% of the level")."""
    if not isinstance(band, dict):
        return None
    v = band.get("cap_pct")
    if _ok_num(v) and v > 0:
        return v
    m = CAP_NOTE.search(band.get("note") or "")
    return float(m.group(1)) if m and float(m.group(1)) > 0 else None


def band_of(caps, level):
    """The narrowest kill_xp_cap band holding `level` (a later, more specific
    band beats a wide old one), skipping bands marked `superseded_by`."""
    if not _ok_int(level, *LEVEL_RANGE):
        return None
    rows = [c for c in caps or [] if isinstance(c, dict) and not c.get("superseded_by")
            and _ok_int(c.get("level_min"), *LEVEL_RANGE)
            and _ok_int(c.get("level_max"), *LEVEL_RANGE)
            and c["level_min"] <= level <= c["level_max"] and band_cap(c) is not None]
    if not rows:
        return None
    return min(rows, key=lambda c: (c["level_max"] - c["level_min"], -c["level_min"]))


def cap_pct(caps, level):
    return band_cap(band_of(caps, level))


def same_band(caps, a, b):
    """EXP per kill read at level `a` still holds at level `b`: same cap band,
    or the same level when no band covers them."""
    if a == b:
        return True
    ba, bb = band_of(caps, a), band_of(caps, b)
    return ba is not None and bb is not None \
        and (ba["level_min"], ba["level_max"]) == (bb["level_min"], bb["level_max"])


def _ceil(x):
    return math.ceil(round(x, 9))


def kill_math(xp_per_kill_pct, stack_pct, cap):
    """{base, effective, cap_bound}: effective = min(base x (1 + stack / 100),
    cap); cap_bound when the unbuffed kill already reaches the cap."""
    base = xp_per_kill_pct
    eff = base * (1 + max(0, stack_pct or 0) / 100)
    if cap is not None:
        eff = min(eff, cap)
    return {"base": base, "effective": round(eff, 6),
            "cap_bound": cap is not None and base >= cap}


def zone_numbers(row, level, xp_pct, stack_pct, caps):
    """Derived numbers of one stored zone row for the operator's level.
    Returns {current, reread_level, cap_pct, monsters: [...], effective_pct,
    cap_bound, kills_to_level, next_level}. The zone figure averages the listed
    monsters' effective EXP; the zone is cap_bound when every monster is."""
    cap = cap_pct(caps, level)
    at = row.get("level_at_read")
    current = _ok_int(level, *LEVEL_RANGE) and _ok_int(at, *LEVEL_RANGE) \
        and same_band(caps, at, level)
    out = {"current": bool(current), "reread_level": None if current else level,
           "cap_pct": cap, "monsters": [], "effective_pct": None, "cap_bound": False,
           "kills_to_level": None,
           "next_level": level + 1 if _ok_int(level, LEVEL_RANGE[0], LEVEL_RANGE[1] - 1)
           else None}
    if not current:
        return out
    ms = [dict(m, **kill_math(m["xp_per_kill_pct"], stack_pct, cap))
          for m in row.get("monsters") or []]
    out["monsters"] = ms
    if not ms:
        return out
    eff = sum(m["effective"] for m in ms) / len(ms)
    out["effective_pct"] = round(eff, 6)
    out["cap_bound"] = all(m["cap_bound"] for m in ms)
    if _ok_num(xp_pct) and eff > 0 and out["next_level"] is not None:
        out["kills_to_level"] = _ceil(max(0.0, 100 - xp_pct) / eff)
    return out


# -- service -----------------------------------------------------------------------

class ZoneXpService:
    """Store domain `zone_xp` {zones: {key: {zone_id, zone_name, unmatched,
    recommended_level, monsters, read_at, level_at_read, screenshot}}}; key is
    the grind spot id, else "name:<normalised name>". `state` returns
    {level, pct, xp_stack_pct, caps} (plan 011 / 018); `active_spot` returns
    the running grind session's spot name or None."""

    def __init__(self, store, table, state=None, active_spot=None, clock=time.time):
        self.store = store
        self.table = table or []
        self.state = state or (lambda: {})
        self.active_spot = active_spot
        self.clock = clock
        self._lock = threading.RLock()

    def _zones(self):
        doc = self.store.get(KIND)
        zones = doc.get("zones") if isinstance(doc.get("zones"), dict) else {}
        return {k: v for k, v in zones.items() if isinstance(k, str) and isinstance(v, dict)}

    def commit(self, value, file, shot_at, level):
        """Store one read; returns (key, prev row or None). A row read from a
        newer shot stays (ValueError)."""
        value = check_value(value)
        key, unmatched = zone_key(value["zone_name"], self.table)
        with self._lock:
            zones = self._zones()
            prev = zones.get(key)
            if prev is not None and isinstance(prev.get("read_at"), str) \
                    and prev["read_at"] > _iso(shot_at):
                raise ValueError(f"a newer read of {value['zone_name']} is stored")
            zones[key] = {"zone_id": None if unmatched else key,
                          "zone_name": value["zone_name"], "unmatched": unmatched,
                          "recommended_level": value["recommended_level"],
                          "monsters": value["monsters"], "read_at": _iso(shot_at),
                          "level_at_read": level if _ok_int(level, *LEVEL_RANGE) else None,
                          "screenshot": file if isinstance(file, str) else ""}
            if len(zones) > MAX_ZONES:  # oldest reads go; the one just written stays
                rest = sorted(((k, z) for k, z in zones.items() if k != key),
                              key=lambda kv: str(kv[1].get("read_at")))
                zones = dict(rest[-(MAX_ZONES - 1):] + [(key, zones[key])])
            self.store.put(KIND, {"zones": zones})
        return key, prev

    def restore(self, key, prev, read_at, file):
        """Undo one commit while its row is unchanged (same shot and read time)."""
        with self._lock:
            zones = self._zones()
            cur = zones.get(key)
            if cur is None or cur.get("read_at") != read_at or cur.get("screenshot") != file:
                raise ValueError("that zone was read again since; nothing undone")
            if isinstance(prev, dict):
                zones[key] = prev
            else:
                zones.pop(key, None)
            self.store.put(KIND, {"zones": zones})

    def _state(self):
        try:
            s = self.state() or {}
        except Exception:  # noqa: BLE001 - no level = numbers stay blank
            s = {}
        return (s.get("level") if _ok_int(s.get("level"), *LEVEL_RANGE) else None,
                s.get("pct") if _ok_num(s.get("pct")) else None,
                s.get("xp_stack_pct") if _ok_num(s.get("xp_stack_pct")) else 0,
                s.get("caps") if isinstance(s.get("caps"), list) else [])

    def _row(self, key, z, st):
        level, pct, stack, caps = st
        date = (z.get("read_at") or "")[:10]
        return dict(z, key=key, source=f"in-game zone info {date}".strip(),
                    **zone_numbers(z, level, pct, stack, caps))

    def view(self):
        """GET /api/zones/xp body."""
        st = self._state()
        rows = [self._row(k, z, st) for k, z in self._zones().items()]
        rows.sort(key=lambda r: (r.get("recommended_level") or 0, r["zone_name"]))
        return {"zones": rows, "level": st[0], "xp_pct": st[1], "buff_stack_pct": st[2],
                "cap_pct": cap_pct(st[3], st[0])}

    def by_spot(self):
        """{grind spot id: zone row} for matched zones (plan 012 recommender)."""
        st = self._state()
        return {k: self._row(k, z, st) for k, z in self._zones().items()
                if not z.get("unmatched")}

    def for_name(self, name):
        if not isinstance(name, str) or not name.strip():
            return None
        key, _ = zone_key(name, self.table)
        z = self._zones().get(key)
        return self._row(key, z, self._state()) if z is not None else None

    def here(self):
        """{zone_name, cap_bound, source} of the running grind session's spot
        when its zone has a current read, else None."""
        if self.active_spot is None:
            return None
        try:
            name = self.active_spot()
        except Exception:  # noqa: BLE001 - no session read = no flag
            return None
        r = self.for_name(name)
        if r is None or not r["current"]:
            return None
        return {"zone_name": r["zone_name"], "cap_bound": r["cap_bound"],
                "cap_pct": r["cap_pct"], "source": r["source"]}

    def cap_bound_here(self):
        h = self.here()
        return bool(h and h["cap_bound"])
