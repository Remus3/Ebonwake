"""EW server: loopback-only stdlib HTTP server.

Routes: /api/health, /api/version (fleet P0-5), /api/state, /events (SSE),
/ (302 to the dashboard page, plan 020) and /app/* (static dashboard + overlay
assets, browser fallback),
/api/market/{watch,item,hot} (plan 002), /api/today (plan 003),
/api/progress (plan 004), /api/grind (plan 005), /api/events (plan 006; plan 014
adds its `suggested` coupon block),
/api/deadeye (plan 007), /api/game (plan 008), /api/leveling (plan 011),
/api/spots (plan 012, GET only), and POST
/api/market/watch + /api/today + /api/progress + /api/grind + /api/events + /api/deadeye +
/api/ocr (plan 009) + /api/leveling behind one shared guard.
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

from . import (__version__, coupons, deadeye, events, gamewatch, grind, leveling, market, ocr,
               ports, progress, single, spots, today)
from .store import Store

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = REPO_ROOT / "app"
KIT_TOKENS = REPO_ROOT / "ops" / "fleet_kit" / "tokens.css"
RUNTIME = REPO_ROOT / "ops" / "runtime"
DASHBOARD_PAGE = "/app/dashboard/index.html"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
MAX_POST_BYTES = 4096
# A 20000-char deadeye note JSON-escaped at 6 bytes/char (\uXXXX) is 120000 bytes;
# 128 KiB covers it plus the envelope. Raised for this route only (plan 007).
MAX_DEADEYE_POST_BYTES = 131072
# An over-cap body up to this size is read and dropped before the 413 so the
# close is not a TCP reset (a 64 KiB bound reset deadeye's 128 KiB+ bodies and
# made test_route_cap_is_per_route flaky with ConnectionAborted on Windows).
DRAIN_MAX_BYTES = 1 << 20
POST_CAPS = {"/api/deadeye": MAX_DEADEYE_POST_BYTES}
SSE_TICK_S = 0.25  # SSE wakes this often to notice a leveling change (plan 011)
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
                 profile_client=None, profile_cfg=None, grind_clock=None, events_clock=None,
                 deadeye_clock=None, game_watch=None, game_cfg=None, game_poll=False,
                 ocr_runner=None, ocr_cache_dir=None, leveling_clock=None,
                 coupon_client=None, coupon_spawn=None):
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
        # Season level objectives auto-tick from the newest XP sample (plan 013);
        # plan 023: the newest started XP epoch flags bracket tables to re-verify.
        self.progress = progress.ProgressService(
            self.store, profile_client, level=lambda: self.leveling.current_level(),
            epoch=lambda: self.leveling.active_epoch())
        # Plan 018: buff presets follow the newest started XP epoch.
        self.grind = grind.GrindService(self.store, clock=grind_clock or time.time,
                                        epoch=lambda: self.leveling.active_epoch())
        # XP stack counts armed grind buffs that carry an xp_pct (plan 011).
        self.leveling = leveling.LevelingService(self.store, clock=leveling_clock or time.time,
                                                 buffs=lambda: self.grind.view()["buffs"])
        # Plan 012: plan 004's character + plan 005's per-spot silver/h; plan
        # 018: the newest started XP epoch flags rows to re-verify.
        self.spots = spots.SpotsService.from_file(
            character=lambda: self.progress.view(refresh=False)["character"],
            grind=self.grind.view, epoch=self.leveling.active_epoch)
        # Plan 024: level-gated deadlines show read-only in "Ending soon".
        self.events = events.EventsService(self.store, clock=events_clock or time.time,
                                           deadlines=self.leveling.deadline_rows)
        # Coupon suggestions (plan 014): off unless a client is passed; only main()
        # passes the live one, so no test ever reaches the network.
        self.coupons = coupons.CouponService(coupon_client, self.events, spawn=coupon_spawn)
        self.deadeye = deadeye.DeadeyeService(self.store, clock=deadeye_clock or time.time)
        if game_watch is None:
            # Only main() passes the real config; a bare make_server (every test)
            # never reads config/local.json (plan 008 refute round 1).
            cfg = {} if game_cfg is None else game_cfg
            game_watch = gamewatch.GameWatch.from_config(cfg)
        self.game = game_watch
        if ocr_cache_dir is None:  # beside the store, so a test store keeps OCR in tmp too
            ocr_cache_dir = Path(store_root).parent / "ocr" if store_root else RUNTIME / "ocr"
        self.ocr = ocr.OcrService(self.game, ocr_cache_dir, runner=ocr_runner)
        if game_poll:  # off by default so tests never probe processes; main() turns it on
            self.game.start()

    def server_close(self):
        self.game.stop()
        super().server_close()

    def version(self):
        return {"commit": self.commit, "started": self.started, "pid": os.getpid(),
                "config_hash": self.cfg_hash, "schema": 1}

    def state(self):
        return {"app": "ebonwake", "version": __version__, "tabs": TABS,
                "sources": {"market": self.market.source(), "today": self.today.source(),
                            "profile": self.progress.source(), "grind": self.grind.source(),
                            "events": self.events.source(), "coupons": self.coupons.source(),
                            "deadeye": self.deadeye.source(),
                            "game": self.game.source(), "leveling": self.leveling.source()},
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

    def _redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

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
        if path == "/api/grind":
            return self._send(200, self.server.grind.view())
        if path == "/api/events":
            return self._send(200, dict(self.server.events.view(),
                                        suggested=self.server.coupons.view(refresh=True)))
        if path == "/api/deadeye":
            return self._send(200, self.server.deadeye.view())
        if path == "/api/game":
            return self._send(200, self.server.game.view())
        if path == "/api/leveling":
            return self._send(200, self.server.leveling.view())
        if path == "/api/spots":
            try:
                return self._send(200, self.server.spots.view(parse_qs(query)))
            except ValueError as e:
                return self._send(400, {"error": str(e)})
        if path == "/events":
            return self._sse()
        if path == "/":  # plan 020: redirect so relative asset paths resolve
            return self._redirect(DASHBOARD_PAGE)
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
               "remove_track": "remove_track", "claim": "claim", "obj_add": "obj_add",
               "obj_edit": "obj_edit", "obj_del": "obj_del", "brackets_set": "brackets_set"}
        if len(body) != 1 or not (set(ops) & set(body)):
            raise ValueError("body must be one of {character|step|add_track|remove_track|"
                             "claim|obj_add|obj_edit|obj_del|brackets_set: ...}")
        (op, arg), = body.items()
        return getattr(self.server.progress, ops[op])(arg)

    def _post_grind(self, body):
        ops = {"start", "stop", "log", "delete", "add_spot", "buff", "clear_buff"}
        if len(body) != 1 or not (ops & set(body)):
            raise ValueError("body must be one of "
                             "{start|stop|log|delete|add_spot|buff|clear_buff: ...}")
        (op, arg), = body.items()
        return getattr(self.server.grind, op)(arg)

    def _post_events(self, body):
        ops = {"add", "edit", "done", "delete", "purge_expired"}
        if len(body) != 1 or not (ops & set(body)):
            raise ValueError("body must be one of {add|edit|done|delete|purge_expired: ...}")
        (op, arg), = body.items()
        out = getattr(self.server.events, op)(arg)
        out["suggested"] = self.server.coupons.view(refresh=False)  # a POST never fetches
        return out

    def _post_deadeye(self, body):
        ops = {"note", "add_step", "edit_step", "step_done", "delete_step", "move_step"}
        if len(body) != 1 or not (ops & set(body)):
            raise ValueError("body must be one of "
                             "{note|add_step|edit_step|step_done|delete_step|move_step: ...}")
        (op, arg), = body.items()
        return getattr(self.server.deadeye, op)(arg)

    def _post_ocr(self, body):
        return self.server.ocr.read(body)

    def _post_leveling(self, body):
        ops = {"sample": "sample", "sample_del": "sample_del", "hot_add": "hot_add",
               "hot_del": "hot_del", "milestones": "set_milestones",
               "epoch_add": "epoch_add", "epoch_del": "epoch_del",
               "deadline_set": "deadline_set", "deadline_del": "deadline_del"}
        if len(body) != 1 or not (set(ops) & set(body)):
            raise ValueError("body must be one of {sample|sample_del|hot_add|hot_del|"
                             "milestones|epoch_add|epoch_del|deadline_set|deadline_del: ...}")
        (op, arg), = body.items()
        return getattr(self.server.leveling, ops[op])(arg)

    POST_ROUTES = {"/api/market/watch": _post_market_watch, "/api/today": _post_today,
                   "/api/progress": _post_progress, "/api/grind": _post_grind,
                   "/api/events": _post_events, "/api/deadeye": _post_deadeye,
                   "/api/ocr": _post_ocr, "/api/leveling": _post_leveling}

    def do_POST(self):  # noqa: N802
        """Shared guard for every POST route: loopback Host + application/json +
        <= 4 KiB (more only via POST_CAPS) + a JSON object body. No CORS header is sent and OPTIONS is never
        answered, so browser pages cannot make this request. A route handler gets
        the parsed dict and returns the 200 body or raises ValueError (-> 400)."""
        unread = [True]

        def reply(code, body):
            if unread[0]:  # drain a small unread body so the close is not a reset
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    n = 0
                while 0 < n <= DRAIN_MAX_BYTES:  # chunked; loopback only, bounded
                    chunk = self.rfile.read(min(n, 1 << 16))
                    if not chunk:
                        break
                    n -= len(chunk)
            self.close_connection = True
            return self._send(code, body, cors=False)

        if not self._host_ok():
            return reply(403, {"error": "host"})
        rpath = self.path.split("?", 1)[0]
        route = self.POST_ROUTES.get(rpath)
        cap = POST_CAPS.get(rpath, MAX_POST_BYTES)
        if route is None:
            return reply(404, {"error": "not found"})
        ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            return reply(415, {"error": "content-type must be application/json"})
        try:
            length = int(self.headers.get("Content-Length") or "")
        except ValueError:
            return reply(411, {"error": "length required"})
        if length < 0 or length > cap:
            return reply(413, {"error": f"body over {cap} bytes"})
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
        except ocr.OcrError as e:
            return reply(502, {"error": f"ocr failed: {e}"})
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
        game, lev = self.server.game, self.server.leveling
        seq, lseq = game.seq, lev.seq
        interval = self.server.sse_interval
        try:
            msg = json.dumps({"type": "heartbeat", "now": _now_iso()})
            self.wfile.write(f"data: {msg}\n\n".encode("utf-8"))
            self.wfile.flush()
            quiet = time.monotonic() + interval
            while True:
                # Named events (game, leveling) leave onmessage (heartbeat)
                # consumers untouched. Leveling changes are picked up within one
                # SSE_TICK_S slice of the game wait.
                left = quiet - time.monotonic()
                new = game.wait_change(seq, max(0.0, min(SSE_TICK_S, left)))
                out = []
                if new != seq:
                    seq = new
                    out.append(f"event: game\ndata: {json.dumps(game.view())}\n\n")
                if lev.seq != lseq:
                    lseq = lev.seq
                    try:  # a bad buff source never ends the stream (refute r1 minor 3)
                        out.append(f"event: leveling\ndata: {json.dumps(lev.view())}\n\n")
                    except Exception:  # noqa: BLE001
                        pass
                if not out and time.monotonic() >= quiet:
                    msg = json.dumps({"type": "heartbeat", "now": _now_iso()})
                    out.append(f"data: {msg}\n\n")
                if out:
                    self.wfile.write("".join(out).encode("utf-8"))
                    self.wfile.flush()
                    quiet = time.monotonic() + interval
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            return


def make_server(port=ports.SERVER, store_root=None, commit=None, sse_interval=15.0,
                market_client=None, market_seed=None, today_clock=None,
                profile_client=None, profile_cfg=None, grind_clock=None, events_clock=None,
                deadeye_clock=None, game_watch=None, game_cfg=None, game_poll=False,
                ocr_runner=None, ocr_cache_dir=None, leveling_clock=None,
                coupon_client=None, coupon_spawn=None):
    return EWServer(("127.0.0.1", port), store_root=store_root, commit=commit,
                    sse_interval=sse_interval, market_client=market_client,
                    market_seed=market_seed, today_clock=today_clock,
                    profile_client=profile_client, profile_cfg=profile_cfg,
                    grind_clock=grind_clock, events_clock=events_clock,
                    deadeye_clock=deadeye_clock, game_watch=game_watch,
                    game_cfg=game_cfg, game_poll=game_poll, ocr_runner=ocr_runner,
                    ocr_cache_dir=ocr_cache_dir, leveling_clock=leveling_clock,
                    coupon_client=coupon_client, coupon_spawn=coupon_spawn)


def main(argv=None, probe=None):
    # Single instance (plan 010): an EW server already on the port wins.
    if (probe or single.probe)() == "ew":
        return 0
    srv = make_server(commit=read_commit(), game_poll=True,
                      game_cfg=gamewatch.config_bdo(REPO_ROOT),
                      coupon_client=coupons.CouponClient())
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0
