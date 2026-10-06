"""Grind log (plan 005 slice A): sessions, per-spot silver/h, buff timers.

All operator input; nothing is read from the game (OCR is plan 009). Elapsed and
left times are derived from the injected clock on every read, so there is no
timer thread and a server that was down is still correct.
"""

import datetime as _dt
import re
import threading
import time
from fractions import Fraction

from . import levels
from .today import _iso, _parse_iso, slug

MAX_MINUTES = 1440
MAX_BUFF_MINUTES = 43200  # 30 days: Value Pack / Kamasylve blessing (see plan 005)
MAX_SILVER = 10 ** 13
MAX_TRASH = 10 ** 6
MAX_NAME = 60
MAX_SPOTS = 100
MAX_BUFFS = 50
XP_PCT_RANGE = (0, 1000)  # optional buff XP bonus, counted by the leveling XP stack (plan 011)
MAX_SESSIONS = 2000  # stored; GET shows the newest VIEW_SESSIONS
VIEW_SESSIONS = 200
ID_RE = re.compile(r"^[a-z0-9-]{1,40}$")
SID_RE = re.compile(r"^s[0-9]{1,9}$")

SEED_BUFFS = ("XP scroll", "Drop rate scroll", "Hot Time", "Value Pack", "Old Moon book",
              "Kamasylve blessing")

# Plan 038 drop buffs: every game number (caps, scroll price, dates) lives in
# the data file; tests/test_grind.py fails on such a literal in this module.
DROPS_FILE = levels.DATA_DIR / "drop_buffs.json"
BYPASS = ("none", "to400", "to500")  # applied in this order, each to its own cap
CAP_KEYS = ("base_pct", "bypass_pct", "beyond_pct")  # cap for BYPASS[i]
DROP_PCT_RANGE = (0, 1000)
CAP_RANGE = (0, 10000)
PER_WEEK_RANGE = (0, 1000)
AGRIS_BUFF_ID = "agris-scroll"
CAPS_ID = "caps"
SCROLL_ID = "agris_scroll"
DROP_ROW_FIELDS = ("id", "name", "bypass", "source", "verified")
DROP_ROW_OPTIONAL = ("aliases", "rate_pct", "amount_pct", "note")
CAPS_FIELDS = CAP_KEYS + ("source", "verified")
SCROLL_INTS = {"price_silver": (0, MAX_SILVER), "minutes": (1, MAX_MINUTES),
               "per_week": PER_WEEK_RANGE}
SCROLL_DATES = ("sale_until_utc", "removed_utc")
SCROLL_FIELDS = tuple(SCROLL_INTS) + SCROLL_DATES + ("source", "verified")
MAX_DROP_TEXT = 240


def silver_per_hour(silver, minutes):
    """silver * 60 / minutes, floored to an int; 0 when minutes is 0."""
    return silver * 60 // minutes if minutes > 0 else 0


# -- drop buffs (plan 038) -----------------------------------------------------

def _ok_num(v, lo, hi):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi


def _tidy(x):
    """A sum of pct values: int when whole, else rounded to 2 places."""
    x = round(x, 2)
    return int(x) if x == int(x) else x


def _date(v):
    if not isinstance(v, str) or not levels.DATE_RE.match(v):
        return None
    try:
        return _dt.date.fromisoformat(v)
    except ValueError:
        return None


def _check_caps(caps):
    vals = [caps.get(k) for k in CAP_KEYS]
    if not all(_ok_int(v, *CAP_RANGE) for v in vals) or vals != sorted(vals):
        raise ValueError("caps must be ints with base_pct <= bypass_pct <= beyond_pct")
    return caps


def _check_drop_value(kind, field, v):
    """Validated override / data value for one field of a buff row, caps or scroll."""
    if kind == "buff":
        if field in ("rate_pct", "amount_pct") and _ok_num(v, *DROP_PCT_RANGE):
            return v
        if field == "bypass" and v in BYPASS:
            return v
    elif kind == "caps":
        if field in CAP_KEYS and _ok_int(v, *CAP_RANGE):
            return v
    elif field in SCROLL_INTS and _ok_int(v, *SCROLL_INTS[field]):
        return v
    elif field in SCROLL_DATES and _date(v) is not None:
        return v
    raise ValueError(f"bad {kind} field {field}")


def _check_sourced(row, what):
    if not levels._ok_text(row.get("source"), MAX_DROP_TEXT):
        raise ValueError(f"{what}: source required")
    ver = row.get("verified")
    if not (ver is False or _date(ver) is not None):
        raise ValueError(f"{what}: verified must be a date or false")


def _check_drop_row(r):
    if (not isinstance(r, dict) or not set(DROP_ROW_FIELDS) <= set(r)
            or not set(r) <= set(DROP_ROW_FIELDS + DROP_ROW_OPTIONAL)):
        raise ValueError("drop buff row has wrong fields")
    if not isinstance(r["id"], str) or not ID_RE.match(r["id"]):
        raise ValueError(f"drop buff id: {r['id']!r}")
    _name(r["name"])
    aliases = r.get("aliases", [])
    if not isinstance(aliases, list):
        raise ValueError(f"{r['id']}: aliases must be a list")
    for a in aliases:
        _name(a)
    _check_drop_value("buff", "bypass", r["bypass"])
    if "rate_pct" not in r and "amount_pct" not in r:
        raise ValueError(f"{r['id']}: rate_pct or amount_pct required")
    for k in ("rate_pct", "amount_pct"):
        if k in r:
            _check_drop_value("buff", k, r[k])
    _check_sourced(r, r["id"])
    if r["verified"] is False and not levels._ok_text(r.get("note"), MAX_DROP_TEXT):
        raise ValueError(f"{r['id']}: an unverified row needs a note")
    if "note" in r and not levels._ok_text(r["note"], MAX_DROP_TEXT):
        raise ValueError(f"{r['id']}: bad note")


def load_drop_data(path=DROPS_FILE):
    """{caps, agris_scroll, buffs} from the tracked file, or ValueError."""
    data = levels._read(path, "drop buffs")
    if not isinstance(data, dict) or set(data) != {"caps", SCROLL_ID, "buffs"}:
        raise ValueError("drop buffs must be {caps, agris_scroll, buffs}")
    caps, scroll, rows = data["caps"], data[SCROLL_ID], data["buffs"]
    if not isinstance(caps, dict) or set(caps) != set(CAPS_FIELDS):
        raise ValueError("caps has wrong fields")
    _check_caps(caps)
    _check_sourced(caps, "caps")
    if not isinstance(scroll, dict) or set(scroll) != set(SCROLL_FIELDS):
        raise ValueError("agris_scroll has wrong fields")
    for k in tuple(SCROLL_INTS) + SCROLL_DATES:
        _check_drop_value("scroll", k, scroll[k])
    if _date(scroll["sale_until_utc"]) > _date(scroll["removed_utc"]):
        raise ValueError("agris_scroll sale ends after removal")
    _check_sourced(scroll, SCROLL_ID)
    if not isinstance(rows, list):
        raise ValueError("buffs must be a list")
    seen = set()
    for r in rows:
        _check_drop_row(r)
        keys = {r["id"]} | {n.lower() for n in [r["name"]] + r.get("aliases", [])}
        if keys & seen:
            raise ValueError(f"duplicate drop buff: {r['id']}")
        seen |= keys
    return data


def drop_stack(active, caps):
    """Sum active drop buffs against the caps row.

    Normal sources stack to caps.base_pct; `to400` sources then add their own
    value up to caps.bypass_pct and `to500` sources up to caps.beyond_pct (a
    bypass never lifts wasted normal %). Item drop AMOUNT is summed apart and
    never capped. cap_used is the highest cap any active rate source reaches.
    """
    total = capped = amount = 0
    cap_used = caps[CAP_KEYS[0]]
    for tier, key in zip(BYPASS, CAP_KEYS):
        add = sum(r.get("rate_pct", 0) for r in active if r["bypass"] == tier)
        if add:
            cap_used = max(cap_used, caps[key])
            total += add
            capped = min(capped + add, max(capped, caps[key]))
    amount = sum(r.get("amount_pct", 0) for r in active)
    return {"rate_total": _tidy(total), "rate_capped": _tidy(capped),
            "wasted": _tidy(total - capped), "amount_total": _tidy(amount),
            "cap_used": cap_used, "over_cap": total > capped}


def agris_roi(silver_h, scroll, rate_before, rate_after, now):
    """Blessing of Agris value at `silver_h`, from the scroll row (after overrides).

    Drop rate multiplies drops by (100 + rate) %, so the scroll's capped uplift
    is worth gain = (after - before) / (100 + before) of the spot's silver for
    its minutes. break_even_silver_h is the silver/h at which that pays the
    price; None when the scroll adds nothing. hidden once removed_utc is reached.
    """
    gain = Fraction(rate_after - rate_before) / (100 + Fraction(rate_before))
    hours = Fraction(scroll["minutes"], 60)
    price = scroll["price_silver"]
    be = int(price / (hours * gain)) if gain > 0 else None
    today = now.date()
    out = {"price_silver": price, "minutes": scroll["minutes"],
           "per_week": scroll["per_week"], "sale_until_utc": scroll["sale_until_utc"],
           "removed_utc": scroll["removed_utc"],
           "on_sale": today <= _date(scroll["sale_until_utc"]),
           "hidden": today >= _date(scroll["removed_utc"]),
           "rate_before": _tidy(rate_before), "rate_after": _tidy(rate_after),
           "gain_pct": _tidy(float(gain * 100)), "break_even_silver_h": be,
           "silver_h": silver_h,
           "gain_silver": None, "net_silver": None, "worth": None}
    if silver_h is not None:
        gs = int(silver_h * hours * gain)
        out.update(gain_silver=gs, net_silver=gs - price, worth=be is not None and gs >= price)
    return out


# -- validation ----------------------------------------------------------------

def _int(v, name, lo, hi):
    if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
        raise ValueError(f"{name} must be an int {lo}..{hi}")
    return v


def _name(v):
    if not isinstance(v, str):
        raise ValueError("name must be a string")
    v = v.strip()
    if not v or len(v) > MAX_NAME:
        raise ValueError(f"name must be 1..{MAX_NAME} characters")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in v):
        raise ValueError("name must not contain control characters")
    return v


def _fields(arg, what, keys):
    if not isinstance(arg, dict) or set(arg) != set(keys):
        raise ValueError(f"{what} must be {{{', '.join(keys)}}}")
    return arg


def _unique(base, taken):
    if base not in taken:
        return base
    n = 2
    while True:
        suffix = f"-{n}"
        cand = base[:40 - len(suffix)].rstrip("-") + suffix
        if cand not in taken:
            return cand
        n += 1


# -- stored-entry cleaning (corrupt docs degrade, never raise) -----------------

def _iso_or_none(s):
    """Normalised UTC stamp, or None when unparseable or out of datetime range
    once shifted to UTC (e.g. "0001-01-01T00:00:00+01:00")."""
    when = _parse_iso(s)
    if when is None:
        return None
    try:
        return _iso(when)
    except (OverflowError, ValueError):
        return None


def _ok_int(v, lo, hi):
    return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


def _clean_named(it):
    if not (isinstance(it, dict) and isinstance(it.get("id"), str) and ID_RE.match(it["id"])
            and isinstance(it.get("name"), str) and it["name"]):
        return None
    return it


def _clean_session(it):
    if not (isinstance(it, dict) and isinstance(it.get("id"), str) and SID_RE.match(it["id"])
            and isinstance(it.get("spot"), str) and _parse_iso(it.get("started"))
            and _ok_int(it.get("minutes"), 1, MAX_MINUTES)
            and _ok_int(it.get("silver"), 0, MAX_SILVER)
            and _ok_int(it.get("trash"), 0, MAX_TRASH)):
        return None
    return {k: it[k] for k in ("id", "spot", "started", "minutes", "silver", "trash")}


class GrindService:
    """Store domain `grind`: {"spots": [{id, name}], "sessions": [{id, spot, started,
    minutes, silver, trash}] (oldest first), "active": {spot, started}|null,
    "buffs": [{id, name, ends, xp_pct?}], "next_sid": int, "updated": "<iso>"}."""

    def __init__(self, store, clock=time.time, presets=None, epoch=None,
                 drops_path=DROPS_FILE):
        self.store = store
        self.clock = clock
        # Plan 038 drop-buff table (tracked data); a bad file degrades to an
        # error on the card and refuses drop writes.
        self.drops, self.drops_error = None, None
        try:
            self.drops = load_drop_data(drops_path)
        except ValueError as e:
            self.drops_error = str(e)
        # `epoch` returns the newest started XP epoch or None (plan 018): until
        # one has started the presets offer the pre-patch value and no hint shows.
        self.epoch = epoch
        # Plan 018 XP buff presets (tracked data); a bad file degrades to none.
        self.presets_error = None
        if presets is None:
            try:
                presets = levels.load_buff_presets()
            except ValueError as e:
                presets, self.presets_error = [], str(e)
        self.presets = presets
        self._lock = threading.Lock()  # read-modify-write; Store guards each file op
        with self._lock:
            if "spots" not in store.get("grind"):
                seen, buffs = set(), []
                for name in SEED_BUFFS:
                    bid = _unique(slug(name), seen)
                    seen.add(bid)
                    buffs.append({"id": bid, "name": name, "ends": None})
                self._save({"spots": [], "sessions": [], "active": None, "buffs": buffs,
                            "next_sid": 1})

    def _now(self):
        return _dt.datetime.fromtimestamp(self.clock(), _dt.timezone.utc)

    def _load(self):
        doc = self.store.get("grind")

        def lst(key, clean):
            raw = doc.get(key)
            out, seen = [], set()
            for c in (clean(i) for i in (raw if isinstance(raw, list) else [])):
                if c is not None and c["id"] not in seen:
                    seen.add(c["id"])
                    out.append(c)
            return out

        spots = [{"id": s["id"], "name": s["name"]} for s in lst("spots", _clean_named)]
        buffs = [{"id": b["id"], "name": b["name"],
                  "ends": _iso_or_none(b.get("ends")),
                  "xp_pct": b.get("xp_pct") if _ok_int(b.get("xp_pct"), *XP_PCT_RANGE) else None}
                 for b in lst("buffs", _clean_named)]
        sessions = lst("sessions", _clean_session)
        act = doc.get("active")
        active = None
        if (isinstance(act, dict) and act.get("spot") in {s["id"] for s in spots}
                and _parse_iso(act.get("started"))):
            active = {"spot": act["spot"], "started": act["started"]}
        nxt = doc.get("next_sid")
        top = max((int(s["id"][1:]) for s in sessions), default=0) + 1
        nxt = max(nxt, top) if _ok_int(nxt, 1, 10 ** 9 - 1) else top
        drop_on, overrides = self._clean_drops(doc.get("drop_on"), doc.get("drop_overrides"))
        return {"spots": spots, "sessions": sessions, "active": active, "buffs": buffs,
                "next_sid": nxt, "drop_on": drop_on, "drop_overrides": overrides}

    # -- drop buffs (plan 038) -------------------------------------------------

    def _drop_kind(self, rid):
        if rid == CAPS_ID:
            return "caps"
        if rid == SCROLL_ID:
            return "scroll"
        if self.drops is not None and rid in {r["id"] for r in self.drops["buffs"]}:
            return "buff"
        return None

    def _clean_drops(self, on, overrides):
        """Stored toggles / overrides; without the data file they pass through
        untouched so a broken file never wipes operator values."""
        if self.drops is None:
            return (on if isinstance(on, list) else [],
                    overrides if isinstance(overrides, dict) else {})
        out_on = []
        for rid in on if isinstance(on, list) else []:
            if isinstance(rid, str) and self._drop_kind(rid) == "buff" and rid not in out_on:
                out_on.append(rid)
        out = {}
        for rid, fields in (overrides if isinstance(overrides, dict) else {}).items():
            kind = self._drop_kind(rid)
            if kind is None or not isinstance(fields, dict):
                continue
            keep = {}
            for f, v in fields.items():
                try:
                    keep[f] = _check_drop_value(kind, f, v)
                except ValueError:
                    continue
            if kind == "caps":  # one at a time; a value breaking the order is dropped
                caps = {k: self.drops["caps"][k] for k in CAP_KEYS}
                for f in list(keep):
                    try:
                        _check_caps(dict(caps, **{f: keep[f]}))
                        caps[f] = keep[f]
                    except ValueError:
                        del keep[f]
            if keep:
                out[rid] = keep
        return out_on, out

    def _drop_effective(self, doc):
        """(caps, scroll, rows) after the operator's overrides."""
        ov = doc["drop_overrides"]
        caps = dict(self.drops["caps"], **ov.get(CAPS_ID, {}))
        scroll = dict(self.drops[SCROLL_ID], **ov.get(SCROLL_ID, {}))
        rows = [dict(r, **ov.get(r["id"], {}), overridden=r["id"] in ov)
                for r in self.drops["buffs"]]
        return caps, scroll, rows

    def _drops_view(self, doc, buffs, spots, now):
        if self.drops is None:
            return {"error": self.drops_error}, None
        caps, scroll, rows = self._drop_effective(doc)
        armed = {b["name"].lower() for b in buffs if b["left_s"] is not None}
        on = set(doc["drop_on"])
        active, out_rows = [], []
        for r in rows:
            names = {n.lower() for n in [r["name"]] + r.get("aliases", [])}
            via = "timer" if names & armed else ("toggle" if r["id"] in on else None)
            if via:
                active.append(dict(r, via=via))
            out_rows.append({"id": r["id"], "name": r["name"], "aliases": r.get("aliases", []),
                             "rate_pct": r.get("rate_pct"), "amount_pct": r.get("amount_pct"),
                             "bypass": r["bypass"], "source": r["source"],
                             "verified": r["verified"], "note": r.get("note"),
                             "on": r["id"] in on, "timer": bool(names & armed),
                             "overridden": r["overridden"]})
        drops = dict(drop_stack(active, caps), error=None,
                     caps={k: caps[k] for k in CAP_KEYS},
                     active=[{"id": a["id"], "name": a["name"], "via": a["via"],
                              "rate_pct": a.get("rate_pct"),
                              "amount_pct": a.get("amount_pct"), "bypass": a["bypass"]}
                             for a in active],
                     buffs=out_rows)
        agris = next((r for r in rows if r["id"] == AGRIS_BUFF_ID), None)
        if agris is None:
            return drops, None
        rest = [a for a in active if a["id"] != AGRIS_BUFF_ID]
        before = drop_stack(rest, caps)["rate_capped"]
        after = drop_stack(rest + [agris], caps)["rate_capped"]
        # Plan 005 spot average: the running session's spot, else the newest logged one.
        ref = doc["active"]["spot"] if doc["active"] else (
            doc["sessions"][-1]["spot"] if doc["sessions"] else None)
        spot = next((s for s in spots if s["id"] == ref and s["minutes"] > 0), None)
        roi = agris_roi(spot["silver_per_h"] if spot else None, scroll, before, after, now)
        roi["spot"] = spot["id"] if spot else None
        roi["spot_name"] = spot["name"] if spot else None
        return drops, roi

    def _need_drops(self):
        if self.drops is None:
            raise ValueError(f"drop buff table unavailable: {self.drops_error}")

    def drop_toggle(self, arg):
        """`{id, on}`: a passive drop source (node, fame, night...) on or off."""
        self._need_drops()
        arg = _fields(arg, "drop_toggle", ("id", "on"))
        if not isinstance(arg["id"], str) or self._drop_kind(arg["id"]) != "buff":
            raise ValueError(f"unknown drop buff: {arg['id']}")
        if not isinstance(arg["on"], bool):
            raise ValueError("on must be true or false")
        with self._lock:
            doc = self._load()
            on = [i for i in doc["drop_on"] if i != arg["id"]]
            doc["drop_on"] = on + [arg["id"]] if arg["on"] else on
            self._save(doc)
        return self.view()

    def drop_override(self, arg):
        """`{id, field, value}`: operator value for a buff row's rate_pct /
        amount_pct / bypass, a caps field or an agris_scroll field; value null
        restores the tracked value."""
        self._need_drops()
        arg = _fields(arg, "drop_override", ("id", "field", "value"))
        kind = self._drop_kind(arg["id"]) if isinstance(arg["id"], str) else None
        if kind is None:
            raise ValueError(f"unknown drop row: {arg['id']}")
        field, value = arg["field"], arg["value"]
        allowed = {"buff": ("rate_pct", "amount_pct", "bypass"), "caps": CAP_KEYS,
                   "scroll": tuple(SCROLL_INTS) + SCROLL_DATES}[kind]
        if field not in allowed:
            raise ValueError(f"field must be one of {', '.join(allowed)}")
        if value is not None:
            _check_drop_value(kind, field, value)
        with self._lock:
            doc = self._load()
            row = dict(doc["drop_overrides"].get(arg["id"], {}))
            row.pop(field, None)
            if value is not None:
                row[field] = value
            if kind == "caps":
                _check_caps(dict(self.drops["caps"], **row))
            ov = dict(doc["drop_overrides"])
            if row:
                ov[arg["id"]] = row
            else:
                ov.pop(arg["id"], None)
            doc["drop_overrides"] = ov
            self._save(doc)
        return self.view()

    def _save(self, doc):
        doc["sessions"] = doc["sessions"][-MAX_SESSIONS:]
        doc["updated"] = _iso(self._now())
        self.store.put("grind", doc)

    @staticmethod
    def _spot_id(doc, sid):
        if not isinstance(sid, str) or sid not in {s["id"] for s in doc["spots"]}:
            raise ValueError(f"unknown spot: {sid}")
        return sid

    @staticmethod
    def _amounts(arg):
        return (_int(arg["silver"], "silver", 0, MAX_SILVER),
                _int(arg["trash"], "trash", 0, MAX_TRASH))

    def _add_session(self, doc, spot, started, minutes, silver, trash):
        sid = f"s{doc['next_sid']}"
        doc["next_sid"] += 1
        doc["sessions"].append({"id": sid, "spot": spot, "started": started, "minutes": minutes,
                                "silver": silver, "trash": trash})

    # -- reads -----------------------------------------------------------------

    def view(self):
        """GET /api/grind body."""
        now = self._now()
        doc = self._load()
        active = doc["active"]
        if active is not None:
            elapsed = int((now - _parse_iso(active["started"])).total_seconds())
            active = dict(active, elapsed_s=max(0, elapsed))
        totals = {}
        for s in doc["sessions"]:
            t = totals.setdefault(s["spot"], [0, 0, 0])
            t[0] += 1
            t[1] += s["minutes"]
            t[2] += s["silver"]
        spots = []
        for sp in doc["spots"]:
            n, minutes, silver = totals.get(sp["id"], (0, 0, 0))
            spots.append({"id": sp["id"], "name": sp["name"], "sessions": n, "minutes": minutes,
                          "silver_per_h": silver_per_hour(silver, minutes)})
        patched = self._patched()
        buffs = []
        for b in doc["buffs"]:
            ends = _parse_iso(b["ends"])
            left = int((ends - now).total_seconds()) if ends is not None else 0
            hint = levels.buff_hint(b["name"], b["xp_pct"], self.presets) if patched else None
            if left > 0:
                buffs.append({"id": b["id"], "name": b["name"], "ends": b["ends"],
                              "left_s": left, "xp_pct": b["xp_pct"], "xp_hint": hint})
            else:  # unarmed or expired: listed so it can be re-armed in one tap
                buffs.append({"id": b["id"], "name": b["name"], "ends": None, "left_s": None,
                              "xp_pct": b["xp_pct"], "xp_hint": hint})
        sessions = list(reversed(doc["sessions"][-VIEW_SESSIONS:]))
        presets = [{"name": p["name"],
                    "xp_pct": p["xp_pct"] if patched else p["pre_patch_xp_pct"],
                    "patched": patched, "notes": p["notes"], "source": p["source"],
                    "verified": p["verified"]} for p in self.presets]
        drops, roi = self._drops_view(doc, buffs, spots, now)
        return {"now": _iso(now), "active": active, "sessions": sessions, "spots": spots,
                "buffs": buffs, "xp_presets": presets, "xp_presets_error": self.presets_error,
                "drops": drops, "agris_roi": roi}

    def _patched(self):
        """True once an XP epoch has started (refute r1 minor 1); without an
        epoch source (bare service) the tracked post-patch values apply."""
        if self.epoch is None:
            return True
        try:
            return self.epoch() is not None
        except Exception:  # noqa: BLE001 - the hint is an extra, never fatal
            return True

    def source(self):
        """`/api/state` sources.grind: {updated, status: "ok"}."""
        return {"updated": _iso_or_none(self.store.get("grind").get("updated")), "status": "ok"}

    # -- writes (each returns the GET body) -----------------------------------

    def add_spot(self, name):
        name = _name(name)
        with self._lock:
            doc = self._load()
            if any(s["name"].lower() == name.lower() for s in doc["spots"]):
                raise ValueError(f"spot exists: {name}")
            if len(doc["spots"]) >= MAX_SPOTS:
                raise ValueError(f"at most {MAX_SPOTS} spots")
            sid = _unique(slug(name), {s["id"] for s in doc["spots"]})
            doc["spots"].append({"id": sid, "name": name})
            self._save(doc)
        return self.view()

    def start(self, spot):
        with self._lock:
            doc = self._load()
            self._spot_id(doc, spot)
            if doc["active"] is not None:
                raise ValueError("a session is already active; stop it first")
            doc["active"] = {"spot": spot, "started": _iso(self._now())}
            self._save(doc)
        return self.view()

    def stop(self, arg):
        """`{silver, trash}`; minutes from active.started, clamped to 1..MAX_MINUTES."""
        silver, trash = self._amounts(_fields(arg, "stop", ("silver", "trash")))
        with self._lock:
            doc = self._load()
            act = doc["active"]
            if act is None:
                raise ValueError("no active session")
            secs = (self._now() - _parse_iso(act["started"])).total_seconds()
            minutes = min(MAX_MINUTES, max(1, int(secs // 60)))
            self._add_session(doc, act["spot"], act["started"], minutes, silver, trash)
            doc["active"] = None
            self._save(doc)
        return self.view()

    def log(self, arg):
        """Manual `{spot, minutes, silver, trash}`; started = now - minutes."""
        arg = _fields(arg, "log", ("spot", "minutes", "silver", "trash"))
        minutes = _int(arg["minutes"], "minutes", 1, MAX_MINUTES)
        silver, trash = self._amounts(arg)
        with self._lock:
            doc = self._load()
            spot = self._spot_id(doc, arg["spot"])
            started = _iso(self._now() - _dt.timedelta(minutes=minutes))
            self._add_session(doc, spot, started, minutes, silver, trash)
            self._save(doc)
        return self.view()

    def delete(self, sid):
        if not isinstance(sid, str) or not SID_RE.match(sid):
            raise ValueError("delete must be a session id like s12")
        with self._lock:
            doc = self._load()
            keep = [s for s in doc["sessions"] if s["id"] != sid]
            if len(keep) == len(doc["sessions"]):
                raise ValueError(f"unknown session: {sid}")
            doc["sessions"] = keep
            self._save(doc)
        return self.view()

    def buff(self, arg):
        """`{name, minutes, xp_pct?}`: ends = now + minutes. Same name
        (case-insensitive) re-arms in place, keeping a stored xp_pct unless one
        is given; a new name is added. xp_pct (0-1000, plan 011) is optional."""
        has_xp = isinstance(arg, dict) and "xp_pct" in arg
        arg = _fields(arg, "buff", ("name", "minutes", "xp_pct") if has_xp
                      else ("name", "minutes"))
        name = _name(arg["name"])
        minutes = _int(arg["minutes"], "minutes", 1, MAX_BUFF_MINUTES)
        xp = _int(arg["xp_pct"], "xp_pct", *XP_PCT_RANGE) if has_xp else None
        with self._lock:
            doc = self._load()
            ends = _iso(self._now() + _dt.timedelta(minutes=minutes))
            for b in doc["buffs"]:
                if b["name"].lower() == name.lower():
                    b["ends"] = ends
                    if has_xp:
                        b["xp_pct"] = xp
                    break
            else:
                if len(doc["buffs"]) >= MAX_BUFFS:
                    raise ValueError(f"at most {MAX_BUFFS} buffs")
                bid = _unique(slug(name), {b["id"] for b in doc["buffs"]})
                doc["buffs"].append({"id": bid, "name": name, "ends": ends, "xp_pct": xp})
            self._save(doc)
        return self.view()

    def clear_buff(self, bid):
        """Disarm a buff timer; the name stays listed for one-tap re-arming."""
        with self._lock:
            doc = self._load()
            for b in doc["buffs"]:
                if b["id"] == bid:
                    b["ends"] = None
                    break
            else:
                raise ValueError(f"unknown buff: {bid}")
            self._save(doc)
        return self.view()
