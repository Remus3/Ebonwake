"""World boss schedule drift check (plan 072) against a public NA table.

Plan 031's table (`data/world_bosses_na.json`) is hand-maintained; this reads a
public NA boss table (the data file's `drift` block: URL, robots.txt, table id,
the zone its times are written in, and a name alias map) at most once a day,
only while robots.txt allows the page for both `*` and our agent (plan 014
`robots_verdict`), with one unauthenticated GET. The parsed table is diffed
against the tracked slots; a difference is a banner on the Bosses and System
cards ("schedule differs from <host> on <n> slots - verify"), never an edit of
the tracked file (data changes stay plan-reviewed). Disallowed, unreachable or
unparsable = state "unknown", no banner. Nothing is read from the game.
"""

import datetime as _dt
import html.parser
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

from . import __version__
from .bosses import AT_RE, pt_offset_hours
from .coupons import MAX_BYTES, ROBOTS, TIMEOUT_S, robots_verdict
from .httpcache import CachedClient, UpstreamError, read_json
from .store import atomic_write_json

USER_AGENT = f"Ebonwake/{__version__} (BDO companion; boss schedule check, 1 GET per day)"
MIN_INTERVAL_S = 24 * 3600
TTL_S = MIN_INTERVAL_S
KEY = "bossdrift_na"
ATTEMPT_FILE = "bossdrift_attempt.json"
TZS = ("PT", "UTC")
MAX_MOVE_MIN = 180   # a missing + an extra slot with the same bosses this close = one move
MAX_SLOTS = 200
MAX_NAME = 64
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_FULL_DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_WEEK_MIN = 7 * 1440
_DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "ops" / "runtime" / "cache" / "bossdrift"

_WS_RE = re.compile(r"\s+")
_TIME_RE = re.compile(r"\b([0-9]{1,2}):([0-5][0-9])(?:\s*([AaPp])\.?[Mm]\.?)?")
_SPLIT_RE = re.compile(r"\s*(?:,|&|\+|/)\s*")
_EMPTY = ("", "-", "--")


def default_fetch(url, timeout):
    """Plain unauthenticated GET, size-capped; raises on HTTP error or timeout."""
    req = urllib.request.Request(url, method="GET",
                                 headers={"User-Agent": USER_AGENT,
                                          "Accept": "text/html,text/plain;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (data-file https URLs)
        return r.read(MAX_BYTES)


def _decode(raw):
    return raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)


def _https(url):
    try:
        p = urllib.parse.urlsplit(url)
    except (ValueError, AttributeError):
        return False
    return p.scheme == "https" and bool(p.hostname) and url.isascii()


def config(table):
    """The data file's `drift` block validated, or None (= feature off)."""
    d = table.get("drift") if isinstance(table, dict) else None
    if not isinstance(d, dict):
        return None
    url, robots, tid, tz, aliases = (d.get(k) for k in ("url", "robots", "table_id", "tz",
                                                         "aliases"))
    if not (isinstance(url, str) and _https(url) and isinstance(robots, str) and _https(robots)
            and urllib.parse.urlsplit(url).hostname == urllib.parse.urlsplit(robots).hostname):
        return None
    if not (isinstance(tid, str) and tid) or tz not in TZS or not isinstance(aliases, dict):
        return None
    if not all(isinstance(k, str) and isinstance(v, str) and k and v for k, v in aliases.items()):
        return None
    return {"url": url, "robots": robots, "table_id": tid, "tz": tz,
            "aliases": {_clean(k).lower(): v for k, v in aliases.items()}}


# -- parser ---------------------------------------------------------------------

def _clean(text):
    return _WS_RE.sub(" ", text).strip()


class _Table(html.parser.HTMLParser):
    """Rows of cells of the table with id `table_id`; a cell is a list of text
    segments (any tag inside a cell, <br> included, starts a new segment).
    Script / style text and nested tables are ignored."""

    def __init__(self, table_id):
        super().__init__(convert_charrefs=True)
        self.table_id = table_id
        self.rows = []
        self.found = False
        self._depth = 0      # table nesting depth inside the target (0 = outside)
        self._skip = 0
        self._cell = None

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
            return
        if tag == "table":
            if self._depth:
                self._depth += 1
            elif dict(attrs).get("id") == self.table_id and not self.found:
                self.found = True
                self._depth = 1
            return
        if self._depth != 1:
            return
        if tag == "tr":
            self._end_cell()
            self.rows.append([])
        elif tag in ("td", "th"):
            self._end_cell()
            if not self.rows:
                self.rows.append([])
            self._cell = [""]
            self.rows[-1].append(self._cell)
        elif self._cell is not None:
            self._cell.append("")

    def handle_startendtag(self, tag, attrs):
        if self._depth == 1 and self._cell is not None:
            self._cell.append("")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
            return
        if not self._depth:
            return
        if tag == "table":
            self._depth -= 1
            if not self._depth:
                self._end_cell()
            return
        if self._depth != 1:
            return
        if tag in ("td", "th", "tr"):
            self._end_cell()
        elif self._cell is not None:
            self._cell.append("")

    def handle_data(self, data):
        if self._depth == 1 and not self._skip and self._cell is not None:
            self._cell[-1] += data

    def _end_cell(self):
        self._cell = None


def _time(text):
    m = _TIME_RE.search(text)
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2)), m.group(3)
    if ap:
        if not 1 <= h <= 12:
            return None
        h = h % 12 + (12 if ap in "Pp" else 0)
    if not 0 <= h <= 23:
        return None
    return f"{h:02d}:{mi:02d}"


def _weekday(text):
    t = _clean(text).lower().rstrip(".")
    if len(t) < 2:
        return None
    for i, full in enumerate(_FULL_DAYS):
        if full.startswith(t):
            return i
    return None


def _segments(cell):
    return [s for s in (_clean(x) for x in cell) if s not in _EMPTY]


def parse_table(page, table_id):
    """[{weekday, at, bosses}] as written on the page (raw names, page zone);
    raises ValueError when the table, its time header or every slot is missing."""
    p = _Table(table_id)
    try:
        p.feed(page if isinstance(page, str) else _decode(page))
        p.close()
    except Exception as e:  # noqa: BLE001 - malformed markup = parse failure
        raise ValueError(f"table unreadable: {type(e).__name__}") from e
    if not p.found:
        raise ValueError(f"table #{table_id} not found")
    cols, start = None, 0
    for i, row in enumerate(p.rows):
        hit = {j: _time(" ".join(c)) for j, c in enumerate(row)}
        hit = {j: t for j, t in hit.items() if t is not None and j > 0}
        if hit and _weekday(" ".join(row[0]) if row else "") is None:
            cols, start = hit, i + 1
            break
    if not cols:
        raise ValueError("no time header row")
    grid = {}
    for row in p.rows[start:]:
        if not row:
            continue
        wd = _weekday(" ".join(_segments(row[0])))
        if wd is None:
            continue
        for j, at in cols.items():
            names = _segments(row[j]) if j < len(row) else []
            if names:
                grid.setdefault((wd, at), []).extend(names)
    if not grid:
        raise ValueError("no slots in table")
    if len(grid) > MAX_SLOTS:
        raise ValueError("table too large")
    return [{"weekday": wd, "at": at, "bosses": b} for (wd, at), b in sorted(grid.items())]


def normalize(name, aliases, known=()):
    """Public name -> the tracked table's name: alias map first (lower-case
    keys), then a case-insensitive match on a known name, else the name as-is."""
    n = _clean(name)
    key = n.lower()
    if key in aliases:
        return aliases[key]
    for k in known:
        if k.lower() == key:
            return k
    return n


def _names(segments, aliases, known):
    lower = {k.lower() for k in known}
    out = []
    for seg in segments:
        key = _clean(seg).lower()
        parts = [seg] if key in aliases or key in lower else _SPLIT_RE.split(seg)
        for part in parts:
            if _clean(part) in _EMPTY:
                continue
            n = normalize(part, aliases, known)[:MAX_NAME]
            if n not in out:
                out.append(n)
    return out


def _wmin(wd, at):
    hh, mm = at.split(":")
    return wd * 1440 + int(hh) * 60 + int(mm)


def _from_wmin(m):
    m %= _WEEK_MIN
    return m // 1440, f"{m % 1440 // 60:02d}:{m % 60:02d}"


def remote_slots(page, cfg, table, now_epoch):
    """The public table as PT slots with tracked names: [{weekday, at, bosses}]."""
    known = sorted({b for s in table["slots"] for b in s["bosses"]})
    shift = 0
    if cfg["tz"] == "UTC":
        now = _dt.datetime.fromtimestamp(now_epoch, _dt.timezone.utc)
        shift = pt_offset_hours(now) * 60
    merged = {}
    for s in parse_table(page, cfg["table_id"]):
        key = _from_wmin(_wmin(s["weekday"], s["at"]) + shift)
        row = merged.setdefault(key, [])
        row.extend(n for n in _names(s["bosses"], cfg["aliases"], known) if n not in row)
    return [{"weekday": wd, "at": at, "bosses": b} for (wd, at), b in sorted(merged.items()) if b]


# -- diff -------------------------------------------------------------------------

def _index(slots):
    out = {}
    for s in slots:
        row = out.setdefault((s["weekday"], s["at"]), [])
        row.extend(b for b in s["bosses"] if b not in row)
    return out


def _join(names):
    return " + ".join(names)


def _entry(kind, lk, rk, local, remote):
    wd, rwd = (lk or rk)[0], rk[0] if rk else None
    lat, rat = lk[1] if lk else None, rk[1] if rk else None
    day = DAY_NAMES[wd]
    if kind == "moved":
        to = rat if rwd == wd else f"{DAY_NAMES[rwd]} {rat}"
        text = f"{day} {lat} -> {to}: {_join(local)}"
    elif kind == "changed":
        text = f"{day} {lat}: {_join(local)} -> {_join(remote)}"
    elif kind == "missing":
        text = f"{day} {lat}: {_join(local)} not listed upstream"
    else:
        text = f"{day} {rat}: {_join(remote)} listed upstream only"
    return {"kind": kind, "weekday": wd, "local_at": lat, "remote_weekday": rwd,
            "remote_at": rat, "local": list(local), "remote": list(remote), "text": text}


def _dist(a, b):
    d = abs(_wmin(*a) - _wmin(*b)) % _WEEK_MIN
    return min(d, _WEEK_MIN - d)


def diff(local, remote):
    """Slot differences, ordered by (weekday, time): "changed" (same slot, other
    bosses), "moved" (same bosses within MAX_MOVE_MIN), "missing" (tracked
    only), "extra" (public only)."""
    L, R = _index(local), _index(remote)
    out = []
    for k in sorted(set(L) & set(R)):
        if set(L[k]) != set(R[k]):
            out.append(_entry("changed", k, k, L[k], R[k]))
    lonly = sorted(set(L) - set(R))
    ronly = sorted(set(R) - set(L))
    for lk in list(lonly):
        cands = [rk for rk in ronly if set(R[rk]) == set(L[lk]) and _dist(lk, rk) <= MAX_MOVE_MIN]
        if cands:
            rk = min(cands, key=lambda c: (_dist(lk, c), c))
            out.append(_entry("moved", lk, rk, L[lk], R[rk]))
            lonly.remove(lk)
            ronly.remove(rk)
    out += [_entry("missing", k, None, L[k], []) for k in lonly]
    out += [_entry("extra", None, k, [], R[k]) for k in ronly]
    out.sort(key=lambda e: (e["weekday"], e["local_at"] or e["remote_at"]))
    return out


def banner(host, n):
    return f"schedule differs from {host} on {n} slot{'' if n == 1 else 's'} - verify"


def clean_slots(raw):
    """Cached slots re-validated (a corrupt cache = no data), or None."""
    if not isinstance(raw, list) or not raw or len(raw) > MAX_SLOTS:
        return None
    out = []
    for s in raw:
        if not isinstance(s, dict):
            return None
        wd, at, b = s.get("weekday"), s.get("at"), s.get("bosses")
        if not (isinstance(wd, int) and not isinstance(wd, bool) and 0 <= wd <= 6):
            return None
        if not (isinstance(at, str) and AT_RE.match(at)):
            return None
        if not (isinstance(b, list) and b and all(
                isinstance(x, str) and x and len(x) <= MAX_NAME and x.isprintable() for x in b)):
            return None
        out.append({"weekday": wd, "at": at, "bosses": list(b)})
    return out


# -- client -----------------------------------------------------------------------

class DriftClient(CachedClient):
    """robots.txt check + one table GET, cached TTL_S, plan 002 backoff, plus a
    persisted attempt floor so a failure never retries before MIN_INTERVAL_S."""

    def __init__(self, cfg, fetch=None, clock=time.time, cache_dir=None):
        super().__init__(fetch=fetch or default_fetch, clock=clock,
                         cache_dir=cache_dir if cache_dir is not None else _DEFAULT_CACHE)
        self.cfg = cfg

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

    def _download(self, table):
        now = self.clock()
        self._record(now, None)  # the floor holds even if this attempt dies midway
        verdict = robots_verdict(self.fetch, urls=(self.cfg["url"],),
                                 robots_url=self.cfg["robots"])
        self._record(now, verdict)
        if verdict != "allow":
            raise UpstreamError(f"robots.txt: {verdict}")
        try:
            raw = self.fetch(self.cfg["url"], TIMEOUT_S)
        except Exception as e:  # noqa: BLE001 - HTTPError, URLError, timeout
            raise UpstreamError(f"{type(e).__name__}: {e}"[:200]) from e
        try:
            return {"slots": remote_slots(_decode(raw), self.cfg, table, now)}
        except ValueError as e:
            raise UpstreamError(f"parse: {e}"[:200]) from e

    def due(self):
        at = self.attempt()["at"]
        return at is None or self.clock() - at >= MIN_INTERVAL_S

    def refresh(self, table, spawn=None):
        """Background refresh when due; never blocks the caller."""
        if not self.due():
            return False
        return self.refresh_async(KEY, TTL_S, lambda: self._download(table), spawn)

    def peek(self):
        return super().peek(KEY, TTL_S)


# -- service --------------------------------------------------------------------

class DriftService:
    """`drift` block of GET /api/bosses and `/api/state` sources.bossdrift.
    Reads the cache only, except `view(refresh=True)` which may start one
    background refresh. Never writes the tracked table."""

    def __init__(self, client, table, spawn=None):
        self.client = client
        self.table = table
        self.spawn = spawn  # background refresh runner (None = daemon thread)

    def _source_url(self):
        return self.client.cfg["url"] if self.client is not None else None

    def _compute(self):
        """(state, robots, res, diff)."""
        if self.client is None:
            return "unknown", None, None, []
        res = self.client.peek()
        robots = self.client.attempt()["robots"]
        if robots != "allow" or res is None or res["data"] is None:
            return "unknown", robots, res, []
        if res["stale"] and res["error"]:
            return "unknown", robots, res, []
        slots = clean_slots(res["data"].get("slots") if isinstance(res["data"], dict) else None)
        if slots is None:
            return "unknown", robots, res, []
        d = diff(self.table["slots"], slots)
        return ("differs" if d else "ok"), robots, res, d

    def _banner(self, state, d):
        if state != "differs":
            return None
        return banner(urllib.parse.urlsplit(self._source_url()).hostname, len(d))

    def view(self, refresh=False):
        if refresh and self.client is not None:
            self.client.refresh(self.table, self.spawn)
        state, robots, res, d = self._compute()
        return {"state": state, "source": self._source_url(), "robots": robots,
                "checked": res["fetched_at"] if res else None,
                "error": res["error"] if res else None,
                "n": len(d), "diff": d, "banner": self._banner(state, d)}

    def source(self):
        """`/api/state` sources.bossdrift; never fetches."""
        state, robots, res, d = self._compute()
        return {"updated": res["fetched_at"] if res else None, "ttl_s": TTL_S,
                "status": state, "robots": robots, "banner": self._banner(state, d)}
