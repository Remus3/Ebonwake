"""Plan 085: runtime verdicts of hinted data rows against cached patch notes.

`notes()` yields the cached official patch-notes pages (plan 064's fetcher
keeps their text; this module never fetches). Each run checks every hinted
row (`patchverify`) against every cached page, oldest first: a newer
non-silent verdict replaces an older one, a silent page never erases a
confirmed / contradicted one. `checked_at` is the time of the run that
checked the row (bug PV: the page's fetch time never moved, since a stamped
page is never re-fetched). Verdicts persist in `ops/runtime/
data_verdicts.json` (atomic write), keyed `<file>#<row id>`, each carrying the
row fingerprint: once a lane edits the tracked row (value or hint) its old
verdict is dropped. Tracked data files are never written here.
"""

import datetime as _dt
import threading
import time
from pathlib import Path

from . import patchverify
from .httpcache import read_json
from .store import atomic_write_json

STATE_FILE = Path(__file__).resolve().parents[2] / "ops" / "runtime" / "data_verdicts.json"
NOTES_TTL_S = 30
FIELDS = ("verdict", "evidence", "notice_no", "url", "date", "checked_at", "fp")


def _iso(at):
    return _dt.datetime.fromtimestamp(at, _dt.timezone.utc).replace(microsecond=0).isoformat()


def _order(v):
    return (v.get("date") or "", v.get("notice_no") or 0)


def _clean(v, fp):
    if not (isinstance(v, dict) and set(v) == set(FIELDS) and v["fp"] == fp
            and v["verdict"] in patchverify.VERDICTS):
        return None
    if not (isinstance(v["notice_no"], int) and isinstance(v["url"], str)):
        return None
    if v["evidence"] is not None and not isinstance(v["evidence"], str):
        return None
    return dict(v)


def apply(verdicts, hinted, note, checked):
    """Fold one cached note {group_no, title, url, stamp, fetched_at, text} into
    `verdicts` (in place), stamping every row it checks with `checked` (ISO);
    returns it."""
    date = patchverify.note_date(note["title"], note.get("stamp"))
    for key, r in patchverify.check(note["text"], note["title"], hinted).items():
        new = {"verdict": r["verdict"], "evidence": r["evidence"], "notice_no": note["group_no"],
               "url": note["url"], "date": date, "checked_at": checked, "fp": hinted[key]["fp"]}
        old = verdicts.get(key)
        if (old is None or old["verdict"] == "silent"
                or (new["verdict"] != "silent" and _order(new) >= _order(old))):
            verdicts[key] = new
        else:
            old["checked_at"] = checked  # kept, but checked against this page too
    return verdicts


class VerdictService:
    """GET /api/data/verdicts plus per-row lookups for the loaders (plan 085)."""

    def __init__(self, notes, path=STATE_FILE, data_dir=None, clock=time.time):
        self.notes = notes
        self.path = Path(path)
        self.data_dir = data_dir
        self.clock = clock
        self._lock = threading.Lock()
        self._hints = None
        self.error = None
        self._memo = None  # (notes signature, result)
        self._notes_at = None  # (read at, sorted notes)

    def hints(self):
        if self._hints is None:
            try:
                self._hints = patchverify.load(self.data_dir or patchverify.DATA_DIR)
                self.error = None
            except ValueError as e:
                self._hints, self.error = {"hinted": {}, "unchecked": []}, str(e)[:200]
        return self._hints

    def _notes(self):
        """Cached pages, re-read at most every NOTES_TTL_S: one grind / leveling view
        looks up ~25 rows and must not re-parse the page cache each time."""
        now = self.clock()
        if self._notes_at is not None and 0 <= now - self._notes_at[0] < NOTES_TTL_S:
            return self._notes_at[1]
        try:
            raw = self.notes() if self.notes is not None else []
        except Exception:  # noqa: BLE001 - a broken cache checks nothing, never a 500
            raw = []
        out = sorted((n for n in raw if isinstance(n, dict)),
                     key=lambda n: (patchverify.note_date(n["title"], n.get("stamp")) or "",
                                    n["group_no"]))
        self._notes_at = (now, out)
        return out

    def run(self):
        """Verdicts after folding every cached note; writes the state file only on change."""
        hints = self.hints()
        notes = self._notes()
        sig = tuple((n["group_no"], n["fetched_at"]) for n in notes)
        with self._lock:
            if self._memo is not None and self._memo[0] == sig:
                return self._memo[1]
            doc = read_json(self.path)
            doc = doc if isinstance(doc, dict) else {}
            prev = doc.get("verdicts") if isinstance(doc.get("verdicts"), dict) else {}
            verdicts = {}
            for k, h in hints["hinted"].items():
                c = _clean(prev.get(k), h["fp"])
                if c is not None:
                    verdicts[k] = c
            checked = _iso(self.clock())
            for n in notes:
                apply(verdicts, hints["hinted"], n, checked)
            last = doc.get("last_notice") if isinstance(doc.get("last_notice"), dict) else None
            if notes:
                n = notes[-1]
                last = {"notice_no": n["group_no"], "title": n["title"], "url": n["url"],
                        "date": patchverify.note_date(n["title"], n.get("stamp"))}
            out = {"verdicts": verdicts, "last_notice": last}
            if out != {"verdicts": prev, "last_notice": doc.get("last_notice")}:
                atomic_write_json(self.path, out)
            self._memo = (sig, out)
            return out

    def view(self):
        """{verdicts, unchecked, last_notice, error}; fingerprints stay internal."""
        res = self.run()
        return {"verdicts": {k: {f: v[f] for f in FIELDS if f != "fp"}
                             for k, v in sorted(res["verdicts"].items())},
                "unchecked": list(self.hints()["unchecked"]),
                "last_notice": res["last_notice"], "error": self.error}

    def verdict(self, key):
        """{verdict, date, evidence, url} of a confirmed / contradicted row, else None."""
        v = self.run()["verdicts"].get(key)
        if v is None or v["verdict"] == "silent":
            return None
        return {"verdict": v["verdict"], "date": v["date"], "evidence": v["evidence"],
                "url": v["url"]}

    def signal(self):
        """Plan 073 `data` row input."""
        res = self.run()
        vs = res["verdicts"]
        bad = sorted((k, v) for k, v in vs.items() if v["verdict"] == "contradicted")
        return {"error": self.error, "last_notice": res["last_notice"],
                "confirmed": sum(v["verdict"] == "confirmed" for v in vs.values()),
                "unchecked": len(self.hints()["unchecked"]),
                "contradicted": [{"key": k, "evidence": v["evidence"], "date": v["date"]}
                                 for k, v in bad]}
