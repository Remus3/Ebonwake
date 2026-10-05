"""EW server routes on an ephemeral loopback port."""

import http.client
import json
import threading

import pytest

from server.ew import app as ewapp
from server.ew.store import Store


@pytest.fixture()
def srv(tmp_path):
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="a" * 40,
                          sse_interval=0.05)
    t = threading.Thread(target=s.serve_forever, daemon=True)
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
    assert ids[0] == "today" and "system" in ids and len(ids) == 7


def test_foreign_host_rejected(srv):
    assert _get(srv, "/api/health", host="evil.example.com")[0] == 403


def test_static_dashboard_and_tokens(srv):
    st, ct, body = _get(srv, "/")
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


def test_store_roundtrip_atomic(tmp_path):
    st = Store(tmp_path)
    assert st.get("market") == {}
    st.put("market", {"watch": [4901]})
    assert st.get("market") == {"watch": [4901]}
    assert not list(tmp_path.glob("*.tmp"))
    with pytest.raises(ValueError):
        st.put("../x", {})
