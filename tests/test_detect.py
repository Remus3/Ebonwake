"""Plan 065: zero-config path auto-detect over a fake fs (no real disk, no
registry, no drive-path literal), precedence, re-detect and onboarding auto-tick."""

import json
import threading

import pytest

from server.ew import app as ewapp
from server.ew import detect, gamewatch, market, onboarding, settings
from server.ew.store import Store
from tests.test_server import _get, _no_network, _post

T0 = 1_790_000_000.0
HOME = "/fake/home"
REDIR = "/fake/cloud/Docs"
STEAM = "/fake/steam"
LIB2 = "/fake/lib2"


def _n(p):
    p = str(p).replace("\\", "/")
    while "//" in p:
        p = p.replace("//", "/")
    return p.rstrip("/") or "/"


class FakeFS:
    """Dirs + files keyed by normalised path; records every read."""

    def __init__(self, dirs=(), files=None):
        self.files = {_n(k): v for k, v in (files or {}).items()}
        self.dirs = set()
        for d in list(dirs) + list(self.files):
            parts = _n(d).split("/")
            for i in range(2, len(parts) + 1):
                self.dirs.add("/".join(parts[:i]))
        for f in self.files:
            self.dirs.discard(f)
        self.reads = []

    def is_dir(self, p):
        return _n(p) in self.dirs

    def is_file(self, p):
        return _n(p) in self.files

    def read_text(self, p):
        self.reads.append(_n(p))
        try:
            return self.files[_n(p)]
        except KeyError:
            raise OSError(p) from None


def vdf(*libs):
    rows = []
    for i, lib in enumerate(libs):
        esc = lib.replace("\\", "\\\\")
        rows.append(f'\t"{i}"\n\t{{\n\t\t"path"\t\t"{esc}"\n\t\t"label"\t\t""\n'
                    f'\t\t"apps"\n\t\t{{\n\t\t\t"228980"\t\t"1"\n\t\t}}\n\t}}\n')
    return '"libraryfolders"\n{\n' + "".join(rows) + "}\n"


ACF = '"AppState"\n{\n\t"appid"\t\t"582660"\n\t"installdir"\t\t"Black Desert Online"\n}\n'


def reg(values):
    return lambda hive, key, name: values.get((hive, key, name))


SHELL = (detect.HKCU, detect.USER_SHELL_FOLDERS, "Personal")
STEAM_KEY = (detect.HKCU, detect.STEAM_KEY, "SteamPath")


def world(game_in_lib2=True, manifest=True, redirected=True, shot=True):
    docs = (REDIR if redirected else HOME + "/Documents") + "/Black Desert"
    dirs = [STEAM + "/steamapps/common", LIB2 + "/steamapps/common"]
    files = {STEAM + "/steamapps/libraryfolders.vdf": vdf(STEAM, LIB2)}
    if shot:
        dirs.append(docs + "/ScreenShot")
    else:
        dirs.append(docs)
    lib = LIB2 if game_in_lib2 else STEAM
    dirs.append(lib + "/steamapps/common/Black Desert Online/Log")
    if manifest:
        files[lib + "/steamapps/appmanifest_582660.acf"] = ACF
    fs = FakeFS(dirs, files)
    regv = {STEAM_KEY: STEAM}
    if redirected:
        regv[SHELL] = "%OneDriveRoot%/Docs"
    env = {"USERPROFILE": HOME, "OneDriveRoot": "/fake/cloud"}
    return fs, env, reg(regv)


def run(fs, env, registry):
    return detect.detect(fs=fs, env=env, registry=registry)


# ---- documents dir ----

def test_redirected_documents_from_shell_folder():
    fs, env, r = world()
    assert _n(run(fs, env, r)["documents_dir"]) == REDIR + "/Black Desert"


def test_documents_fallback_userprofile():
    fs, env, r = world(redirected=False)
    assert _n(run(fs, env, r)["documents_dir"]) == HOME + "/Documents/Black Desert"


def test_documents_needs_screenshot_or_game_option():
    fs, env, r = world(shot=False)
    assert run(fs, env, r)["documents_dir"] is None
    fs.files[_n(REDIR + "/Black Desert/GameOption.txt")] = "x"
    assert _n(run(fs, env, r)["documents_dir"]) == REDIR + "/Black Desert"
    assert not any("GameOption" in p for p in fs.reads)  # existence only, never opened


def test_shell_folder_pointing_nowhere_falls_back():
    fs, env, _ = world(redirected=False)
    r = reg({SHELL: "/fake/gone", STEAM_KEY: STEAM})
    assert _n(run(fs, env, r)["documents_dir"]) == HOME + "/Documents/Black Desert"


# ---- install dir ----

def test_game_in_second_steam_library():
    fs, env, r = world()
    assert _n(run(fs, env, r)["install_dir"]) == LIB2 + "/steamapps/common/Black Desert Online"


def test_game_in_default_library():
    fs, env, r = world(game_in_lib2=False)
    assert _n(run(fs, env, r)["install_dir"]) == STEAM + "/steamapps/common/Black Desert Online"


def test_manifest_confirmed_library_wins_over_a_leftover_folder():
    fs, env, r = world()  # game + manifest in LIB2
    fs.dirs.add(_n(STEAM + "/steamapps/common/Black Desert Online"))  # stale, no manifest
    assert _n(run(fs, env, r)["install_dir"]) == LIB2 + "/steamapps/common/Black Desert Online"


def test_manifest_installdir_names_the_folder():
    fs, env, r = world()
    fs.files[_n(LIB2 + "/steamapps/appmanifest_582660.acf")] = ACF.replace(
        "Black Desert Online", "BDO Renamed")
    fs.dirs.add(_n(LIB2 + "/steamapps/common/BDO Renamed"))
    assert _n(run(fs, env, r)["install_dir"]) == LIB2 + "/steamapps/common/BDO Renamed"


def test_missing_game():
    fs, env, r = world()
    fs.dirs = {d for d in fs.dirs if "Black Desert Online" not in d}
    del fs.files[_n(LIB2 + "/steamapps/appmanifest_582660.acf")]
    out = run(fs, env, r)
    assert out["install_dir"] is None
    assert out["documents_dir"] is not None


def test_no_steam_at_all():
    out = run(FakeFS(), {"USERPROFILE": HOME}, reg({}))
    assert out == {"install_dir": None, "documents_dir": None}


def test_steam_from_program_files_env():
    fs = FakeFS(["/fake/Steam/steamapps/common/Black Desert Online"])
    env = {"USERPROFILE": HOME, "ProgramFiles(x86)": "/fake"}
    out = run(fs, env, reg({}))
    assert _n(out["install_dir"]) == "/fake/Steam/steamapps/common/Black Desert Online"


def test_corrupt_vdf_still_checks_the_steam_root():
    fs, env, r = world(game_in_lib2=False)
    fs.files[_n(STEAM + "/steamapps/libraryfolders.vdf")] = '"libraryfolders" { "0" { "path'
    assert _n(run(fs, env, r)["install_dir"]) == STEAM + "/steamapps/common/Black Desert Online"


def test_parse_vdf_old_and_new_formats():
    assert detect.library_paths(vdf("/a", "/b")) == ["/a", "/b"]
    old = '"LibraryFolders"\n{\n\t"TimeNextStatsReport"\t"1"\n\t"1"\t"/old/lib"\n}\n'
    assert detect.library_paths(old) == ["/old/lib"]
    assert detect.library_paths(vdf("x\\y")) == ["x\\y"]
    assert detect.library_paths("garbage {") == []


def test_reads_only_steam_config_files():
    fs, env, r = world()
    run(fs, env, r)
    assert fs.reads and all(p.endswith((".vdf", ".acf")) for p in fs.reads)


def test_registry_and_fs_errors_are_absorbed():
    class Boom(FakeFS):
        def is_dir(self, p):
            raise OSError("denied")

    def bad(*_):
        raise OSError("no hive")

    assert run(Boom(), {}, bad) == {"install_dir": None, "documents_dir": None}


# ---- precedence ----

def test_explicit_config_wins_per_key():
    found = {"install_dir": "/det/i", "documents_dir": "/det/d"}
    out = detect.resolve({"install_dir": "/cfg/i"}, found)
    assert out["install_dir"] == "/cfg/i" and out["documents_dir"] == "/det/d"
    assert out["source"] == {"install_dir": "config", "documents_dir": "detected"}
    blank = detect.resolve({"install_dir": "  ", "documents_dir": None}, {})
    assert blank["install_dir"] is None and blank["source"]["install_dir"] is None


# ---- gamewatch: acceptance (redirected Documents, game in library 2) ----

def test_gamewatch_leaves_unconfigured_with_no_config_edit():
    fs, env, r = world()
    gw = gamewatch.GameWatch(tasklist=lambda: False)
    assert gw.poll() == "unconfigured"
    det = detect.Detector(probe=lambda: run(fs, env, r), config=lambda: {}, clock=lambda: T0)
    det.attach(gw)
    assert gw.configured
    assert gw.poll() == "not_running"
    assert _n(gw.shot_dir) == REDIR + "/Black Desert/ScreenShot"


def test_explicit_config_wins_in_gamewatch():
    fs, env, r = world()
    gw = gamewatch.GameWatch(tasklist=lambda: False)
    det = detect.Detector(probe=lambda: run(fs, env, r),
                          config=lambda: {"install_dir": "/cfg/bdo"}, clock=lambda: T0)
    det.attach(gw)
    assert _n(gw.log_dir) == "/cfg/bdo/Log"


def test_redetects_hourly_while_unconfigured():
    now = [T0]
    found = [{"install_dir": None, "documents_dir": None}]
    calls = []

    def probe():
        calls.append(now[0])
        return dict(found[0])

    gw = gamewatch.GameWatch(clock=lambda: now[0], tasklist=lambda: False)
    det = detect.Detector(probe=probe, config=lambda: {}, clock=lambda: now[0])
    det.attach(gw)
    assert len(calls) == 1 and gw.poll() == "unconfigured"
    now[0] += 60
    gw.poll()
    assert len(calls) == 1  # not every poll
    found[0] = {"install_dir": "/late/bdo", "documents_dir": None}
    now[0] += detect.RETRY_S
    gw.poll()  # the poller re-detects and configures
    assert len(calls) == 2 and gw.configured
    assert gw.poll() == "not_running"
    now[0] += detect.RETRY_S * 3
    gw.poll()
    assert len(calls) == 2  # configured: no more probing


def test_failing_probe_never_breaks_the_poll():
    def probe():
        raise RuntimeError("x")

    gw = gamewatch.GameWatch(tasklist=lambda: False)
    det = detect.Detector(probe=probe, config=lambda: {}, clock=lambda: T0)
    det.attach(gw)
    assert gw.poll() == "unconfigured"
    assert det.paths() == {"install_dir": None, "documents_dir": None}


# ---- settings: allowlisted, validated, detected shown ----

def test_settings_bdo_keys_validated_as_existing_dirs(tmp_path):
    cfg = tmp_path / "local.json"
    s = settings.Settings(cfg)
    real = tmp_path / "bdo"
    real.mkdir()
    out = s.apply({"set": {"bdo.install_dir": str(real), "bdo.documents_dir": ""}})
    assert json.loads(cfg.read_text())["bdo"]["install_dir"] == str(real)
    assert out["settings"]["bdo.install_dir"] == str(real)
    for bad in (str(tmp_path / "nope"), "relative/dir", 5, {"env": "X"}, "x" * 2000):
        with pytest.raises(ValueError):
            s.apply({"set": {"bdo.install_dir": bad}})


def test_settings_view_shows_detected(tmp_path):
    s = settings.Settings(tmp_path / "local.json",
                          detected=lambda: {"install_dir": "/det/i", "documents_dir": None})
    v = s.view()
    assert v["detected"] == {"bdo.install_dir": "/det/i", "bdo.documents_dir": None}
    assert settings.Settings(tmp_path / "x.json").view()["detected"] == {
        "bdo.install_dir": None, "bdo.documents_dir": None}


# ---- onboarding auto-tick ----

def _ob(tmp_path, detected=None, cfg=None):
    path = tmp_path / "config" / "local.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg or {}), encoding="utf-8")
    store = Store(tmp_path / "store")
    return onboarding.status(tmp_path, store, config_path=path, detected=detected), store, path


def _done(v):
    return {s["id"]: s["done"] for s in v["steps"]}


def test_onboarding_ticks_from_detected_paths(tmp_path):
    inst, docs = tmp_path / "inst", tmp_path / "docs"
    inst.mkdir()
    docs.mkdir()
    # Plan 077: no Log / ScreenShot yet = an in-game act with no Settings link.
    v, _, _ = _ob(tmp_path, detected={"install_dir": str(inst), "documents_dir": str(docs)})
    d = _done(v)
    assert not d["log"] and not d["screenshots"] and d["overlay"]
    assert all(s["link"] is None for s in v["steps"] if s["id"] in ("log", "screenshots"))
    (inst / "Log").mkdir()
    (docs / "ScreenShot").mkdir()
    v, _, _ = _ob(tmp_path, detected={"install_dir": str(inst), "documents_dir": str(docs)})
    d = _done(v)
    assert d["log"] and d["screenshots"]


def test_onboarding_detected_path_that_vanished_is_not_done(tmp_path):
    v, _, _ = _ob(tmp_path, detected={"install_dir": str(tmp_path / "gone"),
                                      "documents_dir": None})
    assert not _done(v)["log"]


def test_onboarding_hides_when_only_optional_steps_left(tmp_path):
    inst, docs = tmp_path / "inst", tmp_path / "docs"
    (inst / "Log").mkdir(parents=True)
    (docs / "ScreenShot").mkdir(parents=True)
    v, _, _ = _ob(tmp_path, detected={"install_dir": str(inst), "documents_dir": str(docs)},
                  cfg={"profile": {"family": "Moonfam"}})
    left = [s for s in v["steps"] if not s["done"]]
    assert left and all(s["optional"] for s in left)
    assert v["complete"] is False and v["show"] is False
    v2, _, _ = _ob(tmp_path, detected={"install_dir": str(inst), "documents_dir": str(docs)})
    assert v2["show"] is True  # family still required


def test_onboarding_watch_ticks_on_an_auto_seeded_item(tmp_path):
    _, store, path = _ob(tmp_path)
    store.put("market", {"watch": [{"id": 1, "sid": 0, "auto": True}]})
    assert _done(onboarding.status(tmp_path, store, config_path=path))["watch"]


# ---- HTTP wiring: detector -> /api/game, /api/settings, /api/onboarding ----

def test_server_wires_the_detector(tmp_path):
    inst, docs, other = tmp_path / "inst", tmp_path / "docs", tmp_path / "other"
    for d in (inst, docs, other):
        d.mkdir()
    cfg = tmp_path / "config" / "local.json"
    cfg.parent.mkdir()
    cfg.write_text("{}\n", encoding="utf-8")
    found = {"install_dir": str(inst), "documents_dir": str(docs)}
    det = detect.Detector(probe=lambda: dict(found),
                          config=lambda: gamewatch.config_bdo(tmp_path), clock=lambda: T0)
    gw = gamewatch.GameWatch(tasklist=lambda: False)
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          sse_interval=0.05, market_seed=[],
                          market_client=market.ArshaClient(fetch=_no_network,
                                                           cache_dir=tmp_path / "cache"),
                          profile_cfg={}, config_path=cfg, game_watch=gw, detector=det)
    t = threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    t.start()
    try:
        assert gw.poll() == "not_running"
        st, _, body = _get(s, "/api/settings")
        assert st == 200
        assert json.loads(body)["detected"]["bdo.install_dir"] == str(inst)
        ob = json.loads(_get(s, "/api/onboarding")[2])
        # plan 077: detected, no Log / ScreenShot yet -> in-game acts, no link
        assert _done(ob)["log"] is False and _done(ob)["screenshots"] is False
        assert [x["link"] for x in ob["steps"] if x["id"] in ("log", "screenshots")] == [None, None]
        sig = {r["id"]: r for r in json.loads(_get(s, "/api/signals")[2])["rows"]}
        assert sig["screenshots"]["reason"] == "folder_missing"  # same story
        (inst / "Log").mkdir()
        (docs / "ScreenShot").mkdir()
        gw.poll()
        ob = json.loads(_get(s, "/api/onboarding")[2])
        assert _done(ob)["log"] is True and _done(ob)["screenshots"] is True
        assert str(tmp_path) not in json.dumps(ob)
        st, _ = _post(s, "/api/settings", {"set": {"bdo.install_dir": str(other)}})
        assert st == 200
        assert gw.log_dir == other / "Log"  # "use other" applies live
        _post(s, "/api/settings", {"set": {"bdo.install_dir": ""}})
        assert gw.log_dir == inst / "Log"  # blank: back to detected
    finally:
        s.shutdown()
        s.server_close()
