"""Plan 072: world boss schedule drift check against a public NA table.

At most one robots-gated GET a day, parse #mainTbl, normalize names through the
data file's alias map, diff against the tracked table. A difference is a banner
only (never an edit of the data file); unreachable / disallowed / unparsable =
state "unknown", no banner. Fixture HTML only - no network.
"""

import datetime as dt
import http.client
import json
import threading
from pathlib import Path

import pytest

from server.ew import app as ewapp
from server.ew import bossdrift, bosses, market

UTC = dt.timezone.utc
T0 = dt.datetime(2026, 11, 3, 18, 0, 0, tzinfo=UTC).timestamp()  # after the DST change
FIX = Path(__file__).parent / "fixtures" / "bossdrift"
SAME = FIX / "mainTbl_na.html"
MOVED = FIX / "mainTbl_na_moved.html"
TABLE = bosses.load_table()
CFG = bossdrift.config(TABLE)
ROBOTS_ALLOW = b"User-agent: *\nDisallow: /admin\n"
ROBOTS_DISALLOW = b"User-agent: *\nDisallow: /bdo\n"


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


class Net:
    """Fake opener: robots.txt and the table page by URL; records every call."""

    def __init__(self, robots=ROBOTS_ALLOW, page=None):
        self.robots = robots
        self.page = SAME.read_bytes() if page is None else page
        self.calls = []

    def __call__(self, url, timeout):
        self.calls.append(url)
        if url == CFG["robots"]:
            body = self.robots
        elif url == CFG["url"]:
            body = self.page
        else:
            raise AssertionError(f"unexpected url {url}")
        if isinstance(body, Exception):
            raise body
        return body


def _sync(fn):
    fn()


def _svc(tmp_path, net=None, clock=None):
    clock = clock or Clock()
    net = net or Net()
    client = bossdrift.DriftClient(CFG, fetch=net, clock=clock, cache_dir=tmp_path / "cache")
    return bossdrift.DriftService(client, TABLE, spawn=_sync), net, clock


# --- data file + constants ------------------------------------------------------

def test_config_from_data_file():
    assert CFG["url"] == "https://mmotimer.com/bdo/?server=na"
    assert CFG["robots"] == "https://mmotimer.com/robots.txt"
    assert CFG["table_id"] == "mainTbl" and CFG["tz"] == "PT"
    assert CFG["aliases"]["golden pig"] == "Golden Pig King"
    assert all(k == k.lower() for k in CFG["aliases"])


def test_config_missing_or_bad_is_none():
    assert bossdrift.config({"slots": []}) is None
    bad = dict(TABLE, drift=dict(TABLE["drift"], url="http://mmotimer.com/bdo/"))
    assert bossdrift.config(bad) is None
    bad = dict(TABLE, drift=dict(TABLE["drift"], tz="CET"))
    assert bossdrift.config(bad) is None


def test_module_never_posts():
    src = Path(bossdrift.__file__).read_text(encoding="ascii")
    assert "POST" not in src and "method=\"GET\"" in src


# --- parser + normalization -------------------------------------------------------

def test_parse_fixture_matches_local_table():
    got = bossdrift.remote_slots(SAME.read_text(encoding="ascii"), CFG, TABLE, T0)
    key = sorted((s["weekday"], s["at"], sorted(s["bosses"])) for s in got)
    want = sorted((s["weekday"], s["at"], sorted(s["bosses"])) for s in TABLE["slots"])
    assert key == want


def test_parse_ignores_decoy_table_and_scripts():
    slots = bossdrift.parse_table(SAME.read_text(encoding="ascii"), "mainTbl")
    assert len(slots) == len(TABLE["slots"])
    assert not any("Fake" in b for s in slots for b in s["bosses"])


@pytest.mark.parametrize("name,want", [
    ("Golden Pig", "Golden Pig King"),
    ("  golden   PIG  ", "Golden Pig King"),
    ("Ancient Kutum", "Kutum"),
    ("Kzarka, Lord of Corruption", "Kzarka"),
    ("kzarka", "Kzarka"),
    ("Some New Boss", "Some New Boss"),
])
def test_alias_normalization(name, want):
    known = {b for s in TABLE["slots"] for b in s["bosses"]}
    assert bossdrift.normalize(name, CFG["aliases"], known) == want


def test_cell_split_on_separators():
    page = ("<table id=mainTbl><tr><th></th><th>10:00</th><th>11:00</th></tr>"
            "<tr><td>Mon</td><td>Golden Pig &amp; Kzarka</td><td>Quint / Muraka</td></tr></table>")
    got = bossdrift.remote_slots(page, CFG, TABLE, T0)
    assert sorted((s["at"], sorted(s["bosses"])) for s in got) == [
        ("10:00", ["Golden Pig King", "Kzarka"]), ("11:00", ["Muraka", "Quint"])]


def test_parse_12h_header():
    page = ("<table id=mainTbl><tr><th>Day</th><th>12:00 AM</th><th>8:15 PM</th></tr>"
            "<tr><td>Sunday</td><td>Vell</td><td>Garmoth</td></tr></table>")
    got = bossdrift.parse_table(page, "mainTbl")
    assert sorted((s["weekday"], s["at"]) for s in got) == [(6, "00:00"), (6, "20:15")]


@pytest.mark.parametrize("page", [
    "", "<html>no table</html>", "<table id=other><tr><th>10:00</th></tr></table>",
    "<table id=mainTbl><tr><th>Mon</th><td>Kzarka</td></tr></table>",
    "<table id=mainTbl><tr><th></th><th>10:00</th></tr><tr><td>Funday</td><td>Kzarka</td></tr></table>",
    "<table id=mainTbl><tr><th></th><th>10:00</th></tr><tr><td>Mon</td><td>&nbsp;</td></tr></table>",
    "\x00\xff<table",
])
def test_parse_failure_raises(page):
    with pytest.raises(ValueError):
        bossdrift.parse_table(page, "mainTbl")


def test_utc_source_shifts_to_pt():
    cfg = dict(CFG, tz="UTC")
    page = ("<table id=mainTbl><tr><th></th><th>02:00</th></tr>"
            "<tr><td>Tuesday</td><td>Garmoth</td></tr></table>")
    got = bossdrift.remote_slots(page, cfg, TABLE, T0)  # PST: UTC-8
    assert got == [{"weekday": 0, "at": "18:00", "bosses": ["Garmoth"]}]


# --- diff -------------------------------------------------------------------------

def _remote(path):
    return bossdrift.remote_slots(path.read_text(encoding="ascii"), CFG, TABLE, T0)


def test_identical_table_no_diff():
    assert bossdrift.diff(TABLE["slots"], _remote(SAME)) == []


def test_moved_slot_is_exactly_one_entry():
    d = bossdrift.diff(TABLE["slots"], _remote(MOVED))
    assert d == [{"kind": "moved", "weekday": 1, "local_at": "17:00",
                  "remote_weekday": 1, "remote_at": "18:00",
                  "local": ["Bulgasal", "Kzarka"], "remote": ["Bulgasal", "Kzarka"],
                  "text": "Tue 17:00 -> 18:00: Bulgasal + Kzarka"}]


def test_changed_missing_extra():
    local = [{"weekday": 0, "at": "10:00", "bosses": ["Kzarka"]},
             {"weekday": 0, "at": "12:00", "bosses": ["Garmoth"]},
             {"weekday": 2, "at": "09:00", "bosses": ["Vell"]}]
    remote = [{"weekday": 0, "at": "10:00", "bosses": ["Nouver"]},
              {"weekday": 4, "at": "09:00", "bosses": ["Offin"]},
              {"weekday": 2, "at": "09:00", "bosses": ["Vell"]}]
    d = bossdrift.diff(local, remote)
    assert [(x["kind"], x["weekday"], x["text"]) for x in d] == [
        ("changed", 0, "Mon 10:00: Kzarka -> Nouver"),
        ("missing", 0, "Mon 12:00: Garmoth not listed upstream"),
        ("extra", 4, "Fri 09:00: Offin listed upstream only")]


def test_move_across_midnight_pairs():
    local = [{"weekday": 1, "at": "00:00", "bosses": ["Kzarka"]}]
    remote = [{"weekday": 0, "at": "23:00", "bosses": ["Kzarka"]}]
    d = bossdrift.diff(local, remote)
    assert [x["kind"] for x in d] == ["moved"] and d[0]["text"] == "Tue 00:00 -> Mon 23:00: Kzarka"


def test_far_move_is_not_paired():
    local = [{"weekday": 1, "at": "00:00", "bosses": ["Kzarka"]}]
    remote = [{"weekday": 1, "at": "12:00", "bosses": ["Kzarka"]}]
    assert [x["kind"] for x in bossdrift.diff(local, remote)] == ["missing", "extra"]


# --- client + service ---------------------------------------------------------------

def test_moved_fixture_banner_names_exactly_that_slot(tmp_path):
    svc, net, _ = _svc(tmp_path, Net(page=MOVED.read_bytes()))
    v = svc.view(refresh=True)
    assert v["state"] == "differs" and v["n"] == 1
    assert v["banner"] == "schedule differs from mmotimer.com on 1 slot - verify"
    assert [x["text"] for x in v["diff"]] == ["Tue 17:00 -> 18:00: Bulgasal + Kzarka"]
    assert net.calls == [CFG["robots"], CFG["url"]]


def test_same_table_is_ok_no_banner(tmp_path):
    svc, _, _ = _svc(tmp_path)
    v = svc.view(refresh=True)
    assert v["state"] == "ok" and v["banner"] is None and v["diff"] == [] and v["n"] == 0


def test_robots_off_means_no_get(tmp_path):
    svc, net, _ = _svc(tmp_path, Net(robots=ROBOTS_DISALLOW))
    v = svc.view(refresh=True)
    assert net.calls == [CFG["robots"]]
    assert v["state"] == "unknown" and v["banner"] is None and v["robots"] == "disallow"


@pytest.mark.parametrize("robots", [OSError("down"), b"<html>404</html>", b""])
def test_robots_unreachable_means_no_get(tmp_path, robots):
    svc, net, _ = _svc(tmp_path, Net(robots=robots))
    v = svc.view(refresh=True)
    assert net.calls == [CFG["robots"]] and v["state"] == "unknown" and v["banner"] is None


def test_page_unreachable_is_unknown(tmp_path):
    svc, _, _ = _svc(tmp_path, Net(page=OSError("timeout")))
    v = svc.view(refresh=True)
    assert v["state"] == "unknown" and v["banner"] is None


def test_parse_failure_is_unknown(tmp_path):
    svc, _, _ = _svc(tmp_path, Net(page=b"<html>redesigned</html>"))
    v = svc.view(refresh=True)
    assert v["state"] == "unknown" and v["banner"] is None


def test_at_most_one_attempt_a_day(tmp_path):
    svc, net, clock = _svc(tmp_path, Net(page=OSError("down")))
    svc.view(refresh=True)
    clock.t += 23 * 3600
    svc.view(refresh=True)
    assert net.calls == [CFG["robots"], CFG["url"]]
    net.page = SAME.read_bytes()
    clock.t += 3600
    assert svc.view(refresh=True)["state"] == "ok"
    assert len(net.calls) == 4
    clock.t += 3600
    svc.view(refresh=True)
    assert len(net.calls) == 4


def test_robots_flip_hides_old_diff(tmp_path):
    svc, net, clock = _svc(tmp_path, Net(page=MOVED.read_bytes()))
    assert svc.view(refresh=True)["state"] == "differs"
    net.robots = ROBOTS_DISALLOW
    clock.t += 25 * 3600
    v = svc.view(refresh=True)
    assert v["state"] == "unknown" and v["banner"] is None


def test_peek_only_view_never_fetches(tmp_path):
    svc, net, _ = _svc(tmp_path)
    assert svc.view(refresh=False)["state"] == "unknown"
    assert svc.source()["status"] == "unknown"
    assert net.calls == []


def test_disabled_without_client():
    svc = bossdrift.DriftService(None, TABLE)
    v = svc.view(refresh=True)
    assert v["state"] == "unknown" and v["banner"] is None and v["diff"] == []
    assert svc.source() == {"updated": None, "ttl_s": bossdrift.TTL_S, "status": "unknown",
                            "robots": None, "banner": None}


def test_corrupt_cache_is_unknown(tmp_path):
    svc, net, _ = _svc(tmp_path)
    svc.view(refresh=True)
    path = tmp_path / "cache" / f"{bossdrift.KEY}.json"
    doc = json.loads(path.read_text())
    doc["data"] = {"slots": [{"weekday": 9, "at": "x", "bosses": 3}]}
    path.write_text(json.dumps(doc))
    assert svc.view(refresh=False)["state"] == "unknown"


def test_never_edits_data_file(tmp_path):
    before = bosses.TABLE_FILE.read_bytes()
    svc, _, _ = _svc(tmp_path, Net(page=MOVED.read_bytes()))
    svc.view(refresh=True)
    assert bosses.TABLE_FILE.read_bytes() == before


# --- HTTP wiring ------------------------------------------------------------------------

def _no_network(url, timeout):
    raise AssertionError(f"network call {url}")


def _offline_market(tmp_path):
    return market.ArshaClient(fetch=_no_network, cache_dir=tmp_path / "mcache")


@pytest.fixture
def srv(tmp_path):
    clock = Clock()
    net = Net(page=MOVED.read_bytes())
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", sse_interval=0.05,
                          market_seed=[], profile_cfg={},
                          market_client=_offline_market(tmp_path), bosses_clock=clock,
                          drift_client=bossdrift.DriftClient(CFG, fetch=net, clock=clock,
                                                             cache_dir=tmp_path / "dc"),
                          drift_spawn=_sync, config_path=tmp_path / "local.json")
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield s, net
    s.shutdown()
    s.server_close()


def _get(s, path):
    c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=5)
    c.request("GET", path)
    r = c.getresponse()
    return r.status, json.loads(r.read())


def test_get_bosses_carries_drift(srv):
    s, net = srv
    st, body = _get(s, "/api/bosses")
    assert st == 200 and body["drift"]["state"] == "differs" and body["drift"]["n"] == 1
    st, state = _get(s, "/api/state")
    assert state["sources"]["bossdrift"]["status"] == "differs"
    assert state["sources"]["bossdrift"]["banner"].startswith("schedule differs from mmotimer.com")


def test_default_make_server_has_drift_disabled(tmp_path):
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", market_seed=[],
                          profile_cfg={}, market_client=_offline_market(tmp_path),
                          config_path=tmp_path / "l.json")
    try:
        assert s.drift.client is None
        assert s.bosses_view()["drift"]["state"] == "unknown"
    finally:
        s.server_close()
