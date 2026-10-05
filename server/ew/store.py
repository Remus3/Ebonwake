"""JSON document store: one file per domain, atomic writes, schema version."""

import json
import os
import threading
from pathlib import Path

SCHEMA = 1
_LOCK = threading.Lock()


def atomic_write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8",
                   newline="\n")
    tmp.replace(path)


class Store:
    def __init__(self, root):
        self.root = Path(root)

    def _path(self, domain):
        if not domain.replace("_", "").isalnum():
            raise ValueError(f"bad domain name: {domain!r}")
        return self.root / f"{domain}.json"

    def get(self, domain, default=None):
        p = self._path(domain)
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {} if default is None else default
        return doc.get("data", {})

    def put(self, domain, data):
        with _LOCK:
            atomic_write_json(self._path(domain), {"schema": SCHEMA, "data": data})
