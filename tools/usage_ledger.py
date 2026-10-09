#!/usr/bin/env python3
"""Plan 100 (perf 2.8): the loop governor counts runs from the kit usage log.

    usage_ledger.py count [--root R]      runs in the rolling window, both ledgers
    usage_ledger.py backfill [--root R]   label kind-less usage rows kind build

The kit's headless_budget.json and headless_usage.jsonl drift (a run started
outside the loop, a budget file removed after a fail-closed refusal), so the
governor takes the larger of the two counts (UsageBudget). Pre-kit-v8 usage
rows carry no "kind"; backfill_kind labels them "build" with
"kind_src": "backfill". Both ledgers are kit-owned runtime state: this module
never writes the budget file, and rewrites the usage log only atomically and
only when no append landed meanwhile. Stdlib only.
"""

import argparse
import datetime as _dt
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
USAGE_REL = Path("ops/loop/control/headless_usage.jsonl")  # kit USAGE_REL
BUDGET_REL = Path("ops/loop/control/headless_budget.json")  # kit BUDGET_REL
WINDOW_S = 86400  # kit WINDOW_S
BACKFILL_KIND = "build"


def _epoch(ts):
    """Epoch seconds of an ISO timestamp, or None. A naive one is local time."""
    if not isinstance(ts, str):
        return None
    try:
        dt = _dt.datetime.fromisoformat(ts)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.timestamp()


def _rows(path):
    """Parsed dict rows of a JSONL file; torn or non-object lines are skipped."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return []
    out = []
    for line in text.splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict):
            out.append(r)
    return out


def run_starts(path, now, window=WINDOW_S):
    """Sorted epoch starts (ts - duration_s) of the usage rows in the window."""
    floor, starts = now - window, []
    for r in _rows(path):
        end = _epoch(r.get("ts"))
        if end is None:
            continue
        dur = r.get("duration_s")
        if isinstance(dur, bool) or not isinstance(dur, (int, float)) or dur < 0:
            dur = 0
        start = end - dur
        if floor < start <= now:
            starts.append(start)
    return sorted(starts)


class UsageBudget:
    """The kit RunBudget, counting max(budget file, usage log) in the window.
    Readability (fail closed) stays the budget file's; the kit's own hard cap
    inside spawn is untouched."""

    def __init__(self, inner, usage_path):
        self.inner, self.usage_path = inner, Path(usage_path)

    @property
    def cap(self):
        return self.inner.cap

    @property
    def window(self):
        return self.inner.window

    @property
    def clock(self):
        return self.inner.clock

    @clock.setter
    def clock(self, value):
        self.inner.clock = value

    def _usage(self):
        return run_starts(self.usage_path, self.clock(), self.window)

    def readable(self):
        return self.inner.readable()

    def used(self):
        """An unreadable budget file already reads as cap (kit RunBudget)."""
        return max(self.inner.used(), len(self._usage()))

    def can_start(self):
        return self.inner.readable() and self.used() < self.cap

    def frees_at(self):
        starts = self._usage()
        if len(starts) > self.inner.used():
            return starts[0] + self.window
        return self.inner.frees_at()


def backfill_kind(path, kind=BACKFILL_KIND, _before_replace=None):
    """Label every usage row lacking "kind" with kind + "kind_src": "backfill".
    Every other line keeps its bytes. Idempotent (nothing to label = no write).
    Atomic tmp-then-replace, skipped (0) when the file changed size since it
    was read, so a kit append is never lost; the next call retries. Returns
    the number of rows labelled."""
    path = Path(path)
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return 0
    lines, n = data.split(b"\n"), 0
    for i, raw in enumerate(lines):
        try:
            r = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        if not isinstance(r, dict) or "kind" in r:
            continue
        r["kind"], r["kind_src"] = kind, "backfill"
        lines[i] = json.dumps(r).encode("ascii")
        n += 1
    if not n:
        return 0
    tmp = path.with_name(path.name + ".p100.tmp")
    tmp.write_bytes(b"\n".join(lines))
    if _before_replace is not None:
        _before_replace()
    try:
        if path.stat().st_size != len(data):
            tmp.unlink()
            return 0
        os.replace(tmp, path)
    except OSError:  # a writer holds it open (Windows): retry next tick
        try:
            tmp.unlink()
        except OSError:
            pass
        return 0
    return n


def main(argv=None):
    ap = argparse.ArgumentParser(description="EW usage ledger (plan 100)")
    ap.add_argument("cmd", choices=("count", "backfill"))
    ap.add_argument("--root", default=str(ROOT))
    a = ap.parse_args(argv)
    root = Path(a.root)
    if a.cmd == "backfill":
        print(f"backfilled {backfill_kind(root / USAGE_REL)}")
        return 0
    print(f"usage runs in window: {len(run_starts(root / USAGE_REL, time.time()))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
