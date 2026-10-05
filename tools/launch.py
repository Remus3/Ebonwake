"""Start the EW server (if not already healthy) and the Electron app.

Used by the "Ebonwake" Desktop shortcut (pythonw tools/launch.py). Idempotent:
a healthy server is reused; Electron's single-instance lock focuses an already
running dashboard. No console windows (CREATE_NO_WINDOW / DETACHED_PROCESS).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.ew import ports  # noqa: E402

HEALTH = f"http://127.0.0.1:{ports.SERVER}/api/health"
FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)


def healthy(timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(HEALTH, timeout=timeout) as r:
            return bool(json.loads(r.read().decode("utf-8")).get("ok"))
    except Exception:
        return False


def pythonw() -> str:
    exe = Path(sys.executable)
    alt = exe.with_name("pythonw.exe")
    return str(alt if alt.exists() else exe)


def electron_exe() -> Path:
    mod = ROOT / "app" / "node_modules" / "electron"
    rel = (mod / "path.txt").read_text(encoding="utf-8").strip()
    return mod / "dist" / rel


def main(argv: list[str]) -> int:
    if not healthy():
        subprocess.Popen([pythonw(), "-m", "server.ew"], cwd=str(ROOT), creationflags=FLAGS,
                         close_fds=True)
        for _ in range(40):
            if healthy():
                break
            time.sleep(0.25)
    if "--server-only" in argv:
        return 0 if healthy() else 1
    # The tray's "Restart server" re-runs this file with the same pythonw.
    env = dict(os.environ, EW_PYTHONW=pythonw())
    subprocess.Popen([str(electron_exe()), "."], cwd=str(ROOT / "app"), creationflags=FLAGS,
                     close_fds=True, env=env)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
