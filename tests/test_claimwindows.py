"""Plan 086: reward claim windows - claim-by deadlines that outlive the event.

Trimmed fixtures of the two live notices (10592 Combat Special: claims to the
Oct 15 maintenance; 10656 Donghwa's coupons: Mail (B) until the Oct 29
maintenance). Fixture HTML only - no network.
"""

import datetime as dt
import http.client
import json
import threading
from pathlib import Path

import pytest

from server.ew import app as ewapp
from server.ew import claimwindows, coupons, eventnotices, events, maintdigest, market, whatnow
from server.ew.store import Store

UTC = dt.timezone.utc
FIX = Path(__file__).parent / "fixtures" / "notices"
COMBAT, COUPON = 10592, 10656
COMBAT_TITLE = "[Combat Special] More Benefits and Loot"
COUPON_TITLE = "[Donghwa's Gift] One Day Only, Time-Limited Coupon"
PAGES = {COMBAT: (FIX / "claim-10592.html").read_text(encoding="utf-8"),
         COUPON: (FIX / "claim-10656.html").read_text(encoding="utf-8")}
SLOT8 = {"weekday": "thu", "start_utc": "08:00", "duration_min": 180}
SLOT7 = {"weekday": "thu", "start_utc": "07:00", "duration_min": 180}
T_FETCH = dt.datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
T_AFTER = dt.datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
T_LATE = dt.datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
COMBAT_TEXT = ("Milestone rewards are sent to the Black Spirit's Safe and can be claimed until "
               "Oct 15, 2026 (Thu) before maintenance.")
COUPON_TEXT = ("Rewards from redeemed coupons are sent to your Mail (B) and stay there until the "
               "Oct 29, 2026 (Thu) maintenance.")


def _utc(*a):
    return dt.datetime(*a, tzinfo=UTC)


def _resolve(slot, notices=None):
    return lambda pt: eventnotices._point_utc(pt, slot, True, notices)


# --- extraction ----------------------------------------------------------------------

def test_combat_special_claim_resolves_from_the_maintenance_slot():
    n = eventnotices.parse_notice(PAGES[COMBAT], COMBAT_TITLE, 2026, 10)
    assert n["claims"] == [{"text": COMBAT_TEXT,
                            "until": {"date": "2026-10-15", "edge": "before", "time": None}}]
    assert n["parse_v"] == maintdigest.PARSE_V == 3
    # the event's own window is the pass sale, which ends a week earlier
    assert n["window"]["ends"] == {"date": "2026-10-08", "edge": "before", "time": None}
    assert claimwindows.latest(n["claims"], _resolve(SLOT8)) == (_utc(2026, 10, 15, 8, 0),
                                                                 COMBAT_TEXT)


def test_coupon_mail_claim_is_the_oct_29_maintenance_start():
    n = eventnotices.parse_notice(PAGES[COUPON], COUPON_TITLE, 2026, 10)
    assert n["claims"] == [{"text": COUPON_TEXT,
                            "until": {"date": "2026-10-29", "edge": "before", "time": None}}]
    assert claimwindows.latest(n["claims"], _resolve(SLOT7))[0] == _utc(2026, 10, 29, 7, 0)
    # an imported maintenance notice for that date beats the weekly slot (plan 064)
    mn = {"2026-10-29": {"start_utc": _utc(2026, 10, 29, 8, 30).isoformat(),
                         "end_utc": _utc(2026, 10, 29, 12, 0).isoformat(), "source": "s"}}
    assert claimwindows.latest(n["claims"], _resolve(SLOT7, mn))[0] == _utc(2026, 10, 29, 8, 30)


def test_sentence_without_a_date_is_dropped():
    pats = claimwindows.load_patterns()
    lines = ["After that, unclaimed rewards are no longer available.",
             "Rewards can be claimed until the end of the season."]
    assert claimwindows.extract(lines, 2026, 10, pats) == []
    assert claimwindows.extract(["Nothing to claim here. Oct 15 is a Thursday."], 2026, 10,
                                pats) == []


def test_point_forms_and_cap():
    pats = claimwindows.load_patterns()
    out = claimwindows.extract([
        "Rewards can be claimed until Oct 22 (Thu) 23:59 (UTC).",
        "Items are claimable until October 30 after maintenance.",
        "Points can be claimed until Nov 5, 2026 (Thu) before maintenance.",
        "Coupons can be claimed until Dec 3, 2026 (Thu) before maintenance.",
    ], 2026, 10, pats)
    assert len(out) == claimwindows.MAX_CLAIMS
    assert [c["until"] for c in out] == [
        {"date": "2026-10-22", "edge": None, "time": "23:59"},
        {"date": "2026-10-30", "edge": "after", "time": None},
        {"date": "2026-11-05", "edge": "before", "time": None}]
    # a yearless date read in December is next January
    jan = claimwindows.extract(["Can be claimed until Jan 7 (Thu) before maintenance."],
                               2026, 12, pats)
    assert jan[0]["until"]["date"] == "2027-01-07"
    # "Mail (B)" / "Level 61" are not dates
    assert claimwindows.points("Mail (B) until Level 61", 2026, 10) == []


def test_text_is_ascii_and_capped():
    pats = claimwindows.load_patterns()
    long = ("Your " + "very " * 60 + "rewards can be claimed until Oct 15, 2026 " + chr(0x2014)
            + " before maintenance.")
    t = claimwindows.extract([long], 2026, 10, pats)[0]["text"]
    assert len(t) == claimwindows.MAX_TEXT and t.isascii() and t.endswith("...")


def test_pattern_file_schema():
    raw = claimwindows.PATTERNS_FILE.read_bytes()
    assert raw.isascii() and b"\r" not in raw
    doc = json.loads(raw)
    assert doc["patterns"] and all(isinstance(p, str) and p.strip() for p in doc["patterns"])
    assert len(claimwindows.load_patterns()) == len(doc["patterns"])


@pytest.mark.parametrize("body", ['{"patterns": []}', '{"patterns": ["("]}', 'not json',
                                  '{"patterns": [""]}'])
def test_bad_pattern_file_raises_and_parse_degrades(tmp_path, body):
    p = tmp_path / "pats.json"
    p.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError):
        claimwindows.load_patterns(p)
    assert claimwindows.claim_lines([COMBAT_TEXT], 2026, 10, path=p) == []


def test_cache_claims_revalidated():
    good = {"text": COMBAT_TEXT, "until": {"date": "2026-10-15", "edge": "before", "time": None}}
    raw = [good, good, {"text": "x", "until": {"date": "2026-13-01", "edge": None, "time": None}},
           {"text": "caf" + chr(0xe9), "until": good["until"]}, "junk",
           {"text": "y", "until": {"date": "2026-10-15", "edge": "during", "time": None}}]
    assert claimwindows.clean_claims(raw) == [good]
    entry = {"stamp": None, "fetched_at": 1.0, "window": None, "claims": raw, "parse_v": 3}
    assert eventnotices._clean_detail(entry)["claims"] == [good]
    assert eventnotices._clean_detail(dict(entry, claims=None))["claims"] == []


# --- store ---------------------------------------------------------------------------

class Clock:
    def __init__(self, when):
        self.t = when.timestamp()

    def __call__(self):
        return self.t

    def at(self, when):
        self.t = when.timestamp()


def _events(tmp_path, when=T_FETCH):
    clock = Clock(when)
    return events.EventsService(Store(tmp_path / "store"), clock=clock), clock


URL = eventnotices.detail_url(COUPON)


def test_claim_equal_to_ends_adds_nothing(tmp_path):
    ev, _ = _events(tmp_path)
    ends = _utc(2026, 10, 15, 7, 0).isoformat()
    ev.add({"kind": "event", "title": "Same", "ends": ends, "url": URL})
    assert claimwindows.keep(_utc(2026, 10, 15, 7, 0), _utc(2026, 10, 15, 7, 0)) is False
    assert ev.sync_claims({URL: (ends, COUPON_TEXT)}) is False
    assert "claim_until" not in ev.view()["items"][0]
    assert claimwindows.keep(_utc(2026, 10, 15, 7, 0), None) is True


def test_claim_sync_decorates_items_of_that_notice_coupons_included(tmp_path):
    ev, clock = _events(tmp_path)
    ends = _utc(2026, 10, 15, 7, 0).isoformat()
    ev.add({"kind": "event", "title": COUPON_TITLE, "ends": ends, "url": URL})
    ev.add({"kind": "coupon", "title": COUPON_TITLE, "code": "DAILY24HCOUPON07",
            "ends": ends, "url": URL})
    ev.add({"kind": "event", "title": "Other", "ends": ends})
    claim = _utc(2026, 10, 29, 7, 0).isoformat()
    assert ev.sync_claims({URL: (claim, COUPON_TEXT)}) is True
    assert ev.sync_claims({URL: (claim, COUPON_TEXT)}) is False  # no change, no write
    rows = {i["title"] + i["kind"]: i for i in ev.view()["items"]}
    for k in (COUPON_TITLE + "event", COUPON_TITLE + "coupon"):
        assert rows[k]["claim_until"] == claim and rows[k]["claim_text"] == COUPON_TEXT
        assert rows[k]["claim_open"] is True
    assert "claim_until" not in rows["Otherevent"]
    clock.at(T_AFTER)  # event over, claim still open: purge keeps it
    out = ev.purge_expired(True)
    assert out["purged"] == 1 and len(out["items"]) == 2
    assert all(i["status"] == "expired" and i["claim_open"] for i in out["items"])


def test_ack_hides_and_a_new_deadline_asks_again(tmp_path):
    ev, clock = _events(tmp_path, T_AFTER)
    ev.add({"kind": "event", "title": COUPON_TITLE, "url": URL,
            "ends": _utc(2026, 10, 15, 7, 0).isoformat()})
    ev.sync_claims({URL: (_utc(2026, 10, 29, 7, 0).isoformat(), COUPON_TEXT)})
    out = ev.claim_ack(URL)
    assert out["items"][0]["claimed"] is True and out["items"][0]["claim_open"] is False
    with pytest.raises(ValueError):
        ev.claim_ack(URL)  # nothing open any more
    # same date re-resolved (maintenance notice imported: 07:00 -> 08:30): ack kept
    ev.sync_claims({URL: (_utc(2026, 10, 29, 8, 30).isoformat(), COUPON_TEXT)})
    it = ev.view()["items"][0]
    assert it["claim_until"] == _utc(2026, 10, 29, 8, 30).isoformat()
    assert it["claimed"] is True and it["claim_open"] is False
    ev.sync_claims({URL: (_utc(2026, 11, 5, 7, 0).isoformat(), COUPON_TEXT)})
    assert ev.view()["items"][0]["claim_open"] is True


def test_notice_that_no_longer_holds_a_claim_clears_it(tmp_path):
    ev, _ = _events(tmp_path, T_AFTER)
    ev.add({"kind": "event", "title": COUPON_TITLE, "url": URL,
            "ends": _utc(2026, 10, 15, 7, 0).isoformat()})
    ev.sync_claims({URL: (_utc(2026, 10, 29, 7, 0).isoformat(), COUPON_TEXT)})
    assert ev.sync_claims({"https://other/1": None}) is False  # other notices: untouched
    assert ev.sync_claims({URL: None}) is True
    assert "claim_until" not in ev.view()["items"][0]
    # the service maps a read without claims to None, an unread notice to nothing
    n = [{"group_no": COUPON, "url": URL}, {"group_no": COMBAT, "url": "u2"}]
    det = {COUPON: {"claims": []}}
    assert eventnotices.NoticeService.claim_windows(n, det, SLOT7, {}) == {URL: None}


# --- What now ----------------------------------------------------------------------

def _item(title, until, url=None, **kw):
    return dict({"id": "e1", "kind": "event", "title": title, "url": url, "done": False,
                 "status": "expired", "claim_until": until.isoformat(),
                 "claim_text": "sent to your Mail (B) until x", "claim_open": True}, **kw)


def test_whatnow_ranks_a_1_day_claim_above_a_3_day_one():
    now = T_LATE
    view = {"items": [_item("Far", now + dt.timedelta(seconds=259000), "https://a/1"),
                      _item("Near", now + dt.timedelta(days=1), "https://a/2"),
                      _item("Near", now + dt.timedelta(days=1), "https://a/2", kind="coupon"),
                      _item("Gone", now - dt.timedelta(hours=1), "https://a/3"),
                      _item("Acked", now + dt.timedelta(hours=5), "https://a/4", claimed=True,
                            claim_open=False)]}
    cands = whatnow.claims(view, now)
    assert [c["text"] for c in cands] == ["Claim Far rewards (Mail) - 3 days left",
                                          "Claim Near rewards (Mail) - 1 day left"]
    r = whatnow.rank(cands, whatnow.load_weights())
    assert r["top"]["text"].startswith("Claim Near") and r["top"]["source"] == "claim"
    assert r["next"][0]["text"].startswith("Claim Far")
    # beyond the 3-day horizon: dropped
    far = whatnow.claims({"items": [_item("X", now + dt.timedelta(days=4))]}, now)
    assert whatnow.rank(far, whatnow.load_weights())["top"] is None


def test_whatnow_claim_text_fits_and_names_the_place():
    assert whatnow.claim_place("in the Black Spirit's Safe") == "Safe"
    assert whatnow.claim_place("Mail (B)") == "Mail"
    assert whatnow.claim_place("rewards") == "Mail / Safe"
    c = whatnow.claims({"items": [_item("T" * 80, T_LATE + dt.timedelta(days=2))]}, T_LATE)[0]
    assert len(c["text"]) <= whatnow.TEXT_MAX and c["text"].endswith("- 2 days left")
    assert whatnow.load_weights()["claim"] == {"weight": 0.9, "horizon_s": 259200,
                                               "nominal_s": 86400}


def test_digest_lists_a_claim_window_ending_at_the_next_maintenance():
    start = _utc(2026, 10, 15, 7, 0)
    items = [_item(COMBAT_TITLE, start, "https://a/1", done=True, ends=_utc(2026, 10, 8, 7, 0).isoformat()),
             _item(COMBAT_TITLE, start, "https://a/1", kind="coupon"),
             _item("Later", start + dt.timedelta(days=14), "https://a/2"),
             _item("Acked", start, "https://a/3", claimed=True)]
    out = maintdigest.ending(items, [], _utc(2026, 10, 14, 12, 0), start)
    assert out == [{"kind": "claim", "title": COMBAT_TITLE, "ends": start.isoformat()}]


# --- acceptance over HTTP --------------------------------------------------------------

ROBOTS = b"User-agent: *\nDisallow: /en-US/Account\n"


def _a(no, title):
    return (f"<li><a href='/en-US/News/Detail?groupContentNo={no}'>"
            f"<strong class='title'>{title}</strong></a></li>")


class Net:
    def __init__(self):
        self.boards = {1: b"<ul></ul>", 2: b"<ul></ul>",
                       3: ("<ul>" + _a(COMBAT, COMBAT_TITLE) + _a(COUPON, COUPON_TITLE)
                           + "</ul>").encode()}

    def __call__(self, url, timeout):
        boards = {u: b for b, u in eventnotices.LIST_URLS.items()}
        if url == coupons.ROBOTS_URL:
            return ROBOTS
        if url in boards:
            return self.boards[boards[url]]
        if url.startswith(eventnotices.DETAIL_URL):
            return PAGES[int(url.split("groupContentNo=")[1].split("&")[0])].encode()
        raise AssertionError(f"unexpected url {url}")


def _no_network(url, timeout):
    raise AssertionError("test touched the network")


@pytest.fixture
def srv(tmp_path):
    clock = Clock(T_FETCH)
    (tmp_path / "local.json").write_text("{}", encoding="ascii")
    client = eventnotices.NoticeClient(fetch=Net(), clock=clock, cache_dir=tmp_path / "cache")
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          sse_interval=0.05, market_seed=[], profile_cfg={},
                          market_client=market.ArshaClient(fetch=_no_network,
                                                           cache_dir=tmp_path / "mcache"),
                          events_clock=clock, leveling_clock=clock, today_clock=clock,
                          config_path=tmp_path / "local.json",
                          notice_client=client, notice_spawn=lambda fn: fn())
    s.test_clock = clock
    t = threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    t.start()
    yield s
    s.shutdown()
    s.server_close()


def _req(s, method, path, body=None):
    c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=5)
    data = None if body is None else json.dumps(body).encode()
    c.request(method, path, body=data,
              headers={"Content-Type": "application/json"} if data is not None else {})
    r = c.getresponse()
    out = json.loads(r.read())
    c.close()
    return r.status, out


def _acts(wn):
    return ([wn["top"]] if wn["top"] else []) + wn["next"]


def test_acceptance_events_whatnow_and_ack(srv):
    st, v = _req(srv, "GET", "/api/events")  # list + Details read, auto-added, claims synced
    assert st == 200
    by = {i["title"]: i for i in v["items"]}
    assert by[COUPON_TITLE]["claim_until"] == _utc(2026, 10, 29, 7, 0).isoformat()
    assert by[COMBAT_TITLE]["claim_until"] == _utc(2026, 10, 15, 7, 0).isoformat()
    assert by[COUPON_TITLE]["claim_text"] == COUPON_TEXT

    srv.test_clock.at(T_AFTER)
    st, v = _req(srv, "GET", "/api/events")
    by = {i["title"]: i for i in v["items"]}
    assert by[COUPON_TITLE]["status"] == "expired" and by[COUPON_TITLE]["claim_open"] is True
    assert by[COMBAT_TITLE]["claim_open"] is False  # the Oct 15 claim cut-off is past
    st, wn = _req(srv, "GET", "/api/whatnow")
    assert all(a["source"] != "claim" for a in _acts(wn))

    srv.test_clock.at(T_LATE)
    st, wn = _req(srv, "GET", "/api/whatnow")
    claim = [a for a in _acts(wn) if a["source"] == "claim"]
    assert len(claim) == 1  # one row per notice, coupons included
    t = claim[0]["text"]  # the long title is clipped to fit the 80-char action text
    assert t.startswith("Claim [Donghwa's Gift] One Day Only")
    assert t.endswith("... rewards (Mail) - 2 days left") and len(t) <= whatnow.TEXT_MAX
    assert claim[0]["due"] == _utc(2026, 10, 29, 7, 0).isoformat()

    st, out = _req(srv, "POST", "/api/events", {"claimed": COUPON})
    assert st == 200
    assert {i["title"]: i for i in out["items"]}[COUPON_TITLE]["claim_open"] is False
    st, wn = _req(srv, "GET", "/api/whatnow")
    assert all(a["source"] != "claim" for a in _acts(wn))
    assert _req(srv, "POST", "/api/events", {"claimed": COUPON})[0] == 400
    assert _req(srv, "POST", "/api/events", {"claimed": "10656"})[0] == 400
    assert _req(srv, "POST", "/api/events", {"claimed": COMBAT})[0] == 400  # already past
