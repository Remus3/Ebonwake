"""First-run checklist (plan 051): ordered set-up steps, each `done` computed
from real state on every read.

Inputs: EW's own gitignored config/local.json (profile.family, overlay.anchor,
the plan 008 `bdo` folders), the plan 008 folders themselves (existence /
listing only, no file is ever opened) and the EW store (market watch, today
ticks, leveling samples). A view never carries a path. Store domain
`onboarding`: {"dismissed": "<iso>" | null}.

Plan 065: steps tick themselves from detection and data - the auto-detected
BDO folders (server/ew/detect.py; explicit config still wins), an overlay
corner left at its default, a plan 071 auto-seeded watch item - and the card
hides once every step left is optional.

Plan 077: the `log` and `screenshots` steps read the plan 073 signal digest
(`signals.py`) - the live `/api/signals` rows when wired, else the same
classifier over the resolved folders - so Get started, Settings and Signal
health tell one story. A found parent folder whose Log / ScreenShot subfolder
does not exist yet is an in-game act with no link (nothing to set).
"""

import datetime as _dt
import json
import threading
import time
from pathlib import Path

from . import detect, leveling, settings, signals

MIN_WATCH = 3

# id, title, hint, link {tab, field?}, optional; list order is the display order.
# Optional steps happen through normal use; they never keep the card up alone.
# Hints name Settings labels (group > field), never config keys (plan 077).
STEPS = (
    ("family", "Set your family name",
     "Settings > Profile > Family name turns on the profile card (BDO-REST-API).",
     {"tab": "settings", "field": "profile.family"}, False),
    ("log", "Point EW at the BDO install folder",
     "Not found in a Steam library: Settings > Game folders > BDO install folder "
     "(its Log folder tells EW the game is running).",
     {"tab": "settings", "field": "bdo.install_dir"}, False),
    ("screenshots", "Point EW at the BDO Documents folder",
     "Not found under Documents: Settings > Game folders > BDO Documents folder "
     "(its ScreenShot folder feeds OCR).",
     {"tab": "settings", "field": "bdo.documents_dir"}, False),
    ("overlay", "Choose an overlay corner",
     "Settings > Overlay > Anchor picks where the overlay sits.",
     {"tab": "settings", "field": "overlay.anchor"}, False),
    ("watch", f"Watch {MIN_WATCH} market items",
     "Market > add items to price-watch.",
     {"tab": "market"}, True),
    ("daily", "Tick your first daily",
     "Today > tick a daily once it is done.",
     {"tab": "today"}, True),
    ("leveling", "Log an XP sample",
     "Progress > type your level and percent for the ETA.",
     {"tab": "progress"}, True),
)

# Plan 077: the digest row behind each folder step. Reason `unset` keeps the
# static step (Settings link); reason `subfolder` swaps in the in-game act with
# no link; any other row (ok, game closed, stalled watcher) means the folder is
# there and the step is done.
FOLDER_STEPS = {
    "log": {"row": "session_log", "unset": "unconfigured", "subfolder": "log_dir_missing",
            "title": "Start the game once",
            "hint": "Install folder found - launch BDO once; EW reads the Log folder it creates."},
    "screenshots": {"row": "screenshots", "unset": "unconfigured", "subfolder": "folder_missing",
                    "title": "Take one screenshot in game",
                    "hint": "Take one screenshot in game - EW reads the folder it creates."},
}


def _iso(ts):
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).replace(microsecond=0).isoformat()


def _read_config(path):
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _section(doc, key):
    v = doc.get(key)
    return v if isinstance(v, dict) else {}


def _dir_value(v):
    return Path(v) if isinstance(v, str) and v.strip() else None


def _has_family(doc):
    fam = _section(doc, "profile").get("family")
    return bool(fam) and settings.valid_family(fam)


def _is_dir(p):
    try:
        return p.is_dir()
    except OSError:
        return False


def _folder_inputs(paths):
    """The digest's folder inputs from the resolved folders (stat only), the same
    shape gamewatch.health() feeds it; used when no live digest is wired."""
    inst, docs = _dir_value(paths["install_dir"]), _dir_value(paths["documents_dir"])
    return {"session_log": {"configured": inst is not None,
                            "install_ok": inst is not None and _is_dir(inst),
                            "log_dir_ok": inst is not None and _is_dir(inst / "Log")},
            "screenshots": {"configured": docs is not None,
                            "documents_ok": docs is not None and _is_dir(docs),
                            "dir_ok": docs is not None and _is_dir(docs / "ScreenShot")}}


def _digest_rows(digest):
    rows = digest.get("rows") if isinstance(digest, dict) else None
    if not isinstance(rows, list):
        return {}
    return {r["id"]: r for r in rows if isinstance(r, dict) and isinstance(r.get("id"), str)}


def _folder_step(sid, row):
    """(done, title, hint, link) for a folder step from its digest row; None
    keeps the static open step (folder unset, or the row is unreadable)."""
    spec = FOLDER_STEPS[sid]
    reason = row.get("reason") if isinstance(row, dict) else None
    if not isinstance(row, dict) or row.get("level") not in signals.LEVELS \
            or (row.get("level") != "ok" and reason is None) or reason == spec["unset"]:
        return None
    if reason == spec["subfolder"]:
        return False, spec["title"], spec["hint"], None
    return True, None, None, None


def _has_anchor(doc):
    """Chosen, or the default kept (no key); only an invalid value is open."""
    ov = _section(doc, "overlay")
    return "anchor" not in ov or settings.valid_anchor(ov["anchor"])


def _watching(store):
    watch = store.get("market").get("watch")
    items = [w for w in watch if isinstance(w, dict)] if isinstance(watch, list) else []
    return len(items) >= MIN_WATCH or any(w.get("auto") is True for w in items)


def _ticked_daily(store):
    doc = store.get("today")
    items, ticks = doc.get("items"), doc.get("ticks")
    if not isinstance(items, list) or not isinstance(ticks, dict):
        return False
    daily = {it.get("id") for it in items if isinstance(it, dict) and it.get("kind") == "daily"}
    return any(isinstance(k, str) and k in daily and v for k, v in ticks.items())


def _has_sample(store):
    return bool(leveling._points(store.get("leveling").get("samples")))


def _dismissed(store):
    v = store.get("onboarding").get("dismissed")
    return v if isinstance(v, str) and v else None


def status(root, store, config_path=None, detected=None, digest=None):
    """Ordered steps + totals. `root` is the repo root (config/local.json under
    it) unless `config_path` names the file; `detected` is plan 065's
    {"install_dir", "documents_dir"} (explicit config wins per key); `digest` is
    the plan 073 `/api/signals` body (plan 077; rows it lacks are classified here)."""
    doc = _read_config(config_path or Path(root) / "config" / "local.json")
    rows = _digest_rows(digest)
    if any(spec["row"] not in rows for spec in FOLDER_STEPS.values()):
        paths = detect.resolve(_section(doc, "bdo"), detected)
        local = _digest_rows(signals.digest(_folder_inputs(paths), time.time()))
        rows = dict(local, **rows)
    done = {"family": _has_family(doc), "overlay": _has_anchor(doc),
            "watch": _watching(store), "daily": _ticked_daily(store),
            "leveling": _has_sample(store)}
    steps = []
    for sid, title, hint, link, optional in STEPS:
        step = {"id": sid, "title": title, "hint": hint, "link": dict(link),
                "done": done.get(sid, False), "optional": optional}
        if sid in FOLDER_STEPS:
            over = _folder_step(sid, rows.get(FOLDER_STEPS[sid]["row"]))
            if over is not None:
                step["done"] = over[0]
                if not over[0]:
                    step.update(title=over[1], hint=over[2], link=over[3])
        steps.append(step)
    n = sum(1 for s in steps if s["done"])
    when = _dismissed(store)
    complete = n == len(steps)
    required_left = any(not s["done"] and not s["optional"] for s in steps)
    return {"steps": steps, "done": n, "total": len(steps), "complete": complete,
            "dismissed": when is not None, "dismissed_at": when,
            "show": required_left and when is None}


def _check_true(arg, op):
    if arg is not True:
        raise ValueError(f"{op} takes true")


class OnboardingService:
    """GET /api/onboarding body; POST {"dismiss": true} | {"restore": true}."""

    def __init__(self, store, config_path, clock=time.time, detected=None, signals=None):
        self.store = store
        self.config_path = Path(config_path)
        self.clock = clock
        self.detected = detected  # plan 065: callable -> detect.Detector.paths()
        self.signals = signals  # plan 077: callable -> the /api/signals body
        self._lock = threading.Lock()

    def view(self):
        try:
            found = self.detected() if self.detected is not None else None
        except Exception:  # noqa: BLE001 - a detector fault reads as "nothing detected"
            found = None
        try:
            dig = self.signals() if self.signals is not None else None
        except Exception:  # noqa: BLE001 - a digest fault falls back to the folders
            dig = None
        return status(self.config_path.parent.parent, self.store, config_path=self.config_path,
                      detected=found, digest=dig)

    def dismiss(self, arg):
        _check_true(arg, "dismiss")
        with self._lock:
            self.store.put("onboarding", dict(self.store.get("onboarding"),
                                              dismissed=_iso(self.clock())))
        return self.view()

    def restore(self, arg):
        _check_true(arg, "restore")
        with self._lock:
            self.store.put("onboarding", dict(self.store.get("onboarding"), dismissed=None))
        return self.view()
