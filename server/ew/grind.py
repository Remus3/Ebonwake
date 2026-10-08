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
from fractions import Fraction
from pathlib import Path

from . import levels, market, patchverify, spots
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
AUTO_SPOT = "unspecified"  # plan 062: auto session spot before any session is logged
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
DROP_ROW_OPTIONAL = ("aliases", "rate_pct", "amount_pct", "note", "verify")  # 085: verify hint
CAPS_FIELDS = CAP_KEYS + ("source", "verified")
SCROLL_INTS = {"price_silver": (0, MAX_SILVER), "minutes": (1, MAX_MINUTES),
               "per_week": PER_WEEK_RANGE}
SCROLL_DATES = ("sale_until_utc", "removed_utc")
SCROLL_FIELDS = tuple(SCROLL_INTS) + SCROLL_DATES + ("source", "verified")
MAX_DROP_TEXT = 240

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


def _confirmed_on(patch):
    """Plan 085: the patch-notes date of a confirmed verdict, else None."""
    return patch["date"] if patch is not None and patch["verdict"] == "confirmed" else None


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
    if "verify" in r:
        patchverify.compile_hint(r["verify"], f"{r['id']}.verify", r.get("name"))


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

    def __init__(self, store, clock=time.time, presets=None, epoch=None,
                 drops_path=DROPS_FILE, loot_tables=None, prices=None, tax=None):
        self.store = store
        self.clock = clock
        # Plan 038 drop-buff table (tracked data); a bad file degrades to an
        # error on the card and refuses drop writes.
        self.drops, self.drops_error = None, None
        try:
            self.drops = load_drop_data(drops_path)
        except ValueError as e:
            self.drops_error = str(e)
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
        # Plan 062: `play() -> {state, id, start, spot}|None`, set by the app.
        self.play = None
        # Plan 081: ocr_loot(spot, started) -> the loot counts auto-OCR read during
        # that session ({source: "ocr", items, at}) or None; set by the app.
        self.ocr_loot = None
        # Plan 085: verdict(key) -> a tracked row's patch-notes verdict or None; set by the app.
        self.verdict = None
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
                  "armed": _iso_or_none(b.get("armed")),  # plan 046 summary window
                  "xp_pct": b.get("xp_pct") if _ok_int(b.get("xp_pct"), *XP_PCT_RANGE) else None}
                 for b in lst("buffs", _clean_named)]
        sessions = lst("sessions", _clean_session)
        act = doc.get("active")
        active = None
        if (isinstance(act, dict) and act.get("spot") in {s["id"] for s in spots}
                and _parse_iso(act.get("started"))):
            # Plan 062: `auto` marks a session opened by the play-session hook.
            active = {"spot": act["spot"], "started": act["started"],
                      "auto": act.get("auto") is True}
        nxt = doc.get("next_sid")
        top = max((int(s["id"][1:]) for s in sessions), default=0) + 1
        nxt = max(nxt, top) if _ok_int(nxt, 1, 10 ** 9 - 1) else top
        drop_on, overrides = self._clean_drops(doc.get("drop_on"), doc.get("drop_overrides"))
        loot_items = _clean_loot_items(doc.get("loot_items"), {s["id"] for s in spots})
        # Plan 046: a pending stop belongs to the session it was raised for; a
        # stale one (session stopped or replaced) is dropped on read.
        pend, pending = doc.get("pending_stop"), None
        if (active is not None and isinstance(pend, dict)
                and pend.get("started") == active["started"]):
            at = _iso_or_none(pend.get("at"))
            if at is not None and _parse_iso(at) >= _parse_iso(active["started"]):
                pending = {"at": at, "started": active["started"]}
        return {"spots": spots, "sessions": sessions, "active": active, "buffs": buffs,
                "next_sid": nxt, "drop_on": drop_on, "drop_overrides": overrides,
                "loot_items": loot_items, "pending_stop": pending}

    # -- drop buffs (plan 038) -------------------------------------------------

    def _patch(self, file, rid):
        """Plan 085 verdict of tracked row `<file>#<rid>`, or None (a fault: None)."""
        if self.verdict is None:
            return None
        try:
            return self.verdict(f"{file}#{rid}")
        except Exception:  # noqa: BLE001 - a verdict fault never breaks the view
            return None

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
            # plan 085: an operator override changes the value, so no verdict applies
            pv = None if r["overridden"] else self._patch(DROPS_FILE.name, r["id"])
            out_rows.append({"id": r["id"], "name": r["name"], "aliases": r.get("aliases", []),
                             "rate_pct": r.get("rate_pct"), "amount_pct": r.get("amount_pct"),
                             "bypass": r["bypass"], "source": r["source"],
                             "verified": _confirmed_on(pv) or r["verified"], "patch": pv,
                             "note": r.get("note"),
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
                "tax": t, "error": self.loot_error, "prefill": self._prefill(doc, spot, items)}

    def _prefill(self, doc, spot, items):
        """Plan 081: the running session's OCR loot counts for this spot (names
        on its list only), or None. Typing in the form only corrects them."""
        act = doc["active"]
        if self.ocr_loot is None or act is None or act["spot"] != spot:
            return None
        try:
            p = self.ocr_loot(spot, act["started"])
        except Exception:  # noqa: BLE001 - the prefill is an extra, never fatal
            return None
        if not isinstance(p, dict) or not isinstance(p.get("items"), list):
            return None
        names = {it["name"].lower(): it["name"] for it in items}
        rows = [dict(r, name=names[r["name"].lower()]) for r in p["items"]
                if isinstance(r, dict) and isinstance(r.get("name"), str)
                and r["name"].lower() in names]
        return dict(p, items=rows) if rows else None

    def loot_target(self):
        """Plan 081: {spot, started, names} of the running session (the auto-OCR
        reads its loot counts from each shot), or None."""
        doc = self._load()
        act = doc["active"]
        if act is None:
            return None
        names = [it["name"] for it in self._items(doc, act["spot"])]
        return {"spot": act["spot"], "started": act["started"], "names": names} if names else None

    def loot_candidates(self):
        """Plan 071: marketable loot ids of the active spot, else the newest
        session's spot, each with its count in that spot's newest loot-valued
        session (1 when none). Never prices, so never fetches."""
        doc = self._load()
        act = doc["active"]
        spot = act["spot"] if act else (doc["sessions"][-1]["spot"] if doc["sessions"] else None)
        if spot not in {s["id"] for s in doc["spots"]}:
            return {"spot": None, "items": []}
        counts = {}
        for ses in reversed(doc["sessions"]):
            if ses["spot"] == spot and ses.get("loot"):
                for it in ses["loot"]:
                    if it.get("id") is not None:
                        counts[it["id"]] = counts.get(it["id"], 0) + it["count"]
                break
        return {"spot": spot, "items": [
            {"id": it["id"], "name": it["name"], "count": counts.get(it["id"], 1)}
            for it in self._items(doc, spot) if it["marketable"] and it.get("id") is not None]}

    def loot_names(self, spot):
        """Plan 040: the spot's loot item names (table + operator) for the OCR
        loot import; ValueError for an unknown spot."""
        doc = self._load()
        self._spot_id(doc, spot)
        return [it["name"] for it in self._items(doc, spot)]

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
        presets = []
        for p in self.presets:
            pv = self._patch(levels.BUFFS_FILE.name, p["id"])
            presets.append({"name": p["name"],
                            "xp_pct": p["xp_pct"] if patched else p["pre_patch_xp_pct"],
                            "patched": patched, "notes": p["notes"], "source": p["source"],
                            "verified": _confirmed_on(pv) or p["verified"], "patch": pv})
        drops, roi = self._drops_view(doc, buffs, spots, now)
        pend = doc["pending_stop"]
        if pend is not None:
            secs = (_parse_iso(pend["at"]) - _parse_iso(pend["started"])).total_seconds()
            pend = dict(pend, spot=doc["active"]["spot"],
                        minutes=min(MAX_MINUTES, max(1, int(secs // 60))))
        return {"now": _iso(now), "active": active, "sessions": sessions, "spots": spots,
                "buffs": buffs, "xp_presets": presets, "xp_presets_error": self.presets_error,
                "drops": drops, "agris_roi": roi, "pending_stop": pend,
                "session": {"auto": active is not None and active["auto"],
                            "play": self._play()}}

    def _play(self):
        """Plan 062 `session.play` from the play-session hook, or None."""
        if self.play is None:
            return None
        try:
            return self.play()
        except Exception:  # noqa: BLE001 - the play pill is an extra, never fatal
            return None

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
        """`{silver, trash, loot?, at_exit?}`; minutes from active.started to now
        (plan 046 `at_exit: true`: to the pending game-exit time), clamped to
        1..MAX_MINUTES. Loot is priced before the lock (market reads may block)."""
        at_exit = isinstance(arg, dict) and "at_exit" in arg
        if at_exit:
            if arg["at_exit"] is not True:
                raise ValueError("at_exit must be true")
            arg = {k: v for k, v in arg.items() if k != "at_exit"}
        arg, has_loot = self._with_loot(arg, "stop", ("silver", "trash"))
        silver, trash = self._amounts(arg)
        doc = self._load()
        act = doc["active"]
        if act is None:
            raise ValueError("no active session")
        if at_exit and doc["pending_stop"] is None:
            raise ValueError("no pending game-exit stop")
        valued = self._resolve_loot(doc, act["spot"], arg["loot"]) if has_loot else None
        with self._lock:
            doc = self._load()
            if doc["active"] != act:
                raise ValueError("the active session changed; try again")
            end = self._now()
            if at_exit:
                if doc["pending_stop"] is None:
                    raise ValueError("no pending game-exit stop")
                end = _parse_iso(doc["pending_stop"]["at"])
            secs = (end - _parse_iso(act["started"])).total_seconds()
            minutes = min(MAX_MINUTES, max(1, int(secs // 60)))
            self._add_session(doc, act["spot"], act["started"], minutes, silver, trash, valued)
            doc["active"] = None
            doc["pending_stop"] = None
            self._save(doc)
        return self.view()

    # -- plan 046: game-exit pending stop -------------------------------------

    def mark_pending_stop(self, at):
        """Game exited at `at` (ISO) with a session open: flag it, never stop it.
        Returns False (no write) without an active session, for an auto session
        (plan 062 closes it itself), when one is already pending, or when `at`
        is before the session started."""
        when = _iso_or_none(at)
        if when is None:
            raise ValueError("pending stop needs an ISO time")
        with self._lock:
            doc = self._load()
            act = doc["active"]
            if (act is None or act["auto"] or doc["pending_stop"] is not None
                    or _parse_iso(when) < _parse_iso(act["started"])):
                return False
            doc["pending_stop"] = {"at": when, "started": act["started"]}
            self._save(doc)
        return True

    def clear_pending_stop(self):
        """The game came back up: a pending stop no longer describes the
        session (the next exit raises a fresh one). Returns whether one was set."""
        with self._lock:
            doc = self._load()
            if doc["pending_stop"] is None:
                return False
            doc["pending_stop"] = None
            self._save(doc)
        return True

    # -- plan 062: auto play-session binding ---------------------------------

    def auto_start(self, at):
        """Open an `auto` session at `at` (ISO) on the last used spot, else
        "unspecified" (added on demand). Returns the active {spot, started,
        auto} or None (no write) when a session is already running."""
        when = _iso_or_none(at)
        if when is None:
            raise ValueError("auto start needs an ISO time")
        with self._lock:
            doc = self._load()
            if doc["active"] is not None:
                return None
            ids = {s["id"] for s in doc["spots"]}
            spot = doc["sessions"][-1]["spot"] if doc["sessions"] else None
            if spot not in ids:
                spot = AUTO_SPOT
                if spot not in ids:
                    if len(doc["spots"]) >= MAX_SPOTS:
                        return None
                    doc["spots"].append({"id": AUTO_SPOT, "name": AUTO_SPOT})
            doc["active"] = {"spot": spot, "started": when, "auto": True}
            doc["pending_stop"] = None
            self._save(doc)
            return dict(doc["active"])

    def auto_stop(self, started, at):
        """Close the auto session that started at `started` at `at` (ISO), logged
        with 0 silver (a loot import may value it later). Returns False (no
        write) when that auto session is no longer the active one."""
        end = _parse_iso(at)
        if end is None:
            raise ValueError("auto stop needs an ISO time")
        with self._lock:
            doc = self._load()
            act = doc["active"]
            if act is None or not act["auto"] or act["started"] != started:
                return False
            secs = (end - _parse_iso(act["started"])).total_seconds()
            minutes = min(MAX_MINUTES, max(1, int(secs // 60)))
            self._add_session(doc, act["spot"], act["started"], minutes, 0, 0)
            doc["active"] = None
            doc["pending_stop"] = None
            self._save(doc)
        return True

    def keep(self, arg):
        """`{"keep": true}`: dismiss the pending stop; the session keeps running."""
        if arg is not True:
            raise ValueError("keep must be true")
        with self._lock:
            doc = self._load()
            if doc["pending_stop"] is None:
                raise ValueError("no pending game-exit stop")
            doc["pending_stop"] = None
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
            armed = _iso(self._now())
            ends = _iso(self._now() + _dt.timedelta(minutes=minutes))
            for b in doc["buffs"]:
                if b["name"].lower() == name.lower():
                    b["ends"], b["armed"] = ends, armed
                    if has_xp:
                        b["xp_pct"] = xp
                    break
            else:
                if len(doc["buffs"]) >= MAX_BUFFS:
                    raise ValueError(f"at most {MAX_BUFFS} buffs")
                bid = _unique(slug(name), {b["id"] for b in doc["buffs"]})
                doc["buffs"].append({"id": bid, "name": name, "ends": ends, "xp_pct": xp,
                                     "armed": armed})
            self._save(doc)
        return self.view()

    def restore_buff(self, name, ends, armed=None):
        """Plan 063 undo: put a buff timer back to `ends` / `armed` (ISO or None =
        disarmed) as it was before an auto-OCR commit. Not a POST op."""
        when = _iso_or_none(ends) if ends is not None else None
        with self._lock:
            doc = self._load()
            for b in doc["buffs"]:
                if isinstance(name, str) and b["name"].lower() == name.lower():
                    b["ends"] = when
                    b["armed"] = _iso_or_none(armed) if when is not None and armed else None
                    break
            else:
                raise ValueError(f"unknown buff: {name}")
            self._save(doc)
        return self.view()

    def clear_buff(self, bid):
        """Disarm a buff timer; the name stays listed for one-tap re-arming."""
        with self._lock:
            doc = self._load()
            for b in doc["buffs"]:
                if b["id"] == bid:
                    b["ends"] = b["armed"] = None
                    break
            else:
                raise ValueError(f"unknown buff: {bid}")
            self._save(doc)
        return self.view()
