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

Gallery (plan 083): every ScreenShot file the plan 008 watcher lists is indexed
once `{id, file, at, char_no|null}`; char_no = the newest character load at or
before the shot inside the same plan 062 logged-in window, else null. Files are
referenced in place, never copied. A gallery pick is a plan 079 override (key
`portrait.<cls>`, rule `none`): a portrait newer than the pick retires it
(`portrait`), a pinned id that left the index retires it (`missing`).
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

from . import overrides

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
# Plan 083: screenshot index (ScreenShot files referenced in place, never copied).
SHOT_DIR = "ScreenShot"
SHOT_ID_RE = re.compile(r"^s[0-9a-f]{16}\Z")
SHOT_NAME_RE = re.compile(r"^[A-Za-z0-9 _.()\-]{1,200}\Z")
SHOT_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".bmp": "image/bmp"}
SHOT_CAP = 200  # indexed screenshots; the oldest is dropped
SHOTS_PER_CLASS = 24
PICK_PREFIX = "portrait."


def _iso(ts):
    return _dt.datetime.fromtimestamp(int(ts), _dt.timezone.utc).isoformat()


def _epoch(s):
    """ISO time -> epoch. A naive time (the client log `Date`) is local wall time."""
    if not isinstance(s, str):
        return None
    try:
        return _dt.datetime.fromisoformat(s.strip()).timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def _count(v):
    """A shown count: a non-negative int (a whole float too), never bool / str."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v < 0:
        return None
    return int(v) if float(v).is_integer() else None


def _text(v):
    return v if isinstance(v, str) and v else None


def card_block(level, lifeskill, cp):
    """Plan 084 character card over values EW already has, never fetched or
    stored: `level` = LevelingService.level_info(), `lifeskill` = the plan 042
    Life & CP view, `cp` = ImperialService.cp_info() (the plan 081 value the
    Imperial card shows). Hidden / missing / malformed -> None, never zero."""
    lv = level if isinstance(level, dict) else {}
    life = lifeskill if isinstance(lifeskill, dict) and lifeskill.get("status") == "ok" else {}
    row = cp if isinstance(cp, dict) else {}
    lvl = _count(lv.get("level"))
    energy = _count(life.get("energy"))
    cpv = _count(row.get("value"))
    src = _text(row.get("source")) if cpv is not None else None
    cp_at = _text(row.get("at")) if cpv is not None else None
    if cp_at is None and src == "profile":
        cp_at = _text(life.get("at"))
    return {"level": lvl,
            "level_source": _text(lv.get("level_source")) if lvl is not None else None,
            "level_at": _text(lv.get("level_at")) if lvl is not None else None,
            "energy": energy, "energy_at": _text(life.get("at")) if energy is not None else None,
            "cp": cpv, "cp_src": src, "cp_at": cp_at, "name": _text(life.get("character"))}


def _shot_ok(name):
    return (isinstance(name, str) and SHOT_NAME_RE.match(name) is not None and ".." not in name
            and os.path.splitext(name)[1].lower() in SHOT_MIME)


def shot_id(name, at):
    return "s" + hashlib.sha256(f"{name}|{at}".encode("utf-8")).hexdigest()[:16]


def attribute(at, loads, windows):
    """Plan 083: the char_no a screenshot taken at `at` belongs to - the newest
    load at or before it inside the same logged-in window - else None.
    `loads`: [{char_no, at}]; `windows`: [{start, end|null}] (null = still open)."""
    t = _epoch(at)
    if t is None:
        return None
    win = None
    for w in windows or []:
        s = _epoch(w.get("start")) if isinstance(w, dict) else None
        e = _epoch(w.get("end")) if isinstance(w, dict) and w.get("end") is not None else None
        if s is not None and s <= t and (e is None or t <= e):
            win = s
    if win is None:
        return None
    best = None
    for row in loads or []:
        c = row.get("char_no") if isinstance(row, dict) else None
        lt = _epoch(row.get("at")) if isinstance(row, dict) else None
        if isinstance(c, str) and CHAR_RE.match(c) and lt is not None and win <= lt <= t:
            if best is None or lt >= best[0]:
                best = (lt, c)
    return best[1] if best else None


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


def _clean_shot(e):
    if not isinstance(e, dict) or not isinstance(e.get("id"), str) or not SHOT_ID_RE.match(e["id"]):
        return None
    at = e.get("at")
    if not _shot_ok(e.get("file")) or _epoch(at) is None:
        return None
    c = e.get("char_no")
    cls = e.get("cls") if isinstance(e.get("cls"), str) and e["cls"] else None
    return {"id": e["id"], "file": e["file"], "at": at,
            "char_no": c if isinstance(c, str) and CHAR_RE.match(c) else None, "cls": cls}


class PortraitService:
    """Store domain `portraits`: {"characters": {char_no: {cls, cls_src,
    first_seen, last_seen}}, "seen": {file name: [mtime, size]}, "index":
    [{id, char_no, mtime, at, sha}], "shots": [{id, file, at, char_no, cls}]
    (plan 083; `cls` = a typed class for an unattributed shot)}. Archive files
    live in `archive_dir`. `ledger` is the plan 079 OverrideLedger (or None)."""

    def __init__(self, store, archive_dir, documents=lambda: None, loads=lambda: [],
                 progress_cls=lambda: None, clock=time.time, on_change=None,
                 shots=lambda: [], windows=lambda: [], ledger=None, card=None):
        self.store = store
        self.card = card  # plan 084: () -> card_block(...), or None
        self.archive_dir = Path(archive_dir)
        self.documents = documents
        self.loads = loads
        self.progress_cls = progress_cls
        self.clock = clock
        self.on_change = on_change
        self.shots = shots
        self.windows = windows
        self.ledger = ledger
        self.classes = load_classes()
        self._lock = threading.RLock()

    # -- state -------------------------------------------------------------------

    def _load(self):
        doc = self.store.get("portraits")
        chars = doc.get("characters") if isinstance(doc.get("characters"), dict) else {}
        seen = doc.get("seen") if isinstance(doc.get("seen"), dict) else {}
        index = [e for e in map(_clean_entry, doc.get("index") or []) if e is not None] \
            if isinstance(doc.get("index"), list) else []
        shots = [e for e in map(_clean_shot, doc.get("shots") or []) if e is not None] \
            if isinstance(doc.get("shots"), list) else []
        return {"characters": {k: _clean_char(v) for k, v in chars.items()
                               if isinstance(k, str) and CHAR_RE.match(k)},
                "seen": {k: v for k, v in seen.items() if isinstance(k, str) and isinstance(v, list)},
                "index": index, "shots": shots}

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

    def _record_shots(self, d):
        """Plan 083: index each listed screenshot once, attributed at record time."""
        rows = []
        for s in self.shots() or []:
            if isinstance(s, dict) and _shot_ok(s.get("name")) and _epoch(s.get("mtime")) is not None:
                rows.append((s["name"], s["mtime"]))
        if not rows:
            return False
        have = {e["id"] for e in d["shots"]}
        floor = min((_epoch(e["at"]) for e in d["shots"]), default=None) \
            if len(d["shots"]) >= SHOT_CAP else None
        loads, windows = self.loads() or [], self.windows() or []
        added = False
        for name, at in rows:
            sid = shot_id(name, at)
            if sid in have or (floor is not None and _epoch(at) <= floor):
                continue  # recorded once; a shot older than a full index stays out
            d["shots"].append({"id": sid, "file": name, "at": at,
                               "char_no": attribute(at, loads, windows), "cls": None})
            have.add(sid)
            added = True
        if added:
            d["shots"].sort(key=lambda e: (_epoch(e["at"]), e["id"]))
            del d["shots"][:-SHOT_CAP]
        return added

    def poll(self, state=None, at=None):
        """GameWatch poller (fn(state, at)): register loads, bind, scan, index shots."""
        with self._lock:
            d = self._load()
            bound = self._register(d)
            new, dirty = self._scan(d)
            shot = self._record_shots(d)
            if bound or dirty or shot:
                self._save(d)
        if bound or new or shot:
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

    # -- gallery (plan 083) ------------------------------------------------------

    @staticmethod
    def _shot_cls(d, s):
        if s["cls"]:
            return s["cls"]
        ch = d["characters"].get(s["char_no"]) if s["char_no"] else None
        return ch["cls"] if ch else None

    @staticmethod
    def _portrait_item(e):
        return {"id": e["id"], "kind": "portrait", "at": e["at"], "char_no": e["char_no"]}

    @staticmethod
    def _shot_item(s):
        return {"id": s["id"], "kind": "shot", "at": s["at"], "char_no": s["char_no"]}

    def _history(self, d, cls):
        bound = {c for c, ch in d["characters"].items() if ch["cls"] == cls}
        rows = sorted((e for e in d["index"] if e["char_no"] in bound),
                      key=lambda e: e["mtime"], reverse=True)
        return [self._portrait_item(e) for e in rows]

    def _class_shots(self, d, cls):
        rows = [s for s in d["shots"] if self._shot_cls(d, s) == cls]
        rows.sort(key=lambda s: (_epoch(s["at"]), s["id"]), reverse=True)
        return [self._shot_item(s) for s in rows]

    def _unknown(self, d):
        bound = {c for c, ch in d["characters"].items() if ch["cls"]}
        out = [self._portrait_item(e) for e in d["index"] if e["char_no"] not in bound]
        out += [self._shot_item(s) for s in d["shots"] if self._shot_cls(d, s) is None]
        out.sort(key=lambda i: (_epoch(i["at"]) or 0, i["id"]), reverse=True)
        return out

    def _resolve(self, d, cls):
        """Current image of `cls`: the plan 079 pick while it is live, else the
        newest portrait. A newer portrait retires the pick (`portrait`); a
        pinned id no longer in this class's gallery retires it (`missing`)."""
        newest = self._current(d, cls)
        auto = None if newest is None else dict(self._portrait_item(newest), **{"from": "auto"})
        if self.ledger is None:
            return auto
        key = PICK_PREFIX + cls
        items = {i["id"]: i for i in self._history(d, cls) + self._class_shots(d, cls)}
        e = self.ledger.doc()["live"].get(key)
        if e is not None and e["value"] not in items:
            self.ledger.clear(key, by="missing")
            e = None
        live = None
        if newest is not None and (e is None or newest["mtime"] > (_epoch(e["set_at"]) or 0)):
            live = {"value": newest["id"], "signal": "portrait"}
        r = self.ledger.effective(key, live, None)
        if r["from"] != "override" or r["value"] not in items:
            return auto
        now = self.clock()
        ent = self._ledger_item(r["entry"], now)
        return dict(items[r["value"]], **{"from": "override", "set_at": r["entry"]["set_at"],
                                          "entry": ent})

    def _ledger_item(self, entry, now):
        return overrides.item(entry, self.ledger.policy, now)

    def _card(self):
        if self.card is None:
            return None
        try:
            return self.card()
        except Exception:  # noqa: BLE001 - a broken source never breaks the chip
            return None

    def view(self, char_no=None, cls=None):
        """GET /api/portraits body; `cls` adds that class's gallery (plan 083)."""
        with self._lock:
            d = self._load()
        pcls = self.progress_cls()
        names = {ch["cls"] for ch in d["characters"].values() if ch["cls"]}
        names |= {s["cls"] for s in d["shots"] if s["cls"]}
        if isinstance(pcls, str) and pcls:
            names.add(pcls)
        want = self._canon(cls)
        if want:
            names.add(want)
        classes = {c: {"current": self._resolve(d, c)} for c in sorted(names)}
        bound = {c for c, ch in d["characters"].items() if ch["cls"]}
        loaded = d["characters"].get(char_no) if isinstance(char_no, str) else None
        out = {"classes": classes,
               "unknown": sum(1 for e in d["index"] if e["char_no"] not in bound),
               "char_no": char_no if isinstance(char_no, str) else None,
               "loaded_cls": loaded["cls"] if loaded else None,
               "progress_cls": pcls if isinstance(pcls, str) and pcls else None,
               "card": self._card()}
        if want:
            out.update(cls=want, history=self._history(d, want),
                       shots=self._class_shots(d, want)[:SHOTS_PER_CLASS],
                       unknown_items=self._unknown(d))
        return out

    def pick(self, cls, pid):
        """Pin one gallery item as the class image (plan 079 typed override)."""
        want = self._canon(cls)
        if want is None or self.ledger is None:
            raise ValueError("pick: unknown class")
        with self._lock:
            d = self._load()
            ids = {i["id"] for i in self._history(d, want) + self._class_shots(d, want)}
        if not isinstance(pid, str) or pid not in ids:
            raise ValueError("pick: not an image of this class")
        self.ledger.set(PICK_PREFIX + want, pid, source="typed", reason="picked in the gallery")
        self._notify()
        return self.view(cls=want)

    def use_newest(self, key):
        """{"clear": "portrait.<cls>"}: retire the pick (back to the newest)."""
        want = self._canon(key[len(PICK_PREFIX):]) if isinstance(key, str) \
            and key.startswith(PICK_PREFIX) else None
        if want is None or self.ledger is None:
            raise ValueError("clear: not a portrait key")
        self.ledger.clear(PICK_PREFIX + want, by="operator")
        self._notify()
        return self.view(cls=want)

    def bind(self, pid, cls):
        """Optional typed class for an unknown-character item (research 0011 A4):
        a portrait or attributed shot binds its characterNo (`typed`); a shot
        with no characterNo carries the class itself. Never required."""
        want = self._canon(cls)
        if want is None or not isinstance(pid, str):
            raise ValueError("bind: unknown class or id")
        with self._lock:
            d = self._load()
            row = next((e for e in d["index"] if e["id"] == pid), None) \
                or next((s for s in d["shots"] if s["id"] == pid), None)
            if row is None:
                raise ValueError("bind: not an indexed image")
            if row.get("char_no"):
                ch = d["characters"].setdefault(row["char_no"], {
                    "cls": None, "cls_src": None, "first_seen": row["at"], "last_seen": row["at"]})
                ch["cls"], ch["cls_src"] = want, "typed"
            else:
                row["cls"] = want
            self._save(d)
        self._notify()
        return self.view(cls=want)

    def blob(self, pid, size="full"):
        """(bytes, mime) of an INDEXED id (never a path input), or None. A
        screenshot is served as its original file (no stdlib JPEG thumbnail)."""
        if isinstance(pid, str) and SHOT_ID_RE.match(pid) and size in SIZES:
            with self._lock:
                row = next((s for s in self._load()["shots"] if s["id"] == pid), None)
            docs = self.documents()
            if row is None or not docs:
                return None
            try:
                data = _read_once(Path(docs) / SHOT_DIR / row["file"])
            except OSError:
                return None
            return data, SHOT_MIME[os.path.splitext(row["file"])[1].lower()]
        png = self.image(pid, size)
        return None if png is None else (png, "image/png")

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
