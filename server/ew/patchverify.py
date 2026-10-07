"""Plan 085: check hinted tracked data rows against one official patch-notes page.

A tracked data row (any dict carrying `verified`) may hold an optional hint
`verify: {title, expect: [regex], contradict: [regex]}` (ASCII, at most
MAX_PATTERNS each, compiled case-insensitive at load; a bad regex is a load
error). Against one patch-notes Detail text whose title matches `title`, a row
is `contradicted` when any `contradict` pattern matches, `confirmed` when every
`expect` pattern matches (and at least one exists), else `silent`. An
unverified row (`verified: false`) without a hint is `unchecked`, never guessed.

Pure: no network, no clock, no file writes; `load` only reads the tracked data
files. Verdicts are runtime state (`dataverdicts`); tracked data is never edited.
"""

import datetime as _dt
import hashlib
import json
import re
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
PATCH_TITLE_RE = re.compile(r"^\[Updates\]\s*Patch Notes\b", re.IGNORECASE)
HINT_KEYS = ("title", "expect", "contradict")
MAX_PATTERNS = 4
MAX_PATTERN = 200
MAX_EVIDENCE = 200
VERDICTS = ("confirmed", "contradicted", "silent")
_MONTHS = {m: n for n, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_TITLE_DATE_RE = re.compile(r"\b([A-Za-z]{3,9})\.?\s+([0-9]{1,2}),?\s+([0-9]{4})\b")
_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_WS_RE = re.compile(r"\s+")


def is_patch_title(title):
    return isinstance(title, str) and bool(PATCH_TITLE_RE.match(title))


def _ascii(s):
    return isinstance(s, str) and all(32 <= ord(ch) < 127 for ch in s)


def _compile(p, where):
    if not (_ascii(p) and 0 < len(p) <= MAX_PATTERN):
        raise ValueError(f"{where}: pattern must be 1..{MAX_PATTERN} printable ASCII")
    try:
        return re.compile(p, re.IGNORECASE)
    except re.error as e:
        raise ValueError(f"{where}: bad regex: {e}") from None


def compile_hint(h, where="verify"):
    """{title, expect: [re], contradict: [re]} or ValueError."""
    if not isinstance(h, dict) or "title" not in h or not set(h) <= set(HINT_KEYS):
        raise ValueError(f"{where}: verify must be {{title, expect, contradict}}")
    out = {"title": _compile(h["title"], f"{where}.title")}
    for k in ("expect", "contradict"):
        ps = h.get(k, [])
        if not isinstance(ps, list) or len(ps) > MAX_PATTERNS:
            raise ValueError(f"{where}.{k}: a list of at most {MAX_PATTERNS} patterns")
        out[k] = [_compile(p, f"{where}.{k}[{i}]") for i, p in enumerate(ps)]
    if not out["expect"] and not out["contradict"]:
        raise ValueError(f"{where}: needs at least one expect or contradict pattern")
    return out


def _walk(node, path, out):
    if isinstance(node, dict):
        if "verified" in node:
            out.append((path, node))
        elif "verify" in node:
            raise ValueError(f"{path or '/'}: verify only on a data row (one with verified)")
        for k, v in node.items():
            if k != "verify":
                _walk(v, f"{path}/{k}" if path else str(k), out)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _walk(v, f"{path}/{i}" if path else str(i), out)


def data_rows(doc, file):
    """[(key, row)] for every data row of one parsed file; key = `<file>#<id>`
    (or `<file>#<json path>` for a row without a string id)."""
    found = []
    _walk(doc, "", found)
    out = []
    for path, row in found:
        rid = row.get("id")
        out.append((f"{file}#{rid if isinstance(rid, str) and rid else path}", row))
    return out


def fingerprint(row):
    """Changes whenever the tracked row (value or hint) changes."""
    return hashlib.sha1(json.dumps(row, sort_keys=True).encode("ascii")).hexdigest()[:12]


def load(data_dir=DATA_DIR):
    """{"hinted": {key: {"hint", "fp"}}, "unchecked": [key]} over every tracked
    data file; ValueError on a bad hint or an unreadable file."""
    root = Path(data_dir)
    hinted, unchecked = {}, []
    for p in sorted(root.rglob("*.json")):
        file = p.relative_to(root).as_posix()
        try:
            doc = json.loads(p.read_text(encoding="ascii"))
        except (OSError, ValueError) as e:
            raise ValueError(f"{file} unreadable: {type(e).__name__}") from e
        for key, row in data_rows(doc, file):
            if "verify" in row:
                if key in hinted:
                    raise ValueError(f"{key}: duplicate hinted row")
                hinted[key] = {"hint": compile_hint(row["verify"], key), "fp": fingerprint(row)}
            elif row.get("verified") is False:
                unchecked.append(key)
    return {"hinted": hinted, "unchecked": sorted(set(unchecked))}


def _evidence(text, m):
    """The line holding match `m`, whitespace collapsed, ASCII, <= MAX_EVIDENCE chars."""
    lo = text.rfind("\n", 0, m.start()) + 1
    hi = text.find("\n", m.end())
    line = text[lo:hi if hi >= 0 else len(text)]
    line = "".join(ch if 32 <= ord(ch) < 127 else " " for ch in line)
    line = _WS_RE.sub(" ", line).strip()
    if len(line) > MAX_EVIDENCE:  # keep the match in view
        start = max(0, min(m.start() - lo - 40, len(line) - MAX_EVIDENCE))
        line = line[start:start + MAX_EVIDENCE]
    return line


def check_row(text, hint):
    """{"verdict", "evidence"} of one compiled hint over one page text."""
    for rx in hint["contradict"]:
        m = rx.search(text)
        if m is not None:
            return {"verdict": "contradicted", "evidence": _evidence(text, m)}
    if hint["expect"]:
        hits = [rx.search(text) for rx in hint["expect"]]
        if all(h is not None for h in hits):
            return {"verdict": "confirmed", "evidence": _evidence(text, hits[0])}
    return {"verdict": "silent", "evidence": None}


def check(text, title, hinted):
    """{key: {"verdict", "evidence"}} for every hinted row whose title pattern
    matches this notice's title (other rows: no verdict from this notice)."""
    out = {}
    if not isinstance(text, str) or not isinstance(title, str):
        return out
    for key, h in hinted.items():
        if h["hint"]["title"].search(title):
            out[key] = check_row(text, h["hint"])
    return out


def note_date(title, stamp=None):
    """YYYY-MM-DD from a title like `Patch Notes - October 8, 2026`, else the
    list stamp, else None."""
    m = _TITLE_DATE_RE.search(title) if isinstance(title, str) else None
    if m is not None:
        mon = _MONTHS.get(m.group(1)[:3].lower())
        if mon is not None:
            try:
                return _dt.date(int(m.group(3)), mon, int(m.group(2))).isoformat()
            except ValueError:
                pass
    if isinstance(stamp, str) and _DATE_RE.match(stamp):
        return stamp
    return None
