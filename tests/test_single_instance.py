"""Plan 010 item 1: a second `python -m server.ew` finds an EW server already on
ports.SERVER (via /api/version) and exits 0 without binding. The probe is pure
with an injectable opener, so no test touches the network."""

import io
import json
import urllib.error

from server.ew import app as ewapp
from server.ew import ports, single

EW_DOC = {"commit": "a" * 40, "started": "2026-10-05T00:00:00Z", "pid": 4242,
          "config_hash": "b" * 12, "schema": 1}


class _Resp(io.BytesIO):
    def __init__(self, body, server="Ebonwake ", status=200):
        super().__init__(body)
        self.headers = {"Server": server}
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _opener(body=None, server="Ebonwake ", exc=None, seen=None):
    def op(url, timeout):
        if seen is not None:
            seen.append((url, timeout))
        if exc is not None:
            raise exc
        return _Resp(body, server=server)
    return op


def test_url_uses_registry_port():
    assert single.VERSION_URL == f"http://127.0.0.1:{ports.SERVER}/api/version"


def test_ew_answer():
    seen = []
    got = single.probe(opener=_opener(json.dumps(EW_DOC).encode(), seen=seen))
    assert got == "ew"
    assert seen[0][0] == single.VERSION_URL and 0 < seen[0][1] <= 2


def test_no_answer():
    for exc in (urllib.error.URLError(ConnectionRefusedError(10061, "refused")),
                ConnectionRefusedError(10061, "refused"), TimeoutError("slow")):
        assert single.probe(opener=_opener(exc=exc)) == "none"


def test_foreign_answers():
    # Same fleet contract keys but another server's banner: not EW.
    assert single.probe(opener=_opener(json.dumps(EW_DOC).encode(), server="nginx")) == "foreign"
    # EW banner, wrong body shape.
    assert single.probe(opener=_opener(b'{"ok": true}')) == "foreign"
    assert single.probe(opener=_opener(b"<html>hi</html>")) == "foreign"
    assert single.probe(opener=_opener(json.dumps(dict(EW_DOC, schema=2)).encode())) == "foreign"
    assert single.probe(opener=_opener(json.dumps([1, 2]).encode())) == "foreign"
    err = urllib.error.HTTPError(single.VERSION_URL, 404, "nf", {}, None)
    assert single.probe(opener=_opener(exc=err)) == "foreign"


def test_is_ew_version_doc():
    assert single.is_ew_version(EW_DOC, "Ebonwake ")
    assert not single.is_ew_version(dict(EW_DOC, extra=1), "Ebonwake ")
    assert not single.is_ew_version(EW_DOC, None)


def test_main_exits_zero_without_binding_when_ew_answers(monkeypatch):
    def boom(**kw):
        raise AssertionError("bound a second server")
    monkeypatch.setattr(ewapp, "make_server", boom)
    assert ewapp.main([], probe=lambda: "ew") == 0


def test_main_binds_when_nothing_answers(monkeypatch):
    calls = []

    class Fake:
        def serve_forever(self):
            calls.append("serve")

        def server_close(self):
            calls.append("close")

    monkeypatch.setattr(ewapp, "make_server", lambda **kw: Fake())
    assert ewapp.main([], probe=lambda: "none") == 0
    assert calls == ["serve", "close"]


def test_real_server_banner_and_doc_pass_probe(tmp_path):
    # The live handler's Server header and /api/version body satisfy the probe.
    import threading
    import urllib.request

    from server.ew import market
    s = ewapp.make_server(port=0, store_root=tmp_path / "store", commit="c" * 40,
                          market_seed=[], profile_cfg={},
                          market_client=market.ArshaClient(
                              fetch=lambda u, t: (_ for _ in ()).throw(AssertionError()),
                              cache_dir=tmp_path / "cache"))
    t = threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    t.start()
    try:
        url = f"http://127.0.0.1:{s.server_address[1]}/api/version"
        assert single.probe(url=url, opener=urllib.request.urlopen) == "ew"
    finally:
        s.shutdown()
        s.server_close()
