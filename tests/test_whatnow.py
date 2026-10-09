"""Plan 069: one "What now" card - the next best action ranked by urgency.

Pure inference over views EW already serves plus the clock; nothing reads the game.
"""

import datetime as dt
import http.client
import json
import threading

import pytest

from server.ew import app as ewapp
from server.ew import market, whatnow

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 6, 18, 0, tzinfo=UTC)  # a Tuesday


def iso(when):
    return when.isoformat()


def later(**kw):
    return NOW + dt.timedelta(**kw)


# --- weights data ---------------------------------------------------------------

def test_weights_file_covers_every_source():
    w = whatnow.load_weights()
    assert set(w) == set(whatnow.SOURCES)
    for row in w.values():
        assert set(row) == {"weight", "horizon_s", "nominal_s"}
        assert row["weight"] > 0 and row["horizon_s"] > 0 and row["nominal_s"] > 0
    whatnow.WEIGHTS_FILE.read_bytes().decode("ascii")


def test_weights_bad_file_falls_back(tmp_path):
    p = tmp_path / "w.json"
    p.write_text('{"boss": {"weight": -1}, "reset": "x"}', encoding="ascii")
    w = whatnow.load_weights(p)
    assert w == whatnow.DEFAULT_WEIGHTS
    p.write_text("not json", encoding="ascii")
    assert whatnow.load_weights(p) == whatnow.DEFAULT_WEIGHTS


# --- ranking ----------------------------------------------------------------------

def cand(source, left_s, text=None):
    due = None if left_s is None else iso(NOW + dt.timedelta(seconds=left_s))
    return {"source": source, "text": text or source, "why": "w", "due": due, "left_s": left_s}


W1 = {s: {"weight": 1.0, "horizon_s": 10 ** 7, "nominal_s": 3600} for s in whatnow.SOURCES}


def test_rank_orders_by_urgency():
    out = whatnow.rank([cand("coupon", 72000), cand("boss", 480), cand("reset", 1500)], W1)
    assert [a["source"] for a in [out["top"]] + out["next"]] == ["boss", "reset", "coupon"]
    assert set(out["top"]) == {"text", "why", "due", "source"}
    assert out["empty"] is False


def test_rank_ties_by_weight():
    w = dict(W1, coupon={"weight": 2.0, "horizon_s": 10 ** 7, "nominal_s": 3600})
    out = whatnow.rank([cand("boss", 600), cand("coupon", 600)], w)
    assert out["top"]["source"] == "coupon"
    # equal score (weight x urgency): heavier weight first, then the sooner due
    w = dict(W1, coupon={"weight": 2.0, "horizon_s": 10 ** 7, "nominal_s": 3600})
    out = whatnow.rank([cand("boss", 300), cand("coupon", 600)], w)
    assert [out["top"]["source"], out["next"][0]["source"]] == ["coupon", "boss"]


def test_rank_top_one_plus_two_next():
    out = whatnow.rank([cand("boss", n * 60) for n in range(1, 6)], W1)
    assert out["top"] is not None and len(out["next"]) == 2


def test_rank_drops_beyond_horizon_and_past_due():
    w = dict(W1, boss={"weight": 1.0, "horizon_s": 1800, "nominal_s": 3600})
    out = whatnow.rank([cand("boss", 1801), cand("reset", -5)], w)
    assert out["top"] is None and out["empty"] is True


def test_rank_undated_uses_nominal():
    w = dict(W1, ocr={"weight": 1.0, "horizon_s": 10 ** 7, "nominal_s": 600})
    out = whatnow.rank([cand("ocr", None), cand("boss", 1200)], w)
    assert out["top"]["source"] == "ocr" and out["top"]["due"] is None


def test_empty_state():
    out = whatnow.rank([], W1)
    assert out == {"top": None, "next": [], "empty": True, "empty_text": "All clear - play"}


# --- source adapters ----------------------------------------------------------

def test_boss_adapter_next_spawn_and_looted():
    view = {"next": [{"bosses": ["Kzarka", "Nouver"], "at_utc": iso(later(minutes=8)),
                      "at_pt": "2026-10-06T11:08:00-07:00", "day": "2026-10-06", "despawn_min": 30},
                     {"bosses": ["Garmoth"], "at_utc": iso(later(minutes=50)),
                      "at_pt": "2026-10-06T11:50:00-07:00", "day": "2026-10-06", "despawn_min": 30}],
            "today": {"remaining": []},
            "looted": {"2026-10-06": ["Garmoth"]}}
    out = whatnow.from_bosses(view, NOW)
    assert [c["text"] for c in out] == ["Kzarka + Nouver spawn"]
    assert out[0]["source"] == "boss" and out[0]["left_s"] == 480 and "11:08 PT" in out[0]["why"]


def test_boss_adapter_up_now():
    view = {"next": [], "looted": {},
            "today": {"remaining": [{"bosses": ["Karanda"], "at_utc": iso(later(minutes=-5)),
                                     "at_pt": "2026-10-06T10:55:00-07:00",
                                     "day": "2026-10-06", "despawn_min": 30, "up": True}]}}
    out = whatnow.from_bosses(view, NOW)
    assert out[0]["text"] == "Karanda is up" and out[0]["left_s"] == 1500


def test_reset_adapter_counts_rows_left():
    view = {"daily_reset": iso(later(minutes=25)), "weekly_reset": iso(later(days=2)),
            "items": [{"id": "a", "title": "Barter", "kind": "daily", "done": False},
                      {"id": "b", "title": "Pet feed", "kind": "daily", "done": False},
                      {"id": "c", "title": "Done one", "kind": "daily", "done": True},
                      {"id": "d", "title": "Guild", "kind": "weekly", "done": False},
                      {"id": "e", "title": "Own rule", "kind": "daily", "done": False,
                       "next_reset": iso(later(hours=5))},
                      {"id": "f", "title": "Event", "kind": "event", "done": False}]}
    out = whatnow.from_today(view, NOW)
    by = {c["due"]: c for c in out}
    assert by[iso(later(minutes=25))]["text"] == "Finish 2 dailies before reset"
    assert by[iso(later(minutes=25))]["why"] == "Barter, Pet feed"
    assert by[iso(later(days=2))]["text"] == "Finish 1 weekly before reset"
    assert by[iso(later(hours=5))]["text"] == "Finish 1 daily before reset"
    assert all(c["source"] == "reset" for c in out) and len(out) == 3


def test_reset_adapter_nothing_left():
    view = {"daily_reset": iso(later(minutes=25)), "weekly_reset": iso(later(days=2)),
            "items": [{"id": "a", "title": "Barter", "kind": "daily", "done": True}]}
    assert whatnow.from_today(view, NOW) == []


def test_buff_adapter_needs_open_session():
    view = {"active": {"spot": "gyfin"},
            "buffs": [{"name": "Elixir", "ends": iso(later(minutes=4)), "left_s": 240},
                      {"name": "Unarmed", "ends": None, "left_s": None}]}
    out = whatnow.from_grind(view, NOW)
    assert [(c["text"], c["left_s"]) for c in out] == [("Re-arm Elixir", 240)]
    assert out[0]["source"] == "buff"
    assert whatnow.from_grind(dict(view, active=None), NOW) == []


def test_hot_time_adapter():
    view = {"hot": {"active": [{"id": "h1", "label": "Evening", "pct": 30, "ends_in_s": 3600}],
                    "next": None}}
    out = whatnow.hot_time(view, NOW)
    assert out[0]["text"] == "Hot Time +30% - grind now" and out[0]["left_s"] == 3600
    assert out[0]["source"] == "hot" and out[0]["why"] == "Evening"
    assert whatnow.hot_time({"hot": {"active": [], "next": None}}, NOW) == []


def test_deadline_adapter_only_tight_or_late():
    view = {"deadlines": [
        {"id": "d1", "label": "Season", "needs_level": 61, "enrol_by_utc": iso(later(days=3)),
         "state": "tight"},
        {"id": "d2", "label": "Other", "needs_level": 62, "enrol_by_utc": iso(later(days=3)),
         "state": "on_track"}]}
    out = whatnow.deadlines(view, NOW)
    assert [c["text"] for c in out] == ["Reach Lv 61 for Season"]
    assert out[0]["source"] == "deadline" and "tight" in out[0]["why"]


def test_hot_time_due_stable_across_clock_reads():
    a = whatnow.hot_time({"hot": {"active": [{"label": "x", "pct": 5, "ends_in_s": 3600}]}}, NOW)
    b = whatnow.hot_time({"hot": {"active": [{"label": "x", "pct": 5, "ends_in_s": 3599}]}}, NOW)
    assert a[0]["due"] == b[0]["due"] == iso(later(hours=1))


def test_deadline_late_after_enrolment_uses_quest_cutoff():
    view = {"deadlines": [{"id": "d1", "label": "Season", "needs_level": 61, "state": "late",
                           "enrol_by_utc": iso(later(days=-1)),
                           "quests_by_utc": iso(later(days=2))}]}
    out = whatnow.deadlines(view, NOW)
    assert out[0]["left_s"] == 2 * 86400


def test_boss_adapter_skips_junk_rows():
    view = {"next": ["junk", {"bosses": ["Kzarka"], "at_utc": iso(later(minutes=8)),
                              "day": "2026-10-06", "despawn_min": 30}],
            "today": {"remaining": [None]}, "looted": {}}
    assert [c["text"] for c in whatnow.from_bosses(view, NOW)] == ["Kzarka spawns"]


def test_maintenance_adapter_slot_and_notice():
    slot = {"weekday": "tue", "start_utc": "19:00", "duration_min": 180}
    out = whatnow.maintenance(NOW, slot, {})
    assert out[0]["source"] == "maint" and out[0]["left_s"] == 3600
    assert out[0]["text"] == "Maintenance soon - wrap up"
    notices = {"2026-10-06": {"start_utc": iso(later(minutes=30)), "end_utc": iso(later(hours=3)),
                              "source": "https://example.invalid/n"}}
    out = whatnow.maintenance(NOW, slot, notices)
    assert out[0]["left_s"] == 1800 and "notice" in out[0]["why"]
    # a running maintenance is not an action
    slot = {"weekday": "tue", "start_utc": "17:00", "duration_min": 180}
    out = whatnow.maintenance(NOW, slot, {})
    assert out[0]["left_s"] == 7 * 86400 - 3600


def test_coupon_adapter_open_coupons_with_end():
    view = {"items": [
        {"id": "e1", "kind": "coupon", "title": "Fall gift", "code": "FALL-2026",
         "ends": iso(later(hours=20)), "status": "active", "done": False},
        {"id": "e2", "kind": "coupon", "title": "Used", "code": "USED-1",
         "ends": iso(later(hours=2)), "status": "done", "done": True},
        {"id": "e3", "kind": "event", "title": "Not a coupon", "code": None,
         "ends": iso(later(hours=1)), "status": "active", "done": False},
        {"id": "e4", "kind": "coupon", "title": "Open-ended", "code": "OPEN-1",
         "ends": None, "status": "active", "done": False}]}
    out = whatnow.from_events(view, NOW)
    # plan 074: open events with an end rank too, as source `event`
    assert [(c["source"], c["text"], c["left_s"]) for c in out] == [
        ("coupon", "Redeem coupon FALL-2026", 72000), ("event", "Not a coupon ends", 3600)]
    assert out[0]["why"] == "Fall gift expires" and out[1]["why"] == "event ends"


def test_dice_adapter():
    view = {"items": [{"id": "black-spirits-adventure-dice", "title": "Black Spirit's Adventure dice",
                       "kind": "daily", "done": False}],
            "dice": {"earned": 2, "max": 3, "next_reset": iso(later(hours=6))}}
    out = whatnow.dice(view, NOW)
    assert out[0]["text"] == "Roll the dice (2/3 earned)" and out[0]["left_s"] == 21600
    view["items"][0]["done"] = True
    assert whatnow.dice(view, NOW) == []
    view["items"][0]["done"] = False
    view["dice"]["earned"] = 0
    assert whatnow.dice(view, NOW) == []


def test_market_adapter_alert_hits_only():
    items = [{"id": 1, "name": "Memory Fragment", "price": 1500, "below": 2000, "above": None,
              "alert": "below"},
             {"id": 2, "name": None, "price": 9, "below": None, "above": None, "alert": None}]
    out = whatnow.market_alerts(items, NOW)
    assert [c["text"] for c in out] == ["Market: Memory Fragment below"]
    assert out[0]["due"] is None and out[0]["left_s"] is None and out[0]["source"] == "market"


def test_ocr_adapter():
    out = whatnow.ocr_review({"review": [{"id": 1}, {"id": 2}]}, NOW)
    assert out[0]["text"] == "Review 2 OCR reads" and out[0]["source"] == "ocr"
    assert whatnow.ocr_review({"review": []}, NOW) == []


def test_collect_survives_a_broken_source():
    def boom():
        raise RuntimeError("bad")
    view = {"next": [{"bosses": ["Kzarka"], "at_utc": iso(later(minutes=8)),
                      "at_pt": "2026-10-06T11:08:00-07:00", "day": "2026-10-06",
                      "despawn_min": 30}], "today": {"remaining": []}, "looted": {}}
    out = whatnow.collect({"bosses": lambda: view, "today": boom}, NOW, whatnow.load_weights())
    assert out["top"]["source"] == "boss" and out["errors"] == ["today"]


# --- acceptance ------------------------------------------------------------------

def test_acceptance_boss_reset_coupon():
    inputs = {
        "bosses": lambda: {"next": [{"bosses": ["Kzarka"], "at_utc": iso(later(minutes=8)),
                                     "at_pt": "2026-10-06T11:08:00-07:00", "day": "2026-10-06",
                                     "despawn_min": 30}], "today": {"remaining": []},
                           "looted": {}},
        "today": lambda: {"daily_reset": iso(later(minutes=25)),
                          "weekly_reset": iso(later(days=2)),
                          "items": [{"id": "a", "title": "Barter", "kind": "daily",
                                     "done": False}]},
        "events": lambda: {"items": [{"id": "e1", "kind": "coupon", "title": "Gift",
                                      "code": "GIFT-1", "ends": iso(later(hours=22)),
                                      "status": "active", "done": False}]},
    }
    out = whatnow.collect(inputs, NOW, whatnow.load_weights())
    assert [a["source"] for a in [out["top"]] + out["next"]] == ["boss", "reset", "coupon"]


# --- service + routes -----------------------------------------------------------

class Clock:
    def __init__(self, when):
        self.t = when.timestamp()

    def __call__(self):
        return self.t


def test_service_seq_bumps_only_on_change():
    state = {"n": 1}
    inputs = {"ocr": lambda: {"review": [{"id": i} for i in range(state["n"])]}}
    clock = Clock(NOW)
    svc = whatnow.WhatNowService(inputs, clock=clock)
    s1, v1 = svc.refresh(0)
    assert v1["top"]["text"] == "Review 1 OCR read" and v1["now"] == iso(NOW)
    clock.t += 10
    assert svc.refresh(0)[0] == s1  # same actions, clock moved: no change
    state["n"] = 3
    assert svc.refresh(60)[0] == s1  # cached within max_age
    s2, v2 = svc.refresh(0)
    assert s2 == s1 + 1 and v2["top"]["text"] == "Review 3 OCR reads"


def _no_network(url, timeout):
    raise AssertionError("test touched the network")


@pytest.fixture()
def srv(tmp_path):
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          sse_interval=0.05, market_seed=[],
                          market_client=market.ArshaClient(fetch=_no_network,
                                                           cache_dir=tmp_path / "cache"),
                          profile_cfg={})
    t = threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    t.start()
    yield s
    s.shutdown()
    s.server_close()


def _get(s, path):
    c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=5)
    c.request("GET", path)
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, json.loads(body)


def test_route_whatnow(srv):
    st, doc = _get(srv, "/api/whatnow")
    assert st == 200
    assert {"now", "top", "next", "empty", "empty_text", "errors"} <= set(doc)
    assert doc["errors"] == []
    for a in ([doc["top"]] if doc["top"] else []) + doc["next"]:
        assert set(a) == {"text", "why", "due", "source"}


def test_sse_whatnow_event(srv):
    c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
    c.request("GET", "/events")
    r = c.getresponse()
    assert b"heartbeat" in r.fp.readline()
    name = None
    for _ in range(400):
        line = r.fp.readline().decode("utf-8").rstrip("\n")
        if line.startswith("event: "):
            name = line[len("event: "):]
        elif line.startswith("data: ") and name == "whatnow":
            doc = json.loads(line[len("data: "):])
            break
    else:
        pytest.fail("no whatnow event")
    c.close()
    assert "top" in doc and "next" in doc
