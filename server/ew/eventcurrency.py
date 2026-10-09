"""Event currency planner (plan 095): guaranteed currency by event end vs an
exchange wishlist, shortfall and buy-by warnings.

Rules are tracked data (`data/event_currency.json`, keyed by official notice
number): the currency, the event end, its sources (daily login, daily minutes
played, weekly games, once-per-family quests, field drops) and the exchange
rows with per-family limits. `project` is pure over the plan 075 login-day
history (UTC date -> logged-in minutes, plus marked days), the operator's
ticks of weekly / once rows and an optional typed balance:

    earned     = qualifying dates x daily amounts + ticked weekly periods
                 + ticked once rows                    (or the typed balance)
    guaranteed = earned + remaining days x daily amounts
                 + remaining weekly periods + unticked once rows

A daily date counts for `daily_login` at >= 1 minute (or a marked day) and for
`daily_minutes` at >= `min_minutes`; weekly periods follow the plan 021 reset
rule (default Thursday 00:00 UTC) and a period that passed unticked is lost;
drops are never projected (unknown rate). Today counts as remaining until it
qualifies. The typed balance (a plan 079-style override) expires at the event
end. An ended event is hidden.

Store domains: `event_wishlist` {"items": [{event, item, qty}], "balances":
{event: {value, set_at, expires_at}}} and `event_ticks` {"ticks": {event:
{source id: [iso, ...]}}}. Nothing here fetches, reads the game or buys.
"""

import datetime as _dt
import json
import re
import threading
import time
from pathlib import Path

from . import today as _today

DATA_FILE = Path(__file__).resolve().parent / "data" / "event_currency.json"
DOMAIN = "event_wishlist"
TICKS_DOMAIN = "event_ticks"
KINDS = ("daily_login", "daily_minutes", "weekly", "once", "drop")
DAILY = ("daily_login", "daily_minutes")
TICK_KINDS = ("weekly", "once")
DEFAULT_WEEKLY = {"every": "week", "weekday": 3, "at": "00:00"}  # plan 021 / 003
EVENT_RE = re.compile(r"^[0-9]{1,9}$")
ID_RE = re.compile(r"^[a-z0-9_-]{1,40}$")
DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
MAX_TEXT = 120
MAX_AMOUNT = 100000
MAX_BALANCE = 10 ** 7
MAX_TICKS = 20
MAX_WISH = 200
RULE_KEYS = {"title", "currency", "unit", "url", "read", "verified", "starts", "ends", "note",
             "sources", "exchange"}
SOURCE_KEYS = {"id", "name", "kind", "amount", "min_minutes", "reset_rule", "window"}
EXCHANGE_KEYS = {"item", "cost", "limit", "removed_at"}
_DAY = _dt.timedelta(days=1)
_UTC = _dt.timezone.utc


def _int(v, lo, hi):
    return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


def _text(v, what, hi=MAX_TEXT):
    if not isinstance(v, str) or not v.strip() or len(v) > hi or not v.isascii() \
            or not v.isprintable():
        raise ValueError(f"{what} must be 1..{hi} printable ASCII characters")
    return v


def _stamp(v, what):
    t = _today._parse_iso(v)
    if t is None:
        raise ValueError(f"{what} must be an ISO UTC time with an offset")
    return t.astimezone(_UTC)


def _day(v, what):
    if not isinstance(v, str) or not DATE_RE.match(v):
        raise ValueError(f"{what} must be YYYY-MM-DD")
    try:
        return _dt.date.fromisoformat(v)
    except ValueError:
        raise ValueError(f"{what} must be a real date") from None


def _iso(when):
    return _today._iso(when)


def _midnight(d):
    return _dt.datetime.combine(d, _dt.time(0), _UTC)


# -- rules (tracked data) -------------------------------------------------------------

def _source(raw, starts, ends):
    if not isinstance(raw, dict) or not {"id", "name", "kind", "amount"} <= set(raw) \
            or not set(raw) <= SOURCE_KEYS:
        raise ValueError("a source is {id, name, kind, amount, min_minutes?, reset_rule?, window?}")
    sid = raw["id"]
    if not isinstance(sid, str) or not ID_RE.match(sid):
        raise ValueError("source id must match ^[a-z0-9_-]{1,40}$")
    kind = raw["kind"]
    if kind not in KINDS:
        raise ValueError(f"source {sid}: kind must be one of {', '.join(KINDS)}")
    out = {"id": sid, "name": _text(raw["name"], f"source {sid} name"), "kind": kind}
    if kind == "drop":
        if raw["amount"] is not None:
            raise ValueError(f"source {sid}: a drop has amount null (never projected)")
        out["amount"] = None
    elif not _int(raw["amount"], 1, MAX_AMOUNT):
        raise ValueError(f"source {sid}: amount must be an int 1..{MAX_AMOUNT}")
    else:
        out["amount"] = raw["amount"]
    if ("min_minutes" in raw) != (kind == "daily_minutes"):
        raise ValueError(f"source {sid}: min_minutes is required for daily_minutes only")
    if kind == "daily_minutes":
        if not _int(raw["min_minutes"], 1, 1440):
            raise ValueError(f"source {sid}: min_minutes must be 1..1440")
        out["min_minutes"] = raw["min_minutes"]
    if "reset_rule" in raw:
        if kind != "weekly":
            raise ValueError(f"source {sid}: reset_rule is for weekly rows only")
        rule = _today.validate_rule(raw["reset_rule"])
        if rule["every"] != "week":
            raise ValueError(f"source {sid}: reset_rule every must be week")
        out["reset_rule"] = rule
    elif kind == "weekly":
        out["reset_rule"] = dict(DEFAULT_WEEKLY)
    first, last = starts.date(), (ends - _dt.timedelta(microseconds=1)).date()
    win = raw.get("window")
    if win is None:
        lo, hi = first, last
    else:
        if not isinstance(win, dict) or set(win) != {"from", "to"}:
            raise ValueError(f"source {sid}: window must be {{from, to}}")
        lo, hi = _day(win["from"], f"source {sid} window.from"), _day(win["to"], f"{sid} window.to")
        if not first <= lo <= hi <= last:
            raise ValueError(f"source {sid}: window must lie inside the event span")
    out["window"] = {"from": lo.isoformat(), "to": hi.isoformat()}
    return out


def _exchange(raw):
    if not isinstance(raw, dict) or not {"item", "cost", "limit"} <= set(raw) \
            or not set(raw) <= EXCHANGE_KEYS:
        raise ValueError("an exchange row is {item, cost, limit, removed_at?}")
    item = _text(raw["item"], "exchange item")
    if not _int(raw["cost"], 1, MAX_AMOUNT) or not _int(raw["limit"], 1, 999):
        raise ValueError(f"exchange {item}: cost 1..{MAX_AMOUNT} and limit 1..999")
    out = {"item": item, "cost": raw["cost"], "limit": raw["limit"]}
    if "removed_at" in raw:
        out["removed_at"] = _iso(_stamp(raw["removed_at"], f"exchange {item} removed_at"))
    return out


def validate_rule(no, raw):
    """One event's rule -> normalised dict (with `event`); raises ValueError."""
    if not isinstance(no, str) or not EVENT_RE.match(no):
        raise ValueError("event number must be 1..9 digits")
    if not isinstance(raw, dict) or not set(raw) <= RULE_KEYS or \
            not RULE_KEYS - {"note"} <= set(raw):
        raise ValueError(f"event {no}: keys must be {', '.join(sorted(RULE_KEYS))}")
    starts, ends = _stamp(raw["starts"], f"{no} starts"), _stamp(raw["ends"], f"{no} ends")
    if ends <= starts:
        raise ValueError(f"event {no}: ends must be after starts")
    url = _text(raw["url"], f"{no} url", 300)
    if not url.startswith("https://"):
        raise ValueError(f"event {no}: url must be https://")
    if not isinstance(raw["verified"], bool):
        raise ValueError(f"event {no}: verified must be true or false")
    unit = _text(raw["unit"], f"{no} unit", 30)
    srcs = raw["sources"]
    if not isinstance(srcs, list) or not 0 < len(srcs) <= 50:
        raise ValueError(f"event {no}: sources must be a list of 1..50 rows")
    sources = [_source(s, starts, ends) for s in srcs]
    if len({s["id"] for s in sources}) != len(sources):
        raise ValueError(f"event {no}: source ids must be unique")
    exch = raw["exchange"]
    if not isinstance(exch, list) or len(exch) > 100:
        raise ValueError(f"event {no}: exchange must be a list of at most 100 rows")
    exchange = [_exchange(x) for x in exch]
    if len({x["item"] for x in exchange}) != len(exchange):
        raise ValueError(f"event {no}: exchange items must be unique")
    return {"event": no, "title": _text(raw["title"], f"{no} title"),
            "currency": _text(raw["currency"], f"{no} currency"), "unit": unit, "url": url,
            "read": _day(raw["read"], f"{no} read").isoformat(), "verified": raw["verified"],
            "starts": _iso(starts), "ends": _iso(ends),
            "note": raw["note"] if isinstance(raw.get("note"), str) else "",
            "sources": sources, "exchange": exchange}


def load_rules(path=None):
    """{event number: rule}; a bad file raises ValueError (a test pins the seed)."""
    try:
        doc = json.loads(Path(path or DATA_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ValueError(f"event_currency.json unreadable: {e}") from None
    evs = doc.get("events") if isinstance(doc, dict) else None
    if not isinstance(evs, dict):
        raise ValueError("event_currency.json: events must be an object")
    return {no: validate_rule(no, r) for no, r in evs.items()}


# -- projection (pure) -------------------------------------------------------------------

def _span(src, rule):
    """[lo, hi) datetimes a source is open: its window dates, inside the event."""
    lo = max(_midnight(_dt.date.fromisoformat(src["window"]["from"])),
             _dt.datetime.fromisoformat(rule["starts"]))
    hi = min(_midnight(_dt.date.fromisoformat(src["window"]["to"]) + _DAY),
             _dt.datetime.fromisoformat(rule["ends"]))
    return lo, hi


def _qualifies(src, d, dates, marked):
    key = d.isoformat()
    m = dates.get(key)
    m = m if isinstance(m, (int, float)) and not isinstance(m, bool) else 0
    if src["kind"] == "daily_login":
        return key in marked or m >= 1
    return m >= src["min_minutes"]


def _stamps(raw):
    out = []
    for s in raw if isinstance(raw, list) else ():
        t = _today._parse_iso(s)
        if t is not None:
            out.append(t.astimezone(_UTC))
    return out


def _periods(src, rule):
    """Weekly periods [(lo, hi)] of a source, clipped to its open span."""
    lo, hi = _span(src, rule)
    out, p = [], _today.last_reset(src["reset_rule"], lo)
    while p < hi:
        q = p + 7 * _DAY
        out.append((max(p, lo), min(q, hi)))
        p = q
    return out


def _one(src, rule, now, dates, marked, ticks):
    """(earned, remaining) of one source at `now`."""
    kind, amt = src["kind"], src["amount"]
    if kind == "drop":
        return 0, 0
    lo, hi = _span(src, rule)
    if kind in DAILY:
        today = now.date()
        earned = remaining = 0
        d = lo.date()
        while _midnight(d) < hi:
            ok = _qualifies(src, d, dates, marked)
            if d < today or (d == today and ok):
                earned += amt if ok else 0
            elif _midnight(d) + _DAY > now:  # today (not yet met) or ahead
                remaining += amt
            d += _DAY
        return earned, remaining
    stamps = _stamps(ticks)
    if kind == "once":
        if any(lo <= t < hi for t in stamps):
            return amt, 0
        return 0, amt if hi > now else 0
    earned = remaining = 0
    for a, b in _periods(src, rule):
        if any(a <= t < b for t in stamps):
            earned += amt
        elif b > now:
            remaining += amt
    return earned, remaining


def active_balance(entry, now):
    """A stored typed balance in force at `now` (expires at the event end), else None."""
    if not isinstance(entry, dict) or not _int(entry.get("value"), 0, MAX_BALANCE):
        return None
    exp = _today._parse_iso(entry.get("expires_at"))
    if exp is None or now >= exp:
        return None
    return entry["value"]


def project(rule, now, dates, marked, ticks, balance=None):
    """Projection of one event at `now` (UTC datetime), None once it ended.
    `dates` {YYYY-MM-DD: minutes}, `marked` [YYYY-MM-DD], `ticks` {source id: [iso]}."""
    if now >= _dt.datetime.fromisoformat(rule["ends"]):
        return None
    dates = dates if isinstance(dates, dict) else {}
    marked = set(marked or ())
    ticks = ticks if isinstance(ticks, dict) else {}
    rows, earned, remaining, play = [], 0, 0, None
    for s in rule["sources"]:
        e, r = _one(s, rule, now, dates, marked, ticks.get(s["id"]))
        rows.append({"id": s["id"], "name": s["name"], "kind": s["kind"], "earned": e,
                     "remaining": r})
        earned += e
        remaining += r
        if play is None and s["kind"] == "daily_minutes":
            lo, hi = _span(s, rule)
            if lo <= now < hi and not _qualifies(s, now.date(), dates, marked):
                m = dates.get(now.date().isoformat())
                m = int(m) if isinstance(m, (int, float)) and not isinstance(m, bool) else 0
                play = {"minutes": m, "min_minutes": s["min_minutes"], "amount": s["amount"]}
    typed = active_balance(balance, now)
    have = earned if typed is None else typed
    return {"event": rule["event"], "title": rule["title"], "currency": rule["currency"],
            "unit": rule["unit"], "url": rule["url"], "verified": rule["verified"],
            "starts": rule["starts"], "ends": rule["ends"], "earned": have,
            "earned_src": "log" if typed is None else "typed", "logged": earned,
            "remaining": remaining, "guaranteed": have + remaining, "by_source": rows,
            "play_today": play}


def _mmdd(iso):
    return iso[5:10]


def wish_rows(rule, wishes, now):
    """The event's wishlist rows (qty > 0) with cost and the buy-by line."""
    by = {x["item"]: x for x in rule["exchange"]}
    out = []
    for w in wishes:
        x = by.get(w.get("item")) if w.get("event") == rule["event"] else None
        if x is None or not _int(w.get("qty"), 1, x["limit"]):
            continue
        rem = x.get("removed_at")
        use = None
        if rem is not None and _dt.datetime.fromisoformat(rem) > now:
            use = f"use before {_mmdd(rem)}"
        out.append({"item": x["item"], "qty": w["qty"], "cost": x["cost"], "limit": x["limit"],
                    "subtotal": x["cost"] * w["qty"], "removed_at": rem, "use_before": use})
    return out


def label(row):
    """"Seals: 120 earned, 640 guaranteed by 11-05, wishlist 590 - covered"
    (mirrors ewcore.js currencyText)."""
    unit = row["unit"][:1].upper() + row["unit"][1:]
    have = f"{row['earned']} in hand (typed)" if row["earned_src"] == "typed" \
        else f"{row['earned']} earned"
    out = f"{unit}: {have}, {row['guaranteed']} guaranteed by {_mmdd(row['ends'])}"
    if row["wishlist_cost"] > 0:
        out += f", wishlist {row['wishlist_cost']} - " + (
            f"{row['shortfall']} short - from drops" if row["shortfall"] > 0 else "covered")
    return out


# -- service ---------------------------------------------------------------------------------

class CurrencyService:
    """GET /api/events/currency (and the Events `currency` block), the Today
    event rows and their ticks, the wishlist and the typed balance.
    `history()` -> plan 075 `LoginDays.history()` ({dates, marked, ...})."""

    def __init__(self, store, clock=time.time, history=None, rules=None):
        self.store = store
        self.clock = clock
        self.history = history or (lambda: {"dates": {}, "marked": []})
        self.rules = load_rules() if rules is None else rules
        self._lock = threading.Lock()

    def _now(self):
        return _dt.datetime.fromtimestamp(self.clock(), _UTC)

    def _rule(self, no):
        if not isinstance(no, str) or no not in self.rules:
            raise ValueError("event must be a tracked currency event number")
        return self.rules[no]

    # -- store ---------------------------------------------------------------------

    def _wish_doc(self):
        doc = self.store.get(DOMAIN)
        items = [w for w in doc.get("items") or () if isinstance(w, dict)]
        bal = doc.get("balances") if isinstance(doc.get("balances"), dict) else {}
        return {"items": items[:MAX_WISH], "balances": dict(bal)}

    def _ticks(self):
        t = self.store.get(TICKS_DOMAIN).get("ticks")
        return t if isinstance(t, dict) else {}

    def _hist(self):
        try:
            h = self.history() or {}
        except Exception:  # noqa: BLE001 - no history = nothing logged yet
            h = {}
        dates = h.get("dates") if isinstance(h.get("dates"), dict) else {}
        return dates, list(h.get("marked") or ())

    # -- reads ----------------------------------------------------------------------

    def view(self):
        now = self._now()
        dates, marked = self._hist()
        with self._lock:
            wl, ticks = self._wish_doc(), self._ticks()
        out = []
        for no in sorted(self.rules, key=lambda n: (self.rules[n]["ends"], n)):
            rule = self.rules[no]
            ev_ticks = ticks.get(no) if isinstance(ticks.get(no), dict) else {}
            p = project(rule, now, dates, marked, ev_ticks, wl["balances"].get(no))
            if p is None:
                continue
            wish = wish_rows(rule, wl["items"], now)
            cost = sum(w["subtotal"] for w in wish)
            row = dict(p, wishlist=wish, wishlist_cost=cost,
                       shortfall=max(0, cost - p["guaranteed"]),
                       exchange=[dict(x) for x in rule["exchange"]])
            row["label"] = label(row)
            out.append(row)
        return {"events": out}

    def today_rows(self):
        """Weekly / once rows open now (inside their window, event not ended),
        for the Today checklist: {key, event, source, name, kind, amount, unit,
        done, next_reset (weekly)}."""
        now = self._now()
        with self._lock:
            ticks = self._ticks()
        out = []
        for no in sorted(self.rules):
            rule = self.rules[no]
            ev_ticks = ticks.get(no) if isinstance(ticks.get(no), dict) else {}
            for s in rule["sources"]:
                if s["kind"] not in TICK_KINDS:
                    continue
                lo, hi = _span(s, rule)
                if not lo <= now < hi:
                    continue
                a, b = self._period_now(s, rule, now)
                stamps = _stamps(ev_ticks.get(s["id"]))
                out.append({"key": f"{no}:{s['id']}", "event": no, "source": s["id"],
                            "name": s["name"], "kind": s["kind"], "amount": s["amount"],
                            "unit": rule["unit"], "done": any(a <= t < b for t in stamps),
                            "next_reset": _iso(b) if s["kind"] == "weekly" else None})
        return out

    @staticmethod
    def _period_now(src, rule, now):
        if src["kind"] == "once":
            return _span(src, rule)
        for a, b in _periods(src, rule):
            if a <= now < b:
                return a, b
        return now, now

    def loss_rows(self):
        """Plan 074 digest rows for wishlist items removed at a later time:
        [{notice_no, url, due_utc, loss: [text]}], only while the removal is ahead."""
        now = self._now()
        with self._lock:
            wl = self._wish_doc()
        out = []
        for no in sorted(self.rules):
            rule = self.rules[no]
            for w in wish_rows(rule, wl["items"], now):
                if w["use_before"] is None:
                    continue
                text = (f"{w['item']} from the {rule['unit']} exchange is removed"
                        f" - use it before")[:200]
                out.append({"notice_no": int(no), "url": rule["url"], "due_utc": w["removed_at"],
                            "loss": [text]})
        return sorted(out, key=lambda r: (r["due_utc"], r["notice_no"]))

    def typed_overrides(self):
        """Plan 079 / 081: typed balances in force, as /api/overrides rows."""
        now = self._now()
        with self._lock:
            bal = self._wish_doc()["balances"]
        out = []
        for no in sorted(bal):
            rule = self.rules.get(no)
            v = active_balance(bal[no], now)
            if rule is None or v is None or now >= _dt.datetime.fromisoformat(rule["ends"]):
                continue
            unit = rule["unit"][:1].upper() + rule["unit"][1:]
            out.append({"key": f"events.currency.{no}.balance", "label": f"{unit} balance (typed)",
                        "value": v, "set_at": bal[no].get("set_at"),
                        "expires_at": bal[no]["expires_at"],
                        "reason": "replaces the logged count until the event ends",
                        "cards": ["events"]})
        return out

    # -- writes -------------------------------------------------------------------------

    def _key(self, key):
        if not isinstance(key, str) or key.count(":") != 1:
            raise ValueError("event row key must be <event>:<source>")
        no, sid = key.split(":")
        rule = self._rule(no)
        src = next((s for s in rule["sources"] if s["id"] == sid), None)
        if src is None or src["kind"] not in TICK_KINDS:
            raise ValueError("only weekly and once rows are ticked")
        return no, src, rule

    def tick(self, key):
        return self.tick_at(key, self.clock())

    def tick_at(self, key, when):
        """Tick a weekly / once row at epoch `when` (inside its open span);
        a row already done for that period is left alone."""
        no, src, rule = self._key(key)
        at = _dt.datetime.fromtimestamp(when, _UTC)
        lo, hi = _span(src, rule)
        if not lo <= at < hi:
            raise ValueError(f"{src['name']} is not open now")
        a, b = self._period_now(src, rule, at)
        with self._lock:
            doc = self.store.get(TICKS_DOMAIN)
            ticks = doc.get("ticks") if isinstance(doc.get("ticks"), dict) else {}
            ev = ticks.get(no) if isinstance(ticks.get(no), dict) else {}
            have = [s for s in ev.get(src["id"]) or () if isinstance(s, str)]
            if not any(a <= t < b for t in _stamps(have)):
                ev[src["id"]] = (have + [_iso(at)])[-MAX_TICKS:]
                ticks[no] = ev
                self.store.put(TICKS_DOMAIN, {"ticks": ticks})
        return self.today_rows()

    def untick(self, key):
        """Undo this period's tick (a once row: its tick)."""
        no, src, rule = self._key(key)
        now = self._now()
        a, b = self._period_now(src, rule, now)
        with self._lock:
            doc = self.store.get(TICKS_DOMAIN)
            ticks = doc.get("ticks") if isinstance(doc.get("ticks"), dict) else {}
            ev = ticks.get(no) if isinstance(ticks.get(no), dict) else {}
            have = [s for s in ev.get(src["id"]) or () if isinstance(s, str)]
            keep = [s for s in have if not (_today._parse_iso(s) is not None
                                            and a <= _today._parse_iso(s) < b)]
            if keep != have:
                ev[src["id"]] = keep
                ticks[no] = ev
                self.store.put(TICKS_DOMAIN, {"ticks": ticks})
        return self.today_rows()

    def wish(self, arg):
        """{event, item, qty}: qty 0..limit (0 removes the row)."""
        if not isinstance(arg, dict) or set(arg) != {"event", "item", "qty"}:
            raise ValueError("wish must be {event, item, qty}")
        rule = self._rule(arg["event"])
        x = next((r for r in rule["exchange"] if r["item"] == arg["item"]), None)
        if x is None:
            raise ValueError("item must be a row of that event's exchange")
        if not _int(arg["qty"], 0, x["limit"]):
            raise ValueError(f"qty must be 0..{x['limit']} (the per-family exchange limit)")
        with self._lock:
            doc = self._wish_doc()
            items = [w for w in doc["items"]
                     if not (w.get("event") == rule["event"] and w.get("item") == x["item"])]
            if arg["qty"] > 0:
                items.append({"event": rule["event"], "item": x["item"], "qty": arg["qty"]})
            doc["items"] = items[-MAX_WISH:]
            self.store.put(DOMAIN, doc)
        return self.view()

    def balance(self, arg):
        """{event, value: int | null}: the operator's balance now, until the event end."""
        if not isinstance(arg, dict) or set(arg) != {"event", "value"}:
            raise ValueError("currency_balance must be {event, value}")
        rule = self._rule(arg["event"])
        v = arg["value"]
        if v is not None and not _int(v, 0, MAX_BALANCE):
            raise ValueError(f"value must be null or an int 0..{MAX_BALANCE}")
        now = self._now()
        with self._lock:
            doc = self._wish_doc()
            bal = {k: e for k, e in doc["balances"].items() if active_balance(e, now) is not None}
            if v is None:
                bal.pop(rule["event"], None)
            else:
                bal[rule["event"]] = {"value": v, "set_at": _iso(now), "expires_at": rule["ends"]}
            doc["balances"] = bal
            self.store.put(DOMAIN, doc)
        return self.view()
