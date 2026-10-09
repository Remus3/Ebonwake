"""Plan 028: market item-name index (seed + util/db + cached /item and /hot).

No network: every UtilDb gets an injected fake fetch.
"""

import http.client
import json
import threading
import urllib.parse

import pytest

from server.ew import app as ewapp
from server.ew import itemnames, market
from server.ew.store import atomic_write_json

SEED = [{"id": 16080, "sid": 0, "name": "Cron Stone"},
        {"id": 820979, "sid": 0, "name": "Essence of Dawn"},
        {"id": 721003, "sid": 0, "name": "Caphras Stone"}]
E_ACUTE = chr(0xE9)


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class DbFetch:
    """Fake arsha /util/db: names from `db`, or raise `err`."""

    def __init__(self, db=None, err=None):
        self.db = dict(db or {})
        self.err = err
        self.calls = []

    def __call__(self, url, timeout):
        self.calls.append(url)
        if self.err is not None:
            raise self.err
        assert url.startswith(itemnames.UTIL_DB_URL + "?")
        q = urllib.parse.parse_qs(url.split("?", 1)[1])
        ids = [int(i) for i in q["id"]]
        assert q["lang"] == ["en"] and len(ids) <= itemnames.UTIL_DB_MAX_IDS
        body = [{"id": i, "name": self.db[i], "grade": 1} for i in ids if i in self.db]
        return json.dumps(body[0] if len(body) == 1 else body).encode()


def _index(tmp_path, fetch=None, clock=None, seed=SEED):
    clock = clock or Clock()
    db = itemnames.UtilDb(fetch=fetch or DbFetch(err=OSError("offline")), clock=clock,
                          cache_dir=tmp_path / "cache" / "utildb")
    return itemnames.NameIndex(seed=seed, market_cache_dir=tmp_path / "cache",
                               index_path=tmp_path / "market_names.json", utildb=db,
                               clock=clock)


def _cache(tmp_path, key, data, t=1_000_000.0):
    atomic_write_json(tmp_path / "cache" / f"{key}.json", {"fetched_at": t, "data": data})


def _names(rows):
    return [r["name"] for r in rows]


# --- seed ------------------------------------------------------------------

def test_tracked_seed_has_verified_staples():
    seed = {s["id"]: s for s in itemnames.load_seed()}
    assert seed[16080]["name"] == "Cron Stone"
    assert seed[820979]["name"] == "Essence of Dawn"
    assert seed[721003]["name"] == "Caphras Stone"
    raw = json.loads(itemnames.SEED_PATH.read_text(encoding="utf-8"))
    for it in raw["items"]:
        assert set(it) >= {"id", "sid", "name", "source", "verified"}


def test_seed_loader_drops_bad_rows(tmp_path):
    p = tmp_path / "seed.json"
    p.write_text(json.dumps({"items": [
        {"id": 1, "sid": 0, "name": "Ok"}, {"id": "2", "sid": 0, "name": "Bad id"},
        {"id": 3, "sid": 0, "name": ""}, {"id": True, "sid": 0, "name": "Bool"},
        {"id": 4, "name": "No sid"}, "junk"]}), encoding="utf-8")
    assert itemnames.load_seed(p) == [{"id": 1, "sid": 0, "name": "Ok"},
                                      {"id": 4, "sid": 0, "name": "No sid"}]
    assert itemnames.load_seed(tmp_path / "missing.json") == []


# --- validation --------------------------------------------------------------

@pytest.mark.parametrize("q", [None, "", "a", " a ", "x" * 41, "caf" + E_ACUTE, "ab\tc",
                               "ab" + chr(0x7F), 12])
def test_bad_queries_raise(tmp_path, q):
    with pytest.raises(ValueError):
        _index(tmp_path).search(q)


@pytest.mark.parametrize("q", ["cr", "x" * 40, " cron ", "Stone (Armor)!"])
def test_good_queries_pass(tmp_path, q):
    assert isinstance(_index(tmp_path).search(q), list)


# --- ranking -----------------------------------------------------------------

def test_prefix_before_substring_case_insensitive(tmp_path):
    seed = SEED + [{"id": 5, "sid": 0, "name": "Stone Tablet"},
                   {"id": 6, "sid": 0, "name": "stonewall"}]
    rows = _index(tmp_path, seed=seed).search("STONE")
    assert _names(rows) == ["Stone Tablet", "stonewall", "Caphras Stone", "Cron Stone"]
    assert rows[0] == {"id": 5, "sid": 0, "name": "Stone Tablet"}


def test_top_20_cap(tmp_path):
    seed = [{"id": i, "sid": 0, "name": f"Shard {i:03d}"} for i in range(1, 40)]
    rows = _index(tmp_path, seed=seed).search("shard")
    assert len(rows) == itemnames.MAX_RESULTS == 20
    assert rows[0]["name"] == "Shard 001"


def test_exact_id_query_ranks_first(tmp_path):
    rows = _index(tmp_path).search("16080")
    assert rows[0] == {"id": 16080, "sid": 0, "name": "Cron Stone"}


def test_no_match_is_empty(tmp_path):
    assert _index(tmp_path).search("zzz") == []


# --- sources + precedence ----------------------------------------------------

def test_offline_arsha_seed_only(tmp_path):
    f = DbFetch(err=OSError("offline"))
    idx = _index(tmp_path, fetch=f)
    assert _names(idx.search("cron")) == ["Cron Stone"]
    assert idx.search("4901") == []  # numeric query tried util/db, which is down
    assert len(f.calls) == 1
    idx.search("4901")  # backoff: no second call
    assert len(f.calls) == 1


def test_cached_item_and_hot_names_are_indexed(tmp_path):
    _cache(tmp_path, "sublist_4901_0", {"id": 4901, "sid": 0, "name": "Black Stone"})
    _cache(tmp_path, "hot_0_0", [{"id": 11103, "sid": 3, "name": "Kzarka Longbow"},
                                 {"id": "bad", "name": "x"}, "junk"])
    _cache(tmp_path, "history_4901_0", {"id": 4901, "sid": 0, "name": "Not a name source"})
    atomic_write_json(tmp_path / "cache" / "sublist_1_0.json", {"broken": True})
    idx = _index(tmp_path)
    assert idx.search("black") == [{"id": 4901, "sid": 0, "name": "Black Stone"}]
    assert idx.search("kzarka") == [{"id": 11103, "sid": 3, "name": "Kzarka Longbow"}]
    assert idx.search("not a name") == []


def test_mocked_util_db_names_unknown_id(tmp_path):
    f = DbFetch({4901: "Black Stone"})
    idx = _index(tmp_path, fetch=f)
    assert idx.search("4901") == [{"id": 4901, "sid": 0, "name": "Black Stone"}]
    assert idx.search("black") == [{"id": 4901, "sid": 0, "name": "Black Stone"}]
    assert len(f.calls) == 1


def test_util_db_cached_7_days_and_persisted(tmp_path):
    clk = Clock()
    f = DbFetch({4901: "Black Stone"})
    _index(tmp_path, fetch=f, clock=clk).search("4901")
    # New index (restart), network off: the persisted util/db name survives.
    off = DbFetch(err=OSError("offline"))
    idx = _index(tmp_path, fetch=off, clock=clk)
    assert _names(idx.search("black")) == ["Black Stone"]
    idx.search("4901")
    assert off.calls == []  # still fresh
    clk.t += itemnames.UTIL_DB_TTL_S + 1
    assert _names(idx.search("4901")) == ["Black Stone"]  # stale name kept on failure
    assert len(off.calls) == 1


def test_unknown_id_miss_is_remembered(tmp_path):
    f = DbFetch({})
    idx = _index(tmp_path, fetch=f)
    assert idx.search("123456") == []
    assert idx.search("123456") == []
    assert len(f.calls) == 1


def test_precedence_seed_over_utildb_over_cache(tmp_path):
    _cache(tmp_path, "sublist_16080_0", {"id": 16080, "sid": 0, "name": "Cache Cron"})
    _cache(tmp_path, "sublist_4901_0", {"id": 4901, "sid": 0, "name": "Cache Black"})
    f = DbFetch({16080: "Db Cron", 4901: "Db Black"})
    idx = _index(tmp_path, fetch=f)
    idx.search("16080")
    idx.search("4901")
    assert idx.search("16080")[0]["name"] == "Cron Stone"
    assert idx.search("4901")[0]["name"] == "Db Black"
    assert idx.search("cache") == []


def test_lookup_batches_at_most_100_ids(tmp_path):
    f = DbFetch({i: f"Item {i}" for i in range(1, 251)})
    db = itemnames.UtilDb(fetch=f, clock=Clock(), cache_dir=tmp_path / "u")
    got = db.lookup(list(range(1, 251)))
    assert len(f.calls) == 3 and got[250] == "Item 250"


def test_util_db_error_code_body_backs_off(tmp_path):
    class Code:
        calls = []

        def __call__(self, url, timeout):
            self.calls.append(url)
            return b'{"code": 103, "message": "blocked"}'
    f = Code()
    db = itemnames.UtilDb(fetch=f, clock=Clock(), cache_dir=tmp_path / "u")
    assert db.lookup([4901]) is None
    assert db.lookup([4901]) is None
    assert len(f.calls) == 1


def test_index_file_is_runtime_json(tmp_path):
    _cache(tmp_path, "sublist_4901_0", {"id": 4901, "sid": 0, "name": "Black Stone"})
    _index(tmp_path).search("black")
    doc = json.loads((tmp_path / "market_names.json").read_text(encoding="utf-8"))
    assert doc["schema"] == 1 and doc["cache"]["4901:0"] == "Black Stone"


# --- route -------------------------------------------------------------------

@pytest.fixture()
def nsrv(tmp_path):
    sub = {"name": "Black Stone", "id": 4901, "sid": 0, "basePrice": 200}
    calls = []

    def fetch(url, timeout):
        calls.append(url)
        if "/util/db" in url:
            return json.dumps({"id": 721003, "name": "Caphras Stone", "grade": 2}).encode()
        return json.dumps(sub).encode()
    client = market.ArshaClient(fetch=fetch, clock=Clock(), cache_dir=tmp_path / "cache")
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          sse_interval=0.05, market_client=client, market_seed=[4901],
                          profile_cfg={})
    t = threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    t.start()
    s.calls = calls
    yield s
    s.shutdown()
    s.server_close()


def _get(s, path):
    c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=5)
    c.request("GET", path)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, json.loads(data)


def test_route_search_seed(nsrv):
    st, doc = _get(nsrv, "/api/market/search?q=cron")
    assert st == 200 and doc["items"] == [{"id": 16080, "sid": 0, "name": "Cron Stone"}]
    assert nsrv.calls == []  # a name query never reaches the network


def test_route_search_sees_cached_watch_names(nsrv):
    _get(nsrv, "/api/market/watch")
    st, doc = _get(nsrv, "/api/market/search?q=Black%20St")
    assert st == 200 and doc["items"] == [{"id": 4901, "sid": 0, "name": "Black Stone"}]


@pytest.mark.parametrize("qs", ["", "?q=", "?q=a", "?q=" + "x" * 41, "?q=caf%C3%A9", "?x=cron"])
def test_route_search_bad_query_400(nsrv, qs):
    st, doc = _get(nsrv, "/api/market/search" + qs)
    assert st == 400 and "error" in doc
