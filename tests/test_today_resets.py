"""Plan 021: per-item reset rules (Sunday weeklies, non-midnight dailies).

Legacy items (no `reset`) keep plan 003 behaviour; tests/test_today.py covers
those and is untouched.
"""

import datetime as dt
import http.client
import json
import re
import threading

import pytest

from server.ew import app as ewapp
from server.ew import today
from server.ew.store import Store

UTC = dt.timezone.utc
SUNDAY = {"every": "week", "weekday": 6, "at": "00:00"}
FIVE_AM = {"every": "day", "at": "05:00"}


def T(*a):
    return dt.datetime(*a, tzinfo=UTC)


class Clock:
    def __init__(self, when):
        self.t = when.timestamp()

    def __call__(self):
        return self.t

    def set(self, when):
        self.t = when.timestamp()


def _svc(tmp_path, when=T(2026, 10, 4, 12)):
    clk = Clock(when)
    return today.TodayService(Store(tmp_path / "store"), clock=clk), clk


def _item(view, iid):
    return next(i for i in view["items"] if i["id"] == iid)


# --- last_reset / next_reset ---------------------------------------------------

@pytest.mark.parametrize("now, want", [
    (T(2026, 10, 3, 23, 59, 59), T(2026, 9, 27)),   # Saturday 23:59:59 -> previous Sunday
    (T(2026, 10, 4, 0, 0, 0), T(2026, 10, 4)),       # Sunday 00:00 exactly
    (T(2026, 10, 4, 0, 0, 1), T(2026, 10, 4)),
    (T(2026, 10, 8, 12), T(2026, 10, 4)),            # Thursday: not a Sunday item reset
    (T(2027, 1, 2, 10), T(2026, 12, 27)),            # year rollover
])
def test_sunday_rule(now, want):
    assert today.last_reset(SUNDAY, now) == want
    assert today.next_reset(SUNDAY, now) == want + dt.timedelta(days=7)
    assert want.weekday() == 6


@pytest.mark.parametrize("now, want", [
    (T(2026, 10, 4, 4, 59, 59), T(2026, 10, 3, 5)),  # 04:59:59 -> yesterday 05:00
    (T(2026, 10, 4, 5, 0, 0), T(2026, 10, 4, 5)),    # 05:00 exactly
    (T(2026, 10, 4, 23, 0), T(2026, 10, 4, 5)),
    (T(2026, 11, 1, 0, 30), T(2026, 10, 31, 5)),     # month rollover
    (T(2027, 1, 1, 4, 0), T(2026, 12, 31, 5)),       # year rollover
])
def test_five_am_daily_rule(now, want):
    assert today.last_reset(FIVE_AM, now) == want
    assert today.next_reset(FIVE_AM, now) == want + dt.timedelta(days=1)


def test_week_rule_with_time():
    rule = {"every": "week", "weekday": 2, "at": "10:30"}  # Wednesday 10:30
    assert today.last_reset(rule, T(2026, 10, 7, 10, 29)) == T(2026, 9, 30, 10, 30)
    assert today.last_reset(rule, T(2026, 10, 7, 10, 30)) == T(2026, 10, 7, 10, 30)
    assert today.next_reset(rule, T(2026, 10, 7, 10, 30)) == T(2026, 10, 14, 10, 30)


def test_legacy_wrappers_match_default_rules():
    for now in (T(2026, 10, 1), T(2026, 9, 30, 23, 59, 59), T(2027, 1, 2, 10)):
        assert today.last_daily_reset(now) == today.last_reset(today.DEFAULT_RULES["daily"], now)
        assert today.last_weekly_reset(now) == today.last_reset(today.DEFAULT_RULES["weekly"], now)
    with pytest.raises(ValueError):
        today.last_reset(SUNDAY, dt.datetime(2026, 10, 4))


# --- validation ----------------------------------------------------------------

def test_validate_rule_normalises():
    assert today.validate_rule({"every": "week", "weekday": 6, "at": "00:00"}) == SUNDAY
    assert today.validate_rule({"every": "day", "at": "05:00"}) == FIVE_AM
    assert today.validate_rule({"every": "day"}) == {"every": "day", "at": "00:00"}


@pytest.mark.parametrize("rule, msg", [
    ("daily", "reset must be an object"),
    ({"every": "month", "at": "00:00"}, "every must be one of day, week"),
    ({"every": "week", "at": "00:00"}, "weekday must be an int 0-6"),
    ({"every": "week", "weekday": 7, "at": "00:00"}, "weekday must be an int 0-6"),
    ({"every": "week", "weekday": -1, "at": "00:00"}, "weekday must be an int 0-6"),
    ({"every": "week", "weekday": True, "at": "00:00"}, "weekday must be an int 0-6"),
    ({"every": "day", "weekday": 1, "at": "00:00"}, "weekday only with every week"),
    ({"every": "day", "at": "24:00"}, "at must be HH:MM 00:00-23:59"),
    ({"every": "day", "at": "05:60"}, "at must be HH:MM 00:00-23:59"),
    ({"every": "day", "at": "5:00"}, "at must be HH:MM 00:00-23:59"),
    ({"every": "day", "at": 500}, "at must be HH:MM 00:00-23:59"),
    ({"every": "day", "at": "05:00", "tz": "x"}, "unknown reset field(s): tz"),
])
def test_validate_rule_rejects(rule, msg):
    with pytest.raises(ValueError, match=re.escape(msg)):
        today.validate_rule(rule)


def test_validate_new_rule_matches_kind():
    e = today.validate_new({"title": "Black Shrine", "kind": "weekly", "reset": SUNDAY})
    assert e["reset"] == SUNDAY
    with pytest.raises(ValueError, match="reset every must match kind"):
        today.validate_new({"title": "x", "kind": "daily", "reset": SUNDAY})
    with pytest.raises(ValueError, match="reset every must match kind"):
        today.validate_new({"title": "x", "kind": "weekly", "reset": FIVE_AM})
    with pytest.raises(ValueError, match="event items take no reset"):
        today.validate_new({"title": "x", "kind": "event", "until": "2026-12-01",
                            "reset": FIVE_AM})
    assert "reset" not in today.validate_new({"title": "x", "kind": "daily"})
    assert "reset" not in today.validate_new({"title": "x", "kind": "daily", "reset": None})


# --- service -------------------------------------------------------------------

def test_sunday_item_ticks_and_clears_on_sunday(tmp_path):
    svc, clk = _svc(tmp_path, T(2026, 10, 3, 12))  # Saturday
    v = svc.add({"title": "Black Shrine (5/week)", "kind": "weekly", "reset": SUNDAY})
    it = _item(v, "black-shrine-5-week")
    assert it["reset"] == SUNDAY
    assert it["next_reset"] == "2026-10-04T00:00:00+00:00"
    svc.tick("black-shrine-5-week")
    svc.tick("weekly-boss-rewards")
    clk.set(T(2026, 10, 3, 23, 59, 59))
    assert _item(svc.view(), "black-shrine-5-week")["done"] is True
    clk.set(T(2026, 10, 4, 0, 0, 0))  # Sunday: shrine clears, Thursday weekly does not
    v = svc.view()
    assert _item(v, "black-shrine-5-week")["done"] is False
    assert _item(v, "black-shrine-5-week")["next_reset"] == "2026-10-11T00:00:00+00:00"
    assert _item(v, "weekly-boss-rewards")["done"] is True
    clk.set(T(2026, 10, 8, 0, 0, 0))  # Thursday: legacy weekly clears
    assert _item(svc.view(), "weekly-boss-rewards")["done"] is False


def test_five_am_daily_item(tmp_path):
    svc, clk = _svc(tmp_path, T(2026, 10, 4, 1))
    svc.add({"title": "Dice 5am", "kind": "daily", "reset": FIVE_AM})
    svc.tick("dice-5am")
    svc.tick("barter-run")
    clk.set(T(2026, 10, 4, 4, 59, 59))
    v = svc.view()
    assert _item(v, "dice-5am")["done"] is True
    assert _item(v, "dice-5am")["next_reset"] == "2026-10-04T05:00:00+00:00"
    clk.set(T(2026, 10, 4, 5, 0, 0))
    v = svc.view()
    assert _item(v, "dice-5am")["done"] is False
    assert _item(v, "barter-run")["done"] is True  # midnight daily untouched


def test_legacy_items_unchanged_and_rule_survives_reload(tmp_path):
    svc, clk = _svc(tmp_path)
    svc.add({"title": "Altar", "kind": "weekly", "reset": SUNDAY})
    v = svc.view()
    assert set(_item(v, "barter-run")) == {"id", "title", "kind", "until", "done", "ticked_at"}
    svc2 = today.TodayService(Store(tmp_path / "store"), clock=clk)
    assert _item(svc2.view(), "altar")["reset"] == SUNDAY
    svc2.move({"id": "altar", "to": 0})  # rewrite keeps the rule
    assert _item(svc2.view(), "altar")["reset"] == SUNDAY


def test_corrupt_stored_rule_falls_back_to_kind_default(tmp_path):
    svc, _ = _svc(tmp_path)
    doc = svc.store.get("today")
    doc["items"].append({"id": "bad", "title": "Bad", "kind": "weekly", "until": None,
                         "order": 99, "reset": {"every": "week", "weekday": 9, "at": "00:00"}})
    doc["items"].append({"id": "mism", "title": "Mism", "kind": "daily", "until": None,
                         "order": 100, "reset": SUNDAY})
    svc.store.put("today", doc)
    v = svc.view()
    assert set(_item(v, "bad")) == {"id", "title", "kind", "until", "done", "ticked_at"}
    assert set(_item(v, "mism")) == {"id", "title", "kind", "until", "done", "ticked_at"}


# --- seed presets ------------------------------------------------------------------

def test_tracked_presets_file():
    rows = today.load_presets()
    names = [r["name"] for r in rows]
    assert names == ["Black Shrine (5/week)", "Altar of Blood weekly",
                     "Black Spirit's Adventure dice"]
    by = {r["name"]: r for r in rows}
    assert by["Black Shrine (5/week)"]["reset"] == SUNDAY
    assert by["Black Shrine (5/week)"]["kind"] == "weekly"
    assert by["Black Shrine (5/week)"]["verified"] is True
    assert by["Altar of Blood weekly"]["reset"] == SUNDAY
    assert by["Black Spirit's Adventure dice"]["reset"] == FIVE_AM
    assert by["Black Spirit's Adventure dice"]["kind"] == "daily"
    assert by["Black Spirit's Adventure dice"]["verified"] is False
    assert all(r["source"] for r in rows)
    today.PRESETS_FILE.read_bytes().decode("ascii")


@pytest.mark.parametrize("doc", [
    "not json",
    {"rows": []},
    [{"name": "x", "reset": {"every": "day", "at": "99:00"}, "source": "s", "verified": True}],
    [{"name": "x", "reset": FIVE_AM, "source": "s", "verified": "yes"}],
    [{"name": "", "reset": FIVE_AM, "source": "s", "verified": True}],
    [{"name": "x", "reset": FIVE_AM, "verified": True}],
])
def test_presets_file_rejected(tmp_path, doc):
    p = tmp_path / "r.json"
    p.write_text(doc if isinstance(doc, str) else json.dumps(doc), encoding="ascii")
    with pytest.raises(ValueError):
        today.load_presets(p)


def test_view_offers_presets_and_nothing_auto_added(tmp_path):
    svc, _ = _svc(tmp_path)
    v = svc.view()
    assert [p["name"] for p in v["reset_presets"]][0] == "Black Shrine (5/week)"
    assert not any(i["title"].startswith("Black Shrine") for i in v["items"])


# --- route ----------------------------------------------------------------------

@pytest.fixture()
def tsrv(tmp_path):
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          sse_interval=0.05, market_seed=[], profile_cfg={})
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield s
    s.shutdown()


def _post(srv, body):
    c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
    c.request("POST", "/api/today", body=json.dumps(body),
              headers={"Content-Type": "application/json"})
    r = c.getresponse()
    return r.status, json.loads(r.read())


def test_route_add_with_rule_and_400s(tsrv):
    st, body = _post(tsrv, {"add": {"title": "Altar of Blood weekly", "kind": "weekly",
                                    "reset": SUNDAY}})
    assert st == 200
    assert _item(body, "altar-of-blood-weekly")["reset"] == SUNDAY
    for bad, msg in [
        ({"every": "week", "weekday": 7, "at": "00:00"}, "weekday must be an int 0-6"),
        ({"every": "day", "at": "24:00"}, "at must be HH:MM"),
        ({"every": "year", "at": "00:00"}, "every must be one of"),
    ]:
        st, body = _post(tsrv, {"add": {"title": "x", "kind": "weekly", "reset": bad}})
        assert st == 400 and msg in body["error"]
