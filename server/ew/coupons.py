"""Coupon suggestions (plan 014) from the official NA/EU news list page.

Reverses plan 006 "no scraping" only under the plan 014 conditions: one
unauthenticated GET of NEWS_URL at most every MIN_INTERVAL_S, a descriptive
User-Agent, and only while the publisher's robots.txt allows that path for both
a generic agent and ours (checked with urllib.robotparser on every attempt;
disallow or unreachable = feature off). Never logged in, never redeems, never
adds: candidates are suggestions the operator adds with one click.

Requests never wait on upstream: they read the cache (`peek`); GET /api/events
also starts a background refresh, at most one in flight, and at most one
attempt (robots + page) per MIN_INTERVAL_S even after a failure.
"""

import datetime as _dt
import html.parser
import re
import time
import urllib.parse
import urllib.request
import urllib.robotparser
from pathlib import Path

from . import __version__
from .events import CODE_RE, MAX_TITLE, MAX_URL
from .httpcache import CachedClient, UpstreamError, freshness, read_json
from .store import atomic_write_json

HOST = "www.naeu.playblackdesert.com"
NEWS_URL = f"https://{HOST}/en-US/News"  # = events.SOURCES[0]
ROBOTS_URL = f"https://{HOST}/robots.txt"
UA_TOKEN = "Ebonwake"
USER_AGENT = f"{UA_TOKEN}/{__version__} (BDO companion; coupon check, 1 GET per 6 h)"
MIN_INTERVAL_S = 6 * 3600
TTL_S = MIN_INTERVAL_S
TIMEOUT_S = 15
MAX_BYTES = 2 * 1024 * 1024
MAX_CANDIDATES = 20
KEY = "coupons_news"
ATTEMPT_FILE = "coupons_attempt.json"
ROBOTS = ("allow", "disallow", "unreachable")
_DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "ops" / "runtime" / "cache" / "coupons"

_UA_LINE_RE = re.compile(r"^\s*user-agent\s*:\s*\S", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[A-Za-z0-9-]+")
_WS_RE = re.compile(r"\s+")
_DATE_PATTERNS = (
    (re.compile(r"\b([0-9]{4})[-.]([0-9]{2})[-.]([0-9]{2})\b"), ("y", "m", "d")),
    (re.compile(r"\b([0-9]{2})/([0-9]{2})/([0-9]{4})\b"), ("m", "d", "y")),
)
_MONTH_RE = re.compile(r"\b([A-Za-z]{3,9})\.? ([0-9]{1,2}), ([0-9]{4})\b")
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


# -- robots gate ----------------------------------------------------------------

def robots_verdict(fetch, urls=(NEWS_URL,), robots_url=ROBOTS_URL):
    """"allow" when robots.txt lets both `*` and our agent GET every one of
    `urls` (plan 059 passes its list + Detail pages); "disallow" when it does
    not; "unreachable" when it cannot be read or holds no User-agent group (= off).
    `robots_url` is the policy of the host of `urls` (plan 064: the Steam store;
    plan 061: the profile source origin)."""
    try:
        text = _decode(fetch(robots_url, TIMEOUT_S))
    except Exception:  # noqa: BLE001 - any failure to read the policy = feature off
        return "unreachable"
    lines = text.splitlines()
    # No explicit policy (empty, garbage, an HTML soft-404) = no policy read = off.
    if not any(_UA_LINE_RE.match(ln) for ln in lines):
        return "unreachable"
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(lines)
    ok = all(rp.can_fetch("*", u) and rp.can_fetch(USER_AGENT, u) for u in urls)
    return "allow" if ok else "disallow"


# -- extractor ------------------------------------------------------------------

def is_code(tok):
    """Plan 006 rule `^[A-Za-z0-9-]{4,40}$`, tightened so prose is not a code:
    no lowercase, at least one letter, no edge hyphen, and a digit or two hyphens."""
    if not isinstance(tok, str) or not CODE_RE.match(tok) or tok[0] == "-" or tok[-1] == "-":
        return False
    if tok != tok.upper() or not any(ch.isalpha() for ch in tok):
        return False
    return any(ch.isdigit() for ch in tok) or tok.count("-") >= 2


_WORD_CODE_RE = re.compile(r"^[A-Za-z0-9]{8,24}$")
COPY_CLASS = "js-btncopycoupon"  # the official page's copy button (lower-cased)


def is_word_code(tok):
    """Plan 064 word-style code (letters and digits, 8-24 chars, at least one
    letter). Only trusted beside the official copy button (`copy_codes`)."""
    return (isinstance(tok, str) and bool(_WORD_CODE_RE.match(tok))
            and any(ch.isalpha() for ch in tok))


class _CopyCodes(html.parser.HTMLParser):
    """The text of the element just before each copy-coupon button."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.codes = []
        self._last = ""  # text of the most recent closed / open leaf element
        self._buf = None

    def handle_starttag(self, tag, attrs):
        cls = (dict(attrs).get("class") or "").lower().split()
        if COPY_CLASS in cls:
            self.codes.append(self._last.strip())
            self._buf = None
            return
        self._buf = []

    def handle_endtag(self, tag):
        if self._buf is not None and "".join(self._buf).strip():
            self._last = "".join(self._buf)
        self._buf = None

    def handle_data(self, data):
        if self._buf is not None:
            self._buf.append(data)
        elif data.strip():
            self._last = data


def copy_codes(page):
    """Codes shown right before a `js-btnCopyCoupon` button, upper-cased, page
    order, one each: a word-style code or plan 014's dashed shape."""
    p = _CopyCodes()
    try:
        p.feed(page if isinstance(page, str) else _decode(page))
        p.close()
    except Exception:  # noqa: BLE001 - malformed markup degrades to what was parsed
        pass
    out = []
    for c in (x.upper() for x in p.codes):
        if (is_word_code(c) or is_code(c)) and c not in out:
            out.append(c)
    return out


def parse_date(text):
    """First date in `text` as YYYY-MM-DD (ISO, dotted, US MM/DD/YYYY or
    "Oct 3, 2026"), or None."""
    for rx, order in _DATE_PATTERNS:
        for m in rx.finditer(text):
            parts = dict(zip(order, (int(g) for g in m.groups())))
            try:
                return _dt.date(parts["y"], parts["m"], parts["d"]).isoformat()
            except ValueError:
                continue
    for m in _MONTH_RE.finditer(text):
        mon = _MONTHS.get(m.group(1)[:3].lower())
        if mon is None:
            continue
        try:
            return _dt.date(int(m.group(3)), mon, int(m.group(2))).isoformat()
        except ValueError:
            continue
    return None


def official_url(href, base=NEWS_URL):
    """Absolute https URL on the official host under /en-US/News/, or None."""
    try:
        url = urllib.parse.urljoin(base, href.strip())
        p = urllib.parse.urlsplit(url)
    except (ValueError, AttributeError):
        return None
    if p.scheme != "https" or p.hostname != HOST or not p.path.startswith("/en-US/News/"):
        return None
    if len(url) > MAX_URL or not url.isascii() or any(ch.isspace() for ch in url):
        return None
    return url


def _clean_title(text):
    t = "".join(ch for ch in _WS_RE.sub(" ", text) if ord(ch) >= 32 and ord(ch) != 127).strip()
    return t[:MAX_TITLE].rstrip()


class _Notices(html.parser.HTMLParser):
    """Collects every <a href=...News/Detail...> with its text and the text of
    any descendant whose class mentions "title". Script/style text is ignored."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.notices = []
        self._cur = None
        self._skip = 0
        self._title_depth = []
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
            return
        a = dict(attrs)
        if tag == "a":
            self._flush()
            href = a.get("href") or ""
            if "/News/Detail" in href:
                self._cur = {"href": href, "text": [], "title": []}
                self._depth = 0
                self._title_depth = []
            return
        if self._cur is not None and tag not in ("br", "img", "hr", "input", "meta"):
            self._depth += 1
            if "title" in (a.get("class") or "").lower().split():
                self._title_depth.append(self._depth)

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
            return
        if tag == "a":
            self._flush()
            return
        if self._cur is not None:
            if self._title_depth and self._title_depth[-1] == self._depth:
                self._title_depth.pop()
            self._depth = max(0, self._depth - 1)

    def handle_data(self, data):
        if self._cur is None or self._skip:
            return
        self._cur["text"].append(data)
        if self._title_depth:
            self._cur["title"].append(data)

    def _flush(self):
        if self._cur is not None:
            self.notices.append({"href": self._cur["href"], "text": " ".join(self._cur["text"]),
                                 "title": " ".join(self._cur["title"])})
        self._cur = None

    def close(self):
        super().close()
        self._flush()


def extract(page):
    """Candidate codes from notices that mention "coupon", newest notice first:
    [{code, title, url, date}], at most MAX_CANDIDATES, one entry per code."""
    p = _Notices()
    try:
        p.feed(page if isinstance(page, str) else _decode(page))
        p.close()
    except Exception:  # noqa: BLE001 - malformed markup degrades to what was parsed
        pass
    found, seen = [], set()
    for n, notice in enumerate(p.notices):
        text = _WS_RE.sub(" ", notice["text"]).strip()
        if "coupon" not in text.lower():
            continue
        url = official_url(notice["href"])
        if url is None:
            continue
        title = _clean_title(notice["title"] or text)
        if not title:
            continue
        date = parse_date(text)
        for tok in _TOKEN_RE.findall(text):
            if is_code(tok) and tok not in seen:
                seen.add(tok)
                found.append((n, {"code": tok, "title": title, "url": url, "date": date}))
    # Newest first; undated notices after dated ones; page order breaks ties.
    found.sort(key=lambda x: (x[1]["date"] is None, _neg_date(x[1]["date"]), x[0]))
    return [c for _, c in found[:MAX_CANDIDATES]]


def _neg_date(d):
    return 0 if d is None else -_dt.date.fromisoformat(d).toordinal()


def clean_candidate(c):
    """A cached candidate re-validated (a corrupt cache never reaches the UI)."""
    if not isinstance(c, dict):
        return None
    code, title, url, date = c.get("code"), c.get("title"), c.get("url"), c.get("date")
    if not (is_code(code) or is_word_code(code) and code == code.upper()) \
            or not isinstance(title, str) or not isinstance(url, str):
        return None
    if _clean_title(title) != title or not title or official_url(url) != url:
        return None
    if date is not None and not (isinstance(date, str) and _DATE_RE.match(date)):
        return None
    return {"code": code, "title": title, "url": url, "date": date}


# -- client -----------------------------------------------------------------------

class CouponClient(CachedClient):
    """robots.txt check + one news-list GET, cached TTL_S, plan 002 backoff, plus
    a persisted attempt floor so a failure never retries before MIN_INTERVAL_S."""

    def __init__(self, fetch=None, clock=time.time, cache_dir=None):
        super().__init__(fetch=fetch or default_fetch, clock=clock,
                         cache_dir=cache_dir if cache_dir is not None else _DEFAULT_CACHE)

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

    def _download(self):
        now = self.clock()
        self._record(now, None)  # the floor holds even if this attempt dies midway
        verdict = robots_verdict(self.fetch)
        self._record(now, verdict)
        if verdict != "allow":
            raise UpstreamError(f"robots.txt: {verdict}")
        try:
            raw = self.fetch(NEWS_URL, TIMEOUT_S)
        except Exception as e:  # noqa: BLE001 - HTTPError, URLError, timeout
            raise UpstreamError(f"{type(e).__name__}: {e}"[:200]) from e
        return {"candidates": extract(_decode(raw))}

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


# -- service --------------------------------------------------------------------

class CouponService:
    """`suggested` block of GET /api/events and `/api/state` sources.coupons.
    Codes already in the Events store are skipped; nothing is ever written."""

    def __init__(self, client, events_service, spawn=None, extra=None):
        self.client = client
        self.events = events_service
        self.spawn = spawn  # background refresh runner (None = daemon thread)
        # Plan 064: codes read from official notices without a full window
        # ({code, title, url, date} rows), suggested beside the list's codes.
        self.extra = extra

    def _status(self, res, robots):
        if self.client is None:
            return "none"
        if robots in ("disallow", "unreachable"):
            return "off"
        if res is None or res["data"] is None:
            return "error" if res is not None and res["error"] and not self.client.is_pending() \
                else "pending"
        return "stale" if res["stale"] else "ok"

    def view(self, refresh=False):
        if self.client is None:
            return {"status": "none", "robots": None, "source": NEWS_URL, "error": None,
                    "freshness": None, "candidates": []}
        if refresh:
            self.client.refresh(self.spawn)
        res = self.client.peek()
        robots = self.client.attempt()["robots"]
        status = self._status(res, robots)
        cands = []
        if status not in ("off", "none") and res is not None and isinstance(res["data"], dict):
            raw = res["data"].get("candidates")
            known = {i["code"] for i in self.events.view()["items"] if i.get("code")}
            for c in (clean_candidate(x) for x in (raw if isinstance(raw, list) else [])):
                if c is not None and c["code"] not in known:
                    cands.append(c)
                    known.add(c["code"])
        if status != "off" and self.extra is not None:
            known = {i["code"] for i in self.events.view()["items"] if i.get("code")}
            known |= {c["code"] for c in cands}
            for c in (clean_candidate(x) for x in self.extra()):
                if c is not None and c["code"] not in known and len(cands) < MAX_CANDIDATES:
                    cands.append(c)
                    known.add(c["code"])
        return {"status": status, "robots": robots, "source": NEWS_URL,
                "error": res["error"] if res else None,
                "freshness": freshness(res) if res else None, "candidates": cands}

    def source(self):
        """`/api/state` sources.coupons: {updated, ttl_s, status, robots}; never fetches."""
        if self.client is None:
            return {"updated": None, "ttl_s": TTL_S, "status": "none", "robots": None}
        res = self.client.peek()
        robots = self.client.attempt()["robots"]
        return {"updated": res["fetched_at"] if res else None, "ttl_s": TTL_S,
                "status": self._status(res, robots), "robots": robots}
