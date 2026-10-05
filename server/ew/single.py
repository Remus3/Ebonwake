"""Single-instance probe for the EW server (plan 010 item 1).

Before binding, `python -m server.ew` asks the registry port's /api/version who
is there. "ew" = an EW server already answers (the fleet P0-5 version contract
plus the Ebonwake Server banner): exit 0 without binding. "none" = nothing
listens: bind. "foreign" = something else answers: bind and let the OS refuse.
Pure, with an injectable opener, so tests never touch the network.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from . import ports

VERSION_URL = f"http://127.0.0.1:{ports.SERVER}/api/version"
VERSION_KEYS = frozenset({"commit", "started", "pid", "config_hash", "schema"})
BANNER = "Ebonwake"
MAX_BODY = 65536


def is_ew_version(doc, server_header) -> bool:
    return (isinstance(doc, dict) and set(doc) == VERSION_KEYS and doc.get("schema") == 1
            and isinstance(server_header, str) and server_header.startswith(BANNER))


def probe(url: str = VERSION_URL, opener=urllib.request.urlopen, timeout: float = 0.75) -> str:
    try:
        with opener(url, timeout=timeout) as r:
            body = r.read(MAX_BODY)
            header = r.headers.get("Server")
    except urllib.error.HTTPError:
        return "foreign"  # something answered, just not with a version doc
    except (urllib.error.URLError, OSError, ValueError):
        return "none"
    try:
        doc = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return "foreign"
    return "ew" if is_ew_version(doc, header) else "foreign"
