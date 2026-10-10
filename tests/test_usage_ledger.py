"""Plan 100: the governor counts runs from headless_usage.jsonl too, and
kind-less (pre-kit-v8) usage rows are backfilled kind build. tmp_path only."""

import datetime as _dt
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
import usage_ledger  # noqa: E402
import ew_loop  # noqa: E402
from test_ew_loop import deps, make_root  # noqa: E402

NOW = 1_790_000_000.0
USAGE = "ops/loop/control/headless_usage.jsonl"


def iso(epoch):
    return _dt.datetime.fromtimestamp(epoch).astimezone().isoformat(timespec="seconds")


def row(end, dur=60.0, kind="build", **extra):
    r = {"ts": iso(end), "kit": 13, "code": "EW", "note": "lane-build",
         "duration_s": dur, "rc": 0}
    if kind is not None:
        r["kind"] = kind
    r.update(extra)
    return r


def write_rows(path, rows, tail=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(r) + "\n" for r in rows) + tail
    path.write_bytes(text.encode("ascii"))


class FakeBudget:
    def __init__(self, used=0, readable=True, cap=120):
        self.n, self.ok, self.cap, self.window = used, readable, cap, 86400
        self.clock = lambda: NOW

    def used(self):
        return self.n

    def readable(self):
        return self.ok

    def frees_at(self):
        return None


def test_run_starts_window_torn_and_bad_rows(tmp_path):
    p = tmp_path / "u.jsonl"
    rows = [row(NOW - 100), row(NOW - 3600, dur=120),
            row(NOW - 90000),  # outside the 24 h window
            {"kit": 7, "note": "x"},  # no ts
            {"ts": "garbage", "duration_s": 1}]
    write_rows(p, rows, tail='{"ts": "torn')
    starts = usage_ledger.run_starts(p, NOW, 86400)
    assert starts == sorted([NOW - 160, NOW - 3720])
    assert usage_ledger.run_starts(tmp_path / "missing.jsonl", NOW, 86400) == []


def test_usage_budget_takes_the_larger_ledger(tmp_path):
    p = tmp_path / "u.jsonl"
    write_rows(p, [row(NOW - 10 * i) for i in range(1, 6)])
    inner = FakeBudget(used=2)
    b = usage_ledger.UsageBudget(inner, p)
    assert b.used() == 5 and b.cap == 120 and b.window == 86400 and b.readable()
    inner.n = 9
    assert b.used() == 9
    assert b.can_start()
    b.clock = lambda: NOW + 86400 * 2  # forwarded: every row ages out
    assert inner.clock() == NOW + 86400 * 2 and inner.n == 9 and b.used() == 9
    inner.n = 0
    assert b.used() == 0
    inner.ok = False
    assert not b.readable() and not b.can_start()


@pytest.mark.git  # real git world via test_ew_loop (plan 097 tier guard)
def test_spawn_block_counts_usage_rows_the_budget_missed(tmp_path):
    root = make_root(tmp_path)
    d = deps(root)
    assert isinstance(d.budget, usage_ledger.UsageBudget)
    d.budget.clock = d.clock
    now = d.clock()
    cap = d.budget.cap
    write_rows(root / USAGE, [row(now - 30 - i) for i in range(cap - ew_loop.HEADROOM)])
    assert not (root / "ops/loop/control/headless_budget.json").exists()
    assert ew_loop.spawn_block(root, d.budget, now) == "runs cap"
    doc = ew_loop.tick(deps=d, no_push=True)
    assert d.seen["launch"] == [] and doc["state"] == "limit"


def test_spawn_block_under_cap_with_usage_rows(tmp_path):
    root = make_root(tmp_path)
    d = deps(root)
    d.budget.clock = d.clock
    write_rows(root / USAGE, [row(d.clock() - 30)] * 5)
    assert ew_loop.spawn_block(root, d.budget, d.clock()) is None


def test_backfill_kind_labels_only_kindless_rows_and_is_idempotent(tmp_path):
    p = tmp_path / "u.jsonl"
    rows = [row(NOW - 5, kind=None, kit=7), row(NOW - 4, kind="inbox"),
            row(NOW - 3, kind=None, kit=7)]
    write_rows(p, rows, tail="not json\n")
    before = p.read_bytes().split(b"\n")
    assert usage_ledger.backfill_kind(p) == 2
    after = p.read_bytes().split(b"\n")
    got = [json.loads(x) for x in after[:3]]
    assert [g["kind"] for g in got] == ["build", "inbox", "build"]
    assert got[0]["kind_src"] == "backfill" and "kind_src" not in got[1]
    assert after[1] == before[1] and after[3] == b"not json"  # untouched bytes
    assert {k: v for k, v in got[0].items() if k not in ("kind", "kind_src")} == \
        {k: v for k, v in rows[0].items()}
    mtime = p.stat().st_mtime_ns
    assert usage_ledger.backfill_kind(p) == 0
    assert p.stat().st_mtime_ns == mtime
    assert usage_ledger.backfill_kind(tmp_path / "missing.jsonl") == 0
    assert not list(tmp_path.glob("*.tmp"))


def test_backfill_skips_when_the_file_grew_meanwhile(tmp_path):
    p = tmp_path / "u.jsonl"
    write_rows(p, [row(NOW - 5, kind=None)])

    def grow():
        with p.open("a", encoding="ascii", newline="\n") as fh:
            fh.write(json.dumps(row(NOW - 1)) + "\n")
    assert usage_ledger.backfill_kind(p, _before_replace=grow) == 0
    lines = p.read_text(encoding="ascii").splitlines()
    assert len(lines) == 2 and "kind" not in json.loads(lines[0])
    assert usage_ledger.backfill_kind(p) == 1


@pytest.mark.git  # real git world via test_ew_loop (plan 097 tier guard)
def test_tick_backfills_usage_log_but_dry_run_does_not(tmp_path):
    root = make_root(tmp_path)
    write_rows(root / USAGE, [row(NOW - 5, kind=None)])
    ew_loop.tick(deps=deps(root), dry_run=True, no_push=True)
    assert "kind" not in json.loads((root / USAGE).read_text().splitlines()[0])
    ew_loop.tick(deps=deps(root), no_push=True)
    assert json.loads((root / USAGE).read_text().splitlines()[0])["kind"] == "build"


def test_cli_count_and_backfill(tmp_path, capsys):
    root = tmp_path
    write_rows(root / USAGE, [row(NOW, kind=None)])
    assert usage_ledger.main(["backfill", "--root", str(root)]) == 0
    assert capsys.readouterr().out.strip() == "backfilled 1"
    assert usage_ledger.main(["count", "--root", str(root)]) == 0
    assert capsys.readouterr().out.strip().startswith("usage runs in window: ")
