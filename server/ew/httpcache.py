"""Shared read-only HTTP cache + backoff (factored from plan 002 for plan 004).

One JSON file per key holding `{fetched_at, data}`, atomic writes, and a
persisted `backoff.json` beside it so restarts honour a running backoff. A
failure backs off `min(30 * 2**n, 1800)` s; a `Pending` answer ("being fetched,
retry later") is transient and retries after a flat PENDING_RETRY_S without
growing n. During backoff or failure the last cache is served `stale: true`
with `error`; no cache -> `data: null`. Read-only GET only (ToS floor).
"""

import datetime as _dt
import json
import threading
import time
import urllib.request
from pathlib import Path

from . import __version__
from .store import atomic_write_json

BACKOFF_BASE_S = 30
BACKOFF_MAX_S = 1800
PENDING_RETRY_S = 60


class UpstreamError(Exception):
    pass


class Pending(UpstreamError):
    """Upstream accepted the request but has no data yet (HTTP 202)."""


def iso(epoch):
    if epoch is None:
        return None
    return _dt.datetime.fromtimestamp(epoch, _dt.timezone.utc).replace(microsecond=0).isoformat()


def default_fetch(url, timeout):
    """Plain GET; raises on HTTP error or timeout, Pending on HTTP 202."""
    req = urllib.request.Request(url, method="GET",
                                 headers={"User-Agent": f"Ebonwake/{__version__}",
                                          "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (fixed https bases)
        if r.status == 202:
            raise Pending("being fetched, retry later")
        return r.read()


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def freshness(res):
    return {k: res[k] for k in ("fetched_at", "age_s", "ttl_s", "stale", "error")}


def _daemon(fn):
    threading.Thread(target=fn, name="ew-refresh", daemon=True).start()


class CachedClient:
    def __init__(self, fetch=None, clock=time.time, cache_dir=None):
        if cache_dir is None:
            raise ValueError("cache_dir is required")
        self.fetch = fetch or default_fetch
        self.clock = clock
        self.cache_dir = Path(cache_dir)
        self._lock = threading.Lock()      # guards _keylocks
        self._bo_lock = threading.Lock()   # guards backoff.json read-modify-write
        self._keylocks = {}
        self._inflight = set()             # keys with a background refresh running

    def _keylock(self, key):
        with self._lock:
            return self._keylocks.setdefault(key, threading.Lock())

    # -- paths / state ----------------------------------------------------
    def _cache_path(self, key):
        return self.cache_dir / f"{key}.json"

    def _backoff_path(self):
        return self.cache_dir / "backoff.json"

    def _backoffs(self):
        with self._bo_lock:
            doc = read_json(self._backoff_path())
        return doc if isinstance(doc, dict) else {}

    def key_backoff(self, key):
        bo = self._backoffs().get(key, {})
        if not (isinstance(bo, dict) and isinstance(bo.get("n", 0), int)
                and isinstance(bo.get("until", 0), (int, float))):
            return {}  # corrupt entry = no backoff, never a 500
        return bo

    def _set_backoff(self, key, entry):
        with self._bo_lock:
            doc = read_json(self._backoff_path())
            doc = doc if isinstance(doc, dict) else {}
            if entry:
                doc[key] = entry
            else:
                doc.pop(key, None)
            atomic_write_json(self._backoff_path(), doc)

    def _cached(self, key):
        cached = read_json(self._cache_path(key))
        if not (isinstance(cached, dict) and "data" in cached
                and isinstance(cached.get("fetched_at"), (int, float))
                and not isinstance(cached.get("fetched_at"), bool)):
            return None  # corrupt cache = no cache
        return cached

    # -- core -------------------------------------------------------------
    def cached_get(self, key, ttl, download):
        """`download()` returns the data or raises UpstreamError / Pending."""
        # Per-key lock: one in-flight fetch per key (dedupe); other keys and
        # fresh-cache reads never wait behind a slow upstream.
        with self._keylock(key):
            now = self.clock()
            cached = self._cached(key)
            if cached and now - cached["fetched_at"] < ttl:
                return self._result(cached, now, ttl, False, None)
            bo = self.key_backoff(key)
            if bo and now < bo.get("until", 0):
                return self._result(cached, now, ttl, True, bo.get("error") or "backoff")
            n = int(bo.get("n", 0)) if bo else 0
            try:
                data = download()
            except Pending as e:
                err = str(e)
                self._set_backoff(key, {"n": n, "until": now + PENDING_RETRY_S, "error": err,
                                        "pending": True})
                return self._result(cached, now, ttl, True, err)
            except UpstreamError as e:
                wait = min(BACKOFF_BASE_S * 2 ** n, BACKOFF_MAX_S)
                err = str(e)
                self._set_backoff(key, {"n": n + 1, "until": now + wait, "error": err})
                return self._result(cached, now, ttl, True, err)
            entry = {"fetched_at": now, "data": data}
            atomic_write_json(self._cache_path(key), entry)
            if bo:
                self._set_backoff(key, None)
            return self._result(entry, now, ttl, False, None)

    def refreshing(self, key):
        with self._lock:
            return key in self._inflight

    def refresh_async(self, key, ttl, download, spawn=None):
        """Start `cached_get` off the caller's thread unless one is already in
        flight for `key` or the cache is fresh. Returns True when one started.
        `spawn(fn)` is injectable (tests); default is a daemon thread."""
        cached = self._cached(key)
        if cached and self.clock() - cached["fetched_at"] < ttl:
            return False
        with self._lock:
            if key in self._inflight:
                return False
            self._inflight.add(key)

        def run():
            try:
                self.cached_get(key, ttl, download)
            except Exception:  # noqa: BLE001 - a refresh failure never kills the server
                pass
            finally:
                with self._lock:
                    self._inflight.discard(key)

        try:
            (spawn or _daemon)(run)
        except Exception:  # noqa: BLE001 - no thread = serve the cache, never a 500
            with self._lock:
                self._inflight.discard(key)
            return False
        return True

    def pending(self, key):
        """True while a refresh is running or upstream said "being fetched"."""
        if self.refreshing(key):
            return True
        bo = self.key_backoff(key)
        return bool(bo.get("pending")) and self.clock() < bo.get("until", 0)

    def peek(self, key, ttl):
        """Cache/backoff view without fetching, or None when nothing is known."""
        now = self.clock()
        cached = self._cached(key)
        bo = self.key_backoff(key)
        if not cached and not bo:
            return None
        stale = not cached or now - cached["fetched_at"] >= ttl
        return self._result(cached, now, ttl, stale, bo.get("error") if bo else None)

    @staticmethod
    def _result(entry, now, ttl, stale, error):
        if not entry:
            return {"data": None, "fetched_at": None, "age_s": None, "ttl_s": ttl,
                    "stale": True, "error": error}
        return {"data": entry["data"], "fetched_at": iso(entry["fetched_at"]),
                "age_s": int(now - entry["fetched_at"]), "ttl_s": ttl, "stale": stale,
                "error": error}
