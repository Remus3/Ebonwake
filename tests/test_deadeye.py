"""Plan 007 slice A: deadeye store domain (build notes + enhancement plan), routes.

Text only: nothing here is executed or sent anywhere. The clock is injected so
`updated` stamps are exact.
"""

import datetime as dt
import http.client
import json
import math
import threading

import pytest

from server.ew import app as ewapp
from server.ew import deadeye
from server.ew.store import Store

UTC = dt.timezone.utc
T0 = dt.datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)


class Clock:
    def __init__(self, when=T0):
        self.t = when.timestamp()

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


@pytest.fixture()
def clock():
    return Clock()


@pytest.fixture()
def svc(tmp_path, clock):
    return deadeye.DeadeyeService(Store(tmp_path / "store"), clock=clock)


def _sec(doc, sid):
    return next(s for s in doc["sections"] if s["id"] == sid)


def _add(svc, item="Blackstar Bow", current="+15", target="PRI", note=None):
    arg = {"item": item, "current": current, "target": target}
    if note is not None:
        arg["note"] = note
    return svc.add_step(arg)


# --- constants -----------------------------------------------------------------

def test_sections_fixed_order():
    assert deadeye.SECTIONS == (
        ("addons", "Skill add-ons"), ("crystals", "Crystals"), ("artifacts", "Artifacts"),
        ("lightstones", "Lightstones"), ("rotation", "PvE rotation"), ("misc", "Misc"))


def test_levels():
    lv = deadeye.LEVELS
    assert len(lv) == 21
    assert lv[:16] == tuple(f"+{n}" for n in range(16))
    assert lv[16:] == ("PRI", "DUO", "TRI", "TET", "PEN")


# --- seed ----------------------------------------------------------------------

def test_seed_view(svc):
    doc = svc.view()
    assert doc["now"] == T0.isoformat()
    assert doc["levels"] == list(deadeye.LEVELS)
    assert [(s["id"], s["title"]) for s in doc["sections"]] == list(deadeye.SECTIONS)
    assert all(s["text"] == "" and s["updated"] is None for s in doc["sections"])
    assert doc["plan"] == [] and doc["progress"] == {"done": 0, "total": 0}


def test_seed_not_reapplied(tmp_path, clock):
    st = Store(tmp_path / "store")
    deadeye.DeadeyeService(st, clock=clock).note({"section": "misc", "text": "hi"})
    b = deadeye.DeadeyeService(st, clock=clock)
    assert _sec(b.view(), "misc")["text"] == "hi"


# --- notes ---------------------------------------------------------------------

def test_note_saves_text_and_stamp(svc, clock):
    clock.advance(30)
    doc = svc.note({"section": "rotation", "text": "# Rotation\r\n- Rain of Arrows\r\n"})
    s = _sec(doc, "rotation")
    assert s["text"] == "# Rotation\n- Rain of Arrows\n"
    assert s["updated"] == (T0 + dt.timedelta(seconds=30)).isoformat()
    assert _sec(doc, "misc")["updated"] is None


def test_note_empty_text_clears(svc):
    svc.note({"section": "misc", "text": "x"})
    assert _sec(svc.note({"section": "misc", "text": ""}), "misc")["text"] == ""


def test_note_limit_inclusive(svc):
    doc = svc.note({"section": "misc", "text": "x" * 20000})
    assert len(_sec(doc, "misc")["text"]) == 20000


def test_note_limit_counts_after_crlf_normalise(svc):
    doc = svc.note({"section": "misc", "text": "\r\n" * 20000})
    assert _sec(doc, "misc")["text"] == "\n" * 20000


@pytest.mark.parametrize("arg", [
    {"section": "misc", "text": "x" * 20001}, {"section": "nope", "text": "x"},
    {"section": "misc", "text": 5}, {"section": "misc", "text": None}, {"section": 1, "text": ""},
    {"section": "misc"}, {"section": "misc", "text": "", "x": 1}, "misc", None,
])
def test_note_bad(svc, arg):
    with pytest.raises(ValueError):
        svc.note(arg)
    assert all(s["text"] == "" for s in svc.view()["sections"])


# --- plan: add -----------------------------------------------------------------

def test_add_step(svc):
    doc = _add(svc, item="  Blackstar Bow ", current="+15", target="PRI", note=" fs ")
    (st,) = doc["plan"]
    assert st == {"id": "d1", "item": "Blackstar Bow", "current": "+15", "target": "PRI",
                  "note": "fs", "done": False, "steps": 1}
    assert doc["progress"] == {"done": 0, "total": 1}


def test_add_step_note_optional_and_steps(svc):
    st = _add(svc, current="+0", target="PEN")["plan"][0]
    assert st["note"] == "" and st["steps"] == 20


@pytest.mark.parametrize("arg", [
    {"item": "", "current": "+0", "target": "+1"},
    {"item": "   ", "current": "+0", "target": "+1"},
    {"item": "x" * 61, "current": "+0", "target": "+1"},
    {"item": "a\nb", "current": "+0", "target": "+1"},
    {"item": 5, "current": "+0", "target": "+1"},
    {"item": "A", "current": "+16", "target": "PRI"},
    {"item": "A", "current": "+0", "target": "pri"},
    {"item": "A", "current": "PRI", "target": "PRI"},
    {"item": "A", "current": "DUO", "target": "PRI"},
    {"item": "A", "current": 0, "target": "+1"},
    {"item": "A", "current": "+0", "target": "+1", "note": "x" * 201},
    {"item": "A", "current": "+0", "target": "+1", "note": 5},
    {"item": "A", "current": "+0", "target": "+1", "note": "a\x00b"},
    {"item": "A", "current": "+0"},
    {"item": "A", "current": "+0", "target": "+1", "done": True},
    "A", None,
])
def test_add_step_bad(svc, arg):
    with pytest.raises(ValueError):
        svc.add_step(arg)
    assert svc.view()["plan"] == []


def test_add_step_limits_inclusive(svc):
    st = svc.add_step({"item": "x" * 60, "current": "TET", "target": "PEN",
                       "note": "n" * 200})["plan"][0]
    assert len(st["item"]) == 60 and len(st["note"]) == 200 and st["steps"] == 1


def test_max_steps(svc):
    for n in range(deadeye.MAX_STEPS):
        _add(svc, item=f"i{n}")
    assert deadeye.MAX_STEPS == 100
    with pytest.raises(ValueError):
        _add(svc, item="one more")
    assert len(svc.view()["plan"]) == 100


def test_ids_never_reused(svc):
    a = _add(svc, item="A")["plan"][-1]["id"]
    b = _add(svc, item="B")["plan"][-1]["id"]
    svc.delete_step(b)
    c = _add(svc, item="C")["plan"][-1]["id"]
    assert (a, b, c) == ("d1", "d2", "d3")


# --- plan: edit / done / delete / move ------------------------------------------

def test_edit_step_partial(svc):
    _add(svc, item="Bow", current="+10", target="+15", note="old")
    st = svc.edit_step({"id": "d1", "target": "TRI"})["plan"][0]
    assert st["item"] == "Bow" and st["target"] == "TRI" and st["note"] == "old"
    assert st["steps"] == deadeye.LEVELS.index("TRI") - deadeye.LEVELS.index("+10")
    st = svc.edit_step({"id": "d1", "item": " Bow 2 ", "current": "PRI", "note": ""})["plan"][0]
    assert (st["item"], st["current"], st["note"], st["steps"]) == ("Bow 2", "PRI", "", 2)


@pytest.mark.parametrize("arg", [
    {"id": "d1", "target": "+10"},           # not strictly above current
    {"id": "d1", "current": "+15"},          # current == target
    {"id": "d1", "current": "PEN"},          # current above target
    {"id": "d1", "item": ""}, {"id": "d1", "note": "x" * 201},
    {"id": "d1", "target": "PEN!"}, {"id": "d9", "item": "X"}, {"id": 1, "item": "X"},
    {"id": "d1"}, {"id": "d1", "done": True}, {"item": "X"}, "d1", None,
])
def test_edit_step_bad_keeps_step(svc, arg):
    _add(svc, item="Bow", current="+10", target="+15")
    before = svc.view()["plan"]
    with pytest.raises(ValueError):
        svc.edit_step(arg)
    assert svc.view()["plan"] == before


def test_step_done_and_progress(svc):
    _add(svc, item="A")
    _add(svc, item="B")
    doc = svc.step_done({"id": "d2", "done": True})
    assert [s["done"] for s in doc["plan"]] == [False, True]
    assert doc["progress"] == {"done": 1, "total": 2}
    doc = svc.step_done({"id": "d2", "done": False})
    assert doc["progress"] == {"done": 0, "total": 2}


@pytest.mark.parametrize("arg", [
    {"id": "d1", "done": 1}, {"id": "d1", "done": "true"}, {"id": "d1"},
    {"id": "d9", "done": True}, {"id": "d1", "done": True, "x": 1}, "d1",
])
def test_step_done_bad(svc, arg):
    _add(svc, item="A")
    with pytest.raises(ValueError):
        svc.step_done(arg)
    assert svc.view()["plan"][0]["done"] is False


def test_delete_step(svc):
    _add(svc, item="A")
    _add(svc, item="B")
    doc = svc.delete_step("d1")
    assert [s["id"] for s in doc["plan"]] == ["d2"]
    for bad in ("d1", "x", 1, None):
        with pytest.raises(ValueError):
            svc.delete_step(bad)


def test_move_step(svc):
    for n in "ABC":
        _add(svc, item=n)
    doc = svc.move_step({"id": "d3", "dir": -1})
    assert [s["item"] for s in doc["plan"]] == ["A", "C", "B"]
    doc = svc.move_step({"id": "d1", "dir": 1})
    assert [s["item"] for s in doc["plan"]] == ["C", "A", "B"]


def test_move_step_past_end_is_noop(svc):
    for n in "AB":
        _add(svc, item=n)
    assert [s["item"] for s in svc.move_step({"id": "d1", "dir": -1})["plan"]] == ["A", "B"]
    assert [s["item"] for s in svc.move_step({"id": "d2", "dir": 1})["plan"]] == ["A", "B"]


@pytest.mark.parametrize("arg", [
    {"id": "d1", "dir": 0}, {"id": "d1", "dir": 2}, {"id": "d1", "dir": True},
    {"id": "d1", "dir": "1"}, {"id": "d1", "dir": 1.0}, {"id": "d9", "dir": 1}, {"id": "d1"},
    "d1",
])
def test_move_step_bad(svc, arg):
    _add(svc, item="A")
    _add(svc, item="B")
    with pytest.raises(ValueError):
        svc.move_step(arg)


# --- store degradation ----------------------------------------------------------

def test_corrupt_entries_skipped(tmp_path, clock):
    st = Store(tmp_path / "store")
    good = {"id": "d4", "item": "Bow", "current": "+15", "target": "PRI", "note": "",
            "done": True}
    st.put("deadeye", {"notes": {"misc": {"text": "ok", "updated": T0.isoformat()},
                                 "addons": "junk", "rotation": {"text": 5},
                                 "bogus": {"text": "x", "updated": None}},
                       "plan": [good, "junk", {"id": "d5", "item": "X", "current": "PRI",
                                               "target": "+1", "note": "", "done": False},
                                dict(good), {"id": 7}],
                       "next_id": 2})
    svc = deadeye.DeadeyeService(st, clock=clock)
    doc = svc.view()
    assert _sec(doc, "misc") == {"id": "misc", "title": "Misc", "text": "ok",
                                 "updated": T0.isoformat()}
    assert _sec(doc, "addons")["text"] == "" and _sec(doc, "rotation")["text"] == ""
    assert [s["id"] for s in doc["plan"]] == ["d4"]
    assert doc["progress"] == {"done": 1, "total": 1}
    # next_id never reuses a surviving id even when the stored counter is behind
    assert _add(svc, item="New")["plan"][-1]["id"] == "d5"


def test_source(svc):
    assert svc.source() == {"done": 0, "total": 0}
    _add(svc, item="A")
    svc.step_done({"id": "d1", "done": True})
    _add(svc, item="B")
    assert svc.source() == {"done": 1, "total": 2}


# --- routes --------------------------------------------------------------------

@pytest.fixture()
def dsrv(tmp_path, clock):
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          sse_interval=0.05, market_seed=[], deadeye_clock=clock)
    t = threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    t.start()
    yield s
    s.shutdown()
    s.server_close()


def _req(s, method, path, body=None, ctype="application/json", host=None):
    c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=5)
    headers = {"Content-Type": ctype} if ctype else {}
    if host:
        headers["Host"] = host
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
    c.request(method, path, body=data, headers=headers)
    r = c.getresponse()
    out = r.read()
    acao = r.getheader("Access-Control-Allow-Origin")
    c.close()
    return r.status, (json.loads(out) if out else None), acao


def test_route_registered():
    assert ewapp.Handler.POST_ROUTES["/api/deadeye"] is ewapp.Handler._post_deadeye


def test_route_get(dsrv):
    st, doc, _ = _req(dsrv, "GET", "/api/deadeye")
    assert st == 200 and set(doc) == {"now", "levels", "sections", "plan", "progress", "stacks"}


def test_route_post_ops(dsrv):
    st, doc, acao = _req(dsrv, "POST", "/api/deadeye",
                         {"note": {"section": "crystals", "text": "**Addon**"}})
    assert st == 200 and acao is None and _sec(doc, "crystals")["text"] == "**Addon**"
    for item in ("A", "B"):
        st, doc, _ = _req(dsrv, "POST", "/api/deadeye",
                          {"add_step": {"item": item, "current": "+0", "target": "+15"}})
        assert st == 200
    st, doc, _ = _req(dsrv, "POST", "/api/deadeye", {"edit_step": {"id": "d1", "note": "n"}})
    assert st == 200 and doc["plan"][0]["note"] == "n"
    st, doc, _ = _req(dsrv, "POST", "/api/deadeye", {"step_done": {"id": "d1", "done": True}})
    assert st == 200 and doc["progress"] == {"done": 1, "total": 2}
    st, doc, _ = _req(dsrv, "POST", "/api/deadeye", {"move_step": {"id": "d2", "dir": -1}})
    assert st == 200 and [s["id"] for s in doc["plan"]] == ["d2", "d1"]
    st, doc, _ = _req(dsrv, "POST", "/api/deadeye", {"delete_step": "d2"})
    assert st == 200 and [s["id"] for s in doc["plan"]] == ["d1"]


def test_route_full_note_fits_cap(dsrv):
    # worst case BMP text: every char JSON-escaped as \uXXXX (6 bytes)
    text = chr(0xE9) * 20000
    body = json.dumps({"note": {"section": "misc", "text": text}}).encode()
    assert len(body) > ewapp.MAX_POST_BYTES
    st, doc, _ = _req(dsrv, "POST", "/api/deadeye", body)
    assert st == 200 and _sec(doc, "misc")["text"] == text


def test_route_cap_is_per_route(dsrv):
    big = json.dumps({"add_spot": "A", "pad": "x" * 5000}).encode()
    assert _req(dsrv, "POST", "/api/grind", big)[0] == 413
    huge = json.dumps({"note": {"section": "misc", "text": "x"},
                       "pad": "x" * ewapp.MAX_DEADEYE_POST_BYTES}).encode()
    assert _req(dsrv, "POST", "/api/deadeye", huge)[0] == 413


def test_route_post_guards(dsrv):
    body = {"add_step": {"item": "A", "current": "+0", "target": "+1"}}
    assert _req(dsrv, "POST", "/api/deadeye", body, host="evil.example.com")[0] == 403
    assert _req(dsrv, "POST", "/api/deadeye", body, ctype="text/plain")[0] == 415
    assert _req(dsrv, "GET", "/api/deadeye")[1]["plan"] == []
    assert _req(dsrv, "GET", "/api/deadeye", host="evil.example.com")[0] == 403


@pytest.mark.parametrize("body", [
    b"not json", b"[]", {}, {"nope": 1},
    {"delete_step": "d1", "move_step": {"id": "d1", "dir": 1}},
    {"note": {"section": "nope", "text": ""}},
    {"note": {"section": "misc", "text": "x" * 20001}},
    {"add_step": {"item": "A", "current": "+1", "target": "+1"}},
    {"edit_step": {"id": "d9", "item": "X"}}, {"step_done": {"id": "d9", "done": True}},
    {"delete_step": "d9"}, {"move_step": {"id": "d9", "dir": 1}},
])
def test_route_post_bad_body(dsrv, body):
    st, doc, _ = _req(dsrv, "POST", "/api/deadeye", body)
    assert st == 400 and "error" in doc


def test_state_reports_deadeye_source(dsrv):
    _req(dsrv, "POST", "/api/deadeye",
         {"add_step": {"item": "A", "current": "+0", "target": "+1"}})
    _, doc, _ = _req(dsrv, "GET", "/api/state")
    assert doc["sources"]["deadeye"] == {"done": 0, "total": 1}


def test_over_cap_413_is_never_a_reset(dsrv):
    # hand-off 08caa5c: an undrained 128 KiB+ body made the close a TCP reset
    huge = json.dumps({"note": {"section": "misc", "text": "x"},
                       "pad": "x" * (2 * ewapp.MAX_DEADEYE_POST_BYTES)}).encode()
    for _ in range(25):
        assert _req(dsrv, "POST", "/api/deadeye", huge)[0] == 413


# --- plan 036: failstack bank, Agris pity, cron budget ----------------------------

def _stacks(doc):
    return doc["stacks"]


def test_stacks_empty_view(svc):
    s = _stacks(svc.view())
    assert s["fs_bank"] == [] and s["agris"] == []
    assert s["crons"] == {"owned": 0, "weekly_income": 0}
    assert s["advice"] == {"step_id": None, "item": None, "family": None, "level": None,
                           "softcap_fs": None, "suggest": None, "agris": None,
                           "reason": "no open plan step"}
    assert s["budget"] == {"needed": 0, "owned": 0, "gap": 0, "weekly_income": 0, "weeks": 0,
                           "lines": [], "unknown": []}


def test_fs_add_merges_and_sorts(svc):
    svc.fs_add({"kind": "saved", "value": 120})
    svc.fs_add({"kind": "advice", "value": 50, "count": 2})
    doc = svc.fs_add({"kind": "saved", "value": 120, "count": 3})
    assert _stacks(doc)["fs_bank"] == [{"kind": "advice", "value": 50, "count": 2},
                                       {"kind": "saved", "value": 120, "count": 4}]


def test_fs_use_decrements_and_removes(svc):
    svc.fs_add({"kind": "cry", "value": 30, "count": 2})
    assert _stacks(svc.fs_use({"kind": "cry", "value": 30}))["fs_bank"] == [
        {"kind": "cry", "value": 30, "count": 1}]
    assert _stacks(svc.fs_use({"kind": "cry", "value": 30}))["fs_bank"] == []
    with pytest.raises(ValueError, match="no stored"):
        svc.fs_use({"kind": "cry", "value": 30})
    svc.fs_add({"kind": "cry", "value": 30})
    with pytest.raises(ValueError, match="only 1"):
        svc.fs_use({"kind": "cry", "value": 30, "count": 2})


@pytest.mark.parametrize("arg", [
    None, {}, {"kind": "advice"}, {"kind": "nope", "value": 10},
    {"kind": "advice", "value": 0}, {"kind": "advice", "value": 1000},
    {"kind": "advice", "value": True}, {"kind": "advice", "value": 10.5},
    {"kind": "advice", "value": 10, "count": 0}, {"kind": "advice", "value": 10, "count": 1000},
    {"kind": "advice", "value": 10, "extra": 1},
])
def test_fs_add_bad(svc, arg):
    with pytest.raises(ValueError):
        svc.fs_add(arg)
    assert _stacks(svc.view())["fs_bank"] == []


def test_fs_count_limits_inclusive(svc):
    svc.fs_add({"kind": "advice", "value": 999, "count": 999})
    with pytest.raises(ValueError, match="999"):
        svc.fs_add({"kind": "advice", "value": 999})
    assert _stacks(svc.view())["fs_bank"][0]["count"] == 999


def test_fs_bank_row_cap(svc):
    for v in range(1, deadeye.MAX_FS_ROWS + 1):
        svc.fs_add({"kind": "saved", "value": v})
    with pytest.raises(ValueError, match="at most"):
        svc.fs_add({"kind": "advice", "value": 1})
    svc.fs_add({"kind": "saved", "value": 1})  # merging into an existing row is fine


def test_agris_set_threshold_and_n_fails(svc):
    doc = svc.agris_set({"family": "sovereign", "step": "TET", "stacks": 7})
    assert _stacks(doc)["agris"] == [{"family": "sovereign", "step": "TET", "stacks": 7,
                                      "threshold": 20, "fails_to_guarantee": 13}]
    doc = svc.agris_set({"family": "sovereign", "step": "TET", "stacks": 25})
    assert _stacks(doc)["agris"][0]["fails_to_guarantee"] == 0
    doc = svc.agris_set({"family": "edana", "step": "PRI", "stacks": 2})  # no threshold
    row = next(r for r in _stacks(doc)["agris"] if r["family"] == "edana")
    assert row["threshold"] is None and row["fails_to_guarantee"] is None
    doc = svc.agris_set({"family": "sovereign", "step": "TET", "stacks": 0})  # 0 clears
    assert [r["family"] for r in _stacks(doc)["agris"]] == ["edana"]


@pytest.mark.parametrize("arg", [
    {}, {"family": "Sovereign", "step": "TET", "stacks": 1},
    {"family": "sovereign", "step": "ZZZ", "stacks": 1},
    {"family": "sovereign", "step": "TET", "stacks": -1},
    {"family": "sovereign", "step": "TET", "stacks": 1001},
    {"family": "sovereign", "step": "TET", "stacks": "3"},
    {"family": "sovereign\n", "step": "TET", "stacks": 1},
])
def test_agris_set_bad(svc, arg):
    with pytest.raises(ValueError):
        svc.agris_set(arg)


def test_crons_set_partial_and_bad(svc):
    assert _stacks(svc.crons_set({"owned": 5000}))["crons"] == {"owned": 5000, "weekly_income": 0}
    assert _stacks(svc.crons_set({"weekly_income": 700}))["crons"] == {
        "owned": 5000, "weekly_income": 700}
    for bad in ({}, {"owned": -1}, {"owned": 10 ** 9 + 1}, {"weekly_income": 1.5},
                {"owned": True}, {"owned": 1, "x": 2}):
        with pytest.raises(ValueError):
            svc.crons_set(bad)
    assert _stacks(svc.view())["crons"] == {"owned": 5000, "weekly_income": 700}


def test_advice_picks_closest_under_softcap(svc):
    _add(svc, item="Sovereign Ring", current="TRI", target="TET")
    for kind, value in (("advice", 80), ("saved", 100), ("cry", 101), ("saved", 60)):
        svc.fs_add({"kind": kind, "value": value})
    a = _stacks(svc.view())["advice"]
    assert (a["step_id"], a["family"], a["level"], a["softcap_fs"]) == ("d1", "sovereign", "TET", 100)
    assert a["suggest"] == {"kind": "saved", "value": 100} and a["reason"] is None
    assert a["agris"] == {"stacks": 0, "threshold": 20, "fails_to_guarantee": 20}


def test_advice_tie_prefers_kind_order_and_skips_done(svc):
    _add(svc, item="Kharazad Earring", current="+0", target="PRI")
    _add(svc, item="Sovereign Ring", current="TRI", target="TET")
    svc.step_done({"id": "d1", "done": True})
    svc.fs_add({"kind": "cry", "value": 90})
    svc.fs_add({"kind": "advice", "value": 90})
    a = _stacks(svc.view())["advice"]
    assert a["step_id"] == "d2" and a["suggest"] == {"kind": "advice", "value": 90}


def test_advice_accessory_from_zero_lands_on_pri(svc):
    _add(svc, item="Kharazad Earring", current="+0", target="DUO")
    svc.fs_add({"kind": "saved", "value": 41})
    svc.agris_set({"family": "kharazad", "step": "PRI", "stacks": 1})
    a = _stacks(svc.view())["advice"]
    assert a["level"] == "PRI" and a["softcap_fs"] == 40 and a["suggest"] is None
    assert "above the soft cap" in a["reason"]
    assert a["agris"] == {"stacks": 1, "threshold": 3, "fails_to_guarantee": 2}


def test_advice_reasons(svc):
    _add(svc, item="Mystery Bow", current="+15", target="PRI")
    assert _stacks(svc.view())["advice"]["reason"] == "no gear family named in the item"
    svc.edit_step({"id": "d1", "item": "Kharazad Bow", "current": "PRI", "target": "DUO"})
    a = _stacks(svc.view())["advice"]
    assert a["level"] == "DUO" and a["reason"] == "no soft cap for this level"
    svc.edit_step({"id": "d1", "current": "TRI", "target": "TET"})
    assert _stacks(svc.view())["advice"]["reason"] == "no stored stacks"


def _crons_needed(pct, cpa, threshold):
    p = pct / 100
    return cpa * (1 - (1 - p) ** threshold) / p


def test_budget_gap_and_weeks(svc):
    _add(svc, item="Sovereign Ring", current="TRI", target="PEN")
    _add(svc, item="Kharazad Ring", current="PRI", target="TRI")  # no chance data -> unknown
    svc.crons_set({"owned": 1000, "weekly_income": 500})
    b = _stacks(svc.view())["budget"]
    tet = _crons_needed(10.01, 780, 20)
    pen = _crons_needed(7.50, 970, 30)
    assert [(ln["step_id"], ln["level"]) for ln in b["lines"]] == [("d1", "TET"), ("d1", "PEN")]
    assert b["lines"][0]["crons_mean"] == pytest.approx(tet, abs=1e-9)
    assert b["needed"] == pytest.approx(tet + pen, abs=1e-6)
    assert b["gap"] == pytest.approx(tet + pen - 1000, abs=1e-6)
    assert b["weeks"] == math.ceil((tet + pen - 1000) / 500)
    assert b["unknown"] == [{"step_id": "d2", "family": "kharazad", "level": "DUO"},
                            {"step_id": "d2", "family": "kharazad", "level": "TRI"}]


def test_budget_uses_agris_stacks_and_covered(svc):
    _add(svc, item="Sovereign Ring", current="TRI", target="TET")
    svc.agris_set({"family": "sovereign", "step": "TET", "stacks": 20})
    b = _stacks(svc.view())["budget"]
    assert b["needed"] == 0 and b["gap"] == 0 and b["weeks"] == 0
    svc.agris_set({"family": "sovereign", "step": "TET", "stacks": 0})
    svc.crons_set({"owned": 10 ** 6})
    b = _stacks(svc.view())["budget"]
    assert b["needed"] > 0 and b["gap"] == 0 and b["weeks"] == 0


def test_budget_no_income_weeks_null(svc):
    _add(svc, item="Sovereign Ring", current="TRI", target="TET")
    b = _stacks(svc.view())["budget"]
    assert b["gap"] > 0 and b["weeks"] is None


def test_stacks_survive_plan_writes_and_corrupt_rows(tmp_path, clock):
    st = Store(tmp_path / "store")
    svc = deadeye.DeadeyeService(st, clock=clock)
    svc.fs_add({"kind": "saved", "value": 50})
    svc.crons_set({"owned": 9})
    _add(svc, item="A")
    svc.note({"section": "misc", "text": "x"})
    s = _stacks(svc.view())
    assert s["fs_bank"] == [{"kind": "saved", "value": 50, "count": 1}] and s["crons"]["owned"] == 9
    doc = st.get("deadeye")
    doc["fs_bank"] = [{"kind": "saved", "value": 50, "count": 1}, "junk",
                      {"kind": "saved", "value": 50, "count": 2},
                      {"kind": "x", "value": 1, "count": 1}]
    doc["agris"] = [{"family": "sovereign", "step": "TET", "stacks": 3}, {"family": 1}]
    doc["crons"] = {"owned": -5, "weekly_income": "x"}
    st.put("deadeye", doc)
    s = _stacks(deadeye.DeadeyeService(st, clock=clock).view())
    assert s["fs_bank"] == [{"kind": "saved", "value": 50, "count": 1}]
    assert [r["stacks"] for r in s["agris"]] == [3]
    assert s["crons"] == {"owned": 0, "weekly_income": 0}


def test_rates_injected(tmp_path, clock):
    rows = [{"family": "toy", "step": "PRI", "softcap_fs": 10, "max_pct_at_softcap": 50,
             "crons_per_attempt": 2, "source": "t", "verified": True}]
    svc = deadeye.DeadeyeService(Store(tmp_path / "s"), clock=clock, rates=lambda: rows)
    _add(svc, item="Toy Ring", current="+15", target="PRI")
    svc.fs_add({"kind": "advice", "value": 10})
    s = _stacks(svc.view())
    assert s["advice"]["suggest"] == {"kind": "advice", "value": 10}
    assert s["budget"]["needed"] == pytest.approx(2 / 0.5)


def test_route_stacks_ops(dsrv):
    st, doc, _ = _req(dsrv, "POST", "/api/deadeye", {"fs_add": {"kind": "advice", "value": 40}})
    assert st == 200 and doc["stacks"]["fs_bank"][0]["value"] == 40
    st, doc, _ = _req(dsrv, "POST", "/api/deadeye", {"fs_use": {"kind": "advice", "value": 40}})
    assert st == 200 and doc["stacks"]["fs_bank"] == []
    st, doc, _ = _req(dsrv, "POST", "/api/deadeye",
                      {"agris_set": {"family": "kharazad", "step": "PRI", "stacks": 2}})
    assert st == 200 and doc["stacks"]["agris"][0]["fails_to_guarantee"] == 1
    st, doc, _ = _req(dsrv, "POST", "/api/deadeye",
                      {"crons_set": {"owned": 3, "weekly_income": 1}})
    assert st == 200 and doc["stacks"]["crons"] == {"owned": 3, "weekly_income": 1}
    st, doc, _ = _req(dsrv, "POST", "/api/deadeye", {"fs_use": {"kind": "advice", "value": 40}})
    assert st == 400 and "error" in doc


# --- plan 055: Jetina boss-crystal planner + Caphras cost calculator -------------

def test_crystal_table_schema():
    t = deadeye.load_crystal()
    assert t["weekly"]["crystals"] == 155 and t["weekly"]["auras"] == 2
    assert t["weekly"]["reset_weekday"] == "Thursday"
    assert t["reform_cost"]["min"] == 60 and t["reform_cost"]["max"] == 120
    for part in (t["weekly"], t["reform_cost"]):
        assert isinstance(part["source"], str) and part["source"]
        assert isinstance(part["verified"], bool)


def test_caphras_table_schema():
    t = deadeye.load_caphras()
    assert t["item_id"] == 721003 and t["max_level"] == 20
    g = t["allowed_grades"]
    assert g["boss"]["allowed"] and g["green"]["allowed"]
    for name in ("blackstar", "kharazad", "sovereign"):
        assert g[name]["allowed"] is False and g[name]["verified"] is False
    for slot in t["slots"]:
        assert slot["grade"] in g and g[slot["grade"]]["allowed"]
        ends = [(r["from"], r["to"]) for r in slot["ranges"]]
        assert ends[0][0] == 0 and ends[-1][1] == t["max_level"]
        assert all(a[1] == b[0] for a, b in zip(ends, ends[1:]))
        for r in slot["ranges"]:
            assert r["stones"] > 0 and r["source"] and isinstance(r["verified"], bool)
    pen = next(s for s in t["slots"] if s["id"] == "boss_main_pen")
    assert [r["verified"] for r in pen["ranges"]] == [True, False]  # C11-C20 preview only


@pytest.mark.parametrize("bad", [
    {"weekly": {"crystals": 0, "auras": 2, "reset_weekday": "Thursday", "source": "s",
                "verified": True}},
    {"reform_cost": {"min": 120, "max": 60, "source": "s", "verified": True}},
    {"reform_cost": {"min": 60, "max": 120, "source": "", "verified": True}},
])
def test_crystal_validate_rejects(bad):
    doc = dict(deadeye.load_crystal(), **bad)
    with pytest.raises(ValueError):
        deadeye.validate_crystal(doc)


@pytest.mark.parametrize("ranges", [
    [{"from": 0, "to": 10, "stones": 1, "source": "s", "verified": True}],  # stops short of 20
    [{"from": 0, "to": 10, "stones": 1, "source": "s", "verified": True},
     {"from": 11, "to": 20, "stones": 1, "source": "s", "verified": True}],  # gap
    [{"from": 0, "to": 20, "stones": 0, "source": "s", "verified": True}],  # zero stones
    [{"from": 0, "to": 20, "stones": 5, "source": "s"}],  # no verified flag
])
def test_caphras_validate_rejects(ranges):
    doc = json.loads(json.dumps(deadeye.load_caphras()))
    doc["slots"][0]["ranges"] = ranges
    with pytest.raises(ValueError):
        deadeye.validate_caphras(doc)


def test_weeks_to_reform_band():
    r = deadeye.weeks_to_reform(100, 3)
    assert r["needed"] == {"min": 180, "max": 360}
    assert r["short"] == {"min": 80, "max": 260}
    assert r["weeks"] == {"min": 1, "max": 2}  # ceil(80/155), ceil(260/155)
    assert r["weekly"] == 155 and r["auras_per_week"] == 2 and r["reset"] == "Thursday"
    assert r["exact"] is False and r["levels"] == 3 and r["on_hand"] == 100


def test_weeks_to_reform_edges():
    covered = deadeye.weeks_to_reform(500, 2)
    assert covered["short"] == {"min": 0, "max": 0} and covered["weeks"] == {"min": 0, "max": 0}
    boundary = deadeye.weeks_to_reform(0, 1, per_level=155)
    assert boundary["weeks"] == {"min": 1, "max": 1} and boundary["exact"] is True
    over = deadeye.weeks_to_reform(0, 1, per_level=156)
    assert over["weeks"] == {"min": 2, "max": 2}
    assert deadeye.weeks_to_reform(0, 20)["weeks"] == {"min": 8, "max": 16}  # 1200/155, 2400/155


@pytest.mark.parametrize("args", [(-1, 1), (0, 0), (0, 21), (True, 1), (0, 1.5), (10 ** 8, 1)])
def test_weeks_to_reform_bad(args):
    with pytest.raises(ValueError):
        deadeye.weeks_to_reform(*args)


def test_weeks_to_reform_bad_per_level():
    for bad in (0, -5, 10 ** 6, "60"):
        with pytest.raises(ValueError):
            deadeye.weeks_to_reform(0, 1, per_level=bad)


def test_caphras_sums_match_fixtures():
    lo = deadeye.caphras_cost("boss_main_pen", 0, 10)
    assert lo["stones"] == 8895 and lo["approx"] is False and lo["verified"] is True
    hi = deadeye.caphras_cost("boss_main_pen", 10, 20)
    assert hi["stones"] == 29403 and hi["verified"] is False  # preview-only range
    full = deadeye.caphras_cost("boss_main_pen", 0, 20, price=2_000_000)
    assert full["stones"] == 8895 + 29403 == 38298
    assert full["silver"] == 38298 * 2_000_000 and full["price"] == 2_000_000
    assert full["item_id"] == 721003 and full["grade"] == "boss"
    assert lo["silver"] is None and lo["price"] is None


def test_caphras_prorated_inside_range_is_approx():
    r = deadeye.caphras_cost("boss_main_pen", 5, 15)
    assert r["approx"] is True and r["stones"] == round(8895 / 2 + 29403 / 2)
    assert deadeye.caphras_cost("boss_main_pen", 3, 3)["stones"] == 0


def test_caphras_guard_blackstar():
    with pytest.raises(ValueError, match="not usable on Blackstar"):
        deadeye.caphras_cost("boss_main_pen", 0, 10, grade="blackstar")
    for g in ("kharazad", "sovereign"):
        with pytest.raises(ValueError, match="not usable"):
            deadeye.caphras_cost("boss_main_pen", 0, 10, grade=g)
    assert deadeye.caphras_cost("boss_main_pen", 0, 10, grade="green")["grade"] == "green"


@pytest.mark.parametrize("args,kw", [
    (("nope", 0, 10), {}), (("boss_main_pen", 10, 5), {}), (("boss_main_pen", -1, 5), {}),
    (("boss_main_pen", 0, 21), {}), (("boss_main_pen", 0, 10), {"grade": "rainbow"}),
    (("boss_main_pen", 0, 10), {"price": -1}), (("boss_main_pen", 0, 10), {"price": True}),
])
def test_caphras_bad(args, kw):
    with pytest.raises(ValueError):
        deadeye.caphras_cost(*args, **kw)


def test_calc_query_uses_cached_price(tmp_path, clock):
    seen = []

    def price(iid):
        seen.append(iid)
        return 1_500_000
    svc = deadeye.DeadeyeService(Store(tmp_path / "s"), clock=clock, price=price)
    r = svc.calc({"kind": ["caphras"], "slot": ["boss_main_pen"], "from": ["0"], "to": ["10"]})
    assert seen == [721003] and r["silver"] == 8895 * 1_500_000 and r["price_source"] == "cache"
    r = svc.calc({"kind": ["caphras"], "slot": ["boss_main_pen"], "from": ["0"], "to": ["10"],
                  "price": ["1000"]})
    assert r["silver"] == 8895000 and r["price_source"] == "operator"
    r = svc.calc({"kind": ["crystal"], "on_hand": ["100"], "levels": ["3"]})
    assert r["weeks"] == {"min": 1, "max": 2}
    view = svc.calc({})
    assert view["crystal"]["weekly"]["crystals"] == 155
    assert view["caphras"]["slots"][0]["id"] == "boss_main_pen"
    assert view["caphras"]["price"] == 1_500_000


def test_calc_query_no_price(tmp_path, clock):
    svc = deadeye.DeadeyeService(Store(tmp_path / "s"), clock=clock)
    r = svc.calc({"kind": ["caphras"], "slot": ["boss_main_pen"], "from": ["0"], "to": ["20"]})
    assert r["silver"] is None and r["price_source"] is None


@pytest.mark.parametrize("q", [
    {"kind": ["x"]},
    {"kind": ["crystal"], "levels": ["3"]},
    {"kind": ["crystal"], "on_hand": ["a"], "levels": ["3"]},
    {"kind": ["caphras"], "slot": ["boss_main_pen"], "from": ["0"]},
    {"kind": ["caphras"], "slot": ["boss_main_pen"], "from": ["0"], "to": ["1e3"]},
    {"kind": ["caphras"], "slot": ["boss_main_pen"], "from": ["0"], "to": ["10"],
     "grade": ["blackstar"]},
])
def test_calc_query_bad(svc, q):
    with pytest.raises(ValueError):
        svc.calc(q)


def test_route_calc(dsrv):
    st, doc, _ = _req(dsrv, "GET", "/api/deadeye/calc?kind=crystal&on_hand=0&levels=1&per_level=60")
    assert st == 200 and doc["weeks"] == {"min": 1, "max": 1}
    st, doc, _ = _req(dsrv, "GET",
                      "/api/deadeye/calc?kind=caphras&slot=boss_main_pen&from=0&to=10&price=2")
    assert st == 200 and doc["stones"] == 8895 and doc["silver"] == 17790
    st, doc, _ = _req(dsrv, "GET", "/api/deadeye/calc")
    assert st == 200 and set(doc) == {"crystal", "caphras"}
    st, doc, _ = _req(dsrv, "GET", "/api/deadeye/calc?kind=caphras&slot=boss_main_pen&from=0"
                                   "&to=10&grade=blackstar")
    assert st == 400 and "Blackstar" in doc["error"]
