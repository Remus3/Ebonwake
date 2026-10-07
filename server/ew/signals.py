"""Plan 073: signal health digest - one liveness row per passive signal.

Rows: session log (plan 008), screenshot watcher (008), OCR (063), official
notices (059 / 064), market (002), profile (061), boss drift (072). Each row is
`ok | warn | off | bad` with an age and one fix hint from the tracked
`data/signal_hints.json`. A signal that is quiet because the game is closed or
its feature is off is `off`, never `bad`.

`digest(inputs, now)` is pure over plain dicts (fixture-friendly);
`SignalService` gathers those dicts from the live services and never fetches,
polls or reads the game - local state only.
"""

import datetime as _dt
import json
import time
from pathlib import Path

HINTS_FILE = Path(__file__).resolve().parent / "data" / "signal_hints.json"
LEVELS = ("ok", "warn", "off", "bad")
_RANK = {"off": 0, "ok": 1, "warn": 2, "bad": 3}
GENERIC_HINT = "see the System tab"

NAMES = {"session_log": "Session log", "screenshots": "Screenshot watcher", "ocr": "OCR",
         "notices": "Official notices", "market": "Market", "profile": "Profile",
         "boss_drift": "Boss drift"}
# Every reason a row can carry; the hints file must cover each (test-enforced).
REASONS = {
    "session_log": ("unconfigured", "log_dir_missing", "watcher_stalled", "quiet", "silent",
                    "game_closed"),
    "screenshots": ("unconfigured", "folder_missing", "game_closed"),
    "ocr": ("disabled", "error", "backlog", "game_closed"),
    "notices": ("disabled", "robots_disallow", "robots_unreachable", "failing", "stale"),
    "market": ("empty", "blocked", "stale"),
    "profile": ("no_family", "no_base", "robots", "stale", "error"),
    "boss_drift": ("not_built", "drift", "error"),
}
ACTIVE = ("running", "logged_in", "disconnected")  # plan 008 states with the client up

_DEFAULT = {
    "thresholds": {"session_log": {"warn_s": 300, "bad_s": 600, "stall_s": 60},
                   "ocr": {"queue_warn": 5},
                   "notices": {"stale_s": 172800, "bad_s": 172800},
                   "market": {"stale_s": 21600}},
    "hints": {"session_log.silent": "logged in but the session log is silent - "
                                    "check Settings > paths, then restart the server"},
}


def load_hints(path=None):
    """{thresholds, hints} from the tracked file; unreadable = built-in defaults."""
    try:
        doc = json.loads(Path(path or HINTS_FILE).read_text(encoding="ascii"))
    except (OSError, ValueError):
        doc = None
    if not (isinstance(doc, dict) and isinstance(doc.get("hints"), dict)
            and isinstance(doc.get("thresholds"), dict)):
        return _DEFAULT
    th = {k: dict(v, **(doc["thresholds"].get(k) or {}))
          for k, v in _DEFAULT["thresholds"].items()}
    return {"thresholds": th, "hints": dict(_DEFAULT["hints"], **doc["hints"])}


def hint(hints, rid, reason):
    text = hints["hints"].get(f"{rid}.{reason}")
    return text if isinstance(text, str) and text.strip() else GENERIC_HINT


def _num(v):
    """Epoch seconds from a number or an aware ISO string (httpcache stamps), or None."""
    if isinstance(v, str):
        try:
            when = _dt.datetime.fromisoformat(v)
        except ValueError:
            return None
        return when.timestamp() if when.tzinfo is not None else None
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _age(now, at):
    at = _num(at)
    return None if at is None else max(0, int(round(now - at)))


def _d(v):
    return v if isinstance(v, dict) else {}


# -- one function per row: (level, reason, age_s, detail) --------------------------

def _session_log(s, now, th):
    t = th["session_log"]
    if not s.get("configured"):
        return "warn", "unconfigured", None, "no install folder"
    if s.get("log_dir_ok") is False:
        return "bad", "log_dir_missing", None, "Log folder not found"
    polled = _age(now, s.get("polled_at"))
    if polled is None or polled > t["stall_s"]:
        return "bad", "watcher_stalled", polled, "last poll " + (
            "never" if polled is None else f"{polled} s ago")
    state = s.get("state")
    # Silence counts from the later of the last line and the last state change,
    # so an older session's line never ages a fresh launch.
    marks = [t for t in (_num(s.get("line_at")), _num(s.get("since"))) if t is not None]
    age = _age(now, max(marks)) if marks else None
    if state not in ACTIVE:
        return "off", "game_closed", age, f"game {state or 'not_running'}"
    detail = f"game {state}"
    if age is not None and age > t["bad_s"] and state == "logged_in":
        return "bad", "silent", age, detail
    if age is not None and age > t["warn_s"]:
        return "warn", "quiet", age, detail
    return "ok", None, age, detail


def _screenshots(s, now, th):
    if not s.get("configured"):
        return "warn", "unconfigured", None, "no Documents folder"
    age = _age(now, s.get("last_at"))
    if s.get("dir_ok") is False:
        return "warn", "folder_missing", age, "ScreenShot folder not found"
    if s.get("state") not in ACTIVE:
        return "off", "game_closed", age, "game closed"
    return "ok", None, age, "last shot " + ("none yet" if age is None else f"{age} s ago")


def _ocr(s, now, th):
    if s.get("enabled") is False:
        return "off", "disabled", None, "auto-OCR off"
    ok_at, err_at = _num(s.get("ok_at")), _num(s.get("error_at"))
    age = _age(now, ok_at)
    pending = s.get("pending") if isinstance(s.get("pending"), int) else 0
    failed = err_at is not None and (ok_at is None or err_at > ok_at)
    if s.get("state") not in ACTIVE and not pending:
        # Expected silence wins: an old failure shows in the detail, never as bad.
        return "off", "game_closed", age, "game closed" + (
            f"; last error: {s.get('error') or 'unknown'}"[:100] if failed else "")
    if failed:
        return "bad", "error", _age(now, err_at), f"last error: {s.get('error') or 'unknown'}"[:120]
    if pending >= th["ocr"]["queue_warn"]:
        return "warn", "backlog", age, f"{pending} queued"
    return "ok", None, age, f"{pending} queued"


def _notices(s, now, th):
    t = th["notices"]
    if not s.get("enabled"):
        return "off", "disabled", None, "notice check off"
    age = _age(now, s.get("ok_at"))
    robots = s.get("robots")
    if robots == "disallow":
        return "off", "robots_disallow", age, "robots: disallow"
    if robots == "unreachable":
        return "warn", "robots_unreachable", age, "robots: unreachable"
    fail = _age(now, s.get("fail_since"))
    if fail is not None:
        return ("bad" if fail > t["bad_s"] else "warn"), "failing", age, f"failing {fail} s"
    if age is not None and age > t["stale_s"]:
        return "warn", "stale", age, "last good fetch old"
    return "ok", None, age, "robots: " + (robots or "not read yet")


def _market(s, now, th):
    age = _age(now, s.get("ok_at"))
    blocked = s.get("blocked") if isinstance(s.get("blocked"), int) else 0
    watched = s.get("watched") if isinstance(s.get("watched"), int) else 0
    if not watched:
        return "off", "empty", age, "nothing watched"
    if blocked:
        return ("bad" if age is None else "warn"), "blocked", age, f"{blocked} endpoint(s) blocked"
    if age is not None and age > th["market"]["stale_s"]:
        return "warn", "stale", age, f"{watched} watched"
    return "ok", None, age, f"{watched} watched"


def _profile(s, now, th):
    status = s.get("status")
    age = _age(now, s.get("updated"))
    if status == "none":
        return "off", "no_family", age, "no profile source"
    if status == "off":
        reason = s.get("reason") if s.get("reason") in ("no_base", "robots") else "no_base"
        return "off", reason, age, f"off ({reason})"
    if status == "error":
        return "bad", "error", age, "fetch failing"
    if status == "stale":
        return "warn", "stale", age, "stale cache"
    return "ok", None, age, status or "ok"


def _boss_drift(s, now, th):
    if not s:
        return "off", "not_built", None, "plan 072 not built"
    age = _age(now, s.get("checked_at"))
    status = s.get("status")
    if status == "error":
        return "bad", "error", age, "check failed"
    if status == "drift":
        return "warn", "drift", age, "table differs"
    return "ok", None, age, "matches"


_ROWS = {"session_log": _session_log, "screenshots": _screenshots, "ocr": _ocr,
         "notices": _notices, "market": _market, "profile": _profile, "boss_drift": _boss_drift}


def _overrides(raw):
    """Plan 079: {count, items} of the override ledger's active entries."""
    items = raw.get("items") if isinstance(raw, dict) else None
    items = [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []
    return {"count": len(items), "items": items}


def digest(inputs, now, hints=None, overrides=None):
    """{rows: [{id, name, level, age_s, reason, detail, hint}], bad, worst, at,
    overrides: {count, items}}."""
    hints = hints or load_hints()
    inputs = _d(inputs)
    rows = []
    for rid in REASONS:
        raw = inputs.get(rid)
        try:
            if raw is not None and not isinstance(raw, dict):
                raise ValueError("unreadable")
            level, reason, age, detail = _ROWS[rid](_d(raw), now, hints["thresholds"])
        except Exception:  # noqa: BLE001 - junk input is a warn row, never a 500
            level, reason, age, detail = "warn", None, None, "unreadable state"
        rows.append({"id": rid, "name": NAMES[rid], "level": level, "age_s": age,
                     "reason": reason, "detail": detail,
                     "hint": hint(hints, rid, reason) if reason else None})
    worst = max((r["level"] for r in rows), key=_RANK.__getitem__)
    at = _dt.datetime.fromtimestamp(now, _dt.timezone.utc).replace(microsecond=0).isoformat()
    return {"rows": rows, "bad": sum(r["level"] == "bad" for r in rows), "worst": worst, "at": at,
            "overrides": _overrides(overrides)}


class SignalService:
    """GET /api/signals. `sources` maps a row id to fn() -> its input dict (or
    None); a failing source becomes a warn row, never a 500."""

    def __init__(self, sources, clock=time.time, hints_path=None, overrides=None):
        self.sources = sources
        self.clock = clock
        self.hints_path = hints_path
        self.overrides = overrides  # plan 079: () -> {count, items}

    def inputs(self):
        out = {}
        for rid, fn in self.sources.items():
            try:
                out[rid] = fn()
            except Exception:  # noqa: BLE001 - digest() turns it into a warn row
                out[rid] = "unreadable"
        return out

    def view(self):
        ovr = None
        if self.overrides is not None:
            try:
                ovr = self.overrides()
            except Exception:  # noqa: BLE001 - a ledger fault lists none, never a 500
                ovr = None
        return digest(self.inputs(), self.clock(), load_hints(self.hints_path), ovr)
