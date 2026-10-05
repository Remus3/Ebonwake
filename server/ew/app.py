"""EW server: loopback-only stdlib HTTP server.

Routes: /api/health, /api/version (fleet P0-5), /api/state, /events (SSE),
/ and /app/* (static dashboard + overlay assets, browser fallback),
/api/market/{watch,item,hot} (plan 002), /api/today (plan 003),
/api/progress (plan 004), and POST /api/market/watch + /api/today +
/api/progress behind one shared guard.
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

from urllib.parse import parse_qs

from . import __version__, market, ports, progress, today
from .store import Store

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = REPO_ROOT / "app"
KIT_TOKENS = REPO_ROOT / "ops" / "fleet_kit" / "tokens.css"
RUNTIME = REPO_ROOT / "ops" / "runtime"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
MAX_POST_BYTES = 4096
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


def config_market_watch(root=REPO_ROOT):
    """`market_watch` ids from gitignored config/local.json, or []."""
    try:
        doc = json.loads((Path(root) / "config" / "local.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    ids = doc.get("market_watch") if isinstance(doc, dict) else None
    return ids if isinstance(ids, list) else []


class EWServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, store_root=None, commit=None, sse_interval=15.0,
                 market_client=None, market_seed=None, today_clock=None,
                 profile_client=None, profile_cfg=None):
        super().__init__(addr, Handler)
        self.started = _now_iso()
        self.commit = commit
        self.cfg_hash = config_hash()
        self.store = Store(store_root or RUNTIME / "store")
        self.sse_interval = sse_interval
        seed = config_market_watch() if market_seed is None else market_seed
        self.market = market.MarketService(market_client or market.ArshaClient(),
                                           market.Watchlist(self.store, seed=seed))
        self.today = today.TodayService(self.store, clock=today_clock or time.time)
        if profile_client is None:
            cfg = progress.config_profile(REPO_ROOT) if profile_cfg is None else profile_cfg
            family = progress.family_from_config(cfg)
            if family is not None:
                profile_client = progress.ProfileClient(family, base_url=cfg.get("base_url"))
        self.progress = progress.ProgressService(self.store, profile_client)

    def version(self):
        return {"commit": self.commit, "started": self.started, "pid": os.getpid(),
                "config_hash": self.cfg_hash, "schema": 1}

    def state(self):
        return {"app": "ebonwake", "version": __version__, "tabs": TABS,
                "sources": {"market": self.market.source(), "today": self.today.source(),
                            "profile": self.progress.source()},
                "now": _now_iso()}


class Handler(BaseHTTPRequestHandler):
    server_version = "Ebonwake"
    sys_version = ""

    def log_message(self, fmt, *args):  # quiet: no stderr spam under pythonw
        pass

    def _host_ok(self):
        host = (self.headers.get("Host") or "").strip()
        name = host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0] + "]"
        return name in LOOPBACK_HOSTS

    def _send(self, code, body, ctype="application/json", cors=True):
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if cors:
            self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _market_item(self, query):
        q = parse_qs(query)
        try:
            item_id = int(q["id"][0])
            sid = int(q.get("sid", ["0"])[0])
        except (KeyError, ValueError, IndexError):
            return self._send(400, {"error": "id and sid must be ints"})
        if not (0 <= item_id <= market.MAX_ID and 0 <= sid <= market.MAX_ID):
            return self._send(400, {"error": "id and sid out of range"})
        return self._send(200, self.server.market.item(item_id, sid))

    def do_GET(self):  # noqa: N802
        if not self._host_ok():
            return self._send(403, {"error": "host"})
        path, _, query = self.path.partition("?")
        if path == "/api/health":
            return self._send(200, {"ok": True, "app": "ebonwake", "port": ports.SERVER})
        if path == "/api/version":
            return self._send(200, self.server.version())
        if path == "/api/state":
            return self._send(200, self.server.state())
        if path == "/api/market/watch":
            return self._send(200, self.server.market.watch())
        if path == "/api/market/item":
            return self._market_item(query)
        if path == "/api/market/hot":
            return self._send(200, self.server.market.hot())
        if path == "/api/today":
            return self._send(200, self.server.today.view())
        if path == "/api/progress":
            return self._send(200, self.server.progress.view())
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

    def _post_market_watch(self, body):
        if len(body) != 1 or not ({"add", "remove"} & set(body)):
            raise ValueError("body must be {\"add\": {...}} or {\"remove\": {...}}")
        wl = self.server.market.watchlist
        return {"watch": wl.add(body["add"]) if "add" in body else wl.remove(body["remove"])}

    def _post_today(self, body):
        ops = {"tick", "untick", "add", "remove", "move"}
        if len(body) != 1 or not (ops & set(body)):
            raise ValueError("body must be one of {tick|untick|add|remove|move: ...}")
        (op, arg), = body.items()
        return getattr(self.server.today, op)(arg)

    def _post_progress(self, body):
        ops = {"character": "set_character", "step": "step", "add_track": "add_track",
               "remove_track": "remove_track"}
        if len(body) != 1 or not (set(ops) & set(body)):
            raise ValueError("body must be one of {character|step|add_track|remove_track: ...}")
        (op, arg), = body.items()
        return getattr(self.server.progress, ops[op])(arg)

    POST_ROUTES = {"/api/market/watch": _post_market_watch, "/api/today": _post_today,
                   "/api/progress": _post_progress}

    def do_POST(self):  # noqa: N802
        """Shared guard for every POST route: loopback Host + application/json +
        <= 4 KiB + a JSON object body. No CORS header is sent and OPTIONS is never
        answered, so browser pages cannot make this request. A route handler gets
        the parsed dict and returns the 200 body or raises ValueError (-> 400)."""
        unread = [True]

        def reply(code, body):
            if unread[0]:  # drain a small unread body so the close is not a reset
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    n = 0
                if 0 < n <= 1 << 16:
                    self.rfile.read(n)
            self.close_connection = True
            return self._send(code, body, cors=False)

        if not self._host_ok():
            return reply(403, {"error": "host"})
        route = self.POST_ROUTES.get(self.path.split("?", 1)[0])
        if route is None:
            return reply(404, {"error": "not found"})
        ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            return reply(415, {"error": "content-type must be application/json"})
        try:
            length = int(self.headers.get("Content-Length") or "")
        except ValueError:
            return reply(411, {"error": "length required"})
        if length < 0 or length > MAX_POST_BYTES:
            return reply(413, {"error": f"body over {MAX_POST_BYTES} bytes"})
        raw = self.rfile.read(length)
        unread[0] = False
        try:
            body = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return reply(400, {"error": "bad json"})
        if not isinstance(body, dict):
            return reply(400, {"error": "body must be a JSON object"})
        try:
            out = route(self, body)
        except ValueError as e:
            return reply(400, {"error": str(e)})
        return reply(200, out)

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


def make_server(port=ports.SERVER, store_root=None, commit=None, sse_interval=15.0,
                market_client=None, market_seed=None, today_clock=None,
                profile_client=None, profile_cfg=None):
    return EWServer(("127.0.0.1", port), store_root=store_root, commit=commit,
                    sse_interval=sse_interval, market_client=market_client,
                    market_seed=market_seed, today_clock=today_clock,
                    profile_client=profile_client, profile_cfg=profile_cfg)


def main(argv=None):
    srv = make_server(commit=read_commit())
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0
