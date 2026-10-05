"""Plan 007 slice A: deadeye store domain (build notes + enhancement plan), routes.

Text only: nothing here is executed or sent anywhere. The clock is injected so
`updated` stamps are exact.
"""

import datetime as dt
import http.client
import json
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
    t = threading.Thread(target=s.serve_forever, daemon=True)
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
    assert st == 200 and set(doc) == {"now", "levels", "sections", "plan", "progress"}


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
