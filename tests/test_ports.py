"""Ports come from server/ew/ports.py and stay inside EW's block."""

import re
from pathlib import Path

from server.ew import ports

ROOT = Path(__file__).resolve().parent.parent


def test_ports_inside_block():
    assert ports.BLOCK == range(8940, 8960)
    for name, port in ports.ASSIGNED.items():
        assert port in ports.BLOCK, name
    assert len(set(ports.ASSIGNED.values())) == len(ports.ASSIGNED)


def test_no_port_literal_at_python_bind_sites():
    lit = re.compile(r"\b89[45]\d\b")
    offenders = []
    for p in (ROOT / "server").rglob("*.py"):
        if p.name == "ports.py":
            continue
        if lit.search(p.read_text(encoding="utf-8")):
            offenders.append(str(p.relative_to(ROOT)))
    assert offenders == []


def test_js_server_url_matches_registry():
    src = (ROOT / "app" / "shared" / "ewcore.js").read_text(encoding="utf-8")
    assert f"http://127.0.0.1:{ports.SERVER}" in src
    for html in (ROOT / "app").rglob("*.html"):
        for m in re.findall(r"127\.0\.0\.1:(\d+)", html.read_text(encoding="utf-8")):
            assert int(m) == ports.SERVER, html.name
