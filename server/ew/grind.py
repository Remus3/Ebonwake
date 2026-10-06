"""Grind log (plan 005 slice A): sessions, per-spot silver/h, buff timers.

All operator input; nothing is read from the game (OCR is plan 009). Elapsed and
left times are derived from the injected clock on every read, so there is no
timer thread and a server that was down is still correct.
"""

import datetime as _dt
import json
import re
import threading
import time
from pathlib import Path

from . import levels, market, spots
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

# Plan 039: loot-valued sessions.
LOOT_FILE = Path(__file__).resolve().parent / "data" / "loot_tables.json"
DATE_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")
MAX_LOOT = 50              # entries per session
MAX_LOOT_COUNT = 10 ** 7   # units of one item per session
MAX_LOOT_ITEMS = 100       # operator-added items per spot
MAX_VENDOR = 10 ** 10      # silver per unit at an NPC
ITEM_KEYS = ("name", "marketable", "id", "vendor_price")


def silver_per_hour(silver, minutes):
    """silver * 60 / minutes, floored to an int; 0 when minutes is 0."""
    return silver * 60 // minutes if minutes > 0 else 0


# -- plan 039: loot tables, valuation, sell-vs-vendor --------------------------

def _check_item(it):
    """Validated copy of a loot item {name, marketable, id?, vendor_price?}; a
    non-marketable item needs a vendor price or it can never be valued."""
    if not isinstance(it, dict) or not {"name", "marketable"} <= set(it) <= set(ITEM_KEYS):
        raise ValueError("loot item must be {name, marketable, id?, vendor_price?}")
    out = {"name": _name(it["name"])}
    if not isinstance(it["marketable"], bool):
        raise ValueError("marketable must be true or false")
    out["marketable"] = it["marketable"]
    if it.get("id") is not None:
        out["id"] = _int(it["id"], "id", 1, market.MAX_ID)
    if it.get("vendor_price") is not None:
        out["vendor_price"] = _int(it["vendor_price"], "vendor_price", 0, MAX_VENDOR)
    if not out["marketable"] and "vendor_price" not in out:
        raise ValueError(f"{out['name']}: a non-marketable item needs a vendor_price")
    return out


def validate_loot_tables(doc):
    """`{"spots": {spot_id: {items, source, verified}}}` -> the spots dict."""
    tables = doc.get("spots") if isinstance(doc, dict) else None
    if not isinstance(tables, dict):
        raise ValueError("loot tables must be {spots: {spot_id: {...}}}")
    out = {}
    for sid, entry in tables.items():
        if not ID_RE.match(sid):
            raise ValueError(f"bad spot id: {sid!r}")
        if not isinstance(entry, dict) or set(entry) != {"items", "source", "verified"}:
            raise ValueError(f"{sid}: entry must be {{items, source, verified}}")
        if not isinstance(entry["source"], str) or not entry["source"].strip():
            raise ValueError(f"{sid}: source is required")
        ver = entry["verified"]
        if not (ver is False or (isinstance(ver, str) and DATE_RE.match(ver))):
            raise ValueError(f"{sid}: verified must be false or YYYY-MM-DD")
        if not isinstance(entry["items"], list):
            raise ValueError(f"{sid}: items must be a list")
        items, seen = [], set()
        for it in entry["items"]:
            c = _check_item(it)
            if c["name"].lower() in seen:
                raise ValueError(f"{sid}: duplicate item {c['name']}")
            seen.add(c["name"].lower())
            items.append(c)
        out[sid] = {"items": items, "source": entry["source"], "verified": ver}
    return out


def load_loot_tables(path=LOOT_FILE):
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ValueError(f"loot tables unreadable: {e}") from e
    return validate_loot_tables(doc)


def _price_ok(v):
    return _ok_int(v, 1, 10 ** 15)


def loot_value(loot, prices, vp, fame):
    """Value `loot` [{name, id?, count, vendor_price?, marketable}]: trash at its
    vendor price (no tax), marketable at plan 027 net proceeds of the stack.
    A marketable item without a market price, or trash without a vendor price,
    is valued 0 and listed in `unknown`. `prices` maps item id -> price."""
    items, unknown, trash, mkt = [], [], 0, 0
    for it in loot:
        count, iid = it["count"], it.get("id")
        unit = value = None
        if it["marketable"]:
            price = prices.get(iid) if iid is not None else None
            if _price_ok(price):
                kind, unit = "market", price
                value = market.net_proceeds(price * count, vp, fame)
                mkt += value
        elif _ok_int(it.get("vendor_price"), 0, MAX_VENDOR):
            kind, unit = "vendor", it["vendor_price"]
            value = unit * count
            trash += value
        if unit is None:
            kind, value = "unknown", 0
            unknown.append(it["name"])
        items.append({"name": it["name"], "id": iid, "count": count, "kind": kind,
                      "unit": unit, "value": value})
    return {"total": trash + mkt, "trash": trash, "market": mkt, "unknown": unknown,
            "items": items}


def sell_or_vendor(item, price, vp, fame):
    """Per unit: `vendor | market | either | unknown`, with `diff` (silver per
    unit the better choice gains) when both sides are known."""
    vendor = item.get("vendor_price")
    vendor = vendor if _ok_int(vendor, 0, MAX_VENDOR) else None
    net = market.net_proceeds(price, vp, fame) if item.get("marketable") and _price_ok(price) \
        else None
    diff = None
    if vendor is not None and net is not None:
        diff = abs(net - vendor)
        choice = "market" if net > vendor else "vendor" if vendor > net else "either"
    elif net is not None:
        choice = "market"
    elif vendor is not None:
        choice = "vendor"
    else:
        choice = "unknown"
    return {"choice": choice, "vendor": vendor, "market_net": net, "diff": diff}


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
    out = {k: it[k] for k in ("id", "spot", "started", "minutes", "silver", "trash")}
    out["loot"], out["loot_value"] = _clean_loot(it.get("loot")), _clean_loot_value(
        it.get("loot_value"))
    return out


def _clean_loot(raw):
    if not isinstance(raw, list):
        return None
    out = []
    for i in raw:
        if not (isinstance(i, dict) and isinstance(i.get("name"), str)
                and _ok_int(i.get("count"), 1, MAX_LOOT_COUNT)
                and i.get("kind") in ("vendor", "market", "unknown")
                and _ok_int(i.get("value"), 0, 10 ** 18)):
            continue
        iid, unit = i.get("id"), i.get("unit")
        out.append({"name": i["name"], "id": iid if _ok_int(iid, 1, market.MAX_ID) else None,
                    "count": i["count"], "kind": i["kind"],
                    "unit": unit if _ok_int(unit, 0, 10 ** 15) else None, "value": i["value"]})
    return out or None


def _clean_loot_value(raw):
    if not (isinstance(raw, dict) and all(_ok_int(raw.get(k), 0, 10 ** 18)
                                          for k in ("total", "trash", "market"))
            and isinstance(raw.get("unknown"), list)):
        return None
    return {"total": raw["total"], "trash": raw["trash"], "market": raw["market"],
            "unknown": [u for u in raw["unknown"] if isinstance(u, str)]}


def _clean_loot_items(raw, spot_ids):
    out = {}
    for sid, items in (raw.items() if isinstance(raw, dict) else ()):
        if sid not in spot_ids or not isinstance(items, list):
            continue
        keep, seen = [], set()
        for it in items:
            try:
                c = _check_item(it)
            except ValueError:
                continue
            if c["name"].lower() not in seen:
                seen.add(c["name"].lower())
                keep.append(c)
        if keep:
            out[sid] = keep[:MAX_LOOT_ITEMS]
    return out


class GrindService:
    """Store domain `grind`: {"spots": [{id, name}], "sessions": [{id, spot, started,
    minutes, silver, trash}] (oldest first), "active": {spot, started}|null,
    "buffs": [{id, name, ends, xp_pct?}], "next_sid": int, "updated": "<iso>"}.
    Plan 039: a session may also carry `loot` [{name, id, count, kind, unit,
    value}] + `loot_value` {total, trash, market, unknown}, priced when logged;
    "loot_items": {spot_id: [{name, marketable, id?, vendor_price?}]} holds
    operator-added items merged over the tracked table."""

    def __init__(self, store, clock=time.time, presets=None, epoch=None, loot_tables=None,
                 prices=None, tax=None):
        self.store = store
        self.clock = clock
        # Plan 039: tracked per-spot loot tables (a bad file degrades to none),
        # `prices(item_id) -> int|None` (cached arsha read) and `tax() -> {vp,
        # fame_pct}` (plan 027 settings). Without them every market item is unknown.
        self.loot_error = None
        if loot_tables is None:
            try:
                loot_tables = load_loot_tables()
            except ValueError as e:
                loot_tables, self.loot_error = {}, str(e)
        self.loot_tables = loot_tables
        try:
            self._spot_names = {r["name"].strip().lower(): r["id"] for r in spots.load_table()}
        except Exception:  # noqa: BLE001 - name mapping is an extra, never fatal
            self._spot_names = {}
        self.prices = prices
        self.tax = tax
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
        loot_items = _clean_loot_items(doc.get("loot_items"), {s["id"] for s in spots})
        return {"spots": spots, "sessions": sessions, "active": active, "buffs": buffs,
                "next_sid": nxt, "loot_items": loot_items}

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

    def _add_session(self, doc, spot, started, minutes, silver, trash, valued=None):
        sid = f"s{doc['next_sid']}"
        doc["next_sid"] += 1
        ses = {"id": sid, "spot": spot, "started": started, "minutes": minutes,
               "silver": silver, "trash": trash}
        if valued is not None:
            ses["loot"] = valued.pop("items")
            ses["loot_value"] = valued
        doc["sessions"].append(ses)

    # -- plan 039: loot ----------------------------------------------------------

    def _table_key(self, doc, spot):
        """Loot table id for a grind spot: its own id, else the plan 012 row
        whose name matches the spot name (case-insensitive), else None."""
        if spot in self.loot_tables:
            return spot
        name = next((s["name"] for s in doc["spots"] if s["id"] == spot), "")
        key = self._spot_names.get(name.strip().lower())
        return key if key in self.loot_tables else None

    def _items(self, doc, spot):
        """Table items merged with operator items (same name: operator wins)."""
        key = self._table_key(doc, spot)
        out = [dict(it, origin="table") for it in
               (self.loot_tables[key]["items"] if key else [])]
        for it in doc["loot_items"].get(spot, []):
            row = dict(it, origin="operator")
            at = next((n for n, o in enumerate(out)
                       if o["name"].lower() == it["name"].lower()), None)
            if at is None:
                out.append(row)
            else:
                out[at] = row
        return out

    def _price(self, iid):
        if self.prices is None or iid is None:
            return None
        try:
            p = self.prices(iid)
        except Exception:  # noqa: BLE001 - an unreachable market leaves the price unknown
            return None
        return p if _price_ok(p) else None

    def _tax(self):
        try:
            t = self.tax() if self.tax is not None else None
        except Exception:  # noqa: BLE001 - defaults (no VP, no fame) are the safe floor
            t = None
        return market.settings_from({"market": t if isinstance(t, dict) else {}})

    def _resolve_loot(self, doc, spot, loot):
        """Operator `[{name|id, count}]` -> priced loot_value(), or None when empty."""
        if not isinstance(loot, list) or len(loot) > MAX_LOOT:
            raise ValueError(f"loot must be a list of at most {MAX_LOOT} {{name|id, count}}")
        if not loot:
            return None
        items = self._items(doc, spot)
        picked, seen = [], set()
        for e in loot:
            if not isinstance(e, dict) or set(e) not in ({"name", "count"}, {"id", "count"}):
                raise ValueError("loot entry must be {name, count} or {id, count}")
            count = _int(e["count"], "count", 1, MAX_LOOT_COUNT)
            if "name" in e:
                want = e["name"].strip().lower() if isinstance(e["name"], str) else None
                it = next((i for i in items if i["name"].lower() == want), None)
            else:
                iid = _int(e["id"], "id", 1, market.MAX_ID)
                it = next((i for i in items if i.get("id") == iid), None)
            if it is None:
                raise ValueError(f"not in this spot's loot table: {e.get('name', e.get('id'))}")
            if it["name"].lower() in seen:
                raise ValueError(f"loot item listed twice: {it['name']}")
            seen.add(it["name"].lower())
            picked.append(dict(it, count=count))
        prices = {i["id"]: self._price(i["id"]) for i in picked
                  if i["marketable"] and i.get("id") is not None}
        t = self._tax()
        return loot_value(picked, prices, t["vp"], t["fame_pct"])

    def loot(self, spot):
        """GET /api/grind/loot?spot=<id>: the spot's items with live prices and
        a sell-vs-vendor hint each."""
        doc = self._load()
        self._spot_id(doc, spot)
        key = self._table_key(doc, spot)
        t = self._tax()
        items = []
        for it in self._items(doc, spot):
            price = self._price(it.get("id")) if it["marketable"] else None
            hint = sell_or_vendor(it, price, t["vp"], t["fame_pct"])
            items.append({"name": it["name"], "id": it.get("id"),
                          "marketable": it["marketable"],
                          "vendor_price": it.get("vendor_price"), "origin": it["origin"],
                          "price": price, "net": hint["market_net"], "hint": hint})
        entry = self.loot_tables.get(key) if key else None
        return {"spot": spot, "table": key, "source": entry["source"] if entry else None,
                "verified": entry["verified"] if entry else None, "items": items,
                "tax": t, "error": self.loot_error}

    def loot_item(self, arg):
        """`{spot, name, marketable, id?, vendor_price?}`: add or replace (same
        name, case-insensitive) an operator item on a spot's loot list."""
        if not isinstance(arg, dict) or "spot" not in arg:
            raise ValueError("loot_item must be {spot, name, marketable, id?, vendor_price?}")
        item = _check_item({k: v for k, v in arg.items() if k != "spot"})
        with self._lock:
            doc = self._load()
            spot = self._spot_id(doc, arg["spot"])
            mine = doc["loot_items"].setdefault(spot, [])
            keep = [i for i in mine if i["name"].lower() != item["name"].lower()]
            if len(keep) >= MAX_LOOT_ITEMS:
                raise ValueError(f"at most {MAX_LOOT_ITEMS} loot items per spot")
            at = next((n for n, i in enumerate(mine)
                       if i["name"].lower() == item["name"].lower()), None)
            if at is None:
                mine.append(item)
            else:
                mine[at] = item
            self._save(doc)
        return self.view()

    def loot_forget(self, arg):
        """`{spot, name}`: drop an operator item (table items cannot be removed)."""
        arg = _fields(arg, "loot_forget", ("spot", "name"))
        name = _name(arg["name"]).lower()
        with self._lock:
            doc = self._load()
            spot = self._spot_id(doc, arg["spot"])
            mine = doc["loot_items"].get(spot, [])
            keep = [i for i in mine if i["name"].lower() != name]
            if len(keep) == len(mine):
                raise ValueError(f"no operator loot item named {arg['name']}")
            doc["loot_items"][spot] = keep
            self._save(doc)
        return self.view()

    # -- reads -----------------------------------------------------------------

    def view(self):
        """GET /api/grind body."""
        now = self._now()
        doc = self._load()
        active = doc["active"]
        if active is not None:
            elapsed = int((now - _parse_iso(active["started"])).total_seconds())
            active = dict(active, elapsed_s=max(0, elapsed))
        # Plan 039: a loot-valued session counts its loot value, else the typed silver.
        totals = {}
        for s in doc["sessions"]:
            lv = s["loot_value"]
            s["valued_silver"] = lv["total"] if lv is not None else s["silver"]
            s["silver_per_h"] = silver_per_hour(s["valued_silver"], s["minutes"])
            t = totals.setdefault(s["spot"], [0, 0, 0])
            t[0] += 1
            t[1] += s["minutes"]
            t[2] += s["valued_silver"]
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
        return {"now": _iso(now), "active": active, "sessions": sessions, "spots": spots,
                "buffs": buffs, "xp_presets": presets, "xp_presets_error": self.presets_error}

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

    @staticmethod
    def _with_loot(arg, what, keys):
        """Plan 039: stop / log take an optional `loot` list."""
        has = isinstance(arg, dict) and "loot" in arg
        return _fields(arg, what, keys + ("loot",) if has else keys), has

    def stop(self, arg):
        """`{silver, trash, loot?}`; minutes from active.started, clamped to
        1..MAX_MINUTES. Loot is priced before the lock (market reads may block)."""
        arg, has_loot = self._with_loot(arg, "stop", ("silver", "trash"))
        silver, trash = self._amounts(arg)
        doc = self._load()
        act = doc["active"]
        if act is None:
            raise ValueError("no active session")
        valued = self._resolve_loot(doc, act["spot"], arg["loot"]) if has_loot else None
        with self._lock:
            doc = self._load()
            if doc["active"] != act:
                raise ValueError("the active session changed; try again")
            secs = (self._now() - _parse_iso(act["started"])).total_seconds()
            minutes = min(MAX_MINUTES, max(1, int(secs // 60)))
            self._add_session(doc, act["spot"], act["started"], minutes, silver, trash, valued)
            doc["active"] = None
            self._save(doc)
        return self.view()

    def log(self, arg):
        """Manual `{spot, minutes, silver, trash, loot?}`; started = now - minutes."""
        arg, has_loot = self._with_loot(arg, "log", ("spot", "minutes", "silver", "trash"))
        minutes = _int(arg["minutes"], "minutes", 1, MAX_MINUTES)
        silver, trash = self._amounts(arg)
        doc = self._load()
        spot = self._spot_id(doc, arg["spot"])
        valued = self._resolve_loot(doc, spot, arg["loot"]) if has_loot else None
        with self._lock:
            doc = self._load()
            self._spot_id(doc, spot)
            started = _iso(self._now() - _dt.timedelta(minutes=minutes))
            self._add_session(doc, spot, started, minutes, silver, trash, valued)
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
