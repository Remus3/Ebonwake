"""Plan 013: season pass tracked by objective - seed, plan 004 migration (done
marks kept), level auto-tick from plan 011 samples / the character level,
claim marks, objective edits, the `season` summary and the POST routes.

No network: the progress services here have no profile client.
"""

import http.client
import json
import threading

import pytest

from server.ew import app as ewapp
from server.ew import leveling, market, progress
from server.ew.store import Store

T0 = 2_000_000.0


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


class Level:
    def __init__(self, v=None):
        self.v = v

    def __call__(self):
        return self.v


def _svc(tmp_path, level=None, clock=None):
    return progress.ProgressService(Store(tmp_path / "store"), None, clock=clock or Clock(),
                                    level=level)


def _season(v):
    return next(t for t in v["tracks"] if t["kind"] == "season")


def _obj(t, oid):
    return next(o for o in t["objectives"] if o["id"] == oid)


COARSE = [("level-10", "Level 10"), ("level-20", "Level 20"), ("level-30", "Level 30"),
          ("level-40", "Level 40"), ("level-50", "Level 50"), ("graduate", "Graduate")]


def _plan004_store(tmp_path, done=()):
    st = Store(tmp_path / "store")
    st.put("progress", {"character": {"cls": "Deadeye", "level": None,
                                      "gs": {"ap": None, "aap": None, "dp": None}},
                        "tracks": [
                            {"id": "main-story", "title": "Main story", "kind": "quest",
                             "steps": [{"id": "balenos", "title": "Balenos",
                                        "done_at": "2026-10-01T00:00:00+00:00"}]},
                            {"id": "season-pass", "title": "Season pass", "kind": "season",
                             "steps": [{"id": i, "title": t,
                                        "done_at": "2026-10-02T00:00:00+00:00"
                                        if i in done else None} for i, t in COARSE]}]})
    return st


# --- seed ------------------------------------------------------------------

def test_seed_is_objectives_structure_only(tmp_path):
    t = _season(_svc(tmp_path).view())
    assert t["id"] == "season-pass" and t["seed"] is True
    objs = t["objectives"]
    assert 10 <= len(objs) <= progress.MAX_STEPS
    assert {o["kind"] for o in objs} == set(progress.OBJ_KINDS)
    for o in objs:
        assert set(o) >= {"id", "title", "kind", "target", "reward", "done_at", "claimed_at",
                          "done", "claimed", "auto"}
        assert o["done"] is False and o["claimed"] is False and o["reward"] == ""
        if o["kind"] == "level":
            assert isinstance(o["target"], int) and 1 <= o["target"] <= 70
        if o["kind"] in ("quest", "other"):
            assert o["target"] is None
    # steps mirror keeps the plan 004 counters working
    assert [s["id"] for s in t["steps"]] == [o["id"] for o in objs]
    assert t["total"] == len(objs) and t["done"] == 0 and t["pct"] == 0
    # the coarse plan 004 targets all exist so a migration has somewhere to land
    lv = {o["target"] for o in objs if o["kind"] == "level"}
    assert {10, 20, 30, 40, 50} <= lv
    assert any(o["id"] == "graduate" for o in objs)


def test_other_tracks_unchanged(tmp_path):
    v = _svc(tmp_path).view()
    assert [t["kind"] for t in v["tracks"]] == ["quest", "season", "gear"]
    for t in v["tracks"]:
        if t["kind"] != "season":
            assert "objectives" not in t


# --- migration -----------------------------------------------------------

def test_migration_from_plan004_keeps_done_marks(tmp_path):
    st = _plan004_store(tmp_path, done={"level-10", "level-30", "graduate"})
    s = progress.ProgressService(st, None, clock=Clock())
    t = _season(s.view())
    assert t["seed"] is True
    done = {o["id"]: o for o in t["objectives"] if o["done"]}
    targets = sorted(o["target"] for o in done.values() if o["kind"] == "level")
    assert targets == [10, 30]
    assert "graduate" in done
    assert all(o["done_at"] == "2026-10-02T00:00:00+00:00" for o in done.values())
    assert t["done"] == 3
    # other tracks untouched; migration persisted (raw store now has objectives)
    raw = st.get("progress")["tracks"]
    assert raw[0]["steps"][0]["done_at"] == "2026-10-01T00:00:00+00:00"
    assert isinstance(raw[1].get("objectives"), list) and "steps" not in raw[1]
    # idempotent: a second service sees the same thing
    t2 = _season(progress.ProgressService(st, None, clock=Clock()).view())
    assert [(o["id"], o["done_at"]) for o in t2["objectives"]] == \
        [(o["id"], o["done_at"]) for o in t["objectives"]]


def test_migration_of_custom_season_track_is_one_to_one(tmp_path):
    st = Store(tmp_path / "store")
    st.put("progress", {"tracks": [
        {"id": "my-pass", "title": "My pass", "kind": "season",
         "steps": [{"id": "lv-45", "title": "Lv 45", "done_at": "2026-10-02T00:00:00+00:00"},
                   {"id": "level-61", "title": "Level 61", "done_at": None},
                   {"id": "boss", "title": "Kill a boss", "done_at": None}]}]})
    t = _season(progress.ProgressService(st, None, clock=Clock()).view())
    assert t["seed"] is False
    assert [(o["id"], o["kind"], o["target"]) for o in t["objectives"]] == \
        [("lv-45", "level", 45), ("level-61", "level", 61), ("boss", "other", None)]
    assert t["objectives"][0]["done_at"] == "2026-10-02T00:00:00+00:00"


# --- auto-tick -----------------------------------------------------------

def test_level_objectives_auto_tick_from_level_source(tmp_path):
    lv = Level(None)
    clk = Clock()
    s = _svc(tmp_path, level=lv, clock=clk)
    assert _season(s.view())["done"] == 0
    lv.v = 31
    t = _season(s.view())
    lvl = [o for o in t["objectives"] if o["kind"] == "level"]
    assert all(o["done"] == (o["target"] <= 31) for o in lvl)
    assert all(o["auto"] == (o["target"] <= 31) for o in lvl)
    assert t["level"] == 31
    stamp = _obj(t, "reach-lv-10")["done_at"]
    assert stamp is not None
    # stamp persisted and kept; a later read does not restamp
    clk.t += 500
    assert _obj(_season(s.view()), "reach-lv-10")["done_at"] == stamp
    # level source going away keeps the done marks (stamped, not derived)
    lv.v = None
    assert _obj(_season(s.view()), "reach-lv-10")["done"] is True


def test_character_level_also_ticks_and_max_wins(tmp_path):
    lv = Level(12)
    s = _svc(tmp_path, level=lv)
    v = s.set_character({"level": 41})
    t = _season(v)
    assert t["level"] == 41 and _obj(t, "reach-lv-40")["done"] is True
    lv.v = 52
    assert _season(s.view())["level"] == 52


def test_untick_of_reached_level_objective_refused(tmp_path):
    s = _svc(tmp_path, level=Level(25))
    with pytest.raises(ValueError, match="level"):
        s.step({"track": "season-pass", "step": "reach-lv-20", "done": False})
    # an unreached one can be ticked and unticked by hand
    s.step({"track": "season-pass", "step": "reach-lv-30", "done": True})
    v = s.step({"track": "season-pass", "step": "reach-lv-30", "done": False})
    assert _obj(_season(v), "reach-lv-30")["done"] is False


def test_leveling_current_level(tmp_path):
    clk = Clock()
    lev = leveling.LevelingService(Store(tmp_path / "store"), clock=clk)
    assert lev.current_level() is None
    lev.sample({"level": 33, "pct": 12.5})
    assert lev.current_level() == 33


# --- claim ---------------------------------------------------------------

def test_claim_needs_done_and_untick_clears_claim(tmp_path):
    s = _svc(tmp_path)
    arg = {"track": "season-pass", "objective": "graduate", "claimed": True}
    with pytest.raises(ValueError, match="not done"):
        s.claim(arg)
    s.step({"track": "season-pass", "step": "graduate", "done": True})
    t = _season(s.view())
    assert t["unclaimed"] == ["graduate"] and t["claimed"] == 0
    t = _season(s.claim(arg))
    o = _obj(t, "graduate")
    assert o["claimed"] is True and o["claimed_at"].endswith("+00:00")
    assert t["unclaimed"] == [] and t["claimed"] == 1
    first = o["claimed_at"]
    assert _obj(_season(s.claim(arg)), "graduate")["claimed_at"] == first  # idempotent
    t = _season(s.claim(dict(arg, claimed=False)))
    assert _obj(t, "graduate")["claimed"] is False
    s.claim(arg)
    t = _season(s.step({"track": "season-pass", "step": "graduate", "done": False}))
    assert _obj(t, "graduate")["claimed_at"] is None


@pytest.mark.parametrize("arg", [None, {}, {"track": "season-pass", "objective": "graduate"},
                                 {"track": "season-pass", "objective": "graduate",
                                  "claimed": 1},
                                 {"track": "season-pass", "objective": "nope", "claimed": True},
                                 {"track": "main-story", "objective": "balenos",
                                  "claimed": True}])
def test_claim_validation(tmp_path, arg):
    with pytest.raises(ValueError):
        _svc(tmp_path).claim(arg)


# --- summary -------------------------------------------------------------

def test_next_three_open_and_summary(tmp_path):
    s = _svc(tmp_path, level=Level(18))
    v = s.view()
    t = _season(v)
    open_ids = [o["id"] for o in t["objectives"] if not o["done"]]
    assert [n["id"] for n in t["next"]] == open_ids[:3]
    lv_next = next(n for n in t["next"] if n["kind"] == "level")
    assert lv_next["gap"] == lv_next["target"] - 18
    sm = v["season"]
    assert sm["track"] == "season-pass" and sm["total"] == t["total"]
    assert sm["done"] == t["done"] and sm["next"] == t["next"] and sm["level"] == 18
    assert sm["unclaimed"] == ["reach-lv-10"] and sm["seed"] is True  # auto-ticked, unclaimed


def test_summary_none_without_season_track(tmp_path):
    s = _svc(tmp_path)
    s.remove_track("season-pass")
    assert s.view()["season"] is None


def test_gap_none_without_level(tmp_path):
    t = _season(_svc(tmp_path).view())
    assert t["level"] is None
    assert all(o["gap"] is None for o in t["objectives"])


# --- edits ---------------------------------------------------------------

def test_objective_add_edit_delete_clear_seed(tmp_path):
    s = _svc(tmp_path)
    n0 = _season(s.view())["total"]
    t = _season(s.obj_add({"track": "season-pass", "title": "Reach Lv 61", "kind": "level",
                           "target": 61, "reward": "Tuvala ring"}))
    assert t["seed"] is False and t["total"] == n0 + 1
    o = t["objectives"][-1]
    assert (o["id"], o["kind"], o["target"], o["reward"]) == \
        ("reach-lv-61", "level", 61, "Tuvala ring")
    t = _season(s.obj_edit({"track": "season-pass", "objective": "reach-lv-61",
                            "reward": "", "title": "Lv 61"}))
    o = _obj(t, "reach-lv-61")
    assert o["title"] == "Lv 61" and o["reward"] == "" and o["target"] == 61
    t = _season(s.obj_edit({"track": "season-pass", "objective": "reach-lv-61",
                            "kind": "quest", "target": None}))
    assert _obj(t, "reach-lv-61")["kind"] == "quest"
    t = _season(s.obj_del({"track": "season-pass", "objective": "reach-lv-61"}))
    assert t["total"] == n0


def test_objective_edit_keeps_marks_and_kind_change_drops_target(tmp_path):
    s = _svc(tmp_path)
    s.step({"track": "season-pass", "step": "reach-lv-10", "done": True})
    s.claim({"track": "season-pass", "objective": "reach-lv-10", "claimed": True})
    t = _season(s.obj_edit({"track": "season-pass", "objective": "reach-lv-10",
                            "kind": "quest"}))  # no target sent: level 10 dropped
    o = _obj(t, "reach-lv-10")
    assert (o["kind"], o["target"]) == ("quest", None)
    assert o["done"] is True and o["claimed"] is True
    assert [x["id"] for x in t["objectives"]].index("reach-lv-10") == 0  # position kept


def test_objective_tick_keeps_seed_flag(tmp_path):
    s = _svc(tmp_path)
    v = s.step({"track": "season-pass", "step": "graduate", "done": True})
    assert _season(v)["seed"] is True


@pytest.mark.parametrize("arg", [
    {"track": "season-pass", "title": "x", "kind": "level"},             # level needs target
    {"track": "season-pass", "title": "x", "kind": "level", "target": 71},
    {"track": "season-pass", "title": "x", "kind": "gear", "target": 21},
    {"track": "season-pass", "title": "x", "kind": "quest", "target": 3},
    {"track": "season-pass", "title": "x", "kind": "bogus"},
    {"track": "season-pass", "title": "", "kind": "other"},
    {"track": "season-pass", "title": "x", "kind": "other", "reward": "a" * 81},
    {"track": "season-pass", "title": "x", "kind": "other", "extra": 1},
    {"track": "main-story", "title": "x", "kind": "other"},              # not a season track
    {"track": "season-pass", "title": "x", "kind": "level", "target": True},
])
def test_objective_add_validation(tmp_path, arg):
    with pytest.raises(ValueError):
        _svc(tmp_path).obj_add(arg)


def test_objective_edit_validation(tmp_path):
    s = _svc(tmp_path)
    with pytest.raises(ValueError):
        s.obj_edit({"track": "season-pass", "objective": "graduate"})  # nothing to edit
    with pytest.raises(ValueError):
        s.obj_edit({"track": "season-pass", "objective": "graduate", "kind": "level"})
    with pytest.raises(ValueError):
        s.obj_edit({"track": "season-pass", "objective": "nope", "title": "x"})
    with pytest.raises(ValueError):
        s.obj_del({"track": "season-pass", "objective": "nope"})


def test_objective_cap(tmp_path):
    s = _svc(tmp_path)
    n = _season(s.view())["total"]
    for i in range(progress.MAX_STEPS - n):
        s.obj_add({"track": "season-pass", "title": f"o {i}", "kind": "other"})
    with pytest.raises(ValueError):
        s.obj_add({"track": "season-pass", "title": "one more", "kind": "other"})


def test_add_season_track_infers_level_objectives(tmp_path):
    s = _svc(tmp_path, level=Level(20))
    v = s.add_track({"title": "Alt pass", "kind": "season", "steps": ["Level 15", "Beat boss"]})
    t = next(t for t in v["tracks"] if t["id"] == "alt-pass")
    assert [(o["kind"], o["target"], o["done"]) for o in t["objectives"]] == \
        [("level", 15, True), ("other", None, False)]
    assert t["seed"] is False


def test_corrupt_objectives_degrade(tmp_path):
    st = Store(tmp_path / "store")
    st.put("progress", {"tracks": [{"id": "sp", "title": "SP", "kind": "season", "seed": "x",
                                    "objectives": [
                                        "junk",
                                        {"id": "a", "title": "A", "kind": "level", "target": 99},
                                        {"id": "b", "title": "B", "kind": "level", "target": 9,
                                         "reward": 5, "done_at": "bad", "claimed_at": "bad"},
                                        {"id": "b", "title": "dup", "kind": "other"},
                                        {"id": "c", "title": "C", "kind": "gear", "target": 99}]}]})
    t = _season(progress.ProgressService(st, None, clock=Clock()).view())
    assert t["seed"] is False
    assert [(o["id"], o["kind"], o["target"], o["reward"]) for o in t["objectives"]] == \
        [("b", "level", 9, ""), ("c", "gear", None, "")]
    assert t["objectives"][0]["done_at"] is None and t["objectives"][0]["claimed_at"] is None


def test_claim_without_done_is_dropped_on_load(tmp_path):
    st = Store(tmp_path / "store")
    st.put("progress", {"tracks": [{"id": "sp", "title": "SP", "kind": "season",
                                    "objectives": [{"id": "a", "title": "A", "kind": "other",
                                                    "claimed_at": "2026-10-02T00:00:00+00:00"}]}]})
    o = _season(progress.ProgressService(st, None, clock=Clock()).view())["objectives"][0]
    assert o["claimed_at"] is None and o["claimed"] is False


# --- routes --------------------------------------------------------------

@pytest.fixture()
def srv(tmp_path):
    def no_net(url, timeout):
        raise AssertionError("network")
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          sse_interval=0.05, market_seed=[], profile_cfg={},
                          market_client=market.ArshaClient(fetch=no_net,
                                                           cache_dir=tmp_path / "cache"))
    th = threading.Thread(target=s.serve_forever, daemon=True)
    th.start()
    yield s
    s.shutdown()
    s.server_close()


def _req(s, method, path, body=None):
    c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=5)
    headers = {"Content-Type": "application/json"} if body is not None else {}
    c.request(method, path, body=None if body is None else json.dumps(body).encode(),
              headers=headers)
    r = c.getresponse()
    out = r.read()
    c.close()
    return r.status, json.loads(out) if out else None


def test_route_season_ops_and_leveling_feed(srv):
    st, doc = _req(srv, "GET", "/api/progress")
    assert st == 200 and doc["season"]["track"] == "season-pass"
    st, _ = _req(srv, "POST", "/api/leveling", {"sample": {"level": 22, "pct": 1.5}})
    assert st == 200
    doc = _req(srv, "GET", "/api/progress")[1]
    assert doc["season"]["level"] == 22
    assert _obj(_season(doc), "reach-lv-20")["done"] is True
    st, doc = _req(srv, "POST", "/api/progress",
                   {"claim": {"track": "season-pass", "objective": "reach-lv-20",
                              "claimed": True}})
    assert st == 200 and _obj(_season(doc), "reach-lv-20")["claimed"] is True
    st, doc = _req(srv, "POST", "/api/progress",
                   {"obj_add": {"track": "season-pass", "title": "Extra", "kind": "other"}})
    assert st == 200 and _season(doc)["objectives"][-1]["id"] == "extra"
    st, doc = _req(srv, "POST", "/api/progress",
                   {"obj_edit": {"track": "season-pass", "objective": "extra",
                                 "reward": "Box"}})
    assert st == 200 and _obj(_season(doc), "extra")["reward"] == "Box"
    st, doc = _req(srv, "POST", "/api/progress",
                   {"obj_del": {"track": "season-pass", "objective": "extra"}})
    assert st == 200 and all(o["id"] != "extra" for o in _season(doc)["objectives"])


@pytest.mark.parametrize("body", [{"claim": {"track": "season-pass"}},
                                  {"obj_add": {"track": "season-pass"}},
                                  {"obj_del": "x"},
                                  {"step": {"track": "season-pass", "step": "reach-lv-10",
                                            "done": False}}])
def test_route_season_bad_bodies(srv, body):
    _req(srv, "POST", "/api/leveling", {"sample": {"level": 22, "pct": 1.5}})
    assert _req(srv, "POST", "/api/progress", body)[0] == 400
