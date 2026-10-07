"""Inventory / weight / storage planner + Value Pack ledger (plan 045).

Operator-typed (store domain `inventory`): base LT, the LT owned per candidate
source and the next upgrade's +LT / silver cost, inventory slots, per-town
storage notes and a log of market sales. The candidate sources, the Value Pack
bonus and the warehouse rule are tracked data (`data/weight_sources.json`,
research 0004 section 3); LT ranges are unverified, so an owned value outside a
range is a warning, never a rejection. VP state comes from the plan 005 buff
timer named "Value Pack" (else the plan 030 `market.vp` setting); the ledger
prices each logged sale with plan 027's `net_proceeds` at the fame stored on
the sale. Nothing is read from the game. A broken data file never breaks the
card: the view carries `error` and every write is refused.
"""

import datetime as _dt
import json
import re
import threading
import time
from fractions import Fraction
from pathlib import Path

from . import derived
from .market import FAME_MAX, net_proceeds
from .today import slug

DATA_FILE = Path(__file__).resolve().parent / "data" / "weight_sources.json"
ID_RE = re.compile(r"^[a-z0-9-]{1,40}$")
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
SALE_RE = re.compile(r"^s[1-9][0-9]{0,8}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
VP_BUFF = "value pack"  # plan 005 seeds this buff name (grind.SEED_BUFFS)
MAX_LT = 5000  # one source
MAX_BASE_LT = 20000
MAX_SLOTS = 400
MAX_TOWN_SLOTS = 10000
MAX_SILVER = 10 ** 13
MAX_NOTE = 120
MAX_NAME = 40
MAX_TOWNS = 40
MAX_SALES = 500
LEDGER_WINDOW_S = 30 * 86400
SET_FIELDS = ("base_lt", "slots", "slots_used", "fame_vt", "vp_cost")
TYPED_FIELDS = ("base_lt", "slots", "slots_used")  # plan 081: superseded by an OCR read
SOURCE_FIELDS = ("lt", "next_lt", "next_cost", "note")
TOWN_FIELDS = ("name", "used", "total", "note")


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _int_in(v, lo, hi):
    return _is_int(v) and lo <= v <= hi


def _ascii(v, max_len, empty=False):
    return (isinstance(v, str) and (empty or v.strip() != "") and len(v) <= max_len
            and all(32 <= ord(ch) <= 126 for ch in v))


def _iso(ts):
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).replace(microsecond=0).isoformat()


def _ts(s):
    if not isinstance(s, str):
        return None
    try:
        when = _dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    return when.timestamp() if when.tzinfo is not None else None


def _fame_ok(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= FAME_MAX


# -- data file -------------------------------------------------------------------

def validate_data(doc):
    """The tracked weight-sources doc, checked; raises ValueError."""
    if not isinstance(doc, dict):
        raise ValueError("weight data must be an object")
    s = doc.get("sources")
    if not (isinstance(s, dict) and s.get("verified") is False and _ascii(s.get("note"), 200)
            and isinstance(s.get("rows"), list) and s["rows"]):
        raise ValueError("weight data: sources needs rows, a note and verified false")
    seen = set()
    for r in s["rows"]:
        if not (isinstance(r, dict) and set(r) == {"id", "name", "min", "max", "verified"}
                and isinstance(r["id"], str) and KEY_RE.match(r["id"]) and r["id"] not in seen
                and _ascii(r["name"], 60) and isinstance(r["verified"], bool)
                and _int_in(r["min"], 0, MAX_LT) and _int_in(r["max"], r["min"], MAX_LT)):
            raise ValueError(f"weight data: bad source row {r!r}"[:200])
        seen.add(r["id"])
    vp = doc.get("value_pack")
    if not (isinstance(vp, dict) and all(_int_in(vp.get(k), 0, MAX_LT)
                                         for k in ("lt", "inventory_slots", "storage_slots"))
            and isinstance(vp.get("verified"), bool) and _ascii(vp.get("note"), 200)):
        raise ValueError("weight data: value_pack needs lt, slots, note and verified")
    wh = doc.get("warehouse")
    if not (isinstance(wh, dict) and all(_int_in(wh.get(k), 0, 10 ** 6)
                                         for k in ("base_vt", "fame_vt", "transfer_vt"))
            and _ascii(wh.get("source"), 200) and wh["source"].startswith("https://")
            and isinstance(wh.get("verified"), str) and DATE_RE.match(wh["verified"])):
        raise ValueError("weight data: warehouse needs VT numbers, an https source and a date")
    try:
        _dt.date.fromisoformat(wh["verified"])
    except ValueError:
        raise ValueError("weight data: warehouse verified is not a date") from None
    return doc


def load_data(path=None):
    """Tracked `data/weight_sources.json` -> validated doc; raises ValueError."""
    try:
        doc = json.loads(Path(path or DATA_FILE).read_text(encoding="ascii"))
    except (OSError, ValueError) as e:
        raise ValueError(f"weight data unreadable: {type(e).__name__}") from e
    return validate_data(doc)


_EMPTY = {"sources": {"rows": [], "note": "", "verified": False},
          "value_pack": {"lt": 0, "inventory_slots": 0, "storage_slots": 0, "note": "",
                         "verified": False},
          "warehouse": {"base_vt": 0, "fame_vt": 0, "transfer_vt": 0, "source": None,
                        "verified": None}}


# -- validation helpers ------------------------------------------------------------

def _opt_int(arg, key, lo, hi):
    v = arg[key]
    if v is not None and not _int_in(v, lo, hi):
        raise ValueError(f"{key} must be null or an int {lo}..{hi}")
    return v


def _note(arg):
    v = arg["note"]
    if not _ascii(v, MAX_NOTE, empty=True):
        raise ValueError(f"note must be 0..{MAX_NOTE} ASCII characters")
    return v.strip()


def _shape(arg, allowed, what, need=None):
    if not isinstance(arg, dict) or set(arg) - set(allowed) or not set(arg) & set(need or allowed):
        raise ValueError(f"{what} must be {{{'|'.join(allowed)}}}")


# -- service ---------------------------------------------------------------------

class InventoryService:
    """Store domain `inventory`: {"base_lt", "slots", "slots_used", "fame_vt",
    "vp_cost", "sources": {id: {lt, next_lt, next_cost, note}}, "towns":
    [{id, name, used, total, note}], "sales": [{id, at, price, vp, fame_pct}],
    "next_sale", "updated"}."""

    def __init__(self, store, clock=time.time, data_path=None, buffs=None, tax=None, reads=None,
                 fame=None):
        self.store = store
        self.clock = clock
        # Plan 081: fame() -> the fame bonus in force {value, source, at, age_s,
        # stale} (source + age beside the net-proceeds figures), or None.
        self.fame = fame or (lambda: None)
        # Plan 081: reads(kind) -> the newest committed OCR read {value, at, source}
        # of kind "weight" ({used, max} LT) / "slots" ({used, total}), or None.
        self.reads = reads or (lambda kind: None)
        self.buffs = buffs or (lambda: [])
        self.tax = tax or (lambda: {"vp": False, "fame_pct": 0})
        try:
            self.data, self.error = load_data(data_path), None
        except ValueError as e:
            self.data, self.error = _EMPTY, str(e)
        self.rows = {r["id"]: r for r in self.data["sources"]["rows"]}
        self._lock = threading.Lock()

    # -- load / save -----------------------------------------------------------------

    @staticmethod
    def _clean_town(t):
        if not (isinstance(t, dict) and isinstance(t.get("id"), str) and ID_RE.match(t["id"])
                and _ascii(t.get("name"), MAX_NAME)):
            return None
        used = t.get("used") if _int_in(t.get("used"), 0, MAX_TOWN_SLOTS) else None
        total = t.get("total") if _int_in(t.get("total"), 1, MAX_TOWN_SLOTS) else None
        if used is not None and total is not None and used > total:
            used = None
        note = t.get("note") if _ascii(t.get("note"), MAX_NOTE, empty=True) else ""
        return {"id": t["id"], "name": t["name"].strip(), "used": used, "total": total,
                "note": note}

    @staticmethod
    def _clean_sale(s):
        if not (isinstance(s, dict) and isinstance(s.get("id"), str) and SALE_RE.match(s["id"])
                and _ts(s.get("at")) is not None and _int_in(s.get("price"), 1, MAX_SILVER)
                and isinstance(s.get("vp"), bool) and _fame_ok(s.get("fame_pct"))):
            return None
        return {k: s[k] for k in ("id", "at", "price", "vp", "fame_pct")}

    def _load(self):
        doc = self.store.get("inventory")
        doc = doc if isinstance(doc, dict) else {}
        out = {"base_lt": doc.get("base_lt") if _int_in(doc.get("base_lt"), 0, MAX_BASE_LT)
               else None,
               "slots": doc.get("slots") if _int_in(doc.get("slots"), 1, MAX_SLOTS) else None,
               "fame_vt": doc.get("fame_vt") is True,
               "vp_cost": doc.get("vp_cost") if _int_in(doc.get("vp_cost"), 0, MAX_SILVER)
               else None}
        used = doc.get("slots_used")
        out["slots_used"] = used if _int_in(used, 0, MAX_SLOTS) else None
        ta = doc.get("typed_at") if isinstance(doc.get("typed_at"), dict) else {}
        out["typed_at"] = {k: ta[k] for k in TYPED_FIELDS
                           if out[k] is not None and _ts(ta.get(k)) is not None}
        raw = doc.get("sources")
        sources = {}
        for sid, v in (raw.items() if isinstance(raw, dict) else ()):
            if sid not in self.rows or not isinstance(v, dict):
                continue
            sources[sid] = {
                "lt": v.get("lt") if _int_in(v.get("lt"), 0, MAX_LT) else None,
                "next_lt": v.get("next_lt") if _int_in(v.get("next_lt"), 1, MAX_LT) else None,
                "next_cost": v.get("next_cost") if _int_in(v.get("next_cost"), 0, MAX_SILVER)
                else None,
                "note": v.get("note").strip() if _ascii(v.get("note"), MAX_NOTE, empty=True)
                else ""}
        out["sources"] = sources

        def lst(key, clean, cap):
            items, seen = [], set()
            for c in map(clean, doc.get(key) if isinstance(doc.get(key), list) else []):
                if c is not None and c["id"] not in seen:
                    seen.add(c["id"])
                    items.append(c)
            return items[-cap:]

        out["towns"] = lst("towns", self._clean_town, MAX_TOWNS)
        out["sales"] = lst("sales", self._clean_sale, MAX_SALES)
        top = max((int(s["id"][1:]) for s in out["sales"]), default=0) + 1
        nxt = doc.get("next_sale")
        out["next_sale"] = max(nxt, top) if _int_in(nxt, 1, 10 ** 9 - 1) else top
        return out

    def _save(self, doc):
        doc["sales"] = doc["sales"][-MAX_SALES:]
        doc["updated"] = _iso(self.clock())
        self.store.put("inventory", doc)

    def _writable(self):
        if self.error:
            raise ValueError(f"weight data unavailable: {self.error}")

    # -- VP state ----------------------------------------------------------------------

    def _vp_state(self):
        """{active, from, ends, left_s}: an armed plan 005 "Value Pack" timer wins;
        else the plan 030 market.vp setting (no expiry known)."""
        try:
            buffs = self.buffs() or []
        except Exception:  # noqa: BLE001 - a grind failure never breaks this card
            buffs = []
        for b in buffs if isinstance(buffs, list) else []:
            if (isinstance(b, dict) and isinstance(b.get("name"), str)
                    and b["name"].strip().lower() == VP_BUFF
                    and _int_in(b.get("left_s"), 1, 10 ** 9)):
                return {"active": True, "from": "buff", "ends": b.get("ends"),
                        "left_s": b["left_s"]}
        if self._settings()["vp"]:
            return {"active": True, "from": "settings", "ends": None, "left_s": None}
        return {"active": False, "from": None, "ends": None, "left_s": None}

    def _settings(self):
        try:
            t = self.tax() or {}
        except Exception:  # noqa: BLE001
            t = {}
        fame = t.get("fame_pct") if isinstance(t, dict) else None
        return {"vp": isinstance(t, dict) and t.get("vp") is True,
                "fame_pct": fame if _fame_ok(fame) else 0}

    # -- view --------------------------------------------------------------------------

    def _source_view(self, r, own):
        lt = own.get("lt")
        warn = ""
        if lt is not None and lt > r["max"]:
            warn = f"above the candidate range {r['min']}..{r['max']} (unverified)"
        elif lt is not None and 0 < lt < r["min"]:
            warn = f"below the candidate range {r['min']}..{r['max']} (unverified)"
        return {"id": r["id"], "name": r["name"], "min": r["min"], "max": r["max"],
                "verified": r["verified"], "owned_lt": lt, "next_lt": own.get("next_lt"),
                "next_cost": own.get("next_cost"), "note": own.get("note", ""), "warn": warn}

    def _ledger(self, doc, now):
        sales, total, recent = [], 0, 0
        for s in reversed(doc["sales"]):
            net = net_proceeds(s["price"], s["vp"], s["fame_pct"])
            gain = net - net_proceeds(s["price"], False, s["fame_pct"]) if s["vp"] else 0
            total += gain
            if now - _ts(s["at"]) <= LEDGER_WINDOW_S:
                recent += gain
            sales.append(dict(s, net=net, gain=gain))
        cost = doc["vp_cost"]
        return {"sales": sales, "count": len(sales), "gain_total": total, "gain_30d": recent,
                "vp_cost": cost, "net_30d": recent - cost if cost is not None else None}

    def _read(self, kind):
        try:
            r = self.reads(kind)
        except Exception:  # noqa: BLE001 - a broken OCR feed falls back to the typed values
            return None
        return r if isinstance(r, dict) and isinstance(r.get("value"), dict) else None

    def _inputs(self, doc, owned, vp_lt, vp_slots, now):
        """Plan 081: base LT, base slots and slots used as derived.pick() rows
        (an OCR read newer than the typed value supersedes it) + the weight now.
        The weight read "x / y LT" gives base LT = y - owned sources - VP LT."""
        w, s = self._read("weight"), self._read("slots")
        live = {}
        weight = None
        if w is not None:
            mx, used = w["value"].get("max"), w["value"].get("used")
            base = int(round(mx - owned - vp_lt)) if isinstance(mx, (int, float)) else None
            if base is not None and 0 <= base <= MAX_BASE_LT:
                live["base_lt"] = dict(w, value=base)
            weight = dict(derived.pick(None, w, now), used=used, max=mx)
            weight.pop("value", None)
        if s is not None:
            tot, used = s["value"].get("total"), s["value"].get("used")
            if _int_in(tot, 1, MAX_SLOTS) and _int_in(tot - vp_slots, 1, MAX_SLOTS):
                live["slots"] = dict(s, value=tot - vp_slots)
            if _int_in(used, 0, MAX_SLOTS):
                live["slots_used"] = dict(s, value=used)
        out = {}
        for k in TYPED_FIELDS:
            typed = {"value": doc[k], "at": doc["typed_at"].get(k)} if doc[k] is not None else None
            out[k] = derived.pick(typed, live.get(k), now)
        return out, weight

    def typed_overrides(self):
        """Plan 081: typed base LT / slots in force, as /api/overrides rows."""
        with self._lock:
            doc = self._load()
        v = self._view(doc)
        labels = {"base_lt": "Base LT (typed)", "slots": "Inventory slots (typed)",
                  "slots_used": "Slots used (typed)"}
        return [{"key": f"inventory.{k}", "label": labels[k], "value": row["value"],
                 "set_at": row["at"], "reason": "the next inventory screenshot supersedes it",
                 "cards": ["inventory"]}
                for k, row in v["inputs"].items() if row["source"] == "typed"]

    def _view(self, doc):
        now = self.clock()
        vpd = self.data["value_pack"]
        vp = self._vp_state()
        vp.update(lt=vpd["lt"], inventory_slots=vpd["inventory_slots"],
                  storage_slots=vpd["storage_slots"], verified=vpd["verified"],
                  note=vpd["note"],
                  reminder=None if vp["active"] else
                  (f"Value Pack off: +{vpd['lt']} LT / +{vpd['inventory_slots']} inventory"
                   f" / +{vpd['storage_slots']} storage slots unused"))
        sources = [self._source_view(r, doc["sources"].get(r["id"], {}))
                   for r in self.data["sources"]["rows"]]
        owned = sum(s["owned_lt"] or 0 for s in sources)
        vp_lt = vpd["lt"] if vp["active"] else 0
        nxt = sorted(({"id": s["id"], "name": s["name"], "next_lt": s["next_lt"],
                       "next_cost": s["next_cost"],
                       "cost_per_lt": s["next_cost"] // s["next_lt"]}
                      for s in sources if s["next_lt"] and s["next_cost"] is not None),
                     key=lambda n: (Fraction(n["next_cost"], n["next_lt"]), n["next_cost"]))
        vp_slots = vpd["inventory_slots"] if vp["active"] else 0
        inputs, weight = self._inputs(doc, owned, vp_lt, vp_slots, now)
        base_lt, base_slots, used = (inputs[k]["value"] for k in TYPED_FIELDS)
        total = base_slots + vp_slots if base_slots is not None else None
        wh = self.data["warehouse"]
        towns = [dict(t, free=t["total"] - t["used"]
                      if t["total"] is not None and t["used"] is not None else None)
                 for t in doc["towns"]]
        return {"sources": sources, "sources_note": self.data["sources"]["note"],
                "base_lt": base_lt, "lt_owned": owned, "vp_lt": vp_lt,
                "lt_total": base_lt + owned + vp_lt if base_lt is not None else None,
                "inputs": inputs, "weight_now": weight,
                "next_cheapest": nxt,
                "slots": {"base": base_slots, "used": used, "vp": vp_slots, "total": total,
                          "free": total - used if total is not None and used is not None
                          else None},
                "warehouse": {"vt": wh["base_vt"] + (wh["fame_vt"] if doc["fame_vt"] else 0),
                              "fame": doc["fame_vt"], "base_vt": wh["base_vt"],
                              "fame_vt": wh["fame_vt"], "transfer_vt": wh["transfer_vt"],
                              "source": wh["source"], "verified": wh["verified"]},
                "towns": towns,
                "town_slots": {"used": sum(t["used"] or 0 for t in towns),
                               "total": sum(t["total"] or 0 for t in towns)},
                "vp": vp, "ledger": self._ledger(doc, now), "fame": self._fame(),
                "error": self.error}

    def _fame(self):
        try:
            f = self.fame()
        except Exception:  # noqa: BLE001 - the source line is an extra, never fatal
            return None
        return f if isinstance(f, dict) else None

    def view(self):
        """GET /api/inventory body."""
        with self._lock:
            doc = self._load()
        return self._view(doc)

    # -- writes (each returns the GET body) -----------------------------------------------

    def set(self, arg):
        """{base_lt?, slots?, slots_used?, fame_vt?, vp_cost?}; null clears a number."""
        self._writable()
        _shape(arg, SET_FIELDS, "set")
        f = {}
        if "base_lt" in arg:
            f["base_lt"] = _opt_int(arg, "base_lt", 0, MAX_BASE_LT)
        if "slots" in arg:
            f["slots"] = _opt_int(arg, "slots", 1, MAX_SLOTS)
        if "slots_used" in arg:
            f["slots_used"] = _opt_int(arg, "slots_used", 0, MAX_SLOTS)
        if "vp_cost" in arg:
            f["vp_cost"] = _opt_int(arg, "vp_cost", 0, MAX_SILVER)
        if "fame_vt" in arg:
            if not isinstance(arg["fame_vt"], bool):
                raise ValueError("fame_vt must be a bool")
            f["fame_vt"] = arg["fame_vt"]
        with self._lock:
            doc = self._load()
            doc.update(f)
            for k in TYPED_FIELDS:
                if k in f:
                    if f[k] is None:
                        doc["typed_at"].pop(k, None)
                    else:
                        doc["typed_at"][k] = _iso(self.clock())
            self._save(doc)
            return self._view(doc)

    def source(self, arg):
        """{id, lt?|next_lt?|next_cost?|note?} for one candidate LT source."""
        self._writable()
        _shape(arg, ("id",) + SOURCE_FIELDS, "source", need=SOURCE_FIELDS)
        if not isinstance(arg.get("id"), str) or arg["id"] not in self.rows:
            raise ValueError("source id must be one of " + ", ".join(self.rows))
        f = {}
        if "lt" in arg:
            f["lt"] = _opt_int(arg, "lt", 0, MAX_LT)
        if "next_lt" in arg:
            f["next_lt"] = _opt_int(arg, "next_lt", 1, MAX_LT)
        if "next_cost" in arg:
            f["next_cost"] = _opt_int(arg, "next_cost", 0, MAX_SILVER)
        if "note" in arg:
            f["note"] = _note(arg)
        with self._lock:
            doc = self._load()
            row = doc["sources"].setdefault(arg["id"], {"lt": None, "next_lt": None,
                                                       "next_cost": None, "note": ""})
            row.update(f)
            self._save(doc)
            return self._view(doc)

    @staticmethod
    def _town_fields(arg, base):
        t = dict(base)
        if "name" in arg:
            if not _ascii(arg["name"], MAX_NAME):
                raise ValueError(f"name must be 1..{MAX_NAME} ASCII characters")
            t["name"] = arg["name"].strip()
        if "used" in arg:
            t["used"] = _opt_int(arg, "used", 0, MAX_TOWN_SLOTS)
        if "total" in arg:
            t["total"] = _opt_int(arg, "total", 1, MAX_TOWN_SLOTS)
        if "note" in arg:
            t["note"] = _note(arg)
        if t["used"] is not None and t["total"] is not None and t["used"] > t["total"]:
            raise ValueError("used must not exceed total")
        return t

    @staticmethod
    def _find(items, iid, what):
        for x in items:
            if isinstance(iid, str) and x["id"] == iid:
                return x
        raise ValueError(f"unknown {what}: {iid!r}"[:80])

    def town_add(self, arg):
        """{name, used?, total?, note?}."""
        self._writable()
        _shape(arg, TOWN_FIELDS, "town_add", need=("name",))
        t = self._town_fields(arg, {"name": None, "used": None, "total": None, "note": ""})
        with self._lock:
            doc = self._load()
            if len(doc["towns"]) >= MAX_TOWNS:
                raise ValueError(f"at most {MAX_TOWNS} towns")
            base = slug(t["name"]) or "town"
            tid, n, taken = base, 2, {x["id"] for x in doc["towns"]}
            while tid in taken:
                suffix = f"-{n}"
                tid, n = base[:40 - len(suffix)].rstrip("-") + suffix, n + 1
            doc["towns"].append(dict({"id": tid}, **t))
            self._save(doc)
            return self._view(doc)

    def town_edit(self, arg):
        """{id, name?|used?|total?|note?}."""
        self._writable()
        _shape(arg, ("id",) + TOWN_FIELDS, "town_edit", need=TOWN_FIELDS)
        with self._lock:
            doc = self._load()
            t = self._find(doc["towns"], arg.get("id"), "town")
            t.update(self._town_fields(arg, t))
            self._save(doc)
            return self._view(doc)

    def town_del(self, tid):
        self._writable()
        with self._lock:
            doc = self._load()
            doc["towns"].remove(self._find(doc["towns"], tid, "town"))
            self._save(doc)
            return self._view(doc)

    def sale(self, arg):
        """{price, vp?}: one market sale the operator collected; vp defaults to the
        live VP state, fame_pct is the plan 030 setting at log time."""
        self._writable()
        _shape(arg, ("price", "vp"), "sale", need=("price",))
        if not _int_in(arg["price"], 1, MAX_SILVER):
            raise ValueError(f"price must be an int 1..{MAX_SILVER}")
        if "vp" in arg and not isinstance(arg["vp"], bool):
            raise ValueError("vp must be a bool")
        vp = arg["vp"] if "vp" in arg else self._vp_state()["active"]
        fame = self._settings()["fame_pct"]
        with self._lock:
            doc = self._load()
            doc["sales"].append({"id": f"s{doc['next_sale']}", "at": _iso(self.clock()),
                                 "price": arg["price"], "vp": vp, "fame_pct": fame})
            doc["next_sale"] += 1
            self._save(doc)
            return self._view(doc)

    def sale_del(self, sid):
        self._writable()
        with self._lock:
            doc = self._load()
            doc["sales"].remove(self._find(doc["sales"], sid, "sale"))
            self._save(doc)
            return self._view(doc)
