"""Plan 003 slice A: reset clocks, derived done-state, today store domain, routes.

No network, no game client: everything is operator ticks plus a seed.
"""

import datetime as dt
import http.client
import json
import threading

import pytest

from server.ew import app as ewapp
from server.ew import today
from server.ew.store import Store

UTC = dt.timezone.utc


def T(*a):
    return dt.datetime(*a, tzinfo=UTC)


class Clock:
    def __init__(self, when):
        self.t = when.timestamp()

    def __call__(self):
        return self.t

    def set(self, when):
        self.t = when.timestamp()


# --- reset clocks ----------------------------------------------------------

@pytest.mark.parametrize("now, want", [
    (T(2026, 10, 4, 0, 0, 0), T(2026, 10, 4)),            # exactly 00:00:00
    (T(2026, 10, 3, 23, 59, 59), T(2026, 10, 3)),         # one second before
    (T(2026, 10, 4, 13, 30), T(2026, 10, 4)),
    (T(2026, 11, 1, 0, 0, 0), T(2026, 11, 1)),            # month rollover
    (T(2026, 10, 31, 23, 59, 59), T(2026, 10, 31)),
    (T(2027, 1, 1, 0, 0, 0), T(2027, 1, 1)),              # year rollover
    (T(2026, 12, 31, 23, 59, 59), T(2026, 12, 31)),
])
def test_last_daily_reset(now, want):
    assert today.last_daily_reset(now) == want


@pytest.mark.parametrize("now, want", [
    (T(2026, 10, 1, 0, 0, 0), T(2026, 10, 1)),            # Thursday exactly 00:00
    (T(2026, 9, 30, 23, 59, 59), T(2026, 9, 24)),         # Wednesday one second before
    (T(2026, 10, 1, 0, 0, 1), T(2026, 10, 1)),
    (T(2026, 10, 4, 12, 0), T(2026, 10, 1)),              # Sunday
    (T(2026, 10, 7, 23, 59, 59), T(2026, 10, 1)),         # following Wednesday
    (T(2026, 10, 8, 0, 0, 0), T(2026, 10, 8)),            # next Thursday
    (T(2027, 1, 2, 10, 0), T(2026, 12, 31)),              # year rollover (Thu Dec 31)
    (T(2026, 3, 2, 10, 0), T(2026, 2, 26)),               # month rollover
])
def test_last_weekly_reset(now, want):
    assert today.last_weekly_reset(now) == want
    assert want.weekday() == 3


def test_next_resets_and_naive_rejected():
    now = T(2026, 10, 4, 12)
    assert today.next_daily_reset(now) == T(2026, 10, 5)
    assert today.next_weekly_reset(now) == T(2026, 10, 8)
    assert today.next_weekly_reset(T(2026, 10, 8)) == T(2026, 10, 15)
    with pytest.raises(ValueError):
        today.last_daily_reset(dt.datetime(2026, 10, 4))


def test_non_utc_offset_normalised():
    # 2026-10-01 01:00 at +02:00 is Wed 2026-09-30 23:00 UTC.
    now = dt.datetime(2026, 10, 1, 1, 0, tzinfo=dt.timezone(dt.timedelta(hours=2)))
    assert today.last_daily_reset(now) == T(2026, 9, 30)
    assert today.last_weekly_reset(now) == T(2026, 9, 24)


# --- slugs -----------------------------------------------------------------

@pytest.mark.parametrize("title, want", [
    ("Black Spirit's Adventure dice", "black-spirits-adventure-dice"),
    ("daily Challenges (Y)", "daily-challenges-y"),
    ("  --Hello__World!!  ", "hello-world"),
    ("!!!", "item"),
    ("x" * 60, "x" * 40),
])
def test_slug(title, want):
    s = today.slug(title)
    assert s == want and today.ID_RE.match(s)


# --- service ---------------------------------------------------------------

def _svc(tmp_path, when=T(2026, 10, 4, 12)):
    clk = Clock(when)
    return today.TodayService(Store(tmp_path / "store"), clock=clk), clk


def _item(view, iid):
    return next(i for i in view["items"] if i["id"] == iid)


def test_seed_once(tmp_path):
    svc, _ = _svc(tmp_path)
    v = svc.view()
    ids = [i["id"] for i in v["items"]]
    assert ids == ["attendance-reward", "black-spirits-adventure-dice", "daily-challenges-y",
                   "barter-run", "guild-mission", "black-spirit-weekly-quests",
                   "weekly-boss-rewards", "pearl-shop-weekly-free-item"]
    kinds = [i["kind"] for i in v["items"]]
    assert kinds == ["daily"] * 5 + ["weekly"] * 3
    assert all(i["done"] is False and i["ticked_at"] is None and i["until"] is None
               for i in v["items"])
    # emptied list is not re-seeded
    for iid in ids:
        svc.remove(iid)
    svc2 = today.TodayService(Store(tmp_path / "store"), clock=Clock(T(2026, 10, 4)))
    assert svc2.view()["items"] == []


def test_view_shape(tmp_path):
    svc, _ = _svc(tmp_path, T(2026, 10, 4, 12, 0, 5))
    v = svc.view()
    assert v["now"] == "2026-10-04T12:00:05+00:00"
    assert v["daily_reset"] == "2026-10-05T00:00:00+00:00"
    assert v["weekly_reset"] == "2026-10-08T00:00:00+00:00"
    assert set(v["items"][0]) == {"id", "title", "kind", "until", "done", "ticked_at"}


def test_tick_untick(tmp_path):
    svc, _ = _svc(tmp_path)
    v = svc.tick("barter-run")
    assert _item(v, "barter-run")["done"] is True
    assert _item(v, "barter-run")["ticked_at"] == "2026-10-04T12:00:00+00:00"
    v = svc.untick("barter-run")
    assert _item(v, "barter-run")["done"] is False and _item(v, "barter-run")["ticked_at"] is None
    svc.untick("barter-run")  # idempotent


def test_daily_done_derived_across_reset(tmp_path):
    svc, clk = _svc(tmp_path, T(2026, 10, 4, 23, 59, 59))
    svc.tick("barter-run")
    clk.set(T(2026, 10, 5, 0, 0, 0))
    it = _item(svc.view(), "barter-run")
    assert it["done"] is False and it["ticked_at"] == "2026-10-04T23:59:59+00:00"
    # a fresh service (server restarted over the reset) agrees
    svc2 = today.TodayService(Store(tmp_path / "store"), clock=clk)
    assert _item(svc2.view(), "barter-run")["done"] is False


def test_weekly_done_survives_daily_reset_until_thursday(tmp_path):
    svc, clk = _svc(tmp_path, T(2026, 10, 2, 9))  # Friday
    svc.tick("weekly-boss-rewards")
    clk.set(T(2026, 10, 7, 23, 59, 59))           # Wednesday
    assert _item(svc.view(), "weekly-boss-rewards")["done"] is True
    clk.set(T(2026, 10, 8, 0, 0, 0))              # Thursday reset
    assert _item(svc.view(), "weekly-boss-rewards")["done"] is False


def test_tick_exactly_at_reset_counts(tmp_path):
    svc, _ = _svc(tmp_path, T(2026, 10, 5, 0, 0, 0))
    assert _item(svc.tick("barter-run"), "barter-run")["done"] is True


def test_event_daily_window_and_expiry(tmp_path):
    svc, clk = _svc(tmp_path, T(2026, 10, 4, 12))
    v = svc.add({"title": "Autumn login event", "kind": "event", "until": "2026-10-06"})
    it = _item(v, "autumn-login-event")
    assert it["kind"] == "event" and it["until"] == "2026-10-06" and it["done"] is False
    svc.tick("autumn-login-event")
    clk.set(T(2026, 10, 5, 1))
    assert _item(svc.view(), "autumn-login-event")["done"] is False  # resets daily
    clk.set(T(2026, 10, 6, 23, 59, 59))
    assert any(i["id"] == "autumn-login-event" for i in svc.view()["items"])  # last day shown
    clk.set(T(2026, 10, 7, 0, 0, 0))
    assert all(i["id"] != "autumn-login-event" for i in svc.view()["items"])  # expired


def test_add_dedupes_id(tmp_path):
    svc, _ = _svc(tmp_path)
    svc.add({"title": "Barter run", "kind": "daily"})
    v = svc.add({"title": "barter RUN!", "kind": "weekly"})
    ids = [i["id"] for i in v["items"]]
    assert ids.count("barter-run") == 1 and "barter-run-2" in ids and "barter-run-3" in ids
    assert ids[-1] == "barter-run-3"
    long = "y" * 40
    svc.add({"title": long, "kind": "daily"})
    v = svc.add({"title": long, "kind": "daily"})
    assert "y" * 38 + "-2" in [i["id"] for i in v["items"]]


def test_remove_drops_tick(tmp_path):
    svc, _ = _svc(tmp_path)
    svc.tick("guild-mission")
    v = svc.remove("guild-mission")
    assert all(i["id"] != "guild-mission" for i in v["items"])
    v = svc.add({"title": "Guild mission", "kind": "daily"})
    assert _item(v, "guild-mission")["ticked_at"] is None


def test_move(tmp_path):
    svc, _ = _svc(tmp_path)
    v = svc.move({"id": "guild-mission", "to": 0})
    assert [i["id"] for i in v["items"]][:2] == ["guild-mission", "attendance-reward"]
    v = svc.move({"id": "guild-mission", "to": 99})  # clamped to the end
    assert v["items"][-1]["id"] == "guild-mission"
    doc = Store(tmp_path / "store").get("today")
    assert [i["order"] for i in doc["items"]] == list(range(len(doc["items"])))


@pytest.mark.parametrize("call, arg", [
    ("tick", "nope"), ("tick", "BAD ID"), ("tick", 5), ("untick", "x" * 41),
    ("remove", "nope"), ("remove", None),
    ("add", {"title": "", "kind": "daily"}),
    ("add", {"title": "   ", "kind": "daily"}),
    ("add", {"title": "x" * 81, "kind": "daily"}),
    ("add", {"title": "a\nb", "kind": "daily"}),
    ("add", {"title": 5, "kind": "daily"}),
    ("add", {"title": "ok", "kind": "monthly"}),
    ("add", {"title": "ok"}),
    ("add", {"title": "ok", "kind": "event", "until": "2026-13-01"}),
    ("add", {"title": "ok", "kind": "event", "until": "next week"}),
    ("add", {"title": "ok", "kind": "event", "until": 20261001}),
    ("add", {"title": "ok", "kind": "daily", "extra": 1}),
    ("add", "ok"),
    ("move", {"id": "barter-run", "to": "1"}),
    ("move", {"id": "barter-run", "to": True}),
    ("move", {"id": "barter-run", "to": -1}),
    ("move", {"id": "nope", "to": 0}),
    ("move", {"id": "barter-run"}),
    ("move", ["barter-run", 0]),
])
def test_validation(tmp_path, call, arg):
    svc, _ = _svc(tmp_path)
    before = Store(tmp_path / "store").get("today")
    with pytest.raises(ValueError):
        getattr(svc, call)(arg)
    assert Store(tmp_path / "store").get("today") == before


def test_item_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(today, "MAX_ITEMS", 9)
    svc, _ = _svc(tmp_path)
    svc.add({"title": "ninth", "kind": "daily"})
    with pytest.raises(ValueError):
        svc.add({"title": "tenth", "kind": "daily"})


def test_corrupt_doc_degrades(tmp_path):
    st = Store(tmp_path / "store")
    st.put("today", {"items": [{"id": "ok-one", "title": "Ok", "kind": "daily", "order": 0},
                               {"id": "BAD", "title": "x", "kind": "daily"},
                               {"id": "no-kind", "title": "x"}, "junk", None],
                     "ticks": {"ok-one": "not a date", "ghost": "2026-10-04T00:00:00+00:00"}})
    svc = today.TodayService(st, clock=Clock(T(2026, 10, 4, 12)))
    v = svc.view()
    assert [i["id"] for i in v["items"]] == ["ok-one"]
    assert v["items"][0]["done"] is False and v["items"][0]["ticked_at"] is None
    assert _item(svc.tick("ok-one"), "ok-one")["done"] is True
    st.put("today", {"items": "garbage", "ticks": []})
    assert svc.view()["items"] == []


def test_source(tmp_path):
    svc, clk = _svc(tmp_path, T(2026, 10, 4, 12))
    assert svc.source() == {"updated": "2026-10-04T12:00:00+00:00", "status": "ok"}
    clk.set(T(2026, 10, 4, 13))
    svc.tick("barter-run")
    assert svc.source()["updated"] == "2026-10-04T13:00:00+00:00"


def test_concurrent_ticks_lose_nothing(tmp_path):
    svc, _ = _svc(tmp_path)
    ids = [f"item-{n}" for n in range(12)]
    for n in range(12):
        svc.add({"title": f"item {n}", "kind": "daily"})
    errors = []

    def worker(iid):
        try:
            svc.tick(iid)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=worker, args=(i,)) for i in ids]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors
    v = svc.view()
    assert all(_item(v, i)["done"] for i in ids)


# --- routes ----------------------------------------------------------------

@pytest.fixture()
def tsrv(tmp_path):
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          sse_interval=0.05, market_seed=[])
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


def test_route_get(tsrv):
    st, doc, _ = _req(tsrv, "GET", "/api/today")
    assert st == 200 and len(doc["items"]) == 8
    assert doc["daily_reset"].endswith("T00:00:00+00:00")
    assert dt.datetime.fromisoformat(doc["weekly_reset"]).weekday() == 3


def test_route_post_ops(tsrv):
    st, doc, acao = _req(tsrv, "POST", "/api/today", {"tick": "barter-run"})
    assert st == 200 and acao is None and _item(doc, "barter-run")["done"] is True
    st, doc, _ = _req(tsrv, "POST", "/api/today", {"untick": "barter-run"})
    assert _item(doc, "barter-run")["done"] is False
    st, doc, _ = _req(tsrv, "POST", "/api/today",
                      {"add": {"title": "Dice event", "kind": "event", "until": "2099-01-01"}})
    assert st == 200 and _item(doc, "dice-event")["until"] == "2099-01-01"
    st, doc, _ = _req(tsrv, "POST", "/api/today", {"move": {"id": "dice-event", "to": 0}})
    assert doc["items"][0]["id"] == "dice-event"
    st, doc, _ = _req(tsrv, "POST", "/api/today", {"remove": "dice-event"})
    assert st == 200 and all(i["id"] != "dice-event" for i in doc["items"])


def test_route_post_guards(tsrv):
    assert _req(tsrv, "POST", "/api/today", {"tick": "barter-run"}, host="evil.example.com")[0] == 403
    assert _req(tsrv, "POST", "/api/today", {"tick": "barter-run"}, ctype="text/plain")[0] == 415
    assert _req(tsrv, "POST", "/api/today", {"tick": "barter-run"}, ctype=None)[0] == 415
    big = json.dumps({"tick": "barter-run", "pad": "x" * 5000}).encode()
    assert _req(tsrv, "POST", "/api/today", big)[0] == 413
    _, doc, _ = _req(tsrv, "GET", "/api/today")
    assert _item(doc, "barter-run")["done"] is False


@pytest.mark.parametrize("body", [b"not json", b"[]", {}, {"nope": 1},
                                  {"tick": "barter-run", "untick": "barter-run"},
                                  {"tick": "nope"}, {"add": {"title": "x", "kind": "yearly"}},
                                  {"move": {"id": "barter-run", "to": "x"}}])
def test_route_post_bad_body(tsrv, body):
    st, doc, _ = _req(tsrv, "POST", "/api/today", body)
    assert st == 400 and "error" in doc


def test_route_get_foreign_host(tsrv):
    assert _req(tsrv, "GET", "/api/today", host="evil.example.com")[0] == 403


def test_state_reports_today_source(tsrv):
    _, doc, _ = _req(tsrv, "GET", "/api/state")
    assert doc["sources"]["today"]["status"] == "ok"
    assert doc["sources"]["today"]["updated"].endswith("+00:00")
    assert "market" in doc["sources"]


@pytest.mark.parametrize("raw", ["{not json", "[1, 2]", '{"schema": 1, "data": [1]}'])
def test_corrupt_store_file_degrades_and_is_kept(tmp_path, raw):
    from server.ew.store import Store as _Store
    root = tmp_path / "store"
    root.mkdir()
    (root / "today.json").write_text(raw, encoding="utf-8")
    st = _Store(root)
    assert st.get("today") == {}
    kept = list(root.glob("today.json.corrupt-*"))
    assert len(kept) == 1 and kept[0].read_text(encoding="utf-8") == raw
