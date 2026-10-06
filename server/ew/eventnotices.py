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
"""

import datetime as _dt
import html.parser
import re
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from . import __version__, maint
from .coupons import HOST, MAX_BYTES, ROBOTS, TIMEOUT_S, UA_TOKEN, robots_verdict
from .coupons import _clean_title, parse_date
from .httpcache import CachedClient, UpstreamError, freshness, read_json
from .store import atomic_write_json
from .today import _iso, _parse_iso

LIST_URL = f"https://{HOST}/en-US/News/Notice?boardType=3"  # = events.SOURCES[1]
DETAIL_URL = f"https://{HOST}/en-US/News/Detail?groupContentNo="
DETAIL_PATH = "/en-US/News/Detail"
ROBOT_URLS = (LIST_URL, DETAIL_URL + "1")
USER_AGENT = (f"{UA_TOKEN}/{__version__} (BDO companion; event notice check, "
              "1 list + at most 5 notice GETs per 6 h)")
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
    """Visible text, one line per block element; head/script/style dropped."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._skip = 0

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
            self.parts.append(data)


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


def parse_detail(page, ref_year, ref_month=None):
    """The first window line of a Detail page, or None (no parseable window)."""
    p = _TextParser()
    try:
        p.feed(page if isinstance(page, str) else _decode(page))
        p.close()
    except Exception:  # noqa: BLE001 - malformed markup degrades to what was parsed
        pass
    for line in "".join(p.parts).split("\n"):
        line = _WS_RE.sub(" ", line).strip()
        if line:
            w = parse_window(line, ref_year, ref_month)
            if w is not None:
                return w
    return None


# -- resolution -------------------------------------------------------------------

def _point_utc(pt, slot, end):
    d = _dt.date.fromisoformat(pt["date"])
    if pt["time"] is not None:
        hh, mm = (int(x) for x in pt["time"].split(":"))
        return _dt.datetime.combine(d, _dt.time(hh, mm), _dt.timezone.utc)
    if pt["edge"] is not None:
        return maint.resolve(d, pt["edge"], slot)
    t = _dt.time(23, 59, 59) if end else _dt.time(0, 0, 0)
    return _dt.datetime.combine(d, t, _dt.timezone.utc)


def resolve_window(w, slot):
    """Raw window -> {starts, ends (ISO UTC), ends_text, maint_relative, ends_edge}.
    An explicit HH:MM UTC wins; else before/after maintenance via `maint`."""
    s, e = w["starts"], w["ends"]
    return {"starts": _iso(_point_utc(s, slot, False)), "ends": _iso(_point_utc(e, slot, True)),
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


def _clean_detail(entry):
    """{stamp, fetched_at, window} or None."""
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
    return {"stamp": stamp, "fetched_at": at, "window": cw}


def _clean_notice(n):
    if not isinstance(n, dict) or not _is_no(n.get("group_no")):
        return None
    title, stamp = n.get("title"), n.get("stamp")
    if not isinstance(title, str) or not title or _clean_title(title) != title:
        return None
    if stamp is not None and not (isinstance(stamp, str) and _DATE_RE.match(stamp)):
        return None
    return {"group_no": n["group_no"], "title": title, "url": detail_url(n["group_no"]),
            "stamp": stamp}


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
        """{"at": epoch or None, "robots": verdict or None}; corrupt = nothing."""
        doc = read_json(self._attempt_path())
        doc = doc if isinstance(doc, dict) else {}
        at = doc.get("at")
        ok_at = isinstance(at, (int, float)) and not isinstance(at, bool)
        rb = doc.get("robots")
        return {"at": at if ok_at else None, "robots": rb if rb in ROBOTS else None}

    def _record(self, at, robots):
        atomic_write_json(self._attempt_path(), {"at": at, "robots": robots})

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
        if notice["stamp"] is not None:
            return notice["stamp"] != cached["stamp"]
        return now - cached["fetched_at"] >= DETAIL_MAX_AGE_S

    def _download(self):
        now = self.clock()
        self._record(now, None)  # the floor holds even if this attempt dies midway
        verdict = robots_verdict(self.fetch, urls=ROBOT_URLS)
        self._record(now, verdict)
        if verdict != "allow":
            raise UpstreamError(f"robots.txt: {verdict}")
        try:
            raw = self.fetch(LIST_URL, TIMEOUT_S)
        except Exception as e:  # noqa: BLE001 - HTTPError, URLError, timeout
            raise UpstreamError(f"{type(e).__name__}: {e}"[:200]) from e
        notices = parse_list(_decode(raw))
        cache = self.details()
        today = _dt.datetime.fromtimestamp(now, _dt.timezone.utc)
        budget = MAX_DETAILS
        for n in notices:
            if budget <= 0:
                break
            if not self._stale(n, cache.get(n["group_no"]), now):
                continue
            budget -= 1
            try:
                page = self.fetch(n["url"], TIMEOUT_S)
            except Exception:  # noqa: BLE001 - one bad Detail page skips that notice only
                continue
            cache[n["group_no"]] = {"stamp": n["stamp"], "fetched_at": now,
                                    "window": parse_detail(_decode(page), today.year,
                                                           today.month)}
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


class NoticeService:
    """`suggested_events` block of GET /api/events. Rows already in the Events
    store (same url, or same title + ends), dismissed group numbers and windows
    already over are skipped; nothing is ever added here."""

    def __init__(self, client, events_service, store, maint_start=None, spawn=None,
                 slot_path=maint.DATA):
        self.client = client
        self.events = events_service
        self.store = store
        self.maint_start = maint_start or (lambda: "")
        self.spawn = spawn
        self.slot_path = slot_path
        self._lock = threading.Lock()

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

    def dismissed(self):
        return _dismissed_list(self.store.get(DOMAIN))

    def dismiss(self, group_no):
        """Remember a dismissed notice; returns the refreshed view (never fetches)."""
        if not _is_no(group_no):
            raise ValueError("dismiss_notice must be a notice number (groupContentNo)")
        with self._lock:
            d = [n for n in self.dismissed() if n != group_no] + [group_no]
            self.store.put(DOMAIN, {"dismissed": d[-MAX_DISMISSED:]})
        return self.view(refresh=False)

    def _rows(self, res, slot):
        raw = res["data"].get("notices") if isinstance(res["data"], dict) else None
        notices = [c for c in (_clean_notice(n) for n in (raw if isinstance(raw, list) else []))
                   if c is not None]
        details = self.client.details()
        items = self.events.view()["items"]
        urls = {i["url"] for i in items if i.get("url")}
        known = {(i["title"], _parse_iso(i["ends"])) for i in items if i.get("ends")}
        dismissed = set(self.dismissed())
        now = _dt.datetime.fromtimestamp(self.client.clock(), _dt.timezone.utc)
        cands, no_dates, unread = [], [], 0
        for n in notices:
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
                r = resolve_window(d["window"], slot)
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

    def view(self, refresh=False):
        slot = self._slot()
        maint_view = {k: slot[k] for k in ("weekday", "start_utc", "duration_min", "verified",
                                           "overridden")}
        base = {"source": LIST_URL, "maintenance": maint_view, "dismissed": self.dismissed(),
                "candidates": [], "no_dates": [], "unread": 0}
        if self.client is None:
            return dict(base, status="none", robots=None, error=None, freshness=None)
        if refresh:
            self.client.refresh(self.spawn)
        res = self.client.peek()
        robots = self.client.attempt()["robots"]
        status = self._status(res, robots)
        out = dict(base, status=status, robots=robots, error=res["error"] if res else None,
                   freshness=freshness(res) if res else None)
        if status not in ("off", "none") and res is not None and res["data"] is not None:
            out["candidates"], out["no_dates"], out["unread"] = self._rows(res, slot)
        return out
