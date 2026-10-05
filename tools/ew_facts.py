#!/usr/bin/env python3
"""Claude hooks for EW (defense in depth; git hooks are the authoritative gate).

    ew_facts.py               SessionStart: one-screen facts (hand-off next action,
                              server health, lane ETAs). Never fails the session.
    ew_facts.py --edit-guard  PostToolUse Edit|Write: py_compile the edited .py and
                              flag non-ASCII / CRLF in the edited file.
"""

import json
import py_compile
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))


def facts():
    out = ["EW facts"]
    hand = ROOT / "EW-NEXT-SESSION.txt"
    if hand.is_file():
        for line in hand.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("Next action:"):
                out.append(line[:200])
                break
    try:
        from server.ew import ports  # noqa: PLC0415
        with urllib.request.urlopen(f"http://127.0.0.1:{ports.SERVER}/api/health",
                                    timeout=1) as r:
            out.append("server: " + ("ok" if json.load(r).get("ok") else "bad"))
    except Exception:  # noqa: BLE001 - facts never fail a session
        out.append("server: offline")
    try:
        import ew_lane  # noqa: PLC0415
        out += ew_lane.status(ROOT)
    except Exception:  # noqa: BLE001
        pass
    print("\n".join(out))
    return 0


def edit_guard():
    try:
        payload = json.load(sys.stdin)
        path = Path(payload.get("tool_input", {}).get("file_path", ""))
    except (ValueError, AttributeError):
        return 0
    if not path.is_file():
        return 0
    problems = []
    if path.suffix == ".py":
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            problems.append(f"py_compile: {exc.msg.splitlines()[-1][:200]}")
    data = path.read_bytes()
    if b"\r\n" in data:
        problems.append("CRLF line endings (fleet rule: LF)")
    try:
        data.decode("ascii")
    except UnicodeDecodeError:
        if "fleet_kit" not in path.parts:
            problems.append("non-ASCII bytes (fleet rule: ASCII only)")
    if problems:
        print(f"{path.name}: " + "; ".join(problems), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(edit_guard() if "--edit-guard" in sys.argv else facts())
