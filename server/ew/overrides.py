"""Plan 079: override ledger - every operator-set value carries its source,
set-at time and expiry, and a fresh live signal supersedes it.

Store domain `overrides`: {"live": {key: entry}, "retired": [entry, ...],
"seen": {key: config value}}. An entry is {key, value, source: typed|config,
set_at, expires_at|null, reason, retired_at|null, retired_by|null}; at most
one live entry per key; retired entries are kept (last MAX_RETIRED) for the
digest history.

The tracked `data/override_policy.json` gives each key its expiry rule
(`days:N`, `until:maint_end`, `until:path_missing`, or `none` - only for the
NONE_OK keys) and the cards that show its badge.

`resolve` is pure over the doc: a fresh live value retires a live override
(`retired_by: "<signal>"`), an expired one retires `"expired"`, a folder that
is gone retires `"path_missing"`. `from_config` turns a config/local.json value
that differs from its default into a `source: config` entry, set_at = file
mtime when first seen, and is a no-op on re-read.
"""

import copy
import datetime as _dt
import json
import os
import re
import threading
import time
from pathlib import Path

POLICY_FILE = Path(__file__).resolve().parent / "data" / "override_policy.json"
RULE_RE = re.compile(r"^(days:[1-9][0-9]{0,3}|until:maint_end|until:path_missing|none)$")
# Plan 083: `portrait.*` (a gallery pick) is cosmetic - it changes no number.
NONE_OK = ("market.fame_pct", "profile.family", "bdo.install_dir", "bdo.documents_dir",
           "ui.scale", "portrait.*")
INCIDENT_REASON = "incident switch in config/local.json; the fixed value returns when it expires"
SOURCES = ("typed", "config")
MAX_RETIRED = 50
# until:maint_end with no computable window: the slot repeats weekly, so a week.
MAINT_FALLBACK_S = 8 * 86400
MAX_REASON = 200


def _iso(t):
    return _dt.datetime.fromtimestamp(t, _dt.timezone.utc).replace(microsecond=0).isoformat()


def _ts(v):
    if not isinstance(v, str):
        return None
    try:
        t = _dt.datetime.fromisoformat(v)
    except ValueError:
        return None
    return t.timestamp() if t.tzinfo is not None else None


# -- policy ------------------------------------------------------------------------

def clean_policy(doc):
    """{key: {label, expiry, cards}}; a bad row (or `none` outside NONE_OK) is dropped."""
    keys = doc.get("keys") if isinstance(doc, dict) else None
    out = {}
    for key, row in (keys.items() if isinstance(keys, dict) else ()):
        if not (isinstance(key, str) and isinstance(row, dict)):
            continue
        rule = row.get("expiry")
        if not (isinstance(rule, str) and RULE_RE.fullmatch(rule)):
            continue
        if rule == "none" and key not in NONE_OK:
            continue
        cards = row.get("cards")
        cards = [c for c in cards if isinstance(c, str)] if isinstance(cards, list) else []
        label = row.get("label") if isinstance(row.get("label"), str) else key
        out[key] = {"label": label, "expiry": rule, "cards": cards,
                    "first_sight": row.get("first_sight") is True,
                    "note": row["note"][:MAX_REASON] if isinstance(row.get("note"), str) else None}
    return out


def load_policy(path=None):
    try:
        doc = json.loads(Path(path or POLICY_FILE).read_text(encoding="ascii"))
    except (OSError, ValueError):
        doc = None
    return clean_policy(doc)


def policy_for(policy, key):
    """The policy row for `key`: exact, else the longest `prefix.*` match; or None."""
    if key in policy:
        return policy[key]
    best = None
    for k in policy:
        if k.endswith(".*") and key.startswith(k[:-1]) and (best is None or len(k) > len(best)):
            best = k
    return policy[best] if best else None


def expiry_at(rule, set_at, maint_end=None):
    """Epoch expiry for an entry set at `set_at` (epoch), or None (never by time).
    `maint_end(set_at) -> epoch|None` gives the end of the next maintenance."""
    if rule.startswith("days:"):
        return set_at + int(rule[5:]) * 86400
    if rule == "until:maint_end":
        end = None
        if maint_end is not None:
            try:
                end = maint_end(set_at)
            except Exception:  # noqa: BLE001 - no window known: the weekly fallback
                end = None
        return end if isinstance(end, (int, float)) and end > set_at else set_at + MAINT_FALLBACK_S
    return None


# -- the doc -----------------------------------------------------------------------

def empty():
    return {"live": {}, "retired": [], "seen": {}}


def _clean_entry(e, key=None):
    if not (isinstance(e, dict) and isinstance(e.get("key"), str)
            and e.get("source") in SOURCES and _ts(e.get("set_at")) is not None):
        return None
    if key is not None and e["key"] != key:
        return None
    exp = e.get("expires_at")
    out = {"key": e["key"], "value": copy.deepcopy(e.get("value")), "source": e["source"],
           "set_at": e["set_at"], "expires_at": exp if _ts(exp) is not None else None,
           "reason": e["reason"] if isinstance(e.get("reason"), str) else "",
           "retired_at": e.get("retired_at") if _ts(e.get("retired_at")) is not None else None,
           "retired_by": e.get("retired_by") if isinstance(e.get("retired_by"), str) else None}
    return out


def clean(doc):
    doc = doc if isinstance(doc, dict) else {}
    live = {}
    raw = doc.get("live")
    for k, e in (raw.items() if isinstance(raw, dict) else ()):
        c = _clean_entry(e, k)
        if c is not None and c["retired_at"] is None:
            live[k] = c
    retired = [c for c in map(_clean_entry, doc.get("retired") if isinstance(
        doc.get("retired"), list) else []) if c is not None and c["retired_at"] is not None]
    seen = doc.get("seen") if isinstance(doc.get("seen"), dict) else {}
    return {"live": live, "retired": retired[-MAX_RETIRED:], "seen": copy.deepcopy(seen)}


def retire(doc, key, now, by):
    """Move the live entry for `key` to the history; returns it or None."""
    e = doc["live"].pop(key, None)
    if e is None:
        return None
    e = dict(e, retired_at=_iso(now), retired_by=by)
    doc["retired"] = (doc["retired"] + [e])[-MAX_RETIRED:]
    return e


def put(doc, key, value, source, now, rule, reason="", maint_end=None, set_at=None):
    """Create / replace the one live entry for `key` (a replaced one retires
    `"replaced"`); returns the new entry."""
    if source not in SOURCES:
        raise ValueError("source must be typed or config")
    at = now if set_at is None else set_at
    retire(doc, key, now, "replaced")
    exp = expiry_at(rule, at, maint_end)
    e = {"key": key, "value": copy.deepcopy(value), "source": source, "set_at": _iso(at),
         "expires_at": _iso(exp) if exp is not None else None,
         "reason": (reason or "")[:MAX_REASON], "retired_at": None, "retired_by": None}
    doc["live"][key] = e
    if source == "typed":  # a typed write also lands in config: never re-import it
        doc["seen"][key] = copy.deepcopy(value)
    return e


def expired(entry, now):
    exp = _ts(entry.get("expires_at"))
    return exp is not None and now >= exp


def resolve(doc, key, live, default, now, rule, path_exists=os.path.isdir):
    """{value, from: live|override|default, entry|null}. `live` is None (no
    live signal) or {"value", "signal"}; mutates `doc` when it retires."""
    e = doc["live"].get(key)
    if e is not None and live is not None:
        retire(doc, key, now, str(live.get("signal") or "live"))
        e = None
    if e is not None and expired(e, now):
        retire(doc, key, now, "expired")
        e = None
    if e is not None and rule == "until:path_missing":
        try:
            gone = not (isinstance(e["value"], str) and e["value"] and path_exists(e["value"]))
        except (OSError, ValueError):
            gone = True
        if gone:
            retire(doc, key, now, "path_missing")
            e = None
    if e is not None:
        return {"value": copy.deepcopy(e["value"]), "from": "override", "entry": dict(e)}
    if live is not None:
        return {"value": copy.deepcopy(live.get("value")), "from": "live", "entry": None}
    return {"value": copy.deepcopy(default), "from": "default", "entry": None}


_MISSING = object()


def from_config(doc, values, defaults, mtime, keys, policy, maint_end=None, now=None):
    """Import config/local.json values (`values`: dotted key -> value) for
    `keys` that differ from `defaults` as `source: config` entries, set_at =
    `mtime` when first seen. Idempotent: a value already seen is skipped, so an
    expired or superseded config value stays retired until the file changes it.
    A value back at its default forgets the key and retires a config entry."""
    now = mtime if now is None else now
    for key in keys:
        row = policy_for(policy, key)
        if row is None or key not in values:
            continue
        v, d = values[key], defaults.get(key)
        seen = doc["seen"].get(key, _MISSING)
        if v == d:
            doc["seen"].pop(key, None)
            e = doc["live"].get(key)
            if e is not None and e["source"] == "config":
                retire(doc, key, now, "config")
            continue
        if seen is not _MISSING and seen == v:
            continue
        doc["seen"][key] = copy.deepcopy(v)
        e = doc["live"].get(key)
        if e is not None and e["value"] == v:
            continue
        # Plan 080: an incident switch (`first_sight`) runs from when EW first
        # reads it, so a months-old config value still gets its full 24 h.
        incident = row.get("first_sight") is True
        put(doc, key, v, "config", now, row["expiry"],
            reason=(row.get("note") or INCIDENT_REASON) if incident else "config/local.json",
            maint_end=maint_end, set_at=now if incident else min(mtime, now))
        doc["seen"][key] = copy.deepcopy(v)


def item(entry, policy, now, labels=None):
    """One /api/overrides row."""
    row = policy_for(policy, entry["key"]) or {"label": entry["key"], "cards": []}
    exp = _ts(entry.get("expires_at"))
    label = (labels or {}).get(entry["key"])
    if not isinstance(label, str) or not label:
        label = row["label"]
        if entry["key"] not in policy:  # a `prefix.*` row: name the member
            label = f"{label}: {entry['key'].rsplit('.', 1)[-1]}"
    return {"key": entry["key"], "label": label, "value": copy.deepcopy(entry["value"]),
            "source": entry["source"], "set_at": entry["set_at"],
            "expires_at": entry.get("expires_at"),
            "expires_in_s": max(0, int(exp - now)) if exp is not None else None,
            "reason": entry.get("reason", ""), "cards": list(row["cards"]),
            "clearable": entry.get("clearable", True)}


class OverrideLedger:
    """The store-backed ledger. `maint_end(set_at, value) -> epoch|None` gives
    the end of the next maintenance an `until:maint_end` override applies to."""

    def __init__(self, store, clock=time.time, policy=None, maint_end=None,
                 path_exists=os.path.isdir):
        self.store = store
        self.clock = clock
        self.policy = load_policy() if policy is None else policy
        self.maint_end = maint_end
        self.path_exists = path_exists
        self._lock = threading.Lock()

    def rule(self, key):
        row = policy_for(self.policy, key)
        return row["expiry"] if row else None

    def covers(self, key):
        return policy_for(self.policy, key) is not None

    def _maint_end_for(self, value):
        if self.maint_end is None:
            return None
        return lambda at: self.maint_end(at, value)

    def _edit(self, fn):
        with self._lock:
            doc = clean(self.store.get("overrides"))
            before = copy.deepcopy(doc)
            out = fn(doc)
            if doc != before:
                self.store.put("overrides", doc)
        return out

    def doc(self):
        with self._lock:
            return clean(self.store.get("overrides"))

    def effective(self, key, live, default, now=None):
        now = self.clock() if now is None else now
        rule = self.rule(key)
        if rule is None:
            raise ValueError(f"{key}: not an override key")
        return self._edit(lambda d: resolve(d, key, live, default, now, rule, self.path_exists))

    def effective_many(self, spec, now=None):
        """{key: (live, default)} -> {key: effective()} in one store read / write."""
        now = self.clock() if now is None else now
        spec = {k: v for k, v in spec.items() if self.covers(k)}
        return self._edit(lambda d: {k: resolve(d, k, live, dflt, now, self.rule(k),
                                                self.path_exists)
                                     for k, (live, dflt) in spec.items()})

    def set(self, key, value, source="typed", reason="", now=None):
        now = self.clock() if now is None else now
        rule = self.rule(key)
        if rule is None:
            raise ValueError(f"{key}: not an override key")
        return self._edit(lambda d: put(d, key, value, source, now, rule, reason,
                                        self._maint_end_for(value)))

    def clear(self, key, by="operator", now=None):
        now = self.clock() if now is None else now
        return self._edit(lambda d: retire(d, key, now, by))

    def sync_config(self, values, defaults, mtime, now=None):
        now = self.clock() if now is None else now
        keys = [k for k in values if self.covers(k)]

        def run(d):
            for k in keys:  # one maint_end per key value
                from_config(d, values, defaults, mtime, [k], self.policy,
                            self._maint_end_for(values[k]), now)
        self._edit(run)

    def sweep(self, now=None):
        """Retire expired / path-missing entries without a live signal."""
        now = self.clock() if now is None else now

        def run(d):
            for k in list(d["live"]):
                resolve(d, k, None, None, now, self.rule(k) or "none", self.path_exists)
        self._edit(run)

    def items(self, now=None, labels=None):
        now = self.clock() if now is None else now
        d = self.doc()
        return [item(e, self.policy, now, labels) for _, e in sorted(d["live"].items())]

    def history(self):
        return list(reversed(self.doc()["retired"]))
