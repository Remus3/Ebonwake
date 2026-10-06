"""Settings (plan 030): an allowlisted, validated view of config/local.json.

GET returns every allowlisted key (dotted path) with its value or default;
POST {"set": {"a.b": v, ...}} validates every pair first, then splices only
those values into the file text and writes it atomically (tmp + replace).
Untouched bytes - `secrets`, `loop`, `_doc` strings, key order, spacing - are
never re-serialized, so they survive byte-for-byte. Nothing outside the
allowlist is readable or writable here; no validator accepts an object other
than an overlay {x, y} anchor, so an `{"env": ...}` secret reference can never
be written through this route.
"""

import copy
import json
import math
import os
import re
import threading
import time
from json.decoder import scanstring
from pathlib import Path

from . import maint, market, progress

ANCHORS = ("tl", "tr", "bl", "br", "ml", "mr")
WIDGETS = {"grindSession": True, "grindBuff": True, "eventsSoon": True,
           "leveling": False, "season": False, "marketTicker": False,
           "worldBoss": False, "dice": False}
# Plan 026 rule names (+ plan 032 bossSoon); default off except marketAlert
# and buffEnding.
NOTIFY = {"marketAlert": True, "buffEnding": True, "hotTime": False,
          "resetPassed": False, "newCoupon": False, "gameExit": False,
          "bossSoon": False}
THEMES = ("system", "dark", "light")
OVERLAY_MODES = ("auto", "pin", "block")  # plan 067: per-widget pin / block
MODS = ("Control", "Ctrl", "Alt", "Shift", "CommandOrControl", "Super")
_KEY_RE = re.compile(r"^([A-Z0-9]|F([1-9]|1[0-9]|2[0-4]))$")
MAX_SET = 64
# Keys the running server reads only at start (dashboard offers a restart).
RESTART_KEYS = ("profile.family", "coupons.check", "events.notice_check")
_LOCK = threading.Lock()


def _is_bool(v):
    return isinstance(v, bool)


def _num_in(lo, hi):
    def ok(v):
        # isfinite only on floats: a 400-digit int would raise OverflowError.
        return (isinstance(v, (int, float)) and not isinstance(v, bool)
                and (isinstance(v, int) or math.isfinite(v)) and lo <= v <= hi)
    return ok


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def valid_anchor(v):
    if isinstance(v, str):
        return v in ANCHORS
    return (isinstance(v, dict) and set(v) == {"x", "y"} and _is_int(v["x"]) and _is_int(v["y"])
            and abs(v["x"]) <= 100000 and abs(v["y"]) <= 100000)


def valid_display(v):
    return v is None or (_is_int(v) and 0 <= v <= 16)


def valid_accelerator(v):
    """Electron accelerator: one or more known modifiers + one key (no bare letters)."""
    if not isinstance(v, str) or not v or len(v) > 64:
        return False
    parts = v.split("+")
    return (len(parts) >= 2 and all(p in MODS for p in parts[:-1])
            and bool(_KEY_RE.fullmatch(parts[-1])))  # fullmatch: `$` would pass "E\n"


def valid_family(v):
    return isinstance(v, str) and (v == "" or bool(progress.FAMILY_RE.fullmatch(v)))


def valid_maint_start(v):
    """"" (use data/maintenance.json) or HH:MM UTC."""
    return v == "" or maint.valid_hhmm(v)


MAX_DIR = 1024


def valid_dir(v):
    """"" (auto-detect) or an absolute path of an existing directory."""
    if v == "":
        return True
    if not isinstance(v, str) or len(v) > MAX_DIR or v != v.strip():
        return False
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in v):
        return False
    try:
        return os.path.isabs(v) and os.path.isdir(v)
    except (OSError, ValueError):
        return False


def _one_of(choices):
    return lambda v: isinstance(v, str) and v in choices


# dotted key -> (validator, default); insertion order is the GET order.
SPEC = {}
for _w, _d in WIDGETS.items():
    SPEC[f"overlay.widgets.{_w}"] = (_is_bool, _d)
SPEC.update({
    "overlay.anchor": (valid_anchor, "ml"),
    "overlay.display": (valid_display, None),
    "overlay.scale": (_num_in(0.8, 1.6), 1.0),
    "overlay.opacity": (_num_in(0.5, 0.95), 0.85),
    "hotkeys.toggleOverlay": (valid_accelerator, "Control+Alt+E"),
    "hotkeys.showDashboard": (valid_accelerator, "Control+Alt+D"),
    "profile.family": (valid_family, ""),
    "ui.theme": (_one_of(THEMES), "dark"),
    "ui.scale": (_num_in(0.9, 1.3), 1.0),
})
for _n, _d in NOTIFY.items():
    SPEC[f"notify.{_n}"] = (_is_bool, _d)
SPEC.update({
    "market.vp": (_is_bool, False),
    "market.fame_pct": (_num_in(0, market.FAME_MAX), 0),
    "coupons.check": (_is_bool, True),
    # Plan 059: official event-notice suggestions + the maintenance start override.
    "events.notice_check": (_is_bool, True),
    "events.maintenance_start_utc": (valid_maint_start, ""),
    # Plan 064: full-window notice reads are added (with undo), not suggested.
    "notices.auto_add": (_is_bool, True),
    # Plan 062: login opens / exit closes the grind log; exit grace in seconds.
    "play.auto_session": (_is_bool, True),
    "play.grace_s": (lambda v: _is_int(v) and 60 <= v <= 600, 120),
    # Plan 063: auto-OCR of screenshots taken while logged in.
    "ocr.auto": (_is_bool, True),
    "ocr.auto_commit_min": (_num_in(0.75, 0.99), 0.9),
    "ocr.daily_cap": (lambda v: _is_int(v) and 0 <= v <= 1000, 120),
    # Plan 065: "use other" overrides for the auto-detected BDO folders.
    "bdo.install_dir": (valid_dir, ""),
    "bdo.documents_dir": (valid_dir, ""),
    # Plan 067: widgets chosen by context; per-widget pin / block on top.
    "overlay.auto": (_is_bool, True),
    "overlay.idle_min": (lambda v: _is_int(v) and 5 <= v <= 240, 20),
})
for _w in WIDGETS:
    SPEC[f"overlay.mode.{_w}"] = (_one_of(OVERLAY_MODES), "auto")
DETECTED_KEYS = ("bdo.install_dir", "bdo.documents_dir")


def defaults():
    return {k: copy.deepcopy(d) for k, (_, d) in SPEC.items()}


def _lookup(doc, key):
    cur = doc
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None, False
        cur = cur[part]
    return cur, True


def values_from(doc):
    """Allowlisted dotted keys -> configured value, or the default when missing/invalid."""
    out = {}
    for key, (ok, dflt) in SPEC.items():
        v, found = _lookup(doc if isinstance(doc, dict) else {}, key)
        out[key] = copy.deepcopy(v) if found and ok(v) else copy.deepcopy(dflt)
    return out


def validate(body):
    """{"set": {key: value}} -> dict of validated pairs, or ValueError naming the first bad one."""
    if not isinstance(body, dict) or set(body) != {"set"}:
        raise ValueError('body must be {"set": {"a.b": value, ...}}')
    pairs = body["set"]
    if not isinstance(pairs, dict) or not pairs:
        raise ValueError("set must be a non-empty object")
    if len(pairs) > MAX_SET:
        raise ValueError(f"at most {MAX_SET} keys per request")
    for key, v in pairs.items():
        if key not in SPEC:
            raise ValueError(f"{key}: not a settable key")
        if not SPEC[key][0](v):
            raise ValueError(f"{key}: invalid value")
    return dict(pairs)


# ---- text splicing: edit values in place, never re-serialize the rest ----

_WS = " \t\r\n"


def _skip(text, i):
    while i < len(text) and text[i] in _WS:
        i += 1
    return i


def _scan(text):
    """Value spans {path: (start, end)} and object facts {path: {...}} of a JSON doc."""
    dec = json.JSONDecoder()
    spans, objs = {}, {}

    def value(i, path):
        i = _skip(text, i)
        if text[i] == "{":
            end = obj(i, path)
        else:
            _, end = dec.raw_decode(text, i)
        spans[path] = (i, end)
        return end

    def obj(i, path):
        info = {"open": i, "first": None, "last_end": None}
        j = _skip(text, i + 1)
        if text[j] != "}":
            info["first"] = j
            while True:
                if text[j] != '"':
                    raise ValueError("bad object")
                key, j = scanstring(text, j + 1)
                j = _skip(text, j)
                if text[j] != ":":
                    raise ValueError("bad object")
                sub = path + (key,)
                for table in (spans, objs):  # a duplicate key: the last one wins, as in json.loads
                    for k in [k for k in table if k[:len(sub)] == sub]:
                        del table[k]
                j = value(j + 1, sub)
                info["last_end"] = j
                j = _skip(text, j)
                if text[j] == ",":
                    j = _skip(text, j + 1)
                    continue
                if text[j] == "}":
                    break
                raise ValueError("bad object")
        info["close"] = j
        objs[path] = info
        return j + 1

    end = value(0, ())
    if _skip(text, end) != len(text):
        raise ValueError("trailing data")
    return spans, objs


def _nest(keys, v):
    for k in reversed(keys):
        v = {k: v}
    return v


def _dump(v):
    return json.dumps(v, ensure_ascii=True)


def splice(text, path, v):
    """Return `text` with the value at `path` (tuple of keys) set to `v`."""
    spans, objs = _scan(text)
    if path in spans:
        s, e = spans[path]
        return text[:s] + _dump(v) + text[e:]
    n = len(path) - 1
    while path[:n] not in spans:
        n -= 1
    p = path[:n]
    if p not in objs:  # an ancestor holds a non-object: replace it with the nested value
        s, e = spans[p]
        return text[:s] + _dump(_nest(path[n:], v)) + text[e:]
    o = objs[p]
    member = _dump(path[n]) + ": " + _dump(_nest(path[n + 1:], v))
    if o["last_end"] is None:
        return text[:o["close"]] + member + text[o["close"]:]
    indent = text[o["open"] + 1:o["first"]]
    at = o["last_end"]
    return text[:at] + "," + indent + member + text[at:]


def _set_doc(doc, path, v):
    cur = doc
    for k in path[:-1]:
        if not isinstance(cur.get(k), dict):
            cur[k] = {}
        cur = cur[k]
    cur[path[-1]] = copy.deepcopy(v)


def _atomic_write_bytes(path, data):
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    for attempt in range(20):  # Windows: a reader's handle briefly blocks replace
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.01 * (attempt + 1))


class Settings:
    def __init__(self, path, detected=None):
        self.path = Path(path)
        # Plan 065: callable -> {"install_dir", "documents_dir"} (detect.Detector.paths).
        self.detected = detected

    def _detected(self):
        try:
            got = self.detected() if self.detected is not None else {}
        except Exception:  # noqa: BLE001 - a detector fault shows as "nothing detected"
            got = {}
        got = got if isinstance(got, dict) else {}
        out = {}
        for key in DETECTED_KEYS:
            v = got.get(key.split(".", 1)[1])
            out[key] = v if isinstance(v, str) and v else None
        return out

    def _read_text(self):
        try:
            return self.path.read_bytes().decode("utf-8")
        except FileNotFoundError:
            return None

    def view(self):
        try:
            text = self._read_text()
            doc = json.loads(text) if text is not None else {}
            error = None if isinstance(doc, dict) else "config/local.json is not a JSON object"
        except (OSError, ValueError):
            doc, error = {}, "config/local.json is unreadable"
        return {"settings": values_from(doc if isinstance(doc, dict) else {}),
                "defaults": defaults(), "restart_keys": list(RESTART_KEYS), "error": error,
                "detected": self._detected()}

    def apply(self, body):
        pairs = validate(body)
        with _LOCK:
            try:
                text = self._read_text()
            except (OSError, ValueError):  # a directory, a lock, bad UTF-8
                raise ValueError("config/local.json is unreadable") from None
            if text is None:
                text = "{\n}\n"
            try:
                doc = json.loads(text)
                _scan(text)
            except (ValueError, RecursionError):
                raise ValueError("config/local.json is not valid JSON - fix it by hand first") from None
            if not isinstance(doc, dict):
                raise ValueError("config/local.json is not a JSON object - fix it by hand first")
            merged = values_from(doc)
            merged.update(pairs)
            if merged["hotkeys.toggleOverlay"] == merged["hotkeys.showDashboard"]:
                raise ValueError("hotkeys.toggleOverlay and hotkeys.showDashboard must differ")
            before = values_from(doc)
            want = copy.deepcopy(doc)
            for key, v in pairs.items():
                path = tuple(key.split("."))
                text = splice(text, path, v)
                _set_doc(want, path, v)
            if json.loads(text) != want:  # the splice must mean exactly the intended doc
                raise ValueError("config/local.json could not be edited safely - fix it by hand")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_bytes(self.path, text.encode("utf-8"))
        changed = [k for k in pairs if before[k] != pairs[k]]
        out = self.view()
        out["changed"] = changed
        out["restart"] = [k for k in changed if k in RESTART_KEYS]
        return out
