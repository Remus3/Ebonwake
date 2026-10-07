"""Prompt hygiene (plan 070): stale-prompt expiry, dedupe, game-closed quiet,
the 15 / 5 / 1 min alert ladder.

Every prompt-like row (coupon / event suggestion, pending stop, OCR review
row, onboarding step) is registered once by key with {kind, created,
expires}; an expired one vanishes from its GET payload with no click (the
stored row is never touched). TTLs per kind live in tracked
`data/prompt_ttl.json`. The pure parts (ladder, gate, ledger) mirror the
dashboard notify engine in app/shared/ewcore.js. Local state and the clock
only: nothing here reads or touches the game.
"""

import datetime as _dt
import json
import threading
import time
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data" / "prompt_ttl.json"
SESSION = "session"
DOMAIN = "prompts"
MAX_ITEMS = 2000
LADDER_MAX = 6
LADDER_RANGE = (1, 120)
DEFAULT_LADDER = (15, 5, 1)
_UTC = _dt.timezone.utc


# -- config ------------------------------------------------------------------------

def _ttl_ok(v):
    return v is None or v == SESSION or (isinstance(v, int) and not isinstance(v, bool) and v > 0)


def valid_ladder(v):
    """Minutes-before steps: 1-6 ints in 1..120, strictly descending."""
    if not isinstance(v, (list, tuple)) or not 1 <= len(v) <= LADDER_MAX:
        return False
    if not all(isinstance(m, int) and not isinstance(m, bool)
               and LADDER_RANGE[0] <= m <= LADDER_RANGE[1] for m in v):
        return False
    return all(a > b for a, b in zip(v, v[1:]))


def parse_ladder(v):
    """"15,5,1" (a config incident switch, plan 080) -> (15, 5, 1); None when invalid."""
    if not isinstance(v, str) or not v or len(v) > 32:
        return None
    parts = v.split(",")
    if not all(p.strip().isdigit() and p.strip().isascii() for p in parts):
        return None
    out = tuple(int(p) for p in parts)
    return out if valid_ladder(out) else None


def load_config(path=DATA):
    """{ttl_s: {kind: int|None|"session"}, quiet: {states, allow}, ladder_min}.
    A bad file raises ValueError (it is tracked; a test catches a bad edit)."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    ttl = doc.get("ttl_s")
    if not isinstance(ttl, dict) or not ttl or not all(
            isinstance(k, str) and _ttl_ok(v) for k, v in ttl.items()):
        raise ValueError("prompt_ttl.json: ttl_s must map kind -> seconds | null | \"session\"")
    q = doc.get("quiet")
    if not isinstance(q, dict) or not all(
            isinstance(q.get(k), list) and all(isinstance(x, str) for x in q[k])
            for k in ("states", "allow")):
        raise ValueError("prompt_ttl.json: quiet must be {states: [..], allow: [..]}")
    ladder = doc.get("ladder_min")
    if not valid_ladder(ladder):
        raise ValueError("prompt_ttl.json: ladder_min must be descending minutes")
    return {"ttl_s": dict(ttl), "quiet": {"states": list(q["states"]), "allow": list(q["allow"])},
            "ladder_min": list(ladder)}


# -- pure parts ----------------------------------------------------------------------

def ladder_step(left_s, steps=DEFAULT_LADDER):
    """The ladder step now due for an event `left_s` seconds away: the smallest
    step whose mark has passed, or None (too far, or already happened)."""
    if not isinstance(left_s, (int, float)) or left_s <= 0:
        return None
    due = [m for m in steps if left_s <= m * 60]
    return min(due) if due else None


def ladder_hits(timers, now, steps=DEFAULT_LADDER):
    """timers [{key, at (epoch s), title}] -> one hit per timer for its due
    step, keyed `<key>:<step>` so a ledger lets each step fire once."""
    out = []
    for t in timers or ():
        if not isinstance(t, dict) or not isinstance(t.get("key"), str):
            continue
        at = t.get("at")
        step = ladder_step(at - now, steps) if isinstance(at, (int, float)) else None
        if step is None:
            continue
        out.append({"key": f"{t['key']}:{step}", "rule": t.get("rule"), "ladder": True,
                    "title": f"{t.get('title') or t['key']} in {step}m", "step": step})
    return out


def is_quiet(game_state, cfg, enabled=True):
    return enabled is not False and game_state in cfg["quiet"]["states"]


def gate(hits, game_state, cfg, enabled=True):
    """(fire, drop) under the game-closed quiet. While quiet only `allow`
    rules fire; a held ladder hit is stale (its mark passed while the game was
    closed) and goes to `drop`, which the caller marks seen so it never fires
    late; any other held hit is left out of both and re-evaluated next round,
    so it fires at the next logged_in if its rule still produces it. Plan 074:
    a hit marked `closed` (the maintLoss T-24 h / T-1 h toasts) fires anyway."""
    if not is_quiet(game_state, cfg, enabled):
        return list(hits), []
    allow = set(cfg["quiet"]["allow"])
    fire, drop = [], []
    for h in hits:
        if h.get("rule") in allow or h.get("closed") is True:
            fire.append(h)
        elif h.get("ladder"):
            drop.append(h)
    return fire, drop


class Ledger:
    """Each key once (in memory)."""

    def __init__(self):
        self.seen = set()

    def take(self, hits):
        out = []
        for h in hits:
            if h["key"] not in self.seen:
                self.seen.add(h["key"])
                out.append(h)
        return out


def _ts(v):
    """ISO string / epoch number / None -> epoch s or None."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if isinstance(v, str) and v:
        try:
            d = _dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
        return (d if d.tzinfo else d.replace(tzinfo=_UTC)).timestamp()
    return None


def _iso(ts):
    return _dt.datetime.fromtimestamp(ts, _UTC).replace(microsecond=0).isoformat()


# -- registry --------------------------------------------------------------------------

class PromptRegistry:
    """Store domain `prompts`: {items: {key: {kind, created, expires, session}},
    session: n}. First registration wins (dedupe by key); `session` counts
    logged_in starts so a "session" TTL lapses when the next one opens."""

    def __init__(self, store, clock=time.time, config=None, settings=None):
        self.store = store
        self.clock = clock
        self.cfg = config or load_config()
        self.settings = settings or (lambda: {})
        self._lock = threading.Lock()

    def _doc(self):
        d = self.store.get(DOMAIN)
        items = d.get("items") if isinstance(d.get("items"), dict) else {}
        s = d.get("session")
        return {"items": {k: v for k, v in items.items() if isinstance(v, dict)},
                "session": s if isinstance(s, int) and not isinstance(s, bool) else 0}

    def _record(self, key, kind, created, session):
        ttl = self.cfg["ttl_s"].get(kind)
        exp = created + ttl if isinstance(ttl, int) else None
        return {"kind": kind, "created": created, "expires": exp,
                "session": session if ttl == SESSION else None}

    @staticmethod
    def _alive(rec, now, session):
        if rec.get("session") is not None and session > rec["session"]:
            return False
        exp = rec.get("expires")
        return not isinstance(exp, (int, float)) or now < exp

    def register(self, key, kind, created=None):
        """The record for `key` (an existing one wins, so a repeat is a no-op)."""
        with self._lock:
            d = self._doc()
            rec = d["items"].get(key)
            if rec is None:
                now = float(self.clock())
                rec = self._record(key, kind, now if created is None else created, d["session"])
                d["items"][key] = rec
                self._save(d)
            return dict(rec)

    def alive(self, key):
        with self._lock:
            d = self._doc()
            rec = d["items"].get(key)
            return rec is None or self._alive(rec, float(self.clock()), d["session"])

    def keep(self, kind, rows, keyfn, createdfn=None):
        """`rows` minus the expired ones. New keys are registered (created = the
        row's own time when `createdfn` gives one, else now); records of this
        kind whose row is gone are pruned. Duplicate keys show once."""
        now = float(self.clock())
        out, seen = [], set()
        with self._lock:
            d = self._doc()
            items, changed = d["items"], False
            for r in rows or ():
                try:
                    key = keyfn(r)
                except Exception:  # noqa: BLE001 - an odd row is shown, never dropped
                    out.append(r)
                    continue
                if not isinstance(key, str) or not key:
                    out.append(r)
                    continue
                key = f"{kind}:{key}"
                if key in seen:
                    continue
                seen.add(key)
                rec = items.get(key)
                if rec is None:
                    c = _ts(createdfn(r)) if createdfn else None
                    rec = self._record(key, kind, now if c is None or c > now else c, d["session"])
                    items[key] = rec
                    changed = True
                if self._alive(rec, now, d["session"]):
                    out.append(r)
            for k in [k for k, v in items.items() if v.get("kind") == kind and k not in seen]:
                del items[k]
                changed = True
            if changed:
                self._save(d)
        return out

    def _save(self, d):
        items = d["items"]
        if len(items) > MAX_ITEMS:  # oldest first out
            keep = sorted(items, key=lambda k: items[k].get("created") or 0)[-MAX_ITEMS:]
            d["items"] = {k: items[k] for k in keep}
        self.store.put(DOMAIN, d)

    def on_game(self, prev, new, at):
        """gamewatch listener: a new logged_in opens the next session."""
        if new == "logged_in" and prev != "logged_in":
            with self._lock:
                d = self._doc()
                d["session"] += 1
                self._save(d)

    # -- view ------------------------------------------------------------------------

    def notify_prefs(self, now=None):
        """(quiet_closed, ladder steps, muted_until ISO|None) from the effective
        settings (plan 080: fixed values unless a plan 079 entry holds), defaults
        on anything odd."""
        try:
            s = self.settings() or {}
        except Exception:  # noqa: BLE001 - an unreadable setting = the default
            s = {}
        quiet = s.get("notify.quiet_closed", True) is not False
        ladder = parse_ladder(s.get("notify.ladder_min")) or tuple(self.cfg["ladder_min"])
        mute = s.get("notify.mute_until")
        try:
            t = _dt.datetime.fromisoformat(mute) if isinstance(mute, str) and mute else None
        except ValueError:
            t = None
        now = float(self.clock()) if now is None else now
        muted = (_iso(t.timestamp()) if t is not None and t.tzinfo is not None
                 and t.timestamp() > now else None)
        return quiet, list(ladder), muted

    def view(self, game_state=None, timers=None, maint_loss=None):
        """GET /api/prompts: the quiet gate + ladder the notify engine applies,
        the live prompts, `timers` (reset / maintenance) for the ladder and
        plan 074 `maint_loss` [{key, at, title, text}] (unacked loss warnings)."""
        now = float(self.clock())
        quiet_on, ladder, muted = self.notify_prefs(now)
        with self._lock:
            d = self._doc()
        live = [{"key": k, "kind": v.get("kind"), "created": _iso(v["created"]),
                 "expires": _iso(v["expires"]) if isinstance(v.get("expires"), (int, float))
                 else None}
                for k, v in sorted(d["items"].items())
                if isinstance(v.get("created"), (int, float)) and self._alive(v, now, d["session"])]
        return {"quiet_closed": quiet_on, "quiet": is_quiet(game_state, self.cfg, quiet_on),
                "quiet_states": list(self.cfg["quiet"]["states"]),
                "allow_closed": list(self.cfg["quiet"]["allow"]), "ladder_min": ladder,
                "muted_until": muted,
                "ttl_s": dict(self.cfg["ttl_s"]), "session": d["session"],
                "prompts": live, "timers": [dict(t, at=_iso(t["at"])) for t in (timers or [])],
                "maint_loss": [dict(t, at=_iso(t["at"])) for t in (maint_loss or [])],
                "updated": _iso(now)}
