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
"""

import datetime as _dt
import json
import os
import threading
import time
from pathlib import Path

from . import detect, leveling, settings

MIN_WATCH = 3

# id, title, hint, link {tab, field?}, optional; list order is the display order.
# Optional steps happen through normal use; they never keep the card up alone.
STEPS = (
    ("family", "Set your family name",
     "Settings > profile.family turns on the profile card (BDO-REST-API).",
     {"tab": "settings", "field": "profile.family"}, False),
    ("log", "Point EW at the BDO install folder",
     "Not found in a Steam library: Settings > bdo.install_dir (its Log folder "
     "tells EW the game is running).",
     {"tab": "settings", "field": "bdo.install_dir"}, False),
    ("screenshots", "Point EW at the BDO Documents folder",
     "Not found under Documents: Settings > bdo.documents_dir (its ScreenShot folder "
     "feeds OCR).",
     {"tab": "settings", "field": "bdo.documents_dir"}, False),
    ("overlay", "Choose an overlay corner",
     "Settings > overlay.anchor picks where the overlay sits.",
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


def _detected_dir(paths, key):
    """A detected folder that still exists: detection already proved it (Steam's
    manifest / a Black Desert marker), so Log / ScreenShot may appear later."""
    p = _dir_value(paths[key])
    return paths["source"][key] == "detected" and p is not None and _is_dir(p)


def _has_log_dir(paths):
    inst = _dir_value(paths["install_dir"])
    return inst is not None and (_is_dir(inst / "Log") or _detected_dir(paths, "install_dir"))


def _has_shot_dir(paths):
    docs = _dir_value(paths["documents_dir"])
    if docs is None:
        return False
    if _detected_dir(paths, "documents_dir"):
        return True
    try:
        with os.scandir(docs / "ScreenShot"):
            return True
    except OSError:
        return False


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


def status(root, store, config_path=None, detected=None):
    """Ordered steps + totals. `root` is the repo root (config/local.json under
    it) unless `config_path` names the file; `detected` is plan 065's
    {"install_dir", "documents_dir"} (explicit config wins per key)."""
    doc = _read_config(config_path or Path(root) / "config" / "local.json")
    paths = detect.resolve(_section(doc, "bdo"), detected)
    done = {"family": _has_family(doc), "log": _has_log_dir(paths),
            "screenshots": _has_shot_dir(paths), "overlay": _has_anchor(doc),
            "watch": _watching(store), "daily": _ticked_daily(store),
            "leveling": _has_sample(store)}
    steps = [{"id": sid, "title": title, "hint": hint, "link": dict(link), "done": done[sid],
              "optional": optional}
             for sid, title, hint, link, optional in STEPS]
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

    def __init__(self, store, config_path, clock=time.time, detected=None):
        self.store = store
        self.config_path = Path(config_path)
        self.clock = clock
        self.detected = detected  # plan 065: callable -> detect.Detector.paths()
        self._lock = threading.Lock()

    def view(self):
        try:
            found = self.detected() if self.detected is not None else None
        except Exception:  # noqa: BLE001 - a detector fault reads as "nothing detected"
            found = None
        return status(self.config_path.parent.parent, self.store, config_path=self.config_path,
                      detected=found)

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
