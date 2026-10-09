"""Plan 085: patch-notes data verifier - hints, verdicts, runtime state, digest row.

Fixture HTML only (`tests/fixtures/notices/patch-20261008.html`, reconstructed
from research 0013 s1); no network, tracked data never written.
"""

import datetime as dt
import hashlib
import http.client
import json
import shutil
import threading
from pathlib import Path

import pytest

from server.ew import app as ewapp
from server.ew import dataverdicts, eventnotices, grind, levels, market, patchverify, signals
from server.ew import leveling
from server.ew.store import Store

UTC = dt.timezone.utc
FIX = Path(__file__).parent / "fixtures" / "notices"
DATA = patchverify.DATA_DIR
TITLE = "[Updates] Patch Notes - October 8, 2026"
PATCH_NO = 10670
EPOCH = "xp_epochs.json#cap75-xp-rescale"
BLESS = "xp_buffs.json#adventure-blessing"
T0 = dt.datetime(2026, 10, 8, 14, 0, 0, tzinfo=UTC).timestamp()


def _text():
    raw = (FIX / "patch-20261008.html").read_text(encoding="utf-8")
    return "\n".join(eventnotices._lines(raw))


def _note(no=PATCH_NO, title=TITLE, text=None, at=T0, stamp=None):
    return {"group_no": no, "title": title, "url": eventnotices.detail_url(no), "stamp": stamp,
            "fetched_at": at, "text": _text() if text is None else text}


def _stale_dir(tmp_path):
    """A copy of the tracked data with Adventure Blessing seeded at its stale
    pre-patch value (15 %), hinted for that value."""
    d = tmp_path / "data"
    shutil.copytree(DATA, d)
    p = d / "xp_buffs.json"
    rows = json.loads(p.read_text(encoding="ascii"))
    for r in rows:
        if r["id"] == "adventure-blessing":
            to = r"(?:\bto|->|=>)\s*\+?"
            r["xp_pct"], r["pre_patch_xp_pct"] = 15, 10
            r["verify"] = {"title": r"^\[Updates\] Patch Notes\b",
                           "expect": ["Adventure Blessing[^\\n]{0,80}?" + to + "15 ?%"],
                           "contradict": ["Adventure Blessing[^\\n]{0,80}?" + to + "(?!15 ?%)[0-9]{1,4} ?%"]}
    p.write_text(json.dumps(rows), encoding="ascii")
    return d


def _digest(root):
    h = hashlib.sha256()
    for p in sorted(Path(root).rglob("*.json")):
        h.update(p.relative_to(root).as_posix().encode() + p.read_bytes())
    return h.hexdigest()


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("test touched the network")
    monkeypatch.setattr("urllib.request.urlopen", boom)


# --- hints / schema --------------------------------------------------------------

def test_every_tracked_hint_compiles_and_loaders_accept_it():
    res = patchverify.load()
    assert {EPOCH, BLESS, "xp_buffs.json#body-enhancement", "xp_buffs.json#pearl-outfit-set",
            "drop_buffs.json#item-collection-scroll", "drop_buffs.json#adv-item-collection-scroll",
            "drop_buffs.json#ecology-knowledge", "drop_buffs.json#agris-fever"} <= set(res["hinted"])
    for k in res["hinted"]:
        assert k not in res["unchecked"]
    # the loaders of the hinted files still accept them
    levels.load_epochs()
    levels.load_buff_presets()
    grind.load_drop_data()


def test_row_without_hint_is_unchecked():
    res = patchverify.load()
    assert "enhance_rates.json" in " ".join(res["unchecked"])
    assert all("#" in k for k in res["unchecked"])


@pytest.mark.parametrize("hint, msg", [
    ({"title": "x", "expect": ["(unclosed"]}, "bad regex"),
    ({"title": "[", "expect": ["a"]}, "bad regex"),
    ({"title": "x"}, "at least one"),
    ({"title": "x", "expect": ["a"] * 5}, "at most 4"),
    ({"title": "x", "expect": ["caf" + chr(0xE9)]}, "ASCII"),
    ({"title": "x", "expect": ["a"], "other": 1}, "verify must be"),
    ("x", "verify must be"),
])
def test_bad_hint_rejected_at_load(tmp_path, hint, msg):
    d = tmp_path / "data"
    d.mkdir()
    (d / "t.json").write_text(json.dumps([{"id": "r1", "verified": False, "verify": hint}]),
                              encoding="ascii")
    with pytest.raises(ValueError, match=msg):
        patchverify.load(d)
    row = json.loads((DATA / "xp_buffs.json").read_text(encoding="ascii"))[0]
    with pytest.raises(ValueError):
        levels.validate_preset(dict(row, verify=hint))


def test_verify_only_on_a_data_row(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    (d / "t.json").write_text(json.dumps({"x": {"verify": {"title": "a", "expect": ["b"]}}}),
                              encoding="ascii")
    with pytest.raises(ValueError, match="only on a data row"):
        patchverify.load(d)


def test_row_key_without_id_uses_json_path():
    rows = dict(patchverify.data_rows({"caps": {"verified": False}, "b": [{"verified": 1}]}, "f.json"))
    assert set(rows) == {"f.json#caps", "f.json#b/0"}


# --- reader ------------------------------------------------------------------------

def test_fixture_confirms_epoch_and_presets():
    res = patchverify.check(_text(), TITLE, patchverify.load()["hinted"])
    assert res[EPOCH]["verdict"] == "confirmed"
    assert "Lv. 75" in res[EPOCH]["evidence"]
    for rid in ("body-enhancement", "adventure-blessing", "pearl-outfit-set"):
        assert res[f"xp_buffs.json#{rid}"]["verdict"] == "confirmed", rid
    for k, v in res.items():
        if k.startswith("drop_buffs.json#"):
            assert v == {"verdict": "silent", "evidence": None}


def test_stale_preset_is_contradicted(tmp_path):
    res = patchverify.check(_text(), TITLE, patchverify.load(_stale_dir(tmp_path))["hinted"])
    assert res[BLESS]["verdict"] == "contradicted"
    assert res[BLESS]["evidence"] == "Adventure Blessing: Combat EXP gain increased from 15% to 30%."


def test_title_gate_and_epoch_date():
    hinted = patchverify.load()["hinted"]
    other = patchverify.check(_text(), "[Updates] Patch Notes - October 15, 2026", hinted)
    assert EPOCH not in other, "the epoch hint names its own patch date"
    assert other[BLESS]["verdict"] == "confirmed"
    assert patchverify.check(_text(), "[Event] Hot Time", hinted) == {}


BARE = "Patch Notes - October 8, 2026"


@pytest.mark.parametrize("title,want", [
    (TITLE, True), (BARE, True), ("[Updates]Patch Notes - October 8, 2026", True),
    ("patch notes - October 8, 2026", True), ("[Event] Hot Time", False),
    ("October 2 Maintenance Patch Notes", False), ("[Event] Patch Notes", False), (None, False),
])
def test_bare_list_title_is_a_patch_title(title, want):
    """Research 0016 post-merge: the live list carries the bare title; only the
    Detail page <title> has the [Updates] prefix."""
    assert patchverify.is_patch_title(title) is want


def test_bare_title_meets_the_prefixed_hints():
    hinted = patchverify.load()["hinted"]
    assert patchverify.check(_text(), BARE, hinted) == patchverify.check(_text(), TITLE, hinted)
    assert patchverify.check(_text(), BARE, hinted)[EPOCH]["verdict"] == "confirmed"
    assert patchverify.check(_text(), "[Event] Patch Notes - October 8, 2026", hinted) == {}


def test_contradict_wins_and_evidence_is_short_ascii():
    hint = patchverify.compile_hint({"title": "x", "expect": ["foo"], "contradict": ["bar"]})
    text = "foo\n" + ("z" * 300) + " bar " + chr(0x2014) + " " + ("y" * 300)
    r = patchverify.check_row(text, hint)
    assert r["verdict"] == "contradicted" and "bar" in r["evidence"]
    assert len(r["evidence"]) <= patchverify.MAX_EVIDENCE
    assert all(32 <= ord(c) < 127 for c in r["evidence"])
    assert patchverify.check_row("nothing", hint)["verdict"] == "silent"


def test_second_value_on_the_line_is_not_a_contradiction():
    """Refute r1 #3: the gap stops at the first `to`, so a later "to 20%" on the
    same line never contradicts a correct row."""
    hinted = patchverify.load()["hinted"]
    text = ("Body Enhancement: Combat EXP gain increased from 50% to 100%, "
            "Skill EXP from 10% to 20%.")
    r = patchverify.check(text, TITLE, hinted)["xp_buffs.json#body-enhancement"]
    assert r["verdict"] == "confirmed"
    bad = text.replace("to 100%", "to 90%")
    assert patchverify.check(bad, TITLE, hinted)["xp_buffs.json#body-enhancement"]["verdict"] \
        == "contradicted"
    lvl = "The maximum character level has been raised from Lv. 70 to Lv. 75, Life to 80."
    assert patchverify.check(lvl, TITLE, hinted)[EPOCH]["verdict"] == "confirmed"
    # refute r2: the gap BEFORE the keyword must not run on to the next item
    drop = ("Item Collection Increase Scroll item amount changed to 50%; Advanced Item "
            "Collection Increase Scroll item amount changed to 100%. Ecology item drop rate "
            "is now +30%, Agris Fever item amount to 50%.")
    res = patchverify.check(drop, TITLE, hinted)
    for rid in ("item-collection-scroll", "adv-item-collection-scroll", "ecology-knowledge",
                "agris-fever"):
        assert res[f"drop_buffs.json#{rid}"]["verdict"] == "confirmed", rid
    assert patchverify.check(drop.replace("to 50%;", "to 40%;"), TITLE, hinted)[
        "drop_buffs.json#item-collection-scroll"]["verdict"] == "contradicted"
    # refute r3: an expect gap never borrows the next item's value
    loose = ("Character level display fixed; weight limit raised to 75 LT. "
             "Item Collection Increase Scroll item amount is 30%; Ecology +50%")
    res = patchverify.check(loose, TITLE, hinted)
    assert res[EPOCH]["verdict"] == "silent"
    assert res["drop_buffs.json#item-collection-scroll"]["verdict"] == "silent"


def test_note_date():
    assert patchverify.note_date(TITLE) == "2026-10-08"
    assert patchverify.note_date("[Updates] Patch Notes", "2026-10-09") == "2026-10-09"
    assert patchverify.note_date("[Updates] Patch Notes") is None
    assert patchverify.note_date("Patch Notes - February 31, 2026", "2026-02-27") == "2026-02-27"


# --- runtime verdicts ----------------------------------------------------------------

def test_verdict_file_round_trip_and_tracked_data_untouched(tmp_path):
    before = _digest(DATA)
    path = tmp_path / "data_verdicts.json"
    notes = [_note()]
    svc = dataverdicts.VerdictService(lambda: notes, path=path)
    v = svc.view()
    assert v["verdicts"][EPOCH]["verdict"] == "confirmed"
    assert v["verdicts"][EPOCH]["notice_no"] == PATCH_NO
    assert v["verdicts"][EPOCH]["url"] == eventnotices.detail_url(PATCH_NO)
    assert v["verdicts"][EPOCH]["date"] == "2026-10-08"
    assert v["verdicts"][EPOCH]["checked_at"] == "2026-10-08T14:00:00+00:00"
    assert v["last_notice"]["notice_no"] == PATCH_NO and v["error"] is None
    assert v["unchecked"] and "fp" not in v["verdicts"][EPOCH]
    on_disk = json.loads(path.read_text())
    assert on_disk["verdicts"][EPOCH]["verdict"] == "confirmed"
    # a new instance with the patch notes gone from the cache keeps the verdicts
    again = dataverdicts.VerdictService(lambda: [], path=path)
    assert again.view()["verdicts"] == v["verdicts"]
    assert again.verdict(EPOCH)["date"] == "2026-10-08"
    assert _digest(DATA) == before


def test_row_change_drops_its_old_verdict(tmp_path):
    path = tmp_path / "v.json"
    dataverdicts.VerdictService(lambda: [_note()], path=path).run()
    stale = dataverdicts.VerdictService(lambda: [], path=path, data_dir=_stale_dir(tmp_path))
    assert BLESS not in stale.view()["verdicts"], "the row changed: its verdict is void"
    assert EPOCH in stale.view()["verdicts"]


def test_newer_notice_wins_silent_never_erases(tmp_path):
    hinted = patchverify.load()["hinted"]
    v = {}
    dataverdicts.apply(v, hinted, _note())
    newer = _note(no=PATCH_NO + 5, title="[Updates] Patch Notes - October 15, 2026",
                  text="Adventure Blessing: Combat EXP gain changed from 30% to 40%.",
                  at=T0 + 7 * 86400)
    dataverdicts.apply(v, hinted, newer)
    assert v[BLESS]["verdict"] == "contradicted" and v[BLESS]["notice_no"] == PATCH_NO + 5
    assert v[EPOCH]["verdict"] == "confirmed", "the Oct 15 page says nothing on the epoch"
    quiet = _note(no=PATCH_NO + 9, title="[Updates] Patch Notes - October 22, 2026",
                  text="Nothing here.", at=T0 + 14 * 86400)
    dataverdicts.apply(v, hinted, quiet)
    assert v[BLESS]["verdict"] == "contradicted"


def test_bad_hint_table_is_an_error_not_a_crash(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    (d / "t.json").write_text('[{"id": "r", "verified": false, "verify": {"title": "("}}]',
                              encoding="ascii")
    svc = dataverdicts.VerdictService(lambda: [_note()], path=tmp_path / "v.json", data_dir=d)
    v = svc.view()
    assert v["verdicts"] == {} and "bad regex" in v["error"]
    assert svc.signal()["error"]


# --- digest row ---------------------------------------------------------------------

def _row(inp):
    doc = signals.digest({"data": inp}, T0)
    return next(r for r in doc["rows"] if r["id"] == "data")


def test_digest_data_row(tmp_path):
    ok = dataverdicts.VerdictService(lambda: [_note()], path=tmp_path / "a.json")
    r = _row(ok.signal())
    assert r["level"] == "ok" and r["lines"] == [] and "4 confirmed" in r["detail"]
    bad = dataverdicts.VerdictService(lambda: [_note()], path=tmp_path / "b.json",
                                      data_dir=_stale_dir(tmp_path))
    r = _row(bad.signal())
    assert r["level"] == "warn" and r["reason"] == "contradicted"
    assert r["detail"] == "1 data row contradicted by patch notes 2026-10-08 - see System"
    assert r["lines"] == [BLESS + ": Adventure Blessing: Combat EXP gain increased from 15% to 30%."]
    assert r["hint"] and r["hint"] != signals.GENERIC_HINT
    assert _row(None)["level"] == "ok" and "no patch notes" in _row(None)["detail"]


# --- fetcher: patch notes first, text kept --------------------------------------------

ROBOTS = b"User-agent: *\nDisallow: /en-US/Account\n"


def _a(no, title):
    return (f"<li><a href='/en-US/News/Detail?groupContentNo={no}'>"
            f"<strong class='title'>{title}</strong></a></li>")


class Net:
    def __init__(self, n_events=8):
        self.calls = []
        self.boards = {1: b"<ul></ul>", 3: ("<ul>" + "".join(
            _a(30000 + i, f"[Event] E{i}") for i in range(n_events)) + "</ul>").encode(),
            2: ("<ul>" + _a(PATCH_NO, TITLE) + "</ul>").encode()}
        self.page = (FIX / "patch-20261008.html").read_bytes()

    def __call__(self, url, timeout):
        self.calls.append(url)
        boards = {u: b for b, u in eventnotices.LIST_URLS.items()}
        if url.endswith("/robots.txt"):
            return ROBOTS
        if url in boards:
            return self.boards[boards[url]]
        no = int(url.split("groupContentNo=")[1].split("&")[0])
        return self.page if no == PATCH_NO else b"<p>plain</p>"


def test_patch_notes_fetched_first_on_their_board_and_text_kept(tmp_path):
    net = Net()
    client = eventnotices.NoticeClient(fetch=net, clock=lambda: T0, cache_dir=tmp_path / "c")
    client._download()
    details = [u for u in net.calls if "groupContentNo=" in u]
    assert len(details) == eventnotices.MAX_DETAILS
    assert details[0] == eventnotices.detail_url(PATCH_NO), "inside the existing 5-Detail cap, first"
    notes = client.patch_notes()
    assert [n["group_no"] for n in notes] == [PATCH_NO]
    assert "Adventure Blessing" in notes[0]["text"] and notes[0]["title"] == TITLE
    # a page cached before plan 085 (no text kept) is read once more
    (tmp_path / "c" / eventnotices.PATCH_FILE).unlink()
    assert client._stale({"group_no": PATCH_NO, "title": TITLE, "stamp": None},
                         client.details()[PATCH_NO], T0 + 1, {}) is True
    assert client._stale({"group_no": PATCH_NO, "title": TITLE, "stamp": None},
                         client.details()[PATCH_NO], T0 + 1, {PATCH_NO: {}}) is False


def test_patch_notes_older_than_the_kept_window_are_not_refetched(tmp_path):
    """Refute r1 #1: with more patch notes listed than are kept, the older ones
    never eat the Detail budget again."""
    net = Net(n_events=0)
    nos = [PATCH_NO + i for i in range(8)]
    net.boards[2] = ("<ul>" + "".join(_a(n, TITLE) for n in nos) + "</ul>").encode()

    def fetch(url, timeout):  # every listed patch-notes page serves the fixture
        if "groupContentNo=" in url:
            net.calls.append(url)
            return net.page
        return net(url, timeout)
    clock = {"t": T0}
    client = eventnotices.NoticeClient(fetch=fetch, clock=lambda: clock["t"], cache_dir=tmp_path)
    client._download()
    assert len([u for u in net.calls if "groupContentNo=" in u]) == eventnotices.MAX_DETAILS
    assert len(client.patch_notes()) == eventnotices.MAX_PATCH_NOTES
    clock["t"] += 7 * 3600
    client._download()  # the 3 not read yet are details too, once
    net.calls.clear()
    clock["t"] += 7 * 3600
    client._download()
    assert [u for u in net.calls if "groupContentNo=" in u] == []
    assert sorted(n["group_no"] for n in client.patch_notes()) == nos[-4:]


def test_notes_cache_read_once_per_view(tmp_path):
    """Refute r1 #2: many row lookups share one read of the page cache."""
    reads = []

    def notes():
        reads.append(1)
        return [_note()]
    svc = dataverdicts.VerdictService(notes, path=tmp_path / "v.json", clock=lambda: T0)
    for _ in range(25):
        svc.verdict(BLESS)
    assert len(reads) == 1


def test_patch_store_keeps_newest_and_drops_junk(tmp_path):
    client = eventnotices.NoticeClient(fetch=Net(), clock=lambda: T0, cache_dir=tmp_path)
    notes = {}
    for i in range(6):
        notes = client._keep_patch(notes, {"group_no": 100 + i, "title": TITLE, "stamp": None},
                                   "<p>x</p>", T0)
    assert sorted(notes) == [102, 103, 104, 105]
    doc = {str(k): v for k, v in notes.items()}
    doc["7"] = {"title": "[Event] not a patch", "text": "x", "fetched_at": T0}
    doc["8"] = "junk"
    (tmp_path / eventnotices.PATCH_FILE).write_text(json.dumps(doc))
    assert [n["group_no"] for n in client.patch_notes()] == [102, 103, 104, 105]


# --- leveling / grind surfaces ----------------------------------------------------------

def test_leveling_epoch_confirmed_by_patch_notes(tmp_path):
    svc = dataverdicts.VerdictService(lambda: [_note()], path=tmp_path / "v.json")
    lv = leveling.LevelingService(Store(tmp_path / "s"), clock=lambda: T0, deadlines=[])
    lv.verdict = svc.verdict
    d = lv.view()
    ep = d["epoch"]
    assert ep["id"] == "cap75-xp-rescale" and ep["verified"] is True
    assert ep["patch"]["verdict"] == "confirmed" and ep["patch"]["date"] == "2026-10-08"
    assert [e["patch"]["verdict"] for e in d["epochs"]] == ["confirmed"]
    lv.verdict = None
    assert lv.view()["epoch"]["verified"] is False and lv.view()["epoch"]["patch"] is None


def test_grind_presets_and_drop_rows_carry_verdicts(tmp_path):
    svc = dataverdicts.VerdictService(lambda: [_note()], path=tmp_path / "v.json",
                                      data_dir=_stale_dir(tmp_path))
    g = grind.GrindService(Store(tmp_path / "s"), clock=lambda: T0)
    g.verdict = svc.verdict
    d = g.view()
    by = {p["name"]: p for p in d["xp_presets"]}
    assert by["Adventure Blessing"]["patch"]["verdict"] == "contradicted"
    assert by["Body Enhancement"]["patch"]["verdict"] == "confirmed"
    assert by["Body Enhancement"]["verified"] == "2026-10-08"
    rows = {r["id"]: r for r in d["drops"]["buffs"]}
    assert rows["ecology-knowledge"]["patch"] is None and rows["ecology-knowledge"]["verified"] is False


# --- API acceptance -----------------------------------------------------------------------

@pytest.fixture()
def srv(tmp_path):
    cache = tmp_path / "cache"
    client = eventnotices.NoticeClient(fetch=Net(), clock=lambda: T0, cache_dir=cache)
    client._download()  # the cached fixture page, as plan 064's run leaves it

    def no_net(url, timeout):
        raise AssertionError("test touched the network")
    client.fetch = no_net
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          sse_interval=0.05, market_seed=[], profile_cfg={},
                          market_client=market.ArshaClient(fetch=no_net,
                                                           cache_dir=tmp_path / "mcache"),
                          leveling_clock=lambda: T0, config_path=tmp_path / "local.json",
                          notice_client=client, notice_spawn=lambda fn: None,
                          verdict_data_dir=_stale_dir(tmp_path))
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield s
    s.shutdown()
    s.server_close()


def _get(s, path):
    c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=5)
    c.request("GET", path)
    r = c.getresponse()
    out = json.loads(r.read())
    c.close()
    return r.status, out


def test_api_acceptance(srv, tmp_path):
    before = _digest(DATA)
    st, v = _get(srv, "/api/data/verdicts")
    assert st == 200
    assert v["verdicts"][EPOCH]["verdict"] == "confirmed"
    assert v["verdicts"][BLESS]["verdict"] == "contradicted"
    assert v["last_notice"]["notice_no"] == PATCH_NO
    st, sig = _get(srv, "/api/signals")
    row = next(r for r in sig["rows"] if r["id"] == "data")
    assert row["level"] == "warn"
    assert row["detail"] == "1 data row contradicted by patch notes 2026-10-08 - see System"
    assert len(row["lines"]) == 1
    st, lv = _get(srv, "/api/leveling")
    assert lv["epoch"]["patch"] == {"verdict": "confirmed", "date": "2026-10-08",
                                    "evidence": v["verdicts"][EPOCH]["evidence"],
                                    "url": eventnotices.detail_url(PATCH_NO)}
    assert (tmp_path / "data_verdicts.json").is_file()
    assert _digest(DATA) == before


def test_notice_detail_list_links_confirm_the_cap75_epoch(tmp_path):
    """Research 0016 / plan 018 check: with the Updates list linking
    /News/Notice/Detail (live shape since 2026-10-08), patch notes 10678 are
    read and /api/data/verdicts marks cap75-xp-rescale confirmed."""
    net = Net()
    net.boards[2] = ("<ul><li><a href='/News/Notice/Detail?groupContentNo=10678&amp;"
                     f"countryType=en-US'><strong class='title'>{TITLE}</strong></a></li>"
                     "</ul>").encode()
    net_call = net.__call__

    def fetch(url, timeout):
        if url == eventnotices.detail_url(10678):
            net.calls.append(url)
            return net.page
        return net_call(url, timeout)
    client = eventnotices.NoticeClient(fetch=fetch, clock=lambda: T0, cache_dir=tmp_path / "c")
    client._download()
    assert [n["group_no"] for n in client.patch_notes()] == [10678]

    def no_net(url, timeout):
        raise AssertionError("test touched the network")
    client.fetch = no_net
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          sse_interval=0.05, market_seed=[], profile_cfg={},
                          market_client=market.ArshaClient(fetch=no_net,
                                                           cache_dir=tmp_path / "mcache"),
                          leveling_clock=lambda: T0, config_path=tmp_path / "local.json",
                          notice_client=client, notice_spawn=lambda fn: None,
                          verdict_data_dir=_stale_dir(tmp_path))
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    try:
        st, v = _get(s, "/api/data/verdicts")
    finally:
        s.shutdown()
        s.server_close()
    assert st == 200 and v["verdicts"][EPOCH]["verdict"] == "confirmed"
    assert v["last_notice"]["notice_no"] == 10678


LIVE_A = ("<li><a href=\"https://www.naeu.playblackdesert.com/News/Notice/Detail?groupContentNo={no}"
          "&countryType=en-US\" name=\"btnDetail\"><span class=\"title line_clamp\">{title}</span>"
          "</a></li>")


def test_live_bare_title_list_reads_10678_first_and_confirms_cap75(tmp_path):
    """Research 0016 post-merge live check: the list title is bare (no
    [Updates]), no stamp; 10678 must get a Detail GET ahead of the older event
    notices that filled all MAX_DETAILS slots, then cap75 is confirmed."""
    net = Net(n_events=0)
    net.boards[3] = ("<ul>" + "".join(LIVE_A.format(no=n, title=f"[Event] E{n}")
                                      for n in (10663, 10646, 10609, 10580, 10563, 10550))
                     + "</ul>").encode()
    net.boards[2] = ("<ul>" + LIVE_A.format(no=10678, title=BARE)
                     + LIVE_A.format(no=10661, title="Patch Notes - October 1, 2026")
                     + "</ul>").encode()
    net_call = net.__call__

    def fetch(url, timeout):
        if url == eventnotices.detail_url(10678):
            net.calls.append(url)
            return net.page
        return net_call(url, timeout)
    client = eventnotices.NoticeClient(fetch=fetch, clock=lambda: T0, cache_dir=tmp_path / "c")
    notices = client._lists()
    assert [n["title"] for n in notices if n["group_no"] == 10678] == [BARE]
    assert eventnotices.NoticeClient._fetch_order(notices)[0]["group_no"] == 10678
    client._download()
    details = [u for u in net.calls if "groupContentNo=" in u]
    assert details[0] == eventnotices.detail_url(10678)
    assert len(details) == eventnotices.MAX_DETAILS
    notes = client.patch_notes()
    assert 10678 in [n["group_no"] for n in notes]
    hinted = patchverify.load()["hinted"]
    new = next(n for n in notes if n["group_no"] == 10678)
    assert new["title"] == BARE
    assert patchverify.check(new["text"], new["title"], hinted)[EPOCH]["verdict"] == "confirmed"
    svc = dataverdicts.VerdictService(client.patch_notes, path=tmp_path / "v.json",
                                      clock=lambda: T0)
    assert svc.verdict(EPOCH)["verdict"] == "confirmed"


# --- plan 087: Unicode fold, name aliases, hints for the official numbers ------------------

ASIA = "patch-20261008-asia.html"
OLD_BAND = "xp_epochs.json#cap-62-75"
# Dd505bd: the tracked 62-75 row was split into cap-62-64 / cap-65-75 after the NA
# 2026-10-08 notes contradicted it; the legacy row lives on here so the
# contradicted -> digest -> loop work item path stays pinned.
SPLIT_BANDS = ["xp_epochs.json#cap-62-64", "xp_epochs.json#cap-65-75"]
LEGACY_ROW = {
    "level_min": 62, "level_max": 75, "id": "cap-62-75", "verified": False,
    "note": "per-kill XP cap ~0.01% of the level (buffs included), verify",
    "superseded_by": ["band-62-64", "band-65-75"],
    "verify": {"title": "^\\[Updates\\] Patch Notes\\b",
               "expect": ["(?<![0-9])(?<![0-9]\\.)62 ?(?:-|~|to) ?(?:Lv\\.? ?)?75(?![0-9])[^%]{0,30}?"
                          "(?:[0-9.]{1,7} ?% ?(?:->|=>|to\\b) ?)?(?<![0-9.])0\\.01 ?%"
                          "(?! ?(?:->|=>|to\\b))"],
               "contradict": ["(?<![0-9])(?<![0-9]\\.)62 ?(?:-|~|to) ?(?:Lv\\.? ?)?64(?![0-9])[^%]{0,30}?"
                              "(?<![0-9.])[0-9]{1,3}(?:\\.[0-9]{1,3})? ?%",
                              "(?<![0-9])(?<![0-9]\\.)62 ?(?:-|~|to) ?(?:Lv\\.? ?)?75(?![0-9])[^%]{0,30}?"
                              "(?:[0-9.]{1,7} ?% ?(?:->|=>|to\\b) ?)?(?<![0-9.])(?!0\\.01 ?%)"
                              "[0-9]{1,3}(?:\\.[0-9]{1,3})? ?%(?! ?(?:->|=>|to\\b))"]}}


def _legacy_data(tmp_path):
    """A copy of the tracked data with the pre-Dd505bd 62-75 row in place of the split."""
    d = tmp_path / "legacy_data"
    shutil.copytree(DATA, d)
    p = d / "xp_epochs.json"
    doc = json.loads(p.read_text(encoding="ascii"))
    caps = doc[0]["kill_xp_cap"]
    caps[:] = [c for c in caps if c.get("id") not in ("cap-62-64", "cap-65-75")] + [LEGACY_ROW]
    p.write_text(json.dumps(doc), encoding="ascii", newline="\n")
    return d
BANDS = [(1, 5), (6, 10), (11, 15), (16, 20), (21, 25), (26, 30), (31, 35), (36, 40), (41, 47),
         (48, 49), (50, 55), (56, 61), (62, 64), (65, 75)]
NEW_BANDS = [f"xp_epochs.json#band-{lo}-{hi}" for lo, hi in BANDS]
BOOKS = [f"xp_books.json#{s}" for s in ("small", "medium", "large", "xl")]
PRESETS = [f"xp_buffs.json#{r}" for r in ("body-enhancement", "adventure-blessing",
                                          "pearl-outfit-set")]
LEVEL_ROWS = [f"brackets.json#lv-bonus-{lv}" for lv in range(70, 76)] + [
    "brackets.json#level-gap-dr"]


def _asia():
    raw = (FIX / ASIA).read_text(encoding="ascii")
    return "\n".join(eventnotices._lines(raw))


def test_asia_fixture_holds_the_real_glyphs():
    t = _asia()
    for cp in (0x2192, 0x2019, 0x2013, 0x00A0):
        assert chr(cp) in t or cp == 0x00A0  # the line parser may turn NBSP into a space
    assert "Adventure" + chr(0x2019) + "s Boon" in t


def test_fold_maps_the_table_and_blanks_other_non_ascii():
    f = patchverify.fold
    assert f("70 " + chr(0x2192) + " 75") == "70 -> 75"
    for cp in (0x21D2, 0x2794, 0x25BA, 0x27A1):
        assert f(chr(cp)) == "->"
    assert f(chr(0x2013) + chr(0x2014) + chr(0x2212)) == "---"
    assert f("a" + chr(0xA0) + chr(0x2009) + chr(0x202F) + "b") == "a b", "space runs collapse"
    assert f(chr(0x2018) + chr(0x2019) + chr(0x201C) + chr(0x201D)) == "''\"\""
    assert f("5" + chr(0xFF05) + " 2" + chr(0xD7)) == "5% 2x"
    assert f("caf" + chr(0xE9) + chr(0x1F600) + "!") == "caf !"
    plain = "Body Enhancement: 50% to 100%.\nnext"
    assert f(plain) == plain, "ASCII text is matched exactly as before"
    assert f("a  \t b\n\nc") == "a b\n\nc", "line breaks stay"


def test_old_fixture_is_ascii_so_085_matching_is_unchanged():
    assert patchverify.fold(_text()) == _text()


def test_asia_fixture_confirms_epoch_presets_bands_books(tmp_path):
    res = patchverify.check(_asia(), TITLE, patchverify.load()["hinted"])
    for k in [EPOCH] + PRESETS + NEW_BANDS + BOOKS + LEVEL_ROWS + SPLIT_BANDS:
        assert res[k]["verdict"] == "confirmed", (k, res[k])
    assert "level: 70 -> 75" in res[EPOCH]["evidence"]
    assert res[BLESS]["evidence"] == "Adventure's Boon: Combat EXP 15% -> 30%"
    assert OLD_BAND not in res
    assert [k for k, v in res.items() if v["verdict"] == "contradicted"] == []
    for k in ("xp_epochs.json#cap-1-5", "xp_epochs.json#cap-50-55", "xp_epochs.json#cap-56-61"):
        assert res[k]["verdict"] == "confirmed", k
    for k, v in res.items():
        assert all(32 <= ord(c) < 127 for c in v["evidence"] or "")
    old = patchverify.check(_asia(), TITLE, patchverify.load(_legacy_data(tmp_path))["hinted"])
    assert old[OLD_BAND] == {"verdict": "contradicted", "evidence": "Lv. 62 - 64: 0.02%"}
    assert [k for k, v in old.items() if v["verdict"] == "contradicted"] == [OLD_BAND]


def test_unfolded_asia_text_would_stay_silent():
    """The plan 087 gap: without the fold the arrow and apostrophe never match."""
    hinted = patchverify.load()["hinted"]
    raw = _asia()
    r = patchverify._check_folded(raw, hinted[EPOCH]["hint"])
    assert r["verdict"] == "silent"
    assert patchverify._check_folded(raw, hinted[BLESS]["hint"])["verdict"] == "silent"


def test_band_hints_never_borrow_a_neighbour():
    hinted = patchverify.load()["hinted"]
    text = ("Lv. 41 - 47: 5%\nLv. 56 - 61: 0.25%\nLv. 62 or higher: 0.01%\n"
            "Lv. 1 - 5 20 %, 6 - 10 17 %")
    res = patchverify.check(text, TITLE, hinted)
    assert res["xp_epochs.json#band-1-5"]["verdict"] == "confirmed"
    assert res["xp_epochs.json#band-6-10"]["verdict"] == "contradicted"
    assert res["xp_epochs.json#band-56-61"]["verdict"] == "contradicted", "0.25 is not 0.2"
    assert res["xp_epochs.json#band-41-47"]["verdict"] == "confirmed"
    for k in ["xp_epochs.json#band-11-15", "xp_epochs.json#band-65-75"] + SPLIT_BANDS:
        assert res[k]["verdict"] == "silent", k
    books = patchverify.check("Combat Secret Book (Extra Large): 15%", TITLE, hinted)
    assert books["xp_books.json#xl"]["verdict"] == "confirmed"
    assert books["xp_books.json#large"]["verdict"] == "silent", "Extra Large is not Large"


def test_refute_r1_no_false_verdicts_from_neighbouring_text():
    hinted = patchverify.load()["hinted"]
    lv = [k for k in LEVEL_ROWS if "lv-bonus" in k]
    item = ("New accessory: +5 Extra AP Against Monsters, +3 Extra AP Against Monsters "
            "and +3 Monster Damage Reduction.")
    res = patchverify.check(item, TITLE, hinted)
    assert {res[k]["verdict"] for k in lv} == {"silent"}, "an item line is not the level rule"
    wrong = "From Lv. 70, each level grants +4 Extra AP Against Monsters."
    assert {patchverify.check(wrong, TITLE, hinted)[k]["verdict"] for k in lv} == {"contradicted"}
    assert lv[0] not in patchverify.check(wrong, "[Updates] Patch Notes - October 15, 2026", hinted)
    raw = json.loads((DATA / "brackets.json").read_text(encoding="ascii"))["level_bonus"]
    seq = " ?/ ?".join(str(r["ap_vs_monsters"]) for r in raw)
    assert all(r["verify"]["expect"][1].startswith("(?:\\(" + seq + "\\)|") for r in raw), \
        "the totals in the hint are the stored totals"
    assert all(f"{r['level']}\\s+{r['ap_vs_monsters']}\\s+{r['monster_dr']}(?![0-9])"
               in r["verify"]["expect"][1] for r in raw), "the NA table row is the stored row"
    shop = "Pearl Outfit sale: discounts of up to 30%."
    assert patchverify.check(shop, TITLE, hinted)["xp_buffs.json#pearl-outfit-set"]["verdict"] \
        == "silent"
    book = "Combat Secret Book (Small): 0.5% at Lv. 61, 0.2% at Lv. 66"
    assert patchverify.check(book, TITLE, hinted)["xp_books.json#small"]["verdict"] == "silent"
    assert patchverify.check("Combat Secret Book (Small): 0.5% at Lv. 66", TITLE, hinted)[
        "xp_books.json#small"]["verdict"] == "contradicted"
    season = "Increased the max level of the Season Pass to 60."
    assert patchverify.check(season, TITLE, hinted)[EPOCH]["verdict"] == "silent"
    wave = "Lv. 1 " + chr(0xFF5E) + " 5: 20%"
    assert patchverify.check(wave, TITLE, hinted)["xp_epochs.json#band-1-5"]["verdict"] == "confirmed"


@pytest.mark.parametrize("line, key, verdict", [
    # refute r2 #1: the value before an arrow is the old value
    ("Combat Secret Book (Small): 0.2% " + chr(0x2192) + " 0.5%", "xp_books.json#small", "contradicted"),
    ("Combat Secret Book (Small): 0.5% -> 0.2%", "xp_books.json#small", "confirmed"),
    ("Lv. 56 ~ 61: 0.2% -> 0.5%", "xp_epochs.json#band-56-61", "contradicted"),
    ("Lv. 56 - 61: 0.5% to 0.2%", "xp_epochs.json#band-56-61", "confirmed"),
    ("Lv. 56 - 61: 0.5% to 0.2%", "xp_epochs.json#cap-56-61", "confirmed"),
    ("Lv. 62 - 64: 0.01% -> 0.02%", SPLIT_BANDS[0], "confirmed"),
    ("Lv. 62 - 64: 0.02% -> 0.01%", SPLIT_BANDS[0], "contradicted"),
    # refute r2 #2: a level named before the value is not the Lv 66 row
    ("Combat Secret Book (Small) at Lv. 61: 0.5%", "xp_books.json#small", "silent"),
    ("Combat Secret Book (Medium): 1%", "xp_books.json#medium", "confirmed"),
    # refute r3: an NBSP before the arrow folds to a second space, collapsed
    ("Lv. 56 - 61: 0.2% " + chr(0xA0) + chr(0x2192) + " 0.5%", "xp_epochs.json#band-56-61",
     "contradicted"),
    ("Combat Secret Book (Small): 0.5%  -> 0.2%", "xp_books.json#small", "confirmed"),
])
def test_refute_r2_old_values_and_level_keyed_books(line, key, verdict):
    res = patchverify.check(line, TITLE, patchverify.load()["hinted"])
    assert res[key]["verdict"] == verdict


@pytest.mark.parametrize("doc, msg", [
    ({"fold": {"2192": "->"}}, "U\\+XXXX"),
    ({"fold": {"U+2192": chr(0x2192)}}, "printable ASCII"),
    ({"fold": {"U+2192": ""}}, "printable ASCII"),
    ({"fold": {"U+0041": "a"}}, "non-ASCII"),
    ({"fold": {"u+2192": "->"}}, "U\\+XXXX"),
    ({"fold": {}}, "fold table"),
    ([], "fold table"),
])
def test_bad_fold_table_rejected(doc, msg):
    with pytest.raises(ValueError, match=msg):
        patchverify.validate_fold(doc)


def test_fold_table_loads_once_and_unreadable_is_an_error(tmp_path):
    t = patchverify.load_fold()
    assert t[0x2192] == "->" and patchverify.load_fold() is t
    p = tmp_path / "f.json"
    p.write_text('{"fold": {"U+2192": "=>"}}', encoding="ascii")
    assert patchverify.load_fold(p) == {0x2192: "=>"}
    with pytest.raises(ValueError, match="unreadable"):
        patchverify.load_fold(tmp_path / "missing.json")


def test_name_token_expands_name_and_aliases():
    h = patchverify.compile_hint({"title": "x", "expect": ["{name} up"]}, name="Foo")
    assert h["expect"][0].pattern == "(?:Foo) up"
    h = patchverify.compile_hint({"title": "x", "expect": ["{name}: 30%"],
                                  "aliases": ["Adventure's Boon", "A.B"]}, name="Adventure Blessing")
    rx = h["expect"][0]
    assert rx.pattern == "(?:Adventure\\ Blessing|Adventure's\\ Boon|A\\.B): 30%"
    assert rx.search("adventure's boon: 30%") and not rx.search("AxB: 30%")
    plain = patchverify.compile_hint({"title": "x", "expect": ["Foo"], "aliases": ["Bar"]},
                                     name="Foo")
    assert plain["expect"][0].pattern == "Foo", "no {name}: compiled unchanged"


@pytest.mark.parametrize("hint, name, msg", [
    ({"title": "x", "expect": ["{name}"]}, None, "needs a row name"),
    ({"title": "x", "expect": ["a"], "aliases": ["a"] * 5}, "n", "at most 4"),
    ({"title": "x", "expect": ["a"], "aliases": ["caf" + chr(0xE9)]}, "n", "printable ASCII"),
    ({"title": "x", "expect": ["a"], "aliases": "a"}, "n", "at most 4"),
    ({"title": "x", "expect": ["a"], "aliases": [" "]}, "n", "printable ASCII"),
])
def test_bad_alias_rejected(hint, name, msg):
    with pytest.raises(ValueError, match=msg):
        patchverify.compile_hint(hint, name=name)


def test_new_rows_load_and_their_loaders_compile_hints():
    from server.ew import brackets, xpbooks
    res = patchverify.load()
    assert set(NEW_BANDS + BOOKS + LEVEL_ROWS + SPLIT_BANDS) <= set(res["hinted"])
    assert OLD_BAND not in res["hinted"]
    eps = levels.load_epochs()
    ep = next(e for e in eps if e["id"] == "cap75-xp-rescale")
    assert "kill_xp_cap_official" not in ep, "the official bands are read by nothing yet"
    assert [set(c) for c in ep["kill_xp_cap"]] == [set(levels.CAP_FIELDS)] * 5
    assert levels.kill_cap_note(ep, 63) == ("per-kill XP cap 0.02% of the level (buffs included), "
                                            "NA patch notes 2026-10-08")
    assert levels.kill_cap_note(ep, 70) == ("per-kill XP cap 0.01% of the level (buffs included), "
                                            "NA patch notes 2026-10-08")
    assert levels.kill_cap_note(ep, 30) is None
    assert set(brackets.load_tracked()) == set(brackets.TABLES)
    books = xpbooks.load_books()
    assert set(books["sizes"]["small"]) == set(xpbooks.SIZE_FIELDS)
    raw = json.loads((DATA / "brackets.json").read_text(encoding="ascii"))
    assert [r["ap_vs_monsters"] for r in raw["level_bonus"]] == [3, 6, 9, 12, 15, 18]
    assert raw["level_gap_dr"][0]["dr_per_level"] * raw["level_gap_dr"][0]["max_levels"] == 9


def _epoch_row():
    return json.loads((DATA / "xp_epochs.json").read_text(encoding="ascii"))[0]


def test_loaders_reject_bad_hints_on_new_rows():
    from server.ew import brackets, xpbooks
    bad = {"title": "x", "expect": ["(unclosed"]}
    row = _epoch_row()
    row["kill_xp_cap_official"][0]["verify"] = bad
    with pytest.raises(ValueError, match="bad regex"):
        levels.validate_epoch(row, tracked=True)
    row = _epoch_row()
    row["kill_xp_cap"][3]["verify"] = bad
    with pytest.raises(ValueError, match="bad regex"):
        levels.validate_epoch(row, tracked=True)
    row = _epoch_row()
    row["kill_xp_cap"][3]["superseded_by"] = ["band-nope"]
    with pytest.raises(ValueError, match="superseded_by"):
        levels.validate_epoch(row, tracked=True)
    row = _epoch_row()
    row["kill_xp_cap_official"][1]["level_min"] = 1
    with pytest.raises(ValueError, match="rising"):
        levels.validate_epoch(row, tracked=True)
    with pytest.raises(ValueError):
        levels.validate_epoch({k: v for k, v in _epoch_row().items() if k != "kill_xp_cap"})
    doc = json.loads((DATA / "brackets.json").read_text(encoding="ascii"))
    doc["level_bonus"][0]["verify"] = bad
    with pytest.raises(ValueError, match="bad regex"):
        brackets.validate_level_rows(doc)
    doc["level_bonus"][0].pop("verify")
    doc["level_bonus"][1]["id"] = doc["level_bonus"][0]["id"]
    with pytest.raises(ValueError, match="unique"):
        brackets.validate_level_rows(doc)
    books = json.loads((DATA / "xp_books.json").read_text(encoding="ascii"))
    books["sizes"][0]["verify"] = bad
    with pytest.raises(ValueError, match="bad regex"):
        xpbooks.validate_books(books)


def test_asia_verdicts_digest_and_loop_item(tmp_path):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import ew_loop
    before = _digest(DATA)
    path = tmp_path.joinpath(*ew_loop.VERDICTS_REL)
    path.parent.mkdir(parents=True)
    svc = dataverdicts.VerdictService(lambda: [_note(text=_asia())], path=path,
                                      data_dir=_legacy_data(tmp_path))
    v = svc.view()["verdicts"]
    for k in [EPOCH] + PRESETS + NEW_BANDS + BOOKS:
        assert v[k]["verdict"] == "confirmed", k
    assert v[OLD_BAND]["verdict"] == "contradicted"
    r = _row(svc.signal())
    assert r["level"] == "warn"
    assert r["detail"] == "1 data row contradicted by patch notes 2026-10-08 - see System"
    assert r["lines"] == [OLD_BAND + ": Lv. 62 - 64: 0.02%"]
    items = ew_loop.data_items(tmp_path)
    assert len(items) == 1 and OLD_BAND in items[0]["text"]
    assert _digest(DATA) == before


@pytest.fixture()
def srv_asia(tmp_path):
    cache = tmp_path / "cache"
    net = Net()
    net.page = (FIX / ASIA).read_bytes()
    client = eventnotices.NoticeClient(fetch=net, clock=lambda: T0, cache_dir=cache)
    client._download()

    def no_net(url, timeout):
        raise AssertionError("test touched the network")
    client.fetch = no_net
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          sse_interval=0.05, market_seed=[], profile_cfg={},
                          market_client=market.ArshaClient(fetch=no_net,
                                                           cache_dir=tmp_path / "mcache"),
                          leveling_clock=lambda: T0, config_path=tmp_path / "local.json",
                          notice_client=client, notice_spawn=lambda fn: None)
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield s
    s.shutdown()
    s.server_close()


def test_api_acceptance_asia(srv_asia, tmp_path):
    before = _digest(DATA)
    st, v = _get(srv_asia, "/api/data/verdicts")
    assert st == 200
    for k in [EPOCH] + PRESETS + NEW_BANDS + BOOKS + SPLIT_BANDS:
        assert v["verdicts"][k]["verdict"] == "confirmed", k
    assert OLD_BAND not in v["verdicts"], "Dd505bd: the 62-75 row was split"
    st, sig = _get(srv_asia, "/api/signals")
    row = next(r for r in sig["rows"] if r["id"] == "data")
    assert row["level"] == "ok" and row["lines"] == []
    assert _digest(DATA) == before


# --- H0a5846: NA 10678 wording - tables render one cell per line -------------------------

NA = "patch-20261008-na.html"
CAPS_STATED = [f"xp_epochs.json#cap-{r}" for r in ("1-5", "50-55", "56-61")]
DROPS = [f"drop_buffs.json#{r}" for r in ("item-collection-scroll", "adv-item-collection-scroll",
                                          "ecology-knowledge", "agris-fever")]
LV_HEAD = "Level\nExtra AP Against Monsters\nMonster Damage Reduction\n"


def _na():
    raw = (FIX / NA).read_text(encoding="ascii")
    return "\n".join(eventnotices._lines(raw))


def test_na_fixture_is_the_one_cell_per_line_shape():
    t = patchverify.fold(_na())
    assert "\n1~5\n20%\n" in t and "\n65 and above\n0.01%\n" in t
    assert "\n75\n18\n18\n" in t and "Combat Secret Book (S)\n0.20%" in t


def test_na_fixture_confirms_every_stated_row(tmp_path):
    res = patchverify.check(_na(), TITLE, patchverify.load()["hinted"])
    for k in LEVEL_ROWS + NEW_BANDS + BOOKS + PRESETS + CAPS_STATED + SPLIT_BANDS:
        assert res[k]["verdict"] == "confirmed", (k, res[k])
    assert res[SPLIT_BANDS[0]]["evidence"] == "62-64 0.02%"
    assert res[SPLIT_BANDS[1]]["evidence"] == "65 and above 0.01%"
    assert [k for k, v in res.items() if v["verdict"] == "contradicted"] == []
    old = patchverify.check(_na(), TITLE, patchverify.load(_legacy_data(tmp_path))["hinted"])
    assert old[OLD_BAND] == {"verdict": "contradicted", "evidence": "62-64 0.02%"}
    assert [k for k, v in old.items() if v["verdict"] == "contradicted"] == [OLD_BAND]
    assert {res[k]["verdict"] for k in DROPS} == {"silent"}, "the notes never state them"
    assert res["xp_epochs.json#band-65-75"]["evidence"] == "65 and above 0.01%"
    assert res["xp_books.json#small"]["evidence"] == "Combat Secret Book (S) 0.20%"
    assert res[BLESS]["evidence"].endswith("from 15% to 30%.")


def test_na_table_rows_contradict_only_their_own_level():
    hinted = patchverify.load()["hinted"]
    res = patchverify.check(LV_HEAD + "70\n3\n3\n71\n7\n7\n72\n9\n9", TITLE, hinted)
    assert res["brackets.json#lv-bonus-71"]["verdict"] == "contradicted"
    for lv in (70, 72, 73):
        assert res[f"brackets.json#lv-bonus-{lv}"]["verdict"] == "silent", lv
    bare = patchverify.check("70\n3\n3\n71\n7\n7", TITLE, hinted)
    assert {bare[k]["verdict"] for k in LEVEL_ROWS} == {"silent"}, "no header, no verdict"
    lead = ("Extra AP Against Monsters +4 and Monster Damage Reduction +4 are gained from "
            "level 70 and onwards.")
    assert patchverify.check(lead, TITLE, hinted)["brackets.json#lv-bonus-70"]["verdict"] \
        == "contradicted"
    gap = ("For each level you are above a monster, you gain Monster Damage Reduction +2 "
           "against that monster's attacks.")
    assert patchverify.check(gap, TITLE, hinted)["brackets.json#level-gap-dr"]["verdict"] \
        == "contradicted"


def test_na_buff_lines_cross_a_comma_and_a_non_numeric_to():
    hinted = patchverify.load()["hinted"]
    line = ("Increased the Combat/Skill EXP gain from the Adventure's Boon buff obtainable by "
            "talking to NPCs or at Campsites, from 15% to 25%.")
    assert patchverify.check(line, TITLE, hinted)[BLESS]["verdict"] == "contradicted"
    pearl = "Increased the bonus Combat EXP for equipping 4 parts of a Pearl Outfit from 10% to 40%."
    assert patchverify.check(pearl, TITLE, hinted)["xp_buffs.json#pearl-outfit-set"][
        "verdict"] == "contradicted"
