"""Plan 059 step 1: weekly maintenance slot -> UTC instants (pure, no clock)."""

import datetime as dt
import json

import pytest

from server.ew import maint

UTC = dt.timezone.utc
SLOT = {"weekday": "thu", "start_utc": "07:00", "duration_min": 180}


def test_tracked_slot_is_ascii_unverified_and_valid():
    raw = maint.DATA.read_bytes()
    assert raw.isascii() and b"\r" not in raw
    doc = json.loads(raw)
    assert doc["weekday"] in maint.WEEKDAYS and maint.valid_hhmm(doc["start_utc"])
    assert doc["verified"] is False and "verify" in doc["note"]
    assert maint.load_slot() == {k: doc[k] for k in maint.DEFAULT}


@pytest.mark.parametrize("date,edge,want", [
    ("2026-10-15", "before", dt.datetime(2026, 10, 15, 7, 0, tzinfo=UTC)),
    ("2026-10-01", "after", dt.datetime(2026, 10, 1, 10, 0, tzinfo=UTC)),
    # Either side of the US / EU DST changes: a UTC slot does not move.
    ("2026-03-05", "before", dt.datetime(2026, 3, 5, 7, 0, tzinfo=UTC)),
    ("2026-03-12", "before", dt.datetime(2026, 3, 12, 7, 0, tzinfo=UTC)),
    ("2026-11-05", "after", dt.datetime(2026, 11, 5, 10, 0, tzinfo=UTC)),
    # A non-Thursday date still resolves on that date (holiday maintenance).
    ("2026-12-23", "before", dt.datetime(2026, 12, 23, 7, 0, tzinfo=UTC)),
    (dt.date(2026, 10, 15), "after", dt.datetime(2026, 10, 15, 10, 0, tzinfo=UTC)),
])
def test_resolve_table(date, edge, want):
    assert maint.resolve(date, edge, SLOT) == want


def test_resolve_crosses_midnight():
    s = {"weekday": "thu", "start_utc": "23:00", "duration_min": 120}
    assert maint.resolve("2026-10-15", "after", s) == dt.datetime(2026, 10, 16, 1, 0, tzinfo=UTC)


def test_resolve_rejects_bad_edge_and_date():
    with pytest.raises(ValueError):
        maint.resolve("2026-10-15", "during", SLOT)
    with pytest.raises(ValueError):
        maint.resolve("2026-13-40", "before", SLOT)


def test_override_replaces_start_only(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps(dict(SLOT, source="x", verified=False)))
    s = maint.slot("09:30", path=p)
    assert s["start_utc"] == "09:30" and s["overridden"] is True and s["duration_min"] == 180
    assert maint.resolve("2026-10-15", "after", s) == dt.datetime(2026, 10, 15, 12, 30, tzinfo=UTC)
    assert maint.slot("", path=p)["overridden"] is False
    assert maint.slot("25:00", path=p)["start_utc"] == "07:00"


def test_corrupt_slot_file_falls_back(tmp_path):
    p = tmp_path / "m.json"
    p.write_text('{"weekday": "x", "start_utc": "7am", "duration_min": -5}')
    assert maint.load_slot(p) == maint.DEFAULT
    assert maint.load_slot(tmp_path / "missing.json") == maint.DEFAULT
