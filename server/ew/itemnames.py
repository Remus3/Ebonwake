"""Market item-name index (plan 028): type a name, get `{id, sid, name}`.

Three read-only sources, merged with precedence seed > util/db > cache:
(a) the tracked seed `data/market_items.json` (verified ids only);
(b) names in cached arsha `/item` (sublist) and `/hot` responses (plan 002
    cache, never fetched here);
(c) arsha `/util/db?id=` (GET, at most 100 ids per call, 7-day TTL) for ids
    the operator types, behind the shared backoff.
arsha `/search` and `/category` were Imperva-blocked (research 0004 section
5), so nothing here depends on them. Index persisted under ops/runtime/
(atomic write) so names survive restarts with the network off.
"""

import json
import threading
import time
import urllib.parse
from pathlib import Path

from .httpcache import (BACKOFF_BASE_S, BACKOFF_MAX_S, PENDING_RETRY_S, CachedClient,
                        Pending, read_json)
from .store import atomic_write_json

SEED_PATH = Path(__file__).resolve().parent / "data" / "market_items.json"
UTIL_DB_URL = "https://api.arsha.io/util/db"
UTIL_DB_MAX_IDS = 100
UTIL_DB_TTL_S = 7 * 86400
TIMEOUT_S = 10
MAX_RESULTS = 20
Q_MIN, Q_MAX = 2, 40
SCAN_EVERY_S = 30
MAX_ID = 2 ** 31 - 1
_BO_KEY = "utildb"
_PREC = {"seed": 0, "db": 1, "cache": 2}


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _ok_id(v):
    return _is_int(v) and 0 <= v <= MAX_ID


def _row(obj, need_sid):
    """`{id, sid, name}` from a dict, or None when malformed."""
    if not isinstance(obj, dict):
        return None
    i, sid, name = obj.get("id"), obj.get("sid", None if need_sid else 0), obj.get("name")
    if not (_ok_id(i) and _ok_id(sid) and isinstance(name, str) and name.strip()):
        return None
    return {"id": i, "sid": sid, "name": name.strip()}


def load_seed(path=SEED_PATH):
    doc = read_json(path)
    items = doc.get("items") if isinstance(doc, dict) else None
    return [r for r in (_row(x, False) for x in (items or [])) if r]


def validate_query(q):
    if not isinstance(q, str):
        raise ValueError("q is required")
    q = q.strip()
    if not Q_MIN <= len(q) <= Q_MAX:
        raise ValueError(f"q must be {Q_MIN}-{Q_MAX} characters")
    if any(not 0x20 <= ord(c) <= 0x7E for c in q):
        raise ValueError("q must be printable ASCII")
    return q


class UtilDb(CachedClient):
    """arsha `/util/db` names. Read-only GET; one shared backoff key."""

    def _download(self, ids):
        q = [("id", i) for i in ids] + [("lang", "en")]
        raw = self.fetch(UTIL_DB_URL + "?" + urllib.parse.urlencode(q), TIMEOUT_S)
        body = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
        if isinstance(body, dict) and "code" in body:
            raise ValueError(f"arsha code {body.get('code')}: {body.get('message', '')}"[:200])
        if isinstance(body, dict):
            body = [body]
        if not isinstance(body, list):
            raise ValueError("bad shape")
        got = {i: None for i in ids}
        for obj in body:
            r = _row(obj, False)
            if r and r["id"] in got:
                got[r["id"]] = r["name"]
        return got

    def lookup(self, ids):
        """{id: name or None (unknown)} for `ids`, or None while backing off /
        when upstream fails before any batch answered."""
        ids = list(dict.fromkeys(i for i in ids if _ok_id(i)))
        now = self.clock()
        bo = self.key_backoff(_BO_KEY)
        if bo and now < bo.get("until", 0):
            return None
        n = int(bo.get("n", 0)) if bo else 0
        out = {}
        for k in range(0, len(ids), UTIL_DB_MAX_IDS):
            try:
                out.update(self._download(ids[k:k + UTIL_DB_MAX_IDS]))
            except Pending as e:
                self._set_backoff(_BO_KEY, {"n": n, "until": now + PENDING_RETRY_S,
                                            "error": str(e), "pending": True})
                return out or None
            except Exception as e:  # noqa: BLE001 - HTTP, timeout, bad json, arsha code
                wait = min(BACKOFF_BASE_S * 2 ** n, BACKOFF_MAX_S)
                self._set_backoff(_BO_KEY, {"n": n + 1, "until": now + wait,
                                            "error": f"{type(e).__name__}: {e}"[:200]})
                return out or None
        if bo:
            self._set_backoff(_BO_KEY, None)
        return out


class NameIndex:
    def __init__(self, seed, market_cache_dir, index_path, utildb, clock=time.time):
        self.seed = list(seed)
        self.market_cache_dir = Path(market_cache_dir)
        self.index_path = Path(index_path)
        self.utildb = utildb
        self.clock = clock
        self._lock = threading.Lock()
        self._scanned = None
        doc = read_json(self.index_path)
        doc = doc if isinstance(doc, dict) else {}
        db, cache = doc.get("db"), doc.get("cache")
        self.db = {}     # "id" -> {"name": str|None, "fetched_at": epoch}
        for k, v in (db.items() if isinstance(db, dict) else ()):
            if (k.isdigit() and isinstance(v, dict) and isinstance(v.get("fetched_at"), (int, float))
                    and (v.get("name") is None or isinstance(v.get("name"), str))):
                self.db[k] = {"name": v.get("name"), "fetched_at": v["fetched_at"]}
        self.cache = {k: v for k, v in (cache.items() if isinstance(cache, dict) else ())
                      if isinstance(k, str) and isinstance(v, str) and v}  # "id:sid" -> name

    # -- sources ----------------------------------------------------------
    def _scan_cache(self):
        found = {}
        for pattern in ("sublist_*.json", "hot_*.json"):
            for p in sorted(self.market_cache_dir.glob(pattern)):
                doc = read_json(p)
                data = doc.get("data") if isinstance(doc, dict) else None
                for obj in (data if isinstance(data, list) else [data]):
                    r = _row(obj, False)
                    if r:
                        found[f"{r['id']}:{r['sid']}"] = r["name"]
        return found

    def _save(self):
        atomic_write_json(self.index_path, {"schema": 1, "db": self.db, "cache": self.cache})

    def _refresh_cache(self):
        now = self.clock()
        if self._scanned is not None and now - self._scanned < SCAN_EVERY_S:
            return
        self._scanned = now
        found = self._scan_cache()
        merged = dict(self.cache, **found)
        if merged != self.cache:
            self.cache = merged
            self._save()

    def _need_db(self, item_id):
        if any(s["id"] == item_id for s in self.seed):
            return False
        rec = self.db.get(str(item_id))
        return rec is None or self.clock() - rec["fetched_at"] >= UTIL_DB_TTL_S

    def _name_ids(self, item_id):
        with self._lock:
            if not self._need_db(item_id):
                return
        got = self.utildb.lookup([item_id])
        if not got:
            return
        now = self.clock()
        with self._lock:
            for i, name in got.items():
                old = self.db.get(str(i))
                if name is None and old and old.get("name"):
                    old["fetched_at"] = now  # a miss never erases a known name
                else:
                    self.db[str(i)] = {"name": name, "fetched_at": now}
            self._save()

    def entries(self):
        """{(id, sid): name} merged seed > util/db > cache."""
        best = {}

        def put(key, name, src):
            if key not in best or _PREC[src] < best[key][0]:
                best[key] = (_PREC[src], name)
        for k, name in self.cache.items():
            i, _, sid = k.partition(":")
            if i.isdigit() and sid.isdigit():
                put((int(i), int(sid)), name, "cache")
        for k, rec in self.db.items():
            if rec.get("name"):
                put((int(k), 0), rec["name"], "db")
        for s in self.seed:
            put((s["id"], s["sid"]), s["name"], "seed")
        return {k: v[1] for k, v in best.items()}

    # -- query ------------------------------------------------------------
    def search(self, q):
        q = validate_query(q)
        if q.isdigit() and _ok_id(int(q)):
            self._name_ids(int(q))
        with self._lock:
            self._refresh_cache()
            entries = self.entries()
        needle = q.lower()
        hits = []
        for (i, sid), name in entries.items():
            low = name.lower()
            if str(i) == q:
                rank = 0
            elif low.startswith(needle):
                rank = 1
            elif needle in low:
                rank = 2
            else:
                continue
            hits.append((rank, low, i, sid, name))
        hits.sort()
        return [{"id": h[2], "sid": h[3], "name": h[4]} for h in hits[:MAX_RESULTS]]
