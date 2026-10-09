"""Plan 064: official notice auto-import - maintenance UTC, Hot Time, coupons, undo.

Notices (1) and Updates (2) boards beside plan 059's Events board (3), the
same robots gate, attempt floor and Detail budget. Fixture HTML only - no
network.
"""

import datetime as dt
import http.client
import json
import threading
from pathlib import Path

import pytest

from server.ew import app as ewapp
from server.ew import coupons, eventnotices, events, leveling, maint, market, settings
from server.ew.store import Store

UTC = dt.timezone.utc
T0 = dt.datetime(2026, 10, 6, 12, 0, 0, tzinfo=UTC).timestamp()
FIX = Path(__file__).parent / "fixtures" / "notices"
ROBOTS_ALLOW = b"User-agent: *\nDisallow: /en-US/Account\n"
ROBOTS_DISALLOW = b"User-agent: *\nDisallow: /en-US/News\n"
SLOT = {"weekday": "thu", "start_utc": "07:00", "duration_min": 180}

MAINT, HOT, COUPON, PLAIN = 20001, 20002, 20003, 20004
DETAILS = {MAINT: "maint_dst.html", HOT: "hot_time.html", COUPON: "coupon_word.html"}


def _a(no, title, stamp=None):
    s = f"<span class='date'>Last Updated: {stamp}</span>" if stamp else ""
    return (f"<li><a href='/en-US/News/Detail?groupContentNo={no}'>"
            f"<strong class='title'>{title}</strong>{s}</a></li>")


BOARD1 = ("<ul>" + _a(MAINT, "Oct 8 (Thu) Maintenance")
          + _a(COUPON, "[Coupon] Autumn Gift Coupon") + "</ul>").encode()
BOARD2 = b"<ul></ul>"
BOARD3 = ("<ul>" + _a(HOT, "[Event] Hot Time Returns")
          + _a(PLAIN, "[Event] Plain Event") + "</ul>").encode()
PLAIN_PAGE = (b"<div class='contents_area'><p>Oct 6, 2026 00:00 (UTC) - Oct 30, 2026 "
              b"23:59 (UTC)</p></div>")
STEAM_RSS = (b"<?xml version='1.0'?><rss><channel><title>Steam</title>"
             b"<item><title>Black Desert - Patch Notes Oct 8</title><link>x</link></item>"
             b"<item><title><![CDATA[Hot Time &amp; more]]></title></item>"
             b"</channel></rss>")


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


class Net:
    """Fake opener over the three boards, Detail pages and the Steam store."""

    def __init__(self, robots=ROBOTS_ALLOW):
        self.robots = robots
        self.boards = {1: BOARD1, 2: BOARD2, 3: BOARD3}
        self.details = {n: (FIX / f).read_bytes() for n, f in DETAILS.items()}
        self.details[PLAIN] = PLAIN_PAGE
        self.steam_robots = b"User-agent: *\nDisallow: /account\n"
        self.steam = STEAM_RSS
        self.calls = []

    def __call__(self, url, timeout):
        self.calls.append(url)
        boards = {u: b for b, u in eventnotices.LIST_URLS.items()}
        if url == coupons.ROBOTS_URL:
            body = self.robots
        elif url in boards:
            body = self.boards[boards[url]]
        elif url.startswith(eventnotices.DETAIL_URL):
            body = self.details[int(url.split("groupContentNo=")[1].split("&")[0])]
        elif url == eventnotices.STEAM_ROBOTS:
            body = self.steam_robots
        elif url == eventnotices.STEAM_RSS:
            body = self.steam
        else:
            raise AssertionError(f"unexpected url {url}")
        if isinstance(body, Exception):
            raise body
        return body

    def detail_calls(self):
        return [u for u in self.calls if u.startswith(eventnotices.DETAIL_URL)]


def _sync(fn):
    fn()


def _svc(tmp_path, net=None, clock=None, auto=True):
    clock = clock or Clock()
    net = net or Net()
    store = Store(tmp_path / "store")
    ev = events.EventsService(store, clock=clock)
    lv = leveling.LevelingService(store, clock=clock, epochs=[], deadlines=[])
    flag = {"on": auto}
    client = eventnotices.NoticeClient(fetch=net, clock=clock, cache_dir=tmp_path / "cache")
    svc = eventnotices.NoticeService(client, ev, store, spawn=_sync,
                                     slot_path=tmp_path / "noslot.json", leveling=lv,
                                     auto_add=lambda: flag["on"])
    return svc, ev, lv, net, clock, flag


def _iso(*a):
    return dt.datetime(*a, tzinfo=UTC).isoformat()


def _read(p):
    return (FIX / p).read_text(encoding="utf-8")


# --- constants -------------------------------------------------------------------

def test_three_boards_one_host_robots_cover_all():
    assert set(eventnotices.LIST_URLS) == {1, 2, 3}
    assert eventnotices.LIST_URLS[3] == eventnotices.LIST_URL
    for b, u in eventnotices.LIST_URLS.items():
        assert u == f"https://www.naeu.playblackdesert.com/en-US/News/Notice?boardType={b}"
        assert u in eventnotices.ROBOT_URLS
    assert eventnotices.STEAM_RSS == "https://store.steampowered.com/feeds/news/app/582660/"
    assert "api.steampowered.com" not in Path(eventnotices.__file__).read_text(encoding="utf-8")


# --- maintenance: UTC column by header text ---------------------------------------

@pytest.mark.parametrize("fixture", ["maint_dst.html", "maint_std.html"])
def test_maintenance_same_utc_window_in_both_column_orders(fixture):
    m = eventnotices.parse_maint(_read(fixture), "Oct 8 (Thu) Maintenance", 2026, 10)
    assert m == {"date": "2026-10-08", "start_utc": _iso(2026, 10, 8, 8, 0),
                 "end_utc": _iso(2026, 10, 8, 12, 30)}


@pytest.mark.parametrize("title,want", [
    ("Oct 8 (Thu) Maintenance", "2026-10-08"),
    ("October 8 (Thu.) Maintenance Completed", "2026-10-08"),
    ("[Notice] Oct 8, 2026 (Thu) Maintenance", "2026-10-08"),
    ("Jan 7 (Thu) Maintenance", "2027-01-07"),  # weekday picks the year
    ("Maintenance on Oct 8", None),
    ("Oct 8 (Thu) Patch Notes", None),
])
def test_maintenance_title(title, want):
    d = eventnotices.maint_title_date(title, 2026, 10)
    assert (d.isoformat() if d else None) == want


def test_maintenance_table_edge_cases():
    head = "<table><tr><th>PDT (UTC-7)</th><th>UTC</th></tr>"
    t = "Oct 8 (Thu) Maintenance"
    wrap = head + "<tr><td>x</td><td>22:00 - 02:00</td></tr></table>"
    assert eventnotices.parse_maint(wrap, t, 2026, 10) == {
        "date": "2026-10-08", "start_utc": _iso(2026, 10, 8, 22, 0),
        "end_utc": _iso(2026, 10, 9, 2, 0)}
    ampm = head + "<tr><td>x</td><td>7:00 AM - 11:00 AM</td></tr></table>"
    assert eventnotices.parse_maint(ampm, t, 2026, 10)["end_utc"] == _iso(2026, 10, 8, 11, 0)
    no_utc = "<table><tr><th>PDT (UTC-7)</th></tr><tr><td>0:00 - 4:00</td></tr></table>"
    assert eventnotices.parse_maint(no_utc, t, 2026, 10) is None
    assert eventnotices.parse_maint(head + "<tr><td>a</td><td>TBD</td></tr></table>", t,
                                    2026, 10) is None
    assert eventnotices.parse_maint(_read("maint_dst.html"), "Patch Notes", 2026, 10) is None


def test_maint_resolve_prefers_a_notice_for_that_date():
    mn = maint.clean_notices({"notices": [
        {"date": "2026-10-08", "start_utc": _iso(2026, 10, 8, 8, 0),
         "end_utc": _iso(2026, 10, 8, 12, 30), "source": "https://x/1"},
        {"date": "2026-10-09", "start_utc": _iso(2026, 10, 8, 8, 0),  # wrong date: dropped
         "end_utc": _iso(2026, 10, 8, 9, 0), "source": "s"},
        {"date": "2026-10-10", "start_utc": "junk", "end_utc": "x", "source": "s"},
    ]})
    assert list(mn) == ["2026-10-08"]
    assert maint.resolve("2026-10-08", "before", SLOT, mn) == dt.datetime(2026, 10, 8, 8, 0,
                                                                          tzinfo=UTC)
    assert maint.resolve("2026-10-08", "after", SLOT, mn) == dt.datetime(2026, 10, 8, 12, 30,
                                                                         tzinfo=UTC)
    assert maint.resolve("2026-10-15", "before", SLOT, mn) == dt.datetime(2026, 10, 15, 7, 0,
                                                                          tzinfo=UTC)


# --- Hot Time + coupons parse ------------------------------------------------------

def test_hot_time_parse_prefers_combat_and_accepts_the_comma():
    lines = eventnotices._lines(_read("hot_time.html"))
    h = eventnotices.parse_hot(lines, "[Event] Hot Time Returns", 2026, 10)
    assert h == {"bonus": "Combat EXP +1,000%", "pct": 1000,
                 "ends": {"date": "2026-10-22", "edge": None, "time": None}}
    assert eventnotices.parse_hot(["Combat EXP +100%"], "Patch notes", 2026, 10) is None
    assert eventnotices.parse_hot(["Hot Time", "Combat EXP +2,000%"], "", 2026, 10) is None
    assert eventnotices.parse_hot(["Hot Time", "Skill EXP +50%"], "", 2026, 10)["pct"] == 50


def test_word_style_coupon_beside_copy_button():
    page = _read("coupon_word.html")
    assert coupons.copy_codes(page) == ["AUTUMNGIFT2026"]
    n = eventnotices.parse_notice(page, "[Coupon] Autumn Gift Coupon", 2026, 10)
    assert n["codes"] == ["AUTUMNGIFT2026", "EWTEST-1234-ABCD"]
    assert n["window"]["ends"] == {"date": "2026-10-20", "edge": None, "time": "23:59"}
    assert coupons.is_word_code("AUTUMNGIFT2026") and coupons.is_word_code("abcdefgh")
    for bad in ("GIFT", "12345678", "A" * 25, "ABCD-EFGH", "", None):
        assert not coupons.is_word_code(bad)
    assert coupons.is_code("ABCD-1234-EFGH"), "plan 014 dashed shape kept"


def test_rss_titles_only():
    assert eventnotices.parse_rss_titles(STEAM_RSS.decode()) == [
        "Black Desert - Patch Notes Oct 8", "Hot Time & more"]
    assert eventnotices.parse_rss_titles("garbage") == []


# --- client: boards, budget, robots ------------------------------------------------

def test_robots_off_means_no_get(tmp_path):
    svc, ev, lv, net, _clock, _flag = _svc(tmp_path, net=Net(robots=ROBOTS_DISALLOW))
    v = svc.view(refresh=True)
    assert v["status"] == "off" and net.calls == [coupons.ROBOTS_URL]
    assert ev.view()["items"] == [] and lv.view()["hot_auto"] == []


def test_per_run_get_cap_three_lists_five_details(tmp_path):
    net = Net()
    many = "".join(_a(30000 + i, f"Event {i}") for i in range(12))
    net.boards[3] = ("<ul>" + many + "</ul>").encode()
    for i in range(12):
        net.details[30000 + i] = PLAIN_PAGE
    svc, *_ = _svc(tmp_path, net=net)
    svc.view(refresh=True)
    lists = [u for u in net.calls if u in eventnotices.LIST_URLS.values()]
    assert sorted(lists) == sorted(eventnotices.LIST_URLS.values())
    assert len(net.detail_calls()) == eventnotices.MAX_DETAILS
    svc.view(refresh=True)
    assert len(net.detail_calls()) == eventnotices.MAX_DETAILS, "floor: no second run"


def test_maintenance_and_hot_time_fetched_first(tmp_path):
    net = Net()
    many = "".join(_a(30000 + i, f"Event {i}") for i in range(8))
    net.boards[3] = ("<ul>" + many + _a(HOT, "[Event] Hot Time Returns") + "</ul>").encode()
    for i in range(8):
        net.details[30000 + i] = PLAIN_PAGE
    svc, *_ = _svc(tmp_path, net=net)
    svc.view(refresh=True)
    got = [int(u.split("groupContentNo=")[1].split("&")[0]) for u in net.detail_calls()]
    assert got[:2] == [MAINT, HOT]


def test_cache_key_is_group_no_plus_stamp(tmp_path):
    net = Net()
    net.boards[1] = ("<ul>" + _a(MAINT, "Oct 8 (Thu) Maintenance", "Oct 5, 2026") + "</ul>").encode()
    svc, _ev, _lv, net, clock, _flag = _svc(tmp_path, net=net)
    svc.view(refresh=True)
    n = net.detail_calls().count(eventnotices.detail_url(MAINT))
    clock.t += 6 * 3600
    svc.view(refresh=True)
    assert net.detail_calls().count(eventnotices.detail_url(MAINT)) == n
    net.boards[1] = net.boards[1].replace(b"Oct 5", b"Oct 6")
    clock.t += 6 * 3600
    svc.view(refresh=True)
    assert net.detail_calls().count(eventnotices.detail_url(MAINT)) == n + 1


# --- import: maintenance, auto-add, undo, dismiss ----------------------------------

def test_full_import(tmp_path):
    svc, ev, lv, _net, clock, _flag = _svc(tmp_path)
    v = svc.view(refresh=True)
    assert v["status"] == "ok" and v["auto_add"] is True
    assert v["maint_notices"] == [{"date": "2026-10-08", "start_utc": _iso(2026, 10, 8, 8, 0),
                                   "end_utc": _iso(2026, 10, 8, 12, 30),
                                   "source": eventnotices.detail_url(MAINT)}]
    items = ev.view()["items"]
    by = {i["code"]: i for i in items if i["kind"] == "coupon"}
    assert all(i.get("auto") is True for i in items)
    # Hot Time: an Events-board window -> event + an auto window starting after
    # the NOTICE's maintenance end (12:30), not the slot's (10:00); the
    # maintenance notice itself is never an event
    titles = {i["title"] for i in items if i["kind"] == "event"}
    assert titles == {"[Event] Hot Time Returns", "[Event] Plain Event"}
    wins = lv.view()["hot_auto"]
    assert len(wins) == 1
    w = wins[0]
    assert (w["start"], w["end"]) == (_iso(2026, 10, 8, 12, 30), _iso(2026, 10, 22, 7, 0))
    assert w["pct"] == 1000 and w["bonus"] == "Combat EXP +1,000%" and w["auto"] is True
    assert w["source"] == eventnotices.detail_url(HOT) and w["group_no"] == HOT
    # coupons: both codes, with the notice window and link
    assert set(by) == {"AUTUMNGIFT2026", "EWTEST-1234-ABCD"}
    assert by["AUTUMNGIFT2026"]["ends"] == _iso(2026, 10, 20, 23, 59)
    assert by["EWTEST-1234-ABCD"]["url"] == eventnotices.detail_url(COUPON)
    assert {a["group_no"] for a in v["auto"]} == {HOT, COUPON, PLAIN}
    assert v["candidates"] == []
    # the Leveling card: upcoming, then active with the stack
    assert lv.view()["hot"]["next"]["id"] == w["id"]
    clock.t = dt.datetime(2026, 10, 9, tzinfo=UTC).timestamp()
    lview = lv.view()
    assert [a["id"] for a in lview["hot"]["active"]] == [w["id"]]
    assert lview["xp_stack_pct"] == 1000


def test_import_is_idempotent(tmp_path):
    svc, ev, lv, _net, clock, _flag = _svc(tmp_path)
    svc.view(refresh=True)
    n, h = len(ev.view()["items"]), len(lv.view()["hot_auto"])
    for _ in range(3):
        clock.t += 6 * 3600
        svc.view(refresh=True)
    assert len(ev.view()["items"]) == n and len(lv.view()["hot_auto"]) == h


def test_undo_removes_and_dismissed_is_never_readded(tmp_path):
    svc, ev, lv, _net, clock, _flag = _svc(tmp_path)
    svc.view(refresh=True)
    v = svc.undo(HOT)
    assert HOT in v["dismissed"] and HOT not in {a["group_no"] for a in v["auto"]}
    assert lv.view()["hot_auto"] == []
    assert "[Event] Hot Time Returns" not in {i["title"] for i in ev.view()["items"]}
    clock.t += 6 * 3600
    v = svc.view(refresh=True)
    assert lv.view()["hot_auto"] == [] and HOT not in {c["group_no"] for c in v["candidates"]}
    with pytest.raises(ValueError):
        svc.undo(HOT)
    with pytest.raises(ValueError):
        svc.undo("20002")


def test_dismissed_before_import_is_never_added(tmp_path):
    svc, ev, lv, net, clock, flag = _svc(tmp_path, auto=False)
    svc.view(refresh=True)
    assert ev.view()["items"] == [] and lv.view()["hot_auto"] == []
    assert {c["group_no"] for c in svc.view()["candidates"]} == {HOT, PLAIN}, "suggested"
    svc.dismiss(PLAIN)
    flag["on"] = True
    svc.view()
    titles = {i["title"] for i in ev.view()["items"]}
    assert "[Event] Plain Event" not in titles and "[Event] Hot Time Returns" in titles


def test_undo_tolerates_items_deleted_by_hand(tmp_path):
    svc, ev, _lv, _net, _clock, _flag = _svc(tmp_path)
    svc.view(refresh=True)
    iid = next(i["id"] for i in ev.view()["items"] if i["title"] == "[Event] Plain Event")
    ev.delete(iid)
    svc.undo(PLAIN)
    svc.view()
    assert "[Event] Plain Event" not in {i["title"] for i in ev.view()["items"]}


def test_auto_add_off_still_imports_maintenance(tmp_path):
    svc, ev, _lv, _net, _clock, _flag = _svc(tmp_path, auto=False)
    v = svc.view(refresh=True)
    assert v["auto_add"] is False and ev.view()["items"] == []
    assert [m["date"] for m in v["maint_notices"]] == ["2026-10-08"]
    hot = next(c for c in v["candidates"] if c["group_no"] == HOT)
    assert hot["starts"] == _iso(2026, 10, 8, 12, 30), "suggestions resolve via the notice too"


def test_partial_hot_time_is_not_added(tmp_path):
    net = Net()
    net.details[HOT] = (b"<p>Hot Time!</p><p>Ends Oct 22</p><p>Combat EXP +300%</p>")
    svc, _ev, lv, *_ = _svc(tmp_path, net=net)
    svc.view(refresh=True)
    assert lv.view()["hot_auto"] == [], "no start (no window line, no list stamp)"


def test_hot_time_ends_line_with_list_stamp_is_full(tmp_path):
    net = Net()
    net.boards[3] = ("<ul>" + _a(HOT, "[Event] Hot Time Returns", "Oct 6, 2026")
                     + "</ul>").encode()
    net.details[HOT] = (b"<p>Hot Time!</p><p>Ends Oct 22</p><p>Combat EXP +300%</p>")
    svc, _ev, lv, *_ = _svc(tmp_path, net=net)
    svc.view(refresh=True)
    w = lv.view()["hot_auto"][0]
    assert (w["start"], w["end"], w["pct"]) == (_iso(2026, 10, 6), _iso(2026, 10, 22, 23, 59, 59),
                                                300)


def test_partial_coupon_stays_a_suggestion(tmp_path):
    net = Net()
    net.details[COUPON] = (b"<span>NOWINDOWCODE1</span>"
                           b"<button class='js-btnCopyCoupon'>Copy</button>")
    svc, ev, *_ = _svc(tmp_path, net=net)
    svc.view(refresh=True)
    assert not any(i["kind"] == "coupon" for i in ev.view()["items"])
    assert {c["code"] for c in svc.coupon_extra()} == {"NOWINDOWCODE1"}
    cs = coupons.CouponService(None, ev, extra=svc.coupon_extra)
    assert cs.view()["candidates"] == [], "coupons.check off: no suggestions at all"


def test_corrupt_ledger_and_maint_domain_degrade(tmp_path):
    svc, ev, _lv, _net, _clock, _flag = _svc(tmp_path)
    svc.store.put(eventnotices.DOMAIN, {"dismissed": "x", "auto": [{"group_no": "y"}, 5]})
    svc.store.put(eventnotices.MAINT_DOMAIN, {"notices": [{"date": 3}]})
    v = svc.view(refresh=True)
    assert v["status"] == "ok" and len(v["auto"]) == 3


# --- Steam backup ----------------------------------------------------------------

def test_steam_hint_only_after_24h_unreachable(tmp_path):
    net = Net()
    net.boards[3] = OSError("down")
    svc, _ev, _lv, net, clock, _flag = _svc(tmp_path, net=net)
    v = svc.view(refresh=True)
    assert v["status"] == "error" and v["steam_hint"] is None
    assert eventnotices.STEAM_RSS not in net.calls
    for _ in range(4):
        clock.t += 6 * 3600
        v = svc.view(refresh=True)
    assert net.calls.count(eventnotices.STEAM_RSS) == 1
    assert v["steam_hint"]["titles"] == ["Black Desert - Patch Notes Oct 8", "Hot Time & more"]
    assert {u for u in net.calls if "steampowered" in u} == {eventnotices.STEAM_ROBOTS,
                                                             eventnotices.STEAM_RSS}
    net.boards[3] = BOARD3
    clock.t += 6 * 3600
    v = svc.view(refresh=True)
    assert v["status"] == "ok" and v["steam_hint"] is None


def test_official_robots_disallow_clears_the_failure_clock(tmp_path):
    net = Net(robots=OSError("dns"))
    svc, _ev, _lv, net, clock, _flag = _svc(tmp_path, net=net)
    for _ in range(5):
        svc.view(refresh=True)
        clock.t += 6 * 3600
    assert svc.view()["steam_hint"] is not None
    net.robots = ROBOTS_DISALLOW
    svc.view(refresh=True)
    assert svc.view()["steam_hint"] is None and svc.client.failing_since() is None


def test_maint_domain_keeps_newest_dates_without_rewrites(tmp_path):
    svc, *_ = _svc(tmp_path)
    old = [{"date": f"2026-0{m}-0{d}", "start_utc": _iso(2026, m, d, 7), "end_utc":
            _iso(2026, m, d, 9), "source": "https://x/y"} for m in range(1, 9) for d in range(1, 5)]
    svc.store.put(eventnotices.MAINT_DOMAIN, {"notices": old})
    svc.view(refresh=True)
    kept = svc.maint_notices()
    assert len(kept) == eventnotices.MAX_MAINT and "2026-10-08" in kept
    p = next((tmp_path / "store").glob(f"{eventnotices.MAINT_DOMAIN}*"))
    before = p.stat().st_mtime_ns
    svc.view()
    assert p.stat().st_mtime_ns == before, "no rewrite when nothing changed"


def test_steam_robots_disallow_reads_no_feed(tmp_path):
    net = Net(robots=OSError("dns"))
    net.steam_robots = b"User-agent: *\nDisallow: /feeds/\n"
    svc, _ev, _lv, net, clock, _flag = _svc(tmp_path, net=net)
    for _ in range(5):
        svc.view(refresh=True)
        clock.t += 6 * 3600
    assert eventnotices.STEAM_ROBOTS in net.calls and eventnotices.STEAM_RSS not in net.calls
    assert svc.view()["steam_hint"]["titles"] == []


# --- settings + routes -----------------------------------------------------------

def test_setting_fixed_on_not_settable():
    # Plan 080: fixed on; config false is only a 24 h incident switch.
    assert settings.fixed()["notices.auto_add"] is True
    with pytest.raises(ValueError, match="not a settable key"):
        settings.validate({"set": {"notices.auto_add": False}})


def _no_network(url, timeout):
    raise AssertionError("test touched the network")


@pytest.fixture()
def srv(tmp_path):
    clock = Clock()
    net = Net()
    client = eventnotices.NoticeClient(fetch=net, clock=clock, cache_dir=tmp_path / "cache")
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          sse_interval=0.05, market_seed=[], profile_cfg={},
                          market_client=market.ArshaClient(fetch=_no_network,
                                                           cache_dir=tmp_path / "mcache"),
                          events_clock=clock, leveling_clock=clock,
                          config_path=tmp_path / "local.json",
                          notice_client=client, notice_spawn=_sync)
    t = threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    t.start()
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


def test_routes_auto_add_leveling_card_and_undo(srv):
    st, body = _req(srv, "GET", "/api/events")
    assert st == 200
    titles = {i["title"] for i in body["items"]}
    assert "[Event] Hot Time Returns" in titles, "the GET body already holds the import"
    assert {a["group_no"] for a in body["suggested_events"]["auto"]} == {HOT, COUPON, PLAIN}
    st, lv = _req(srv, "GET", "/api/leveling")
    assert [w["group_no"] for w in lv["hot_auto"]] == [HOT]
    assert lv["hot"]["next"]["pct"] == 1000
    st, body = _req(srv, "POST", "/api/events", {"undo_notice": HOT})
    assert st == 200 and "[Event] Hot Time Returns" not in {i["title"] for i in body["items"]}
    st, lv = _req(srv, "GET", "/api/leveling")
    assert lv["hot_auto"] == []
    st, _ = _req(srv, "POST", "/api/events", {"undo_notice": HOT})
    assert st == 400
    st, _ = _req(srv, "POST", "/api/events", {"undo_notice": "x"})
    assert st == 400


def test_auto_add_setting_off_via_config(srv, tmp_path):
    (tmp_path / "local.json").write_text('{"notices": {"auto_add": false}}')
    st, body = _req(srv, "GET", "/api/events")
    assert body["items"] == [] and body["suggested_events"]["auto_add"] is False
    assert {c["group_no"] for c in body["suggested_events"]["candidates"]} == {HOT, PLAIN}
