"""Plan 074: before-maintenance digest - loss warnings from maintenance notices,
everything ending at the next maintenance, ack, What now `maint_loss`.

Fixture HTML only (the 2026-10-08 notice, groupContentNo 10663, trimmed) - no
network.
"""

import datetime as dt
import http.client
import json
import threading
from pathlib import Path

import pytest

from server.ew import app as ewapp
from server.ew import (coupons, eventnotices, maintdigest, market, progress, prompts, settings,
                       whatnow)
from server.ew.store import Store

UTC = dt.timezone.utc
FIX = Path(__file__).parent / "fixtures" / "notices"
PAGE = (FIX / "maint-20261008.html").read_text(encoding="utf-8")
TITLE = "[Maintenance] October 8 (Thu) Maintenance"
NO = 10663
TAG = ("All unclaimed EXP in the Tag Characters UI will be deleted when the update goes "
       "live, so please claim it beforehand.")
START = dt.datetime(2026, 10, 8, 8, 0, tzinfo=UTC)
END = dt.datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
T_ACCEPT = dt.datetime(2026, 10, 7, 12, 0, tzinfo=UTC)  # T-20 h
T_EARLY = dt.datetime(2026, 10, 6, 12, 0, tzinfo=UTC)  # T-44 h
SLOT = {"weekday": "thu", "start_utc": "07:00", "duration_min": 180}
NOTICES = {"2026-10-08": {"start_utc": START.isoformat(), "end_utc": END.isoformat(),
                          "source": eventnotices.detail_url(NO)}}


class Clock:
    def __init__(self, when):
        self.t = when.timestamp()

    def __call__(self):
        return self.t

    def at(self, when):
        self.t = when.timestamp()


# --- extraction ----------------------------------------------------------------------

def test_fixture_yields_maintenance_and_the_tag_warning():
    n = eventnotices.parse_notice(PAGE, TITLE, 2026, 10)
    assert n["maint"] == {"date": "2026-10-08", "start_utc": START.isoformat(),
                          "end_utc": END.isoformat()}
    assert n["loss"] == [TAG]
    assert n["parse_v"] == maintdigest.PARSE_V


def test_notice_without_loss_sentence_yields_none():
    page = (FIX / "maint_std.html").read_text(encoding="utf-8")
    n = eventnotices.parse_notice(page, "Oct 8 (Thu) Maintenance", 2026, 10)
    assert n["maint"] is not None and n["loss"] == []


def test_loss_is_read_from_maintenance_notices_only():
    page = "<p>Event items will be deleted after the event ends.</p>"
    assert eventnotices.parse_notice(page, "[Event] Autumn Hunt", 2026, 10)["loss"] == []


def test_extract_cap_ascii_and_length():
    pats = maintdigest.load_patterns()
    lines = [f"Item {i} will be deleted at the update." for i in range(9)]
    out = maintdigest.extract_loss(lines, pats)
    assert len(out) == maintdigest.MAX_LOSS
    long = "Your " + "very " * 80 + "old coupons will be removed " + chr(0x2014) + " claim them " + \
        chr(0x201c) + "now" + chr(0x201d) + "."
    t = maintdigest.extract_loss([long], pats)[0]
    assert len(t) == maintdigest.MAX_TEXT and t.isascii() and t.endswith("...")
    assert maintdigest.extract_loss(["Nothing happens here. All fine."], pats) == []
    two = "Servers will restart. Unclaimed rewards cannot be recovered afterwards."
    assert maintdigest.extract_loss([two], pats) == ["Unclaimed rewards cannot be recovered afterwards."]


def test_abbreviation_period_ends_no_sentence():
    pats = maintdigest.load_patterns()
    line = ("Servers close at 08:00. Unclaimed EXP will be deleted during the Oct. 8 (Thu.) "
            "maintenance, e.g. Tag EXP, so claim it. Thanks.")
    assert maintdigest.extract_loss([line], pats) == [
        "Unclaimed EXP will be deleted during the Oct. 8 (Thu.) maintenance, e.g. Tag EXP, "
        "so claim it."]


def test_pattern_file_schema():
    raw = maintdigest.PATTERNS_FILE.read_bytes()
    assert raw.isascii() and b"\r" not in raw
    doc = json.loads(raw)
    assert doc["patterns"] and all(isinstance(p, str) and p.strip() for p in doc["patterns"])
    assert len(maintdigest.load_patterns()) == len(doc["patterns"])


@pytest.mark.parametrize("body", ['{"patterns": []}', '{"patterns": ["("]}', '{"x": 1}',
                                  'not json', '{"patterns": [""]}'])
def test_bad_pattern_file_raises_and_parse_degrades(tmp_path, body):
    p = tmp_path / "pats.json"
    p.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError):
        maintdigest.load_patterns(p)
    assert maintdigest.loss_lines([TAG], path=p) == []


def test_detail_cache_keeps_loss_and_old_entries_are_stale_once(tmp_path):
    cached = {"stamp": None, "fetched_at": 1.0, "window": None, "codes": [], "hot": None,
              "maint": {"date": "2026-10-08", "start_utc": START.isoformat(),
                        "end_utc": END.isoformat()}}
    c = eventnotices._clean_detail(cached)
    assert c["loss"] == [] and c["parse_v"] == 1
    client = eventnotices.NoticeClient(fetch=lambda u, t: b"", clock=lambda: 2.0,
                                       cache_dir=tmp_path)
    notice = {"title": TITLE, "stamp": None}
    assert client._stale(notice, c, 2.0) is True
    fresh = eventnotices._clean_detail(dict(cached, loss=[TAG], parse_v=maintdigest.PARSE_V))
    assert fresh["loss"] == [TAG] and client._stale(notice, fresh, 2.0) is False
    # plan 086 (parse_v 3: claim windows) re-reads any older entry once, event or not
    assert client._stale({"title": "[Event] Plain", "stamp": None}, c, 2.0) is True
    assert client._stale({"title": "[Event] Plain", "stamp": None}, fresh, 2.0) is False
    bad = eventnotices._clean_detail(dict(cached, loss=["caf" + chr(0xe9), 5, "x" * 300, TAG]))
    assert bad["loss"] == [TAG]


# --- ending list -----------------------------------------------------------------------

@pytest.mark.parametrize("ends,want", [
    (START, True),
    (START + dt.timedelta(hours=1), True),
    (START + dt.timedelta(hours=1, seconds=1), False),
    (START - dt.timedelta(hours=1), True),
    (START - dt.timedelta(hours=1, seconds=1), False),
    (START + dt.timedelta(days=7), False),
])
def test_ending_edges(ends, want):
    items = [{"kind": "event", "title": "Autumn hunt", "ends": ends.isoformat(), "done": False}]
    out = maintdigest.ending(items, [], T_ACCEPT, START)
    assert bool(out) is want


def test_ending_skips_done_and_past_and_reads_hot_windows():
    near = START.isoformat()
    items = [{"kind": "coupon", "title": "Gift", "ends": near, "done": False},
             {"kind": "event", "title": "Done one", "ends": near, "done": True},
             {"kind": "event", "title": "No end", "ends": None, "done": False}]
    hot = [{"label": "Hot Time", "bonus": "Combat EXP +100%", "end": near}]
    out = maintdigest.ending(items, hot, T_ACCEPT, START)
    assert out == [{"kind": "coupon", "title": "Gift", "ends": near},
                   {"kind": "hot", "title": "Hot Time Combat EXP +100%", "ends": near}]
    assert maintdigest.ending(items, hot, START + dt.timedelta(minutes=1), START) == []


def test_weekly_hot_window_ending_at_maintenance():
    # plan 011: Thu (3) 04:00-08:00 UTC ends at the 08:00 start; Wed one does not
    wins = [{"id": "h1", "days": [3], "start": "04:00", "end": "08:00", "label": "Thu Hot Time",
             "pct": 50},
            {"id": "h2", "days": [2], "start": "20:00", "end": "22:00", "label": "Wed", "pct": 10},
            {"id": "h3", "days": [3], "start": "07:30", "end": "07:30", "label": "Zero", "pct": 5},
            {"id": "bad", "days": [3], "start": "25:00", "end": "08:00"}]
    occ = maintdigest.weekly_hot(wins, T_ACCEPT, START)
    out = maintdigest.ending([], occ, T_ACCEPT, START)
    assert out == [{"kind": "hot", "title": "Thu Hot Time +50%", "ends": START.isoformat()}]


def test_service_reads_weekly_hot_windows(tmp_path):
    d = maintdigest.DigestService(
        Store(tmp_path / "store"), loss_rows=lambda: [], items=lambda: [], hot=lambda: [],
        weekly=lambda: [{"days": [3], "start": "06:00", "end": "08:30", "label": "Hot Time",
                         "pct": 100}],
        maint_inputs=lambda: {"slot": SLOT, "notices": NOTICES}, clock=Clock(T_ACCEPT))
    assert [e["title"] for e in d.view()["ending"]] == ["Hot Time +100%"]


# --- service: show window, ack ------------------------------------------------------------

def _digest(tmp_path, clock, rows=None, items=None, multi=True):
    # multi=True: the Tag sentence exercises the mechanics; one-character
    # suppression has its own tests below
    rows = rows if rows is not None else [{"notice_no": NO, "url": eventnotices.detail_url(NO),
                                           "due_utc": START.isoformat(), "loss": [TAG]}]
    return maintdigest.DigestService(
        Store(tmp_path / "store"), loss_rows=lambda: rows, items=lambda: items or [],
        hot=lambda: [], maint_inputs=lambda: {"slot": SLOT, "notices": NOTICES}, clock=clock,
        multi_character=lambda: multi)


def test_digest_shows_from_t_minus_24h(tmp_path):
    clock = Clock(T_ACCEPT)
    d = _digest(tmp_path, clock, items=[{"kind": "event", "title": "Autumn hunt",
                                         "ends": START.isoformat(), "done": False}])
    v = d.view()
    assert v["show"] is True and v["in_s"] == 20 * 3600
    assert v["maint"] == {"start_utc": START.isoformat(), "end_utc": END.isoformat(),
                          "source": eventnotices.detail_url(NO)}
    assert [(w["text"], w["notice_no"], w["due_utc"]) for w in v["warnings"]] == [
        (TAG, NO, "2026-10-08T08:00:00+00:00")]
    assert v["ending"] == [{"kind": "event", "title": "Autumn hunt", "ends": START.isoformat()}]
    clock.at(T_EARLY)
    assert d.view()["show"] is False


def test_ack_hides_until_the_next_maintenance(tmp_path):
    clock = Clock(T_ACCEPT)
    rows = [{"notice_no": NO, "url": eventnotices.detail_url(NO), "due_utc": START.isoformat(),
             "loss": [TAG]}]
    d = _digest(tmp_path, clock, rows=rows)
    key = d.view()["warnings"][0]["key"]
    assert key == maintdigest.warning_key(NO, TAG)
    v = d.ack(key)
    assert v["warnings"] == [] and [w["key"] for w in v["acked"]] == [key]
    assert d.unacked() == []
    # the same sentence due a week later (same key) shows again once this one is over
    nxt = START + dt.timedelta(days=7)
    rows[0]["due_utc"] = nxt.isoformat()
    clock.at(END + dt.timedelta(minutes=1))
    assert [w["key"] for w in d.view()["warnings"]] == [key]


@pytest.mark.parametrize("key", ["nope", "10663:zz", 5, None, "10663:000000000000"])
def test_ack_rejects_bad_or_unknown_keys(tmp_path, key):
    d = _digest(tmp_path, Clock(T_ACCEPT))
    with pytest.raises(ValueError):
        d.ack(key)


def test_warning_past_due_is_gone(tmp_path):
    d = _digest(tmp_path, Clock(START + dt.timedelta(seconds=1)))
    assert d.view()["warnings"] == []


# --- one character: Tag / alt-only warnings suppressed -------------------------------------

def test_multi_character_patterns_load_and_match_tag_and_alts():
    pats = maintdigest.multi_character_patterns()
    assert pats and all(p.pattern.isascii() for p in pats)
    for text in (TAG, "Unclaimed Tag EXP will be deleted.", "Tagged character EXP will be reset.",
                 "Items on alt characters will be removed.", "Claim it on your alts before."):
        assert any(p.search(text) for p in pats), text
    for text in ("All unclaimed Black Spirit's Pass rewards will be deleted.",
                 "Coupons will no longer be available.", "Claim your Vantage rewards before."):
        assert not any(p.search(text) for p in pats), text


OTHER = "All unclaimed Season Pass rewards will be deleted after the maintenance."


def test_one_character_suppresses_tag_warning_only(tmp_path):
    rows = [{"notice_no": NO, "url": eventnotices.detail_url(NO), "due_utc": START.isoformat(),
             "loss": [TAG, OTHER]}]
    d = _digest(tmp_path, Clock(T_ACCEPT), rows=rows, multi=False)
    v = d.view()
    assert [w["text"] for w in v["warnings"]] == [OTHER]
    assert [w["text"] for w in v["suppressed"]] == [TAG]
    assert [w["text"] for w in d.unacked()] == [OTHER]
    with pytest.raises(ValueError):
        d.ack(maintdigest.warning_key(NO, TAG))
    on = _digest(tmp_path / "multi", Clock(T_ACCEPT), rows=rows, multi=True).view()
    assert [w["text"] for w in on["warnings"]] == [TAG, OTHER] and on["suppressed"] == []


def test_unreadable_multi_character_list_suppresses_nothing(tmp_path):
    bad = tmp_path / "p.json"
    bad.write_text('{"patterns": ["x"]}', encoding="ascii")
    assert maintdigest.multi_character_patterns(bad) == []
    w = [{"text": TAG}]
    assert maintdigest.split_applicable(w, False, []) == (w, [])


def test_unreadable_setting_means_one_character(tmp_path):
    def boom():
        raise RuntimeError("settings unreadable")
    rows = [{"notice_no": NO, "url": "u", "due_utc": START.isoformat(), "loss": [TAG]}]
    d = maintdigest.DigestService(
        Store(tmp_path / "store"), loss_rows=lambda: rows, items=lambda: [], hot=lambda: [],
        maint_inputs=lambda: {"slot": SLOT, "notices": NOTICES}, clock=Clock(T_ACCEPT),
        multi_character=boom)
    v = d.view()
    assert v["warnings"] == [] and [w["text"] for w in v["suppressed"]] == [TAG]


# --- What now ------------------------------------------------------------------------------

def test_whatnow_ranks_maint_loss_above_maint(tmp_path):
    now = START - dt.timedelta(hours=1)
    d = _digest(tmp_path, Clock(now))
    out = whatnow.collect({"maint": lambda: {"slot": SLOT, "notices": NOTICES},
                           "maint_digest": d.view}, now, whatnow.load_weights())
    assert out["top"]["source"] == "maint_loss"
    assert out["top"]["text"].startswith("Before maintenance: All unclaimed EXP")
    assert len(out["top"]["text"]) <= whatnow.TEXT_MAX
    assert out["top"]["due"] == START.isoformat()
    assert [a["source"] for a in out["next"]] == ["maint"]


def test_whatnow_event_rows_rank_and_maint_loss_waits_for_t24(tmp_path):
    items = {"items": [{"kind": "event", "title": "Autumn hunt", "ends": START.isoformat(),
                        "status": "active", "done": False}]}
    d = _digest(tmp_path, Clock(T_ACCEPT))
    inputs = {"maint_digest": d.view, "events": lambda: items,
              "maint": lambda: {"slot": SLOT, "notices": NOTICES}}
    out = whatnow.collect(inputs, T_ACCEPT, whatnow.load_weights())
    assert out["top"]["source"] == "maint_loss"
    assert [a["source"] for a in out["next"]] == ["event"]
    assert out["next"][0]["text"] == "Autumn hunt ends"
    d2 = _digest(tmp_path / "early", Clock(T_EARLY))
    early = whatnow.collect(dict(inputs, maint_digest=d2.view), T_EARLY, whatnow.load_weights())
    assert early["top"] is None


# --- notify: closed toasts pass the quiet gate ----------------------------------------------

def test_gate_lets_closed_hits_through_while_quiet():
    cfg = prompts.load_config()
    hits = [{"key": "maintLoss:a:24h", "rule": "maintLoss", "closed": True},
            {"key": "maintLoss:a:15", "rule": "maintLoss", "ladder": True}]
    fire, drop = prompts.gate(hits, "not_running", cfg)
    assert [h["key"] for h in fire] == ["maintLoss:a:24h"]
    assert [h["key"] for h in drop] == ["maintLoss:a:15"]


# --- acceptance through the server -------------------------------------------------------------

ROBOTS = b"User-agent: *\nDisallow: /en-US/Account\n"


def _a(no, title):
    return (f"<li><a href='/en-US/News/Detail?groupContentNo={no}'>"
            f"<strong class='title'>{title}</strong></a></li>")


class Net:
    def __init__(self):
        self.boards = {1: ("<ul>" + _a(NO, TITLE) + "</ul>").encode(), 2: b"<ul></ul>",
                       3: b"<ul></ul>"}

    def __call__(self, url, timeout):
        boards = {u: b for b, u in eventnotices.LIST_URLS.items()}
        if url == coupons.ROBOTS_URL:
            return ROBOTS
        if url in boards:
            return self.boards[boards[url]]
        if url.startswith(eventnotices.DETAIL_URL) and f"groupContentNo={NO}&" in url:
            return PAGE.encode()
        raise AssertionError(f"unexpected url {url}")


def _no_network(url, timeout):
    raise AssertionError("test touched the network")


@pytest.fixture(params=[True])
def srv(tmp_path, request):
    clock = Clock(T_ACCEPT)
    (tmp_path / "local.json").write_text(
        json.dumps({"profile": {"multi_character": request.param}}), encoding="ascii")
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


def test_acceptance_digest_whatnow_and_ack(srv):
    st, _ = _req(srv, "GET", "/api/events")  # plan 064 read (sync spawn) + import
    assert st == 200
    st, _ = _req(srv, "POST", "/api/events", {"add": {
        "kind": "event", "title": "Autumn hunt", "ends": START.isoformat()}})
    assert st == 200
    st, v = _req(srv, "GET", "/api/maint/digest")
    assert st == 200 and v["show"] is True
    assert v["maint"]["start_utc"] == START.isoformat()
    assert [w["text"] for w in v["warnings"]] == [TAG]
    assert v["warnings"][0]["due_utc"] == START.isoformat()
    assert [e["title"] for e in v["ending"]] == ["Autumn hunt"]
    st, wn = _req(srv, "GET", "/api/whatnow")
    assert wn["top"]["source"] == "maint_loss", wn
    st, p = _req(srv, "GET", "/api/prompts")
    assert [t["key"] for t in p["maint_loss"]] == ["maintLoss:" + v["warnings"][0]["key"]]
    st, a = _req(srv, "POST", "/api/maint/digest", {"ack": v["warnings"][0]["key"]})
    assert st == 200 and a["warnings"] == [] and len(a["acked"]) == 1
    st, p = _req(srv, "GET", "/api/prompts")
    assert p["maint_loss"] == [], "acked warnings never notify"
    st, e = _req(srv, "POST", "/api/maint/digest", {"ack": "1:000000000000"})
    assert st == 400
    st, e = _req(srv, "POST", "/api/maint/digest", {"nope": 1})
    assert st == 400


def test_acceptance_nothing_shows_at_t_minus_44h(srv):
    srv.test_clock.at(T_EARLY)
    _req(srv, "GET", "/api/events")
    st, v = _req(srv, "GET", "/api/maint/digest")
    assert st == 200 and v["show"] is False
    st, wn = _req(srv, "GET", "/api/whatnow")
    acts = ([wn["top"]] if wn["top"] else []) + wn["next"]
    assert all(a["source"] != "maint_loss" for a in acts)


@pytest.mark.parametrize("srv", [False], indirect=True)
def test_acceptance_one_character_hides_tag_warning(srv):
    _req(srv, "GET", "/api/events")
    st, v = _req(srv, "GET", "/api/maint/digest")
    assert st == 200 and v["show"] is True
    assert v["warnings"] == [] and [w["text"] for w in v["suppressed"]] == [TAG]
    st, wn = _req(srv, "GET", "/api/whatnow")
    acts = ([wn["top"]] if wn["top"] else []) + wn["next"]
    assert all(a["source"] != "maint_loss" for a in acts)
    st, p = _req(srv, "GET", "/api/prompts")
    assert p["maint_loss"] == []


def _profile_refresh(srv, names):
    # Plan 041 history beside the store: one row per character of a refresh.
    at = "2026-10-06T12:00:00+00:00"
    path = srv.progress.history.path
    path.write_text("".join(json.dumps({"at": at, "name": n, "main": i == 0}) + "\n"
                            for i, n in enumerate(names)), encoding="ascii")


def test_typed_multi_character_is_a_badged_expiring_override(srv):
    # Zero-touch (research 0010 / plan 079): a forgotten config true shows a
    # badge and expires after 30 days instead of skewing the digest forever.
    st, o = _req(srv, "GET", "/api/overrides")
    row = next(i for i in o["items"] if i["key"] == "profile.multi_character")
    assert row["value"] is True and "shell" in row["cards"]
    assert row["expires_in_s"] is not None and 29 * 86400 < row["expires_in_s"] <= 30 * 86400


def test_one_character_profile_supersedes_typed_multi_character(srv):
    _profile_refresh(srv, ["Deadeye"])
    _req(srv, "GET", "/api/events")
    st, v = _req(srv, "GET", "/api/maint/digest")
    assert st == 200 and v["warnings"] == [] and [w["text"] for w in v["suppressed"]] == [TAG]
    st, o = _req(srv, "GET", "/api/overrides")
    assert all(i["key"] != "profile.multi_character" for i in o["items"])


@pytest.mark.parametrize("srv", [False], indirect=True)
def test_several_profile_characters_show_tag_warning(srv):
    _profile_refresh(srv, ["Deadeye", "Alt"])
    _req(srv, "GET", "/api/events")
    st, v = _req(srv, "GET", "/api/maint/digest")
    assert st == 200 and [w["text"] for w in v["warnings"]] == [TAG]


def test_character_count_reads_newest_refresh(tmp_path):
    h = progress.ProfileHistory(tmp_path / "h.jsonl")
    assert h.character_count() is None
    (tmp_path / "h.jsonl").write_text(
        "".join(json.dumps({"at": at, "name": n}) + "\n" for at, n in (
            ("2026-10-01T00:00:00+00:00", "A"), ("2026-10-01T00:00:00+00:00", "B"),
            ("2026-10-02T00:00:00+00:00", "A"))), encoding="ascii")
    assert h.character_count() == 1


def test_settings_default_is_one_character():
    assert settings.defaults()["profile.multi_character"] is False


def test_post_route_registered():
    assert ewapp.Handler.POST_ROUTES["/api/maint/digest"] is ewapp.Handler._post_maint_digest
