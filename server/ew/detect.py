"""Zero-config path auto-detect (plan 065): BDO Documents + Steam install dir.

Read-only and pure behind injectable fs / env / registry:
- documents dir: the Documents known folder (`Personal` under User Shell
  Folders, which follows OneDrive redirection; then Shell Folders; then
  %USERPROFILE%\\Documents) joined with `Black Desert`. Valid when it holds a
  `ScreenShot` folder or a `GameOption.txt` (existence only, never opened).
- install dir: Steam's own `libraryfolders.vdf` -> each library's
  `steamapps/common/<installdir>`; the app 582660 manifest (Steam's file)
  confirms it and names the folder.
Nothing under the BDO client is ever opened. No absolute path literal: every
root comes from the registry or the environment. Explicit config always wins
(`resolve`); `Detector` re-detects once an hour while gamewatch is unconfigured.
"""

import os
import re
import threading
import time

APP_ID = "582660"
GAME_FOLDER = "Black Desert Online"
DOCS_FOLDER = "Black Desert"
RETRY_S = 3600
KEYS = ("install_dir", "documents_dir")

HKCU, HKLM = "HKCU", "HKLM"
USER_SHELL_FOLDERS = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
SHELL_FOLDERS = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"
STEAM_KEY = r"Software\Valve\Steam"
STEAM_KEYS = ((HKCU, STEAM_KEY, "SteamPath"),
              (HKLM, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
              (HKLM, r"SOFTWARE\Valve\Steam", "InstallPath"))
MAX_CONFIG = 1 << 20  # bytes; a Steam config bigger than this is not parsed

_ENV_RE = re.compile(r"%([^%]+)%")
_TOKEN_RE = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])')


class RealFS:
    def is_dir(self, p):
        return os.path.isdir(p)

    def is_file(self, p):
        return os.path.isfile(p)

    def read_text(self, p):
        with open(p, "rb") as f:
            return f.read(MAX_CONFIG).decode("utf-8", errors="replace")


def win_registry(hive, key, name):
    """One string value from the Windows registry, or None (non-Windows too)."""
    try:
        import winreg
    except ImportError:
        return None
    root = winreg.HKEY_CURRENT_USER if hive == HKCU else winreg.HKEY_LOCAL_MACHINE
    try:
        with winreg.OpenKey(root, key) as k:
            v, _ = winreg.QueryValueEx(k, name)
    except OSError:
        return None
    return v if isinstance(v, str) else None


def _safe(fn, *args):
    try:
        return fn(*args)
    except Exception:  # noqa: BLE001 - a probe failure is "not found", never a crash
        return None


def _expand(s, env):
    return _ENV_RE.sub(lambda m: env.get(m.group(1), m.group(0)), s)


def _reg(registry, env, hive, key, name):
    v = _safe(registry, hive, key, name)
    if not isinstance(v, str) or not v.strip():
        return None
    v = _expand(v.strip(), env)
    return None if "%" in v else v


# ---- VDF (Valve KeyValues text) ----

def _parse_vdf(text):
    """Nested dict of a KeyValues text, or None when malformed."""
    tokens = [(m.group(1), m.group(2)) for m in _TOKEN_RE.finditer(text or "")]
    root, stack, key = {}, [], None
    cur = root
    for s, brace in tokens:
        if brace == "{":
            if key is None:
                return None
            child = {}
            cur[key] = child
            stack.append(cur)
            cur, key = child, None
        elif brace == "}":
            if key is not None or not stack:
                return None
            cur = stack.pop()
        else:
            s = s.replace("\\\\", "\\").replace('\\"', '"')
            if key is None:
                key = s
            else:
                cur[key], key = s, None
    return root if not stack and key is None else None


def _first(doc):
    return next(iter(doc.values()), None) if isinstance(doc, dict) else None


def library_paths(text):
    """libraryfolders.vdf text -> library root paths (new and old formats)."""
    body = _first(_parse_vdf(text))
    if not isinstance(body, dict):
        return []
    out = []
    for k, v in body.items():
        if not k.isdigit():
            continue
        p = v.get("path") if isinstance(v, dict) else v
        if isinstance(p, str) and p.strip():
            out.append(p)
    return out


def _installdir(fs, manifest):
    body = _first(_parse_vdf(_safe(fs.read_text, manifest)))
    d = body.get("installdir") if isinstance(body, dict) else None
    ok = isinstance(d, str) and d.strip() and not re.search(r"[\\/:]|^\.\.?$", d)
    return d if ok else None


# ---- probes ----

def documents_dir(fs, env, registry):
    bases = [_reg(registry, env, HKCU, USER_SHELL_FOLDERS, "Personal"),
             _reg(registry, env, HKCU, SHELL_FOLDERS, "Personal")]
    home = env.get("USERPROFILE")
    if isinstance(home, str) and home.strip():
        bases.append(os.path.join(home, "Documents"))
    for base in bases:
        if not base:
            continue
        d = os.path.join(base, DOCS_FOLDER)
        if _safe(fs.is_dir, os.path.join(d, "ScreenShot")) or \
                _safe(fs.is_file, os.path.join(d, "GameOption.txt")):
            return d
    return None


def _steam_roots(env, registry):
    roots = [_reg(registry, env, *k) for k in STEAM_KEYS]
    for var in ("ProgramFiles(x86)", "ProgramFiles"):
        v = env.get(var)
        if isinstance(v, str) and v.strip():
            roots.append(os.path.join(v, "Steam"))
    return [r for r in roots if r]


def _key(p):
    return os.path.normcase(os.path.normpath(p))


def install_dir(fs, env, registry):
    libs, seen = [], set()
    for root in _steam_roots(env, registry):
        if not _safe(fs.is_dir, root):
            continue
        found = [root]
        for rel in (("steamapps", "libraryfolders.vdf"), ("config", "libraryfolders.vdf")):
            path = os.path.join(root, *rel)
            if _safe(fs.is_file, path):
                found += library_paths(_safe(fs.read_text, path))
                break
        for lib in found:
            if _key(lib) not in seen:
                seen.add(_key(lib))
                libs.append(lib)
    fallback = None
    for lib in libs:
        manifest = os.path.join(lib, "steamapps", f"appmanifest_{APP_ID}.acf")
        has = bool(_safe(fs.is_file, manifest))
        name = (_installdir(fs, manifest) if has else None) or GAME_FOLDER
        game = os.path.join(lib, "steamapps", "common", name)
        if not _safe(fs.is_dir, game):
            continue
        if has:
            return game
        fallback = fallback or game
    return fallback


def detect(fs=None, env=None, registry=None):
    """{"install_dir": str|None, "documents_dir": str|None}; never raises."""
    fs = fs or RealFS()
    env = dict(os.environ) if env is None else env
    registry = registry or win_registry
    return {"install_dir": _safe(install_dir, fs, env, registry),
            "documents_dir": _safe(documents_dir, fs, env, registry)}


def _dir(v):
    return v if isinstance(v, str) and v.strip() else None


def resolve(cfg, found):
    """Explicit config > detected, per key; `source` says which ("config" |
    "detected" | None)."""
    cfg = cfg if isinstance(cfg, dict) else {}
    found = found if isinstance(found, dict) else {}
    out, src = {}, {}
    for k in KEYS:
        c, d = _dir(cfg.get(k)), _dir(found.get(k))
        out[k] = c or d
        src[k] = "config" if c else ("detected" if d else None)
    out["source"] = src
    return out


class Detector:
    """Holds the last detection; `attach(game)` configures a GameWatch and
    re-detects (as a gamewatch poller) at most once per `interval` while it
    stays unconfigured."""

    def __init__(self, probe=detect, config=dict, clock=time.time, interval=RETRY_S):
        self.probe = probe
        self.config = config
        self.clock = clock
        self.interval = interval
        self._lock = threading.Lock()
        self._found = {k: None for k in KEYS}
        self._at = None
        self.game = None

    def refresh(self, now=None):
        now = self.clock() if now is None else now
        got = _safe(self.probe)
        got = got if isinstance(got, dict) else {}
        with self._lock:
            self._found = {k: _dir(got.get(k)) for k in KEYS}
            self._at = now
            return dict(self._found)

    def paths(self):
        with self._lock:
            return dict(self._found)

    def effective(self):
        return resolve(_safe(self.config) or {}, self.paths())

    def apply(self):
        """Push the effective paths into the attached GameWatch."""
        if self.game is not None:
            eff = self.effective()
            self.game.configure(eff["install_dir"], eff["documents_dir"])

    def attach(self, game):
        self.game = game
        self.refresh()
        self.apply()
        game.pollers.append(self.on_poll)

    def on_poll(self, state, at):
        if state != "unconfigured":
            return
        with self._lock:
            due = self._at is None or at - self._at >= self.interval
        if due:
            self.refresh(at)
            self.apply()
