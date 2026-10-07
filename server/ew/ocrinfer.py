"""Progress inference from screenshot OCR (plan 066): level + XP %, AP / AAP /
DP, sanity over the sample history, silver/h and the book-use suggestion.

Pure functions over the plan 009 {text, lines} doc of a screenshot the operator
saved (the plan 063 auto-OCR worker calls them); every field is the plan 063
{kind, name, value, conf 0..1, why} contract. Region hints are relative crops
in the tracked `data/ocr_regions.json` (per UI scale): a read inside its region
wins a tie between two reads of the same field. Nothing is cropped, nothing
reaches the game.
"""

import datetime as _dt
import json
import re
import struct
from pathlib import Path

from . import ocr
from .leveling import _level_now, _norm_pct
from .levels import LEVEL_RANGE
from .progress import GS_KEYS, GS_RANGE

REGIONS_FILE = Path(__file__).resolve().parent / "data" / "ocr_regions.json"
UI_SCALE = 100

# Level / XP % confidence. BDO prints the XP percent with 3 decimals ("12.500%").
CONF_PCT_3DP = 0.97      # dot + 3 decimals: the game's own format
CONF_PCT_SHORT = 0.88    # dot + 1-2 decimals: a decimal dropped
CONF_PCT_COMMA = 0.85    # decimal comma: a dot misread
CONF_PCT_WHOLE = 0.8     # no decimals at all
CONF_NEIGHBOUR = 0.85    # the percent came from a line beside / below the level
# Gear stat confidence.
CONF_GEAR = 0.95         # label and number on one line
CONF_GEAR_ROW = 0.85     # the number is a separate word on the same row

LEVEL_RE = re.compile(r"(?<![A-Za-z])(?:lv|lvl|level)\s*\.?\s*(\d{1,3})(?!\d)", re.IGNORECASE)
PCT_RE = re.compile(r"(?<![\d.,])(\d{1,3})(?:([.,])(\d{1,3}))?(?!\d)\s*%")
GEAR_RE = re.compile(r"(?<![A-Za-z])(awakening\s*ap|awk\.?\s*ap|aap|ap|dp)\s*:?\s*(\d{1,3})(?!\d)",
                     re.IGNORECASE)
PURE_NUM = re.compile(r"^\s*(\d{1,3})\s*$")

# Book use (plan 060): a same-level XP jump between two shots at most
# BOOK_WINDOW_S apart, after the expected grind gain, within BOOK_TOL of one
# book of a size the operator added in the last BOOK_RECENT_S and still owns.
BOOK_WINDOW_S = 3600
BOOK_RECENT_S = 86400
BOOK_TOL = 0.35
SILVER_MIN_SPAN_S = 60


# -- regions -----------------------------------------------------------------------

def load_regions(path=None):
    """The tracked region file; {"scales": []} when unreadable (no tie-break)."""
    try:
        doc = json.loads(Path(path or REGIONS_FILE).read_text(encoding="ascii"))
    except (OSError, ValueError):
        return {"scales": []}
    rows = doc.get("scales") if isinstance(doc, dict) else None
    out = []
    for r in rows if isinstance(rows, list) else []:
        regs = r.get("regions") if isinstance(r, dict) else None
        if not isinstance(regs, dict):
            continue
        boxes = {k: v for k, v in regs.items() if isinstance(k, str) and _ok_box(v)}
        out.append({"ui_scale": r.get("ui_scale"), "verified": r.get("verified") is True,
                    "regions": boxes})
    return {"scales": out}


def _ok_box(v):
    return (isinstance(v, list) and len(v) == 4
            and all(isinstance(x, (int, float)) and not isinstance(x, bool) and 0 <= x <= 1
                    for x in v) and v[0] < v[2] and v[1] < v[3])


def _box(regions, key, scale=UI_SCALE):
    rows = (regions or {}).get("scales") or []
    row = next((r for r in rows if r.get("ui_scale") == scale), rows[0] if rows else None)
    return (row or {}).get("regions", {}).get(key)


def _inside(ln, size, box):
    if size is None or box is None or not size[0] or not size[1]:
        return False
    cx = (ln["x"] + ln["w"] / 2) / size[0]
    cy = (ln["y"] + ln["h"] / 2) / size[1]
    return box[0] <= cx <= box[2] and box[1] <= cy <= box[3]


def image_size(path):
    """(width, height) from a PNG / JPEG / BMP header, else None. Reads the
    operator's saved screenshot file only (the same file the OCR reads)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(32)
            if head.startswith(b"\x89PNG\r\n\x1a\n") and head[12:16] == b"IHDR":
                return struct.unpack(">II", head[16:24])
            if head.startswith(b"BM") and len(head) >= 26:
                w, h = struct.unpack("<ii", head[18:26])
                return (abs(w), abs(h)) if w and h else None
            if head.startswith(b"\xff\xd8"):
                return _jpeg_size(fh)
    except (OSError, struct.error):
        return None
    return None


def _jpeg_size(fh):
    fh.seek(2)
    for _ in range(1000):
        b = fh.read(1)
        while b and b != b"\xff":
            b = fh.read(1)
        while b == b"\xff":
            b = fh.read(1)
        if not b:
            return None
        marker = b[0]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            continue
        if marker == 0xD9:
            return None
        seg = fh.read(2)
        if len(seg) < 2:
            return None
        n = struct.unpack(">H", seg)[0]
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            d = fh.read(5)
            if len(d) < 5:
                return None
            h, w = struct.unpack(">HH", d[1:5])
            return (w, h) if w and h else None
        fh.seek(n - 2, 1)
    return None


# -- extractors --------------------------------------------------------------------

def _pct_conf(m):
    """(pct, conf, why) from a PCT_RE match, or None when over 100."""
    whole, sep, frac = m.group(1), m.group(2), m.group(3)
    v = int(whole) + (int(frac) / 10 ** len(frac) if frac else 0)
    if v > 100:
        return None
    if sep is None:
        return int(whole), CONF_PCT_WHOLE, "XP % without decimals"
    v = _norm_pct(round(v, 3))
    if sep == ",":
        return v, CONF_PCT_COMMA, "decimal comma"
    if len(frac) == 3:
        return v, CONF_PCT_3DP, "XP % with 3 decimals"
    return v, CONF_PCT_SHORT, "XP % with a decimal dropped"


def _best(cands):
    """cands: [(inside, conf, order, field)] -> field: in-region, then conf, then first."""
    if not cands:
        return None
    return max(cands, key=lambda c: (c[0], c[1], -c[2]))[3]


def level_field(doc, size=None, regions=None):
    """{kind: level, name: level, value: {level, pct}, conf, why} or None. The
    level ("Lv 61", "Lv.61", "Level 61") takes the XP percent after it on its
    line, else on a neighbour line (beside / just below)."""
    lines = ocr._lines(doc.get("lines") if isinstance(doc, dict) else None)
    box = _box(regions, "level")
    cands = []
    for i, ln in enumerate(lines):
        for m in LEVEL_RE.finditer(ln["text"]):
            level = int(m.group(1))
            if not LEVEL_RANGE[0] <= level <= LEVEL_RANGE[1]:
                continue
            hit, neighbour = None, False
            pm = PCT_RE.search(ln["text"], m.end())
            if pm is not None:
                hit = _pct_conf(pm)
            else:
                for b in ocr._neighbours(lines, i):
                    if LEVEL_RE.search(b["text"]):
                        continue
                    pm = PCT_RE.search(b["text"])
                    if pm is not None:
                        hit, neighbour = _pct_conf(pm), True
                        break
            if hit is None:
                continue
            pct, conf, why = hit
            if neighbour:
                conf, why = min(conf, CONF_NEIGHBOUR), why + " (neighbour line)"
            inside = _inside(ln, size, box)
            if inside:
                why += "; in the level region"
            cands.append((inside, conf, len(cands),
                          {"kind": "level", "name": "level",
                           "value": {"level": level, "pct": pct},
                           "conf": round(conf, 3), "why": why}))
    return _best(cands)


def gear_fields(doc, size=None, regions=None):
    """[{kind: gear, name: ap|aap|dp, value, conf, why}] from the character
    window ("AP 296", "Awakening AP 300", "AAP: 300", "DP 380"); one per stat."""
    lines = ocr._lines(doc.get("lines") if isinstance(doc, dict) else None)
    box = _box(regions, "gear")
    cands = {k: [] for k in GS_KEYS}
    for i, ln in enumerate(lines):
        labels = list(GEAR_RE.finditer(ln["text"]))
        for m in labels:
            label = m.group(1).lower()
            stat = "aap" if label.startswith(("awakening", "awk", "aap")) else label
            value, conf, why = int(m.group(2)), CONF_GEAR, "label and number on one line"
            if GS_RANGE[0] <= value <= GS_RANGE[1]:
                cands[stat].append((_inside(ln, size, box), conf, len(cands[stat]),
                                    {"kind": "gear", "name": stat, "value": value,
                                     "conf": conf, "why": why}))
        bare = re.search(r"(?<![A-Za-z])(awakening\s*ap|aap|ap|dp)\s*:?\s*$", ln["text"],
                         re.IGNORECASE)
        if bare is not None and len(labels) == 0:
            label = bare.group(1).lower()
            stat = "aap" if label.startswith(("awakening", "aap")) else label
            row = [b for b in ocr._neighbours(lines, i) if ocr._same_row(ln, b)
                   and b["x"] > ln["x"]]
            for b in row:
                pm = PURE_NUM.match(b["text"])
                if pm and GS_RANGE[0] <= int(pm.group(1)) <= GS_RANGE[1]:
                    cands[stat].append((_inside(ln, size, box), CONF_GEAR_ROW, len(cands[stat]),
                                        {"kind": "gear", "name": stat,
                                         "value": int(pm.group(1)), "conf": CONF_GEAR_ROW,
                                         "why": "number beside the label"}))
                    break
    return [f for f in (_best(cands[k]) for k in GS_KEYS) if f is not None]


# -- planner reads (plan 081): weight, slots, CP, fame -----------------------------

def _num_reads(tok):
    """[(value, conf, why)] readings of one OCR number token ("1,560",
    "812.350", "1.234,5"); a lone 3-digit group after one separator reads
    either as thousands or as 3 decimals, the game's own format first."""
    seps = [ch for ch in tok if ch in ",."]
    digits = re.split(r"[,.]", tok)
    if not seps:
        return [(int(tok), 0.95, "whole number")]
    if len(set(seps)) == 2:
        last = seps[-1]
        head, frac = tok.rsplit(last, 1)
        groups = re.split(r"[,.]", head)
        if seps.count(last) != 1 or any(len(g) != 3 for g in groups[1:]) or len(frac) > 3:
            return []
        v = int("".join(groups)) + int(frac) / 10 ** len(frac)
        return [(v, 0.97 if last == "." else 0.85,
                 "grouped with decimals" if last == "." else "decimal comma")]
    sep = seps[0]
    if len(seps) > 1:
        if any(len(g) != 3 for g in digits[1:]):
            return []
        return [(int("".join(digits)), 0.95 if sep == "," else 0.9, "grouped")]
    head, frac = digits
    dec = int(head) + int(frac) / 10 ** len(frac) if len(frac) <= 3 else None
    out = []
    if len(frac) == 3:
        grouped = int(head + frac)
        if sep == ",":
            out = [(grouped, 0.95, "comma grouped"), (dec, 0.8, "decimal comma")]
        else:
            out = [(dec, 0.95, "3 decimals"), (grouped, 0.8, "dot grouped")]
    elif dec is not None:
        out = [(dec, 0.95 if sep == "." else 0.85,
                "decimals" if sep == "." else "decimal comma")]
    return out


NUM_TOK = r"\d[\d.,]*\d|\d"
WEIGHT_RE = re.compile(rf"(?<![\d.,])({NUM_TOK})\s*/\s*({NUM_TOK})\s*LT\b", re.IGNORECASE)
SLOTS_RE = re.compile(r"(?<![\d.,/])(\d{1,3})\s*/\s*(\d{1,3})(?![\d.,/%])")
SLOTS_LABEL = re.compile(r"slot|inventory", re.IGNORECASE)
CP_PAIR_RE = re.compile(r"(?<![A-Za-z])(?:contribution\s*points?|c\.?p\.?)\s*:?\s*(\d{1,5})\s*/\s*"
                        r"(\d{1,5})(?!\d)", re.IGNORECASE)
CP_ONE_RE = re.compile(r"(?<![A-Za-z])contribution\s*points?\s*:?\s*(\d{1,5})(?![\d/]|\s*/)",
                       re.IGNORECASE)
FAME_RE = re.compile(r"fame[^%\d]{0,40}?\+?\s*(\d{1,2})(?:([.,])(\d{1,3}))?\s*%", re.IGNORECASE)
MAX_LT_READ = 20000
SLOTS_RANGE = (8, 400)
CP_MAX_READ = 10000
FAME_MAX_READ = 1.5
CONF_SLOTS_LABEL = 0.95
CONF_SLOTS_BESIDE = 0.92  # a bare a / b beside the weight line
CONF_SLOTS_REGION = 0.9
CONF_CP_PAIR = 0.95
CONF_CP_ONE = 0.85        # one number: maybe the points left, not the total
CONF_FAME_DOT = 0.95
CONF_FAME_WHOLE = 0.92
CONF_FAME_COMMA = 0.85


def _lines_of(doc):
    return ocr._lines(doc.get("lines") if isinstance(doc, dict) else None)


def weight_field(doc, size=None, regions=None):
    """{kind: weight, name: weight, value: {used, max} (LT), conf, why} from the
    inventory "x / y LT" line, or None. Each number is read every plausible
    way; the best reading with 1 <= max <= MAX_LT_READ and used <= 2 x max wins."""
    box = _box(regions, "weight")
    cands = []
    for ln in _lines_of(doc):
        for m in WEIGHT_RE.finditer(ln["text"]):
            best = None
            for u, cu, wu in _num_reads(m.group(1)):
                for x, cx, wx in _num_reads(m.group(2)):
                    if not (1 <= x <= MAX_LT_READ and 0 <= u <= 2 * x):
                        continue
                    conf = min(cu, cx)
                    if best is None or conf > best[0]:
                        best = (conf, u, x, wu if cu <= cx else wx)
            if best is None:
                continue
            conf, u, x, why = best
            inside = _inside(ln, size, box)
            cands.append((inside, conf, len(cands),
                          {"kind": "weight", "name": "weight",
                           "value": {"used": round(u, 3), "max": round(x, 3)},
                           "conf": round(conf, 3),
                           "why": f"x / y LT ({why})" + ("; in the weight region" if inside
                                                          else "")}))
    return _best(cands)


def slots_field(doc, size=None, regions=None):
    """{kind: slots, name: slots, value: {used, total}, conf, why} or None. A
    bare "a / b" is only read with a slot / inventory label on its line, beside
    the weight line, or inside the slots region - never anywhere on screen."""
    lines = _lines_of(doc)
    box = _box(regions, "slots")
    weight_rows = [i for i, ln in enumerate(lines) if WEIGHT_RE.search(ln["text"])]
    cands = []
    for i, ln in enumerate(lines):
        text = ln["text"]
        if WEIGHT_RE.search(text) or CP_PAIR_RE.search(text) or "%" in text:
            continue
        for m in SLOTS_RE.finditer(text):
            used, total = int(m.group(1)), int(m.group(2))
            if not (SLOTS_RANGE[0] <= total <= SLOTS_RANGE[1] and used <= total):
                continue
            inside = _inside(ln, size, box)
            if SLOTS_LABEL.search(text):
                conf, why = CONF_SLOTS_LABEL, "slot label on the line"
            elif any(ln is b for w in weight_rows for b in ocr._neighbours(lines, w)) \
                    or any(lines[w] is b for w in weight_rows for b in ocr._neighbours(lines, i)):
                conf, why = CONF_SLOTS_BESIDE, "beside the weight line"
            elif inside:
                conf, why = CONF_SLOTS_REGION, "in the slots region"
            else:
                continue
            cands.append((inside, conf, len(cands),
                          {"kind": "slots", "name": "slots",
                           "value": {"used": used, "total": total}, "conf": conf, "why": why}))
    return _best(cands)


def cp_field(doc, size=None, regions=None):
    """{kind: cp, name: cp, value: total CP, conf, why} from the "Contribution
    Points 120 / 312" readout (the total after the slash), or None."""
    box = _box(regions, "cp")
    cands = []
    for ln in _lines_of(doc):
        for m in CP_PAIR_RE.finditer(ln["text"]):
            left, total = int(m.group(1)), int(m.group(2))
            if left <= total <= CP_MAX_READ:
                cands.append((_inside(ln, size, box), CONF_CP_PAIR, len(cands),
                              {"kind": "cp", "name": "cp", "value": total,
                               "conf": CONF_CP_PAIR, "why": "points left / total"}))
        for m in CP_ONE_RE.finditer(ln["text"]):
            v = int(m.group(1))
            if v <= CP_MAX_READ:
                cands.append((_inside(ln, size, box), CONF_CP_ONE, len(cands),
                              {"kind": "cp", "name": "cp", "value": v, "conf": CONF_CP_ONE,
                               "why": "one number (left or total?)"}))
    return _best(cands)


def fame_field(doc, size=None, regions=None):
    """{kind: fame, name: fame, value: pct, conf, why} from the market sell
    dialog's family fame bonus line ("Family Fame bonus +1.250%"), or None."""
    box = _box(regions, "fame")
    cands = []
    for ln in _lines_of(doc):
        for m in FAME_RE.finditer(ln["text"]):
            whole, sep, frac = m.group(1), m.group(2), m.group(3)
            v = int(whole) + (int(frac) / 10 ** len(frac) if frac else 0)
            if v > FAME_MAX_READ:
                continue
            if sep is None:
                conf, why = CONF_FAME_WHOLE, "fame % without decimals"
            elif sep == ",":
                conf, why = CONF_FAME_COMMA, "decimal comma"
            else:
                conf, why = CONF_FAME_DOT, "fame % with decimals"
            v = round(v, 3)
            cands.append((_inside(ln, size, box), conf, len(cands),
                          {"kind": "fame", "name": "fame", "value": int(v) if v == int(v) else v,
                           "conf": conf, "why": why}))
    return _best(cands)


def planner_fields(doc, size=None, regions=None):
    """Every plan 081 planner read in one doc (weight, slots, cp, fame)."""
    out = []
    for fn in (weight_field, slots_field, cp_field, fame_field):
        f = fn(doc, size=size, regions=regions)
        if f is not None:
            out.append(f)
    return out


# -- sanity over history (S7) ------------------------------------------------------

def _parse(ts):
    try:
        when = _dt.datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    return when.timestamp() if when.tzinfo is not None else None


def check_level(samples, level, pct, shot_ts):
    """None when (level, pct) read at `shot_ts` extends the history, else why it
    is held for review. `samples`: cleaned plan 011 samples, oldest first
    (typed, `ocr` and plan 041 profile markers). A typed sample at or after the
    shot always wins (manual entry beats an older OCR read)."""
    for s in reversed(samples):
        ts = _parse(s.get("ts"))
        if ts is None:
            continue
        typed = s.get("source") is None and s.get("pct") is not None
        if ts > shot_ts or (typed and ts >= shot_ts):
            return f"shot older than a {'typed' if typed else 'later'} sample ({s['ts']})"
    known, _, _ = _level_now(samples) if samples else (None, None, None)
    if known is not None and level < known:
        return f"level {level} below the known Lv {known}"
    last = next((s for s in reversed(samples) if s.get("pct") is not None), None)
    if last is not None and last["level"] == level and pct < last["pct"]:
        return f"XP {pct}% went back from {last['pct']}% at Lv {level}"
    return None


def prev_sample(samples, shot_ts):
    """Newest sample with a pct at or before `shot_ts`, or None."""
    for s in reversed(samples):
        ts = _parse(s.get("ts"))
        if ts is not None and ts <= shot_ts and s.get("pct") is not None:
            return s
    return None


def book_suggestion(prev, level, pct, shot_ts, rate, books):
    """A {kind: book_use, name: size, value: {size, pct_before, pct_after},
    conf, why} review field when the XP jump since `prev` (same level, at most
    BOOK_WINDOW_S earlier) minus the grind gain at `rate` %/h matches one book
    of a size in `books` ({size: pct of one book}, sizes just added and owned),
    else None."""
    if prev is None or not books or prev.get("level") != level:
        return None
    t0 = _parse(prev.get("ts"))
    if t0 is None or not 0 < shot_ts - t0 <= BOOK_WINDOW_S:
        return None
    jump = pct - prev["pct"]
    residual = jump - (rate or 0) * (shot_ts - t0) / 3600
    best = None
    for size, per in books.items():
        if not isinstance(per, (int, float)) or per <= 0:
            continue
        off = abs(residual - per) / per
        if off <= BOOK_TOL and (best is None or off < best[1]):
            best = (size, off, per)
    if best is None:
        return None
    size, off, per = best
    return {"kind": "book_use", "name": size,
            "value": {"size": size, "pct_before": prev["pct"], "pct_after": pct},
            "conf": round(1 - off, 3),
            "why": f"XP +{round(jump, 3)}% in {int(shot_ts - t0)} s; one {size} book is ~{per}%"}


# -- silver/h (plan 062 session) ---------------------------------------------------

def session_window(play):
    """{id, start, end|None} of the open plan 062 play session, else the last
    one, from PlaySession.view(); None without either."""
    if not isinstance(play, dict):
        return None
    o = play.get("open")
    if isinstance(o, dict) and isinstance(o.get("start"), str):
        return {"id": o.get("id"), "start": o["start"], "end": None}
    last = play.get("last")
    if isinstance(last, dict) and isinstance(last.get("start"), str):
        return {"id": last.get("id"), "start": last["start"], "end": last.get("end")}
    return None


def silver_rate(samples, window):
    """{session, per_h, span_s, n}: silver per hour between the first and the
    last OCR silver sample inside one play session window; None with fewer than
    2 such samples or under SILVER_MIN_SPAN_S between them."""
    if not isinstance(window, dict):
        return None
    start = _parse(window.get("start"))
    end = _parse(window.get("end")) if window.get("end") else None
    if start is None:
        return None
    pts = []
    for s in samples if isinstance(samples, list) else []:
        if not (isinstance(s, dict) and isinstance(s.get("source"), str)
                and s["source"].startswith("ocr:") and isinstance(s.get("value"), int)):
            continue
        ts = _parse(s.get("at"))
        if ts is not None and ts >= start and (end is None or ts <= end):
            pts.append((ts, s["value"]))
    pts.sort()
    if len(pts) < 2 or pts[-1][0] - pts[0][0] < SILVER_MIN_SPAN_S:
        return None
    span = pts[-1][0] - pts[0][0]
    return {"session": window.get("id"),
            "per_h": int(round((pts[-1][1] - pts[0][1]) * 3600 / span)),
            "span_s": int(span), "n": len(pts)}
