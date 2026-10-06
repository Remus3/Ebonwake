"""Overlay context (plan 067): which widgets the overlay shows, chosen by state.

`active_contexts` is pure (clock injected through `now`): it reads one signal
dict built from existing sources - the plan 008 game state and its last
change, the newest ScreenShot-folder file, the plan 059 / 064 maintenance
window, the plan 021 daily reset, the plan 031 next boss spawn and the plan 011
/ 064 Hot Time windows. Several contexts can hold; the tracked rules file
`data/overlay_contexts.json` orders them and lists each one's widgets.
`pick_widgets` applies the plan 030 per-widget mode (auto | pin | block) on
top: pins first, rule widgets fill to `max_widgets`, blocks never show.

Idle is "logged in, no screenshot and no game state change for idle_min" -
there is no input hook of any kind. Nothing here reads or touches the game.
"""

import datetime as _dt
import json
import threading
import time
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data" / "overlay_contexts.json"
CONTEXTS = ("closed", "maint_soon", "boss_soon", "reset_soon", "hot_time", "idle", "in_game")
# Lead times in minutes (plan 067 item 1).
MAINT_SOON_MIN = 60
RESET_SOON_MIN = 30
BOSS_SOON_MIN = 15
IDLE_MIN = 20
# The plan 030 overlay widgets plus the maintenance countdown line.
WIDGETS = ("grindSession", "grindBuff", "eventsSoon", "leveling", "season", "marketTicker",
           "worldBoss", "dice", "whatNow")
EXTRA_WIDGETS = ("maintenance",)
MODES = ("auto", "pin", "block")
MAX_WIDGETS = 4
CACHE_S = 5.0  # the SSE loop re-derives at most this often (settings reads hit the disk)
_UTC = _dt.timezone.utc


def _ts(v):
    """Epoch seconds from a number, an aware datetime or an ISO string; else None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, _dt.datetime):
        return v.timestamp() if v.tzinfo is not None else None
    if isinstance(v, str):
        try:
            d = _dt.datetime.fromisoformat(v)
        except ValueError:
            return None
        return d.timestamp() if d.tzinfo is not None else None
    return None


def _iso(ts):
    if ts is None:
        return None
    return _dt.datetime.fromtimestamp(ts, _UTC).replace(microsecond=0).isoformat()


def _within(at, now, minutes):
    return at is not None and 0 <= at - now <= minutes * 60


def active_contexts(sig, now, idle_min=IDLE_MIN):
    """Every context that holds for signal dict `sig` at `now` (epoch s), in
    CONTEXTS order. `sig`: {game, last_activity, maint_start, maint_end,
    reset_at, boss_at, hot} (times in epoch s or None).

    closed = the game is not running; it stands alone (the overlay hides).
    in_game = any other game state, including `unconfigured` (no watcher: the
    clock-driven contexts still apply)."""
    now = float(now)
    state = sig.get("game")
    if state == "not_running":
        return ["closed"]
    out = []
    ms, me = _ts(sig.get("maint_start")), _ts(sig.get("maint_end"))
    if ms is not None and ms - now <= MAINT_SOON_MIN * 60 and (me is None or now < me):
        out.append("maint_soon")
    if _within(_ts(sig.get("boss_at")), now, BOSS_SOON_MIN):
        out.append("boss_soon")
    if _within(_ts(sig.get("reset_at")), now, RESET_SOON_MIN):
        out.append("reset_soon")
    if sig.get("hot") is True:
        out.append("hot_time")
    last = _ts(sig.get("last_activity"))
    if state == "logged_in" and last is not None and now - last >= idle_min * 60:
        out.append("idle")
    out.append("in_game")
    return out


def load_rules(path=DATA):
    """Tracked rules -> {max_widgets, priority, contexts: {name: {hidden, widgets}}}.
    Unknown widget names and contexts are dropped; a bad file falls back to no
    widgets (the base rows still show), never to an exception."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        doc = {}
    doc = doc if isinstance(doc, dict) else {}
    known = set(WIDGETS) | set(EXTRA_WIDGETS)
    raw_pri = doc.get("priority") if isinstance(doc.get("priority"), list) else []
    pri = [c for c in dict.fromkeys(raw_pri) if c in CONTEXTS]
    pri += [c for c in CONTEXTS if c not in pri]
    raw = doc.get("contexts") if isinstance(doc.get("contexts"), dict) else {}
    ctx = {}
    for c in CONTEXTS:
        r = raw.get(c) if isinstance(raw.get(c), dict) else {}
        ws = r.get("widgets") if isinstance(r.get("widgets"), list) else []
        ctx[c] = {"hidden": r.get("hidden") is True,
                  "widgets": [w for w in dict.fromkeys(x for x in ws if isinstance(x, str))
                              if w in known]}
    mx = doc.get("max_widgets")
    mx = mx if isinstance(mx, int) and not isinstance(mx, bool) and 1 <= mx <= 9 else MAX_WIDGETS
    return {"max_widgets": mx, "priority": pri, "contexts": ctx}


def pick_widgets(rules, contexts, modes=None):
    """(widgets, hidden) for the active `contexts` under `rules` and per-widget
    `modes` {widget: auto|pin|block}. Pins always show (never cut by the cap)
    unless the primary context hides the overlay; a block always wins."""
    modes = modes or {}
    order = [c for c in rules["priority"] if c in contexts]
    if not order:
        return [], False
    if rules["contexts"][order[0]]["hidden"]:
        return [], True
    pins = [w for w in WIDGETS if modes.get(w) == "pin"]
    out = list(pins)
    for c in order:
        for w in rules["contexts"][c]["widgets"]:
            if len(out) >= rules["max_widgets"]:
                break
            if w not in out and modes.get(w) != "block":
                out.append(w)
    return out, False


def derive(sig, now, prefs, rules):
    """Overlay context payload. `prefs`: {auto, idle_min, modes, manual} -
    `manual` is the plan 030 widget booleans used when auto is off."""
    active = active_contexts(sig, now, prefs.get("idle_min", IDLE_MIN))
    order = [c for c in rules["priority"] if c in active]
    auto = prefs.get("auto", True) is not False
    if auto:
        widgets, hidden = pick_widgets(rules, active, prefs.get("modes"))
    else:
        manual = prefs.get("manual") or {}
        widgets, hidden = [w for w in WIDGETS if manual.get(w) is True], False
    ms = _ts(sig.get("maint_start"))
    return {"context": order[0] if order else "in_game", "active": order,
            "widgets": widgets, "hidden": hidden, "auto": auto,
            "maint_at": _iso(ms) if "maint_soon" in active else None}


def signature(payload):
    """The part of a payload whose change is pushed (no timestamps)."""
    return json.dumps([payload.get(k) for k in
                       ("context", "active", "widgets", "hidden", "auto", "maint_at")])


def prefs_from_settings(s):
    """plan 030 settings view dict -> derive() prefs."""
    return {"auto": s.get("overlay.auto", True) is not False,
            "idle_min": s.get("overlay.idle_min", IDLE_MIN),
            "modes": {w: s.get(f"overlay.mode.{w}", "auto") for w in WIDGETS},
            "manual": {w: s.get(f"overlay.widgets.{w}") is True for w in WIDGETS}}


def last_activity(game_view):
    """Newest of the game state change and the newest screenshot (epoch s)."""
    if not isinstance(game_view, dict):
        return None
    ts = [_ts(game_view.get("since"))]
    for s in game_view.get("screenshots") or []:
        if isinstance(s, dict):
            ts.append(_ts(s.get("mtime")))
    ts = [t for t in ts if t is not None]
    return max(ts) if ts else None


class ContextService:
    """Builds the signals from injected providers and caches the payload for
    CACHE_S so the 0.25 s SSE loop never re-reads settings / store each slice.
    Every provider failure degrades to "no signal", never to an exception."""

    def __init__(self, clock=time.time, game=None, settings=None, boss_at=None,
                 maint_window=None, reset_at=None, hot=None, rules=None):
        self.clock = clock
        self.game = game or (lambda: {})
        self.settings = settings or (lambda: {})
        self.boss_at = boss_at or (lambda now: None)
        self.maint_window = maint_window or (lambda now: None)
        self.reset_at = reset_at or (lambda now: None)
        self.hot = hot or (lambda now: False)
        self.rules = rules if rules is not None else load_rules()
        self._lock = threading.Lock()
        self._cache = None  # (at, payload)

    @staticmethod
    def _safe(fn, *a, default=None):
        try:
            return fn(*a)
        except Exception:  # noqa: BLE001 - a bad source never breaks the overlay
            return default

    def signals(self, now):
        g = self._safe(self.game, default={}) or {}
        when = _dt.datetime.fromtimestamp(now, _UTC)
        win = self._safe(self.maint_window, when)
        ms, me = (win if isinstance(win, tuple) and len(win) == 2 else (None, None))
        return {"game": g.get("state") if isinstance(g, dict) else None,
                "last_activity": last_activity(g),
                "maint_start": _ts(ms), "maint_end": _ts(me),
                "reset_at": _ts(self._safe(self.reset_at, when)),
                "boss_at": _ts(self._safe(self.boss_at, when)),
                "hot": self._safe(self.hot, when, default=False) is True}

    def invalidate(self):
        with self._lock:
            self._cache = None

    def view(self, fresh=False):
        now = float(self.clock())
        with self._lock:
            c = self._cache
            if not fresh and c is not None and 0 <= now - c[0] < CACHE_S:
                return dict(c[1])
        prefs = prefs_from_settings(self._safe(self.settings, default={}) or {})
        out = derive(self.signals(now), now, prefs, self.rules)
        out["updated"] = _iso(now)
        with self._lock:
            self._cache = (now, out)
        return dict(out)
