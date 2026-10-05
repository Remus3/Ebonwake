"""OCR of operator-taken screenshots (plan 009 slice A).

Engine (plan 009 follow-up, measured by tools/ocr_bench.py): Tesseract when
installed - image preprocessed by `tools/ocr_prep.ps1` (grayscale, invert when
dark, upscale), passes TESS_PASSES until a silver amount is read - else the
built-in Windows.Media.Ocr via `tools/ocr.ps1`. Every step is one subprocess
with CREATE_NO_WINDOW and a timeout.
Only a file the plan 008 watcher LISTED is read (read-only, by name, never a
caller-supplied path); nothing reaches the game. Results are suggestions: the
extractors only report, and the dashboard posts any accepted value to
/api/grind as ordinary operator input. Nothing is applied here.

The raw OCR lines are cached per file (name + size + mtime) under
ops/runtime/ocr/, and the extractors re-run on every read, so an extractor fix
needs no re-OCR.
"""

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from .grind import MAX_BUFF_MINUTES, MAX_SILVER, SEED_BUFFS
from .store import atomic_write_json

REPO_ROOT = Path(__file__).resolve().parents[2]
PS1 = REPO_ROOT / "tools" / "ocr.ps1"
PREP_PS1 = REPO_ROOT / "tools" / "ocr_prep.ps1"
# Engine choice measured by tools/ocr_bench.py (288 synthetic BDO-style silver /
# amount renders, 11-28 px, 4 fonts, crops + 1920x1080 frames), 2026-10-05:
# Windows.Media.Ocr x1 105/288, x2 159/288; Tesseract 5.4 preprocessed x3 psm6
# 284/288, x3 psm11 282/288, x1 psm6 254/288; the chain below, replayed, 284/288
# (the 4 misses are one wrong glyph: 3 at 14 px Arial, 1 at 28 px Malgun; fallbacks
# only fire when a pass reads no amount at all).
# Passes run in order until one yields a silver amount; the first pass's read
# is kept when none does. Windows OCR is the fallback when Tesseract is absent
# or fails (config `ocr.engine`: auto | tesseract | windows).
TESS_PASSES = ((3.0, 6), (3.0, 11), (1.0, 6))
PREP_MAX_DIM = 8000
CACHE_VERSION = "4"  # bump when the engine chain changes; old reads re-OCR once
ENGINES = ("auto", "tesseract", "windows")
# A word gap wider than this many word heights starts a new line (a space or a
# comma read as a space is ~0.4 h; separate UI numbers on one row sit further).
GAP_SPLIT = 0.8
# Digit word to digit word (009 word-gap fix, measured at x3, gap / max height):
# a comma Tesseract dropped 0.12-0.55, one real space 0.32-0.58, two 0.59-1.00.
# Wider than DIGIT_GAP_SPLIT, or heights / bottoms that do not match (another UI
# element), starts a new line; a kept gap of PROBE_GAP or more inside the silver
# amount is decided by the ink probe (a dropped comma leaves its tail below the
# baseline, a space leaves nothing).
DIGIT_GAP_SPLIT = 0.57
DIGIT_HEIGHT_DIFF = 0.20
DIGIT_BOTTOM_DIFF = 0.25
PROBE_GAP = 0.25
PROBE_ABOVE = 0.15  # probe rectangle: baseline - 0.15 h .. baseline + 0.40 h
PROBE_BELOW = 0.40
INK_SPACE_MAX = 0.02  # ink fraction below this in the gap = a real space
# Plan 016: Tesseract can drop a comma from the text yet keep its pixels in the
# RIGHT word's box ("4,521 372" from "4,521,372", bench c0017), so the probed gap
# is clean paper. A comma word's box runs 0.13-0.19 h below the digit baseline
# (measured, x3); a plain digit word whose bottom is within COMMA_TAIL h of a
# comma-tailed left word (or COMMA_TAIL h below a plain one) holds the dropped
# comma and its gap is never probed (kept joined).
COMMA_TAIL = 0.07
INK_PS1 = REPO_ROOT / "tools" / "ocr_ink.ps1"
OCR_TIMEOUT_S = 60
MAX_TEXT = 20000
MAX_LINE_TEXT = 500
MAX_LINES = 2000
MAX_ERROR = 200
MAX_CACHE = 200
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0

SILVER_WORD = re.compile(r"\bsilver\b", re.IGNORECASE)
# 1,234,567 / 1.234.567 / OCR spacing "1, 234 ,567" / a comma read as a space
# "1 234 567" (Tesseract, measured by tools/ocr_bench.py) / plain digits.
AMOUNT = re.compile(r"(?<![\w.,])(\d{1,3}(?: ?[,.] ?\d{3}| \d{3})+|\d+)(?![\w])")
DURATION = re.compile(r"(?<![\w.,])(\d+)\s*(days?|d|hours?|hrs?|h|minutes?|mins?|m|"
                      r"seconds?|secs?|s)\b", re.IGNORECASE)
_UNIT_S = {"d": 86400, "h": 3600, "m": 60, "s": 1}


class OcrError(RuntimeError):
    """OCR could not run or returned nothing usable (-> HTTP 502)."""


# -- output parsing --------------------------------------------------------------

def _clean_text(v, cap, keep_newlines=False):
    s = v if isinstance(v, str) else ""
    ok = (lambda ch: ord(ch) >= 32 and ord(ch) != 127) if not keep_newlines else \
        (lambda ch: ch == "\n" or (ord(ch) >= 32 and ord(ch) != 127))
    return "".join(ch for ch in s if ok(ch))[:cap]


def _num(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return 0
    return int(v)


def _lines(raw):
    out = []
    for ln in raw if isinstance(raw, list) else []:
        if not isinstance(ln, dict) or not isinstance(ln.get("text"), str):
            continue
        out.append({"text": _clean_text(ln["text"], MAX_LINE_TEXT), "x": _num(ln.get("x")),
                    "y": _num(ln.get("y")), "w": _num(ln.get("w")), "h": _num(ln.get("h"))})
        if len(out) >= MAX_LINES:
            break
    return out


def _sanitise(doc):
    if not isinstance(doc, dict) or not isinstance(doc.get("text"), str) \
            or not isinstance(doc.get("lines"), list):
        raise OcrError("ocr output must be {text, lines}")
    return {"text": _clean_text(doc["text"], MAX_TEXT, keep_newlines=True),
            "lines": _lines(doc["lines"])}


def _load_json(stdout):
    s = (stdout or "").strip()
    for cand in (s, s.splitlines()[-1] if s else ""):
        try:
            return json.loads(cand)
        except ValueError:
            continue
    return None


def parse_output(stdout):
    """ocr.ps1 stdout -> {text, lines}; OcrError on {error} or a bad shape."""
    doc = _load_json(stdout)
    if isinstance(doc, dict) and "error" in doc:
        raise OcrError(_clean_text(str(doc["error"]), MAX_ERROR) or "ocr error")
    if doc is None:
        raise OcrError("ocr output is not JSON")
    return _sanitise(doc)


def powershell_exe():
    """Windows PowerShell 5.1 by absolute path (never pwsh, never cwd)."""
    root = os.environ.get("SystemRoot")
    if root:
        p = Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        if p.is_file():
            return str(p)
    return shutil.which("powershell") or "powershell"


def run_ocr(path, run=subprocess.run, timeout=OCR_TIMEOUT_S):
    """OCR one image file -> {text, lines}; OcrError on any failure."""
    args = [powershell_exe(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(PS1), str(path)]
    try:
        out = run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                  timeout=timeout, creationflags=_NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise OcrError(f"ocr timed out after {timeout} s") from None
    except (OSError, subprocess.SubprocessError) as e:
        raise OcrError(_clean_text(f"ocr did not start: {e}", MAX_ERROR)) from None
    if out.returncode != 0:
        doc = _load_json(out.stdout)
        if isinstance(doc, dict) and "error" in doc:
            raise OcrError(_clean_text(str(doc["error"]), MAX_ERROR) or "ocr error")
        raise OcrError(f"ocr exited {out.returncode}")
    return parse_output(out.stdout)


def tesseract_exe(cfg_path=None):
    """Tesseract by config (`ocr.tesseract` in config/local.json), PATH, then the
    default per-machine install dir. None when absent (Windows OCR only)."""
    cfg = cfg_path or (REPO_ROOT / "config" / "local.json")
    try:
        c = json.loads(Path(cfg).read_text(encoding="utf-8")).get("ocr") or {}
        p = c.get("tesseract") if isinstance(c, dict) else None
        if isinstance(p, str) and p and Path(p).is_file():
            return p
    except (OSError, ValueError, AttributeError):
        pass
    found = shutil.which("tesseract")
    if found:
        return found
    for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        root = os.environ.get(var)
        if root:
            for sub in ("Tesseract-OCR", os.path.join("Programs", "Tesseract-OCR")):
                cand = Path(root) / sub / "tesseract.exe"
                if cand.is_file():
                    return str(cand)
    return None


def _digit_split(prev, w):
    """Line break between two digit words: wide gap, or a height / bottom mismatch."""
    hp, hw = max(prev[4], 1), max(w[4], 1)
    h = max(hp, hw)
    gap = w[1] - (prev[1] + prev[3])
    return gap > DIGIT_GAP_SPLIT * h or abs(hp - hw) > DIGIT_HEIGHT_DIFF * h \
        or abs((prev[2] + prev[4]) - (w[2] + w[4])) > DIGIT_BOTTOM_DIFF * h


def _probe_rect(prev, w):
    """Prepped-image rectangle (x, y, w, h) of the gap below and just above the
    digit baseline, or None when the gap is too narrow to be a real space."""
    h = max(prev[4], w[4], 1)
    x0, x1 = prev[1] + prev[3] + 1, w[1] - 1
    if w[1] - (prev[1] + prev[3]) < PROBE_GAP * h or x1 <= x0:
        return None
    base = min(prev[2] + prev[4], w[2] + w[4])  # a comma-tailed word sits lower
    y0 = round(base - PROBE_ABOVE * h)
    return (x0, y0, x1 - x0, max(1, round(base + PROBE_BELOW * h) - y0))


def _swallowed_comma(prev, w, tailed):
    """The plain digit word w sits as low as a comma tail: its box holds a comma
    Tesseract dropped from the text (plan 016). `tailed`: prev descends too."""
    if not w[0].isdigit():
        return False
    drop = (w[2] + w[4]) - (prev[2] + prev[4])
    return drop >= (-1 if tailed else 1) * COMMA_TAIL * max(prev[4], w[4], 1)


def _parse_tsv(stdout, scale=1.0):
    """parse_tsv plus the probe-able digit|digit boundaries kept inside a line:
    [{line, at (index of the joining space), rect (prepped px), cut (x end of the
    left word, x start of the right word, original px)}]."""
    groups = {}
    order = []
    for row in (stdout or "").splitlines()[1:]:
        f = row.split("\t")
        if len(f) < 12 or f[0] != "5":
            continue
        word = _clean_text(f[11].strip(), MAX_LINE_TEXT)
        try:
            x, y, w, h = (int(v) for v in f[6:10])
            conf = float(f[10])
        except ValueError:
            continue
        if not word or conf < 0:
            continue
        key = (f[1], f[2], f[3], f[4])
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append((word, x, y, w, h))
    segs = []
    # Split a Tesseract line at wide gaps: psm 6 joins a whole screen row into one
    # line, and "1,234,567 ... 100" must not read 1234567100 (009 follow-up r1).
    for key in order:
        ws = sorted(groups[key], key=lambda w: w[1])
        seg = [ws[0]]
        for w in ws[1:]:
            prev = seg[-1]
            if prev[0][-1].isdigit() and w[0][0].isdigit():
                split = _digit_split(prev, w)
            else:
                split = w[1] - (prev[1] + prev[3]) > GAP_SPLIT * max(prev[4], w[4], 1)
            if split:
                segs.append(seg)
                seg = [w]
            else:
                seg.append(w)
        segs.append(seg)
    lines, bounds = [], []
    for ws in segs[:MAX_LINES]:
        x0 = min(w[1] for w in ws)
        y0 = min(w[2] for w in ws)
        x1 = max(w[1] + w[3] for w in ws)
        y1 = max(w[2] + w[4] for w in ws)
        at, swallowed = -1, False
        for prev, w in zip(ws, ws[1:]):
            at += len(prev[0]) + 1
            digits = prev[0][-1].isdigit() and w[0][0].isdigit()
            tailed = swallowed or re.search(r"[,.]", prev[0]) is not None
            swallowed = digits and _swallowed_comma(prev, w, tailed)
            if digits and not swallowed and at < MAX_LINE_TEXT:
                rect = _probe_rect(prev, w)
                if rect:
                    bounds.append({"line": len(lines), "at": at, "rect": rect,
                                   "cut": (round((prev[1] + prev[3]) / scale),
                                           round(w[1] / scale))})
        lines.append({"text": " ".join(w[0] for w in ws), "x": round(x0 / scale),
                      "y": round(y0 / scale), "w": round((x1 - x0) / scale),
                      "h": round((y1 - y0) / scale)})
    doc = _sanitise({"text": "\n".join(ln["text"] for ln in lines), "lines": lines})
    return doc, bounds


def parse_tsv(stdout, scale=1.0):
    """Tesseract `tsv` output -> {text, lines}; words grouped by (block, par, line),
    boxes mapped back to original pixels by dividing by `scale`."""
    return _parse_tsv(stdout, scale)[0]


PROBE_TIMEOUT_S = 10  # measured ~0.4 s; a hung probe must not stall a read for 60 s


def ink_probe(image, rects, run=subprocess.run, timeout=PROBE_TIMEOUT_S):
    """tools/ocr_ink.ps1: dark-pixel fraction per rectangle of a prepped image."""
    spec = ";".join(",".join(str(int(v)) for v in r) for r in rects)
    args = [powershell_exe(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(INK_PS1), "-In", str(image), "-Rects", spec]
    try:
        out = run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                  timeout=timeout, creationflags=_NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise OcrError(f"ink probe timed out after {timeout} s") from None
    except (OSError, subprocess.SubprocessError) as e:
        raise OcrError(_clean_text(f"ink probe did not start: {e}", MAX_ERROR)) from None
    doc = _load_json(out.stdout)
    ink = doc.get("ink") if isinstance(doc, dict) else None
    if isinstance(ink, (int, float)) and not isinstance(ink, bool):
        ink = [ink]  # PowerShell 5.1 may flatten a one-element array
    if out.returncode != 0 or not isinstance(ink, list) or len(ink) != len(rects) \
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                       and 0 <= v <= 1 for v in ink):
        raise OcrError("ink probe returned no fractions")
    return [float(v) for v in ink]


def _cut_line(doc, b):
    """doc with line b["line"] split in two at the space b["at"]."""
    lines = list(doc["lines"])
    ln = lines[b["line"]]
    left_x1, right_x0 = b["cut"]
    right_x1 = ln["x"] + ln["w"]
    lines[b["line"]:b["line"] + 1] = [
        dict(ln, text=ln["text"][:b["at"]], w=max(0, left_x1 - ln["x"])),
        dict(ln, text=ln["text"][b["at"] + 1:], x=right_x0, w=max(0, right_x1 - right_x0))]
    return {"text": "\n".join(x["text"] for x in lines), "lines": lines}


def split_spaced_amount(doc, bounds, image, probe):
    """When the silver amount runs across a probe-able digit|digit gap, ask the
    ink probe (ONE call) and cut the line at the first gap with no ink (a real
    space, not a dropped comma). Probe failure keeps the joined read."""
    hit = _silver_hit(doc["lines"])
    if hit is None:
        return doc
    _, li, start, end = hit
    text = doc["lines"][li]["text"]
    # A grouped tail ("21 187,096,269", "1 234 567") is the rest of one number,
    # never a second one: only the LAST gap, before a plain digit run, may be cut.
    spans = [b for b in bounds if b["line"] == li and start < b["at"] < end
             and not re.search(r"[,. ]", text[b["at"] + 1:end])]  # last gap only (r1)
    if not spans:
        return doc
    try:
        ink = list(probe(image, [b["rect"] for b in spans]))
    except Exception:  # noqa: BLE001 - any probe failure = today's joined read
        return doc
    for b, f in zip(spans, ink):
        if f < INK_SPACE_MAX:
            return _cut_line(doc, b)
    return doc


def run_tesseract(path, exe=None, psm=11, scale=1.0, run=subprocess.run,
                  timeout=OCR_TIMEOUT_S, with_bounds=False):
    """Tesseract over one (already preprocessed) image -> {text, lines}, or
    (doc, probe-able digit gaps) with `with_bounds`."""
    exe = exe or tesseract_exe()
    if not exe:
        raise OcrError("tesseract not found")
    args = [exe, str(path), "stdout", "--psm", str(psm), "-l", "eng", "tsv"]
    try:
        out = run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                  timeout=timeout, creationflags=_NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise OcrError(f"tesseract timed out after {timeout} s") from None
    except (OSError, subprocess.SubprocessError) as e:
        raise OcrError(_clean_text(f"tesseract did not start: {e}", MAX_ERROR)) from None
    if out.returncode != 0:
        raise OcrError(f"tesseract exited {out.returncode}")
    doc, bounds = _parse_tsv(out.stdout, scale)
    return (doc, bounds) if with_bounds else doc


def ocr_engine(cfg_path=None):
    """`ocr.engine` from config/local.json; "auto" when unset or unknown."""
    cfg = cfg_path or (REPO_ROOT / "config" / "local.json")
    try:
        c = json.loads(Path(cfg).read_text(encoding="utf-8")).get("ocr") or {}
        e = c.get("engine") if isinstance(c, dict) else None
    except (OSError, ValueError, AttributeError):
        e = None
    return e if e in ENGINES else "auto"


def prep_image(src, dst, scale, run=subprocess.run, timeout=OCR_TIMEOUT_S):
    """tools/ocr_prep.ps1: grayscale / invert-if-dark / upscale -> applied scale."""
    args = [powershell_exe(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(PREP_PS1), "-In", str(src), "-Out", str(dst), "-Scale", str(scale),
            "-MaxDim", str(PREP_MAX_DIM)]
    try:
        out = run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                  timeout=timeout, creationflags=_NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise OcrError(f"ocr prep timed out after {timeout} s") from None
    except (OSError, subprocess.SubprocessError) as e:
        raise OcrError(_clean_text(f"ocr prep did not start: {e}", MAX_ERROR)) from None
    doc = _load_json(out.stdout)
    if out.returncode != 0 or not isinstance(doc, dict) or "error" in doc:
        err = doc.get("error") if isinstance(doc, dict) else None
        raise OcrError(_clean_text(str(err or f"ocr prep exited {out.returncode}"), MAX_ERROR))
    applied = doc.get("scale")
    if isinstance(applied, bool) or not isinstance(applied, (int, float)) \
            or not 0 < applied <= scale:
        raise OcrError("ocr prep returned no scale")
    return float(applied)


def run_tesseract_chain(path, work_dir, exe, run=subprocess.run, passes=TESS_PASSES,
                        probe=None):
    """Preprocess + Tesseract per pass until a silver amount is read; a silver
    amount read across a one-space-wide digit gap goes to the ink probe."""
    probe = probe or (lambda image, rects: ink_probe(image, rects, run=run))
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    first, preps = None, {}
    try:
        for scale, psm in passes:
            if scale not in preps:
                dst = work / f"prep-{os.getpid()}-{threading.get_ident()}-{len(preps)}.png"
                preps[scale] = (dst, prep_image(path, dst, scale, run=run))
            dst, applied = preps[scale]
            doc, bounds = run_tesseract(dst, exe=exe, psm=psm, scale=applied, run=run,
                                        with_bounds=True)
            if first is None:
                first = doc
            if extract_silver(doc["lines"]) is not None:
                return split_spaced_amount(doc, bounds, dst, probe)
        return first
    finally:
        for dst, _ in preps.values():
            try:
                dst.unlink()  # derived temp image, re-created by the next read
            except OSError:
                pass


def run_auto(path, work_dir, run=subprocess.run, engine=None, exe=None):
    """Default OCR runner: the Tesseract chain, Windows OCR as the fallback."""
    engine = engine or ocr_engine()
    if engine != "windows":
        exe = exe or tesseract_exe()
        if exe:
            try:
                return run_tesseract_chain(path, work_dir, exe, run=run)
            except OcrError:
                if engine == "tesseract":
                    raise
        elif engine == "tesseract":
            raise OcrError("tesseract not found")
    return run_ocr(path, run=run)


# -- geometry helpers ------------------------------------------------------------

def _cy(ln):
    return ln["y"] + ln["h"] / 2


def _same_row(a, b):
    return abs(_cy(a) - _cy(b)) <= max(a["h"], b["h"], 1) / 2


def _below(a, b):
    """b sits under a within two line heights."""
    gap = b["y"] - (a["y"] + a["h"])
    return b["y"] > a["y"] + a["h"] / 2 and gap <= 2 * max(a["h"], 1)


def _neighbours(lines, i):
    """Other lines on i's row (nearest first), then lines just below (nearest first)."""
    a = lines[i]
    row = [b for j, b in enumerate(lines) if j != i and _same_row(a, b)]
    row.sort(key=lambda b: abs(b["x"] - a["x"]))
    below = [b for j, b in enumerate(lines) if j != i and not _same_row(a, b) and _below(a, b)]
    below.sort(key=lambda b: b["y"])
    return row + below


# -- extractors ------------------------------------------------------------------

def _amount_at(text, pos=0, endpos=None):
    """(value, start, end) of the first plausible amount in text[pos:endpos]."""
    for m in AMOUNT.finditer(text[pos:endpos]):  # a slice: no lookbehind past pos
        v = int(re.sub(r"[^0-9]", "", m.group(1)))
        if 0 <= v <= MAX_SILVER:
            return v, pos + m.start(1), pos + m.end(1)
    return None


def _amount(text):
    hit = _amount_at(text)
    return hit[0] if hit else None


def _silver_hit(lines):
    """(value, line index, start, end) of the silver amount, or None."""
    lines = _lines(lines)
    idx = {id(ln): i for i, ln in enumerate(lines)}
    for i, ln in enumerate(lines):
        m = SILVER_WORD.search(ln["text"])
        if not m:
            continue
        hit = _amount_at(ln["text"], m.end()) or _amount_at(ln["text"], 0, m.start())
        if hit is not None:
            return (hit[0], i) + hit[1:]
        for b in _neighbours(lines, i):
            if SILVER_WORD.search(b["text"]):
                continue
            hit = _amount_at(b["text"])
            if hit is not None:
                return (hit[0], idx[id(b)]) + hit[1:]
    return None


def extract_silver(lines):
    """Silver amount: digits (comma or dot grouped) on a "Silver" line, else on
    the same row, else just below it. None when nothing plausible is found."""
    hit = _silver_hit(lines)
    return hit[0] if hit else None


def duration_minutes(text):
    """"1 hr 20 min" / "29d 23h" / "45 minutes" -> whole minutes rounded up,
    capped at the grind buff maximum; None without a unit-tagged number. Clock
    forms ("12:34") are ambiguous (mm:ss vs hh:mm) and are not read."""
    total, hit = 0, False
    for m in DURATION.finditer(text if isinstance(text, str) else ""):
        total += int(m.group(1)) * _UNIT_S[m.group(2)[0].lower()]
        hit = True
    if not hit:
        return None
    return min(MAX_BUFF_MINUTES, math.ceil(total / 60))


def _norm(s):
    return " " + re.sub(r"[^a-z0-9]+", " ", s.lower()).strip() + " "


def _find_names(text, names):
    """Every buff name on a normalised line as (name, rest), in line order; each
    `rest` runs only up to the next name, so one OCR line holding two buffs
    gives each its own time (plan 009 refute round 1)."""
    t = _norm(text)
    hits = []
    for name in names:
        n = _norm(name)
        if not n.strip():
            continue
        start = t.find(n)
        while start >= 0:
            hits.append((start, start + len(n), name))
            start = t.find(n, start + 1)
    hits.sort(key=lambda h: (h[0], -h[1]))  # same start: the LONGER name first
    kept, end = [], -1
    for s, e, name in hits:  # drop a name nested inside an earlier, longer hit
        if s >= end - 1:
            kept.append((s, e, name))
            end = e
    out = []
    for i, (s, e, name) in enumerate(kept):
        stop = kept[i + 1][0] if i + 1 < len(kept) else len(t)
        rest = t[e:stop]
        if i + 1 < len(kept) and kept[i + 1][2] == name and not rest.strip():
            continue  # "XP scroll XP scroll 20 min": the repeat carries the time
        out.append((name, rest))
    return out


def _find_name(text, names):
    found = _find_names(text, names)
    return found[0] if found else (None, None)


def extract_buffs(lines, names=SEED_BUFFS):
    """Buff names (word-bounded, case/punctuation-insensitive) with remaining
    minutes from the rest of the line, else the same row, else just below.
    One entry per name, first sighting wins, in line order."""
    lines = _lines(lines)
    out, seen = [], set()
    for i, ln in enumerate(lines):
        found = _find_names(ln["text"], names)
        for name, rest in found:
            if name in seen:
                continue
            seen.add(name)
            minutes = duration_minutes(rest)
            if minutes is None and len(found) == 1:
                for b in _neighbours(lines, i):
                    if _find_name(b["text"], names)[0] is not None:
                        continue
                    minutes = duration_minutes(b["text"])
                    if minutes is not None:
                        break
            out.append({"name": name, "minutes": minutes})
    return out


def extract(doc):
    """{text, lines} -> POST /api/ocr body {text, silver, buffs}."""
    return {"text": doc["text"], "silver": extract_silver(doc["lines"]),
            "buffs": extract_buffs(doc["lines"])}


# -- service ---------------------------------------------------------------------

class OcrService:
    """POST /api/ocr: OCR a watcher-listed screenshot, cached per file."""

    def __init__(self, game, cache_dir, runner=None):
        self.game = game
        self.cache_dir = Path(cache_dir)
        self.runner = runner if runner is not None else (
            lambda p: run_auto(p, self.cache_dir / "prep"))
        self._lock = threading.Lock()  # one powershell at a time

    def _listed(self, body):
        if not isinstance(body, dict) or set(body) != {"file"}:
            raise ValueError('body must be {"file": name}')
        name = body["file"]
        if not isinstance(name, str) or not name:
            raise ValueError("file must be a screenshot name")
        if self.game.shot_dir is None:
            raise ValueError("screenshot folder is not configured")
        shot = next((s for s in self.game.view()["screenshots"] if s["name"] == name), None)
        if shot is None or Path(name).name != name or "/" in name or "\\" in name:
            raise ValueError("file is not in the screenshot list")
        return shot

    def _key(self, shot):
        raw = f"{CACHE_VERSION}\0{shot['name']}\0{shot['size']}\0{shot['mtime']}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:32]

    def _cached(self, path):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            return _sanitise(doc["ocr"])
        except (OSError, ValueError, KeyError, TypeError, OcrError):
            return None

    def _prune(self):
        try:
            files = sorted(self.cache_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
        except OSError:
            return
        for p in files[:max(0, len(files) - MAX_CACHE)]:
            try:
                p.unlink()  # derived cache, re-creatable by re-OCR
            except OSError:
                pass

    def read(self, body):
        shot = self._listed(body)
        cpath = self.cache_dir / f"{self._key(shot)}.json"
        with self._lock:
            doc = self._cached(cpath)
            if doc is None:
                doc = _sanitise(self.runner(self.game.shot_dir / shot["name"]))
                try:  # a cache that cannot be written must not lose the result
                    atomic_write_json(cpath, {"file": shot["name"], "size": shot["size"],
                                              "mtime": shot["mtime"], "ocr": doc})
                    self._prune()
                except OSError:
                    pass
        return extract(doc)
