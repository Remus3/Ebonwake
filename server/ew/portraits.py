"""Class portraits (plan 082): FaceTexture archive + characterNo -> class binding.

Input (research 0011 s3, adjudicator ruling 2026-10-06): `<documents>/FaceTexture`
holds one `<characterNo>.bmp` per character, written by the game when the
operator takes the in-game portrait and overwritten on a re-take. EW lists the
folder, and a file whose (mtime, size) changed and is at least 2 s old is read
ONCE (short-lived read-only handle; a sharing violation retries next poll).
Nothing in FaceTexture is ever written, renamed, deleted or touched. Each new
version (sha256) is copied to the runtime archive as PNG + two thumbnails
(stdlib BMP decode, zlib PNG encode, box downscale), capped per character.

Binding (research 0011 s2): the characterNo comes from the plan 008 session-log
tail (a string parse in gamewatch.py). Rule A1: exactly one characterNo ever
seen AND the Progress class set -> bind (`single`). A2: an OCR class read at or
over the plan 063 gate (`bind_ocr`) replaces a `single` binding. A second
characterNo never inherits a binding; an unbound portrait is indexed but never
returned for a class (the chip shows an empty slot instead).
"""

import datetime as _dt
import hashlib
import json
import os
import re
import struct
import threading
import time
import zlib
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data" / "classes.json"
FACE_DIR = "FaceTexture"
FACE_RE = re.compile(r"^(\d{6,20})\.bmp\Z")  # \Z: no trailing-newline match
CHAR_RE = re.compile(r"^\d{6,20}\Z")
ID_RE = re.compile(r"^(\d{6,20})-(\d{1,12})\Z")
MIN_AGE_S = 2.0  # the game may still be writing a younger file
ARCHIVE_CAP = 30  # archived versions per character; the oldest is dropped
THUMBS = {"s": (56, 72), "m": (312, 402)}
SIZES = {"full": "", "m": "-m", "s": "-s"}
MAX_PIXELS = 4096 * 4096
OCR_GATE = 0.9  # plan 063 AUTO_COMMIT_MIN
CLS_RANK = {None: 0, "single": 1, "ocr": 2, "typed": 3}
_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def _iso(ts):
    return _dt.datetime.fromtimestamp(int(ts), _dt.timezone.utc).isoformat()


def load_classes(path=DATA):
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    return [c for c in doc["classes"] if isinstance(c, str) and c]


# -- codec (pure) ------------------------------------------------------------------

def decode_bmp(data):
    """24/32-bit uncompressed BMP bytes -> (w, h, RGB bytes, rows top-down).
    Raises ValueError on anything else."""
    if len(data) < 54 or data[:2] != b"BM":
        raise ValueError("not a BMP")
    off, dib = struct.unpack_from("<II", data, 10)
    w, h, planes, bpp, comp = struct.unpack_from("<iiHHI", data, 18)
    if dib < 40 or planes != 1 or bpp not in (24, 32) or w <= 0 or h == 0:
        raise ValueError("unsupported BMP header")
    if comp == 3 and bpp == 32:  # BI_BITFIELDS: only the standard BGRA masks
        # The masks sit at byte 54 either way: right after a 40-byte header or
        # inside a V4 / V5 header.
        if struct.unpack_from("<III", data, 54) != (0xFF0000, 0xFF00, 0xFF):
            raise ValueError("unsupported BMP masks")
    elif comp != 0:
        raise ValueError("compressed BMP")
    top_down, h = h < 0, abs(h)
    if w * h > MAX_PIXELS:
        raise ValueError("BMP too large")
    step = bpp // 8
    stride = ((w * bpp + 31) // 32) * 4
    if off < 54 or len(data) < off + stride * h:
        raise ValueError("truncated BMP")
    out = bytearray(w * h * 3)
    for y in range(h):
        src = off + (y if top_down else h - 1 - y) * stride
        row = data[src:src + w * step]
        o = y * w * 3
        line = bytearray(w * 3)
        line[0::3], line[1::3], line[2::3] = row[2::step], row[1::step], row[0::step]
        out[o:o + w * 3] = line
    return w, h, bytes(out)


def _chunk(kind, body):
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


def encode_png(w, h, rgb):
    """8-bit RGB PNG, filter 0 on every row."""
    stride = w * 3
    raw = b"".join(b"\x00" + rgb[y * stride:(y + 1) * stride] for y in range(h))
    return (_PNG_SIG + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(raw, 6)) + _chunk(b"IEND", b""))


def png_size(data):
    """(w, h) of PNG bytes, or None."""
    if not isinstance(data, bytes) or data[:8] != _PNG_SIG or data[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", data[16:24])


def downscale(w, h, rgb, tw, th):
    """Box-average resize to exactly tw x th (a 1-px box when enlarging)."""
    xs = [(ox * w // tw, max(ox * w // tw + 1, (ox + 1) * w // tw)) for ox in range(tw)]
    out = bytearray(tw * th * 3)
    o = 0
    for oy in range(th):
        y0 = oy * h // th
        y1 = max(y0 + 1, (oy + 1) * h // th)
        for x0, x1 in xs:
            r = g = b = 0
            for y in range(y0, y1):
                seg = rgb[(y * w + x0) * 3:(y * w + x1) * 3]
                r += sum(seg[0::3])
                g += sum(seg[1::3])
                b += sum(seg[2::3])
            n = (y1 - y0) * (x1 - x0)
            out[o], out[o + 1], out[o + 2] = (r + n // 2) // n, (g + n // 2) // n, (b + n // 2) // n
            o += 3
    return tw, th, bytes(out)


# -- file access (FaceTexture: read-only; archive: runtime only) ---------------------

def _read_once(path):
    """One short-lived read-only handle; the game's file is never modified."""
    with open(path, "rb") as f:
        return f.read()


def _write_archive(path, data):
    """Atomic write of one archive file (runtime dir only, never FaceTexture)."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _drop_archive(archive_dir, pid):
    """Delete one archived version's derived files (runtime dir only)."""
    for suffix in SIZES.values():
        try:
            (archive_dir / f"{pid}{suffix}.png").unlink()
        except OSError:  # already gone / locked: the index entry is dropped anyway
            pass


# -- service -------------------------------------------------------------------------

def _clean_char(raw):
    raw = raw if isinstance(raw, dict) else {}
    src = raw.get("cls_src") if raw.get("cls_src") in CLS_RANK else None
    cls = raw.get("cls") if isinstance(raw.get("cls"), str) and src else None
    return {"cls": cls, "cls_src": src if cls else None,
            "first_seen": raw.get("first_seen") if isinstance(raw.get("first_seen"), str) else None,
            "last_seen": raw.get("last_seen") if isinstance(raw.get("last_seen"), str) else None}


def _clean_entry(e):
    if not isinstance(e, dict) or not isinstance(e.get("id"), str):
        return None
    m = ID_RE.match(e["id"])
    if not m or not isinstance(e.get("sha"), str) or not isinstance(e.get("mtime"), int):
        return None
    return {"id": e["id"], "char_no": m.group(1), "mtime": e["mtime"], "at": _iso(e["mtime"]),
            "sha": e["sha"]}


class PortraitService:
    """Store domain `portraits`: {"characters": {char_no: {cls, cls_src,
    first_seen, last_seen}}, "seen": {file name: [mtime, size]}, "index":
    [{id, char_no, mtime, at, sha}]}. Archive files live in `archive_dir`."""

    def __init__(self, store, archive_dir, documents=lambda: None, loads=lambda: [],
                 progress_cls=lambda: None, clock=time.time, on_change=None):
        self.store = store
        self.archive_dir = Path(archive_dir)
        self.documents = documents
        self.loads = loads
        self.progress_cls = progress_cls
        self.clock = clock
        self.on_change = on_change
        self.classes = load_classes()
        self._lock = threading.RLock()

    # -- state -------------------------------------------------------------------

    def _load(self):
        doc = self.store.get("portraits")
        chars = doc.get("characters") if isinstance(doc.get("characters"), dict) else {}
        seen = doc.get("seen") if isinstance(doc.get("seen"), dict) else {}
        index = [e for e in map(_clean_entry, doc.get("index") or []) if e is not None] \
            if isinstance(doc.get("index"), list) else []
        return {"characters": {k: _clean_char(v) for k, v in chars.items()
                               if isinstance(k, str) and CHAR_RE.match(k)},
                "seen": {k: v for k, v in seen.items() if isinstance(k, str) and isinstance(v, list)},
                "index": index}

    def _save(self, d):
        self.store.put("portraits", d)

    def _notify(self):
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception:  # noqa: BLE001 - a listener never breaks the poll
                pass

    # -- scan --------------------------------------------------------------------

    def _face_dir(self, documents_dir):
        docs = self.documents() if documents_dir is None else documents_dir
        return Path(docs) / FACE_DIR if docs else None

    def _archive(self, d, char_no, mtime, data, sha):
        w, h, rgb = decode_bmp(data)
        pid = f"{char_no}-{mtime}"
        taken = {e["id"] for e in d["index"]}
        while pid in taken:  # two versions in one second: next free stamp
            mtime += 1
            pid = f"{char_no}-{mtime}"
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        mw, mh, mrgb = downscale(w, h, rgb, *THUMBS["m"])
        files = {"full": encode_png(w, h, rgb), "m": encode_png(mw, mh, mrgb),
                 "s": encode_png(*downscale(mw, mh, mrgb, *THUMBS["s"]))}
        for size, png in files.items():
            _write_archive(self.archive_dir / f"{pid}{SIZES[size]}.png", png)
        d["index"].append({"id": pid, "char_no": char_no, "mtime": mtime, "at": _iso(mtime),
                           "sha": sha})
        mine = sorted((e for e in d["index"] if e["char_no"] == char_no), key=lambda e: e["mtime"])
        old = {e["id"] for e in mine[:-ARCHIVE_CAP]}
        for gone in sorted(old):
            _drop_archive(self.archive_dir, gone)
        d["index"] = [e for e in d["index"] if e["id"] not in old]
        return pid

    def _scan(self, d, documents_dir=None):
        face = self._face_dir(documents_dir)
        if face is None:
            return [], False
        now = self.clock()
        found = []
        try:
            with os.scandir(face) as it:
                for e in it:
                    m = FACE_RE.match(e.name)
                    if m and e.is_file():
                        st = e.stat()
                        found.append((e.name, m.group(1), st.st_mtime, st.st_size))
        except OSError:
            return [], False
        new, dirty = [], False
        hashes = {e["sha"] for e in d["index"]}
        for name, char_no, mtime, size in sorted(found):
            key = [int(mtime), size]
            if d["seen"].get(name) == key or now - mtime < MIN_AGE_S:
                continue
            try:
                data = _read_once(face / name)
            except OSError:  # PermissionError / sharing violation: retry next poll
                continue
            d["seen"][name], dirty = key, True
            sha = hashlib.sha256(data).hexdigest()
            if sha in hashes:
                continue
            try:
                new.append(self._archive(d, char_no, int(mtime), data, sha))
            except ValueError:  # not a BMP we can decode: seen, never archived
                continue
            hashes.add(sha)
        return new, dirty

    def scan(self, documents_dir=None):
        """List FaceTexture and archive new versions; returns the new ids."""
        with self._lock:
            d = self._load()
            new, dirty = self._scan(d, documents_dir)
            if dirty:
                self._save(d)
        if new:
            self._notify()
        return new

    # -- binding -----------------------------------------------------------------

    def _register(self, d):
        changed = False
        for row in self.loads() or []:
            c = row.get("char_no") if isinstance(row, dict) else None
            if not isinstance(c, str) or not CHAR_RE.match(c):
                continue
            at = row.get("at") if isinstance(row.get("at"), str) else _iso(self.clock())
            ch = d["characters"].get(c)
            if ch is None:
                d["characters"][c] = {"cls": None, "cls_src": None, "first_seen": at,
                                      "last_seen": at}
                changed = True
            elif ch["last_seen"] != at:
                ch["last_seen"], changed = at, True
        pcls = self.progress_cls()
        if len(d["characters"]) == 1 and isinstance(pcls, str) and pcls:  # rule A1
            ch = next(iter(d["characters"].values()))
            if CLS_RANK[ch["cls_src"]] <= CLS_RANK["single"] and ch["cls"] != pcls:
                ch["cls"], ch["cls_src"], changed = pcls, "single", True
        return changed

    def poll(self, state=None, at=None):
        """GameWatch poller (fn(state, at)): register loads, bind, scan."""
        with self._lock:
            d = self._load()
            bound = self._register(d)
            new, dirty = self._scan(d)
            if bound or dirty:
                self._save(d)
        if bound or new:
            self._notify()

    def _canon(self, cls):
        if not isinstance(cls, str):
            return None
        low = cls.strip().lower()
        return next((c for c in self.classes if c.lower() == low), None)

    def bind_ocr(self, char_no, cls, conf, gate=OCR_GATE):
        """A2 hook: an OCR class read (plan 066 kind `class`) at or over the gate
        binds `char_no`, replacing `single`; a typed binding is never replaced."""
        cls = self._canon(cls)
        ok_conf = isinstance(conf, (int, float)) and not isinstance(conf, bool) and conf >= gate
        if cls is None or not ok_conf or not isinstance(char_no, str) or not CHAR_RE.match(char_no):
            return False
        with self._lock:
            d = self._load()
            ch = d["characters"].setdefault(char_no, {"cls": None, "cls_src": None,
                                                      "first_seen": _iso(self.clock()),
                                                      "last_seen": _iso(self.clock())})
            if CLS_RANK[ch["cls_src"]] > CLS_RANK["ocr"]:
                return False
            ch["cls"], ch["cls_src"] = cls, "ocr"
            self._save(d)
        self._notify()
        return True

    # -- reads -------------------------------------------------------------------

    @staticmethod
    def _current(d, cls):
        bound = {c for c, ch in d["characters"].items() if ch["cls"] == cls}
        rows = [e for e in d["index"] if e["char_no"] in bound]
        return max(rows, key=lambda e: e["mtime"]) if rows else None

    def current(self, cls):
        """Newest archived portrait of a character bound to `cls`, else None."""
        with self._lock:
            return self._current(self._load(), cls)

    def view(self, char_no=None):
        """GET /api/portraits body."""
        with self._lock:
            d = self._load()
        pcls = self.progress_cls()
        names = {ch["cls"] for ch in d["characters"].values() if ch["cls"]}
        if isinstance(pcls, str) and pcls:
            names.add(pcls)
        classes = {}
        for cls in sorted(names):
            cur = self._current(d, cls)
            classes[cls] = {"current": None if cur is None else {
                "id": cur["id"], "at": cur["at"], "char_no": cur["char_no"], "from": "auto"}}
        bound = {c for c, ch in d["characters"].items() if ch["cls"]}
        loaded = d["characters"].get(char_no) if isinstance(char_no, str) else None
        return {"classes": classes,
                "unknown": sum(1 for e in d["index"] if e["char_no"] not in bound),
                "char_no": char_no if isinstance(char_no, str) else None,
                "loaded_cls": loaded["cls"] if loaded else None,
                "progress_cls": pcls if isinstance(pcls, str) and pcls else None}

    def image(self, pid, size="full"):
        """PNG bytes of an INDEXED id (never a path input), or None."""
        if not isinstance(pid, str) or not ID_RE.match(pid) or size not in SIZES:
            return None
        with self._lock:
            if pid not in {e["id"] for e in self._load()["index"]}:
                return None
        try:
            return (self.archive_dir / f"{pid}{SIZES[size]}.png").read_bytes()
        except OSError:
            return None
