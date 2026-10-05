#!/usr/bin/env python3
"""ETA timing log (operator standing order 4).

    eta.py record <kind> <seconds> [--ok|--failed]   append one finished run
    eta.py estimate <kind> [--default N]             print "[~Ns]" from history
    eta.py run <kind> -- <cmd...>                    time a command, record it

Log: ops/loop/control/timings.jsonl, one JSON object per line
{"kind", "seconds", "ok", "ts"}. Estimate = median of the last 5 OK runs of that
kind; with none, the default (60 s). Format follows FLEET item 3: s under 120 s,
m under 120 m, h beyond.
"""

import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "ops" / "loop" / "control" / "timings.jsonl"
WINDOW = 5
DEFAULT_S = 60.0


def fmt(seconds):
    s = max(0, round(float(seconds)))
    if s < 120:
        return f"[~{s}s]"
    if s < 120 * 60:
        return f"[~{round(s / 60)}m]"
    return f"[~{round(s / 3600)}h]"


def record(kind, seconds, ok=True, log=LOG, clock=time.time):
    if not kind or any(c.isspace() for c in kind):
        raise ValueError("kind must be a non-empty token")
    log = Path(log)
    log.parent.mkdir(parents=True, exist_ok=True)
    row = {"kind": kind, "seconds": round(float(seconds), 3), "ok": bool(ok),
           "ts": round(clock(), 3)}
    with log.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
    return row


def history(kind, log=LOG):
    rows = []
    try:
        lines = Path(log).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return rows
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue  # a torn line is skipped, never fatal
        if row.get("kind") == kind and row.get("ok") is True:
            rows.append(float(row["seconds"]))
    return rows


def estimate(kind, default=DEFAULT_S, log=LOG):
    rows = history(kind, log)[-WINDOW:]
    return statistics.median(rows) if rows else float(default)


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if len(a) >= 3 and a[0] == "record":
        ok = "--failed" not in a
        record(a[1], float(a[2]), ok=ok)
        return 0
    if len(a) >= 2 and a[0] == "estimate":
        default = float(a[a.index("--default") + 1]) if "--default" in a else DEFAULT_S
        print(fmt(estimate(a[1], default)))
        return 0
    if len(a) >= 4 and a[0] == "run" and a[2] == "--":
        print(f"{a[1]} ETA {fmt(estimate(a[1]))}", file=sys.stderr)
        t0 = time.time()
        rc = subprocess.run(a[3:]).returncode
        record(a[1], time.time() - t0, ok=(rc == 0))
        return rc
    print(__doc__, file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main())
