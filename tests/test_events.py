"""Plan 006 slice A: events store domain (coupons, events, Twitch drops), status
and order rules, countdowns, routes.

No network, no game client, no seeded codes: everything is operator input plus a
fixed list of official source links. The clock is injected so countdowns are exact.
"""

import datetime as dt
import http.client
import json
import threading

import pytest

from server.ew import app as ewapp
from server.ew import events
from server.ew.store import Store

UTC = dt.timezone.utc
T0 = dt.datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
DAY = 86400


class Clock:
    def __init__(self, when=T0):
        self.t = when.timestamp()

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


@pytest.fixture()
def clock():
    return Clock()


@pytest.fixture()
def svc(tmp_path, clock):
    return events.EventsService(Store(tmp_path / "store"), clock=clock)


def _at(seconds):
    return (T0 + dt.timedelta(seconds=seconds)).isoformat()


def _add(svc, **kw):
    arg = {"kind": "event", "title": "Season pass"}
    arg.update(kw)
    return svc.add(arg)["item"]


def _item(doc, iid):
    return next(i for i in doc["items"] if i["id"] == iid)


# --- seed / sources ----------------------------------------------------------

def test_seed_empty(svc):
    doc = svc.view()
    assert doc["items"] == []
    assert doc["counts"] == {"coupon": 0, "event": 0, "drop": 0}
    assert doc["now"] == T0.isoformat()
    assert doc["sources"] == events.SOURCES


def test_sources_official_https_only():
    assert len(events.SOURCES) >= 2
    for s in events.SOURCES:
        assert set(s) == {"name", "url"}
        assert s["url"].startswith("https://") and s["name"]
    urls = " ".join(s["url"] for s in events.SOURCES)
    assert "naeu.playblackdesert.com" in urls and "twitch.tv" in urls


def test_seed_not_reapplied(tmp_path, clock):
    st = Store(tmp_path / "store")
    a = events.EventsService(st, clock=clock)
    a.add({"kind": "event", "title": "A"})
    b = events.EventsService(st, clock=clock)
    assert [i["title"] for i in b.view()["items"]] == ["A"]


# --- add ---------------------------------------------------------------------

def test_add_coupon_full(svc):
    doc = svc.add({"kind": "coupon", "title": " Fall coupon ", "code": "abcd-1234-efgh",
                   "rewards": "Cron x100", "starts": "2026-10-01T00:00:00Z",
                   "ends": "2026-10-10T08:00:00-07:00", "url": "https://example.com/n"})
    it = doc["item"]
    assert it["id"] == "e1" and it["kind"] == "coupon" and it["title"] == "Fall coupon"
    assert it["code"] == "ABCD-1234-EFGH" and it["rewards"] == "Cron x100"
    assert it["starts"] == "2026-10-01T00:00:00+00:00"
    assert it["ends"] == "2026-10-10T15:00:00+00:00"
    assert it["url"] == "https://example.com/n" and it["done"] is False
    assert it["status"] == "active" and it["soon"] is False
    assert it["left_s"] == int((dt.datetime(2026, 10, 10, 15, tzinfo=UTC) - T0).total_seconds())
    assert doc["items"] == [it]
    assert doc["counts"] == {"coupon": 1, "event": 0, "drop": 0}


def test_add_minimal_optional_fields_null(svc):
    it = _add(svc, kind="drop", title="Twitch drop")
    assert it == {"id": "e1", "kind": "drop", "title": "Twitch drop", "code": None,
                  "rewards": None, "starts": None, "ends": None, "url": None, "done": False,
                  "left_s": None, "status": "active", "soon": False}


def test_date_only_ends_end_of_day_starts_start_of_day(svc):
    it = _add(svc, starts="2026-10-05", ends="2026-10-06")
    assert it["starts"] == "2026-10-05T00:00:00+00:00"
    assert it["ends"] == "2026-10-06T23:59:59+00:00"
    assert it["status"] == "upcoming"


def test_ids_never_reused(svc):
    a = _add(svc)["id"]
    b = _add(svc)["id"]
    svc.delete(b)
    c = _add(svc)["id"]
    assert (a, b, c) == ("e1", "e2", "e3")


@pytest.mark.parametrize("arg", [
    {"kind": "quest", "title": "x"},
    {"kind": "event"},
    {"title": "x"},
    {"kind": "event", "title": ""},
    {"kind": "event", "title": "   "},
    {"kind": "event", "title": "x" * 81},
    {"kind": "event", "title": "a\nb"},
    {"kind": "event", "title": 5},
    {"kind": "event", "title": "x", "extra": 1},
    {"kind": "event", "title": "x", "code": "ABCD"},
    {"kind": "coupon", "title": "x"},
    {"kind": "coupon", "title": "x", "code": None},
    {"kind": "coupon", "title": "x", "code": "ABC"},
    {"kind": "coupon", "title": "x", "code": "A" * 41},
    {"kind": "coupon", "title": "x", "code": "ABCD_1234"},
    {"kind": "coupon", "title": "x", "code": "ABCD 1234"},
    {"kind": "event", "title": "x", "rewards": "r" * 201},
    {"kind": "event", "title": "x", "rewards": 5},
    {"kind": "event", "title": "x", "url": "http://example.com"},
    {"kind": "event", "title": "x", "url": "javascript:alert(1)"},
    {"kind": "event", "title": "x", "url": "https://e.com/" + "a" * 300},
    {"kind": "event", "title": "x", "url": "https://"},
    {"kind": "event", "title": "x", "url": "https://exa mple.com"},
    {"kind": "event", "title": "x", "ends": "2026-10-10T00:00:00"},
    {"kind": "event", "title": "x", "ends": "2026-13-01"},
    {"kind": "event", "title": "x", "ends": "tomorrow"},
    {"kind": "event", "title": "x", "ends": 1700000000},
    {"kind": "event", "title": "x", "ends": "2029-01-01"},
    {"kind": "event", "title": "x", "ends": "2024-01-01"},
    {"kind": "event", "title": "x", "starts": "2026-10-09", "ends": "2026-10-08"},
    {"kind": "event", "title": "x", "starts": "2029-01-01"},
    "x", None, [1],
])
def test_add_bad(svc, arg):
    with pytest.raises(ValueError):
        svc.add(arg)
    assert svc.view()["items"] == []


def test_add_limits_inclusive(svc):
    it = _add(svc, kind="coupon", title="x" * 80, code="abcd", rewards="r" * 200,
              url="https://e.com/" + "a" * (300 - len("https://e.com/")))
    assert it["code"] == "ABCD" and len(it["url"]) == 300
    _add(svc, kind="coupon", code="A" * 40)
    _add(svc, ends=_at(730 * DAY))
    _add(svc, starts="2026-10-04", ends="2026-10-04")


def test_add_empty_rewards_is_null(svc):
    assert _add(svc, rewards="  ")["rewards"] is None


def test_duplicate_code_case_insensitive(svc):
    _add(svc, kind="coupon", code="ABCD-1234")
    with pytest.raises(ValueError):
        _add(svc, kind="coupon", code="abcd-1234")
    assert len(svc.view()["items"]) == 1


def test_max_items(svc):
    for _ in range(events.MAX_ITEMS):
        _add(svc)
    with pytest.raises(ValueError):
        _add(svc)
    assert len(svc.view()["items"]) == events.MAX_ITEMS


# --- status / soon / countdown -------------------------------------------------

def test_countdown_soon_and_expiry(svc, clock):
    it = _add(svc, ends=_at(3 * DAY))
    assert it["left_s"] == 3 * DAY and it["soon"] is False and it["status"] == "active"
    clock.advance(DAY)
    it = _item(svc.view(), "e1")
    assert it["left_s"] == 2 * DAY and it["soon"] is True
    clock.advance(2 * DAY - 1)
    it = _item(svc.view(), "e1")
    assert it["left_s"] == 1 and it["status"] == "active"
    clock.advance(1)
    it = _item(svc.view(), "e1")
    assert it["status"] == "expired" and it["soon"] is False and it["left_s"] == 0
    assert svc.view()["counts"]["event"] == 0


def test_upcoming_until_starts(svc, clock):
    _add(svc, starts=_at(60), ends=_at(DAY))
    it = _item(svc.view(), "e1")
    assert it["status"] == "upcoming" and it["soon"] is True
    clock.advance(60)
    assert _item(svc.view(), "e1")["status"] == "active"


def test_done_beats_expired_and_not_soon(svc, clock):
    _add(svc, ends=_at(60))
    doc = svc.done({"id": "e1", "done": True})
    it = _item(doc, "e1")
    assert it["status"] == "done" and it["soon"] is False
    assert doc["counts"]["event"] == 0
    clock.advance(120)
    assert _item(svc.view(), "e1")["status"] == "done"
    doc = svc.done({"id": "e1", "done": False})
    assert _item(doc, "e1")["status"] == "expired"


def test_counts_open_by_kind(svc, clock):
    _add(svc, kind="coupon", code="AAAA")
    _add(svc, kind="coupon", code="BBBB", ends=_at(10))
    _add(svc, kind="drop", title="d")
    _add(svc, kind="event", title="e")
    svc.done({"id": "e4", "done": True})
    clock.advance(10)
    assert svc.view()["counts"] == {"coupon": 1, "event": 0, "drop": 1}


def test_order(svc, clock):
    _add(svc, title="open-late", ends=_at(5 * DAY))          # e1
    _add(svc, title="open-noend")                            # e2
    _add(svc, title="open-soon", ends=_at(DAY))              # e3
    _add(svc, title="done", ends=_at(2 * DAY))               # e4
    _add(svc, title="exp-old", ends=_at(100))                # e5
    _add(svc, title="exp-new", ends=_at(200))                # e6
    _add(svc, title="upcoming", starts=_at(DAY), ends=_at(3 * DAY))  # e7
    svc.done({"id": "e4", "done": True})
    clock.advance(300)
    titles = [i["title"] for i in svc.view()["items"]]
    assert titles == ["open-soon", "upcoming", "open-late", "open-noend", "done",
                      "exp-new", "exp-old"]


# --- edit ----------------------------------------------------------------------

def test_edit_fields_and_null_clears(svc):
    _add(svc, rewards="r", url="https://e.com", ends="2026-10-10", starts="2026-10-05")
    doc = svc.edit({"id": "e1", "title": "New", "rewards": None, "url": None,
                    "starts": None, "ends": "2026-10-20T00:00:00Z"})
    it = _item(doc, "e1")
    assert it["title"] == "New" and it["rewards"] is None and it["url"] is None
    assert it["starts"] is None and it["ends"] == "2026-10-20T00:00:00+00:00"
    doc = svc.edit({"id": "e1", "ends": None})
    assert _item(doc, "e1")["ends"] is None and _item(doc, "e1")["title"] == "New"


def test_edit_coupon_code(svc):
    _add(svc, kind="coupon", code="AAAA")
    _add(svc, kind="coupon", code="BBBB")
    assert _item(svc.edit({"id": "e1", "code": "aaaa"}), "e1")["code"] == "AAAA"  # self ok
    assert _item(svc.edit({"id": "e1", "code": "cccc"}), "e1")["code"] == "CCCC"
    with pytest.raises(ValueError):
        svc.edit({"id": "e1", "code": "bbbb"})
    with pytest.raises(ValueError):
        svc.edit({"id": "e1", "code": None})


@pytest.mark.parametrize("arg", [
    {"id": "e1", "title": None},
    {"id": "e1", "title": ""},
    {"id": "e1", "code": "ABCD"},
    {"id": "e1", "kind": "coupon"},
    {"id": "e1", "done": True},
    {"id": "e1", "nope": 1},
    {"id": "e1", "url": "ftp://x"},
    {"id": "e1", "starts": "2026-10-20"},
    {"id": "e1", "ends": "2026-10-04T00:00:00Z", "starts": "2026-10-05"},
    {"id": "e9", "title": "x"},
    {"id": 1, "title": "x"},
    {"title": "x"},
    "e1",
])
def test_edit_bad_leaves_item(svc, arg):
    _add(svc, title="Keep", ends="2026-10-10")
    with pytest.raises(ValueError):
        svc.edit(arg)
    it = _item(svc.view(), "e1")
    assert it["title"] == "Keep" and it["ends"] == "2026-10-10T23:59:59+00:00"


def test_edit_id_only_is_noop(svc):
    _add(svc, title="Keep")
    assert _item(svc.edit({"id": "e1"}), "e1")["title"] == "Keep"


# --- done / delete / purge ---------------------------------------------------------

@pytest.mark.parametrize("arg", [
    {"id": "e1", "done": 1}, {"id": "e1"}, {"id": "e1", "done": True, "x": 1},
    {"id": "e9", "done": True}, "e1", None,
])
def test_done_bad(svc, arg):
    _add(svc)
    with pytest.raises(ValueError):
        svc.done(arg)
    assert _item(svc.view(), "e1")["done"] is False


def test_delete(svc):
    _add(svc)
    _add(svc)
    doc = svc.delete("e1")
    assert [i["id"] for i in doc["items"]] == ["e2"]
    for bad in ("e1", "x", 5, None):
        with pytest.raises(ValueError):
            svc.delete(bad)


def test_purge_expired(svc, clock):
    _add(svc, title="a", ends=_at(10))
    _add(svc, title="b", ends=_at(10))
    _add(svc, title="c", ends=_at(DAY))
    _add(svc, title="d")
    svc.done({"id": "e2", "done": True})
    clock.advance(10)
    doc = svc.purge_expired(True)
    assert doc["purged"] == 2
    assert [i["title"] for i in doc["items"]] == ["c", "d"]
    assert svc.purge_expired(True)["purged"] == 0
    for bad in (False, 1, "yes", None):
        with pytest.raises(ValueError):
            svc.purge_expired(bad)


# --- store degradation -------------------------------------------------------

def test_corrupt_entries_skipped(tmp_path, clock):
    st = Store(tmp_path / "store")
    st.put("events", {"items": [
        {"id": "e3", "kind": "event", "title": "ok", "ends": "junk", "done": "x"},
        {"id": "e3", "kind": "event", "title": "dup"},
        {"id": "e4", "kind": "quest", "title": "bad kind"},
        {"id": "x", "kind": "event", "title": "bad id"},
        {"id": "e5", "kind": "event", "title": ""},
        {"id": "e6", "kind": "event", "title": "t", "ends": "0001-01-01T00:00:00+01:00"},
        "junk", None], "next_id": "nope"})
    svc = events.EventsService(st, clock=clock)
    doc = svc.view()
    assert [i["id"] for i in doc["items"]] == ["e3", "e6"]
    it = _item(doc, "e3")
    assert it["ends"] is None and it["done"] is False and it["code"] is None
    assert _item(doc, "e6")["ends"] is None
    assert _add(svc)["id"] == "e7"


def test_source(svc, clock):
    assert svc.source() == {"updated": T0.isoformat(), "status": "ok", "open": 0,
                            "soonest": None}
    _add(svc, ends=_at(DAY))
    _add(svc, ends=_at(10))
    _add(svc, ends=_at(5))
    svc.done({"id": "e3", "done": True})
    src = svc.source()
    assert src["open"] == 3 - 1 and src["soonest"] == _at(10)
    clock.advance(10)
    src = svc.source()
    assert src["open"] == 1 and src["soonest"] == _at(DAY)


def test_concurrent_adds_unique_ids(svc):
    def worker():
        for _ in range(10):
            _add(svc)
    ts = [threading.Thread(target=worker) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    ids = [i["id"] for i in svc.view()["items"]]
    assert len(ids) == 40 and len(set(ids)) == 40


# --- routes ------------------------------------------------------------------

@pytest.fixture()
def esrv(tmp_path, clock):
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          sse_interval=0.05, market_seed=[], events_clock=clock)
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield s
    s.shutdown()
    s.server_close()


def _req(s, method, path, body=None, ctype="application/json", host=None):
    c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=5)
    headers = {"Content-Type": ctype} if ctype else {}
    if host:
        headers["Host"] = host
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
    c.request(method, path, body=data, headers=headers)
    r = c.getresponse()
    out = r.read()
    acao = r.getheader("Access-Control-Allow-Origin")
    c.close()
    return r.status, (json.loads(out) if out else None), acao


def test_route_get(esrv):
    st, doc, _ = _req(esrv, "GET", "/api/events")
    assert st == 200 and set(doc) >= {"now", "sources", "counts", "items"}
    assert doc["now"] == T0.isoformat()


def test_route_post_ops(esrv, clock):
    st, doc, acao = _req(esrv, "POST", "/api/events",
                         {"add": {"kind": "coupon", "title": "C", "code": "abcd",
                                  "ends": _at(DAY)}})
    assert st == 200 and acao is None and doc["item"]["code"] == "ABCD"
    st, doc, _ = _req(esrv, "POST", "/api/events", {"edit": {"id": "e1", "title": "C2"}})
    assert st == 200 and doc["items"][0]["title"] == "C2"
    st, doc, _ = _req(esrv, "POST", "/api/events", {"done": {"id": "e1", "done": True}})
    assert st == 200 and doc["items"][0]["status"] == "done"
    _req(esrv, "POST", "/api/events", {"add": {"kind": "event", "title": "E", "ends": _at(5)}})
    clock.advance(5)
    st, doc, _ = _req(esrv, "POST", "/api/events", {"purge_expired": True})
    assert st == 200 and doc["purged"] == 1 and [i["id"] for i in doc["items"]] == ["e1"]
    st, doc, _ = _req(esrv, "POST", "/api/events", {"delete": "e1"})
    assert st == 200 and doc["items"] == []


def test_route_post_guards(esrv):
    body = {"add": {"kind": "event", "title": "A"}}
    assert _req(esrv, "POST", "/api/events", body, host="evil.example.com")[0] == 403
    assert _req(esrv, "POST", "/api/events", body, ctype="text/plain")[0] == 415
    big = json.dumps({"add": {"kind": "event", "title": "A"}, "pad": "x" * 5000}).encode()
    assert _req(esrv, "POST", "/api/events", big)[0] == 413
    assert _req(esrv, "GET", "/api/events")[1]["items"] == []
    assert _req(esrv, "GET", "/api/events", host="evil.example.com")[0] == 403


@pytest.mark.parametrize("body", [b"not json", b"[]", {}, {"nope": 1},
                                  {"add": {"kind": "event", "title": "A"}, "delete": "e1"},
                                  {"add": {"kind": "coupon", "title": "A"}},
                                  {"edit": {"id": "e9", "title": "x"}},
                                  {"done": {"id": "e9", "done": True}},
                                  {"delete": "e9"}, {"purge_expired": False}])
def test_route_post_bad_body(esrv, body):
    st, doc, _ = _req(esrv, "POST", "/api/events", body)
    assert st == 400 and "error" in doc


def test_state_reports_events_source(esrv, clock):
    _req(esrv, "POST", "/api/events", {"add": {"kind": "drop", "title": "D", "ends": _at(DAY)}})
    _, doc, _ = _req(esrv, "GET", "/api/state")
    src = doc["sources"]["events"]
    assert src["status"] == "ok" and src["open"] == 1 and src["soonest"] == _at(DAY)
