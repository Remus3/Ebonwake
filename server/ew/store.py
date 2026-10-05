"""JSON document store: one file per domain, atomic writes, schema version."""

import json
import os
import threading
import time
from pathlib import Path

SCHEMA = 1
_LOCK = threading.Lock()


def atomic_write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8",
                   newline="\n")
    # Windows refuses a replace while another handle reads the target
    # (PermissionError); readers hold handles for microseconds, so retry briefly.
    for attempt in range(20):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.01 * (attempt + 1))


class Store:
    def __init__(self, root):
        self.root = Path(root)

    def _path(self, domain):
        if not domain.replace("_", "").isalnum():
            raise ValueError(f"bad domain name: {domain!r}")
        return self.root / f"{domain}.json"

    def get(self, domain, default=None):
        p = self._path(domain)
        empty = {} if default is None else default
        with _LOCK:
            try:
                doc = json.loads(p.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return empty
            except (ValueError, UnicodeDecodeError, OSError):
                doc = None
            data = doc.get("data") if isinstance(doc, dict) else None
            if not isinstance(data, dict):
                # Corrupt doc degrades to empty, never a 500. The bad bytes are
                # kept beside it (renamed, never deleted) for a human to inspect.
                try:
                    p.replace(p.with_name(f"{p.name}.corrupt-{int(time.time())}"))
                except OSError:
                    pass
                return empty
            return data

    def put(self, domain, data):
        with _LOCK:
            atomic_write_json(self._path(domain), {"schema": SCHEMA, "data": data})
