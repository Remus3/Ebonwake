"""Imperial crafting delivery planner (plan 053): daily boxes = CP / 2 per
type (cooking, alchemy), box payout = 250 percent of the packed items' market
price (no tax, operator-typed mastery bonus on top), best boxes by payout per
input silver, and the midnight-server-time reset countdown.

Rules and seed boxes come from the tracked, sourced `data/imperial_boxes.json`
(official wiki 133). CP comes from plan 042's Life & CP card when the profile
shows it, else the operator's typed value. Prices are the market cache only
(never fetched here): payout on the item's base price, input cost on its last
sold price. Delivered counts are operator ticks; nothing is read from the game.
"""

import datetime as _dt
import json
import math
import re
import threading
import time
from fractions import Fraction
from pathlib import Path

from . import today
from .today import slug

DATA_PATH = Path(__file__).resolve().parent / "data" / "imperial_boxes.json"
TYPES = ("cooking", "alchemy")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
BOX_ID_RE = re.compile(r"^(cooking|alchemy)-[a-z0-9-]{1,40}$")
RULE_FIELDS = {"types", "cap_divisor", "payout_pct", "taxed", "reset", "note", "source",
               "verified"}
BOX_FIELDS = {"type", "name", "items", "source", "verified"}
MAX_ID = 2 ** 31 - 1
MAX_NAME = 40
MAX_ITEMS = 10
QTY_MAX = 9999
CP_MAX = 10000
COUNT_MAX = CP_MAX  # no cap is known without CP: clamp the tick count here
MASTERY_MAX = 500
MAX_BOXES = 60
BEST_N = 3


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v):
    return (_is_int(v) or isinstance(v, float)) and not isinstance(v, bool) and math.isfinite(v)


def _ascii(v, max_len, allow_empty=False):
    return (isinstance(v, str) and len(v) <= max_len and (allow_empty or v.strip() != "")
            and all(32 <= ord(ch) <= 126 for ch in v))


def _verified_ok(v):
    if v is False:
        return True
    if not isinstance(v, str) or not DATE_RE.match(v):
        return False
    try:
        _dt.date.fromisoformat(v)
    except ValueError:
        return False
    return True


def _source_ok(r):
    src = r.get("source")
    return _ascii(src, 200) and src.startswith("https://") and _verified_ok(r.get("verified"))


# -- data file -------------------------------------------------------------------

def validate_items(items):
    """[{id, qty}] (1..MAX_ITEMS, unique ids) or ValueError."""
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_ITEMS:
        raise ValueError(f"items must be a list of 1..{MAX_ITEMS} {{id, qty}}")
    seen = set()
    for it in items:
        if not isinstance(it, dict) or set(it) != {"id", "qty"}:
            raise ValueError("each item must be exactly {id, qty}")
        if not _is_int(it["id"]) or not 1 <= it["id"] <= MAX_ID or it["id"] in seen:
            raise ValueError(f"item id must be a unique int in 1..{MAX_ID}")
        if not _is_int(it["qty"]) or not 1 <= it["qty"] <= QTY_MAX:
            raise ValueError(f"item qty must be an int in 1..{QTY_MAX}")
        seen.add(it["id"])
    return [{"id": it["id"], "qty": it["qty"]} for it in items]


def box_id(box_type, name):
    return f"{box_type}-{slug(name)}"[:48]


def validate_data(doc):
    """The tracked imperial_boxes.json, checked; ValueError on any malformed row."""
    if not isinstance(doc, dict) or set(doc) != {"rules", "boxes"}:
        raise ValueError("imperial data must be {rules, boxes}")
    r = doc["rules"]
    if not isinstance(r, dict) or set(r) != RULE_FIELDS:
        raise ValueError(f"rules must be {{{', '.join(sorted(RULE_FIELDS))}}}")
    if r["types"] != list(TYPES):
        raise ValueError(f"rules.types must be {list(TYPES)}")
    if not _is_int(r["cap_divisor"]) or not 1 <= r["cap_divisor"] <= 100:
        raise ValueError("rules.cap_divisor must be an int in 1..100")
    if not _is_num(r["payout_pct"]) or not 0 < r["payout_pct"] <= 1000:
        raise ValueError("rules.payout_pct must be a number in (0, 1000]")
    if r["taxed"] is not False:
        raise ValueError("rules.taxed must be false (imperial delivery pays untaxed)")
    rule = today.validate_rule(r["reset"])
    if rule["every"] != "day":
        raise ValueError("rules.reset must be a daily rule")
    if not _ascii(r["note"], 300, allow_empty=True) or not _source_ok(r):
        raise ValueError("rules note / source (https) / verified (date or false) invalid")
    boxes = doc["boxes"]
    if not isinstance(boxes, list) or len(boxes) > MAX_BOXES:
        raise ValueError(f"boxes must be a list of at most {MAX_BOXES}")
    ids = set()
    for b in boxes:
        if not isinstance(b, dict) or set(b) != BOX_FIELDS:
            raise ValueError(f"each box must be {{{', '.join(sorted(BOX_FIELDS))}}}")
        if b["type"] not in TYPES or not _ascii(b["name"], MAX_NAME) or not _source_ok(b):
            raise ValueError(f"box type / name / source / verified invalid: {b.get('name')!r}"[:200])
        validate_items(b["items"])
        bid = box_id(b["type"], b["name"])
        if bid in ids:
            raise ValueError(f"duplicate box: {bid}")
        ids.add(bid)
    return doc


def load_data(path=None):
    p = Path(DATA_PATH if path is None else path)
    try:
        doc = json.loads(p.read_text(encoding="ascii"))
    except (OSError, ValueError) as e:  # ValueError covers UnicodeDecodeError
        raise ValueError(f"{p.name}: {e}"[:200]) from e
    return validate_data(doc)


# -- math ------------------------------------------------------------------------

def daily_cap(cp, divisor=2):
    """Boxes per type per day: floor(CP / divisor); None when CP is unknown."""
    if not _is_num(cp) or cp < 0:
        return None
    return int(cp) // divisor


def _quote(prices, item_id):
    q = prices(item_id) if callable(prices) else (prices or {}).get(item_id)
    if _is_int(q) and q > 0:
        return q, q
    if not isinstance(q, dict):
        return None, None
    base, last = q.get("base"), q.get("last")
    base = base if _is_int(base) and base > 0 else None
    last = last if _is_int(last) and last > 0 else None
    return base or last, last or base


def box_value(box, prices, payout_pct=250, mastery_pct=0):
    """One box -> {payout, cost, ratio, missing}. payout = payout_pct percent of
    the items' base price x qty, times (1 + mastery_pct / 100), floored to
    silver; cost = the items' last sold price x qty (what buying them costs);
    ratio = payout / cost. `prices` maps item id -> {base, last} (or one int
    for both); an item with no price lands in `missing` and nulls the totals."""
    base_sum = cost_sum = 0
    missing = []
    for it in box["items"]:
        base, last = _quote(prices, it["id"])
        if base is None:
            missing.append(it["id"])
            continue
        base_sum += base * it["qty"]
        cost_sum += last * it["qty"]
    if missing:
        return {"payout": None, "cost": None, "ratio": None, "missing": missing}
    pay = Fraction(str(payout_pct)) / 100 * (1 + Fraction(str(mastery_pct)) / 100)
    payout = math.floor(base_sum * pay)
    return {"payout": payout, "cost": cost_sum,
            "ratio": round(payout / cost_sum, 3) if cost_sum else None, "missing": []}


def best_boxes(rows, n=BEST_N):
    """Valued box rows of one type -> the top n ids by payout per input silver
    (ties: higher payout, then name); unpriced boxes never rank."""
    ok = [r for r in rows if r["ratio"] is not None]
    ok.sort(key=lambda r: (-r["ratio"], -r["payout"], r["name"]))
    return [r["id"] for r in ok[:n]]


def _iso(when):
    return when.astimezone(_dt.timezone.utc).replace(microsecond=0).isoformat()


# -- service ---------------------------------------------------------------------

class ImperialService:
    """Store domain `imperial`: {"day": last reset date, "delivered": {type: n},
    "cp": int | null, "mastery": {type: pct}, "boxes": [{id, type, name,
    items}], "updated"}. Delivered counts belong to `day`; a later reset reads
    them as zero. Every write returns the GET body."""

    def __init__(self, store, clock=time.time, cp=None, prices=None, name=None,
                 data_path=None):
        self.store = store
        self.clock = clock
        self.cp_source = cp or (lambda: None)
        self.prices = prices or (lambda iid: None)
        self.name = name or (lambda iid: None)
        self._lock = threading.Lock()
        try:
            self.data, self.data_error = load_data(data_path), None
        except ValueError as e:  # a broken data file never breaks the card
            self.data, self.data_error = None, str(e)
        rules = self.data["rules"] if self.data else {}
        self.divisor = rules.get("cap_divisor", 2)
        self.payout_pct = rules.get("payout_pct", 250)
        self.rule = today.validate_rule(rules.get("reset") or {"every": "day"})

    # -- clock -----------------------------------------------------------------

    def _now(self):
        return _dt.datetime.fromtimestamp(self.clock(), _dt.timezone.utc)

    def _day(self, now):
        return today.last_reset(self.rule, now).date().isoformat()

    # -- load / save -----------------------------------------------------------

    def _load(self, day):
        doc = self.store.get("imperial")
        same = doc.get("day") == day
        raw = doc.get("delivered") if same and isinstance(doc.get("delivered"), dict) else {}
        delivered = {t: raw[t] if _is_int(raw.get(t)) and 0 <= raw[t] <= COUNT_MAX else 0
                     for t in TYPES}
        cp = doc.get("cp")
        cp = cp if _is_int(cp) and 0 <= cp <= CP_MAX else None
        m = doc.get("mastery") if isinstance(doc.get("mastery"), dict) else {}
        mastery = {t: m[t] if _is_num(m.get(t)) and 0 <= m[t] <= MASTERY_MAX else 0
                   for t in TYPES}
        boxes, seen = [], set()
        for b in doc.get("boxes") if isinstance(doc.get("boxes"), list) else []:
            try:
                ok = (isinstance(b, dict) and b.get("type") in TYPES
                      and _ascii(b.get("name"), MAX_NAME) and isinstance(b.get("id"), str)
                      and b["id"] == box_id(b["type"], b["name"]) and b["id"] not in seen)
                items = validate_items(b.get("items")) if ok else None
            except ValueError:
                items = None
            if items is not None:
                seen.add(b["id"])
                boxes.append({"id": b["id"], "type": b["type"], "name": b["name"].strip(),
                              "items": items})
        return {"day": day, "delivered": delivered, "cp": cp, "mastery": mastery,
                "boxes": boxes[:MAX_BOXES]}

    def _write(self, fn):
        with self._lock:
            now = self._now()
            doc = self._load(self._day(now))
            fn(doc)
            self.store.put("imperial", dict(doc, updated=_iso(now)))
        return self.view()

    def _cp(self, doc):
        try:
            v = self.cp_source()
        except Exception:  # noqa: BLE001 - a broken profile never breaks the card
            v = None
        if _is_num(v) and 0 <= v <= CP_MAX:
            return int(v), "profile"
        if doc["cp"] is not None:
            return doc["cp"], "operator"
        return None, None

    def _cap(self, doc):
        return daily_cap(self._cp(doc)[0], self.divisor)

    @staticmethod
    def _type(t):
        if t not in TYPES:
            raise ValueError(f"type must be one of {', '.join(TYPES)}")
        return t

    # -- writes ----------------------------------------------------------------

    def deliver(self, arg):
        """{type, add}: add (or, negative, take back) delivered boxes today;
        the count clamps to 0..today's cap (COUNT_MAX when CP is unknown)."""
        if not isinstance(arg, dict) or set(arg) != {"type", "add"}:
            raise ValueError("deliver must be {type, add}")
        t = self._type(arg["type"])
        add = arg["add"]
        if not _is_int(add) or add == 0 or abs(add) > COUNT_MAX:
            raise ValueError(f"add must be a non-zero int in -{COUNT_MAX}..{COUNT_MAX}")

        def go(doc):
            cap = self._cap(doc)
            top = COUNT_MAX if cap is None else cap
            doc["delivered"][t] = max(0, min(top, doc["delivered"][t] + add))
        return self._write(go)

    def set_cp(self, cp):
        """Operator-typed contribution points (used while the profile hides CP)."""
        if cp is not None and (not _is_int(cp) or not 0 <= cp <= CP_MAX):
            raise ValueError(f"cp must be null or an int in 0..{CP_MAX}")
        return self._write(lambda doc: doc.update(cp=cp))

    def set_mastery(self, arg):
        """{type, pct}: the operator's mastery bonus on imperial payouts."""
        if not isinstance(arg, dict) or set(arg) != {"type", "pct"}:
            raise ValueError("mastery must be {type, pct}")
        t = self._type(arg["type"])
        pct = arg["pct"]
        if not _is_num(pct) or not 0 <= pct <= MASTERY_MAX:
            raise ValueError(f"pct must be a number in 0..{MASTERY_MAX}")
        pct = round(float(pct), 2)
        pct = int(pct) if pct == int(pct) else pct
        return self._write(lambda doc: doc["mastery"].update({t: pct}))

    def box_add(self, arg):
        """{type, name, items: [{id, qty}]}: an operator box row."""
        if not isinstance(arg, dict) or set(arg) != {"type", "name", "items"}:
            raise ValueError("box_add must be {type, name, items}")
        t = self._type(arg["type"])
        if not _ascii(arg["name"], MAX_NAME):
            raise ValueError(f"name must be 1..{MAX_NAME} printable ASCII characters")
        name = arg["name"].strip()
        items = validate_items(arg["items"])
        bid = box_id(t, name)
        seeded = {box_id(b["type"], b["name"]) for b in (self.data or {}).get("boxes", [])}

        def go(doc):
            if bid in seeded or any(b["id"] == bid for b in doc["boxes"]):
                raise ValueError(f"box already listed: {bid}")
            if len(doc["boxes"]) >= MAX_BOXES:
                raise ValueError(f"at most {MAX_BOXES} operator boxes")
            doc["boxes"].append({"id": bid, "type": t, "name": name, "items": items})
        return self._write(go)

    def box_del(self, bid):
        """Remove an operator box (tracked seed rows are edited in the data file)."""
        if not isinstance(bid, str) or not BOX_ID_RE.match(bid):
            raise ValueError("box id must be <type>-<slug>")

        def go(doc):
            keep = [b for b in doc["boxes"] if b["id"] != bid]
            if len(keep) == len(doc["boxes"]):
                raise ValueError(f"unknown operator box: {bid}")
            doc["boxes"] = keep
        return self._write(go)

    # -- read ------------------------------------------------------------------

    def _row(self, b, origin, mastery, source=None, verified=None):
        v = box_value(b, self.prices, self.payout_pct, mastery)
        items = []
        for it in b["items"]:
            try:
                nm = self.name(it["id"])
            except Exception:  # noqa: BLE001 - a broken name index never breaks the card
                nm = None
            items.append({"id": it["id"], "qty": it["qty"],
                          "name": nm if isinstance(nm, str) else None})
        return dict(v, id=box_id(b["type"], b["name"]), type=b["type"], name=b["name"],
                    items=items, origin=origin, source=source, verified=verified)

    def view(self):
        now = self._now()
        with self._lock:
            doc = self._load(self._day(now))
        cp, cp_src = self._cp(doc)
        cap = daily_cap(cp, self.divisor)
        nxt = today.next_reset(self.rule, now)
        types = []
        for t in TYPES:
            d = doc["delivered"][t]
            left = None if cap is None else max(0, cap - d)
            types.append({"type": t, "cap": cap, "delivered": d, "left": left,
                          "done": left == 0, "mastery_pct": doc["mastery"][t]})
        rows = []
        for b in (self.data or {}).get("boxes", []):
            rows.append(self._row(b, "seed", doc["mastery"][b["type"]], b["source"],
                                  b["verified"]))
        for b in doc["boxes"]:
            rows.append(self._row(b, "operator", doc["mastery"][b["type"]]))
        rules = self.data["rules"] if self.data else None
        return {"now": _iso(now), "day": doc["day"],
                "reset": {"next_utc": _iso(nxt), "left_s": int((nxt - now).total_seconds())},
                "cp": {"value": cp, "source": cp_src, "typed": doc["cp"]},
                "types": types, "boxes": rows,
                "best": {t: best_boxes([r for r in rows if r["type"] == t]) for t in TYPES},
                "rules": None if rules is None else {
                    "cap_divisor": rules["cap_divisor"], "payout_pct": rules["payout_pct"],
                    "taxed": rules["taxed"], "note": rules["note"],
                    "source": rules["source"], "verified": rules["verified"]},
                "data_error": self.data_error}
