"""Session-end summary (plan 046): what a game session, a day and a week earned.

Read-only over EW's own store (grind sessions + buffs, leveling samples, today
ticks, events claims); nothing is read from the game. The game-exit hook takes
the plan 008 state change only (running / logged_in / disconnected ->
not_running): it records the play window in the `summary` domain and marks
`grind.pending_stop` when a grind session is open. A session is never stopped
automatically; the operator picks stop-at-exit or keep-running on the Grind tab.
"""

import datetime as _dt
import threading
import time

from . import grind, leveling
from .today import _iso, _parse_iso, last_daily_reset, last_weekly_reset

GAME_UP = ("running", "logged_in", "disconnected")
GAME_DOWN = "not_running"
WINDOWS = ("session", "day", "week")


def _in(when, since, until):
    return when is not None and since <= when < until


def _grind_part(doc, since, until):
    """Logged sessions STARTED in [since, until), valued as GET /api/grind does
    (plan 039 loot value when present, else the typed silver)."""
    sessions = [c for c in (grind._clean_session(s) for s in _list(doc.get("sessions")))
                if c is not None and _in(_parse_iso(c["started"]), since, until)]
    names = {s["id"]: s["name"] for s in
             (grind._clean_named(x) for x in _list(doc.get("spots"))) if s is not None}
    by, minutes, silver = {}, 0, 0
    for s in sessions:
        val = s["loot_value"]["total"] if s["loot_value"] is not None else s["silver"]
        row = by.setdefault(s["spot"], {"spot": s["spot"], "name": names.get(s["spot"], s["spot"]),
                                        "sessions": 0, "minutes": 0, "silver": 0})
        row["sessions"] += 1
        row["minutes"] += s["minutes"]
        row["silver"] += val
        minutes += s["minutes"]
        silver += val
    spots = sorted(by.values(), key=lambda r: (-r["silver"], r["spot"]))
    for r in spots:
        r["silver_per_h"] = grind.silver_per_hour(r["silver"], r["minutes"])
    return {"sessions": len(sessions), "minutes": minutes, "silver": silver,
            "silver_per_h": grind.silver_per_hour(silver, minutes), "spots": spots}


def _xp_part(doc, since, until):
    """Level-percent gained: newest typed sample at or before `until` minus the
    newest at or before `since` (else the first inside the window). None with
    fewer than two usable points."""
    pts = [p for p in leveling._points(_list(doc.get("samples"))) if p[0] <= until]
    before = [p for p in pts if p[0] <= since]
    inside = [p for p in pts if p[0] > since]
    start = before[-1] if before else (inside[0] if inside else None)
    end = pts[-1] if pts else None
    if start is None or end is None or end[0] <= start[0]:
        return None
    gained = end[1] - start[1]
    return {"gained_pct": round(gained, 3) if gained != int(gained) else int(gained),
            "from": _lvl(start[1]), "to": _lvl(end[1])}


def _lvl(cum):
    level = int(cum // 100)
    pct = cum - level * 100
    return {"level": level, "pct": round(pct, 3) if pct != int(pct) else int(pct)}


def _buffs_part(doc, since, until):
    """Buffs whose timer ran inside the window: ends after `since` and armed
    before `until`. A timer armed before plan 046 has no `armed` stamp and
    counts on its end alone (as-built deviation 1)."""
    out = []
    for b in (grind._clean_named(x) for x in _list(doc.get("buffs"))):
        ends = _parse_iso(b.get("ends")) if b is not None else None
        armed = _parse_iso(b.get("armed")) if b is not None else None
        if ends is not None and ends > since and (armed is None or armed < until):
            out.append({"name": b["name"], "ends": _iso(ends)})
    return sorted(out, key=lambda r: r["ends"])


def _dailies_part(doc, since, until):
    ticks = doc.get("ticks") if isinstance(doc.get("ticks"), dict) else {}
    titles = {it.get("id"): it for it in _list(doc.get("items")) if isinstance(it, dict)}
    out = []
    for iid, stamp in ticks.items():
        when = _parse_iso(stamp)
        it = titles.get(iid)
        if it is not None and isinstance(it.get("title"), str) and _in(when, since, until):
            out.append({"id": iid, "title": it["title"], "kind": it.get("kind"),
                        "ticked_at": _iso(when)})
    return sorted(out, key=lambda r: r["ticked_at"])


def _events_part(doc, since, until):
    out = []
    for it in _list(doc.get("items")):
        if not (isinstance(it, dict) and it.get("done") is True
                and isinstance(it.get("title"), str)):
            continue
        when = _parse_iso(it.get("done_at"))
        if _in(when, since, until):
            out.append({"id": it.get("id"), "kind": it.get("kind"), "title": it["title"],
                        "done_at": _iso(when)})
    return sorted(out, key=lambda r: r["done_at"])


def _list(v):
    return v if isinstance(v, list) else []


def session_summary(store, since, until):
    """Summary of [since, until) (UTC datetimes) from the store."""
    if until < since:
        since, until = until, since
    g = store.get("grind")
    return {"since": _iso(since), "until": _iso(until),
            "grind": _grind_part(g, since, until),
            "xp": _xp_part(store.get("leveling"), since, until),
            "buffs": _buffs_part(g, since, until),
            "dailies": _dailies_part(store.get("today"), since, until),
            "events": _events_part(store.get("events"), since, until)}


def is_empty(summary):
    return (summary["grind"]["sessions"] == 0 and summary["xp"] is None
            and not summary["buffs"] and not summary["dailies"] and not summary["events"])


def day_summary(store, now):
    """Nightly recap: since the last NA daily reset (00:00 UTC)."""
    return session_summary(store, last_daily_reset(now), now)


def week_summary(store, now):
    """Weekly recap: since the last NA weekly reset (Thursday 00:00 UTC)."""
    return session_summary(store, last_weekly_reset(now), now)


class SummaryService:
    """Store domain `summary`: {"last_game": {since, until}|null, "updated"}.
    `on_game(prev, new, at)` is the plan 008 change hook; `grind` is the
    GrindService whose `pending_stop` it marks."""

    def __init__(self, store, grind_service, clock=time.time):
        self.store = store
        self.grind = grind_service
        self.clock = clock
        self._lock = threading.Lock()
        self._up_since = None  # first time the game was seen up this server run

    def _now(self):
        return _dt.datetime.fromtimestamp(self.clock(), _dt.timezone.utc)

    def on_game(self, prev, new, at):
        """Game state change at `at` (epoch seconds). Only up -> not_running ends
        a play window (and marks a pending stop); down -> up starts one and
        clears a pending stop left by the previous exit."""
        when = _dt.datetime.fromtimestamp(at, _dt.timezone.utc).replace(microsecond=0)
        with self._lock:
            if new in GAME_UP:
                relaunch = prev not in GAME_UP
                if self._up_since is None:
                    self._up_since = when
            elif prev not in GAME_UP or new != GAME_DOWN:
                self._up_since = None
                return
            else:
                since, self._up_since = self._up_since or when, None
                self.store.put("summary", {"last_game": {"since": _iso(since),
                                                         "until": _iso(when)},
                                           "updated": _iso(self._now())})
        if new in GAME_UP:
            if relaunch:  # back in game: the old exit time no longer ends the session
                self.grind.clear_pending_stop()
            return
        self.grind.mark_pending_stop(_iso(when))

    def record_play(self, since, until):
        """Plan 062: an auto play session closed; its window (UTC datetimes) is
        the last game session the summary shows."""
        with self._lock:
            self.store.put("summary", {"last_game": {"since": _iso(since), "until": _iso(until)},
                                       "updated": _iso(self._now())})

    def last_game(self):
        lg = self.store.get("summary").get("last_game")
        if not isinstance(lg, dict):
            return None
        since, until = _parse_iso(lg.get("since")), _parse_iso(lg.get("until"))
        if since is None or until is None:
            return None
        return since, until

    def view(self):
        """GET /api/summary: the last game session, today so far and this week."""
        now = self._now()
        lg = self.last_game()
        session = session_summary(self.store, *lg) if lg else None
        if session is not None:
            session["empty"] = is_empty(session)
        day, week = day_summary(self.store, now), week_summary(self.store, now)
        day["empty"], week["empty"] = is_empty(day), is_empty(week)
        return {"now": _iso(now), "session": session, "day": day, "week": week,
                "pending_stop": self.grind.view()["pending_stop"]}
