"""Game watch (plan 008 slice A): session-log tail + ScreenShot folder watcher.

Read-only, nothing is ever sent to the game. Inputs (research 0001 section 4):
- the newest `<install_dir>/Log/Client_YYYY-MM-DD_HHMMSS.json` (UTF-16LE, one
  JSON object per line), opened read-only with the default share mode so the
  game is never blocked;
- a process LIST from `tasklist /FI` (never a handle to the game process, never
  its memory);
- a directory listing of `<documents_dir>/ScreenShot` (stat only, files never
  opened; OCR is plan 009).
Paths come only from gitignored config/local.json `bdo`; without
`install_dir` the watcher reports `unconfigured` and never guesses a path. One
daemon thread polls every 2 s; clock and tasklist are injectable for tests.

Liveness (operator QA 2026-10-05): a definite process-list answer wins over the
log. Game gone => not_running at once, even when the log was written seconds
ago (the old 120 s recent-log grace made a closed game read "running" for ~2
min). The log-recency fallback is used only when tasklist itself fails. A
terminal log line ("terminating app: ExitInstance") ends the session at once.
"""

import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

POLL_S = 2.0
RECENT_LOG_S = 120
MAX_SHOTS = 50
MAX_EVENT_TEXT = 200
MAX_READ = 4 << 20  # bytes per poll; a bigger backlog drains over later polls
PROCESS = "BlackDesert64.exe"
LOG_RE = re.compile(r"^Client_\d{4}-\d{2}-\d{2}_\d{6}\.json$")
SHOT_EXT = (".jpg", ".jpeg", ".png", ".bmp")
STATES = ("unconfigured", "not_running", "running", "logged_in", "disconnected")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
_BOM = b"\xff\xfe"

# Ordered, case-insensitive WORD-BOUNDED patterns over the `Log` field; first
# match wins. Disconnect rows come first so "login ... disconnected" is a
# disconnect. Word boundaries keep "catalog index" / "CatalogInfo" / "blogin"
# from reading as a login (plan 008 refute round 1). Real wording is confirmed
# by the plan 008 operator check (data change only).
CLASSIFIERS = (
    (r"\bterminating app\b", "exited"),
    (r"\bexit ?instance\b", "exited"),
    (r"\bdisconnect(?:ed|ion)?\b", "disconnected"),
    (r"\breconnect fail", "disconnected"),
    (r"\bconnection lost\b", "disconnected"),
    (r"\blog ?out\b", "running"),
    (r"\bexit(?:ed|ing)?\b", "running"),
    (r"\blog(?:ged)? ?in\b", "logged_in"),
    (r"\bserver ?select", "logged_in"),
    (r"\bselect ?server", "logged_in"),
)
# Terminal classifier state: the client is shutting down; reported as not_running.
TERMINAL = "exited"
_CAMEL = re.compile(r"([a-z0-9])([A-Z])")
_CLASSIFIERS = tuple((re.compile(p), s) for p, s in CLASSIFIERS)


def _iso(ts):
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).replace(microsecond=0).isoformat()


def config_bdo(root):
    """`bdo` object from gitignored config/local.json, or {}."""
    try:
        doc = json.loads((Path(root) / "config" / "local.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    b = doc.get("bdo") if isinstance(doc, dict) else None
    return b if isinstance(b, dict) else {}


def classify(text):
    """`Log` text -> logged_in | running | disconnected | exited, or None."""
    # camelCase and snake_case tokens split into words first, so "OnLogin",
    # "login_success" and "DisconnectedFromServer" still match word-bounded rows
    # (plan 008 refute round 2 note).
    if isinstance(text, str):
        text = _CAMEL.sub(r"\1 \2", text).replace("_", " ")
    low = text.lower() if isinstance(text, str) else ""
    for pattern, state in _CLASSIFIERS:
        if pattern.search(low):
            return state
    return None


def split_lines(raw):
    """UTF-16LE bytes -> (complete decoded lines, bytes consumed). A line ends at
    an even-aligned 0a 00; the partial tail is left for the next poll."""
    lines, pos, start = [], 0, 0
    while True:
        i = raw.find(b"\n\x00", pos)
        if i < 0:
            break
        if i % 2:
            pos = i + 1
            continue
        lines.append(raw[start:i].decode("utf-16-le", errors="replace").rstrip("\r"))
        start = pos = i + 2
    return lines, start


def parse_line(line):
    """One log line -> {Date, LogType, Log} dict, or None."""
    line = line.lstrip(chr(0xFEFF)).strip()
    if not line:
        return None
    try:
        doc = json.loads(line)
    except ValueError:
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get("Log"), str):
        return None
    return doc


def _clean(v):
    s = v if isinstance(v, str) else ("" if v is None else str(v))
    return "".join(ch for ch in s if ord(ch) >= 32 and ord(ch) != 127)[:MAX_EVENT_TEXT]


def _tasklist_exe():
    root = os.environ.get("SystemRoot")
    if root:
        p = Path(root) / "System32" / "tasklist.exe"
        if p.is_file():
            return str(p)
    return shutil.which("tasklist") or "tasklist"


def process_listed(run=subprocess.run):
    """True when tasklist lists BlackDesert64.exe, False when it definitely does
    not, None when tasklist failed (unknown). Process LIST only: no handle, no
    memory, no input."""
    args = [_tasklist_exe(), "/FI", f"IMAGENAME eq {PROCESS}", "/FO", "CSV", "/NH"]
    try:
        out = run(args, capture_output=True, text=True, timeout=5, creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    for line in (out.stdout or "").splitlines():
        first = line.split(",", 1)[0].strip().strip('"')
        if first.lower() == PROCESS.lower():
            return True
    return False


def _dir(v):
    return v if isinstance(v, str) and v.strip() else None


class GameWatch:
    """State: unconfigured | not_running | running | logged_in | disconnected."""

    def __init__(self, install_dir=None, documents_dir=None, clock=time.time, tasklist=None):
        install_dir, documents_dir = _dir(install_dir), _dir(documents_dir)
        self.log_dir = Path(install_dir) / "Log" if install_dir else None
        self.shot_dir = Path(documents_dir) / "ScreenShot" if documents_dir else None
        self.clock = clock
        self.tasklist = tasklist if tasklist is not None else process_listed
        self.started = clock()
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self.seq = 0
        self._state = None
        self._since = None
        self._updated = None
        self._log_file = None
        self._offset = 0
        self._log_state = None  # last classified state of the current session
        self._last_event = None
        self._shots = []
        self._stop = threading.Event()
        self._thread = None
        # Plan 046: `fn(prev, new, at)` called after each state change, outside
        # the lock; a failing listener never breaks the poll.
        self.listeners = []

    @classmethod
    def from_config(cls, cfg, clock=time.time, tasklist=None):
        cfg = cfg if isinstance(cfg, dict) else {}
        return cls(cfg.get("install_dir"), cfg.get("documents_dir"), clock=clock,
                   tasklist=tasklist)

    @property
    def configured(self):
        return self.log_dir is not None

    # -- polling ---------------------------------------------------------------

    def _newest_log(self):
        try:
            names = [e.name for e in os.scandir(self.log_dir)
                     if LOG_RE.match(e.name) and e.is_file()]
        except OSError:
            return None
        return max(names) if names else None

    def _tail(self, name):
        """Read new complete lines of `name` from the stored offset; returns the
        file mtime or None."""
        if name != self._log_file:
            self._log_file, self._offset = name, 0
            self._log_state, self._last_event = None, None
        path = self.log_dir / name
        try:
            with open(path, "rb") as f:  # read-only; default share mode never blocks the game
                st = os.fstat(f.fileno())
                if st.st_size < self._offset:
                    self._offset, self._log_state = 0, None
                f.seek(self._offset)
                raw = f.read(MAX_READ)
        except OSError:
            return None
        skip = 2 if self._offset == 0 and raw.startswith(_BOM) else 0
        lines, used = split_lines(raw[skip:])
        self._offset += skip + used
        for line in lines:
            doc = parse_line(line)
            state = classify(doc["Log"]) if doc else None
            if state is None:
                continue
            if self._log_state == TERMINAL:  # sticky until a new log file or the
                continue                     # process is seen gone (refute r1)
            self._log_state = state
            self._last_event = {"date": _clean(doc.get("Date")), "type": _clean(doc.get("LogType")),
                                "log": _clean(doc["Log"]), "state": state}
        return st.st_mtime

    def _screenshots(self):
        if self.shot_dir is None:
            return []
        out = []
        try:
            with os.scandir(self.shot_dir) as it:
                for e in it:
                    if not e.name.lower().endswith(SHOT_EXT) or not e.is_file():
                        continue
                    st = e.stat()
                    if st.st_mtime >= self.started:
                        out.append((st.st_mtime, e.name, st.st_size))
        except OSError:
            return []
        out.sort(reverse=True)
        return [{"name": n, "size": sz, "mtime": _iso(m)} for m, n, sz in out[:MAX_SHOTS]]

    def poll(self):
        """One poll step; safe to call any time (the thread calls it every 5 s)."""
        now = self.clock()
        shots = self._screenshots()
        with self._lock:
            if not self.configured:
                state = "unconfigured"
            else:
                name = self._newest_log()
                mtime = self._tail(name) if name else None
                listed = self.tasklist()
                if listed is None:  # probe failed: fall back to log recency
                    listed = mtime is not None and now - mtime <= RECENT_LOG_S
                if listed and self._log_state != TERMINAL:
                    state = self._log_state or "running"
                elif listed:  # shutting down: terminal line seen, process lingers
                    state = "not_running"
                else:
                    state, self._log_state = "not_running", None
            self._shots = shots
            self._updated = now
            prev, changed = self._state, state != self._state
            if changed:
                self._state, self._since = state, now
                self.seq += 1
                self._cond.notify_all()
        if changed:
            for fn in list(self.listeners):
                try:
                    fn(prev, state, now)
                except Exception:  # noqa: BLE001 - a listener never breaks the poll
                    pass
        return state

    def wait_change(self, seq, timeout):
        """Block until `self.seq` differs from `seq` or `timeout` s pass; returns seq."""
        with self._cond:
            self._cond.wait_for(lambda: self.seq != seq, timeout)
            return self.seq

    # -- thread ----------------------------------------------------------------

    def _run(self, interval):
        while not self._stop.is_set():
            try:
                self.poll()
            except Exception:  # noqa: BLE001 - a bad poll never kills the watcher
                pass
            self._stop.wait(interval)

    def start(self, interval=POLL_S):
        if self.running():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(interval,),
                                        name="ew-gamewatch", daemon=True)
        self._thread.start()

    def stop(self, timeout=2.0):
        self._stop.set()
        t = self._thread
        if t is not None:
            t.join(timeout)

    def running(self):
        return self._thread is not None and self._thread.is_alive()

    # -- reads -----------------------------------------------------------------

    def view(self):
        """GET /api/game body. `log_file` is a file name, never a path."""
        with self._lock:
            state = self._state or ("not_running" if self.configured else "unconfigured")
            return {"state": state,
                    "since": _iso(self._since) if self._since is not None else None,
                    "log_file": self._log_file,
                    "last_event": dict(self._last_event) if self._last_event else None,
                    "screenshots": [dict(s) for s in self._shots],
                    "configured": self.configured}

    def source(self):
        """`/api/state` sources.game: {updated, status: <state>}; never polls."""
        with self._lock:
            state = self._state or ("not_running" if self.configured else "unconfigured")
            return {"updated": _iso(self._updated) if self._updated is not None else None,
                    "status": state}
