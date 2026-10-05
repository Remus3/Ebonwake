import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import eta  # noqa: E402


def test_fmt_units():
    assert eta.fmt(42) == "[~42s]"
    assert eta.fmt(600) == "[~10m]"
    assert eta.fmt(3 * 3600) == "[~3h]"


def test_estimate_median_of_last_five_ok(tmp_path):
    log = tmp_path / "t.jsonl"
    assert eta.estimate("pytest", default=33, log=log) == 33
    for s in (100, 1, 2, 3, 4, 5):
        eta.record("pytest", s, log=log)
    eta.record("pytest", 999, ok=False, log=log)
    eta.record("other", 7, log=log)
    assert eta.estimate("pytest", log=log) == 3


def test_torn_line_is_skipped(tmp_path):
    log = tmp_path / "t.jsonl"
    eta.record("x", 10, log=log)
    with log.open("a") as fh:
        fh.write('{"kind": "x", "sec')
    assert eta.estimate("x", log=log) == 10
