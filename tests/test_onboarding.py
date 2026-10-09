"""Plan 051: first-run checklist. Each step's `done` comes from real state -
config/local.json keys, the plan 008 folders (existence only) and the EW store -
and a dismiss flag lives in the store."""

import json
import threading

import pytest

from server.ew import app as ewapp
from server.ew import market, onboarding
from server.ew.store import Store
from tests.test_server import _get, _no_network, _post

IDS = ["family", "log", "screenshots", "overlay", "watch", "daily", "leveling"]
T0 = 1791201600.0  # 2026-10-05T12:00:00Z


def _cfg(tmp_path, doc):
    p = tmp_path / "config" / "local.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


def _done(view):
    return {s["id"]: s["done"] for s in view["steps"]}


def _svc(tmp_path, doc=None):
    cfg = _cfg(tmp_path, doc if doc is not None else {})
    store = Store(tmp_path / "store")
    return onboarding.OnboardingService(store, cfg, clock=lambda: T0), store, cfg


def _ticked(view):
    return [k for k, d in _done(view).items() if d]


def test_fresh_install_only_default_overlay_done(tmp_path):
    svc, _, _ = _svc(tmp_path)
    v = svc.view()
    assert [s["id"] for s in v["steps"]] == IDS
    assert _ticked(v) == ["overlay"]  # plan 065: the default corner kept counts
    assert v["done"] == 1 and v["total"] == len(IDS)
    assert v["complete"] is False and v["dismissed"] is False and v["show"] is True
    for s in v["steps"]:
        assert s["title"] and s["hint"] and s["link"]["tab"]


def test_missing_or_bad_config_is_not_an_error(tmp_path):
    store = Store(tmp_path / "store")
    v = onboarding.status(tmp_path, store, config_path=tmp_path / "nope.json")
    assert _ticked(v) == ["overlay"]
    bad = tmp_path / "bad.json"
    bad.write_text("{nope", encoding="utf-8")
    assert _ticked(onboarding.status(tmp_path, store, config_path=bad)) == ["overlay"]
    arr = tmp_path / "arr.json"
    arr.write_text("[1]", encoding="utf-8")
    assert _ticked(onboarding.status(tmp_path, store, config_path=arr)) == ["overlay"]


def test_status_defaults_to_root_config(tmp_path):
    _cfg(tmp_path, {"profile": {"family": "Moon"}})
    v = onboarding.status(tmp_path, Store(tmp_path / "store"))
    assert _done(v)["family"] is True


@pytest.mark.parametrize("family,ok", [("Moon", True), ("", False), ("a", False),
                                      ("bad name!", False), (7, False)])
def test_family(tmp_path, family, ok):
    svc, _, _ = _svc(tmp_path, {"profile": {"family": family}})
    assert _done(svc.view())["family"] is ok


def test_log_dir_needs_install_dir_with_log_folder(tmp_path):
    inst = tmp_path / "bdo"
    inst.mkdir()
    svc, _, _ = _svc(tmp_path, {"bdo": {"install_dir": str(inst)}})
    assert _done(svc.view())["log"] is False  # no Log folder yet
    (inst / "Log").mkdir()
    assert _done(svc.view())["log"] is True
    svc2, _, _ = _svc(tmp_path, {"bdo": {"install_dir": "   "}})
    assert _done(svc2.view())["log"] is False
    svc3, _, _ = _svc(tmp_path, {"bdo": {"install_dir": str(tmp_path / "missing")}})
    assert _done(svc3.view())["log"] is False
    svc4, _, _ = _svc(tmp_path, {"bdo": "x"})
    assert _done(svc4.view())["log"] is False


def test_screenshot_folder_readable(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    svc, _, _ = _svc(tmp_path, {"bdo": {"documents_dir": str(docs)}})
    assert _done(svc.view())["screenshots"] is False
    (docs / "ScreenShot").write_text("not a dir", encoding="utf-8")
    assert _done(svc.view())["screenshots"] is False
    (docs / "ScreenShot").unlink()
    (docs / "ScreenShot").mkdir()
    assert _done(svc.view())["screenshots"] is True
    assert _done(svc.view())["log"] is False  # independent steps


# ---- plan 077: folder steps from the signal digest ----

def _step(view, sid):
    return next(s for s in view["steps"] if s["id"] == sid)


@pytest.mark.parametrize("sid,key,sub", [("screenshots", "documents_dir", "ScreenShot"),
                                         ("log", "install_dir", "Log")])
def test_folder_step_three_states(tmp_path, sid, key, sub):
    parent = tmp_path / "parent"
    # unset -> the Settings field link
    s = _step(_svc(tmp_path)[0].view(), sid)
    assert s["done"] is False and s["link"] == {"tab": "settings", "field": "bdo." + key}
    assert "Settings > Game folders" in s["hint"] and "bdo." not in s["hint"]
    # set but the folder itself is gone -> still a Settings fix
    s = _step(_svc(tmp_path, {"bdo": {key: str(parent)}})[0].view(), sid)
    assert s["done"] is False and s["link"]["field"] == "bdo." + key
    # found, no subfolder yet -> an in-game act, no link (nothing to set)
    parent.mkdir()
    svc, _, _ = _svc(tmp_path, {"bdo": {key: str(parent)}})
    s = _step(svc.view(), sid)
    assert s["done"] is False and s["link"] is None
    assert s["title"] == onboarding.FOLDER_STEPS[sid]["title"]
    assert s["hint"] == onboarding.FOLDER_STEPS[sid]["hint"]
    # subfolder present -> done
    (parent / sub).mkdir()
    assert _step(svc.view(), sid)["done"] is True


def test_detected_documents_without_screenshot_is_the_screenshot_act(tmp_path):
    """The 0009 live state: Documents auto-detected, no ScreenShot folder yet."""
    docs = tmp_path / "docs"
    docs.mkdir()
    v = onboarding.status(tmp_path, Store(tmp_path / "store"), config_path=_cfg(tmp_path, {}),
                          detected={"install_dir": None, "documents_dir": str(docs)})
    s = _step(v, "screenshots")
    assert s["done"] is False and s["link"] is None
    assert s["hint"].startswith("Take one screenshot in game")
    assert v["show"] is True


def _digest(**levels):
    from server.ew import signals
    rows = {"session_log": {"configured": False}, "screenshots": {"configured": False}}
    rows.update(levels)
    return signals.digest(rows, T0)


def test_live_digest_wins_over_the_folders(tmp_path):
    cfg = _cfg(tmp_path, {})
    store = Store(tmp_path / "store")
    dig = _digest(screenshots={"configured": True, "documents_ok": True, "dir_ok": False,
                               "state": "not_running"},
                  session_log={"configured": True, "install_ok": True, "log_dir_ok": True,
                               "state": "not_running", "polled_at": T0})
    svc = onboarding.OnboardingService(store, cfg, clock=lambda: T0, signals=lambda: dig)
    v = svc.view()
    assert _step(v, "log")["done"] is True  # nothing on disk: the digest is the truth
    s = _step(v, "screenshots")
    assert s["done"] is False and s["link"] is None


@pytest.mark.parametrize("bad", [None, "junk", {"rows": "x"}, {"rows": [{"id": "screenshots"}]}])
def test_unreadable_digest_falls_back_to_the_folders(tmp_path, bad):
    docs = tmp_path / "docs"
    (docs / "ScreenShot").mkdir(parents=True)
    cfg = _cfg(tmp_path, {"bdo": {"documents_dir": str(docs)}})
    svc = onboarding.OnboardingService(Store(tmp_path / "store"), cfg, signals=lambda: bad)
    s = _step(svc.view(), "screenshots")
    if bad == {"rows": [{"id": "screenshots"}]}:  # a row with no level keeps the static step
        assert s["done"] is False and s["link"]["field"] == "bdo.documents_dir"
    else:
        assert s["done"] is True

    def boom():
        raise RuntimeError("x")
    svc.signals = boom
    assert _step(svc.view(), "screenshots")["done"] is True


def test_no_step_hint_names_a_config_key(tmp_path):
    for _, _, hint, _, _ in onboarding.STEPS:
        assert "bdo." not in hint and "profile.family" not in hint and "overlay.anchor" not in hint


@pytest.mark.parametrize("anchor,ok", [("tr", True), ("ml", True), ({"x": 10, "y": 20}, True),
                                      ("zz", False), (None, False), ({"x": 1}, False)])
def test_overlay_anchor_key_present_and_valid(tmp_path, anchor, ok):
    svc, _, _ = _svc(tmp_path, {"overlay": {"anchor": anchor}})
    assert _done(svc.view())["overlay"] is ok


def test_overlay_anchor_absent_is_the_default_kept(tmp_path):
    svc, _, _ = _svc(tmp_path, {"overlay": {"scale": 1.2}})
    assert _done(svc.view())["overlay"] is True  # plan 065 auto-tick


def test_watch_needs_three_items(tmp_path):
    svc, store, _ = _svc(tmp_path)
    wl = market.Watchlist(store)
    for n, iid in enumerate((11, 12, 13)):
        assert _done(svc.view())["watch"] is False, n
        wl.add({"id": iid, "sid": 0})
    assert _done(svc.view())["watch"] is True
    store.put("market", {"watch": "junk"})
    assert _done(svc.view())["watch"] is False


def test_daily_needs_a_ticked_daily(tmp_path):
    from server.ew import today
    svc, store, _ = _svc(tmp_path)
    t = today.TodayService(store, clock=lambda: T0)
    assert _done(svc.view())["daily"] is False  # the seed list alone is not set-up
    weekly = next(i for i in t.view()["items"] if i["kind"] == "weekly")
    t.tick(weekly["id"])
    assert _done(svc.view())["daily"] is False
    daily = next(i for i in t.view()["items"] if i["kind"] == "daily")
    t.tick(daily["id"])
    assert _done(svc.view())["daily"] is True
    t.untick(daily["id"])
    assert _done(svc.view())["daily"] is False


def test_leveling_needs_a_typed_sample(tmp_path):
    svc, store, _ = _svc(tmp_path)
    store.put("leveling", {"samples": [{"ts": "2026-10-05T10:00:00+00:00", "level": 61,
                                        "pct": None, "source": "profile"}]})
    assert _done(svc.view())["leveling"] is False  # a profile marker is not a sample
    store.put("leveling", {"samples": [{"ts": "2026-10-05T10:00:00+00:00", "level": 61,
                                        "pct": 12.5}]})
    assert _done(svc.view())["leveling"] is True


def _all_done(tmp_path):
    inst, docs = tmp_path / "bdo", tmp_path / "docs"
    (inst / "Log").mkdir(parents=True)
    (docs / "ScreenShot").mkdir(parents=True)
    svc, store, cfg = _svc(tmp_path, {"profile": {"family": "Moon"}, "overlay": {"anchor": "tr"},
                                      "bdo": {"install_dir": str(inst), "documents_dir": str(docs)}})
    store.put("market", {"watch": [{"id": i, "sid": 0, "below": None, "above": None}
                                   for i in (1, 2, 3)]})
    store.put("today", {"items": [{"id": "barter", "title": "Barter", "kind": "daily",
                                   "until": None, "order": 0}],
                        "ticks": {"barter": "2026-10-05T11:00:00+00:00"}})
    store.put("leveling", {"samples": [{"ts": "2026-10-05T10:00:00+00:00", "level": 61,
                                        "pct": 1.0}]})
    return svc


def test_all_steps_done_hides_the_card(tmp_path):
    v = _all_done(tmp_path).view()
    assert all(_done(v).values())
    assert v["complete"] is True and v["show"] is False and v["done"] == v["total"]


def test_dismiss_and_restore(tmp_path):
    svc, store, _ = _svc(tmp_path)
    v = svc.dismiss(True)
    assert v["dismissed"] is True and v["show"] is False
    assert v["dismissed_at"] == "2026-10-05T12:00:00+00:00"
    assert store.get("onboarding")["dismissed"] == "2026-10-05T12:00:00+00:00"
    # a fresh service over the same store keeps it (stored, not in memory)
    again = onboarding.OnboardingService(store, tmp_path / "config" / "local.json")
    assert again.view()["dismissed"] is True
    v = svc.restore(True)
    assert v["dismissed"] is False and v["show"] is True and v["dismissed_at"] is None


@pytest.mark.parametrize("arg", [False, None, 1, "yes", {}])
def test_dismiss_and_restore_take_only_true(tmp_path, arg):
    svc, store, _ = _svc(tmp_path)
    with pytest.raises(ValueError):
        svc.dismiss(arg)
    with pytest.raises(ValueError):
        svc.restore(arg)
    assert store.get("onboarding") == {}


def test_corrupt_dismiss_value_reads_as_not_dismissed(tmp_path):
    svc, store, _ = _svc(tmp_path)
    store.put("onboarding", {"dismissed": 5})
    assert svc.view()["dismissed"] is False


def test_view_never_leaks_paths(tmp_path):
    v = _all_done(tmp_path).view()
    assert str(tmp_path) not in json.dumps(v)


# ---- HTTP ----

@pytest.fixture()
def srv(tmp_path):
    cfg = _cfg(tmp_path, {"profile": {"family": "Moon"}})
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          sse_interval=0.05, market_seed=[],
                          market_client=market.ArshaClient(fetch=_no_network,
                                                           cache_dir=tmp_path / "cache"),
                          profile_cfg={}, config_path=cfg)
    t = threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    t.start()
    yield s
    s.shutdown()
    s.server_close()


def test_http_get_reads_the_injected_config(srv):
    st, _, body = _get(srv, "/api/onboarding")
    v = json.loads(body)
    assert st == 200 and [s["id"] for s in v["steps"]] == IDS
    assert _done(v)["family"] is True
    assert _done(v)["daily"] is False  # TodayService seeded, nothing ticked


def test_http_post_dismiss_and_restore(srv):
    st, out = _post(srv, "/api/onboarding", {"dismiss": True})
    assert st == 200 and out["dismissed"] is True and out["show"] is False
    assert json.loads(_get(srv, "/api/onboarding")[2])["dismissed"] is True
    st, out = _post(srv, "/api/onboarding", {"restore": True})
    assert st == 200 and out["dismissed"] is False


@pytest.mark.parametrize("body", [{}, {"dismiss": False}, {"nope": True},
                                  {"dismiss": True, "restore": True}])
def test_http_post_rejects(srv, body):
    st, out = _post(srv, "/api/onboarding", body)
    assert st == 400 and out["error"]
