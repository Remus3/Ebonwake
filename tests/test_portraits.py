"""Plan 082: FaceTexture archive, characterNo -> class binding, current().

Every BMP is a tiny synthetic file written into tmp_path; no test reads the
real Documents folder.
"""

import os
import struct
import zlib

import pytest

from server.ew import portraits
from server.ew.store import Store

T0 = 1_800_000_000.0
CHAR_A = "12345678901234567"
CHAR_B = "76543210987654321"


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


def bmp(w, h, bpp=32, top_down=False, pixel=lambda x, y: (x * 10 % 256, y * 20 % 256, 77)):
    """Tiny BI_RGB BMP; rows bottom-up unless `top_down`. pixel -> (r, g, b)."""
    stride = ((w * bpp + 31) // 32) * 4
    rows = []
    for y in range(h):
        row = bytearray()
        for x in range(w):
            r, g, b = pixel(x, y)
            row += bytes((b, g, r)) + (b"\xff" if bpp == 32 else b"")
        rows.append(bytes(row) + b"\x00" * (stride - len(row)))
    body = b"".join(rows if top_down else reversed(rows))
    off = 14 + 40
    head = b"BM" + struct.pack("<IHHI", off + len(body), 0, 0, off)
    dib = struct.pack("<IiiHHIIiiII", 40, w, -h if top_down else h, 1, bpp, 0, len(body),
                      2835, 2835, 0, 0)
    return head + dib + body


def png_decode(data):
    """Minimal PNG reader for the encoder's own output (8-bit RGB, filter 0)."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat = 8, b""
    w = h = None
    while pos < len(data):
        n, = struct.unpack(">I", data[pos:pos + 4])
        kind = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + n]
        assert struct.unpack(">I", data[pos + 8 + n:pos + 12 + n])[0] == zlib.crc32(kind + chunk)
        if kind == b"IHDR":
            w, h, depth, ctype = struct.unpack(">IIBB", chunk[:10])
            assert (depth, ctype) == (8, 2)
        elif kind == b"IDAT":
            idat += chunk
        pos += 12 + n
    raw = zlib.decompress(idat)
    rows = [raw[y * (w * 3 + 1):(y + 1) * (w * 3 + 1)] for y in range(h)]
    assert all(r[0] == 0 for r in rows)
    return w, h, b"".join(r[1:] for r in rows)


@pytest.fixture()
def env(tmp_path):
    docs = tmp_path / "docs"
    (docs / "FaceTexture").mkdir(parents=True)
    st = {"loads": [], "cls": "Deadeye", "events": 0}
    clk = Clock()

    def bump():
        st["events"] += 1

    svc = portraits.PortraitService(
        Store(tmp_path / "store"), tmp_path / "runtime" / "portraits",
        documents=lambda: docs, loads=lambda: st["loads"], progress_cls=lambda: st["cls"],
        clock=clk, on_change=bump)
    return svc, docs / "FaceTexture", st, clk


def _face(face, char_no, data, mtime):
    p = face / f"{char_no}.bmp"
    p.write_bytes(data)
    os.utime(p, (mtime, mtime))
    return p


# -- codec ---------------------------------------------------------------------

@pytest.mark.parametrize("bpp", [24, 32])
@pytest.mark.parametrize("top_down", [False, True])
def test_bmp_to_png_round_trip(bpp, top_down):
    w, h = 5, 3
    px = lambda x, y: (x * 40, y * 90, (x + y) * 7)  # noqa: E731
    dw, dh, rgb = portraits.decode_bmp(bmp(w, h, bpp, top_down, px))
    assert (dw, dh) == (w, h)
    pw, ph, back = png_decode(portraits.encode_png(dw, dh, rgb))
    assert (pw, ph) == (w, h) and back == rgb
    for y in range(h):
        for x in range(w):
            i = (y * w + x) * 3
            assert tuple(back[i:i + 3]) == px(x, y)  # row 0 is the TOP row


@pytest.mark.parametrize("bad", [b"", b"XX" + b"\x00" * 60, bmp(2, 2)[:-3],
                                 bmp(2, 2, bpp=32)[:28] + struct.pack("<H", 8) + bmp(2, 2)[30:]])
def test_decode_bmp_rejects_bad(bad):
    with pytest.raises(ValueError):
        portraits.decode_bmp(bad)


def test_downscale_sizes_and_box_average():
    w, h, rgb = 4, 2, bytes([10, 20, 30, 30, 40, 50] * 4)
    tw, th, out = portraits.downscale(w, h, rgb, 2, 1)
    assert (tw, th) == (2, 1) and out == bytes([20, 30, 40, 20, 30, 40])
    for tw, th in portraits.THUMBS.values():
        _, _, o = portraits.downscale(w, h, rgb, tw, th)
        assert len(o) == tw * th * 3


# -- scan / archive --------------------------------------------------------------

def test_scan_archives_png_and_thumbs(env):
    svc, face, st, clk = env
    _face(face, CHAR_A, bmp(12, 16), T0 - 10)
    new = svc.scan()
    assert new == [f"{CHAR_A}-{int(T0 - 10)}"]
    pid = new[0]
    assert portraits.png_size(svc.image(pid, "full")) == (12, 16)
    assert portraits.png_size(svc.image(pid, "m")) == portraits.THUMBS["m"] == (312, 402)
    assert portraits.png_size(svc.image(pid, "s")) == portraits.THUMBS["s"] == (56, 72)
    assert st["events"] == 1


def test_young_file_waits_two_seconds(env):
    svc, face, _, clk = env
    _face(face, CHAR_A, bmp(4, 4), T0 - 1)
    assert svc.scan() == []
    clk.t = T0 + 1
    assert len(svc.scan()) == 1


def test_unchanged_file_not_reread(env, monkeypatch):
    svc, face, _, _ = env
    _face(face, CHAR_A, bmp(4, 4), T0 - 10)
    svc.scan()
    reads = []
    real = portraits._read_once
    monkeypatch.setattr(portraits, "_read_once", lambda p: reads.append(p) or real(p))
    assert svc.scan() == [] and reads == []


def test_same_hash_not_rearchived(env):
    svc, face, _, _ = env
    data = bmp(4, 4)
    _face(face, CHAR_A, data, T0 - 100)
    assert len(svc.scan()) == 1
    _face(face, CHAR_A, data, T0 - 50)  # touched, same bytes
    assert svc.scan() == []
    _face(face, CHAR_A, bmp(4, 4, pixel=lambda x, y: (1, 2, 3)), T0 - 20)  # re-taken
    assert len(svc.scan()) == 1
    assert len(svc.store.get("portraits")["index"]) == 2  # the previous one stays archived


def test_locked_file_retried_next_poll(env, monkeypatch):
    svc, face, _, _ = env
    _face(face, CHAR_A, bmp(4, 4), T0 - 10)

    def locked(p):
        raise PermissionError("sharing violation")

    real = portraits._read_once
    monkeypatch.setattr(portraits, "_read_once", locked)
    assert svc.scan() == []
    monkeypatch.setattr(portraits, "_read_once", real)
    assert len(svc.scan()) == 1


def test_bad_bmp_marked_seen_never_archived(env):
    svc, face, _, _ = env
    _face(face, CHAR_A, b"not a bmp at all", T0 - 10)
    assert svc.scan() == [] and svc.scan() == []
    assert svc.store.get("portraits").get("index", []) == []


def test_non_portrait_names_ignored(env):
    svc, face, _, _ = env
    for name in ("abc.bmp", "123.bmp", f"{CHAR_A}.png", f"{CHAR_A}.bmp.bak"):
        (face / name).write_bytes(bmp(2, 2))
        os.utime(face / name, (T0 - 10, T0 - 10))
    assert svc.scan() == []


@pytest.mark.slow  # plan 097: about 3 s (32 scans of the archive)
def test_archive_cap_per_character(env):
    svc, face, _, _ = env
    for i in range(portraits.ARCHIVE_CAP + 2):
        _face(face, CHAR_A, bmp(2, 2, pixel=lambda x, y, i=i: (i, 0, 0)), T0 - 1000 + i)
        svc.scan()
    idx = svc.store.get("portraits")["index"]
    assert len(idx) == portraits.ARCHIVE_CAP == 30
    assert min(e["mtime"] for e in idx) == int(T0 - 1000 + 2)
    assert svc.image(f"{CHAR_A}-{int(T0 - 1000)}", "full") is None
    files = os.listdir(svc.archive_dir)
    assert len(files) == 3 * portraits.ARCHIVE_CAP


def test_no_documents_dir_is_quiet(tmp_path):
    svc = portraits.PortraitService(Store(tmp_path / "s"), tmp_path / "a", documents=lambda: None)
    assert svc.scan() == [] and svc.view()["classes"] == {}


# -- binding -------------------------------------------------------------------

def test_a1_binds_single_char_and_current(env):
    svc, face, st, _ = env
    _face(face, CHAR_A, bmp(4, 4), T0 - 10)
    st["loads"] = [{"char_no": CHAR_A, "at": "2026-10-06 20:00:00"}]
    svc.poll("logged_in", T0)
    ch = svc.store.get("portraits")["characters"][CHAR_A]
    assert ch["cls"] == "Deadeye" and ch["cls_src"] == "single"
    cur = svc.current("Deadeye")
    assert cur["char_no"] == CHAR_A and cur["id"] == f"{CHAR_A}-{int(T0 - 10)}"
    v = svc.view()
    assert v["classes"]["Deadeye"]["current"] == {"id": cur["id"], "at": cur["at"],
                                                  "char_no": CHAR_A, "from": "auto",
                                                  "kind": "portrait"}  # plan 083: + kind
    assert v["unknown"] == 0


def test_second_char_never_inherits(env):
    svc, face, st, _ = env
    st["loads"] = [{"char_no": CHAR_A, "at": "a"}]
    svc.poll("logged_in", T0)
    st["loads"] = [{"char_no": CHAR_A, "at": "a"}, {"char_no": CHAR_B, "at": "b"}]
    _face(face, CHAR_B, bmp(4, 4), T0 - 10)
    svc.poll("logged_in", T0)
    chars = svc.store.get("portraits")["characters"]
    assert chars[CHAR_B]["cls"] is None
    assert svc.current("Deadeye") is None  # A has no portrait; B's is never shown
    assert svc.view()["unknown"] == 1


def test_two_chars_before_class_binds_nothing(env):
    svc, _, st, _ = env
    st["cls"] = None
    st["loads"] = [{"char_no": CHAR_A, "at": "a"}, {"char_no": CHAR_B, "at": "b"}]
    svc.poll("logged_in", T0)
    st["cls"] = "Deadeye"
    svc.poll("logged_in", T0)
    chars = svc.store.get("portraits")["characters"]
    assert chars[CHAR_A]["cls"] is None and chars[CHAR_B]["cls"] is None


def test_unbound_portrait_indexed_never_returned(env):
    svc, face, st, _ = env
    st["cls"] = None
    _face(face, CHAR_A, bmp(4, 4), T0 - 10)
    svc.poll("logged_in", T0)
    assert svc.current("Deadeye") is None and svc.view()["unknown"] == 1


def test_ocr_binding_replaces_single(env):
    svc, face, st, _ = env
    st["loads"] = [{"char_no": CHAR_A, "at": "a"}]
    _face(face, CHAR_A, bmp(4, 4), T0 - 10)
    svc.poll("logged_in", T0)
    assert svc.bind_ocr(CHAR_A, "Wizard", 0.5) is False  # under the plan 063 gate
    assert svc.bind_ocr(CHAR_A, "Not A Class", 0.99) is False
    assert svc.bind_ocr(CHAR_A, "wizard", 0.95) is True
    ch = svc.store.get("portraits")["characters"][CHAR_A]
    assert ch["cls"] == "Wizard" and ch["cls_src"] == "ocr"
    svc.poll("logged_in", T0)  # A1 never overwrites an OCR binding
    assert svc.current("Deadeye") is None and svc.current("Wizard")["char_no"] == CHAR_A


def test_no_portrait_is_empty_slot(env):
    svc, _, st, _ = env
    st["loads"] = [{"char_no": CHAR_A, "at": "a"}]
    svc.poll("logged_in", T0)
    assert svc.current("Deadeye") is None
    assert svc.view()["classes"] == {"Deadeye": {"current": None}}


def test_view_loaded_char(env):
    svc, _, st, _ = env
    st["loads"] = [{"char_no": CHAR_A, "at": "a"}]
    svc.poll("logged_in", T0)
    v = svc.view(char_no=CHAR_A)
    assert v["char_no"] == CHAR_A and v["loaded_cls"] == "Deadeye"
    assert v["progress_cls"] == "Deadeye"


def test_classes_file_is_fixed_list():
    cls = portraits.load_classes()
    assert "Deadeye" in cls and len(cls) == len(set(cls)) >= 25
    assert all(isinstance(c, str) and c.isascii() for c in cls)


@pytest.mark.parametrize("bad", ["", "../x", "..", "1-2", f"{CHAR_A}-1/../x", f"{CHAR_A}-abc",
                                 f"{CHAR_A}-{int(T0)}", None, 5])
def test_image_rejects_ids_not_in_index(env, bad):
    svc, _, _, _ = env
    assert svc.image(bad, "full") is None


def test_image_rejects_bad_size(env):
    svc, face, _, _ = env
    _face(face, CHAR_A, bmp(4, 4), T0 - 10)
    pid = svc.scan()[0]
    assert svc.image(pid, "huge") is None and svc.image(pid, "s") is not None


# -- plan 084: character card block --------------------------------------------

def test_card_block_from_existing_views():
    life = {"status": "ok", "character": "Testarcher", "at": "2026-10-06T19:00:00+00:00",
            "energy": 412, "cp": {"value": 388}}
    c = portraits.card_block(
        {"level": 62, "level_source": "ocr", "level_at": "2026-10-06T18:00:00+00:00"},
        life, {"value": 390, "source": "ocr", "at": "2026-10-06T20:00:00+00:00"})
    assert c == {"level": 62, "level_source": "ocr", "level_at": "2026-10-06T18:00:00+00:00",
                 "energy": 412, "energy_at": "2026-10-06T19:00:00+00:00",
                 "cp": 390, "cp_src": "ocr", "cp_at": "2026-10-06T20:00:00+00:00",
                 "name": "Testarcher"}


def test_card_block_hidden_and_missing_are_none():
    life = {"status": "ok", "character": None, "at": "2026-10-06T19:00:00+00:00",
            "energy": "hidden", "cp": "hidden"}
    c = portraits.card_block({"level": None, "level_source": None, "level_at": None}, life, None)
    assert c == {"level": None, "level_source": None, "level_at": None, "energy": None,
                 "energy_at": None, "cp": None, "cp_src": None, "cp_at": None, "name": None}
    assert portraits.card_block(None, None, None) == c
    # junk never becomes a number (no "0", no bool)
    junk = portraits.card_block({"level": True, "level_source": "x"},
                                {"status": "ok", "energy": True, "character": 5},
                                {"value": "12", "source": "ocr"})
    assert junk["level"] is None and junk["energy"] is None and junk["cp"] is None
    assert junk["name"] is None and junk["level_source"] is None


def test_card_block_profile_cp_takes_snapshot_age():
    life = {"status": "ok", "character": "Testarcher", "at": "2026-10-06T19:00:00+00:00",
            "energy": 10, "cp": {"value": 300}}
    c = portraits.card_block(None, life, {"value": 300, "source": "profile", "at": None})
    assert (c["cp"], c["cp_src"], c["cp_at"]) == (300, "profile", "2026-10-06T19:00:00+00:00")


def test_view_carries_card_and_writes_no_store_field(env):
    svc, _, st, _ = env
    st["loads"] = [{"char_no": CHAR_A, "at": "a"}]
    svc.poll("logged_in", T0)
    before = svc.store.get("portraits")
    assert svc.view()["card"] is None  # no card source wired
    svc.card = lambda: {"level": 61}
    assert svc.view()["card"] == {"level": 61}

    def boom():
        raise RuntimeError("x")
    svc.card = boom
    assert svc.view()["card"] is None  # a broken source never breaks the chip
    assert svc.store.get("portraits") == before


def test_leveling_level_info(tmp_path):
    from server.ew import leveling
    lv = leveling.LevelingService(Store(tmp_path / "store"), clock=Clock())
    assert lv.level_info() == {"level": None, "level_source": None, "level_at": None}
    lv.sample({"level": 61, "pct": 12.5})
    info = lv.level_info()
    assert info["level"] == 61 and info["level_source"] == "typed"
    assert info["level_at"].startswith("2027-01-15")


def test_imperial_cp_info(tmp_path):
    from server.ew import imperial
    s = imperial.ImperialService(Store(tmp_path / "store"), clock=Clock(), cp=lambda: 300)
    assert s.cp_info() == {"value": 300, "source": "profile", "at": None}
    t = imperial.ImperialService(Store(tmp_path / "s2"), clock=Clock())
    assert t.cp_info() == {"value": None, "source": None, "at": None}
