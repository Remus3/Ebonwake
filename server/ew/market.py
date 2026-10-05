"""Market data (plan 002 slice A): arsha.io v2 `na` client, disk cache, backoff,
watchlist and alert rules.

Read-only GET only (ToS floor): no authenticated or write call to arsha or
Pearl Abyss, ever. Upstream outages are normal, so every result carries its
freshness and stale cache is served during backoff. The cache + backoff core
lives in httpcache.py (shared with plan 004).
"""

import json
import math
import threading
import time
import urllib.parse
from pathlib import Path

from .httpcache import (BACKOFF_BASE_S, BACKOFF_MAX_S, CachedClient,  # noqa: F401
                        Pending, UpstreamError, default_fetch, iso)
from .httpcache import freshness as _freshness

BASE = "https://api.arsha.io/v2/na/"
TIMEOUT_S = 10
TTL = {"sublist": 300, "orders": 120, "history": 3600, "hot": 600}
ENDPOINT = {"sublist": "GetWorldMarketSubList", "orders": "GetBiddingInfoList",
            "history": "GetMarketPriceInfo", "hot": "GetWorldMarketHotList"}
MAX_ID = 2 ** 31 - 1
_DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "ops" / "runtime" / "cache" / "market"


def _pick(body, sid):
    """Normalise a list-or-object body to the object for `sid`."""
    if isinstance(body, list):
        for obj in body:
            if isinstance(obj, dict) and obj.get("sid", 0) == sid:
                return obj
        return None
    return body


class ArshaClient(CachedClient):
    def __init__(self, fetch=None, clock=time.time, cache_dir=None):
        super().__init__(fetch=fetch, clock=clock,
                         cache_dir=cache_dir if cache_dir is not None else _DEFAULT_CACHE)

    def _key(self, kind, item_id, sid):
        return f"{kind}_{int(item_id)}_{int(sid)}"

    def backoff_state(self, kind, item_id=0, sid=0):
        return self.key_backoff(self._key(kind, item_id, sid))

    def _url(self, kind, item_id, sid):
        q = {"lang": "en"}
        if kind != "hot":
            q["id"] = int(item_id)
        if kind in ("orders", "history"):
            q["sid"] = int(sid)
        return BASE + ENDPOINT[kind] + "?" + urllib.parse.urlencode(q)

    def _download(self, kind, item_id, sid):
        try:
            raw = self.fetch(self._url(kind, item_id, sid), TIMEOUT_S)
        except Pending:
            raise
        except Exception as e:  # HTTPError, URLError, timeout, OSError
            raise UpstreamError(f"{type(e).__name__}: {e}") from e
        try:
            body = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
        except (ValueError, UnicodeDecodeError) as e:
            raise UpstreamError(f"bad json: {e}") from e
        if isinstance(body, dict) and "code" in body:
            raise UpstreamError(f"arsha code {body.get('code')}: {body.get('message', '')}"[:200])
        if kind == "hot":
            if isinstance(body, dict):
                body = [body]
            if not isinstance(body, list):
                raise UpstreamError("bad shape")
            return body
        obj = _pick(body, sid)
        if not isinstance(obj, dict):
            raise UpstreamError(f"no entry for sid {sid}")
        return obj

    def get(self, kind, item_id=0, sid=0):
        return self.cached_get(self._key(kind, item_id, sid), TTL[kind],
                               lambda: self._download(kind, item_id, sid))

    def sublist(self, item_id, sid=0):
        return self.get("sublist", item_id, sid)

    def history(self, item_id, sid=0):
        return self.get("history", item_id, sid)

    def orders(self, item_id, sid=0):
        return self.get("orders", item_id, sid)

    def hot(self):
        return self.get("hot")


# -- rules ----------------------------------------------------------------

def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def price_of(sub):
    if not isinstance(sub, dict):
        return None
    for k in ("lastSoldPrice", "basePrice"):
        v = sub.get(k)
        if _is_int(v) and v > 0:
            return v
    return None


# -- plan 027: net proceeds after tax + pre-order queues ---------------------

RULES_FILE = Path(__file__).resolve().parent / "data" / "market_rules.json"
FAME_MAX = 1.5
_rules_cache = None


def load_rules():
    """Tracked tax constants (server/ew/data/market_rules.json), read once."""
    global _rules_cache
    if _rules_cache is None:
        _rules_cache = json.loads(RULES_FILE.read_text(encoding="utf-8"))
    return _rules_cache


def _half_up(x):
    # floor(x + 0.5) on the same IEEE double is exactly JS Math.round for x >= 0;
    # Python round() is half-even and would split from ewcore.js at 12.5 bp.
    return math.floor(x + 0.5)


def _bp(rate):
    return _half_up(rate * 10000)


def _valid_fame(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= FAME_MAX


def net_proceeds(price, vp, fame_pct):
    """Silver the seller collects for a sale at `price` (integer, floored).

    Rates are taken to basis points and multiplied as integers so a 1 T sale
    floors exactly; ewcore.js netProceeds mirrors this with BigInt.
    """
    if not _is_int(price) or price < 0:
        return None
    if not _valid_fame(fame_pct):
        raise ValueError(f"fame_pct must be a number in 0..{FAME_MAX}")
    r = load_rules()
    keep = 10000 - _bp(r["tax"])
    mult = 10000 + (_bp(r["vp_bonus"]) if vp else 0) + _half_up(fame_pct * 100)
    return price * keep * mult // 10 ** 8


def preorder_state(item):
    """`capped` (last sale at priceMax), `no_stock` (currentStock 0) or None."""
    if not isinstance(item, dict):
        return None
    last, top = item.get("lastSoldPrice"), item.get("priceMax")
    if _is_int(last) and _is_int(top) and top > 0 and last == top:
        return "capped"
    if _is_int(item.get("currentStock")) and item["currentStock"] == 0:
        return "no_stock"
    return None


def settings_from(doc):
    """`market` {vp, fame_pct} from a config/local.json doc; bad values -> defaults."""
    m = doc.get("market") if isinstance(doc, dict) else None
    m = m if isinstance(m, dict) else {}
    vp = m.get("vp")
    fame = m.get("fame_pct")
    return {"vp": vp if isinstance(vp, bool) else False,
            "fame_pct": fame if _valid_fame(fame) else 0}


def alert_for(price, below, above):
    if price is None:
        return None
    if below is not None and price <= below:
        return "below"
    if above is not None and price >= above:
        return "above"
    return None


def _check_int(entry, name, required, default=None):
    v = entry.get(name, default)
    if v is None and not required:
        return None
    if not _is_int(v) or v < 0 or v > MAX_ID:
        raise ValueError(f"{name} must be an int in 0..{MAX_ID}")
    return v


def validate_entry(entry, thresholds=True):
    if not isinstance(entry, dict):
        raise ValueError("entry must be an object")
    out = {"id": _check_int(entry, "id", True), "sid": _check_int(entry, "sid", True, 0)}
    if thresholds:
        out["below"] = _check_int(entry, "below", False)
        out["above"] = _check_int(entry, "above", False)
    return out


class Watchlist:
    """Store domain `market`: {"watch": [{id, sid, below, above}]}."""

    def __init__(self, store, seed=None):
        self.store = store
        self._lock = threading.Lock()
        doc = store.get("market")
        if "watch" not in doc:
            ids = [i for i in (seed or []) if _is_int(i) and 0 <= i <= MAX_ID]
            store.put("market", dict(doc, watch=[
                {"id": i, "sid": 0, "below": None, "above": None} for i in ids]))

    def items(self):
        return list(self.store.get("market").get("watch", []))

    def _save(self, watch):
        self.store.put("market", dict(self.store.get("market"), watch=watch))

    def add(self, entry):
        e = validate_entry(entry)
        with self._lock:
            watch = self.items()
            for i, w in enumerate(watch):
                if w["id"] == e["id"] and w["sid"] == e["sid"]:
                    watch[i] = e
                    break
            else:
                watch.append(e)
            self._save(watch)
        return self.items()

    def remove(self, entry):
        e = validate_entry(entry, thresholds=False)
        with self._lock:
            self._save([w for w in self.items()
                        if not (w["id"] == e["id"] and w["sid"] == e["sid"])])
        return self.items()


class MarketService:
    def __init__(self, client, watchlist, settings=None):
        self.client = client
        self.watchlist = watchlist
        self.settings = settings_from({"market": settings or {}})
        self._last = None  # freshness summary of the last watch refresh

    def tax(self):
        """Rates the dashboard's pair calculator mirrors (plan 027)."""
        r = load_rules()
        return dict(self.settings, tax=r["tax"], vp_bonus=r["vp_bonus"],
                    fame_verified=r["fame_steps"]["verified"])

    def watch(self):
        vp, fame = self.settings["vp"], self.settings["fame_pct"]
        items = []
        for w in self.watchlist.items():
            res = self.client.sublist(w["id"], w["sid"])
            sub = res["data"] if isinstance(res["data"], dict) else {}
            price = price_of(sub)
            items.append({"id": w["id"], "sid": w["sid"], "name": sub.get("name"),
                          "price": price, "stock": sub.get("currentStock"),
                          "trades": sub.get("totalTrades"), "below": w.get("below"),
                          "above": w.get("above"), "alert": alert_for(price, w.get("below"),
                                                                      w.get("above")),
                          "net": net_proceeds(price, vp, fame),
                          "preorder": preorder_state(sub),
                          "freshness": _freshness(res)})
        self._last = [it["freshness"] for it in items]
        return {"items": items, "tax": self.tax(), "updated": iso(self.client.clock())}

    def item(self, item_id, sid=0):
        sub = self.client.sublist(item_id, sid)
        hist = self.client.history(item_id, sid)
        orders = self.client.orders(item_id, sid)
        h = hist["data"].get("history") if isinstance(hist["data"], dict) else None
        points = []
        if isinstance(h, dict):
            for k, v in h.items():
                try:
                    points.append([int(k), v])
                except (TypeError, ValueError):
                    continue
        points.sort(key=lambda p: p[0])
        o = orders["data"].get("orders") if isinstance(orders["data"], dict) else None
        return {"sub": sub["data"], "history": points, "orders": o if isinstance(o, list) else [],
                "freshness": {"sub": _freshness(sub), "history": _freshness(hist),
                              "orders": _freshness(orders)}}

    def hot(self):
        res = self.client.hot()
        rows = res["data"] if isinstance(res["data"], list) else []
        # Copies: the cached rows stay as arsha sent them.
        items = [dict(x, preorder=preorder_state(x)) if isinstance(x, dict) else x for x in rows]
        return {"items": items, "freshness": _freshness(res)}

    def source(self):
        """`/api/state` sources.market: {updated, ttl_s, status: ok|stale|error|none}."""
        ttl = TTL["sublist"]
        if not self._last:
            return {"updated": None, "ttl_s": ttl, "status": "none"}
        stamps = [f["fetched_at"] for f in self._last if f["fetched_at"]]
        updated = min(stamps) if stamps else None
        if not stamps:
            status = "error"
        elif any(f["stale"] for f in self._last):
            status = "stale"
        else:
            status = "ok"
        return {"updated": updated, "ttl_s": ttl, "status": status}
