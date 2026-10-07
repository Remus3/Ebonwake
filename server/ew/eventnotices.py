"""Official event-notice suggestions (plan 059) from the NA/EU Events board.

Same conditions as plan 014's coupon check, on the same official host: only
while robots.txt allows the list and Detail paths for both `*` and our agent
(`coupons.robots_verdict`; disallow or unreachable = off), a persisted attempt
floor of one run per MIN_INTERVAL_S, and per run one GET of LIST_URL plus at
most MAX_DETAILS Detail GETs for notices not cached yet (a cached Detail is
re-read only when the list shows a new "Last Updated" stamp, or after
DETAIL_MAX_AGE_S when the list shows none). Unauthenticated, read-only,
never adds: each window is a suggestion the operator adds or dismisses.

A notice's first window line is stored raw (date + "before"/"after"
maintenance, or an explicit HH:MM UTC); it is resolved to UTC on every read
through `maint`, so a changed maintenance setting needs no refetch.

Plan 064 widens this to the Notices (1) and Updates (2) boards, still one run
per MIN_INTERVAL_S and MAX_DETAILS Detail GETs per run in total, and reads
more from each Detail page: a "<Mon D> (<Ddd>) Maintenance" notice's UTC
column (found by header text) -> store domain `maint_notices`, which beats
the weekly slot; Hot Time bonus lines -> plan 011 auto windows; word-style
coupon codes beside the copy button. With `notices.auto_add` (plan 080:
fixed on; a config value is a 24 h incident switch) a
parse whose window has both ends is ADDED (`auto: true`, source link) and
remembered by groupContentNo, so undo / dismiss sticks; partial parses stay
suggestions. When the official host has failed for STEAM_AFTER_S, the Steam
store news RSS (robots-gated on its own host) gives titles only, as a
"check official notices" hint - never a window.

Plan 074: a maintenance notice's Detail read also keeps its loss-warning
sentences (`maintdigest.loss_lines`, `loss` + `parse_v` in the Detail
cache); a maintenance entry with an older `parse_v` is re-read once, inside
the same MAX_DETAILS budget.

Plan 086: every Detail read also keeps its reward claim-window sentences
(`claimwindows.claim_lines`, `claims`); `parse_v` 3, so any older entry is
re-read once inside the same budget. Each read syncs the latest claim
deadline onto the stored events / coupons of that notice (`claim_until`,
`claim_text`) when it outlives their end.
"""

import datetime as _dt
import html.parser
import re
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from . import __version__, claimwindows, logindays, maint, maintdigest
from .coupons import HOST, MAX_BYTES, ROBOTS, TIMEOUT_S, UA_TOKEN, robots_verdict
from .coupons import _TOKEN_RE, _clean_title, copy_codes, is_code, is_word_code, parse_date
from .httpcache import CachedClient, UpstreamError, freshness, read_json
from .store import atomic_write_json
from .today import _iso, _parse_iso

BOARDS = (3, 1, 2)  # Events (plan 059) first, then Notices and Updates (plan 064)
LIST_URLS = {b: f"https://{HOST}/en-US/News/Notice?boardType={b}" for b in BOARDS}
LIST_URL = LIST_URLS[3]  # = events.SOURCES[1]
DETAIL_URL = f"https://{HOST}/en-US/News/Detail?groupContentNo="
DETAIL_PATH = "/en-US/News/Detail"
ROBOT_URLS = tuple(LIST_URLS.values()) + (DETAIL_URL + "1",)
USER_AGENT = (f"{UA_TOKEN}/{__version__} (BDO companion; event notice check, "
              "3 lists + at most 5 notice GETs per 6 h)")
STEAM_HOST = "store.steampowered.com"  # the Steam Web API host stays banned
STEAM_RSS = f"https://{STEAM_HOST}/feeds/news/app/582660/"
STEAM_ROBOTS = f"https://{STEAM_HOST}/robots.txt"
STEAM_AFTER_S = 24 * 3600
STEAM_FILE = "eventnotices_steam.json"
MAX_HINTS = 10
MAINT_DOMAIN = "maint_notices"
MAX_MAINT = 30
MAX_AUTO = 200
MIN_INTERVAL_S = 6 * 3600
TTL_S = MIN_INTERVAL_S
MAX_DETAILS = 5
DETAIL_MAX_AGE_S = 7 * 86400
MAX_NOTICES = 60
MAX_DISMISSED = 500
MAX_GROUP_NO = 10 ** 9
KEY = "eventnotices_list"
ATTEMPT_FILE = "eventnotices_attempt.json"
DETAILS_FILE = "eventnotices_details.json"
DOMAIN = "event_notices"
_DEFAULT_CACHE = (Path(__file__).resolve().parents[2] / "ops" / "runtime" / "cache"
                  / "eventnotices")

_WS_RE = re.compile(r"[ \t\r\f\v]+")
_MONTHS = {m: n for n, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


def default_fetch(url, timeout):
    """Plain unauthenticated GET, size-capped; raises on HTTP error or timeout."""
    req = urllib.request.Request(url, method="GET",
                                 headers={"User-Agent": USER_AGENT,
                                          "Accept": "text/html,text/plain;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (fixed https URLs)
        return r.read(MAX_BYTES)


def _decode(raw):
    return raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)


def _is_no(v):
    return isinstance(v, int) and not isinstance(v, bool) and 0 < v < MAX_GROUP_NO


def detail_url(group_no):
    return f"{DETAIL_URL}{group_no}&countryType=en-US"


def group_no_of(href, base=LIST_URL):
    """groupContentNo of an official Detail link (same host, same path), or None."""
    try:
        p = urllib.parse.urlsplit(urllib.parse.urljoin(base, href.strip()))
        q = urllib.parse.parse_qs(p.query)
    except (ValueError, AttributeError):
        return None
    if p.scheme != "https" or p.hostname != HOST or p.path != DETAIL_PATH:
        return None
    vals = q.get("groupContentNo") or []
    if len(vals) != 1 or not re.fullmatch(r"[0-9]{1,9}", vals[0]):
        return None
    n = int(vals[0])
    return n if _is_no(n) else None


# -- list -------------------------------------------------------------------------

class _ListParser(html.parser.HTMLParser):
    """Every <a href=...Detail...> with its text and its class="title" text."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self._cur = None
        self._skip = 0
        self._title = []
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
            return
        a = dict(attrs)
        if tag == "a":
            self._flush()
            href = a.get("href") or ""
            if DETAIL_PATH in href:
                self._cur = {"href": href, "text": [], "title": [], "rest": []}
                self._depth, self._title = 0, []
            return
        if self._cur is not None and tag not in ("br", "img", "hr", "input", "meta"):
            self._depth += 1
            if "title" in (a.get("class") or "").lower().split():
                self._title.append(self._depth)

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
            return
        if tag == "a":
            self._flush()
            return
        if self._cur is not None:
            if self._title and self._title[-1] == self._depth:
                self._title.pop()
            self._depth = max(0, self._depth - 1)

    def handle_data(self, data):
        if self._cur is None or self._skip:
            return
        self._cur["text"].append(data)
        self._cur["title" if self._title else "rest"].append(data)

    def _flush(self):
        if self._cur is not None:
            self.links.append({k: v if k == "href" else " ".join(v) for k, v in self._cur.items()})
        self._cur = None

    def close(self):
        super().close()
        self._flush()


def parse_list(page):
    """Events board -> [{group_no, title, url, stamp}] in page order, one per
    group_no, official Detail links only; `stamp` is a date beside the title
    ("Last Updated"), or None when the list shows none."""
    p = _ListParser()
    try:
        p.feed(page if isinstance(page, str) else _decode(page))
        p.close()
    except Exception:  # noqa: BLE001 - malformed markup degrades to what was parsed
        pass
    out, seen = [], set()
    for link in p.links:
        n = group_no_of(link["href"])
        if n is None or n in seen:
            continue
        has_title = bool(link["title"].strip())
        title = _clean_title(link["title"] if has_title else link["text"])
        if not title:
            continue
        seen.add(n)
        out.append({"group_no": n, "title": title, "url": detail_url(n),
                    "stamp": parse_date(link["rest"]) if has_title else None})
        if len(out) >= MAX_NOTICES:
            break
    return out


# -- detail -----------------------------------------------------------------------

_BLOCK = {"p", "div", "li", "ul", "ol", "br", "tr", "td", "table", "section", "article",
          "h1", "h2", "h3", "h4", "h5", "h6", "dd", "dt"}


class _TextParser(html.parser.HTMLParser):
    """Visible text, one line per block element; head/script/style dropped.
    `unwrap` (plan 074): a source line break inside a block is a space, so a
    sentence wrapped in the markup stays one line."""

    def __init__(self, unwrap=False):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._skip = 0
        self._unwrap = unwrap

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head", "title"):
            self._skip += 1
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head", "title"):
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data.replace("\n", " ") if self._unwrap else data)


def _point(p):
    return (rf"(?P<{p}mon>[A-Za-z]{{3,9}})\.?\s+(?P<{p}day>[0-9]{{1,2}})(?:st|nd|rd|th)?"
            rf"(?:,?\s+(?P<{p}year>[0-9]{{4}}))?(?:\s*\([A-Za-z]{{3,4}}\.?\))?"
            rf"(?:\s*,?\s*(?:(?P<{p}edge>after|before)\s+(?:the\s+)?maintenance"
            rf"|(?:at\s+)?(?P<{p}hh>[0-9]{{1,2}}):(?P<{p}mm>[0-9]{{2}})\s*\(?UTC\)?))?")


_DASHES = "-~" + chr(0x2013) + chr(0x2014)  # hyphen, tilde, en dash, em dash
_WINDOW_RE = re.compile(_point("s_") + r"\s*(?:[" + _DASHES + r"]|\bto\b|\buntil\b)\s*"
                        + _point("e_"), re.IGNORECASE)


def _pt(m, p, year):
    """One window point -> {date, edge, time}, or None when not a real date."""
    mon = _MONTHS.get(m.group(p + "mon")[:3].lower())
    if mon is None:
        return None
    try:
        d = _dt.date(year, mon, int(m.group(p + "day")))
    except ValueError:
        return None
    edge = m.group(p + "edge")
    t = None
    if m.group(p + "hh") is not None:
        hh, mm = int(m.group(p + "hh")), int(m.group(p + "mm"))
        if hh > 23 or mm > 59:
            return None
        t = f"{hh:02d}:{mm:02d}"
    return {"date": d.isoformat(), "edge": edge.lower() if edge else None, "time": t}


def _key(pt, end):
    """Sort instant of a raw point under the default slot (order check only)."""
    return _point_utc(pt, maint.DEFAULT, end)


def _start_year(m, ref_year, ref_month):
    """Both years missing: the start year (ref_year - 1 .. + 1) whose start
    lies nearest the fetch month, so "Dec 10 - Dec 31" read in January is last
    December and "Jan 2 - Jan 20" read in December is next January."""
    if ref_month is None:
        return ref_year
    ref = _dt.date(ref_year, ref_month, 15)
    best = None
    for y in (ref_year - 1, ref_year, ref_year + 1):
        s = _pt(m, "s_", y)
        if s is not None:
            dist = abs((_dt.date.fromisoformat(s["date"]) - ref).days)
            if best is None or dist < best[0]:
                best = (dist, y)
    return ref_year if best is None else best[1]


def _years(m, ref_year, ref_month):
    sy, ey = m.group("s_year"), m.group("e_year")
    sy = int(sy) if sy else None
    ey = int(ey) if ey else None
    if sy is None and ey is None:
        y = _start_year(m, ref_year, ref_month)
        return y, y, "end"
    if sy is None:
        return ey, ey, "start"
    if ey is None:
        return sy, sy, "end"
    return sy, ey, None


def parse_window(line, ref_year, ref_month=None):
    """First `START - END` window in one line of text -> {starts, ends,
    ends_text}, each point {date, edge, time}; None when there is none. A
    missing year comes from the other point, else from the fetch date
    (`ref_year`, `ref_month`: the start nearest it); an end before its start
    wraps into the next year."""
    for m in _WINDOW_RE.finditer(line):
        try:
            sy, ey, fix = _years(m, ref_year, ref_month)
            s, e = _pt(m, "s_", sy), _pt(m, "e_", ey)
            if s is None or e is None:
                continue
            if _key(e, True) < _key(s, False) and fix is not None:
                if fix == "end":
                    e = _pt(m, "e_", ey + 1)
                else:
                    s = _pt(m, "s_", sy - 1)
                if s is None or e is None:
                    continue
            if _key(e, True) < _key(s, False):
                continue
        except (ValueError, OverflowError):
            continue
        ends_text = _WS_RE.sub(" ", line[m.start("e_mon"):m.end()]).strip()
        return {"starts": s, "ends": e, "ends_text": ends_text[:120]}
    return None


def _lines(page, unwrap=False):
    """Visible text of a page, one stripped non-empty line per block."""
    p = _TextParser(unwrap)
    try:
        p.feed(page if isinstance(page, str) else _decode(page))
        p.close()
    except Exception:  # noqa: BLE001 - malformed markup degrades to what was parsed
        pass
    return [s for s in (_WS_RE.sub(" ", ln).strip() for ln in "".join(p.parts).split("\n")) if s]


def parse_detail(page, ref_year, ref_month=None):
    """The first window line of a Detail page, or None (no parseable window)."""
    for line in _lines(page):
        w = parse_window(line, ref_year, ref_month)
        if w is not None:
            return w
    return None


# -- plan 064: maintenance table, Hot Time, coupon codes ----------------------------

_SPACES_RE = re.compile(r"\s+")
_DOW = {d: n for n, d in enumerate(("mon", "tue", "wed", "thu", "fri", "sat", "sun"))}
_MAINT_TITLE_RE = re.compile(
    r"^(?:\[[^\]]{1,40}\]\s*)?(?P<mon>[A-Za-z]{3,9})\.?\s+(?P<day>[0-9]{1,2})"
    r"(?:,\s*(?P<year>[0-9]{4}))?\s*\((?P<dow>[A-Za-z]{3,4})\.?\)\s*Maintenance\b", re.IGNORECASE)
# The UTC column: "UTC" not followed by a non-zero offset ("PDT (UTC-7)" is not
# it) and no other zone name in the header cell.
_UTC_HEAD_RE = re.compile(r"\bUTC\b(?!\s*[+-]\s*[0-9]*[1-9])", re.IGNORECASE)
_ZONE_RE = re.compile(r"\b(?:P[SD]T|M[SD]T|C[SD]T|E[SD]T|CES?T|BST|KST|JST|AES?T)\b",
                      re.IGNORECASE)
_CLOCK_RE = re.compile(r"\b([0-9]{1,2}):([0-9]{2})(?:\s*([AaPp])\.?[Mm]\b\.?)?")
_MD_RE = re.compile(r"\b([A-Za-z]{3,9})\.?\s+([0-9]{1,2})\b")
_HOT_RE = re.compile(r"\bhot\s*time\b", re.IGNORECASE)
_BONUS_RE = re.compile(r"(?P<name>(?:[A-Za-z]+\s+)?EXP)\s*\+\s*"
                       r"(?P<n>[0-9]{1,3}(?:,[0-9]{3})+|[0-9]{1,5})\s*%", re.IGNORECASE)
_ENDS_RE = re.compile(r"^Ends\s*:?\s+(?P<mon>[A-Za-z]{3,9})\.?\s+(?P<day>[0-9]{1,2})"
                      r"(?:st|nd|rd|th)?\b(?:,?\s+(?P<year>[0-9]{4})\b)?", re.IGNORECASE)
HOT_PCT = (1, 1000)  # = leveling.XP_PCT_RANGE without 0
MAX_BONUS = 40
MAX_CODES = 20


def _near_date(mon, day, ref, dow=None):
    """A yearless month/day in ref.year - 1 .. + 1: the one on weekday `dow`
    when given, else (and as tie-break) the one nearest `ref`."""
    best = None
    for y in (ref.year - 1, ref.year, ref.year + 1):
        try:
            d = _dt.date(y, mon, day)
        except ValueError:
            continue
        key = (dow is not None and d.weekday() != dow, abs((d - ref).days))
        if best is None or key < best[0]:
            best = (key, d)
    return None if best is None else best[1]


def _ref_date(ref_year, ref_month):
    return _dt.date(ref_year, ref_month or 7, 1 if ref_month is None else 15)


def maint_title_date(title, ref_year, ref_month=None):
    """Date of a "<Month> <D> (<Ddd>) Maintenance" title, or None."""
    m = _MAINT_TITLE_RE.match(title or "")
    mon = _MONTHS.get(m.group("mon")[:3].lower()) if m else None
    if mon is None:
        return None
    day = int(m.group("day"))
    if m.group("year"):
        try:
            return _dt.date(int(m.group("year")), mon, day)
        except ValueError:
            return None
    return _near_date(mon, day, _ref_date(ref_year, ref_month),
                      _DOW.get(m.group("dow")[:3].lower()))


class _TableParser(html.parser.HTMLParser):
    """Every <table> as rows of cell texts (th and td alike)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self._stack = []
        self._cell = None
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "table":
            self._end_cell()
            self._stack.append([])
        elif tag == "tr" and self._stack:
            self._end_cell()
            self._stack[-1].append([])
        elif tag in ("td", "th") and self._stack:
            self._end_cell()
            if not self._stack[-1]:
                self._stack[-1].append([])
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in ("td", "th"):
            self._end_cell()
        elif tag == "table" and self._stack:
            self._end_cell()
            self.tables.append(self._stack.pop())

    def handle_data(self, data):
        if self._cell is not None and not self._skip:
            self._cell.append(data)

    def _end_cell(self):
        if self._cell is not None and self._stack and self._stack[-1]:
            self._stack[-1][-1].append(_SPACES_RE.sub(" ", "".join(self._cell)).strip())
        self._cell = None


def _utc_col(row):
    for i, c in enumerate(row):
        if _UTC_HEAD_RE.search(c) and not _ZONE_RE.search(c):
            return i
    return None


def _clock(m):
    hh, mm, ap = int(m.group(1)), int(m.group(2)), m.group(3)
    if ap:
        if not 1 <= hh <= 12:
            return None
        hh = hh % 12 + (12 if ap.lower() == "p" else 0)
    return (hh, mm) if hh <= 23 and mm <= 59 else None


def _cell_window(cell, day):
    """"Oct 8 (Thu) 07:00 - 11:00" (24 h or AM/PM; dates optional, else the
    title's) -> (start, end) UTC datetimes, or None. A yearless date is taken
    nearest the title date; an end not after its start, with no own date, is
    the next day."""
    clocks = [_clock(m) for m in _CLOCK_RE.finditer(cell)][:2]
    if len(clocks) < 2 or None in clocks:
        return None
    dates = []
    for m in _MD_RE.finditer(cell):
        mon = _MONTHS.get(m.group(1)[:3].lower())
        if mon is not None:
            d = _near_date(mon, int(m.group(2)), day)
            if d is not None:
                dates.append(d)
    sd = dates[0] if dates else day
    ed = dates[1] if len(dates) > 1 else sd
    s = _dt.datetime.combine(sd, _dt.time(*clocks[0]), _dt.timezone.utc)
    e = _dt.datetime.combine(ed, _dt.time(*clocks[1]), _dt.timezone.utc)
    if e <= s and len(dates) < 2:
        e += _dt.timedelta(days=1)
    if not 0 < (e - s).total_seconds() <= maint.MAX_DURATION_MIN * 60:
        return None
    return s, e


def parse_maint(page, title, ref_year, ref_month=None):
    """A maintenance notice's window from its time table, the UTC column found
    BY HEADER TEXT (column order changes with DST): {date, start_utc,
    end_utc} (date = the UTC start date), or None."""
    day = maint_title_date(title, ref_year, ref_month)
    if day is None:
        return None
    p = _TableParser()
    try:
        p.feed(page if isinstance(page, str) else _decode(page))
        p.close()
    except Exception:  # noqa: BLE001 - malformed markup degrades to what was parsed
        pass
    for table in p.tables:
        col = None
        for row in table:
            if col is None:
                col = _utc_col(row)
                continue
            w = _cell_window(row[col], day) if col < len(row) else None
            if w is not None:
                return {"date": w[0].date().isoformat(), "start_utc": _iso(w[0]),
                        "end_utc": _iso(w[1])}
    return None


def parse_hot(lines, title, ref_year, ref_month=None):
    """A Hot Time post (title or text says "Hot Time") with a bonus line such
    as "Combat EXP +1,000%" -> {bonus, pct, ends}: the Combat EXP line when
    there is one, else the first; `ends` is the raw point of an "Ends <Mon D>"
    line or None. None when not a Hot Time post or no bonus in range."""
    if not (_HOT_RE.search(title or "") or any(_HOT_RE.search(ln) for ln in lines)):
        return None
    best, ends = None, None
    ref = _ref_date(ref_year, ref_month)
    for line in lines:
        m = _ENDS_RE.match(line)
        mon = _MONTHS.get(m.group("mon")[:3].lower()) if m else None
        if ends is None and mon is not None:
            try:
                d = (_dt.date(int(m.group("year")), mon, int(m.group("day"))) if m.group("year")
                     else _near_date(mon, int(m.group("day")), ref))
            except ValueError:
                d = None
            if d is not None:
                ends = {"date": d.isoformat(), "edge": None, "time": None}
        for b in _BONUS_RE.finditer(line):
            pct = int(b.group("n").replace(",", ""))
            if not HOT_PCT[0] <= pct <= HOT_PCT[1]:
                continue
            name = _SPACES_RE.sub(" ", b.group("name")).strip()
            combat = name.lower() == "combat exp"
            if best is None or (combat and not best[0]):
                best = (combat, {"bonus": f"{name} +{b.group('n')}%"[:MAX_BONUS], "pct": pct})
    return None if best is None else dict(best[1], ends=ends)


def parse_codes(page, lines):
    """Coupon codes: word-style or dashed ones beside the copy button, plus
    plan 014's dashed shape on any line that mentions a coupon."""
    out = copy_codes(page)
    for line in lines:
        if "coupon" in line.lower():
            for tok in _TOKEN_RE.findall(line):
                if is_code(tok) and tok not in out:
                    out.append(tok)
    return out[:MAX_CODES]


def parse_notice(page, title, ref_year, ref_month=None):
    """Everything plan 059 + 064 + 074 read from one Detail page: {window,
    maint, hot, codes, loss, parse_v}; `loss` only on a maintenance notice;
    + plan 075 `login` (a suggested login rule, or None); + plan 086 `claims`
    [{text, until}] (raw claim deadlines)."""
    text = page if isinstance(page, str) else _decode(page)
    lines = _lines(text)
    unwrapped = _lines(text, unwrap=True)
    window = next((w for w in (parse_window(ln, ref_year, ref_month) for ln in lines)
                   if w is not None), None)
    m = parse_maint(text, title, ref_year, ref_month)
    return {"window": window, "maint": m,
            "hot": parse_hot(lines, title, ref_year, ref_month),
            "codes": parse_codes(text, lines),
            "login": logindays.suggest_rule([title] + lines if isinstance(title, str) else lines),
            "loss": maintdigest.loss_lines(unwrapped) if m is not None else [],
            "claims": claimwindows.claim_lines(unwrapped, ref_year, ref_month),
            "parse_v": maintdigest.PARSE_V}


def parse_rss_titles(text):
    """Item titles of an RSS feed (titles only; nothing else is read)."""
    out = []
    for item in re.findall(r"<item\b.*?</item>", text, re.IGNORECASE | re.DOTALL):
        m = re.search(r"<title>(.*?)</title>", item, re.IGNORECASE | re.DOTALL)
        if m is None:
            continue
        raw = m.group(1).strip()
        if raw.startswith("<![CDATA[") and raw.endswith("]]>"):
            raw = raw[9:-3]
        t = _clean_title(html.unescape(raw))
        if t and t not in out:
            out.append(t)
        if len(out) >= MAX_HINTS:
            break
    return out


# -- resolution -------------------------------------------------------------------

def _point_utc(pt, slot, end, notices=None):
    d = _dt.date.fromisoformat(pt["date"])
    if pt["time"] is not None:
        hh, mm = (int(x) for x in pt["time"].split(":"))
        return _dt.datetime.combine(d, _dt.time(hh, mm), _dt.timezone.utc)
    if pt["edge"] is not None:
        return maint.resolve(d, pt["edge"], slot, notices)
    t = _dt.time(23, 59, 59) if end else _dt.time(0, 0, 0)
    return _dt.datetime.combine(d, t, _dt.timezone.utc)


def resolve_window(w, slot, notices=None):
    """Raw window -> {starts, ends (ISO UTC), ends_text, maint_relative, ends_edge}.
    An explicit HH:MM UTC wins; else before/after maintenance via `maint`
    (plan 064: an imported maintenance notice for that date beats the slot)."""
    s, e = w["starts"], w["ends"]
    return {"starts": _iso(_point_utc(s, slot, False, notices)),
            "ends": _iso(_point_utc(e, slot, True, notices)),
            "ends_text": w["ends_text"],
            "maint_relative": any(p["time"] is None and p["edge"] is not None for p in (s, e)),
            "ends_edge": e["edge"] if e["time"] is None else None}


def _clean_point(p):
    if not (isinstance(p, dict) and set(p) == {"date", "edge", "time"}):
        return None
    if not (isinstance(p["date"], str) and _DATE_RE.match(p["date"])):
        return None
    if p["edge"] not in (None,) + maint.EDGES:
        return None
    if p["time"] is not None and not maint.valid_hhmm(p["time"]):
        return None
    try:
        _dt.date.fromisoformat(p["date"])
    except ValueError:
        return None
    return dict(p)


def clean_window(w):
    """A cached raw window re-validated (a corrupt cache never reaches the UI)."""
    if not isinstance(w, dict):
        return None
    s, e, t = _clean_point(w.get("starts")), _clean_point(w.get("ends")), w.get("ends_text")
    if s is None or e is None or not isinstance(t, str) or len(t) > 120:
        return None
    if _key(e, True) < _key(s, False):
        return None
    return {"starts": s, "ends": e, "ends_text": t}


def _clean_maint(m):
    if not (isinstance(m, dict) and set(m) == {"date", "start_utc", "end_utc"}):
        return None
    return dict(m) if maint.clean_notices({"notices": [dict(m, source="")]}) else None


def _clean_hot(h):
    if not (isinstance(h, dict) and set(h) == {"bonus", "pct", "ends"}):
        return None
    b, p, e = h["bonus"], h["pct"], h["ends"]
    if not (isinstance(b, str) and 0 < len(b) <= MAX_BONUS):
        return None
    if not (isinstance(p, int) and not isinstance(p, bool) and HOT_PCT[0] <= p <= HOT_PCT[1]):
        return None
    ce = _clean_point(e) if e is not None else None
    if e is not None and ce is None:
        return None
    return {"bonus": b, "pct": p, "ends": ce}


def _clean_codes(raw):
    out = []
    for c in raw if isinstance(raw, list) else []:
        if isinstance(c, str) and c == c.upper() and (is_code(c) or is_word_code(c)) \
                and c not in out:
            out.append(c)
    return out[:MAX_CODES]


def _clean_detail(entry):
    """{stamp, fetched_at, window, maint, hot, codes, login, loss, claims, parse_v}
    or None (a pre-064 entry has no maint / hot / codes: None, None, []; a
    pre-074 one no loss / parse_v: [], 1; a pre-086 one no claims: [])."""
    if not isinstance(entry, dict):
        return None
    at = entry.get("fetched_at")
    stamp = entry.get("stamp")
    if not isinstance(at, (int, float)) or isinstance(at, bool):
        return None
    if stamp is not None and not (isinstance(stamp, str) and _DATE_RE.match(stamp)):
        return None
    w = entry.get("window")
    cw = clean_window(w) if w is not None else None
    if w is not None and cw is None:
        return None
    m, h = entry.get("maint"), entry.get("hot")
    cm = _clean_maint(m) if m is not None else None
    ch = _clean_hot(h) if h is not None else None
    if (m is not None and cm is None) or (h is not None and ch is None):
        return None
    pv = entry.get("parse_v")
    pv = pv if isinstance(pv, int) and not isinstance(pv, bool) and pv > 0 else 1
    return {"stamp": stamp, "fetched_at": at, "window": cw, "maint": cm, "hot": ch,
            "codes": _clean_codes(entry.get("codes")),
            "login": logindays.clean_suggestion(entry.get("login")),  # plan 075; pre-075 None
            "loss": maintdigest.clean_loss(entry.get("loss")) if cm is not None else [],
            "claims": claimwindows.clean_claims(entry.get("claims")),  # plan 086
            "parse_v": pv}


def _clean_notice(n):
    if not isinstance(n, dict) or not _is_no(n.get("group_no")):
        return None
    title, stamp = n.get("title"), n.get("stamp")
    if not isinstance(title, str) or not title or _clean_title(title) != title:
        return None
    if stamp is not None and not (isinstance(stamp, str) and _DATE_RE.match(stamp)):
        return None
    board = n.get("board", 3)  # a pre-064 cache holds Events-board rows only
    if board not in BOARDS:
        return None
    return {"group_no": n["group_no"], "title": title, "url": detail_url(n["group_no"]),
            "stamp": stamp, "board": board}


# -- client -----------------------------------------------------------------------

class NoticeClient(CachedClient):
    """robots.txt check + one list GET + at most MAX_DETAILS Detail GETs per
    run, the list cached TTL_S (plan 002 backoff), Detail windows cached in
    DETAILS_FILE, and a persisted attempt floor of one run per MIN_INTERVAL_S."""

    def __init__(self, fetch=None, clock=time.time, cache_dir=None):
        super().__init__(fetch=fetch or default_fetch, clock=clock,
                         cache_dir=cache_dir if cache_dir is not None else _DEFAULT_CACHE)
        self._details_lock = threading.Lock()

    def _attempt_path(self):
        return self.cache_dir / ATTEMPT_FILE

    def attempt(self):
        """{"at", "robots", "ok_at", "fail_since"}: the last attempt, its robots
        verdict, the last good run and the first failure since (plan 064: the
        official host unreachable); corrupt = nothing."""
        doc = read_json(self._attempt_path())
        doc = doc if isinstance(doc, dict) else {}

        def num(k):
            v = doc.get(k)
            return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None

        rb = doc.get("robots")
        return {"at": num("at"), "robots": rb if rb in ROBOTS else None,
                "ok_at": num("ok_at"), "fail_since": num("fail_since")}

    def _record(self, at, robots, **health):
        doc = self.attempt()
        doc.update(at=at, robots=robots, **health)
        atomic_write_json(self._attempt_path(), doc)

    def failing_since(self):
        """Epoch of the first failed run since the last good one, or None."""
        return self.attempt()["fail_since"]

    def steam_hint(self):
        """{at, robots, titles} of the last Steam RSS read, or None."""
        doc = read_json(self.cache_dir / STEAM_FILE)
        if not isinstance(doc, dict) or doc.get("robots") not in ROBOTS:
            return None
        at, titles = doc.get("at"), doc.get("titles")
        if not isinstance(at, (int, float)) or isinstance(at, bool):
            return None
        titles = [t for t in (titles if isinstance(titles, list) else [])
                  if isinstance(t, str) and t and _clean_title(t) == t][:MAX_HINTS]
        return {"at": at, "robots": doc["robots"], "titles": titles}

    def _steam(self, now):
        """Backup read: Steam store news RSS titles, robots-gated on that host."""
        verdict = robots_verdict(self.fetch, urls=(STEAM_RSS,), robots_url=STEAM_ROBOTS)
        titles = []
        if verdict == "allow":
            try:
                titles = parse_rss_titles(_decode(self.fetch(STEAM_RSS, TIMEOUT_S)))
            except Exception:  # noqa: BLE001 - the backup failing leaves no hint
                titles = []
        atomic_write_json(self.cache_dir / STEAM_FILE,
                          {"at": now, "robots": verdict, "titles": titles})

    def _failed(self, now, robots):
        since = self.attempt()["fail_since"]
        since = now if since is None else since
        self._record(now, robots, fail_since=since)
        if now - since >= STEAM_AFTER_S:
            self._steam(now)

    def details(self):
        """{group_no: {stamp, fetched_at, window}} of valid cached Detail reads."""
        with self._details_lock:
            doc = read_json(self.cache_dir / DETAILS_FILE)
        out = {}
        for k, v in (doc.items() if isinstance(doc, dict) else ()):
            c = _clean_detail(v)
            if c is not None and isinstance(k, str) and re.fullmatch(r"[0-9]{1,9}", k):
                out[int(k)] = c
        return out

    def _stale(self, notice, cached, now):
        if cached is None:
            return True
        if cached["parse_v"] < maintdigest.PARSE_V:
            return True  # plan 074 / 086: read before loss / claim parsing, once
        if notice["stamp"] is not None:
            return notice["stamp"] != cached["stamp"]
        return now - cached["fetched_at"] >= DETAIL_MAX_AGE_S

    def _lists(self):
        """One GET per board (BOARDS order) -> notices tagged with their board,
        one per group_no. Any failed board fails the run (UpstreamError), so
        the last good list stays in force (plan 002 stale) rather than a
        partial one dropping that board's cached Detail reads."""
        notices, seen = [], set()
        for b in BOARDS:
            try:
                raw = self.fetch(LIST_URLS[b], TIMEOUT_S)
            except Exception as e:  # noqa: BLE001 - HTTPError, URLError, timeout
                raise UpstreamError(f"{type(e).__name__}: {e}"[:200]) from e
            for n in parse_list(_decode(raw)):
                if n["group_no"] not in seen:
                    seen.add(n["group_no"])
                    notices.append(dict(n, board=b))
        return notices

    @staticmethod
    def _fetch_order(notices):
        """Maintenance, then Hot Time titles first (time-critical), else list order."""
        def rank(n):
            if _MAINT_TITLE_RE.match(n["title"]):
                return 0
            return 1 if _HOT_RE.search(n["title"]) else 2
        return sorted(notices, key=rank)

    def _download(self):
        now = self.clock()
        self._record(now, None)  # the floor holds even if this attempt dies midway
        verdict = robots_verdict(self.fetch, urls=ROBOT_URLS)
        self._record(now, verdict)
        if verdict != "allow":
            if verdict == "unreachable":
                self._failed(now, verdict)
            else:  # a disallow is a reachable host: no Steam hint
                self._record(now, verdict, fail_since=None)
            raise UpstreamError(f"robots.txt: {verdict}")
        try:
            notices = self._lists()
        except UpstreamError:
            self._failed(now, verdict)
            raise
        self._record(now, verdict, ok_at=now, fail_since=None)
        cache = self.details()
        today = _dt.datetime.fromtimestamp(now, _dt.timezone.utc)
        budget = MAX_DETAILS
        for n in self._fetch_order(notices):
            if budget <= 0:
                break
            if not self._stale(n, cache.get(n["group_no"]), now):
                continue
            budget -= 1
            try:
                page = self.fetch(n["url"], TIMEOUT_S)
            except Exception:  # noqa: BLE001 - one bad Detail page skips that notice only
                continue
            cache[n["group_no"]] = dict(parse_notice(_decode(page), n["title"], today.year,
                                                     today.month),
                                        stamp=n["stamp"], fetched_at=now)
        listed = {n["group_no"] for n in notices}
        with self._details_lock:  # notices gone from the board are forgotten
            atomic_write_json(self.cache_dir / DETAILS_FILE,
                              {str(k): v for k, v in cache.items() if k in listed})
        return {"notices": notices}

    def due(self):
        at = self.attempt()["at"]
        return at is None or self.clock() - at >= MIN_INTERVAL_S

    def refresh(self, spawn=None):
        """Background refresh when due; never blocks the caller."""
        if not self.due():
            return False
        return self.refresh_async(KEY, TTL_S, self._download, spawn)

    def peek(self):
        return super().peek(KEY, TTL_S)

    def is_pending(self):
        return self.pending(KEY)


# -- service ----------------------------------------------------------------------

def _dismissed_list(doc):
    raw = doc.get("dismissed") if isinstance(doc, dict) else None
    out = []
    for v in raw if isinstance(raw, list) else []:
        if _is_no(v) and v not in out:
            out.append(v)
    return out[-MAX_DISMISSED:]


def _clean_auto(a):
    """One auto-add ledger row {group_no, title, url, events: [id], hot: [id], at}."""
    if not isinstance(a, dict) or not _is_no(a.get("group_no")):
        return None
    ev, hot = a.get("events"), a.get("hot")
    if not (isinstance(ev, list) and all(isinstance(i, str) and re.fullmatch(r"e[0-9]{1,9}", i)
                                         for i in ev)):
        return None
    if not (isinstance(hot, list) and all(isinstance(i, str) and re.fullmatch(r"a[0-9]{1,9}", i)
                                          for i in hot)):
        return None
    title, at = a.get("title"), a.get("at")
    if not isinstance(title, str) or not isinstance(at, str):
        return None
    return {"group_no": a["group_no"], "title": title, "url": detail_url(a["group_no"]),
            "events": list(ev), "hot": list(hot), "at": at}


class NoticeService:
    """`suggested_events` block of GET /api/events. Rows already in the Events
    store (same url, or same title + ends), dismissed group numbers and windows
    already over are skipped. Plan 064: every read also imports what the
    cached Detail reads hold - maintenance notices always (store domain
    MAINT_DOMAIN), and with `auto_add()` true each Events-board window, coupon
    code and Hot Time window whose both ends resolve, once per groupContentNo
    (ledger "auto" in DOMAIN; `undo` removes them and dismisses the notice)."""

    def __init__(self, client, events_service, store, maint_start=None, spawn=None,
                 slot_path=maint.DATA, leveling=None, auto_add=None):
        self.client = client
        self.events = events_service
        self.store = store
        self.maint_start = maint_start or (lambda: "")
        self.spawn = spawn
        self.slot_path = slot_path
        self.leveling = leveling  # plan 011 service for Hot Time auto windows (None = skip)
        self.auto_add = auto_add or (lambda: False)
        self._lock = threading.RLock()

    def _slot(self):
        try:
            override = self.maint_start()
        except Exception:  # noqa: BLE001 - an unreadable setting = the data file slot
            override = ""
        return maint.slot(override, path=self.slot_path)

    def _status(self, res, robots):
        if self.client is None:
            return "none"
        if robots in ("disallow", "unreachable"):
            return "off"
        if res is None or res["data"] is None:
            return "error" if res is not None and res["error"] and not self.client.is_pending() \
                else "pending"
        return "stale" if res["stale"] else "ok"

    def _doc(self):
        doc = self.store.get(DOMAIN)
        raw = doc.get("auto") if isinstance(doc, dict) else None
        auto, seen = [], set()
        for a in (_clean_auto(x) for x in (raw if isinstance(raw, list) else [])):
            if a is not None and a["group_no"] not in seen:
                seen.add(a["group_no"])
                auto.append(a)
        return {"dismissed": _dismissed_list(doc), "auto": auto[-MAX_AUTO:]}

    def dismissed(self):
        return self._doc()["dismissed"]

    def dismiss(self, group_no):
        """Remember a dismissed notice; returns the refreshed view (never fetches)."""
        if not _is_no(group_no):
            raise ValueError("dismiss_notice must be a notice number (groupContentNo)")
        with self._lock:
            doc = self._doc()
            d = [n for n in doc["dismissed"] if n != group_no] + [group_no]
            self.store.put(DOMAIN, dict(doc, dismissed=d[-MAX_DISMISSED:]))
        return self.view(refresh=False)

    def undo(self, group_no):
        """Plan 064: remove what a notice auto-added (events items, Hot Time
        windows) and dismiss it, so it is never re-added; returns the view."""
        if not _is_no(group_no):
            raise ValueError("undo_notice must be a notice number (groupContentNo)")
        with self._lock:
            doc = self._doc()
            row = next((a for a in doc["auto"] if a["group_no"] == group_no), None)
            if row is None:
                raise ValueError(f"notice {group_no} was not auto-added")
            for iid in row["events"]:
                try:
                    self.events.delete(iid)
                except ValueError:
                    pass  # already deleted by hand
            if self.leveling is not None:
                self.leveling.hot_auto_del(row["hot"])
            d = [n for n in doc["dismissed"] if n != group_no] + [group_no]
            self.store.put(DOMAIN, {"dismissed": d[-MAX_DISMISSED:],
                                    "auto": [a for a in doc["auto"] if a["group_no"] != group_no]})
        return self.view(refresh=False)

    def maint_notices(self):
        """{date: {start_utc, end_utc, source}} of imported maintenance notices."""
        return maint.clean_notices(self.store.get(MAINT_DOMAIN))

    def loss_rows(self):
        """Plan 074: [{notice_no, url, due_utc, loss}] of cached maintenance
        notices with loss sentences (cache only, never fetches)."""
        if self.client is None:
            return []
        return [{"notice_no": no, "url": detail_url(no), "due_utc": d["maint"]["start_utc"],
                 "loss": list(d["loss"])}
                for no, d in sorted(self.client.details().items())
                if d["maint"] is not None and d["loss"]]

    def _notices(self, res):
        raw = res["data"].get("notices") if isinstance(res["data"], dict) else None
        return [c for c in (_clean_notice(n) for n in (raw if isinstance(raw, list) else []))
                if c is not None]

    def _import_maint(self, notices, details):
        have = self.maint_notices()
        merged = dict(have)
        for n in notices:
            m = (details.get(n["group_no"]) or {}).get("maint")
            if m is not None:
                merged[m["date"]] = {"start_utc": m["start_utc"], "end_utc": m["end_utc"],
                                     "source": n["url"]}
        merged = dict(sorted(merged.items())[-MAX_MAINT:])  # newest MAX_MAINT dates
        if merged != have:
            self.store.put(MAINT_DOMAIN, {"notices": [dict(v, date=k)
                                                      for k, v in merged.items()]})
            merged = self.maint_notices()
        return merged

    def _hot_row(self, n, d, slot, mn, now):
        """Hot Time auto window of one notice, or None (no bonus, or a partial
        window: the start comes from the window line, else the list's stamp;
        the end from the window line, else the "Ends" line)."""
        h = d["hot"]
        if h is None or self.leveling is None:
            return None
        w = d["window"]
        s = w["starts"] if w else (None if n["stamp"] is None
                                   else {"date": n["stamp"], "edge": None, "time": None})
        e = w["ends"] if w else h["ends"]
        if s is None or e is None:
            return None
        try:
            st, en = _point_utc(s, slot, False, mn), _point_utc(e, slot, True, mn)
        except (ValueError, OverflowError):
            return None
        if en <= now or st >= en:
            return None
        return {"start": _iso(st), "end": _iso(en), "label": "Hot Time", "bonus": h["bonus"],
                "pct": h["pct"], "source": n["url"], "group_no": n["group_no"]}

    def _auto_one(self, n, d, slot, mn, now, items):
        """Add what one notice holds with a full window -> (event ids, hot ids)."""
        ev_ids, hot_ids = [], []
        hot = self._hot_row(n, d, slot, mn, now)
        if hot is not None:
            wid = self.leveling.hot_auto_add(hot)
            if wid is not None:
                hot_ids.append(wid)
        r = None
        if d["window"] is not None:
            try:
                r = resolve_window(d["window"], slot, mn)
            except (ValueError, OverflowError):
                r = None
        if r is None or _parse_iso(r["ends"]) <= now:
            return ev_ids, hot_ids
        codes = {i["code"] for i in items if i.get("code")}
        adds = [{"kind": "coupon", "title": n["title"], "code": c, "starts": r["starts"],
                 "ends": r["ends"], "url": n["url"]} for c in d["codes"] if c not in codes]
        known = {(i["title"], _parse_iso(i["ends"])) for i in items if i.get("ends")}
        if (not d["codes"] and n["board"] == 3 and n["url"] not in {i.get("url") for i in items}
                and (n["title"], _parse_iso(r["ends"])) not in known):
            adds.append({"kind": "event", "title": n["title"], "starts": r["starts"],
                         "ends": r["ends"], "url": n["url"]})
        for a in adds:
            try:
                ev_ids.append(self.events.add(a, auto=True)["item"]["id"])
            except ValueError:
                continue  # full store, a code added meanwhile: skip that one
        return ev_ids, hot_ids

    @staticmethod
    def claim_windows(notices, details, slot, mn):
        """Plan 086: {notice url: (claim_until UTC datetime, text) | None} - the
        latest resolvable claim deadline of each listed notice's cached Detail
        read; None when that read holds none (a stored window is cleared)."""
        out = {}
        for n in notices:
            d = details.get(n["group_no"])
            if d is None:
                continue  # not read yet: leave what is stored
            out[n["url"]] = claimwindows.latest(d["claims"],
                                                lambda pt: _point_utc(pt, slot, True, mn))
        return out

    def claimed(self, group_no):
        """Plan 086 ack: the operator claimed a notice's rewards (Mail / Safe);
        its claim window is hidden everywhere. Returns the events view."""
        if not _is_no(group_no):
            raise ValueError("claimed must be a notice number (groupContentNo)")
        return self.events.claim_ack(detail_url(group_no))

    def _import(self, notices, details, slot, now):
        """Maintenance notices always; the rest only with auto_add() true.
        Plan 086: claim windows sync onto the stored items of their notice
        always (they decorate what is there, they add nothing)."""
        mn = self._import_maint(notices, details)
        self._auto_import(notices, details, slot, mn, now)
        self.events.sync_claims({url: None if c is None else (_iso(c[0]), c[1]) for url, c in
                                 self.claim_windows(notices, details, slot, mn).items()})
        return mn

    def _auto_import(self, notices, details, slot, mn, now):
        try:
            on = self.auto_add() is True
        except Exception:  # noqa: BLE001 - an unreadable setting = off
            on = False
        if not on:
            return
        doc = self._doc()
        done = set(doc["dismissed"]) | {a["group_no"] for a in doc["auto"]}
        new = []
        for n in notices:
            d = details.get(n["group_no"])
            if d is None or n["group_no"] in done or _MAINT_TITLE_RE.match(n["title"]):
                continue
            ev_ids, hot_ids = self._auto_one(n, d, slot, mn, now, self.events.view()["items"])
            if ev_ids or hot_ids:
                new.append({"group_no": n["group_no"], "title": n["title"], "url": n["url"],
                            "events": ev_ids, "hot": hot_ids, "at": _iso(now)})
        if new:
            self.store.put(DOMAIN, {"dismissed": doc["dismissed"],
                                    "auto": (doc["auto"] + new)[-MAX_AUTO:]})

    def login_suggestions(self):
        """Plan 075: {notice url: suggested login rule} from cached Detail reads
        only (never fetches); dismissed notices skipped."""
        if self.client is None:
            return {}
        dismissed = set(self.dismissed())
        return {detail_url(no): d["login"] for no, d in self.client.details().items()
                if d.get("login") is not None and no not in dismissed}

    def coupon_extra(self):
        """Plan 064: codes read from notices, as plan 014 coupon candidates
        {code, title, url, date}; dismissed notices skipped. Codes already in
        the Events store are filtered by the coupon service."""
        if self.client is None:
            return []
        res = self.client.peek()
        if res is None or res["data"] is None:
            return []
        details = self.client.details()
        dismissed = set(self.dismissed())
        out = []
        for n in self._notices(res):
            d = details.get(n["group_no"])
            if d is None or n["group_no"] in dismissed:
                continue
            out.extend({"code": c, "title": n["title"], "url": n["url"], "date": n["stamp"]}
                       for c in d["codes"])
        return out

    def _rows(self, notices, details, slot, mn):
        items = self.events.view()["items"]
        urls = {i["url"] for i in items if i.get("url")}
        known = {(i["title"], _parse_iso(i["ends"])) for i in items if i.get("ends")}
        doc = self._doc()
        dismissed = set(doc["dismissed"]) | {a["group_no"] for a in doc["auto"]}
        now = _dt.datetime.fromtimestamp(self.client.clock(), _dt.timezone.utc)
        cands, no_dates, unread = [], [], 0
        for n in notices:
            if n["board"] != 3 or _MAINT_TITLE_RE.match(n["title"]):
                continue  # plan 064 boards feed maintenance / Hot Time / codes only
            d = details.get(n["group_no"])
            if d is None:
                unread += 1
                continue
            if n["group_no"] in dismissed:
                continue
            if d["window"] is None:
                no_dates.append({"title": n["title"], "url": n["url"], "group_no": n["group_no"]})
                continue
            try:
                r = resolve_window(d["window"], slot, mn)
            except (ValueError, OverflowError):
                continue
            ends = _parse_iso(r["ends"])
            if n["url"] in urls or (n["title"], ends) in known or ends <= now:
                continue
            cands.append(dict({"title": n["title"], "url": n["url"], "group_no": n["group_no"]},
                              starts=r["starts"], ends=r["ends"], ends_text=r["ends_text"],
                              kind="event", maint_relative=r["maint_relative"],
                              ends_edge=r["ends_edge"]))
        return cands, no_dates, unread

    def _steam_view(self, now):
        """Plan 064 backup hint, only while the official host has failed for
        STEAM_AFTER_S: {titles, robots, url} - titles, never windows."""
        since = self.client.failing_since()
        if since is None or now - since < STEAM_AFTER_S:
            return None
        hint = self.client.steam_hint()
        if hint is None or hint["at"] < since:
            return None
        return {"titles": hint["titles"], "robots": hint["robots"],
                "url": "https://store.steampowered.com/news/app/582660"}

    def _auto_view(self):
        """Ledger rows newest first (the dashboard's undo list)."""
        return [{"group_no": a["group_no"], "title": a["title"], "url": a["url"],
                 "events": len(a["events"]), "hot": len(a["hot"]), "at": a["at"]}
                for a in reversed(self._doc()["auto"][-20:])]

    def view(self, refresh=False):
        slot = self._slot()
        maint_view = {k: slot[k] for k in ("weekday", "start_utc", "duration_min", "verified",
                                           "overridden")}
        try:
            auto_on = self.auto_add() is True
        except Exception:  # noqa: BLE001 - an unreadable setting = off
            auto_on = False
        base = {"source": LIST_URL, "maintenance": maint_view, "dismissed": self.dismissed(),
                "candidates": [], "no_dates": [], "unread": 0, "auto_add": auto_on,
                "auto": [], "maint_notices": [], "steam_hint": None}
        if self.client is None:
            return dict(base, status="none", robots=None, error=None, freshness=None)
        if refresh:
            self.client.refresh(self.spawn)
        res = self.client.peek()
        robots = self.client.attempt()["robots"]
        status = self._status(res, robots)
        out = dict(base, status=status, robots=robots, error=res["error"] if res else None,
                   freshness=freshness(res) if res else None,
                   steam_hint=self._steam_view(self.client.clock()))
        mn = self.maint_notices()
        if status not in ("off", "none") and res is not None and res["data"] is not None:
            notices, details = self._notices(res), self.client.details()
            now = _dt.datetime.fromtimestamp(self.client.clock(), _dt.timezone.utc)
            with self._lock:
                mn = self._import(notices, details, slot, now)
                out["candidates"], out["no_dates"], out["unread"] = self._rows(
                    notices, details, slot, mn)
            out["dismissed"] = self.dismissed()
        out["auto"] = self._auto_view()
        out["maint_notices"] = [dict(v, date=k) for k, v in sorted(mn.items())][-5:]
        return out
