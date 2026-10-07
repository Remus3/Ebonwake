"""Plan 061: profile source robots gate, self-hosted base, graceful degrade.

No network: every client gets an injected fake fetch that records each URL.
"""

import json
from pathlib import Path

import pytest

from server.ew import app as ewapp
from server.ew import progress, settings
from server.ew.store import Store

FAMILY = "Testfam"
HIT = [{"familyName": "Testfam", "region": "NA", "guild": None,
        "characters": [{"name": "Shooty", "class": "Deadeye", "main": True, "level": 62}]}]
MIRROR = "https://mirror.example.com/v1"
ROOT = Path(__file__).resolve().parents[1]


class Clock:
    def __init__(self, t=2_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class Net:
    """robots.txt -> `robots` (bytes or Exception); anything else -> `body`."""

    def __init__(self, robots=b"User-agent: *\nAllow: /\n", body=HIT):
        self.robots = robots
        self.body = body
        self.calls = []

    def profile_calls(self):
        return [u for u in self.calls if not u.endswith("/robots.txt")]

    def robots_calls(self):
        return [u for u in self.calls if u.endswith("/robots.txt")]

    def __call__(self, url, timeout):
        self.calls.append(url)
        out = self.robots if url.endswith("/robots.txt") else self.body
        if isinstance(out, Exception):
            raise out
        return out if isinstance(out, bytes) else json.dumps(out).encode()


def _pc(tmp_path, base_url=None, net=None, clock=None):
    net = net or Net()
    c = progress.ProfileClient(FAMILY, base_url=base_url, fetch=net, clock=clock or Clock(),
                               cache_dir=tmp_path / "cache")
    return c, net


def _sync(fn):
    fn()


def _svc(tmp_path, client):
    return progress.ProgressService(Store(tmp_path / "store"), client, spawn=_sync)


# --- defaults: profile source off, zero requests ----------------------------

def test_default_base_is_off():
    assert progress.DEFAULT_BASE == ""


def test_default_base_makes_zero_requests(tmp_path):
    c, net = _pc(tmp_path)
    assert c.off_reason() == "no_base"
    svc = _svc(tmp_path, c)
    for _ in range(3):
        view = svc.view()
    c.get()
    c.refresh(_sync)
    assert net.calls == []
    assert view["profile"]["state"] == "off" and view["profile"]["reason"] == "no_base"
    assert view["profile"]["status"] == "off" and view["profile"]["data"] is None
    assert svc.source()["status"] == "off"


@pytest.mark.parametrize("base", ["http://mirror.example.com/v1", "ftp://127.0.0.1/v1",
                                  "https://", "not a url", "https://user:pw@example.com",
                                  "https://mirror.example.com/v1?x=1", 42, None])
def test_bad_base_is_off_and_silent(tmp_path, base):
    c, net = _pc(tmp_path, base_url=base)
    c.get()
    c.refresh(_sync)
    assert c.off_reason() == "no_base" and net.calls == []


def test_server_with_shipped_defaults_makes_zero_requests(tmp_path):
    net = Net()
    pc = progress.ProfileClient(FAMILY, base_url=None, fetch=net, clock=Clock(),
                                cache_dir=tmp_path / "pcache")
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", market_seed=[],
                          profile_client=pc)
    try:
        s.progress.spawn = _sync
        doc = s.progress.view()
        assert doc["profile"]["state"] == "off" and doc["profile"]["reason"] == "no_base"
    finally:
        s.server_close()
    assert net.calls == []


def test_example_config_never_points_at_a_public_host():
    doc = json.loads((ROOT / "config" / "local.example.json").read_text(encoding="utf-8"))
    base = doc["profile"]["base_url"]
    assert base in ("", progress.SELF_HOST_EXAMPLE)
    assert progress.is_loopback_base(progress.SELF_HOST_EXAMPLE)


def test_no_public_host_literal_left_in_server():
    dead = "cutepap" + ".us"
    hits = [str(p) for p in (ROOT / "server").rglob("*") if p.is_file()
            and p.suffix in (".py", ".json", ".txt", ".md")
            and dead in p.read_text(encoding="utf-8", errors="replace")]
    assert hits == []


# --- robots gate ------------------------------------------------------------

def test_robots_disallow_is_off_with_zero_profile_gets(tmp_path):
    net = Net(robots=b"User-agent: *\nDisallow: /\n")
    c, _ = _pc(tmp_path, base_url=MIRROR, net=net)
    svc = _svc(tmp_path, c)
    view = svc.view()
    for _ in range(3):
        view = svc.view()
        c.get()
    assert net.profile_calls() == []
    assert net.robots_calls() == ["https://mirror.example.com/robots.txt"]  # once per 24 h
    assert view["profile"]["state"] == "off" and view["profile"]["reason"] == "robots"


def test_robots_disallow_for_our_agent_only_is_off(tmp_path):
    net = Net(robots=b"User-agent: Ebonwake\nDisallow: /\n\nUser-agent: *\nAllow: /\n")
    c, _ = _pc(tmp_path, base_url=MIRROR, net=net)
    c.get()
    assert c.off_reason() == "robots" and net.profile_calls() == []


def test_robots_unreachable_is_off(tmp_path):
    net = Net(robots=OSError("boom"))
    c, _ = _pc(tmp_path, base_url=MIRROR, net=net)
    c.get()
    assert c.off_reason() == "robots" and net.profile_calls() == []


def test_robots_allow_calls_profile_then_rechecks_after_24h(tmp_path):
    clk = Clock()
    c, net = _pc(tmp_path, base_url=MIRROR, clock=clk)
    assert c.get()["data"]["family"] == "Testfam"
    assert net.robots_calls() == ["https://mirror.example.com/robots.txt"]
    assert net.profile_calls()[0].startswith(MIRROR + "/adventurer/search?")
    clk.t += progress.PROFILE_TTL + 1
    c.get()
    assert len(net.robots_calls()) == 1  # verdict cached for 24 h
    clk.t += progress.ROBOTS_RECHECK_S
    net.robots = b"User-agent: *\nDisallow: /\n"
    n = len(net.profile_calls())
    c.get()
    assert len(net.robots_calls()) == 2 and len(net.profile_calls()) == n
    assert c.off_reason() == "robots"


def test_robots_verdict_survives_restart(tmp_path):
    net = Net(robots=b"User-agent: *\nDisallow: /\n")
    c, _ = _pc(tmp_path, base_url=MIRROR, net=net)
    c.get()
    c2, _ = _pc(tmp_path, base_url=MIRROR, net=net)
    assert c2.off_reason() == "robots"
    c2.get()
    assert len(net.robots_calls()) == 1 and net.profile_calls() == []


@pytest.mark.parametrize("base", [progress.SELF_HOST_EXAMPLE, "http://localhost:8001/v1",
                                  "http://[::1]:8001/v1", "https://127.0.0.1/v1"])
def test_loopback_base_skips_the_gate(tmp_path, base):
    net = Net(robots=b"User-agent: *\nDisallow: /\n")
    c, _ = _pc(tmp_path, base_url=base, net=net)
    assert c.off_reason() is None
    assert c.get()["data"]["family"] == "Testfam"
    assert net.robots_calls() == [] and len(net.profile_calls()) == 1
    assert net.profile_calls()[0].startswith(base.rstrip("/") + "/adventurer/search?")


def test_loopback_view_is_on(tmp_path):
    c, net = _pc(tmp_path, base_url=progress.SELF_HOST_EXAMPLE)
    view = _svc(tmp_path, c).view()
    assert view["profile"]["state"] == "on" and view["profile"]["status"] == "ok"
    assert view["profile"]["data"]["family"] == "Testfam"


# --- backoff: three consecutive failures -> 24 h ----------------------------

def test_backoff_to_24h_after_three_failures_serves_last_good(tmp_path):
    clk = Clock()
    c, net = _pc(tmp_path, base_url=progress.SELF_HOST_EXAMPLE, clock=clk)
    assert c.get()["stale"] is False
    net.body = OSError("down")
    for _ in range(3):
        clk.t += progress.PROFILE_TTL + 1
        bo = c.key_backoff(c.key)
        clk.t = max(clk.t, bo.get("until", 0) + 1)
        res = c.get()
    bo = c.key_backoff(c.key)
    assert bo["n"] == 3 and bo["until"] - clk.t == progress.FAIL_BACKOFF_S == 86400
    assert res["data"]["family"] == "Testfam" and res["stale"] is True and res["age_s"] > 0
    n = len(net.calls)
    clk.t += 86400 - 60
    c.get()
    c.refresh(_sync)
    assert len(net.calls) == n  # quiet for the whole day
    clk.t += 61
    net.body = HIT
    assert c.get()["stale"] is False and c.key_backoff(c.key) == {}


def test_two_failures_keep_the_short_backoff(tmp_path):
    clk = Clock()
    c, net = _pc(tmp_path, base_url=progress.SELF_HOST_EXAMPLE, clock=clk,
                 net=Net(body=OSError("down")))
    c.get()
    clk.t += 1000
    c.get()
    bo = c.key_backoff(c.key)
    assert bo["n"] == 2 and bo["until"] - clk.t <= 1800


# --- settings: profile.base_url ----------------------------------------------

@pytest.mark.parametrize("v,ok", [("", True), (progress.SELF_HOST_EXAMPLE, True),
                                  ("https://mirror.example.com/v1", True),
                                  ("http://mirror.example.com/v1", False),
                                  ("http://127.0.0.1:8001/v1\n", False), (None, False),
                                  ("https://x" + "a" * 300, False), (7, False)])
def test_settings_base_url_validator(v, ok):
    assert settings.SPEC["profile.base_url"][0](v) is ok
    assert "profile.base_url" in settings.RESTART_KEYS
