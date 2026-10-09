"""EW server routes on an ephemeral loopback port."""

import http.client
import json
import threading

import pytest

from server.ew import app as ewapp
from server.ew import market
from server.ew.store import Store


def _no_network(url, timeout):
    raise AssertionError("test touched the network")


@pytest.fixture()
def srv(tmp_path):
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          sse_interval=0.05, market_seed=[],
                          market_client=market.ArshaClient(fetch=_no_network,
                                                           cache_dir=tmp_path / "cache"),
                          profile_cfg={})
    t = threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    t.start()
    yield s
    s.shutdown()
    s.server_close()


def _get(s, path, host=None):
    port = s.server_address[1]
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Host": host} if host else {}
    c.request("GET", path, headers=headers)
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, r.getheader("Content-Type"), body


def test_health(srv):
    st, ct, body = _get(srv, "/api/health")
    assert st == 200 and json.loads(body)["ok"] is True


def test_version_contract(srv):
    st, _, body = _get(srv, "/api/version")
    doc = json.loads(body)
    assert st == 200
    assert set(doc) == {"commit", "started", "pid", "config_hash", "schema"}
    assert doc["commit"] == "a" * 40 and doc["schema"] == 1
    text = body.decode()
    assert "\\" not in text and ":\\" not in text and "@" not in text


def test_state_lists_tabs(srv):
    doc = json.loads(_get(srv, "/api/state")[2])
    ids = [t["id"] for t in doc["tabs"]]
    # plan 025: Home first and default; plan 030: Settings last.
    assert ids == ["home", "today", "market", "progress", "grind", "events", "deadeye", "system",
                   "settings"]


def test_foreign_host_rejected(srv):
    assert _get(srv, "/api/health", host="evil.example.com")[0] == 403


def test_root_redirects_to_dashboard(srv):
    # Plan 020: relative asset paths only resolve from the real page URL.
    port = srv.server_address[1]
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("GET", "/")
    r = c.getresponse()
    r.read()
    c.close()
    assert r.status == 302 and r.getheader("Location") == "/app/dashboard/index.html"


def test_static_dashboard_and_tokens(srv):
    st, ct, body = _get(srv, "/app/dashboard/index.html")
    assert st == 200 and "text/html" in ct and b"EBONWAKE" in body
    st, ct, _ = _get(srv, "/ops/fleet_kit/tokens.css")
    assert st == 200 and "css" in ct


def test_static_traversal_refused(srv):
    assert _get(srv, "/app/../CLAUDE.md")[0] == 404
    assert _get(srv, "/app/..%2fCLAUDE.md")[0] == 404


def test_sse_heartbeat(srv):
    port = srv.server_address[1]
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("GET", "/events")
    r = c.getresponse()
    assert r.status == 200 and r.getheader("Content-Type") == "text/event-stream"
    line = r.fp.readline()
    assert line.startswith(b"data: ") and b"heartbeat" in line
    c.close()


def _sse_events(r, want, limit=200):
    """Read SSE frames until every name in `want` was seen; returns {name: data}."""
    seen, name = {}, None
    for _ in range(limit):
        line = r.fp.readline().decode("utf-8").rstrip("\n")
        if line.startswith("event: "):
            name = line[len("event: "):]
        elif line.startswith("data: ") and name:
            seen[name] = line[len("data: "):]
        elif not line:
            name = None
        if want <= set(seen):
            break
    return seen


# Plan 049: a successful POST pushes its domain name; clients re-GET.
@pytest.mark.parametrize("path,body,domain", [
    ("/api/today", {"tick": "barter-run"}, "today"),
    ("/api/grind", {"add_spot": "Gyfin"}, "grind"),
    ("/api/market/watch", {"add": {"id": 4901, "sid": 0, "below": 5}}, "market"),
    ("/api/progress", {"character": {"level": 61}}, "progress"),
    ("/api/events", {"purge_expired": True}, "events"),
    ("/api/leveling", {"sample": {"level": 61, "pct": 10}}, "leveling"),
])
def test_sse_domain_event_after_post(srv, path, body, domain):
    port = srv.server_address[1]
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("GET", "/events")
    r = c.getresponse()
    assert b"heartbeat" in r.fp.readline()
    st, doc = _post(srv, path, body)
    assert st == 200, doc
    seen = _sse_events(r, {domain})
    c.close()
    assert domain in seen
    if domain != "leveling":  # leveling keeps its plan 011 full-view payload
        assert seen[domain] == json.dumps(domain)


def test_sse_no_domain_event_after_failed_post(srv):
    port = srv.server_address[1]
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("GET", "/events")
    r = c.getresponse()
    r.fp.readline()
    assert _post(srv, "/api/today", {"nope": 1})[0] == 400
    assert _post(srv, "/api/market/watch", {"add": {"id": 4901, "sid": 0, "below": 5}})[0] == 200
    seen = _sse_events(r, {"market"})
    c.close()
    assert "today" not in seen and "market" in seen


def _post(s, path, body, ctype="application/json", host=None, extra=None):
    port = s.server_address[1]
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Content-Type": ctype} if ctype else {}
    if host:
        headers["Host"] = host
    headers.update(extra or {})
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    c.request("POST", path, body=data, headers=headers)
    r = c.getresponse()
    out = r.read()
    c.close()
    return r.status, (json.loads(out) if out else None)


def test_post_watch_add_remove(srv):
    st, doc = _post(srv, "/api/market/watch", {"add": {"id": 4901, "sid": 0, "below": 5}})
    assert st == 200 and {"id": 4901, "sid": 0, "below": 5, "above": None} in doc["watch"]
    st, doc = _post(srv, "/api/market/watch", {"remove": {"id": 4901, "sid": 0}})
    assert st == 200 and all(w["id"] != 4901 or w["sid"] != 0 for w in doc["watch"])


def test_post_bad_host(srv):
    st, _ = _post(srv, "/api/market/watch", {"add": {"id": 1}}, host="evil.example.com")
    assert st == 403


@pytest.mark.parametrize("ctype", [None, "text/plain", "application/x-www-form-urlencoded",
                                   "multipart/form-data"])
def test_post_bad_content_type(srv, ctype):
    assert _post(srv, "/api/market/watch", {"add": {"id": 1}}, ctype=ctype)[0] == 415


def test_post_json_with_charset_ok(srv):
    st, _ = _post(srv, "/api/market/watch", {"add": {"id": 7}},
                  ctype="application/json; charset=utf-8")
    assert st == 200


def test_post_oversize(srv):
    body = json.dumps({"add": {"id": 1}, "pad": "x" * 5000}).encode()
    assert _post(srv, "/api/market/watch", body)[0] == 413


@pytest.mark.parametrize("body", [b"not json", b"[]", {"add": {"id": "1"}}, {"nope": 1},
                                  {"add": {"id": 1}, "remove": {"id": 1}}, {"remove": {"sid": 0}}])
def test_post_bad_body(srv, body):
    assert _post(srv, "/api/market/watch", body)[0] == 400


def test_post_unknown_path(srv):
    assert _post(srv, "/api/health", {})[0] == 404


def test_options_preflight_not_answered(srv):
    port = srv.server_address[1]
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("OPTIONS", "/api/market/watch", headers={
        "Origin": "http://evil.example.com", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type"})
    r = c.getresponse()
    r.read()
    c.close()
    assert r.status >= 400 and r.getheader("Access-Control-Allow-Methods") is None


def test_store_roundtrip_atomic(tmp_path):
    st = Store(tmp_path)
    assert st.get("market") == {}
    st.put("market", {"watch": [4901]})
    assert st.get("market") == {"watch": [4901]}
    assert not list(tmp_path.glob("*.tmp"))
    with pytest.raises(ValueError):
        st.put("../x", {})
