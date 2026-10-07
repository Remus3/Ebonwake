"""Before-maintenance digest (plan 074): loss warnings + what ends at the next maintenance.

Two pure parts and one service, over text EW already holds:

1. Loss warnings. Plan 064 already GETs each maintenance notice's Detail page;
   `loss_lines` keeps the sentences of that page matching a tracked pattern
   (`data/maint_loss_patterns.json`, data not code), at most MAX_LOSS per
   notice, each cut to MAX_TEXT ASCII chars. They ride in plan 064's Detail
   cache (`loss`, `parse_v`); a cache entry with an older PARSE_V is re-read
   once. Each warning is due its notice's maintenance start.
2. Ending at maintenance: every stored event / coupon (plan 006 / 059 / 064)
   and Hot Time window (plan 064 dated, plan 011 weekly) whose end lies in [now, start + 1 h] and
   within ENDING_NEAR_S of the maintenance start ("before maintenance" edges
   resolve to the start itself).

`DigestService` serves GET / POST /api/maint/digest: the digest shows from
LEAD_S (T-24 h); an ack hides one warning until that maintenance is over.
No new GET, no new host; nothing here reads or touches the game.
"""

import datetime as _dt
import hashlib
import json
import re
import threading
import time
import unicodedata
from pathlib import Path

from . import maint as _maint

PATTERNS_FILE = Path(__file__).resolve().parent / "data" / "maint_loss_patterns.json"
PARSE_V = 2  # Detail cache entries before plan 074 count as 1
MAX_LOSS = 5
MAX_TEXT = 200
MAX_PATTERNS = 50
LEAD_S = 24 * 3600
ENDING_NEAR_S = 3600
MAX_ACKED = 200
MAX_NOTICE_NO = 10 ** 9
DOMAIN = "maint_digest"
KEY_RE = re.compile(r"^[0-9]{1,9}:[0-9a-f]{12}$")
_UTC = _dt.timezone.utc
_SENT_RE = re.compile(r"[.!?]\s+(?=[A-Z0-9\"'(])")
# a period after one of these ("Oct. 8", "No. 3", "e.g. ...") ends no sentence
_ABBR = {"jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
         "mon", "tue", "tues", "wed", "thu", "thur", "thurs", "fri", "sat", "sun", "no", "vs",
         "approx", "e.g", "i.e", "etc", "st", "mr", "ms", "dr"}
_WORD_END_RE = re.compile(r"([A-Za-z.]+)$")
_SPACES_RE = re.compile(r"\s+")
_FOLD = {0x2018: "'", 0x2019: "'", 0x201c: '"', 0x201d: '"', 0x2013: "-", 0x2014: "-",
         0x2026: "...", 0x00a0: " "}


def _iso(when):
    return when.astimezone(_UTC).replace(microsecond=0).isoformat()


def _parse(v):
    if not isinstance(v, str) or not v:
        return None
    try:
        d = _dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo is not None else d.replace(tzinfo=_UTC)


# -- patterns + extraction ---------------------------------------------------------

def load_patterns(path=None, field="patterns"):
    """Compiled patterns of the tracked file's `field` list; a bad file raises ValueError."""
    try:
        doc = json.loads(Path(path or PATTERNS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ValueError(f"maint_loss_patterns.json unreadable: {e}") from e
    raw = doc.get(field) if isinstance(doc, dict) else None
    if not isinstance(raw, list) or not 0 < len(raw) <= MAX_PATTERNS:
        raise ValueError(f"maint_loss_patterns.json: {field} must be a non-empty list")
    out = []
    for p in raw:
        if not (isinstance(p, str) and p.strip() and p.isascii()):
            raise ValueError("maint_loss_patterns.json: each pattern is a non-empty ASCII string")
        try:
            out.append(re.compile(p, re.IGNORECASE))
        except re.error as e:
            raise ValueError(f"maint_loss_patterns.json: bad pattern {p!r}: {e}") from e
    return out


def ascii_clip(s, n=MAX_TEXT):
    """One line of plain ASCII, at most `n` chars (typographic quotes and dashes folded)."""
    s = str(s).translate(_FOLD)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = _SPACES_RE.sub(" ", "".join(c if c.isprintable() else " " for c in s)).strip()
    return s if len(s) <= n else s[:n - 3].rstrip() + "..."


def _split(line):
    out, at = [], 0
    for m in _SENT_RE.finditer(line):
        w = _WORD_END_RE.search(line[at:m.start()])
        if m.group(0)[0] == "." and w and w.group(1).lower() in _ABBR:
            continue
        out.append(line[at:m.start() + 1])
        at = m.end()
    out.append(line[at:])
    return out


def sentences(lines):
    for line in lines or ():
        for s in _split(line):
            s = s.strip()
            if s:
                yield s


def extract_loss(lines, patterns):
    """Sentences of `lines` matching any pattern -> up to MAX_LOSS ASCII texts."""
    out = []
    for s in sentences(lines):
        if any(p.search(s) for p in patterns):
            t = ascii_clip(s)
            if t and t not in out:
                out.append(t)
                if len(out) >= MAX_LOSS:
                    break
    return out


def loss_lines(lines, path=None):
    """`extract_loss` with the tracked patterns; an unreadable file yields nothing
    (the notice read itself never fails on it - a test pins the file)."""
    try:
        patterns = load_patterns(path)
    except ValueError:
        return []
    return extract_loss(lines, patterns)


def clean_loss(raw):
    """A cached `loss` list re-validated: ASCII strings, 1..MAX_TEXT, at most MAX_LOSS."""
    out = []
    for t in raw if isinstance(raw, list) else []:
        if isinstance(t, str) and 0 < len(t) <= MAX_TEXT and t.isascii() and t.isprintable() \
                and t not in out:
            out.append(t)
    return out[:MAX_LOSS]


def multi_character_patterns(path=None):
    """Patterns of warnings only a multi-character account can act on (Tag
    Characters, alts); an unreadable list suppresses nothing."""
    try:
        return load_patterns(path, "multi_character")
    except ValueError:
        return []


def split_applicable(warns, multi_character, patterns):
    """(shown, suppressed): with `multi_character` off, a warning matching any
    pattern concerns a feature the account cannot have and is suppressed."""
    if multi_character:
        return list(warns), []
    shown, hidden = [], []
    for w in warns:
        (hidden if any(p.search(w["text"]) for p in patterns) else shown).append(w)
    return shown, hidden


def warning_key(notice_no, text):
    """Ack key: notice number + a short hash of the sentence."""
    return f"{notice_no}:{hashlib.sha256(text.encode('ascii')).hexdigest()[:12]}"


# -- the next maintenance --------------------------------------------------------------

def next_maint(now, slot=None, notices=None):
    """(start, end, source) of the first maintenance starting after `now` (an
    official notice beats the weekly slot for its date), or None."""
    notices = notices or {}
    t = now
    for _ in range(3):
        win = _maint.next_window(t, slot, notices)
        if win is None:
            return None
        start, end = win
        if start > now:
            n = notices.get(start.date().isoformat())
            return start, end, n["source"] if n else "weekly slot"
        t = end + _dt.timedelta(seconds=1)  # under way: the one after it
    return None


# -- digest parts ----------------------------------------------------------------------

def warnings(rows, now, start):
    """Loss rows [{notice_no, url, due_utc, loss: [text]}] -> warnings
    {key, text, notice_no, url, due_utc} due after `now` and by `start` + 1 h."""
    out, seen = [], set()
    for r in rows or ():
        if not isinstance(r, dict):
            continue
        no, url, due = r.get("notice_no"), r.get("url"), _parse(r.get("due_utc"))
        if not (isinstance(no, int) and not isinstance(no, bool) and 0 < no < MAX_NOTICE_NO):
            continue
        if due is None or not isinstance(url, str) or due <= now:
            continue
        if (due - start).total_seconds() > ENDING_NEAR_S:
            continue
        for text in clean_loss(r.get("loss")):
            key = warning_key(no, text)
            if key in seen:
                continue
            seen.add(key)
            out.append({"key": key, "text": text, "notice_no": no, "url": url,
                        "due_utc": _iso(due)})
    return sorted(out, key=lambda w: (w["due_utc"], w["notice_no"]))


def ends_at_maint(ends, now, start):
    """True when `ends` is in [now, start + 1 h] and within 1 h of `start`."""
    if ends is None or ends < now:
        return False
    return abs((ends - start).total_seconds()) <= ENDING_NEAR_S


def ending(items, hot, now, start):
    """[{kind, title, ends}] of open events / coupons (plan 006 store) and plan
    064 Hot Time windows that end at this maintenance, soonest first."""
    out = []
    for it in items or ():
        if not isinstance(it, dict) or it.get("done") is True:
            continue
        kind, title = it.get("kind"), it.get("title")
        ends = _parse(it.get("ends"))
        if not isinstance(kind, str) or not isinstance(title, str) or not title:
            continue
        if ends_at_maint(ends, now, start):
            out.append({"kind": kind, "title": title, "ends": _iso(ends)})
    for w in hot or ():
        if not isinstance(w, dict):
            continue
        ends = _parse(w.get("end"))
        label = w.get("label") if isinstance(w.get("label"), str) and w["label"] else "Hot Time"
        bonus = w.get("bonus")
        title = f"{label} {bonus}" if isinstance(bonus, str) and bonus else label
        if ends_at_maint(ends, now, start):
            out.append({"kind": "hot", "title": title, "ends": _iso(ends)})
    return sorted(out, key=lambda r: (r["ends"], r["kind"], r["title"]))


def _hhmm(v):
    m = re.fullmatch(r"([01][0-9]|2[0-3]):([0-5][0-9])", v) if isinstance(v, str) else None
    return None if m is None else int(m.group(1)) * 60 + int(m.group(2))


def weekly_hot(windows, now, start):
    """Plan 011 weekly Hot Time windows ({days: [weekday 0=Mon], start, end
    HH:MM UTC, label, pct}; an end at or before the start wraps past
    midnight) -> dated occurrences {label, bonus, end} from yesterday through
    the day after `start`, for `ending`."""
    out = []
    first = now.astimezone(_UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    span = (start.astimezone(_UTC).date() - first.date()).days + 2
    for w in windows or ():
        if not isinstance(w, dict) or not isinstance(w.get("days"), list):
            continue
        s, e = _hhmm(w.get("start")), _hhmm(w.get("end"))
        if s is None or e is None:
            continue
        dur = (e - s) % 1440  # = leveling.hot_status: a zero-length window never runs
        if dur == 0:
            continue
        label = w.get("label") if isinstance(w.get("label"), str) and w["label"] else "Hot Time"
        pct = w.get("pct")
        bonus = f"+{pct}%" if isinstance(pct, int) and not isinstance(pct, bool) else None
        for back in range(-1, max(span, 0) + 1):
            day = first + _dt.timedelta(days=back)
            if day.weekday() in w["days"]:
                end = day + _dt.timedelta(minutes=s + dur)
                out.append({"label": label, "bonus": bonus, "end": _iso(end)})
    return out


# -- service -----------------------------------------------------------------------------

def _clean_acked(doc):
    raw = doc.get("acked") if isinstance(doc, dict) else None
    out, seen = [], set()
    for a in raw if isinstance(raw, list) else []:
        if not isinstance(a, dict):
            continue
        key, until = a.get("key"), a.get("until")
        if not (isinstance(key, str) and KEY_RE.match(key)) or _parse(until) is None:
            continue
        if key not in seen:
            seen.add(key)
            out.append({"key": key, "until": until})
    return out[-MAX_ACKED:]


class DigestService:
    """GET /api/maint/digest -> {now, maint: {start_utc, end_utc, source} | None,
    show, lead_s, in_s, warnings, ending, acked}; POST {"ack": key} hides one
    warning until the end of the maintenance it is due at (store domain
    DOMAIN). Inputs are zero-arg callables over views EW already serves:
    `loss_rows` (plan 064 Detail cache), `items` (plan 006 events), `hot`
    (plan 064 dated Hot Time windows), `weekly` (plan 011 weekly Hot Time
    windows), `maint_inputs` -> {slot, notices}, `multi_character` -> the
    profile.multi_character setting (off: Tag / alt-only warnings go to
    `suppressed`, never to `warnings`, the ladder or an ack)."""

    def __init__(self, store, loss_rows, items, hot, maint_inputs, clock=time.time,
                 weekly=None, multi_character=None):
        self.store = store
        self.loss_rows = loss_rows
        self.items = items
        self.hot = hot
        self.weekly = weekly or (lambda: [])
        self.maint_inputs = maint_inputs
        self.multi_character = multi_character or (lambda: False)
        self.clock = clock
        self._lock = threading.Lock()
        self._multi_pats = multi_character_patterns()

    def _multi(self):
        try:
            return self.multi_character() is True
        except Exception:  # noqa: BLE001 - an unreadable setting = the default (one character)
            return False

    def _now(self):
        return _dt.datetime.fromtimestamp(self.clock(), _UTC)

    def _safe(self, fn):
        try:
            v = fn()
        except Exception:  # noqa: BLE001 - one bad source never blanks the digest
            return []
        return v if isinstance(v, list) else []

    def _all(self, now):
        try:
            m = self.maint_inputs() or {}
        except Exception:  # noqa: BLE001 - an unreadable setting = the data file slot
            m = {}
        nxt = next_maint(now, m.get("slot"), m.get("notices"))
        if nxt is None:
            return None, [], [], []
        start = nxt[0]
        hot = self._safe(self.hot) + weekly_hot(self._safe(self.weekly), now, start)
        warns, hidden = split_applicable(warnings(self._safe(self.loss_rows), now, start),
                                         self._multi(), self._multi_pats)
        return nxt, warns, ending(self._safe(self.items), hot, now, start), hidden

    def view(self):
        now = self._now()
        nxt, warns, ends, hidden = self._all(now)
        with self._lock:
            acked = {a["key"] for a in _clean_acked(self.store.get(DOMAIN))
                     if _parse(a["until"]) > now}
        base = {"now": _iso(now), "lead_s": LEAD_S}
        if nxt is None:
            return dict(base, maint=None, show=False, in_s=None, warnings=[], ending=[],
                        acked=[], suppressed=[])
        start, end, source = nxt
        in_s = int((start - now).total_seconds())
        return dict(base, maint={"start_utc": _iso(start), "end_utc": _iso(end), "source": source},
                    show=0 < in_s <= LEAD_S, in_s=in_s,
                    warnings=[w for w in warns if w["key"] not in acked], ending=ends,
                    acked=[w for w in warns if w["key"] in acked], suppressed=hidden)

    def unacked(self):
        """Warnings not acked, whatever the lead (plan 070 ladder timers)."""
        return self.view()["warnings"]

    def ack(self, key):
        """Hide one current warning until its maintenance is over; returns the view."""
        if not (isinstance(key, str) and KEY_RE.match(key)):
            raise ValueError("ack must be a warning key (notice:hash)")
        now = self._now()
        nxt, warns, _, _ = self._all(now)
        if nxt is None or key not in {w["key"] for w in warns}:
            raise ValueError(f"no current maintenance warning {key}")
        until = _iso(nxt[1])
        with self._lock:
            acked = [a for a in _clean_acked(self.store.get(DOMAIN))
                     if a["key"] != key and _parse(a["until"]) > now]
            self.store.put(DOMAIN, {"acked": (acked + [{"key": key, "until": until}])[-MAX_ACKED:]})
        return self.view()
