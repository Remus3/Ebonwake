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
