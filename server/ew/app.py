"""EW server: loopback-only stdlib HTTP server.

Routes: /api/health, /api/version (fleet P0-5), /api/state, /events (SSE),
/ and /app/* (static dashboard + overlay assets, browser fallback).
"""

import datetime as _dt
import hashlib
import json
import mimetypes
import os
import shutil
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__, ports
from .store import Store

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = REPO_ROOT / "app"
KIT_TOKENS = REPO_ROOT / "ops" / "fleet_kit" / "tokens.css"
RUNTIME = REPO_ROOT / "ops" / "runtime"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0

TABS = [
    {"id": "today", "title": "Today", "plan": "003"},
    {"id": "market", "title": "Market", "plan": "002"},
    {"id": "progress", "title": "Progress", "plan": "004"},
    {"id": "grind", "title": "Grind", "plan": "005"},
    {"id": "events", "title": "Events", "plan": "006"},
    {"id": "deadeye", "title": "Deadeye", "plan": "007"},
    {"id": "system", "title": "System", "plan": "001"},
]


def _now_iso():
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def read_commit(root=REPO_ROOT):
    """HEAD commit at bind time, or None. git is resolved from PATH, never cwd."""
    git = shutil.which("git")
    if not git:
        return None
    try:
        out = subprocess.run([git, "-C", str(root), "rev-parse", "HEAD"], capture_output=True,
                             text=True, timeout=5, creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    sha = out.stdout.strip()
    return sha if out.returncode == 0 and len(sha) == 40 else None


def config_hash(root=REPO_ROOT):
    p = Path(root) / "config" / "local.json"
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return None


class EWServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, store_root=None, commit=None, sse_interval=15.0):
        super().__init__(addr, Handler)
        self.started = _now_iso()
        self.commit = commit
        self.cfg_hash = config_hash()
        self.store = Store(store_root or RUNTIME / "store")
        self.sse_interval = sse_interval

    def version(self):
        return {"commit": self.commit, "started": self.started, "pid": os.getpid(),
                "config_hash": self.cfg_hash, "schema": 1}

    def state(self):
        return {"app": "ebonwake", "version": __version__, "tabs": TABS,
                "sources": {}, "now": _now_iso()}


class Handler(BaseHTTPRequestHandler):
    server_version = "Ebonwake"
    sys_version = ""

    def log_message(self, fmt, *args):  # quiet: no stderr spam under pythonw
        pass

    def _host_ok(self):
        host = (self.headers.get("Host") or "").strip()
        name = host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0] + "]"
        return name in LOOPBACK_HOSTS

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if not self._host_ok():
            return self._send(403, {"error": "host"})
        path = self.path.split("?", 1)[0]
        if path == "/api/health":
            return self._send(200, {"ok": True, "app": "ebonwake", "port": ports.SERVER})
        if path == "/api/version":
            return self._send(200, self.server.version())
        if path == "/api/state":
            return self._send(200, self.server.state())
        if path == "/events":
            return self._sse()
        if path == "/":
            path = "/app/dashboard/index.html"
        if path == "/ops/fleet_kit/tokens.css":
            return self._file(KIT_TOKENS)
        if path.startswith("/app/"):
            target = (APP_DIR / path[len("/app/"):]).resolve()
            if APP_DIR.resolve() in target.parents and "node_modules" not in target.parts:
                return self._file(target)
        return self._send(404, {"error": "not found"})

    def _file(self, target):
        try:
            data = Path(target).read_bytes()
        except OSError:
            return self._send(404, {"error": "not found"})
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if str(target).endswith(".js"):
            ctype = "text/javascript"
        return self._send(200, data, ctype)

    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            while True:
                msg = json.dumps({"type": "heartbeat", "now": _now_iso()})
                self.wfile.write(f"data: {msg}\n\n".encode("utf-8"))
                self.wfile.flush()
                time.sleep(self.server.sse_interval)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            return


def make_server(port=ports.SERVER, store_root=None, commit=None, sse_interval=15.0):
    return EWServer(("127.0.0.1", port), store_root=store_root, commit=commit,
                    sse_interval=sse_interval)


def main(argv=None):
    srv = make_server(commit=read_commit())
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0
