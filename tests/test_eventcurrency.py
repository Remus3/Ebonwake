"""Plan 095: event currency planner - guaranteed currency by event end vs an
exchange wishlist, shortfall and buy-by warnings. No network."""

import copy
import datetime as dt
import json
from pathlib import Path

import pytest

from server.ew import eventcurrency as ec
from server.ew import maintdigest, whatnow
from server.ew.store import Store

UTC = dt.timezone.utc
SEED = ec.load_rules()
R = SEED["10673"]


def ts(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def at(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


class Clock:
    def __init__(self, t):
        self.t = ts(t)

    def __call__(self):
        return self.t

    def set(self, t):
        self.t = ts(t)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "store")


DATES = {"2026-10-18": 90, "2026-10-19": 30, "2026-10-20": 70}  # one under 60 min
GAME1 = {"game1": ["2026-10-16T12:00:00+00:00"]}


def _svc(store, now, dates=None, rules=None):
    clk = Clock(now)
    hist = {"dates": dict(DATES if dates is None else dates), "marked": []}
    return clk, ec.CurrencyService(store, clock=clk, history=lambda: hist, rules=rules)


# -- 1. seed schema ----------------------------------------------------------------

def test_seed_loads_and_is_sourced():
    assert set(SEED) == {"10673"}
    assert R["unit"] == "seals" and R["verified"] is False
    assert R["url"].endswith("groupContentNo=10673") and R["read"] == "2026-10-09"
    kinds = sorted({s["kind"] for s in R["sources"]})
    assert kinds == sorted(ec.KINDS)
    assert [x["cost"] for x in R["exchange"]] == [80, 80, 80]


def test_seed_file_is_ascii():
    raw = ec.DATA_FILE.read_bytes()
    assert raw.isascii() and b"\r" not in raw


def _bad(mut):
    raw = copy.deepcopy(json.loads(ec.DATA_FILE.read_text(encoding="utf-8"))["events"]["10673"])
    mut(raw)
    with pytest.raises(ValueError):
        ec.validate_rule("10673", raw)


@pytest.mark.parametrize("mut", [
    lambda r: r["sources"][0].update(kind="hourly"),
    lambda r: r["sources"][1].pop("min_minutes"),
    lambda r: r["sources"][0].update(min_minutes=60),
    lambda r: r["sources"][7].update(amount=5),
    lambda r: r["sources"][0].update(amount=-1),
    lambda r: r["sources"][5].update(reset_rule={"every": "week", "weekday": 9}),
    lambda r: r["sources"][0].update(window={"from": "2026-10-01", "to": "2026-11-04"}),
    lambda r: r["sources"][0].update(window={"from": "2026-10-20", "to": "2026-10-19"}),
    lambda r: r["sources"].append(dict(r["sources"][0])),
    lambda r: r["exchange"][0].update(cost=0),
    lambda r: r["exchange"][0].update(limit=0),
    lambda r: r["exchange"][0].update(removed_at="soon"),
    lambda r: r["exchange"].append(dict(r["exchange"][0])),
    lambda r: r.update(ends="2026-10-01T00:00:00+00:00"),
    lambda r: r.pop("unit"),
    lambda r: r.update(title="caf" + chr(233)),
])
def test_validate_rule_rejects(mut):
    _bad(mut)


def test_validate_rule_rejects_bad_number():
    raw = json.loads(ec.DATA_FILE.read_text(encoding="utf-8"))["events"]["10673"]
    with pytest.raises(ValueError):
        ec.validate_rule("x1", raw)


def test_load_rules_bad_file(tmp_path):
    p = tmp_path / "r.json"
    p.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError):
        ec.load_rules(p)


# -- 2. projection on a fixed clock ---------------------------------------------------

def test_projection_mid_event():
    p = ec.project(R, at("2026-10-21T10:00:00Z"), DATES, [], GAME1)
    by = {s["id"]: s for s in p["by_source"]}
    assert by["login"]["earned"] == 60 and by["login"]["remaining"] == 15 * 20
    assert by["play60"]["earned"] == 40 and by["play60"]["remaining"] == 15 * 20
    assert by["game1"]["earned"] == 40 and by["game1"]["remaining"] == 80
    assert by["game2"]["earned"] == 0 and by["game2"]["remaining"] == 120
    assert sum(by[q]["remaining"] for q in ("quest1", "quest2", "quest3")) == 30
    assert p["earned"] == 140 and p["guaranteed"] == 970 and p["earned_src"] == "log"


def test_daily_minutes_counts_only_60_minute_dates():
    p = ec.project(R, at("2026-10-21T10:00:00Z"), {"2026-10-18": 59.9, "2026-10-19": 60}, [], {})
    by = {s["id"]: s for s in p["by_source"]}
    assert by["play60"]["earned"] == 20 and by["login"]["earned"] == 40


def test_today_met_moves_from_remaining_to_earned():
    p = ec.project(R, at("2026-10-21T10:00:00Z"), {"2026-10-21": 61}, [], {})
    by = {s["id"]: s for s in p["by_source"]}
    assert by["play60"]["earned"] == 20 and by["play60"]["remaining"] == 14 * 20
    assert p["play_today"] is None


def test_marked_day_credits_login_only():
    p = ec.project(R, at("2026-10-21T10:00:00Z"), {}, ["2026-10-19"], {})
    by = {s["id"]: s for s in p["by_source"]}
    assert by["login"]["earned"] == 20 and by["play60"]["earned"] == 0


def test_dates_outside_window_do_not_count():
    p = ec.project(R, at("2026-10-21T10:00:00Z"), {"2026-10-08": 120, "2026-10-01": 120}, [], {})
    assert p["earned"] == 0


def test_last_day():
    p = ec.project(R, at("2026-11-04T10:00:00Z"), {}, [], {})
    assert p["earned"] == 0 and p["guaranteed"] == 20 + 20 + 40 + 40 + 30
    assert p["play_today"] == {"minutes": 0, "min_minutes": 60, "amount": 20}


def test_after_end_is_hidden():
    assert ec.project(R, at("2026-11-05T07:00:00Z"), DATES, [], GAME1) is None


def test_before_start_counts_everything_remaining():
    p = ec.project(R, at("2026-10-07T10:00:00Z"), {}, [], {})
    assert p["earned"] == 0
    assert p["guaranteed"] == 27 * 20 * 2 + 4 * 40 * 2 + 30
    assert p["play_today"] is None


def test_weekly_windows_across_thursday_boundary():
    before = ec.project(R, at("2026-10-21T23:59:00Z"), {}, [],
                        {"game1": ["2026-10-21T23:58:00+00:00"]})
    after = ec.project(R, at("2026-10-22T00:00:00Z"), {}, [],
                       {"game1": ["2026-10-21T23:58:00+00:00"]})
    b = {s["id"]: s for s in before["by_source"]}["game1"]
    a = {s["id"]: s for s in after["by_source"]}["game1"]
    assert (b["earned"], b["remaining"]) == (40, 80)  # 10-22, 10-29 still ahead
    assert (a["earned"], a["remaining"]) == (40, 80)  # this period (10-22) now open
    assert ec.project(R, at("2026-10-22T00:00:00Z"), {}, [], {})["by_source"][5]["remaining"] == 80


def test_missed_week_is_lost():
    p = ec.project(R, at("2026-10-23T10:00:00Z"), {}, [], {})
    g = {s["id"]: s for s in p["by_source"]}["game2"]
    assert (g["earned"], g["remaining"]) == (0, 80)  # 10-08 and 10-15 weeks gone


def test_once_rows_counted_once():
    p = ec.project(R, at("2026-10-21T10:00:00Z"), {}, [],
                   {"quest1": ["2026-10-10T00:00:00+00:00", "2026-10-11T00:00:00+00:00"]})
    q = {s["id"]: s for s in p["by_source"]}["quest1"]
    assert (q["earned"], q["remaining"]) == (10, 0)


def test_drops_never_projected():
    p = ec.project(R, at("2026-10-21T10:00:00Z"), DATES, [], {"drops": ["2026-10-20T00:00:00+00:00"]})
    d = {s["id"]: s for s in p["by_source"]}["drops"]
    assert (d["earned"], d["remaining"]) == (0, 0)
    assert p["earned"] == 100 and p["guaranteed"] == 970


def test_balance_override_replaces_earned_and_expires_at_end():
    bal = {"value": 300, "set_at": "2026-10-21T09:00:00+00:00",
           "expires_at": "2026-11-05T07:00:00+00:00"}
    p = ec.project(R, at("2026-10-21T10:00:00Z"), DATES, [], GAME1, balance=bal)
    assert p["earned"] == 300 and p["earned_src"] == "typed" and p["guaranteed"] == 300 + 830
    assert ec.active_balance(bal, at("2026-11-05T06:59:00Z")) == 300
    assert ec.active_balance(bal, at("2026-11-05T07:00:00Z")) is None
    assert ec.active_balance({"value": "x"}, at("2026-10-21T00:00:00Z")) is None


# -- 3. wishlist ------------------------------------------------------------------------

def test_wishlist_limit_enforced(store):
    _, s = _svc(store, "2026-10-21T10:00:00Z")
    s.wish({"event": "10673", "item": "Mythical Censer", "qty": 2})
    for bad in ({"event": "10673", "item": "Mythical Censer", "qty": 3},
                {"event": "10673", "item": "Valks' Cry (+250)", "qty": 2},
                {"event": "10673", "item": "Nope", "qty": 1},
                {"event": "99999", "item": "Mythical Censer", "qty": 1},
                {"event": "10673", "item": "Mythical Censer", "qty": -1},
                {"event": "10673", "item": "Mythical Censer", "qty": True},
                {"event": "10673", "item": "Mythical Censer"}):
        with pytest.raises(ValueError):
            s.wish(bad)
    s.wish({"event": "10673", "item": "Mythical Censer", "qty": 0})
    assert store.get(ec.DOMAIN)["items"] == []


def test_removed_at_warning_and_digest_line(store, tmp_path):
    rules = copy.deepcopy(SEED)
    rules["10673"]["exchange"].append({"item": "Seal buff A", "cost": 10, "limit": 1,
                                       "removed_at": "2026-11-12T07:00:00+00:00"})
    rules["10673"]["exchange"].append({"item": "Seal buff B", "cost": 10, "limit": 1,
                                       "removed_at": "2026-11-01T00:00:00+00:00"})
    clk, s = _svc(store, "2026-10-21T10:00:00Z", rules=rules)
    s.wish({"event": "10673", "item": "Seal buff A", "qty": 1})
    s.wish({"event": "10673", "item": "Seal buff B", "qty": 1})
    row = s.view()["events"][0]
    use = {w["item"]: w["use_before"] for w in row["wishlist"]}
    assert use == {"Seal buff A": "use before 11-12", "Seal buff B": "use before 11-01"}
    loss = s.loss_rows()
    assert [(r["notice_no"], r["due_utc"]) for r in loss] == [
        (10673, "2026-11-01T00:00:00+00:00"), (10673, "2026-11-12T07:00:00+00:00")]
    assert all(t.isascii() and len(t) <= maintdigest.MAX_TEXT for r in loss for t in r["loss"])
    # plan 074 digest: the 11-12 line is due at that maintenance (shown from T-24 h)
    start = at("2026-11-12T07:00:00Z")
    w = maintdigest.warnings(loss, at("2026-11-11T08:00:00Z"), start)
    assert [x["text"] for x in w] == [loss[1]["loss"][0]]
    clk.set("2026-11-13T00:00:00Z")
    assert s.loss_rows() == []  # past removals no longer warn


# -- Events row (acceptance) ---------------------------------------------------------------

def test_acceptance_events_row(store):
    _, s = _svc(store, "2026-10-21T10:00:00Z")
    s.tick_at("10673:game1", ts("2026-10-16T12:00:00Z"))
    s.wish({"event": "10673", "item": "Valks' Cry (+250)", "qty": 1})
    s.wish({"event": "10673", "item": "Mythical Censer", "qty": 2})
    row = s.view()["events"][0]
    assert (row["event"], row["currency"], row["earned"], row["guaranteed"]) == (
        "10673", "Seals of Purification", 140, 970)
    assert (row["wishlist_cost"], row["shortfall"], row["ends"]) == (
        240, 0, "2026-11-05T07:00:00+00:00")
    assert row["label"] == "Seals: 140 earned, 970 guaranteed by 11-05, wishlist 240 - covered"


def test_short_row_label_and_whatnow(store):
    _, s = _svc(store, "2026-11-04T10:00:00Z", dates={"2026-11-04": 30})
    s.wish({"event": "10673", "item": "Valks' Cry (+250)", "qty": 1})
    s.wish({"event": "10673", "item": "Mythical Censer", "qty": 2})
    row = s.view()["events"][0]
    assert row["earned"] == 20 and row["guaranteed"] == 150 and row["shortfall"] == 90
    assert row["label"] == ("Seals: 20 earned, 150 guaranteed by 11-05, wishlist 240"
                            " - 90 short - from drops")
    now = at("2026-11-04T10:00:00Z")
    c = whatnow.event_currency(s.view(), now)
    assert [x["text"] for x in c] == ["Play 60 min today (+20 seals)"]
    assert c[0]["source"] == "event" and c[0]["due"] == "2026-11-05T00:00:00+00:00"
    ranked = whatnow.collect({"currency": s.view}, now, whatnow.load_weights())
    assert ranked["top"]["text"] == "Play 60 min today (+20 seals)"


def test_no_whatnow_when_covered_or_met(store):
    _, s = _svc(store, "2026-10-21T10:00:00Z")
    s.wish({"event": "10673", "item": "Valks' Cry (+250)", "qty": 1})
    assert whatnow.event_currency(s.view(), at("2026-10-21T10:00:00Z")) == []
    _, s2 = _svc(store, "2026-11-04T10:00:00Z", dates={"2026-11-04": 61})
    s2.wish({"event": "10673", "item": "Mythical Censer", "qty": 2})
    assert s2.view()["events"][0]["shortfall"] > 0
    assert whatnow.event_currency(s2.view(), at("2026-11-04T10:00:00Z")) == []


def test_label_without_wishlist_and_typed_balance(store):
    _, s = _svc(store, "2026-10-21T10:00:00Z")
    assert s.view()["events"][0]["label"] == "Seals: 100 earned, 970 guaranteed by 11-05"
    s.balance({"event": "10673", "value": 300})
    row = s.view()["events"][0]
    assert row["label"] == "Seals: 300 in hand (typed), 1170 guaranteed by 11-05"
    ov = s.typed_overrides()
    assert ov[0]["key"] == "events.currency.10673.balance" and ov[0]["value"] == 300
    assert ov[0]["expires_at"] == "2026-11-05T07:00:00+00:00"
    s.balance({"event": "10673", "value": None})
    assert s.typed_overrides() == []
    for bad in ({"event": "10673", "value": -1}, {"event": "10673", "value": 1.5},
                {"event": "1", "value": 1}, {"value": 3}):
        with pytest.raises(ValueError):
            s.balance(bad)


def test_view_hidden_after_end(store):
    clk, s = _svc(store, "2026-10-21T10:00:00Z")
    s.balance({"event": "10673", "value": 300})
    clk.set("2026-11-05T07:00:00Z")
    assert s.view() == {"events": []}
    assert s.typed_overrides() == []


def test_api_shape_keys(store):
    _, s = _svc(store, "2026-10-21T10:00:00Z")
    row = s.view()["events"][0]
    assert {"event", "currency", "earned", "guaranteed", "wishlist_cost", "shortfall",
            "ends"} <= set(row)


# -- weekly / once rows in the Today checklist ------------------------------------------------

def test_today_rows_only_inside_window(store):
    clk, s = _svc(store, "2026-10-21T10:00:00Z")
    s.tick("10673:game1")
    rows = s.today_rows()
    assert [r["key"] for r in rows] == ["10673:quest1", "10673:quest2", "10673:quest3",
                                        "10673:game1", "10673:game2"]
    done = {r["key"]: r["done"] for r in rows}
    assert done["10673:game1"] is True and done["10673:game2"] is False
    g = [r for r in rows if r["key"] == "10673:game1"][0]
    assert g["next_reset"] == "2026-10-22T00:00:00+00:00" and g["amount"] == 40
    clk.set("2026-10-22T00:00:00Z")
    assert {r["key"]: r["done"] for r in s.today_rows()}["10673:game1"] is False
    clk.set("2026-11-05T00:00:00Z")  # weekly window ended 11-04; once rows until the end
    assert [r["key"] for r in s.today_rows()] == ["10673:quest1", "10673:quest2", "10673:quest3"]
    clk.set("2026-11-05T07:00:00Z")
    assert s.today_rows() == []
    clk.set("2026-10-08T09:00:00Z")  # before the event start
    assert s.today_rows() == []


def test_tick_idempotent_and_untick(store):
    _, s = _svc(store, "2026-10-21T10:00:00Z")
    s.tick("10673:quest1")
    s.tick("10673:quest1")
    s.tick("10673:game2")
    s.tick("10673:game2")
    t = store.get(ec.TICKS_DOMAIN)["ticks"]["10673"]
    assert len(t["quest1"]) == 1 and len(t["game2"]) == 1
    s.untick("10673:game2")
    assert store.get(ec.TICKS_DOMAIN)["ticks"]["10673"].get("game2", []) == []
    for bad in ("10673:login", "10673:drops", "10673:nope", "1:game1", 7, "10673"):
        with pytest.raises(ValueError):
            s.tick(bad)


def test_tick_refused_outside_window(store):
    _, s = _svc(store, "2026-11-05T01:00:00Z")
    with pytest.raises(ValueError):
        s.tick("10673:game1")


# -- route -----------------------------------------------------------------------------------

def test_routes(tmp_path):
    import http.client
    import threading

    from server.ew import app as ewapp
    clk = Clock("2026-10-21T10:00:00Z")
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          sse_interval=0.05, market_seed=[], events_clock=clk, today_clock=clk)
    s.store.put("logindays", {"dates": DATES, "marked": [], "open": None, "seen": None,
                              "since": "2026-10-01", "backfilled": True})
    t = threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    t.start()

    def req(method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=5)
        c.request(method, path, body=None if body is None else json.dumps(body).encode(),
                  headers={"Content-Type": "application/json"})
        r = c.getresponse()
        out = (r.status, json.loads(r.read()))
        c.close()
        return out

    try:
        st, doc = req("GET", "/api/events/currency")
        assert st == 200 and doc["events"][0]["earned"] == 100
        st, doc = req("POST", "/api/today", {"event_tick": "10673:game1"})
        assert st == 200 and {r["key"]: r["done"] for r in doc["event_rows"]}["10673:game1"]
        st, doc = req("POST", "/api/today", {"event_tick": "10673:login"})
        assert st == 400
        st, doc = req("POST", "/api/events", {"wish": {"event": "10673",
                                                       "item": "Mythical Censer", "qty": 2}})
        assert st == 200 and doc["currency"]["events"][0]["wishlist_cost"] == 160
        st, doc = req("POST", "/api/events", {"wish": {"event": "10673",
                                                       "item": "Mythical Censer", "qty": 3}})
        assert st == 400
        st, doc = req("POST", "/api/events", {"currency_balance": {"event": "10673", "value": 50}})
        assert st == 200 and doc["currency"]["events"][0]["earned"] == 50
        st, doc = req("GET", "/api/overrides")
        assert "events.currency.10673.balance" in [i["key"] for i in doc["items"]]
        st, doc = req("GET", "/api/events")
        assert st == 200 and doc["currency"]["events"][0]["label"].startswith("Seals: 50 in hand")
        st, doc = req("GET", "/api/today")
        assert [r["key"] for r in doc["event_rows"]][-1] == "10673:game2"
        assert "currency" not in s.whatnow.view()["errors"]
    finally:
        s.shutdown()
        s.server_close()


def test_module_has_no_network_imports():
    src = Path(ec.__file__).read_text(encoding="utf-8")
    for bad in ("urllib", "http.client", "socket", "httpcache"):
        assert bad not in src
