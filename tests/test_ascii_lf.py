"""FLEET item 8: every tracked text file is ASCII and LF."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".js", ".css", ".html", ".yml", ".yaml",
                 ".ini", ".toml", ".cfg", ""}


def _tracked():
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True)
    if out.returncode != 0:
        return []
    return [ROOT / f for f in out.stdout.decode().split("\0") if f]


def test_tracked_text_is_ascii_lf():
    files = [p for p in _tracked() if p.suffix in TEXT_SUFFIXES and p.is_file()
             and p.name not in {"LICENSE"} and "fleet_kit" not in p.parts]
    bad = []
    for p in files:
        data = p.read_bytes()
        if b"\0" in data:
            continue
        try:
            data.decode("ascii")
        except UnicodeDecodeError:
            bad.append(f"{p.relative_to(ROOT)}: non-ascii")
        if b"\r\n" in data:
            bad.append(f"{p.relative_to(ROOT)}: crlf")
    assert bad == []
