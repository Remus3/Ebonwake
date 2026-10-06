"""First-run checklist (plan 051): ordered set-up steps, each `done` computed
from real state on every read.

Inputs: EW's own gitignored config/local.json (profile.family, overlay.anchor,
the plan 008 `bdo` folders), the plan 008 folders themselves (existence /
listing only, no file is ever opened) and the EW store (market watch, today
ticks, leveling samples). A view never carries a path. Store domain
`onboarding`: {"dismissed": "<iso>" | null}.
"""

import datetime as _dt
import json
import os
import threading
import time
from pathlib import Path

from . import leveling, settings

MIN_WATCH = 3

# id, title, hint, link {tab, field?}; list order is the display order.
STEPS = (
    ("family", "Set your family name",
     "Settings > profile.family turns on the profile card (BDO-REST-API).",
     {"tab": "settings", "field": "profile.family"}),
    ("log", "Point EW at the BDO install folder",
     "config/local.json bdo.install_dir: its Log folder tells EW the game is running.",
     {"tab": "system"}),
    ("screenshots", "Point EW at the BDO Documents folder",
     "config/local.json bdo.documents_dir: its ScreenShot folder feeds OCR.",
     {"tab": "system"}),
    ("overlay", "Choose an overlay corner",
     "Settings > overlay.anchor picks where the overlay sits.",
     {"tab": "settings", "field": "overlay.anchor"}),
    ("watch", f"Watch {MIN_WATCH} market items",
     "Market > add items to price-watch.",
     {"tab": "market"}),
    ("daily", "Tick your first daily",
     "Today > tick a daily once it is done.",
     {"tab": "today"}),
    ("leveling", "Log an XP sample",
     "Progress > type your level and percent for the ETA.",
     {"tab": "progress"}),
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


def _has_log_dir(doc):
    inst = _dir_value(_section(doc, "bdo").get("install_dir"))
    try:
        return inst is not None and (inst / "Log").is_dir()
    except OSError:
        return False


def _has_shot_dir(doc):
    docs = _dir_value(_section(doc, "bdo").get("documents_dir"))
    if docs is None:
        return False
    try:
        with os.scandir(docs / "ScreenShot"):
            return True
    except OSError:
        return False


def _has_anchor(doc):
    ov = _section(doc, "overlay")
    return "anchor" in ov and settings.valid_anchor(ov["anchor"])


def _watch_count(store):
    watch = store.get("market").get("watch")
    return sum(1 for w in watch if isinstance(w, dict)) if isinstance(watch, list) else 0


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


def status(root, store, config_path=None):
    """Ordered steps + totals. `root` is the repo root (config/local.json under
    it) unless `config_path` names the file."""
    doc = _read_config(config_path or Path(root) / "config" / "local.json")
    done = {"family": _has_family(doc), "log": _has_log_dir(doc),
            "screenshots": _has_shot_dir(doc), "overlay": _has_anchor(doc),
            "watch": _watch_count(store) >= MIN_WATCH, "daily": _ticked_daily(store),
            "leveling": _has_sample(store)}
    steps = [{"id": sid, "title": title, "hint": hint, "link": dict(link), "done": done[sid]}
             for sid, title, hint, link in STEPS]
    n = sum(1 for s in steps if s["done"])
    when = _dismissed(store)
    complete = n == len(steps)
    return {"steps": steps, "done": n, "total": len(steps), "complete": complete,
            "dismissed": when is not None, "dismissed_at": when,
            "show": not complete and when is None}


def _check_true(arg, op):
    if arg is not True:
        raise ValueError(f"{op} takes true")


class OnboardingService:
    """GET /api/onboarding body; POST {"dismiss": true} | {"restore": true}."""

    def __init__(self, store, config_path, clock=time.time):
        self.store = store
        self.config_path = Path(config_path)
        self.clock = clock
        self._lock = threading.Lock()

    def view(self):
        return status(self.config_path.parent.parent, self.store, config_path=self.config_path)

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
