"""Plan 023: AP/DP bracket tables (data/brackets.json), lookup, summary, operator
overrides through POST /api/progress {"brackets_set": ...}, and the `brackets`
key of GET /api/progress. No network."""

import http.client
import json
import threading

import pytest

from server.ew import app as ewapp
from server.ew import brackets, market, progress
from server.ew.store import Store


def _tables():
    return brackets.load_tracked()


def _ap():
    return _tables()["ap"]["rows"]


# -- tracked file schema --------------------------------------------------------

def test_tracked_file_ascii_and_schema():
    raw = brackets.DATA_FILE.read_bytes()
    raw.decode("ascii")
    assert b"\r" not in raw
    doc = json.loads(raw)
    assert set(doc) == set(brackets.TABLES) | set(brackets.LEVEL_KEYS)  # plan 087 rows
    for name, t in doc.items():
        if name in brackets.LEVEL_KEYS:
            assert all(r["verified"] is False for r in t), name
            continue
        assert t["source"].startswith("https://www.blackdesertfoundry.com/"), name
        assert t["verified"] == "2026-08-13", name
        assert isinstance(t["note"], str) and t["note"], name
        rows = t["rows"]
        mins = [r["min"] for r in rows]
        # monotonic, no gaps: each row's bracket ends where the next one starts
        assert mins == sorted(set(mins)) and mins[0] >= 0, name
        known = [r["value"] for r in rows if r["value"] is not None]
        assert known == sorted(known), name
        assert rows[-1]["value"] is not None, name  # the open-ended top row is a fact
        assert brackets.validate_table(t) == brackets.validate_table(brackets.validate_table(t))


@pytest.mark.parametrize("sheet,bonus", [(245, 48), (257, 83), (273, 142), (248, 48), (276, 142),
                                          (100, 5), (139, 5), (140, 10), (449, 297), (999, 297),
                                          (309, 200), (315, 200)])
def test_research_known_pairs(sheet, bonus):
    assert brackets.lookup(_ap(), sheet)["value"] == bonus


# -- lookup ---------------------------------------------------------------------

def test_lookup_inside_bracket():
    r = brackets.lookup(_ap(), 251)
    assert r == {"x": 251, "bracket_min": 249, "bracket_max": 252, "value": 57,
                 "next_min": 253, "next_gain": 12}


def test_lookup_on_bounds():
    lo = brackets.lookup(_ap(), 249)
    hi = brackets.lookup(_ap(), 252)
    assert lo["bracket_min"] == hi["bracket_min"] == 249 and lo["value"] == hi["value"] == 57
    assert brackets.lookup(_ap(), 253)["value"] == 69


def test_lookup_below_first_row():
    r = brackets.lookup(_ap(), 42)
    assert r == {"x": 42, "bracket_min": None, "bracket_max": 99, "value": 0,
                 "next_min": 100, "next_gain": 5}


def test_lookup_above_last_row():
    r = brackets.lookup(_ap(), 600)
    assert r["bracket_min"] == 449 and r["bracket_max"] is None and r["value"] == 297
    assert r["next_min"] is None and r["next_gain"] is None


def test_lookup_untranscribed_span_is_unknown_not_guessed():
    r = brackets.lookup(_ap(), 290)
    assert r["value"] is None and r["bracket_min"] == 277
    assert r["next_min"] == 309 and r["next_gain"] is None
    # the bracket before a gap knows where it ends but not what comes next
    r = brackets.lookup(_ap(), 274)
    assert r["value"] == 142 and r["next_min"] == 277 and r["next_gain"] is None


def test_summary_none_when_gs_unset():
    assert brackets.summary({"ap": None, "aap": None, "dp": None}, _tables()) is None
    assert brackets.summary(None, _tables()) is None


def test_summary_shape_and_cliff():
    s = brackets.summary({"ap": 251, "aap": None, "dp": 205}, _tables())
    assert s["aap"] is None
    assert s["ap"]["value"] == 57 and s["ap"]["cliff"] is True  # 235->251 +17, 251->267 +65
    assert s["dp"]["value"] == 1 and s["dp"]["all_dr"]["value"] == 0
    assert s["dp"]["all_dr"]["next_min"] == 253
    assert set(s["tables"]) == set(brackets.TABLES)
    assert s["tables"]["ap"]["override"] is False and s["tables"]["ap"]["reverify"] is False
    # 120 sheet AP: 104 -> +5, 120 -> +5, 136 -> +5: flat, no cliff
    assert brackets.summary({"ap": 120}, _tables())["ap"]["cliff"] is False
    # unknown neighbourhood never claims a cliff
    assert brackets.summary({"ap": 290}, _tables())["ap"]["cliff"] is False


def test_summary_reverify_after_epoch():
    ep = {"id": "e", "starts_utc": "2026-10-08T00:00:00+00:00"}
    s = brackets.summary({"ap": 251}, _tables(), epoch=ep)
    assert all(t["reverify"] for t in s["tables"].values())
    old = {"id": "e", "starts_utc": "2026-01-01T00:00:00+00:00"}
    assert not brackets.summary({"ap": 251}, _tables(), epoch=old)["tables"]["ap"]["reverify"]


# -- validation + overrides -------------------------------------------------------

def _table(rows, **kw):
    return dict({"source": "operator", "verified": "2026-10-05", "note": "", "rows": rows}, **kw)


@pytest.mark.parametrize("bad", [
    None, [], {"rows": [{"min": 1, "value": 1}]},
    _table([]),
    _table([{"min": 5, "value": 1}, {"min": 5, "value": 2}]),
    _table([{"min": 6, "value": 1}, {"min": 5, "value": 2}]),
    _table([{"min": 1, "value": 5}, {"min": 5, "value": 2}]),
    _table([{"min": -1, "value": 1}]),
    _table([{"min": 1000, "value": 1}]),
    _table([{"min": 1, "value": "1"}]),
    _table([{"min": True, "value": 1}]),
    _table([{"min": 1, "value": 1, "x": 2}]),
    _table([{"min": 1, "value": None}]),
    _table([{"min": 1, "value": 1}], verified="yesterday"),
    _table([{"min": 1, "value": 1}], source=""),
    _table([{"min": 1, "value": 1}], extra=1),
    _table([{"min": n, "value": n} for n in range(brackets.MAX_ROWS + 1)]),
])
def test_validate_table_rejects(bad):
    with pytest.raises(ValueError):
        brackets.validate_table(bad)


def test_override_precedence(tmp_path):
    store = Store(tmp_path)
    svc = progress.ProgressService(store)
    svc.set_character({"gs": {"ap": 251}})
    assert svc.view(refresh=False)["brackets"]["ap"]["value"] == 57
    out = svc.brackets_set({"ap": _table([{"min": 0, "value": 0}, {"min": 250, "value": 99}])})
    assert out["brackets"]["ap"]["value"] == 99
    assert out["brackets"]["tables"]["ap"]["override"] is True
    assert out["brackets"]["tables"]["dp_dr"]["override"] is False
    assert store.get("brackets_override")["ap"]["rows"][1] == {"min": 250, "value": 99}
    out = svc.brackets_set({"ap": None})
    assert out["brackets"]["ap"]["value"] == 57
    assert "ap" not in store.get("brackets_override")


def test_corrupt_override_is_ignored(tmp_path):
    store = Store(tmp_path)
    store.put("brackets_override", {"ap": {"rows": "junk"}, "dp_dr": 7, "zz": {}})
    svc = progress.ProgressService(store)
    svc.set_character({"gs": {"ap": 245}})
    assert svc.view(refresh=False)["brackets"]["ap"]["value"] == 48


@pytest.mark.parametrize("arg", [None, {}, [], {"nope": None}, {"ap": 5},
                                 {"ap": _table([{"min": 2, "value": 1}, {"min": 1, "value": 2}])}])
def test_brackets_set_validation(tmp_path, arg):
    with pytest.raises(ValueError):
        progress.ProgressService(Store(tmp_path)).brackets_set(arg)


def test_view_brackets_null_without_gs(tmp_path):
    assert progress.ProgressService(Store(tmp_path)).view(refresh=False)["brackets"] is None


# -- routes -----------------------------------------------------------------------

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


def _req(s, method, path, body=None):
    c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=5)
    data = None if body is None else json.dumps(body).encode()
    c.request(method, path, body=data,
              headers={"Content-Type": "application/json"} if data else {})
    r = c.getresponse()
    out = r.read()
    c.close()
    return r.status, (json.loads(out) if out else None)


def test_route_brackets(srv):
    st, doc = _req(srv, "GET", "/api/progress")
    assert st == 200 and doc["brackets"] is None
    st, doc = _req(srv, "POST", "/api/progress", {"character": {"gs": {"ap": 257, "dp": 254}}})
    assert st == 200 and doc["brackets"]["ap"]["value"] == 83
    assert doc["brackets"]["dp"]["all_dr"]["value"] == 2
    st, doc = _req(srv, "POST", "/api/progress",
                   {"brackets_set": {"ap": _table([{"min": 0, "value": 1}])}})
    assert st == 200 and doc["brackets"]["ap"]["value"] == 1
    st, doc = _req(srv, "POST", "/api/progress",
                   {"brackets_set": {"ap": _table([{"min": 0, "value": None}])}})
    assert st == 400
    st, doc = _req(srv, "POST", "/api/progress", {"brackets_set": {"bogus": None}})
    assert st == 400
