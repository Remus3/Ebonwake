"""Game watch (plan 008 slice A): session-log tail + ScreenShot folder watcher.

Read-only, nothing is ever sent to the game. Inputs (research 0001 section 4):
- the newest `<install_dir>/Log/Client_YYYY-MM-DD_HHMMSS.json` (UTF-16LE, one
  JSON object per line), opened read-only with the default share mode so the
  game is never blocked;
- a process LIST from `tasklist /FI` (never a handle to the game process, never
  its memory);
- a directory listing of `<documents_dir>/ScreenShot` (stat only, files never
  opened; OCR is plan 009).
Paths come from gitignored config/local.json `bdo`, else from plan 065's
read-only auto-detect (server/ew/detect.py, explicit config wins); without an
install dir the watcher reports `unconfigured`. One
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

from . import today as _today

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
# Plan 082: a character load names its per-character UI cache path in the log
# (research 0011 F1). A side-channel, never a state: only the log STRING is
# parsed; nothing under that folder is ever opened.
CHAR_LOAD_RE = re.compile(r"UserCache[/\\](\d+)[/\\]\d+[/\\](\d{6,20})[/\\]gameVariable\.xml")
MAX_CHAR_LOADS = 20


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


def char_load(text):
    """Plan 082: `Log` text -> the loaded characterNo (string), or None."""
    m = CHAR_LOAD_RE.search(text) if isinstance(text, str) else None
    return m.group(2) if m else None


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
        self._line_at = None  # plan 073: clock time the tail last read a complete line
        self._shots = []
        self._char_loads = []  # plan 082: last MAX_CHAR_LOADS {char_no, at}, any session
        self._char_no = None  # plan 082: newest load of the current log session
        self._stop = threading.Event()
        self._thread = None
        # Plan 046: `fn(prev, new, at)` called after each state change, outside
        # the lock; a failing listener never breaks the poll.
        self.listeners = []
        # Plan 062: `fn(state, at)` called after EVERY poll (changed or not), after
        # the listeners and outside the lock; a failing poller never breaks the poll.
        self.pollers = []

    @classmethod
    def from_config(cls, cfg, clock=time.time, tasklist=None):
        cfg = cfg if isinstance(cfg, dict) else {}
        return cls(cfg.get("install_dir"), cfg.get("documents_dir"), clock=clock,
                   tasklist=tasklist)

    def configure(self, install_dir=None, documents_dir=None):
        """Plan 065: (re)point the watcher at new folders (detected or set in
        Settings); the log tail starts over when the install dir changes."""
        install_dir, documents_dir = _dir(install_dir), _dir(documents_dir)
        log_dir = Path(install_dir) / "Log" if install_dir else None
        with self._lock:
            if log_dir != self.log_dir:
                self._log_file, self._offset = None, 0
                self._log_state, self._last_event = None, None
                self._line_at, self._char_no = None, None
            self.log_dir = log_dir
            self.shot_dir = Path(documents_dir) / "ScreenShot" if documents_dir else None

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
            self._line_at = None  # plan 073: a new session's silence counts from its state
            self._char_no = None
        path = self.log_dir / name
        try:
            with open(path, "rb") as f:  # read-only; default share mode never blocks the game
                st = os.fstat(f.fileno())
                if st.st_size < self._offset:
                    self._offset, self._log_state, self._char_no = 0, None, None
                f.seek(self._offset)
                raw = f.read(MAX_READ)
        except OSError:
            return None
        skip = 2 if self._offset == 0 and raw.startswith(_BOM) else 0
        lines, used = split_lines(raw[skip:])
        self._offset += skip + used
        if lines:  # plan 073: any complete line (classified or not) is a sign of life
            self._line_at = self.clock()
        for line in lines:
            doc = parse_line(line)
            char_no = char_load(doc["Log"]) if doc else None
            if char_no is not None:  # plan 082 side-channel; never a state
                self._char_no = char_no
                self._char_loads.append({"char_no": char_no,
                                         "at": _clean(doc.get("Date")) or _iso(self.clock())})
                del self._char_loads[:-MAX_CHAR_LOADS]
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
                    state, self._log_state, self._char_no = "not_running", None, None
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
        for fn in list(self.pollers):
            try:
                fn(state, now)
            except Exception:  # noqa: BLE001 - a poller never breaks the poll
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
                    "configured": self.configured,
                    "char_loads": [dict(c) for c in self._char_loads],
                    "char_no": self._char_no if state not in ("not_running", "unconfigured")
                    else None}

    @property
    def documents_dir(self):
        """Plan 082: the Documents folder (parent of ScreenShot), or None."""
        with self._lock:
            return self.shot_dir.parent if self.shot_dir is not None else None

    def health(self):
        """Plan 073 signal inputs; never polls. Folder checks are stat-only."""
        with self._lock:
            state = self._state or ("not_running" if self.configured else "unconfigured")
            log_dir, shot_dir = self.log_dir, self.shot_dir
            out = {"state": state, "configured": self.configured, "line_at": self._line_at,
                   "since": self._since, "polled_at": self._updated,
                   "shots_configured": shot_dir is not None,
                   "last_shot_at": max((t for t in (_parse(s["mtime"]) for s in self._shots)
                                        if t is not None), default=None)}
        out["log_dir_ok"] = log_dir is not None and log_dir.is_dir()
        out["shots_dir_ok"] = shot_dir is not None and shot_dir.is_dir()
        # Plan 077: the parent folders too, so "set but gone" reads as not set.
        out["install_ok"] = log_dir is not None and log_dir.parent.is_dir()
        out["documents_ok"] = shot_dir is not None and shot_dir.parent.is_dir()
        return out

    def source(self):
        """`/api/state` sources.game: {updated, status: <state>}; never polls."""
        with self._lock:
            state = self._state or ("not_running" if self.configured else "unconfigured")
            return {"updated": _iso(self._updated) if self._updated is not None else None,
                    "status": state}


# -- plan 056: Black Spirit's Adventure dice from logged-in minutes -------------

DICE_SEEN_S = 60  # status() refreshes `seen` at most once a minute while logged in


def _parse(s):
    if not isinstance(s, str):
        return None
    try:
        when = _dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    return when.timestamp() if when.tzinfo is not None else None


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 else 0.0


class DiceClock:
    """Store domain `dice`: {"reset": <iso>, "acc_s": <s>, "login_at": <iso>|null,
    "seen": <iso>|null, "active": bool}. A GameWatch listener: minutes are
    wall-clock while the plan 008 state is `logged_in`, summed since the dice
    reset (`rule`, plan 021 row). An open session survives an EW restart: a
    first poll that still reads logged_in resumes it; any other first state
    closes it at `seen` (the last moment EW saw it logged in), so EW downtime
    with the game closed is never counted. Read-only on the game."""

    def __init__(self, store, rule, grants, verified=False, clock=time.time):
        self.store = store
        self.rule = rule
        self.grants = list(grants)
        self.verified = bool(verified)
        self.clock = clock
        self._lock = threading.Lock()

    def _dt(self, ts):
        return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc)

    def _load(self, now):
        """Stored doc rolled to the reset in force at `now` (epoch s)."""
        doc = self.store.get("dice")
        reset = _today.last_reset(self.rule, self._dt(now)).timestamp()
        login = _parse(doc.get("login_at"))
        out = {"reset": reset, "acc_s": _num(doc.get("acc_s")), "login": login,
               "seen": _parse(doc.get("seen")), "active": doc.get("active") is True}
        if _parse(doc.get("reset")) != reset:  # a reset passed: start over
            out.update(acc_s=0.0, active=login is not None)
        return out

    def _save(self, d):
        self.store.put("dice", {
            "reset": _iso(d["reset"]), "acc_s": round(d["acc_s"], 3),
            "login_at": _iso(d["login"]) if d["login"] is not None else None,
            "seen": _iso(d["seen"]) if d["seen"] is not None else None,
            "active": d["active"]})

    @staticmethod
    def _close(d, at):
        d["acc_s"] += max(0.0, at - max(d["login"], d["reset"]))
        d["login"] = None

    def on_game(self, prev, new, at):
        """Plan 008 change hook (GameWatch.listeners)."""
        with self._lock:
            d = self._load(at)
            if d["login"] is not None and prev != "logged_in" and new != "logged_in":
                # Orphan from a previous EW run: close where EW last saw it.
                seen = d["seen"] if d["seen"] is not None else d["login"]
                self._close(d, min(seen, at))
            elif d["login"] is not None and new != "logged_in":
                self._close(d, at)
            elif d["login"] is None and new == "logged_in":
                d["login"], d["active"] = at, True
            d["seen"] = at
            self._save(d)

    def status(self, now=None):
        """GET /api/today `dice`: {earned, max, next_at_min, eta_utc, played_min,
        logged_in, next_reset, verified}."""
        now = self.clock() if now is None else now
        with self._lock:
            d = self._load(now)
            if d["login"] is not None and (d["seen"] is None or now - d["seen"] >= DICE_SEEN_S):
                d["seen"] = now
                self._save(d)
        live = d["login"] is not None
        played = d["acc_s"] + (max(0.0, now - max(d["login"], d["reset"])) if live else 0.0)
        earned = sum(1 for g in self.grants if g * 60 <= played) if d["active"] else 0
        nxt = next((g for g in self.grants[earned:]), None)
        eta = None
        if nxt is not None and live:
            eta = _iso(now + max(0.0, nxt * 60 - played))
        return {"earned": earned, "max": len(self.grants), "next_at_min": nxt,
                "eta_utc": eta, "played_min": int(played // 60), "logged_in": live,
                "next_reset": _iso(_today.next_reset(self.rule, self._dt(now)).timestamp()),
                "verified": self.verified}
