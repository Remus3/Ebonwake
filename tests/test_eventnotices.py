"""Plan 059: official event-notice import, maintenance-relative windows, suggest-only.

robots.txt-gated (allow / disallow / unreachable = off), one Events-board list
GET per 6 h plus at most MAX_DETAILS Detail GETs per run for notices not yet
cached; nothing is auto-added. Fixture HTML only - no network.
"""

import datetime as dt
import http.client
import json
import threading
from pathlib import Path

import pytest

from server.ew import app as ewapp
from server.ew import coupons, eventnotices, events, maint, market
from server.ew.store import Store

UTC = dt.timezone.utc
T0 = dt.datetime(2026, 10, 6, 12, 0, 0, tzinfo=UTC).timestamp()
FIX = Path(__file__).parent / "fixtures" / "eventnotices"
ROBOTS_ALLOW = b"User-agent: *\nDisallow: /en-US/Account\n"
ROBOTS_DISALLOW = b"User-agent: *\nDisallow: /en-US/News\n"
ROBOTS_NO_DETAIL = b"User-agent: *\nDisallow: /en-US/News/Detail\n"
SLOT = {"weekday": "thu", "start_utc": "07:00", "duration_min": 180}
NO_DATES = b"<html><body><div class='contents_area'><p>No window here.</p></div></body></html>"


def _detail(n):
    p = FIX / f"detail_{n}.html"
    return p.read_bytes() if p.exists() else NO_DATES


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


class Net:
    """Fake opener: robots.txt, the list page and Detail pages; records every call."""

    def __init__(self, robots=ROBOTS_ALLOW, page=None):
        self.robots = robots
        self.page = (FIX / "list.html").read_bytes() if page is None else page
        self.details = {}
        self.boards = {}
        self.calls = []

    def __call__(self, url, timeout):
        self.calls.append(url)
        if url == coupons.ROBOTS_URL:
            body = self.robots
        elif url == eventnotices.LIST_URL:
            body = self.page
        elif url in eventnotices.LIST_URLS.values():  # plan 064 boards: empty here
            body = self.boards.get(url, b"<html><body></body></html>")
        elif url.startswith(eventnotices.DETAIL_URL):
            n = int(url.split("groupContentNo=")[1].split("&")[0])
            body = self.details.get(n, _detail(n))
        else:
            raise AssertionError(f"unexpected url {url}")
        if isinstance(body, Exception):
            raise body
        return body

    def detail_calls(self):
        return [u for u in self.calls if u.startswith(eventnotices.DETAIL_URL)]


def _sync(fn):
    fn()


def _client(tmp_path, net, clock):
    return eventnotices.NoticeClient(fetch=net, clock=clock, cache_dir=tmp_path / "cache")


def _svc(tmp_path, net=None, clock=None, override=""):
    clock = clock or Clock()
    net = net or Net()
    store = Store(tmp_path / "store")
    ev = events.EventsService(store, clock=clock)
    svc = eventnotices.NoticeService(_client(tmp_path, net, clock), ev, store,
                                     maint_start=lambda: override, spawn=_sync,
                                     slot_path=tmp_path / "noslot.json")
    return svc, ev, net, clock


def _iso(*a):
    return dt.datetime(*a, tzinfo=UTC).isoformat()


# --- constants: fixed official pages, read-only ------------------------------

def test_fixed_official_urls_https_only():
    assert eventnotices.LIST_URL == ("https://www.naeu.playblackdesert.com/en-US/News/Notice"
                                     "?boardType=3")
    assert eventnotices.LIST_URL == events.SOURCES[1]["url"]
    assert eventnotices.DETAIL_URL.startswith("https://www.naeu.playblackdesert.com/en-US/News/")
    assert eventnotices.MIN_INTERVAL_S == 6 * 3600 and eventnotices.MAX_DETAILS == 5
    assert "Ebonwake" in eventnotices.USER_AGENT


def test_module_never_posts():
    src = Path(eventnotices.__file__).read_text(encoding="utf-8")
    assert '"POST"' not in src and "'POST'" not in src
    assert 'method="GET"' in src


# --- robots gate (plan 014's, over both pages) --------------------------------

def test_robots_allow_covers_list_and_detail():
    assert coupons.robots_verdict(Net(), urls=eventnotices.ROBOT_URLS) == "allow"


@pytest.mark.parametrize("body", [ROBOTS_DISALLOW, ROBOTS_NO_DETAIL,
                                  b"User-agent: Ebonwake\nDisallow: /\n\nUser-agent: *\nAllow: /\n"])
def test_robots_disallow(body):
    assert coupons.robots_verdict(Net(robots=body), urls=eventnotices.ROBOT_URLS) == "disallow"


def test_robots_default_still_news_only():
    assert coupons.robots_verdict(Net(robots=ROBOTS_NO_DETAIL)) == "allow"


# --- list parse ----------------------------------------------------------------

def test_parse_list_fixture():
    got = eventnotices.parse_list((FIX / "list.html").read_text(encoding="utf-8"))
    nos = [n["group_no"] for n in got]
    assert nos == [10658, 10657, 10656, 10647, 10620, 10618, 10602, 10545], "dedup, order kept"
    by = {n["group_no"]: n for n in got}
    assert by[10656]["title"] == "[Donghwa's Gift] One Day Only, Time-Limited Coupon"
    assert by[10656]["url"] == eventnotices.detail_url(10656)
    assert by[10656]["url"] == ("https://www.naeu.playblackdesert.com/en-US/News/Detail"
                                "?groupContentNo=10656&countryType=en-US")
    assert 99999 not in nos, "script text"
    assert 10544 not in nos and 10543 not in nos, "off-site host / other path prefix dropped"


def test_parse_list_reads_the_notice_detail_links():
    """Research 0016: the list now links /News/Notice/Detail; both shapes parse."""
    got = eventnotices.parse_list(
        (FIX / "list_notice_detail.html").read_text(encoding="utf-8"))
    assert [n["group_no"] for n in got] == [10678, 10661, 10640], "dedup, order kept"
    assert got[0]["title"] == "[Updates] Patch Notes - October 8, 2026"
    assert got[0]["url"] == eventnotices.detail_url(10678), "fetched by the old Detail URL"


@pytest.mark.parametrize("href,want", [
    ("/en-US/News/Detail?groupContentNo=5", 5),
    ("/News/Notice/Detail?groupContentNo=5&countryType=en-US", 5),
    ("/en-US/News/Notice/Detail?groupContentNo=5", 5),
    ("https://www.naeu.playblackdesert.com/News/Notice/Detail?groupContentNo=5", 5),
    ("https://evil.example.com/News/Notice/Detail?groupContentNo=5", None),
    ("http://www.naeu.playblackdesert.com/News/Notice/Detail?groupContentNo=5", None),
    ("/News/Other/Detail?groupContentNo=5", None),
    ("/News/Notice/Detail/x?groupContentNo=5", None),
    ("/News/Notice?boardType=2", None),
])
def test_group_no_of_both_paths_only(href, want):
    assert eventnotices.group_no_of(href) == want


def test_list_with_detail_links_parsing_to_nothing_is_a_failure(tmp_path):
    """Research 0016 step 4: a 200 list page with Detail-like anchors that
    parses to 0 notices is a parse failure - no ok_at, Detail cache kept."""
    svc, _ev, net, clock = _svc(tmp_path)
    svc.view(refresh=True)
    client = svc.client
    ok_at, cached = client.attempt()["ok_at"], set(client.details())
    assert ok_at == T0 and cached
    net.page = (b"<ul><li><a href='/News/Changed/Detail?groupContentNo=10658'>"
                b"<b class='title'>T</b></a></li></ul>")
    clock.t += 6 * 3600
    v = svc.view(refresh=True)
    assert v["status"] == "stale" and "0 notices" in v["error"]
    assert client.attempt()["ok_at"] == ok_at and client.attempt()["fail_since"] == clock.t
    assert set(client.details()) == cached, "Detail cache not pruned"


def test_empty_board_without_detail_links_is_still_ok(tmp_path):
    net = Net(page=b"<html><body><ul></ul></body></html>")
    svc, _ev, net, _clock = _svc(tmp_path, net=net)
    assert svc.view(refresh=True)["status"] == "ok"
    assert svc.client.attempt()["ok_at"] == T0


def test_parse_list_tolerates_garbage():
    assert eventnotices.parse_list("") == []
    assert eventnotices.parse_list("<a href='/en-US/News/Detail?groupContentNo=abc'>x</a>") == []
    assert eventnotices.parse_list("<a href='/en-US/News/Detail?groupContentNo=12'>t <b>") \
        == [{"group_no": 12, "title": "t", "url": eventnotices.detail_url(12), "stamp": None}]


def test_parse_list_reads_a_last_updated_stamp_when_present():
    html = ("<a href='/en-US/News/Detail?groupContentNo=7'><strong class='title'>T</strong>"
            "<span class='date'>Last Updated: Oct 6, 2026</span></a>")
    assert eventnotices.parse_list(html)[0]["stamp"] == "2026-10-06"


# --- window parse --------------------------------------------------------------

def test_detail_10656_is_maintenance_relative():
    w = eventnotices.parse_detail(_detail(10656), ref_year=2026)
    assert w["starts"] == {"date": "2026-10-01", "edge": "after", "time": None}
    assert w["ends"] == {"date": "2026-10-15", "edge": "before", "time": None}
    assert w["ends_text"] == "Oct 15, 2026 (Thu) before maintenance"
    r = eventnotices.resolve_window(w, SLOT)
    assert r["starts"] == _iso(2026, 10, 1, 10, 0) and r["ends"] == _iso(2026, 10, 15, 7, 0)
    assert r["maint_relative"] is True and r["ends_edge"] == "before"


def test_detail_10545_explicit_utc_wins():
    w = eventnotices.parse_detail(_detail(10545), ref_year=2026)
    assert w["starts"] == {"date": "2026-09-04", "edge": None, "time": "00:00"}
    assert w["ends"] == {"date": "2026-10-21", "edge": None, "time": "23:59"}
    r = eventnotices.resolve_window(w, SLOT)
    assert r["starts"] == _iso(2026, 9, 4, 0, 0) and r["ends"] == _iso(2026, 10, 21, 23, 59)
    assert r["maint_relative"] is False and r["ends_edge"] is None


def test_detail_without_window_is_none():
    assert eventnotices.parse_detail(_detail(10647), ref_year=2026) is None
    assert eventnotices.parse_detail(b"", ref_year=2026) is None
    assert eventnotices.parse_detail(b"<p>Oct 99, 2026 - Oct 98, 2026</p>", ref_year=2026) is None


@pytest.mark.parametrize("text,starts,ends", [
    ("Oct 1 (Thu) after maintenance ~ Oct 15 (Thu) before maintenance",
     {"date": "2026-10-01", "edge": "after", "time": None},
     {"date": "2026-10-15", "edge": "before", "time": None}),
    ("December 24, 2026 after maintenance - January 7, 2027 before maintenance",
     {"date": "2026-12-24", "edge": "after", "time": None},
     {"date": "2027-01-07", "edge": "before", "time": None}),
    ("Dec 24 after the maintenance to Jan 7 before the maintenance",
     {"date": "2026-12-24", "edge": "after", "time": None},
     {"date": "2027-01-07", "edge": "before", "time": None}),
    ("Oct 1, 2026 after maintenance - Oct 31, 2026 23:59 UTC",
     {"date": "2026-10-01", "edge": "after", "time": None},
     {"date": "2026-10-31", "edge": None, "time": "23:59"}),
    ("Oct 1, 2026 - Oct 31, 2026",
     {"date": "2026-10-01", "edge": None, "time": None},
     {"date": "2026-10-31", "edge": None, "time": None}),
])
def test_window_styles(text, starts, ends):
    w = eventnotices.parse_window(text, ref_year=2026)
    assert (w["starts"], w["ends"]) == (starts, ends)


@pytest.mark.parametrize("text,year,month,start,end", [
    ("Dec 24 - Jan 7", 2026, 12, "2026-12-24", "2027-01-07"),  # end wraps forward
    ("Dec 24 - Jan 7", 2026, 1, "2025-12-24", "2026-01-07"),   # read in January
    ("Dec 10 - Dec 31", 2027, 1, "2026-12-10", "2026-12-31"),  # last December
    ("Jan 2 - Jan 20", 2026, 12, "2027-01-02", "2027-01-20"),  # next January
    ("Oct 1 - Oct 15", 2026, 10, "2026-10-01", "2026-10-15"),
    ("Mar 3 - Apr 4", 2026, 6, "2026-03-03", "2026-04-04"),
])
def test_yearless_window_nearest_the_fetch_month(text, year, month, start, end):
    w = eventnotices.parse_window(text.replace(" - ", " after maintenance - ")
                                  + " before maintenance", ref_year=year, ref_month=month)
    assert (w["starts"]["date"], w["ends"]["date"]) == (start, end)


def test_date_only_points_cover_the_whole_day():
    w = eventnotices.parse_window("Oct 1, 2026 - Oct 31, 2026", ref_year=2026)
    r = eventnotices.resolve_window(w, SLOT)
    assert r["starts"] == _iso(2026, 10, 1, 0, 0) and r["ends"] == _iso(2026, 10, 31, 23, 59, 59)


def test_backwards_window_rejected():
    assert eventnotices.parse_window("Oct 15, 2026 (Thu) 10:00 (UTC) - Oct 1, 2026 00:00 (UTC)",
                                     ref_year=2026) is None


# --- client: robots, attempt floor, GET budget -----------------------------------

def test_first_run_budget_list_plus_five_details(tmp_path):
    svc, _ev, net, _clock = _svc(tmp_path)
    svc.view(refresh=True)
    assert net.calls[:2] == [coupons.ROBOTS_URL, eventnotices.LIST_URL]
    assert len(net.detail_calls()) == eventnotices.MAX_DETAILS
    # plan 064: one list GET per board + the shared Detail budget
    assert len([u for u in net.calls if u != coupons.ROBOTS_URL]) <= 3 + 5


def test_details_cached_then_the_rest_fetched(tmp_path):
    svc, _ev, net, clock = _svc(tmp_path)
    svc.view(refresh=True)
    first = set(net.detail_calls())
    for _ in range(5):
        clock.t += 3600
        svc.view(refresh=True)
    assert net.calls.count(eventnotices.LIST_URL) == 1, "one list GET per 6 h"
    clock.t = T0 + 6 * 3600
    svc.view(refresh=True)
    second = net.detail_calls()[len(first):]
    assert len(second) == 3 and not set(second) & first, "only uncached Detail pages"
    clock.t += 6 * 3600
    n = len(net.detail_calls())
    svc.view(refresh=True)
    assert len(net.detail_calls()) == n, "everything cached: no Detail GET"


def test_changed_stamp_refetches_detail(tmp_path):
    page = (b"<a href='/en-US/News/Detail?groupContentNo=10656'><b class='title'>T</b>"
            b"<span>Last Updated: Oct 5, 2026</span></a>")
    net = Net(page=page)
    svc, _ev, net, clock = _svc(tmp_path, net=net)
    svc.view(refresh=True)
    clock.t += 6 * 3600
    svc.view(refresh=True)
    assert len(net.detail_calls()) == 1
    net.page = page.replace(b"Oct 5", b"Oct 6")
    clock.t += 6 * 3600
    svc.view(refresh=True)
    assert len(net.detail_calls()) == 2


def test_robots_disallow_is_off_and_fetches_nothing_else(tmp_path):
    svc, _ev, net, _clock = _svc(tmp_path, net=Net(robots=ROBOTS_DISALLOW))
    v = svc.view(refresh=True)
    assert v["status"] == "off" and v["robots"] == "disallow" and v["candidates"] == []
    assert net.calls == [coupons.ROBOTS_URL]


def test_robots_unreachable_is_off(tmp_path):
    svc, _ev, net, _clock = _svc(tmp_path, net=Net(robots=OSError("dns")))
    v = svc.view(refresh=True)
    assert v["status"] == "off" and v["robots"] == "unreachable"
    assert net.calls == [coupons.ROBOTS_URL]


def test_list_failure_throttled_by_attempt_floor(tmp_path):
    net = Net()
    net.page = OSError("boom")
    svc, _ev, net, clock = _svc(tmp_path, net=net)
    v = svc.view(refresh=True)
    assert v["status"] == "error" and "boom" in v["error"]
    n = len(net.calls)
    clock.t += 3 * 3600
    svc.view(refresh=True)
    assert len(net.calls) == n


def test_detail_failure_skips_that_notice_only(tmp_path):
    net = Net()
    net.details[10658] = OSError("one bad page")
    svc, _ev, net, _clock = _svc(tmp_path, net=net)
    v = svc.view(refresh=True)
    assert v["status"] == "ok"
    assert len(net.detail_calls()) == eventnotices.MAX_DETAILS
    assert 10656 in {c["group_no"] for c in v["candidates"]}


def test_peek_only_never_fetches(tmp_path):
    svc, _ev, net, _clock = _svc(tmp_path)
    v = svc.view(refresh=False)
    assert net.calls == [] and v["status"] == "pending" and v["candidates"] == []


def test_disabled_without_client(tmp_path):
    store = Store(tmp_path / "store")
    ev = events.EventsService(store, clock=Clock())
    v = eventnotices.NoticeService(None, ev, store).view(refresh=True)
    assert v["status"] == "none" and v["candidates"] == []


def test_corrupt_cache_files_degrade(tmp_path):
    svc, _ev, net, _clock = _svc(tmp_path)
    cache = tmp_path / "cache"
    cache.mkdir(parents=True)
    for name in (f"{eventnotices.KEY}.json", eventnotices.ATTEMPT_FILE, eventnotices.DETAILS_FILE):
        (cache / name).write_text("{not json", encoding="utf-8")
    v = svc.view(refresh=True)
    assert v["status"] == "ok" and v["candidates"]


# --- service: suggest, never add ------------------------------------------------

def _full(tmp_path, **kw):
    """Service after two runs: all 8 Detail pages cached."""
    svc, ev, net, clock = _svc(tmp_path, **kw)
    svc.view(refresh=True)
    clock.t += 6 * 3600
    svc.view(refresh=True)
    return svc, ev, net, clock


def test_suggested_rows(tmp_path):
    svc, ev, _net, _clock = _full(tmp_path)
    v = svc.view(refresh=False)
    assert v["status"] == "ok" and v["source"] == eventnotices.LIST_URL
    by = {c["group_no"]: c for c in v["candidates"]}
    assert set(by) == {10656, 10545}
    c = by[10656]
    assert c == {"title": "[Donghwa's Gift] One Day Only, Time-Limited Coupon",
                 "url": eventnotices.detail_url(10656), "group_no": 10656,
                 "starts": _iso(2026, 10, 1, 10, 0), "ends": _iso(2026, 10, 15, 7, 0),
                 "ends_text": "Oct 15, 2026 (Thu) before maintenance", "kind": "event",
                 "maint_relative": True, "ends_edge": "before"}
    assert 10647 in {n["group_no"] for n in v["no_dates"]}, "title listed as no dates found"
    assert v["maintenance"]["start_utc"] == "07:00" and v["maintenance"]["verified"] is False
    assert ev.view()["items"] == [], "nothing auto-added"


def test_override_moves_maintenance_relative_ends(tmp_path):
    svc, _ev, _net, _clock = _full(tmp_path, override="09:00")
    c = next(c for c in svc.view()["candidates"] if c["group_no"] == 10656)
    assert c["ends"] == _iso(2026, 10, 15, 9, 0) and c["starts"] == _iso(2026, 10, 1, 12, 0)


def test_added_or_dismissed_rows_are_skipped(tmp_path):
    svc, ev, _net, _clock = _full(tmp_path)
    c = next(c for c in svc.view()["candidates"] if c["group_no"] == 10656)
    ev.add({"kind": "event", "title": c["title"], "starts": c["starts"], "ends": c["ends"],
            "url": c["url"]})
    assert 10656 not in {x["group_no"] for x in svc.view()["candidates"]}
    v = svc.dismiss(10545)
    assert v["candidates"] == [] and v["dismissed"] == [10545]
    with pytest.raises(ValueError):
        svc.dismiss("10545")
    with pytest.raises(ValueError):
        svc.dismiss(True)


def test_same_title_and_ends_is_skipped(tmp_path):
    svc, ev, _net, _clock = _full(tmp_path)
    c = next(c for c in svc.view()["candidates"] if c["group_no"] == 10545)
    ev.add({"kind": "event", "title": c["title"], "ends": c["ends"]})
    assert 10545 not in {x["group_no"] for x in svc.view()["candidates"]}


def test_expired_windows_not_suggested(tmp_path):
    svc, _ev, _net, clock = _full(tmp_path)
    clock.t = dt.datetime(2026, 10, 16, tzinfo=UTC).timestamp()
    assert {c["group_no"] for c in svc.view()["candidates"]} == {10545}


def test_cached_details_revalidated(tmp_path):
    svc, _ev, _net, _clock = _full(tmp_path)
    p = tmp_path / "cache" / eventnotices.DETAILS_FILE
    doc = json.loads(p.read_text(encoding="utf-8"))
    doc["10656"]["window"]["ends"] = {"date": "nope", "edge": "sideways", "time": None}
    doc["junk"] = 5
    p.write_text(json.dumps(doc), encoding="utf-8")
    v = svc.view()
    assert {c["group_no"] for c in v["candidates"]} == {10545}


def test_resolve_uses_maint_module():
    w = {"starts": {"date": "2026-10-01", "edge": "after", "time": None},
         "ends": {"date": "2026-10-15", "edge": "before", "time": None}, "ends_text": "x"}
    r = eventnotices.resolve_window(w, SLOT)
    assert r["ends"] == _iso(*maint.resolve("2026-10-15", "before", SLOT).timetuple()[:5])


# --- routes --------------------------------------------------------------------

def _no_network(url, timeout):
    raise AssertionError("test touched the network")


@pytest.fixture()
def srv(tmp_path):
    clock = Clock()
    net = Net()
    # plan 059 suggest-only paths; plan 064 auto-add has its own tests
    (tmp_path / "local.json").write_text('{"notices": {"auto_add": false}}')
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          sse_interval=0.05, market_seed=[], profile_cfg={},
                          market_client=market.ArshaClient(fetch=_no_network,
                                                           cache_dir=tmp_path / "mcache"),
                          events_clock=clock, config_path=tmp_path / "local.json",
                          notice_client=_client(tmp_path, net, clock), notice_spawn=_sync)
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    s.test_net = net
    s.test_clock = clock
    yield s
    s.shutdown()
    s.server_close()


def _req(s, method, path, body=None):
    c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=5)
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"} if body is not None else {}
    c.request(method, path, body=data, headers=headers)
    r = c.getresponse()
    out = json.loads(r.read())
    c.close()
    return r.status, out


def test_get_events_carries_suggested_events(srv):
    st, body = _req(srv, "GET", "/api/events")
    assert st == 200
    sug = body["suggested_events"]
    assert sug["status"] == "ok" and 10656 in {c["group_no"] for c in sug["candidates"]}
    assert body["items"] == []


def test_add_and_dismiss_via_post(srv):
    _req(srv, "GET", "/api/events")
    n = len(srv.test_net.calls)
    st, body = _req(srv, "POST", "/api/events", {"dismiss_notice": 10656})
    assert st == 200
    assert 10656 not in {c["group_no"] for c in body["suggested_events"]["candidates"]}
    assert len(srv.test_net.calls) == n, "a POST never fetches"
    st, body = _req(srv, "POST", "/api/events", {"dismiss_notice": "x"})
    assert st == 400


def test_maintenance_setting_read_live(srv, tmp_path):
    (tmp_path / "local.json").write_text('{"events": {"maintenance_start_utc": "08:30"}, '
                                         '"notices": {"auto_add": false}}')
    _req(srv, "GET", "/api/events")
    srv.test_clock.t += 6 * 3600
    st, body = _req(srv, "GET", "/api/events")
    sug = body["suggested_events"]
    assert sug["maintenance"]["start_utc"] == "08:30" and sug["maintenance"]["overridden"] is True
    c = next(c for c in sug["candidates"] if c["group_no"] == 10656)
    assert c["ends"] == _iso(2026, 10, 15, 8, 30)


def test_default_make_server_has_notices_disabled(tmp_path):
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", market_seed=[], profile_cfg={},
                          market_client=market.ArshaClient(fetch=_no_network,
                                                           cache_dir=tmp_path / "mcache"))
    try:
        assert s.notices.view(refresh=True)["status"] == "none"
    finally:
        s.server_close()
