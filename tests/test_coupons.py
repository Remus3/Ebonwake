"""Plan 014: coupon suggestions from the official NA news list page.

robots.txt-gated (allow / disallow / unreachable = off), at most one news GET per
6 h, suggest only (nothing is auto-added). Fixture HTML only - no network.
"""

import datetime as dt
import http.client
import json
import threading
from pathlib import Path

import pytest

from server.ew import app as ewapp
from server.ew import coupons, events, market
from server.ew.store import Store

UTC = dt.timezone.utc
T0 = dt.datetime(2026, 10, 5, 12, 0, 0, tzinfo=UTC).timestamp()
FIXTURE = Path(__file__).parent / "fixtures" / "coupons" / "news_list.html"
ROBOTS_ALLOW = b"User-agent: *\nDisallow: /en-US/Account\n"
ROBOTS_DISALLOW = b"User-agent: *\nDisallow: /en-US/News\n"


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


class Net:
    """Fake opener: robots.txt and the news page by URL; records every call."""

    def __init__(self, robots=ROBOTS_ALLOW, page=None):
        self.robots = robots
        self.page = FIXTURE.read_bytes() if page is None else page
        self.calls = []

    def __call__(self, url, timeout):
        self.calls.append(url)
        if url == coupons.ROBOTS_URL:
            body = self.robots
        elif url == coupons.NEWS_URL:
            body = self.page
        else:
            raise AssertionError(f"unexpected url {url}")
        if isinstance(body, Exception):
            raise body
        return body


def _sync(fn):
    fn()


def _client(tmp_path, net, clock):
    return coupons.CouponClient(fetch=net, clock=clock, cache_dir=tmp_path / "cache")


def _svc(tmp_path, net=None, clock=None, store=None):
    clock = clock or Clock()
    net = net or Net()
    store = store or Store(tmp_path / "store")
    ev = events.EventsService(store, clock=clock)
    return coupons.CouponService(_client(tmp_path, net, clock), ev, spawn=_sync), ev, net, clock


# --- constants: one fixed official page, read-only ---------------------------

def test_fixed_official_urls_https_only():
    assert coupons.NEWS_URL.startswith("https://www.naeu.playblackdesert.com/")
    assert coupons.ROBOTS_URL == "https://www.naeu.playblackdesert.com/robots.txt"
    assert coupons.MIN_INTERVAL_S == 6 * 3600
    assert coupons.TTL_S >= coupons.MIN_INTERVAL_S
    assert "Ebonwake" in coupons.USER_AGENT


def test_module_never_posts():
    src = Path(coupons.__file__).read_text(encoding="utf-8")
    assert '"POST"' not in src and "'POST'" not in src
    assert 'method="GET"' in src


# --- robots gate ---------------------------------------------------------------

def test_robots_allow():
    net = Net(robots=ROBOTS_ALLOW)
    assert coupons.robots_verdict(net) == "allow"
    assert net.calls == [coupons.ROBOTS_URL]


@pytest.mark.parametrize("body", [
    ROBOTS_DISALLOW,
    b"User-agent: *\nDisallow: /\n",
    b"User-agent: Ebonwake\nDisallow: /\n\nUser-agent: *\nAllow: /\n",
])
def test_robots_disallow(body):
    assert coupons.robots_verdict(Net(robots=body)) == "disallow"


@pytest.mark.parametrize("err", [OSError("down"), TimeoutError("slow"), ValueError("x")])
def test_robots_unreachable_is_off(err):
    assert coupons.robots_verdict(Net(robots=err)) == "unreachable"


@pytest.mark.parametrize("body", [
    b"", b"\xff\xfe junk\n", b"<!DOCTYPE html><html><body>Page not found</body></html>",
    b"Disallow: /private\n", b"User-agent:\nDisallow: /\n",
])
def test_robots_without_explicit_policy_is_off(body):
    assert coupons.robots_verdict(Net(robots=body)) == "unreachable"


def test_robots_tolerates_bad_bytes_inside_a_policy():
    assert coupons.robots_verdict(Net(robots=b"\xff\xfe junk\nUser-agent: *\nAllow: /\n")) == "allow"


# --- extractor -----------------------------------------------------------------

def test_extract_from_fixture():
    got = coupons.extract(FIXTURE.read_text(encoding="utf-8"))
    by = {c["code"]: c for c in got}
    assert set(by) == {"AUTUMN-2026-GIFT", "HAWKEYE7TREAT", "BDOXDEADEYE2026", "OLDCODE-1234"}
    a = by["AUTUMN-2026-GIFT"]
    assert a["date"] == "2026-10-03"
    assert a["title"] == "[Event] Autumn Coupon Code: AUTUMN-2026-GIFT & More"
    assert a["url"] == ("https://www.naeu.playblackdesert.com/en-US/News/Detail"
                        "?groupContentNo=9001&countryType=en-US")
    assert by["HAWKEYE7TREAT"]["date"] == "2026-10-01"
    assert by["HAWKEYE7TREAT"]["url"].startswith("https://www.naeu.playblackdesert.com/en-US/")
    assert by["OLDCODE-1234"]["date"] == "2026-09-20"
    assert [c["code"] for c in got][:1] == ["AUTUMN-2026-GIFT"], "newest notice first"


def test_extract_skips_non_coupon_offsite_script_and_words():
    codes = {c["code"] for c in coupons.extract(FIXTURE.read_text(encoding="utf-8"))}
    assert "CODE1234ABC" not in codes, "notice without 'coupon'"
    assert "PHISH-CODE-9999" not in codes, "off-site link"
    assert "SCRIPTCODE2026" not in codes, "script text"
    assert not codes & {"EVENT", "COUPON", "BLACK", "DESERT", "MORE"}


NOTICE_DETAIL = """<ul>
<li><a href="/News/Notice/Detail?groupContentNo=9101&amp;countryType=en-US">
  <strong class="title">Coupon Code: FALL-2026-GIFT</strong><span class="date">Oct 8, 2026</span></a></li>
<li><a href="https://www.naeu.playblackdesert.com/en-US/News/Notice/Detail?groupContentNo=9102">
  <strong class="title">Coupon KNOWN-CODE-2026</strong></a></li>
<li><a href="https://evil.example.com/News/Notice/Detail?groupContentNo=9103">
  <strong class="title">Coupon PHISH-CODE-0001</strong></a></li>
<li><a href="/News/Other/Detail?groupContentNo=9104">
  <strong class="title">Coupon WRONG-PATH-0001</strong></a></li>
</ul>"""


def test_extract_reads_the_notice_detail_links():
    """Research 0016: list links moved to /News/Notice/Detail; both shapes read,
    other hosts and paths still dropped."""
    got = coupons.extract(NOTICE_DETAIL)
    by = {c["code"]: c for c in got}
    assert set(by) == {"FALL-2026-GIFT", "KNOWN-CODE-2026"}
    assert by["FALL-2026-GIFT"]["url"] == ("https://www.naeu.playblackdesert.com/News/Notice/"
                                           "Detail?groupContentNo=9101&countryType=en-US")
    assert by["FALL-2026-GIFT"]["date"] == "2026-10-08"
    assert all(coupons.clean_candidate(c) == c for c in got), "cache round-trip keeps them"


@pytest.mark.parametrize("tok,ok", [
    ("ABCD-1234", True), ("HAWKEYE7TREAT", True), ("AB12", True),
    ("EVENT", False), ("MAINTENANCE", False), ("Autumn2026", False), ("ABC1", True),
    ("A1", False), ("-ABCD-", False), ("X" * 39 + "1", True), ("X" * 41 + "1", False),
    ("2026", False), ("10-03-2026", False),
])
def test_code_rule(tok, ok):
    assert coupons.is_code(tok) is ok


@pytest.mark.parametrize("text,want", [
    ("Oct 3, 2026", "2026-10-03"), ("October 13, 2026", "2026-10-13"),
    ("2026-10-01", "2026-10-01"), ("2026.09.20", "2026-09-20"), ("10/02/2026", "2026-10-02"),
    ("no date", None), ("2026-02-30", None), ("Foo 3, 2026", None),
])
def test_date_parse(text, want):
    assert coupons.parse_date(text) == want


def test_extract_tolerates_garbage():
    assert coupons.extract("") == []
    assert coupons.extract("<a href='/en-US/News/Detail?x=1'>coupon <b>") == []
    assert coupons.extract("<a href='/en-US/News/Detail?x=1'>Coupon ZZZZ-9999</a>")[0]["date"] is None


def test_extract_caps_title_and_count():
    item = ("<a href='/en-US/News/Detail?groupContentNo={n}'>Coupon " + "y" * 200 + " C{n:04d}-AAAA</a>")
    html = "".join(item.format(n=n) for n in range(100))
    got = coupons.extract(html)
    assert len(got) == coupons.MAX_CANDIDATES
    assert all(len(c["title"]) <= events.MAX_TITLE for c in got)


# --- client: cache, robots, at most one GET per 6 h ----------------------------

def test_service_lists_candidates_minus_known_codes(tmp_path):
    svc, ev, net, clock = _svc(tmp_path)
    ev.add({"kind": "coupon", "title": "old", "code": "oldcode-1234"})
    v = svc.view(refresh=True)
    assert v["status"] == "ok" and v["robots"] == "allow"
    assert [c["code"] for c in v["candidates"]] == ["AUTUMN-2026-GIFT", "HAWKEYE7TREAT",
                                                     "BDOXDEADEYE2026"]
    assert v["source"] == coupons.NEWS_URL
    assert net.calls == [coupons.ROBOTS_URL, coupons.NEWS_URL]
    assert v["freshness"]["stale"] is False


def test_nothing_auto_added(tmp_path):
    svc, ev, _net, _clock = _svc(tmp_path)
    svc.view(refresh=True)
    assert ev.view()["items"] == []


def test_at_most_one_get_per_6h(tmp_path):
    svc, _ev, net, clock = _svc(tmp_path)
    svc.view(refresh=True)
    for _ in range(5):
        clock.t += 3600
        svc.view(refresh=True)
    assert net.calls.count(coupons.NEWS_URL) == 1
    clock.t = T0 + 6 * 3600
    svc.view(refresh=True)
    assert net.calls.count(coupons.NEWS_URL) == 2


def test_failure_throttled_and_stale(tmp_path):
    svc, _ev, net, clock = _svc(tmp_path)
    svc.view(refresh=True)
    clock.t += 6 * 3600
    net.page = OSError("boom")
    v = svc.view(refresh=True)
    assert v["status"] == "stale" and "boom" in v["error"]
    assert len(v["candidates"]) == 4, "last good list still served"
    assert v["freshness"]["stale"] is True
    n = len(net.calls)
    clock.t += 3 * 3600  # backoff long over; the 6 h attempt floor still holds
    svc.view(refresh=True)
    assert len(net.calls) == n


def test_robots_disallow_turns_feature_off(tmp_path):
    svc, _ev, net, clock = _svc(tmp_path, net=Net(robots=ROBOTS_DISALLOW))
    v = svc.view(refresh=True)
    assert v["status"] == "off" and v["robots"] == "disallow"
    assert v["candidates"] == []
    assert coupons.NEWS_URL not in net.calls, "page never fetched when robots disallows"


def test_robots_unreachable_turns_feature_off(tmp_path):
    svc, _ev, net, _clock = _svc(tmp_path, net=Net(robots=OSError("dns")))
    v = svc.view(refresh=True)
    assert v["status"] == "off" and v["robots"] == "unreachable"
    assert net.calls == [coupons.ROBOTS_URL]


def test_robots_flip_hides_old_candidates(tmp_path):
    svc, _ev, net, clock = _svc(tmp_path)
    assert svc.view(refresh=True)["candidates"]
    clock.t += 6 * 3600
    net.robots = ROBOTS_DISALLOW
    v = svc.view(refresh=True)
    assert v["status"] == "off" and v["candidates"] == []


def test_peek_only_view_never_fetches(tmp_path):
    svc, _ev, net, _clock = _svc(tmp_path)
    v = svc.view(refresh=False)
    assert net.calls == [] and v["status"] == "pending" and v["candidates"] == []
    svc.source()
    assert net.calls == []


def test_disabled_without_client(tmp_path):
    ev = events.EventsService(Store(tmp_path / "store"), clock=Clock())
    svc = coupons.CouponService(None, ev)
    v = svc.view(refresh=True)
    assert v["status"] == "none" and v["candidates"] == []
    assert svc.source()["status"] == "none"


def test_corrupt_cache_and_attempt_files(tmp_path):
    svc, _ev, net, _clock = _svc(tmp_path)
    cache = tmp_path / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / f"{coupons.KEY}.json").write_text("{not json", encoding="utf-8")
    (cache / "coupons_attempt.json").write_text('{"at": "x", "robots": 5}', encoding="utf-8")
    v = svc.view(refresh=True)
    assert v["status"] == "ok" and len(v["candidates"]) == 4


def test_cached_candidates_revalidated(tmp_path):
    svc, _ev, _net, _clock = _svc(tmp_path)
    svc.view(refresh=True)
    p = tmp_path / "cache" / f"{coupons.KEY}.json"
    doc = json.loads(p.read_text(encoding="utf-8"))
    doc["data"]["candidates"].append({"code": "bad code", "title": "x", "url": "javascript:1",
                                      "date": None})
    doc["data"]["candidates"].append("junk")
    p.write_text(json.dumps(doc), encoding="utf-8")
    assert len(svc.view(refresh=False)["candidates"]) == 4


# --- routes --------------------------------------------------------------------

def _no_network(url, timeout):
    raise AssertionError("test touched the network")


@pytest.fixture()
def srv(tmp_path):
    clock = Clock()
    net = Net()
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          sse_interval=0.05, market_seed=[], profile_cfg={},
                          market_client=market.ArshaClient(fetch=_no_network,
                                                           cache_dir=tmp_path / "mcache"),
                          events_clock=clock,
                          coupon_client=_client(tmp_path, net, clock), coupon_spawn=_sync)
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    s.test_net = net
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


def test_get_events_carries_suggested(srv):
    st, body = _req(srv, "GET", "/api/events")
    assert st == 200
    sug = body["suggested"]
    assert sug["status"] == "ok" and len(sug["candidates"]) == 4
    assert body["items"] == []


def test_one_click_add_drops_the_suggestion(srv):
    _req(srv, "GET", "/api/events")
    st, body = _req(srv, "POST", "/api/events",
                    {"add": {"kind": "coupon", "title": "Autumn", "code": "AUTUMN-2026-GIFT"}})
    assert st == 200
    assert "AUTUMN-2026-GIFT" not in {c["code"] for c in body["suggested"]["candidates"]}
    n = srv.test_net.calls.count(coupons.NEWS_URL)
    assert n == 1, "a POST never fetches"


def test_state_sources_coupons(srv):
    st, body = _req(srv, "GET", "/api/state")
    assert st == 200 and body["sources"]["coupons"]["status"] in ("pending", "ok", "off", "stale")
    assert srv.test_net.calls == [], "/api/state never fetches"


def test_default_make_server_has_coupons_disabled(tmp_path):
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", market_seed=[],
                          profile_cfg={},
                          market_client=market.ArshaClient(fetch=_no_network,
                                                           cache_dir=tmp_path / "mcache"))
    try:
        assert s.coupons.view(refresh=True)["status"] == "none"
    finally:
        s.server_close()
