"""OCR of operator-taken screenshots (plan 009 slice A).

Windows built-in Windows.Media.Ocr via the vendored PowerShell 5.1 script
`tools/ocr.ps1`, one image per call, run with CREATE_NO_WINDOW and a timeout.
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
OCR_TIMEOUT_S = 60
MAX_TEXT = 20000
MAX_LINE_TEXT = 500
MAX_LINES = 2000
MAX_ERROR = 200
MAX_CACHE = 200
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0

SILVER_WORD = re.compile(r"\bsilver\b", re.IGNORECASE)
# 1,234,567 / 1.234.567 / "1, 234, 567" (OCR spacing) / plain digits.
AMOUNT = re.compile(r"(?<![\w.,])(\d{1,3}(?:[,.] ?\d{3})+|\d+)(?![\w])")
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

def _amount(text):
    for m in AMOUNT.finditer(text):
        v = int(re.sub(r"[^0-9]", "", m.group(1)))
        if 0 <= v <= MAX_SILVER:
            return v
    return None


def extract_silver(lines):
    """Silver amount: digits (comma or dot grouped) on a "Silver" line, else on
    the same row, else just below it. None when nothing plausible is found."""
    lines = _lines(lines)
    for i, ln in enumerate(lines):
        m = SILVER_WORD.search(ln["text"])
        if not m:
            continue
        v = _amount(ln["text"][m.end():])
        if v is None:
            v = _amount(ln["text"][:m.start()])
        if v is not None:
            return v
        for b in _neighbours(lines, i):
            if SILVER_WORD.search(b["text"]):
                continue
            v = _amount(b["text"])
            if v is not None:
                return v
    return None


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


def _find_name(text, names):
    t = _norm(text)
    for name in names:
        n = _norm(name)
        if n.strip() and n in t:
            return name, t[t.index(n) + len(n):]
    return None, None


def extract_buffs(lines, names=SEED_BUFFS):
    """Buff names (word-bounded, case/punctuation-insensitive) with remaining
    minutes from the rest of the line, else the same row, else just below.
    One entry per name, first sighting wins, in line order."""
    lines = _lines(lines)
    out, seen = [], set()
    for i, ln in enumerate(lines):
        name, rest = _find_name(ln["text"], names)
        if name is None or name in seen:
            continue
        seen.add(name)
        minutes = duration_minutes(rest)
        if minutes is None:
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
        self.runner = runner if runner is not None else run_ocr
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
        raw = f"{shot['name']}\0{shot['size']}\0{shot['mtime']}".encode("utf-8")
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
                atomic_write_json(cpath, {"file": shot["name"], "size": shot["size"],
                                          "mtime": shot["mtime"], "ocr": doc})
                self._prune()
        return extract(doc)
