"""Plan 060: Combat Secret Book ledger (books per activity, observed XP, books-to-level)."""

import copy
import datetime as dt
import http.client
import json
import threading

import pytest

from server.ew import app as ewapp
from server.ew import leveling, weekly, xpbooks
from server.ew.store import Store

UTC = dt.timezone.utc
T0 = dt.datetime(2026, 10, 16, 12, 0, 0, tzinfo=UTC)  # a Friday, after the books go live


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
def store(tmp_path):
    return Store(tmp_path / "store")


@pytest.fixture()
def data():
    return xpbooks.load_books()


def _counts(done=0, per_week=5):
    return lambda: {"black-shrine": {"done": done, "per_week": per_week}}


@pytest.fixture()
def books(store, clock):
    return xpbooks.XpBooksService(store, clock=clock, weekly=_counts())


# --- data schema ---------------------------------------------------------------

def test_data_file_schema(data):
    assert set(data["sizes"]) == set(xpbooks.SIZES)
    for sid, row in data["sizes"].items():
        assert row["id"] == sid and row["min_level"] == 60 and row["verified"] is False
        assert row["source"].startswith("https://www.blackdesertfoundry.com/")
    assert data["sizes"]["small"]["pct_at"] == {66: 0.2}
    assert data["sizes"]["medium"]["pct_at"] == {66: 1.0}
    assert data["sizes"]["large"]["pct_at"] == {66: 7.5}
    assert data["sizes"]["xl"]["pct_at"] == {66: 15.0}
    src = data["sources"]
    assert src["black-shrine"]["weekly_row"] == "black-shrine"
    assert src["black-shrine"]["win"] == {"size": "small", "n": 1}
    assert src["node-war"]["loss"] == {"size": "small", "n": 2}
    assert src["war-of-roses"]["win"] == {"size": "xl", "n": 1}
    assert data["read"] == "2026-10-06"


def test_data_file_ascii_and_weekly_rows_exist():
    raw = xpbooks.DATA_FILE.read_bytes()
    raw.decode("ascii")
    ids = {r["id"] for r in weekly.load_content()}
    for s in xpbooks.load_books()["sources"].values():
        assert s["weekly_row"] is None or s["weekly_row"] in ids


def _raw():
    return json.loads(xpbooks.DATA_FILE.read_text(encoding="ascii"))


@pytest.mark.parametrize("mutate", [
    lambda d: d["sizes"].pop(),
    lambda d: d["sizes"][0].update(id="huge"),
    lambda d: d["sizes"][0].update(pct_at={}),
    lambda d: d["sizes"][0].update(pct_at={"66": -1}),
    lambda d: d["sizes"][0].update(pct_at={"x": 1}),
    lambda d: d["sizes"][0].update(min_level=0),
    lambda d: d["sizes"][0].update(verified="no"),
    lambda d: d["sizes"][0].update(extra=1),
    lambda d: d["sources"][0].update(win={"size": "huge", "n": 1}),
    lambda d: d["sources"][0].update(win={"size": "small", "n": 0}),
    lambda d: d["sources"][0].update(loss={"size": "small"}),
    lambda d: d["sources"][0].update(activity="Bad Id"),
    lambda d: d["sources"].append(copy.deepcopy(d["sources"][0])),
    lambda d: d.update(read="yesterday"),
])
def test_data_validation_rejects(mutate):
    d = _raw()
    mutate(d)
    with pytest.raises(ValueError):
        xpbooks.validate_books(d)


def test_bad_data_file_degrades(store, clock, tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{", encoding="ascii")
    svc = xpbooks.XpBooksService(store, clock=clock, data_file=p)
    v = svc.view(66, 40, None)
    assert v["error"] and v["available"] is False


# --- pure math -----------------------------------------------------------------

def _use(size, level, before, after):
    return {"at": T0.isoformat(), "size": size, "level": level, "pct_before": before,
            "pct_after": after}


def test_observed_pct_median_same_size_and_level():
    used = [_use("large", 66, 10, 18), _use("large", 66, 20, 27), _use("large", 66, 30, 37.5),
            _use("large", 67, 0, 5), _use("small", 66, 1, 1.3)]
    assert xpbooks.observed_pct(used, "large", 66) == pytest.approx(7.5)
    assert xpbooks.observed_pct(used, "large", 67) == pytest.approx(5)
    assert xpbooks.observed_pct(used, "large", 68) is None
    assert xpbooks.observed_pct(used, "xl", 66) is None


def test_observed_gain_wraps_a_level_up():
    assert xpbooks.gain(95, 10) == pytest.approx(15)
    assert xpbooks.gain(10, 17.5) == pytest.approx(7.5)


def test_book_pct_fallback_flag_and_nearest_level(data):
    p = xpbooks.book_pct(data, [], "large", 66)
    assert p == {"pct": 7.5, "observed": False, "uses": 0, "flag": "Lv 66 value, verify"}
    # nearest tracked level is used at any level (only Lv 66 is published)
    assert xpbooks.book_pct(data, [], "xl", 72)["pct"] == 15.0
    two = dict(data, sizes=dict(data["sizes"], small=dict(data["sizes"]["small"],
                                                          pct_at={60: 0.5, 66: 0.2})))
    assert xpbooks.book_pct(two, [], "small", 62) == {
        "pct": 0.5, "observed": False, "uses": 0, "flag": "Lv 60 value, verify"}
    # an equal distance picks the lower level (deterministic)
    assert xpbooks.book_pct(two, [], "small", 63)["pct"] == 0.5


def test_book_pct_observed_overrides(data):
    used = [_use("large", 66, 40, 46)]
    assert xpbooks.book_pct(data, used, "large", 66) == {
        "pct": 6.0, "observed": True, "uses": 1, "flag": None}


def test_acceptance_lv66_40pct_three_large(data):
    owned = {"small": 0, "medium": 0, "large": 3, "xl": 0}
    assert xpbooks.pct_from_owned(data, [], owned, 66) == pytest.approx(22.5)
    nxt = xpbooks.books_to_next(data, [], 66, 40)
    assert nxt["large"] == 8
    assert nxt == {"small": 300, "medium": 60, "large": 8, "xl": 4}


def test_books_to_next_rounds_up_and_handles_full(data):
    assert xpbooks.books_to_next(data, [], 66, 41)["large"] == 8  # 59 / 7.5 = 7.87
    assert xpbooks.books_to_next(data, [], 66, 100)["large"] == 0
    assert xpbooks.books_to_next(data, [], 66, None) is None


def test_weekly_expectation_from_033_ticks(data):
    counts = {"black-shrine": {"done": 2, "per_week": 5}}
    w = xpbooks.weekly_expectation(data, [], 66, counts, [], T0)
    row = w["rows"][0]
    assert row == {"activity": "black-shrine", "name": "Black Shrine", "size": "small",
                   "expected": 5, "done": 2, "pct_week": 1.0}
    assert w["pct_week"] == pytest.approx(1.0)


def test_weekly_expectation_logged_activities_trailing_week(data):
    old = (T0 - dt.timedelta(days=8)).isoformat()
    recent = (T0 - dt.timedelta(days=2)).isoformat()
    added = [{"at": recent, "size": "medium", "n": 1, "activity": "guild-boss"},
             {"at": recent, "size": "medium", "n": 2, "activity": "node-war"},
             {"at": old, "size": "xl", "n": 1, "activity": "war-of-roses"},
             {"at": recent, "size": "large", "n": 1, "activity": None},
             {"at": recent, "size": "small", "n": 1, "activity": "black-shrine"}]
    w = xpbooks.weekly_expectation(data, [], 66, {}, added, T0)
    assert w["rows"] == []
    assert w["logged"] == [{"activity": "guild-boss", "name": "Guild Boss", "size": "medium",
                            "n": 1, "pct_week": 1.0},
                           {"activity": "node-war", "name": "Node War", "size": "medium",
                            "n": 2, "pct_week": 2.0}]
    assert w["pct_week"] == pytest.approx(3.0)


def test_weekly_logged_nets_negative_corrections(data):
    recent = (T0 - dt.timedelta(days=1)).isoformat()
    added = [{"at": recent, "size": "medium", "n": 5, "activity": "node-war"},
             {"at": recent, "size": "medium", "n": -3, "activity": "node-war"},
             {"at": recent, "size": "medium", "n": 1, "activity": "guild-boss"},
             {"at": recent, "size": "medium", "n": -1, "activity": "guild-boss"}]
    w = xpbooks.weekly_expectation(data, [], 66, {}, added, T0)
    assert [(r["activity"], r["n"]) for r in w["logged"]] == [("node-war", 2)]
    assert w["pct_week"] == pytest.approx(2.0)


# --- service -------------------------------------------------------------------

def test_view_hidden_below_60_with_olvia_deadline(books):
    dl = [{"id": "olvia-class-3", "label": "Olvia Academy", "needs_level": 60,
           "enrol_by_utc": "2026-11-05T00:00:00+00:00", "state": "on_track"}]
    v = books.view(58, 20, 2.0, deadlines=dl)
    assert v["available"] is False and v["note"] == "books from Lv 60"
    assert v["deadline"] == {"label": "Olvia Academy", "needs_level": 60,
                             "enrol_by_utc": "2026-11-05T00:00:00+00:00"}
    assert "owned_pct" not in v
    v = books.view(None, None, None)
    assert v["available"] is False and v["deadline"] is None


def test_view_acceptance_fixture(books):
    books.add({"size": "large", "n": 3})
    v = books.view(66, 40, None)
    assert v["available"] is True
    assert v["owned"] == {"small": 0, "medium": 0, "large": 3, "xl": 0}
    assert v["owned_pct"] == pytest.approx(22.5)
    assert v["to_next"]["large"] == 8
    assert v["per_book"]["large"]["flag"] == "Lv 66 value, verify"
    assert v["flagged"] is True
    # Black Shrine 5 runs a week x Small 0.2 % = 1 %/week
    assert v["pct_week"] == pytest.approx(1.0)
    assert v["books_pct_h"] == pytest.approx(1.0 / 168, abs=1e-6)
    # no grind rate: the books-only rate still gives an ETA
    assert v["eta_next_with_books_s"] == int(60 / (1.0 / 168) * 3600)


def test_view_rate_with_books(books):
    v = books.view(66, 40, 2.0)
    assert v["rate_with_books_pct_h"] == pytest.approx(2.0 + 1.0 / 168, abs=1e-3)
    assert v["eta_next_with_books_s"] == leveling.eta_next_s(40, 2.0 + 1.0 / 168)


def test_use_overrides_fallback_and_decrements(books, clock):
    books.add({"size": "large", "n": 2, "activity": "conquest-war"})
    books.use({"size": "large", "pct_before": 40, "pct_after": 46.5}, level=66)
    v = books.view(66, 46.5, None)
    assert v["owned"]["large"] == 1
    assert v["per_book"]["large"] == {"pct": 6.5, "observed": True, "uses": 1, "flag": None}
    assert v["owned_pct"] == pytest.approx(6.5)
    assert v["to_next"]["large"] == 9  # 53.5 / 6.5 = 8.2
    assert v["used"][0]["index"] == 0 and v["used"][0]["gain"] == pytest.approx(6.5)
    # delete the use: the book comes back and the fallback returns
    books.delete(0)
    v = books.view(66, 46.5, None)
    assert v["owned"]["large"] == 2 and v["per_book"]["large"]["observed"] is False


def test_use_without_owned_does_not_go_negative(books):
    books.use({"size": "small", "pct_before": 10, "pct_after": 10.2}, level=66)
    v = books.view(66, 10.2, None)
    assert v["owned"]["small"] == 0 and len(v["used"]) == 1
    books.delete(0)
    assert books.view(66, 10.2, None)["owned"]["small"] == 0


def test_add_negative_corrects_but_not_below_zero(books):
    books.add({"size": "medium", "n": 3})
    books.add({"size": "medium", "n": -2})
    assert books.view(66, 0, None)["owned"]["medium"] == 1
    with pytest.raises(ValueError):
        books.add({"size": "medium", "n": -2})


@pytest.mark.parametrize("arg", [
    None, {}, {"size": "huge", "n": 1}, {"size": "small"}, {"size": "small", "n": 0},
    {"size": "small", "n": 100}, {"size": "small", "n": 1.0}, {"size": "small", "n": True},
    {"size": "small", "n": 1, "activity": "nope"}, {"size": "small", "n": 1, "x": 1},
])
def test_add_bad(books, arg):
    with pytest.raises(ValueError):
        books.add(arg)


@pytest.mark.parametrize("arg,level", [
    ({"size": "small", "pct_before": 1, "pct_after": 1}, 66),
    ({"size": "small", "pct_before": -1, "pct_after": 1}, 66),
    ({"size": "small", "pct_before": 1, "pct_after": 100.5}, 66),
    ({"size": "small", "pct_before": 1, "pct_after": 1.2345}, 66),
    ({"size": "huge", "pct_before": 1, "pct_after": 2}, 66),
    ({"size": "small", "pct_before": 1}, 66),
    ({"size": "small", "pct_before": 1, "pct_after": 2}, 59),
    ({"size": "small", "pct_before": 1, "pct_after": 2}, None),
])
def test_use_bad(books, arg, level):
    with pytest.raises(ValueError):
        books.use(arg, level=level)


@pytest.mark.parametrize("idx", [-1, 0, 1.0, True, "0", None])
def test_delete_bad(books, idx):
    with pytest.raises(ValueError):
        books.delete(idx)


def test_corrupt_ledger_degrades(store, books):
    store.put("xpbooks", {"owned": {"small": -3, "large": "x", "xl": 2, "huge": 5},
                          "used": [{"at": "bad"}, _use("large", 66, 10, 17), 5],
                          "added": "nope"})
    v = books.view(66, 10, None)
    assert v["owned"] == {"small": 0, "medium": 0, "large": 0, "xl": 2}
    assert len(v["used"]) == 1


# --- weekly counts + leveling wiring --------------------------------------------

def test_weekly_counts(store, clock):
    w = weekly.WeeklyService(store, clock=clock)
    w.tick("black-shrine")
    w.tick("black-shrine")
    c = w.counts()
    assert c["black-shrine"] == {"done": 2, "per_week": 5}


def test_leveling_view_carries_books_and_ops(store, clock):
    books = xpbooks.XpBooksService(store, clock=clock, weekly=_counts())
    svc = leveling.LevelingService(store, clock=clock, books=books)
    v = svc.view()
    assert v["books"]["available"] is False
    svc.sample({"level": 66, "pct": 40})
    v = svc.book_add({"size": "large", "n": 3, "activity": "conquest-war"})
    assert v["books"]["owned_pct"] == pytest.approx(22.5)
    seq = svc.seq
    v = svc.book_use({"size": "large", "pct_before": 40, "pct_after": 47.5})
    assert svc.seq == seq + 1 and v["books"]["owned"]["large"] == 2
    v = svc.book_del(0)
    assert v["books"]["owned"]["large"] == 3


def test_leveling_without_books_service(store, clock):
    svc = leveling.LevelingService(store, clock=clock)
    assert svc.view()["books"] is None
    with pytest.raises(ValueError):
        svc.book_add({"size": "small", "n": 1})


def test_book_use_needs_typed_pct(store, clock):
    books = xpbooks.XpBooksService(store, clock=clock, weekly=_counts())
    svc = leveling.LevelingService(store, clock=clock, books=books)
    with pytest.raises(ValueError):
        svc.book_use({"size": "large", "pct_before": 40, "pct_after": 47.5})


# --- routes --------------------------------------------------------------------

@pytest.fixture()
def lsrv(tmp_path, clock):
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          sse_interval=0.05, market_seed=[], grind_clock=clock,
                          leveling_clock=clock, today_clock=clock, profile_cfg={})
    t = threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    t.start()
    yield s
    s.shutdown()
    s.server_close()


def _req(s, method, path, body=None):
    c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=5)
    data = None if body is None else json.dumps(body).encode()
    c.request(method, path, body=data, headers={"Content-Type": "application/json"})
    r = c.getresponse()
    out = r.read()
    c.close()
    return r.status, (json.loads(out) if out else None)


def test_route_book_ops(lsrv):
    st, doc = _req(lsrv, "GET", "/api/leveling")
    assert st == 200 and doc["books"]["available"] is False
    assert _req(lsrv, "POST", "/api/leveling", {"sample": {"level": 66, "pct": 40}})[0] == 200
    st, doc = _req(lsrv, "POST", "/api/leveling", {"book_add": {"size": "large", "n": 3}})
    assert st == 200 and doc["books"]["owned_pct"] == pytest.approx(22.5)
    assert doc["books"]["to_next"]["large"] == 8
    st, doc = _req(lsrv, "POST", "/api/today", {"weekly_tick": "black-shrine"})
    assert st == 200
    st, doc = _req(lsrv, "GET", "/api/leveling")
    assert doc["books"]["weekly"]["rows"][0]["done"] == 1
    st, doc = _req(lsrv, "POST", "/api/leveling",
                   {"book_use": {"size": "large", "pct_before": 40, "pct_after": 47}})
    assert st == 200 and doc["books"]["per_book"]["large"]["observed"] is True
    st, doc = _req(lsrv, "POST", "/api/leveling", {"book_del": 0})
    assert st == 200 and doc["books"]["owned"]["large"] == 3
    st, doc = _req(lsrv, "POST", "/api/leveling", {"book_add": {"size": "huge", "n": 1}})
    assert st == 400
