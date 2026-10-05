"""Market data (plan 002 slice A): arsha.io v2 `na` client, disk cache, backoff,
watchlist and alert rules.

Read-only GET only (ToS floor): no authenticated or write call to arsha or
Pearl Abyss, ever. Upstream outages are normal, so every result carries its
freshness and stale cache is served during backoff.
"""

import datetime as _dt
import json
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from . import __version__
from .store import atomic_write_json

BASE = "https://api.arsha.io/v2/na/"
TIMEOUT_S = 10
TTL = {"sublist": 300, "orders": 120, "history": 3600, "hot": 600}
ENDPOINT = {"sublist": "GetWorldMarketSubList", "orders": "GetBiddingInfoList",
            "history": "GetMarketPriceInfo", "hot": "GetWorldMarketHotList"}
BACKOFF_BASE_S = 30
BACKOFF_MAX_S = 1800
MAX_ID = 2 ** 31 - 1
_DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "ops" / "runtime" / "cache" / "market"


def _iso(epoch):
    if epoch is None:
        return None
    return _dt.datetime.fromtimestamp(epoch, _dt.timezone.utc).replace(microsecond=0).isoformat()


def default_fetch(url, timeout):
    """Plain GET; raises on HTTP error or timeout."""
    req = urllib.request.Request(url, method="GET",
                                 headers={"User-Agent": f"Ebonwake/{__version__}",
                                          "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (fixed https base)
        return r.read()


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _pick(body, sid):
    """Normalise a list-or-object body to the object for `sid`."""
    if isinstance(body, list):
        for obj in body:
            if isinstance(obj, dict) and obj.get("sid", 0) == sid:
                return obj
        return None
    return body


class UpstreamError(Exception):
    pass


class ArshaClient:
    def __init__(self, fetch=None, clock=time.time, cache_dir=None):
        self.fetch = fetch or default_fetch
        self.clock = clock
        self.cache_dir = Path(cache_dir) if cache_dir is not None else _DEFAULT_CACHE
        self._lock = threading.Lock()      # guards _keylocks
        self._bo_lock = threading.Lock()   # guards backoff.json read-modify-write
        self._keylocks = {}

    def _keylock(self, key):
        with self._lock:
            return self._keylocks.setdefault(key, threading.Lock())

    # -- paths / state ----------------------------------------------------
    def _key(self, kind, item_id, sid):
        return f"{kind}_{int(item_id)}_{int(sid)}"

    def _cache_path(self, key):
        return self.cache_dir / f"{key}.json"

    def _backoff_path(self):
        return self.cache_dir / "backoff.json"

    def _backoffs(self):
        with self._bo_lock:
            doc = _read_json(self._backoff_path())
        return doc if isinstance(doc, dict) else {}

    def backoff_state(self, kind, item_id=0, sid=0):
        bo = self._backoffs().get(self._key(kind, item_id, sid), {})
        if not (isinstance(bo, dict) and isinstance(bo.get("n", 0), int)
                and isinstance(bo.get("until", 0), (int, float))):
            return {}  # corrupt entry = no backoff, never a 500
        return bo

    def _set_backoff(self, key, entry):
        with self._bo_lock:
            doc = _read_json(self._backoff_path())
            doc = doc if isinstance(doc, dict) else {}
            if entry:
                doc[key] = entry
            else:
                doc.pop(key, None)
            atomic_write_json(self._backoff_path(), doc)

    # -- core -------------------------------------------------------------
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
        key = self._key(kind, item_id, sid)
        ttl = TTL[kind]
        # Per-key lock: one in-flight fetch per key (dedupe); other keys and
        # fresh-cache reads never wait behind a slow upstream.
        with self._keylock(key):
            now = self.clock()
            cached = _read_json(self._cache_path(key))
            if not (isinstance(cached, dict) and "data" in cached
                    and isinstance(cached.get("fetched_at"), (int, float))
                    and not isinstance(cached.get("fetched_at"), bool)):
                cached = None  # corrupt cache = no cache
            if cached and now - cached["fetched_at"] < ttl:
                return self._result(cached, now, ttl, False, None)
            bo = self.backoff_state(kind, item_id, sid)
            if bo and now < bo.get("until", 0):
                return self._result(cached, now, ttl, True, bo.get("error") or "backoff")
            try:
                data = self._download(kind, item_id, sid)
            except UpstreamError as e:
                n = int(bo.get("n", 0)) if bo else 0
                wait = min(BACKOFF_BASE_S * 2 ** n, BACKOFF_MAX_S)
                err = str(e)
                self._set_backoff(key, {"n": n + 1, "until": now + wait, "error": err})
                return self._result(cached, now, ttl, True, err)
            entry = {"fetched_at": now, "data": data}
            atomic_write_json(self._cache_path(key), entry)
            if bo:
                self._set_backoff(key, None)
            return self._result(entry, now, ttl, False, None)

    @staticmethod
    def _result(entry, now, ttl, stale, error):
        if not entry:
            return {"data": None, "fetched_at": None, "age_s": None, "ttl_s": ttl,
                    "stale": True, "error": error}
        return {"data": entry["data"], "fetched_at": _iso(entry["fetched_at"]),
                "age_s": int(now - entry["fetched_at"]), "ttl_s": ttl, "stale": stale,
                "error": error}

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


def _freshness(res):
    return {k: res[k] for k in ("fetched_at", "age_s", "ttl_s", "stale", "error")}


class MarketService:
    def __init__(self, client, watchlist):
        self.client = client
        self.watchlist = watchlist
        self._last = None  # freshness summary of the last watch refresh

    def watch(self):
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
                          "freshness": _freshness(res)})
        self._last = [it["freshness"] for it in items]
        return {"items": items, "updated": _iso(self.client.clock())}

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
        return {"items": res["data"] if isinstance(res["data"], list) else [],
                "freshness": _freshness(res)}

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
