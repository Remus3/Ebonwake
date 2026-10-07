"""Auto play-session (plan 062): login opens, exit closes the grind log.

A GameWatch listener (like `DiceClock`) over the plan 008 state only; nothing
is read from or sent to the game. `logged_in` opens a play session and, when
no grind session is running, a plan 005 grind session marked `auto` on the
last used spot (else "unspecified"). `not_running` / `disconnected` held for
`grace_s` closes both at the moment the game last left logged_in, and records
the play window for the plan 046 summary; no pending-stop prompt is raised for
an auto session. A relaunch or reconnect inside the grace continues the same
session. An open session survives an EW restart only when the first poll still
reads logged_in; any other first state closes it where EW last saw it logged
in. A manual Stop closes the play session and, while the game is up, suppresses
auto-open until the game is next seen not_running.

Store domain `playsession`: {"open": {id, start, spot, auto, grind_started,
seen, left_at, down_since}|null, "last": {id, start, end, spot, auto}|null,
"suppressed": bool, "next_id": int, "history": [{id, start, end}] (plan 081:
closed sessions, oldest first, at most MAX_HISTORY)}. Times are ISO UTC.
"""

import datetime as _dt
import re
import threading
import time

from .gamewatch import _iso, _parse

GRACE_S = 120
GRACE_RANGE = (60, 600)
SEEN_S = 60  # tick() refreshes `seen` at most once a minute while logged in
DOWN = ("not_running", "disconnected")
ID_RE = re.compile(r"^p[0-9]{1,9}$")
MAX_HISTORY = 100


def grace_of(v):
    ok = (isinstance(v, int) and not isinstance(v, bool)
          and GRACE_RANGE[0] <= v <= GRACE_RANGE[1])
    return v if ok else GRACE_S


def _ts(v):
    return _parse(v) if isinstance(v, str) else None


def _clean_open(o):
    if not (isinstance(o, dict) and isinstance(o.get("id"), str) and ID_RE.match(o["id"])):
        return None
    start = _ts(o.get("start"))
    if start is None:
        return None
    return {"id": o["id"], "start": start,
            "spot": o["spot"] if isinstance(o.get("spot"), str) else None,
            "auto": o.get("auto") is True,
            "grind_started": o["grind_started"] if isinstance(o.get("grind_started"), str)
            else None,
            "seen": _ts(o.get("seen")), "left_at": _ts(o.get("left_at")),
            "down_since": _ts(o.get("down_since"))}


def _clean_last(v):
    if not (isinstance(v, dict) and isinstance(v.get("id"), str)
            and _ts(v.get("start")) is not None and _ts(v.get("end")) is not None):
        return None
    return {"id": v["id"], "start": v["start"], "end": v["end"],
            "spot": v["spot"] if isinstance(v.get("spot"), str) else None,
            "auto": v.get("auto") is True}


class PlaySession:
    """`config()` -> (auto_session, grace_s) from the plan 030 settings;
    `on_change()` is called after an open or close (the app bumps the grind
    domain so dashboards re-GET)."""

    def __init__(self, store, grind_service, summary_service, config=None,
                 clock=time.time, on_change=None):
        self.store = store
        self.grind = grind_service
        self.summary = summary_service
        self.config = config or (lambda: (True, GRACE_S))
        self.clock = clock
        self.on_change = on_change
        self._lock = threading.Lock()
        self._state = None  # last plan 008 state seen this EW run
        self.seq = 0
        self._event = None

    # -- store -----------------------------------------------------------------

    def _load(self):
        doc = self.store.get("playsession")
        nxt = doc.get("next_id")
        nxt = nxt if isinstance(nxt, int) and not isinstance(nxt, bool) and nxt >= 1 else 1
        hist = []
        for h in doc.get("history") if isinstance(doc.get("history"), list) else []:
            if isinstance(h, dict) and isinstance(h.get("id"), str) \
                    and _ts(h.get("start")) is not None and _ts(h.get("end")) is not None:
                hist.append({"id": h["id"], "start": h["start"], "end": h["end"]})
        return {"open": _clean_open(doc.get("open")), "last": _clean_last(doc.get("last")),
                "suppressed": doc.get("suppressed") is True, "next_id": nxt,
                "history": hist[-MAX_HISTORY:]}

    def _save(self, d):
        o = d["open"]
        if o is not None:
            o = {k: (_iso(v) if k in ("start", "seen", "left_at", "down_since")
                     and v is not None else v) for k, v in o.items()}
        self.store.put("playsession", {"open": o, "last": d["last"],
                                       "suppressed": d["suppressed"],
                                       "next_id": d["next_id"],
                                       "history": d["history"][-MAX_HISTORY:]})

    def _conf(self):
        try:
            auto, grace = self.config()
        except Exception:  # noqa: BLE001 - a bad settings read falls back to defaults
            auto, grace = True, GRACE_S
        return auto is not False, grace_of(grace)

    # -- transitions -------------------------------------------------------------

    def _open(self, d, at):
        pid = f"p{d['next_id']}"
        d["next_id"] += 1
        act = self.grind.auto_start(_iso(at))
        d["open"] = {"id": pid, "start": at, "spot": act["spot"] if act else None,
                     "auto": True, "grind_started": act["started"] if act else None,
                     "seen": at, "left_at": None, "down_since": None}
        return {"state": "open", "id": pid}

    def _close(self, d, end):
        o = d["open"]
        end = max(end, o["start"])
        if o["grind_started"] is not None:
            self.grind.auto_stop(o["grind_started"], _iso(end))
        since = _dt.datetime.fromtimestamp(o["start"], _dt.timezone.utc).replace(microsecond=0)
        until = _dt.datetime.fromtimestamp(end, _dt.timezone.utc).replace(microsecond=0)
        self.summary.record_play(since, until)
        d["last"] = {"id": o["id"], "start": _iso(o["start"]), "end": _iso(end),
                     "spot": o["spot"], "auto": o["auto"]}
        d["history"].append({"id": o["id"], "start": _iso(o["start"]), "end": _iso(end)})
        d["open"] = None
        return {"state": "closed", "id": o["id"]}

    @staticmethod
    def _end(o):
        """The last moment the session was seen logged in."""
        for k in ("left_at", "seen"):
            if o[k] is not None:
                return o[k]
        return o["start"]

    def _emit(self, ev):
        if ev is None:
            return
        self._event = ev
        self.seq += 1
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception:  # noqa: BLE001 - a hook never breaks the poll
                pass

    def on_game(self, prev, new, at):
        """Plan 008 change hook (GameWatch.listeners)."""
        ev = None
        with self._lock:
            d = self._load()
            o = d["open"]
            first = self._state is None
            self._state = new
            if o is not None and first and new != "logged_in":
                # Orphan from a previous EW run: close where EW last saw it.
                ev = self._close(d, min(self._end(o), at))
            elif o is not None:
                if new == "logged_in":
                    o.update(seen=at, left_at=None, down_since=None)
                else:
                    if prev == "logged_in":
                        o["left_at"] = at
                    if new in DOWN and o["down_since"] is None:
                        o["down_since"] = at
            elif new == "logged_in" and not d["suppressed"] and self._conf()[0]:
                ev = self._open(d, at)
            if new == "not_running":
                d["suppressed"] = False
            self._save(d)
        self._emit(ev)

    def tick(self, state, now):
        """Every poll (GameWatch.pollers): refresh `seen`, close past the grace."""
        ev = None
        with self._lock:
            d = self._load()
            o = d["open"]
            if o is None:
                return
            if state == "logged_in":
                if o["seen"] is None or now - o["seen"] >= SEEN_S:
                    o["seen"] = now
                    self._save(d)
                return
            if o["down_since"] is None or now - o["down_since"] < self._conf()[1]:
                return
            ev = self._close(d, self._end(o))
            self._save(d)
        self._emit(ev)

    def manual_stop(self):
        """The operator stopped the grind session by hand: close the play session;
        while the game is up, no auto-open until it is next seen not_running."""
        ev = None
        with self._lock:
            d = self._load()
            o = d["open"]
            if o is not None:
                o["grind_started"] = None  # already stopped by the operator
                end = o["left_at"] if o["left_at"] is not None else self.clock()
                ev = self._close(d, end)
            if self._state not in (None, "not_running", "unconfigured"):
                d["suppressed"] = True
            if o is not None or d["suppressed"]:
                self._save(d)
        self._emit(ev)

    # -- reads -------------------------------------------------------------------

    def view(self):
        with self._lock:
            d = self._load()
        o = d["open"]
        if o is not None:
            o = {"id": o["id"], "start": _iso(o["start"]), "spot": o["spot"], "auto": o["auto"]}
        return {"open": o, "last": d["last"], "suppressed": d["suppressed"]}

    def sessions(self):
        """Plan 081: closed play sessions [{id, start, end}], oldest first."""
        with self._lock:
            return [dict(h) for h in self._load()["history"]]

    def windows(self):
        """Plan 083: logged-in windows [{start, end|None}], oldest first; the open
        session ends where the game last left logged_in (None while still in)."""
        with self._lock:
            d = self._load()
        out = [{"start": h["start"], "end": h["end"]} for h in d["history"]]
        o = d["open"]
        if o is not None:
            out.append({"start": _iso(o["start"]),
                        "end": _iso(o["left_at"]) if o["left_at"] is not None else None})
        return out

    def state(self):
        """GET /api/grind `session.play`: {state: open, id, start, spot} or None."""
        o = self.view()["open"]
        return dict(o, state="open") if o is not None else None

    def event(self):
        """Last SSE `play_session` payload {state: open|closed, id}, or None."""
        return dict(self._event) if self._event else None
