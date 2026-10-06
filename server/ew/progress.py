"""Progress (plan 004 slice A): operator-entered character + tracks in the
`progress` store domain, and a read-only BDO-REST-API profile card.

Nothing is read from the game. The family name comes only from gitignored
config/local.json (`profile.family`); without one the profile is `status:
"none"` and nothing is fetched. The profile goes through httpcache (TTL 3600 s,
plan 002 backoff, HTTP 202 / "being fetched" = transient flat retry). Requests
never wait on upstream: they read the cache (`peek`); GET also starts a
background refresh, at most one in flight.
"""

import datetime as _dt
import hashlib
import ipaddress
import json
import re
import threading
import time
import urllib.parse
from pathlib import Path

from . import brackets, coupons
from .httpcache import CachedClient, Pending, UpstreamError, freshness, read_json
from .levels import LEVEL_RANGE
from .store import atomic_write_json, atomic_write_text
from .today import slug

# Plan 061: the public BDO-REST-API host is robots-disallowed, so the profile
# source ships off. The documented path is a self-hosted instance
# (man90/bdo-rest-api) set as profile.base_url; SELF_HOST_EXAMPLE is its default.
DEFAULT_BASE = ""
SELF_HOST_EXAMPLE = "http://127.0.0.1:8001/v1"
MAX_BASE = 200
ROBOTS_RECHECK_S = 24 * 3600
FAIL_LIMIT = 3              # consecutive failures on an allowed base ...
FAIL_BACKOFF_S = 24 * 3600  # ... back off this long (plan 002 backoff, extended)
GATE_FILE = "robots_gate.json"
OFF_REASONS = ("no_base", "robots")
REGION = "NA"
PROFILE_TTL = 3600
TIMEOUT_S = 10
FAMILY_RE = re.compile(r"^[A-Za-z0-9_]{2,16}$")
ID_RE = re.compile(r"^[a-z0-9-]{1,40}$")
KINDS = ("quest", "season", "gear")
MAX_TITLE = 80
MAX_NAME = 40
MAX_TRACKS = 30
MAX_STEPS = 60
GS_RANGE = (0, 999)
GS_KEYS = ("ap", "aap", "dp")
# Plan 041 profile history: snapshot key <- BDO-REST-API field (level and
# specLevels are per character only).
SNAP_RAW = (("gs", "gs"), ("energy", "energy"), ("contribution", "contributionPoints"),
            ("combat_fame", "combatFame"), ("life_fame", "lifeFame"))
SERIES_FIELDS = ("level",) + tuple(k for k, _ in SNAP_RAW)
STAT_MAX = 10 ** 9
MAX_SPECS = 12
HISTORY_DAYS = 90
HISTORY_MAX_ROWS = 20000
HISTORY_DEFAULT_DAYS = 30
_DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "ops" / "runtime" / "cache" / "profile"

OBJ_KINDS = ("level", "gear", "quest", "other")
GEAR_RANGE = (1, 20)  # enhancement +1..+15, then 16..20 = PRI DUO TRI TET PEN
MAX_REWARD = 80
NEXT_OPEN = 3
LEVEL_TITLE_RE = re.compile(r"^(?:reach\s+)?(?:level|lv\.?)\s*(\d{1,2})$", re.IGNORECASE)

# Plan 013: season pass objectives, structure only (title, kind, target). The
# UI labels it "seed, verify against the in-game pass" until the operator edits.
SEASON_SEED = [
    ("Reach Lv 10", "level", 10), ("Season quest: Balenos", "quest", None),
    ("Reach Lv 20", "level", 20), ("Season quest: Serendia", "quest", None),
    ("Reach Lv 30", "level", 30), ("Season quest: Calpheon", "quest", None),
    ("Reach Lv 40", "level", 40), ("Season quest: Valencia", "quest", None),
    ("Reach Lv 50", "level", 50),
    ("Tuvala main weapon +10", "gear", 10), ("Tuvala sub-weapon +10", "gear", 10),
    ("Tuvala awakening weapon +10", "gear", 10), ("Tuvala armor +10", "gear", 10),
    ("Reach Lv 53", "level", 53), ("Reach Lv 55", "level", 55),
    ("Tuvala main weapon PRI", "gear", 16), ("Tuvala awakening weapon PRI", "gear", 16),
    ("Tuvala armor PRI", "gear", 16),
    ("Reach Lv 56", "level", 56), ("Reach Lv 57", "level", 57), ("Reach Lv 58", "level", 58),
    ("Reach Lv 59", "level", 59), ("Reach Lv 60", "level", 60),
    ("Graduate", "other", None),
]
# The plan 004 coarse season seed; a stored track with exactly these steps is
# migrated onto SEASON_SEED with its done marks carried over.
COARSE_SEASON = ["Level 10", "Level 20", "Level 30", "Level 40", "Level 50", "Graduate"]

SEED = [
    ("Main story", "quest", ["Balenos", "Serendia", "Calpheon", "Mediah", "Valencia",
                             "Kamasylvia", "Drieghan", "O'dyllita", "Land of the Morning Light",
                             "Ulukita", "Edania"]),
    ("Season pass", "season", None),  # objectives from SEASON_SEED
    ("Tuvala -> PEN gear", "gear", ["Main weapon", "Sub-weapon", "Awakening weapon", "Helmet",
                                   "Armor", "Gloves", "Shoes", "Necklace", "Ring 1", "Ring 2",
                                   "Earring 1", "Earring 2", "Belt"]),
]


# Plan 034: tracked track seeds, one JSON per track, copied into the store as an
# ordinary custom track on request. SEED_ORDER is the "Add track" select order.
SEED_DIR = Path(__file__).resolve().parent / "data" / "tracks"
SEED_ORDER = ("gear_roadmap", "graduation_readiness", "fughar_journal", "igor_bartali",
              "emma_bartali")
SEED_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
SEED_KINDS = ("quest", "gear")
SEED_FIELDS = {"id", "title", "kind", "note", "source", "verified", "steps"}
STEP_FIELDS = {"id", "title", "min_level", "ap", "dp", "note", "source", "verified"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_NOTE = 200
# Plan 042: Life & CP card. In-game life skill order; unknown keys follow.
CP_MILESTONES = Path(__file__).resolve().parent / "data" / "cp_milestones.json"
CP_FIELDS = {"note", "source", "verified", "milestones"}
LIFE_SKILLS = ("gathering", "fishing", "hunting", "cooking", "alchemy", "processing",
               "training", "trading", "farming", "sailing", "bartering")
HIDDEN = "hidden"
GATES =(("min_level", "level", "lv", LEVEL_RANGE), ("ap", "ap", "AP", GS_RANGE),
         ("dp", "dp", "DP", GS_RANGE))


def _iso_now(clock):
    return _dt.datetime.fromtimestamp(clock(), _dt.timezone.utc).replace(
        microsecond=0).isoformat()


def _parse_iso(s):
    if not isinstance(s, str):
        return None
    try:
        when = _dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    return s if when.tzinfo is not None else None


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def pct(done, total):
    """Whole percent, floored, so 100 shows only when every step is done."""
    if total <= 0:
        return 0
    return min(100, done * 100 // total)


# -- profile ---------------------------------------------------------------

def family_from_config(cfg):
    """`profile` object from config/local.json -> family name or None."""
    fam = cfg.get("family") if isinstance(cfg, dict) else None
    return fam if isinstance(fam, str) and FAMILY_RE.match(fam) else None


def config_profile(root):
    """`profile` object from gitignored config/local.json, or {}."""
    try:
        doc = json.loads((Path(root) / "config" / "local.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    p = doc.get("profile") if isinstance(doc, dict) else None
    return p if isinstance(p, dict) else {}


def _split_base(v):
    """urlsplit of a usable base (http/https, a host, no userinfo, query,
    fragment, whitespace or non-ASCII), else None."""
    if not isinstance(v, str) or not v or len(v) > MAX_BASE or not v.isascii() \
            or any(ch.isspace() for ch in v):
        return None
    try:
        p = urllib.parse.urlsplit(v)
        host = p.hostname
        p.port  # noqa: B018 - raises ValueError on a junk port
    except ValueError:
        return None
    if p.scheme not in ("http", "https") or not host or "@" in p.netloc \
            or p.query or p.fragment:
        return None
    return p


def _loopback_host(host):
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def is_loopback_base(v):
    p = _split_base(v)
    return p is not None and _loopback_host(p.hostname)


def base_ok(v):
    """Plan 061 profile.base_url: https on any host, or http on loopback only."""
    p = _split_base(v)
    return p is not None and (p.scheme == "https" or _loopback_host(p.hostname))


def _chars(raw):
    out = []
    for ch in raw if isinstance(raw, list) else []:
        if not isinstance(ch, dict) or not isinstance(ch.get("name"), str):
            continue
        lvl = ch.get("level")
        out.append({"name": ch["name"],
                    "cls": ch.get("class") if isinstance(ch.get("class"), str) else None,
                    "level": lvl if _is_int(lvl) else None,
                    "main": ch.get("main") is True})
    return out


class ProfileClient(CachedClient):
    """BDO-REST-API adventurer search by family name, region NA, GET only.

    Plan 061: off - zero requests - without a usable base, or while the base's
    robots.txt (read at most once per ROBOTS_RECHECK_S, verdict persisted)
    disallows `*` or Ebonwake or cannot be read; a loopback base (the
    operator's own instance) skips the gate. FAIL_LIMIT consecutive failures
    stretch the backoff to FAIL_BACKOFF_S; the last good cache is served."""

    def __init__(self, family, base_url=None, fetch=None, clock=time.time, cache_dir=None):
        super().__init__(fetch=fetch, clock=clock,
                         cache_dir=cache_dir if cache_dir is not None else _DEFAULT_CACHE)
        self.family = family
        self.base = base_url.rstrip("/") if base_ok(base_url) else DEFAULT_BASE
        p = _split_base(self.base)
        self.loopback = p is not None and _loopback_host(p.hostname)
        self.origin = f"{p.scheme}://{p.netloc}" if p is not None else None
        self._gate_lock = threading.Lock()
        # Cache file name never spells the family name.
        h = hashlib.sha256(f"{REGION}:{family.lower()}".encode("utf-8")).hexdigest()[:16]
        self.key = f"profile_{h}"
        # Plan 041: called with profile_snapshots() after each successful
        # download (never on a cache hit, so never an extra request).
        self.on_snapshot = None

    def url(self):
        q = {"query": self.family, "searchType": "familyName", "region": REGION}
        return f"{self.base}/adventurer/search?" + urllib.parse.urlencode(q)

    def _download(self):
        try:
            raw = self.fetch(self.url(), TIMEOUT_S)
        except Pending:
            raise
        except Exception as e:  # HTTPError (404 = no such family), URLError, timeout
            raise UpstreamError(f"{type(e).__name__}: {e}"[:200]) from e
        try:
            body = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
        except (ValueError, UnicodeDecodeError) as e:
            raise UpstreamError(f"bad json: {e}"[:200]) from e
        if isinstance(body, dict) and "familyName" not in body:
            msg = str(body.get("message") or body.get("code") or "bad shape")
            if "fetch" in msg.lower() or "later" in msg.lower():
                raise Pending("being fetched, retry later")
            raise UpstreamError(f"profile: {msg}"[:200])
        hits = body if isinstance(body, list) else [body]
        hits = [h for h in hits if isinstance(h, dict) and isinstance(h.get("familyName"), str)]
        pick = next((h for h in hits if h["familyName"].lower() == self.family.lower()), None)
        if pick is None:
            raise UpstreamError("profile: family not found")
        guild = pick.get("guild")
        if self.on_snapshot is not None:
            try:
                self.on_snapshot(profile_snapshots(pick))
            except Exception:  # noqa: BLE001 - history never breaks the profile card
                pass
        # profileTarget is an opaque account-level id: never cached or served.
        return {"family": pick["familyName"],
                "region": pick.get("region") if isinstance(pick.get("region"), str) else REGION,
                "guild": guild.get("name") if isinstance(guild, dict)
                and isinstance(guild.get("name"), str) else None,
                "characters": _chars(pick.get("characters"))}

    # -- plan 061 robots gate -------------------------------------------------
    def _gate_entry(self):
        doc = read_json(self.cache_dir / GATE_FILE)
        e = doc.get(self.origin) if isinstance(doc, dict) else None
        if not (isinstance(e, dict) and e.get("verdict") in coupons.ROBOTS
                and isinstance(e.get("checked_at"), (int, float))
                and not isinstance(e.get("checked_at"), bool)):
            return None  # corrupt or missing = not checked yet
        return e

    def off_reason(self):
        """"no_base" | "robots" | None (on, or the gate not read yet); never fetches."""
        if not self.base:
            return "no_base"
        if self.loopback:
            return None
        e = self._gate_entry()
        return "robots" if e is not None and e["verdict"] != "allow" else None

    def _gate_due(self):
        if not self.base or self.loopback:
            return False
        e = self._gate_entry()
        return e is None or self.clock() - e["checked_at"] >= ROBOTS_RECHECK_S

    def _check_gate(self):
        """One robots.txt GET when due, then off_reason()."""
        with self._gate_lock:
            if self._gate_due():
                verdict = coupons.robots_verdict(
                    self.fetch, urls=(self.base + "/adventurer/search",),
                    robots_url=self.origin + "/robots.txt")
                path = self.cache_dir / GATE_FILE
                doc = read_json(path)
                doc = doc if isinstance(doc, dict) else {}
                doc[self.origin] = {"verdict": verdict, "checked_at": self.clock()}
                atomic_write_json(path, doc)
        return self.off_reason()

    def cached_get(self, key, ttl, download):
        reason = self._check_gate()
        if reason is not None:
            return self._result(None, self.clock(), ttl, True, f"profile source off: {reason}")
        res = super().cached_get(key, ttl, download)
        bo = self.key_backoff(key)
        if bo.get("n", 0) >= FAIL_LIMIT and not bo.get("pending") and not bo.get("long"):
            self._set_backoff(key, dict(bo, until=self.clock() + FAIL_BACKOFF_S, long=True))
        return res

    def get(self):
        return self.cached_get(self.key, PROFILE_TTL, self._download)

    def peek(self):
        return super().peek(self.key, PROFILE_TTL)

    def refresh(self, spawn=None):
        """Background refresh, at most one in flight; never blocks the caller.
        Off = nothing scheduled, except a due robots re-check."""
        if self.off_reason() is not None and not self._gate_due():
            return False
        return self.refresh_async(self.key, PROFILE_TTL, self._download, spawn)

    def is_pending(self):
        return self.pending(self.key)


def _num(v):
    """A non-negative int stat, or None (privacy-hidden, junk or absurd)."""
    return v if _is_int(v) and 0 <= v <= STAT_MAX else None


def _spec_levels(raw):
    if not isinstance(raw, dict) or not raw:
        return None
    out = {}
    for k, v in list(raw.items())[:MAX_SPECS]:
        if isinstance(k, str) and _ascii(k, MAX_NAME) and \
                ((isinstance(v, str) and _ascii(v, MAX_NAME)) or _num(v) is not None):
            out[k] = v
    return out or None


def profile_snapshots(pick):
    """One adventurer search hit -> [{name, main, level?, gs?, energy?,
    contribution?, combat_fame?, life_fame?, spec_levels?}] per character.
    A stat is read from the character, else from the family (the openapi puts
    some on the profile); hidden or malformed stats are absent, never zero.
    `main` falls back to the first character when none is flagged."""
    chars = [ch for ch in (pick.get("characters") if isinstance(pick.get("characters"), list)
                           else []) if isinstance(ch, dict) and isinstance(ch.get("name"), str)]
    flagged = any(ch.get("main") is True for ch in chars)
    out = []
    for n, ch in enumerate(chars):
        snap = {"name": ch["name"][:MAX_NAME],
                "main": ch.get("main") is True if flagged else n == 0}
        lvl = ch.get("level")
        if _is_int(lvl) and LEVEL_RANGE[0] <= lvl <= LEVEL_RANGE[1]:
            snap["level"] = lvl
        for key, raw in SNAP_RAW:
            v = _num(ch.get(raw))
            v = _num(pick.get(raw)) if v is None and raw not in ch else v
            if v is not None:
                snap[key] = v
        specs = _spec_levels(ch.get("specLevels"))
        if specs is not None:
            snap["spec_levels"] = specs
        out.append(snap)
    return out


def _main_level(snaps):
    main = next((s for s in snaps if s.get("main")), None)
    return main.get("level") if main else None


class ProfileHistory:
    """Plan 041: rolling `profile_history.jsonl`, one line per character per
    successful profile refresh ({at, name, main, level?, ...}). Rewritten
    whole via tmp + replace (atomic append), rows older than HISTORY_DAYS and
    beyond HISTORY_MAX_ROWS dropped; a corrupt line is skipped."""

    def __init__(self, path, clock=time.time):
        self.path = Path(path)
        self.clock = clock
        self._lock = threading.Lock()

    def _now(self):
        return _dt.datetime.fromtimestamp(self.clock(), _dt.timezone.utc)

    def rows(self):
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            return []
        out = []
        for line in text.splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if isinstance(r, dict) and _parse_iso(r.get("at")) and isinstance(r.get("name"), str):
                out.append(r)
        return out

    def _cutoff(self, days):
        return self._now() - _dt.timedelta(days=days)

    def append(self, snaps):
        at = _iso_now(self.clock)
        cut = self._cutoff(HISTORY_DAYS)
        with self._lock:
            rows = [r for r in self.rows() if _dt.datetime.fromisoformat(r["at"]) >= cut]
            rows += [dict(s, at=at) for s in snaps]
            rows = rows[-HISTORY_MAX_ROWS:]
            atomic_write_text(self.path, "".join(json.dumps(r, sort_keys=True) + "\n"
                                                 for r in rows))

    def main_level(self):
        """Main character level of the newest refresh, or None."""
        rows = self.rows()
        if not rows:
            return None
        last = rows[-1]["at"]
        return _main_level([r for r in rows if r["at"] == last])

    def main_snapshot(self):
        """Plan 042: the main character's row of the newest refresh, or None."""
        rows = self.rows()
        if not rows:
            return None
        last = rows[-1]["at"]
        return next((r for r in rows if r["at"] == last and r.get("main") is True), None)

    def series(self, fields, days, character=None):
        """{days, character, series: {field: {points: [{at, v}], first, last,
        delta}}}; character defaults to the main of the newest refresh."""
        cut = self._cutoff(days)
        rows = [r for r in self.rows() if _dt.datetime.fromisoformat(r["at"]) >= cut]
        if character is None:
            main = next((r for r in reversed(rows) if r.get("main") is True), None)
            character = main["name"] if main else None
        rows = [r for r in rows if r["name"] == character]
        out = {}
        for f in fields:
            pts = [{"at": r["at"], "v": r[f]} for r in rows if _num(r.get(f)) is not None]
            first = pts[0]["v"] if pts else None
            last = pts[-1]["v"] if pts else None
            out[f] = {"points": pts, "first": first, "last": last,
                      "delta": None if first is None else last - first}
        return {"days": days, "character": character, "series": out}


def _profile_status(res, pending=False):
    """ok | stale | error | pending (no data, being fetched) | none."""
    if res is None or res["data"] is None:
        if pending:
            return "pending"
        return "none" if res is None else "error"
    return "stale" if res["stale"] else "ok"


# -- validation ------------------------------------------------------------

def _check_text(v, what, max_len, allow_empty=False):
    if not isinstance(v, str):
        raise ValueError(f"{what} must be a string")
    v = v.strip()
    if (not v and not allow_empty) or len(v) > max_len:
        raise ValueError(f"{what} must be {0 if allow_empty else 1}..{max_len} characters")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in v):
        raise ValueError(f"{what} must not contain control characters")
    return v


def _check_id(v, what):
    if not isinstance(v, str) or not ID_RE.match(v):
        raise ValueError(f"{what} must match ^[a-z0-9-]{{1,40}}$")
    return v


def _check_range(v, what, lo, hi):
    if not _is_int(v) or not lo <= v <= hi:
        raise ValueError(f"{what} must be an int in {lo}..{hi}")
    return v


def _unique(base, taken):
    if base not in taken:
        return base
    n = 2
    while True:
        suffix = f"-{n}"
        cand = base[:40 - len(suffix)].rstrip("-") + suffix
        if cand not in taken:
            return cand
        n += 1


# -- plan 034 track seeds ----------------------------------------------------

def _ascii(v, max_len, allow_empty=False):
    return (isinstance(v, str) and len(v) <= max_len and (allow_empty or v.strip() != "")
            and all(32 <= ord(ch) <= 126 for ch in v))


def _date_or_false(v):
    if v is False:
        return True
    if not isinstance(v, str) or not DATE_RE.match(v):
        return False
    try:
        _dt.date.fromisoformat(v)
    except ValueError:
        return False
    return True


def _source_ok(v):
    return _ascii(v, MAX_NOTE) and v.startswith("https://")


def _step_meta(st):
    """The valid plan 034 fields of a step dict (gates, note, source, verified);
    anything malformed is dropped, never guessed."""
    out = {}
    for key, _stat, _unit, rng in GATES:
        v = st.get(key)
        if _is_int(v) and rng[0] <= v <= rng[1]:
            out[key] = v
    if _ascii(st.get("note"), MAX_NOTE, allow_empty=True):
        out["note"] = st["note"]
    if _source_ok(st.get("source")):
        out["source"] = st["source"]
    if "verified" in st and _date_or_false(st["verified"]):
        out["verified"] = st["verified"]
    return out


def validate_seed(doc):
    """Normalised copy of one seed track file, or ValueError."""
    if not isinstance(doc, dict) or set(doc) != SEED_FIELDS:
        raise ValueError(f"seed must be {{{', '.join(sorted(SEED_FIELDS))}}}")
    if not isinstance(doc["id"], str) or not SEED_ID_RE.match(doc["id"]):
        raise ValueError("seed id must match ^[a-z][a-z0-9_]{0,39}$")
    if not _ascii(doc["title"], MAX_TITLE) or doc["kind"] not in SEED_KINDS:
        raise ValueError(f"seed needs an ASCII title and kind {'/'.join(SEED_KINDS)}")
    if not _ascii(doc["note"], MAX_NOTE, allow_empty=True) or not _source_ok(doc["source"]) \
            or not _date_or_false(doc["verified"]):
        raise ValueError("seed note / source (https) / verified (date or false) invalid")
    steps = doc["steps"]
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise ValueError(f"seed steps must be a list of 1..{MAX_STEPS}")
    out, seen = [], set()
    for st in steps:
        if not isinstance(st, dict) or not {"id", "title", "note", "source", "verified"} <= \
                set(st) or set(st) - STEP_FIELDS:
            raise ValueError(f"each step must be {{{', '.join(sorted(STEP_FIELDS))}}}")
        if not isinstance(st["id"], str) or not ID_RE.match(st["id"]) or st["id"] in seen:
            raise ValueError(f"step id must be unique and match ^[a-z0-9-]{{1,40}}$: {st['id']!r}")
        seen.add(st["id"])
        if not _ascii(st["title"], MAX_TITLE):
            raise ValueError(f"step {st['id']}: title must be 1..{MAX_TITLE} ASCII characters")
        meta = _step_meta(st)
        bad = sorted(k for k in set(st) - {"id", "title"} if k not in meta)
        if bad:
            raise ValueError(f"step {st['id']}: invalid {', '.join(bad)}")
        out.append(dict({"id": st["id"], "title": st["title"]}, **meta))
    return dict({k: doc[k] for k in SEED_FIELDS - {"steps"}}, steps=out)


def load_seeds(path=None):
    """([seed], [error]) from the tracked seed dir; a broken file is skipped
    with an error line, never breaking the Progress tab. SEED_ORDER first."""
    root = Path(SEED_DIR if path is None else path)
    if not root.is_dir():
        return [], [f"seeds: no directory {root.name}"]
    try:
        files = sorted(root.glob("*.json"))
    except OSError as e:
        return [], [f"seeds: {e}"[:200]]
    seeds, errors = [], []
    for f in files:
        try:
            s = validate_seed(json.loads(f.read_text(encoding="ascii")))
            if s["id"] != f.stem:
                raise ValueError("id does not match the file name")
        except (OSError, ValueError) as e:
            errors.append(f"{f.name}: {e}"[:200])
            continue
        seeds.append(s)
    rank = {sid: n for n, sid in enumerate(SEED_ORDER)}
    seeds.sort(key=lambda s: (rank.get(s["id"], len(rank)), s["id"]))
    return seeds, errors


# -- plan 042 life skills / energy / contribution points -----------------------

def load_cp_milestones(path=None):
    """Validated tracked CP milestones {note, source, verified, milestones:
    [{cp, label, verified}]} (cp strictly ascending), or ValueError."""
    p = Path(CP_MILESTONES if path is None else path)
    try:
        doc = json.loads(p.read_text(encoding="ascii"))
    except (OSError, ValueError) as e:
        raise ValueError(f"{p.name}: {e}"[:200]) from e
    if not isinstance(doc, dict) or set(doc) != CP_FIELDS:
        raise ValueError(f"cp milestones must be {{{', '.join(sorted(CP_FIELDS))}}}")
    if not _ascii(doc["note"], MAX_NOTE, allow_empty=True) or not _source_ok(doc["source"]) \
            or not _date_or_false(doc["verified"]):
        raise ValueError("cp milestones note / source (https) / verified (date or false) invalid")
    ms = doc["milestones"]
    if not isinstance(ms, list) or not 1 <= len(ms) <= MAX_STEPS:
        raise ValueError(f"cp milestones must be a list of 1..{MAX_STEPS}")
    prev = 0
    for m in ms:
        if not isinstance(m, dict) or set(m) != {"cp", "label", "verified"}:
            raise ValueError("each milestone must be {cp, label, verified}")
        if not _is_int(m["cp"]) or not prev < m["cp"] <= STAT_MAX:
            raise ValueError(f"milestone cp must be an int, strictly ascending: {m['cp']!r}")
        if not _ascii(m["label"], MAX_TITLE) or not _date_or_false(m["verified"]):
            raise ValueError(f"milestone {m['cp']}: label (ASCII) / verified invalid")
        prev = m["cp"]
    return doc


def _skills(raw):
    if not isinstance(raw, dict) or not raw:
        return HIDDEN
    rank = {k: n for n, k in enumerate(LIFE_SKILLS)}
    keys = [k for k, v in raw.items() if isinstance(k, str) and _ascii(k, MAX_NAME)
            and ((isinstance(v, str) and _ascii(v, MAX_NAME)) or _num(v) is not None)]
    if not keys:
        return HIDDEN
    keys.sort(key=lambda k: rank.get(k.lower(), len(rank)))  # stable: unknowns keep API order
    return [{"key": k, "name": k[:1].upper() + k[1:], "rank": str(raw[k])} for k in keys]


def lifeskill_card(snapshot, milestones):
    """One plan 041 snapshot (newest main character row) -> the Life & CP
    card: {status: ok|none, character, at, skills: [{key, name, rank}],
    energy, cp: {value, next, gap, reached}, source, verified, error}. A
    field privacy hides (absent or malformed) is "hidden", never zero."""
    ms = milestones if isinstance(milestones, dict) else {}
    out = {"status": "none", "character": None, "at": None, "skills": None,
           "energy": None, "cp": None, "source": ms.get("source"),
           "verified": ms.get("verified"), "error": ms.get("error")}
    if not isinstance(snapshot, dict):
        return out
    energy, value = _num(snapshot.get("energy")), _num(snapshot.get("contribution"))
    out.update(status="ok", character=snapshot.get("name"), at=snapshot.get("at"),
               skills=_skills(snapshot.get("spec_levels")),
               energy=HIDDEN if energy is None else energy)
    if value is None:
        out["cp"] = HIDDEN
        return out
    rows = [{"cp": m["cp"], "label": m["label"], "verified": m["verified"]}
            for m in ms.get("milestones") or ()]
    nxt = next((m for m in rows if m["cp"] > value), None)
    out["cp"] = {"value": value, "next": nxt, "gap": None if nxt is None else nxt["cp"] - value,
                 "reached": [m for m in rows if m["cp"] <= value]}
    return out


def _gates(st, level, gs, ap_rows):
    """Plan 034 gate chips for a step: [{stat, need, have, gap, state, label}]
    (ap also `bonus_gain`, the plan 023 bonus AP the gap is worth), and
    ready: True | False (a gate needs more) | None (no gate, or a stat unset)."""
    have_of = {"level": level, "ap": gs.get("ap"), "dp": gs.get("dp")}
    out = []
    for key, stat, unit, _rng in GATES:
        need = st.get(key)
        if need is None:
            continue
        have = have_of[stat] if _is_int(have_of[stat]) else None
        if have is None:
            g = {"stat": stat, "need": need, "have": None, "gap": None, "state": "unknown",
                 "label": f"set {'level' if stat == 'level' else unit}"}
        else:
            gap = max(0, need - have)
            g = {"stat": stat, "need": need, "have": have, "gap": gap,
                 "state": "needs" if gap else "ready",
                 "label": f"needs +{gap} {unit}" if gap else "ready"}
        if stat == "ap":
            gain = None
            if g["state"] == "needs" and ap_rows:
                a, b = brackets.lookup(ap_rows, have)["value"], brackets.lookup(ap_rows, need)["value"]
                gain = b - a if a is not None and b is not None else None
            g["bonus_gain"] = gain
        out.append(g)
    states = {g["state"] for g in out}
    if "needs" in states:
        return out, False
    return out, (True if states == {"ready"} else None)


def _make_steps(titles):
    steps, seen = [], set()
    for t in titles:
        sid = _unique(slug(t), seen)
        seen.add(sid)
        steps.append({"id": sid, "title": t, "done_at": None})
    return steps


def _infer(title):
    """"Level 45" / "Lv 45" / "Reach Lv 45" -> ("level", 45); else ("other", None)."""
    m = LEVEL_TITLE_RE.match(title.strip())
    if m and LEVEL_RANGE[0] <= int(m.group(1)) <= LEVEL_RANGE[1]:
        return "level", int(m.group(1))
    return "other", None


def _objective(oid, title, kind, target, reward="", done_at=None, claimed_at=None):
    return {"id": oid, "title": title, "kind": kind, "target": target, "reward": reward,
            "done_at": done_at, "claimed_at": claimed_at if done_at else None}


def _make_objectives(titles):
    out, seen = [], set()
    for t in titles:
        oid = _unique(slug(t), seen)
        seen.add(oid)
        out.append(_objective(oid, t, *_infer(t)))
    return out


def _seed_objectives():
    out, seen = [], set()
    for title, kind, target in SEASON_SEED:
        oid = _unique(slug(title), seen)
        seen.add(oid)
        out.append(_objective(oid, title, kind, target))
    return out


def _target_ok(kind, target):
    if kind == "level":
        return _is_int(target) and LEVEL_RANGE[0] <= target <= LEVEL_RANGE[1]
    if kind == "gear":
        return target is None or (_is_int(target) and GEAR_RANGE[0] <= target <= GEAR_RANGE[1])
    return target is None


def _clean_objective(o):
    if not (isinstance(o, dict) and isinstance(o.get("id"), str) and ID_RE.match(o["id"])
            and isinstance(o.get("title"), str) and o.get("kind") in OBJ_KINDS):
        return None
    kind, target = o["kind"], o.get("target")
    if not _target_ok(kind, target):
        if kind == "gear":
            target = None  # a bad gear target degrades to none; a level one cannot
        else:
            return None
    reward = o.get("reward")
    reward = reward[:MAX_REWARD] if isinstance(reward, str) else ""
    return _objective(o["id"], o["title"], kind, target, reward,
                      _parse_iso(o.get("done_at")), _parse_iso(o.get("claimed_at")))


def _migrate_steps(steps):
    """plan 004 season steps -> (objectives, seed). The untouched coarse seed
    maps onto SEASON_SEED by level target (Graduate by id), keeping done marks;
    any other step list migrates one to one."""
    if [s["title"] for s in steps] == COARSE_SEASON:
        objs = _seed_objectives()
        for s in steps:
            if s["done_at"] is None:
                continue
            kind, target = _infer(s["title"])
            for o in objs:
                if (kind == "level" and o["kind"] == "level" and o["target"] == target) or \
                        (kind != "level" and o["id"] == s["id"]):
                    o["done_at"] = s["done_at"]
                    break
        return objs, True
    objs = []
    for s in steps:
        objs.append(_objective(s["id"], s["title"], *_infer(s["title"]), done_at=s["done_at"]))
    return objs, False


def _default_character():
    return {"name": None, "cls": "Deadeye", "level": None,
            "gs": {"ap": None, "aap": None, "dp": None}}


def _clean_character(c):
    out = _default_character()
    if not isinstance(c, dict):
        return out
    if isinstance(c.get("name"), str) and c["name"]:
        out["name"] = c["name"][:MAX_NAME]
    if isinstance(c.get("cls"), str) and c["cls"]:
        out["cls"] = c["cls"][:MAX_NAME]
    lvl = c.get("level")
    if _is_int(lvl) and LEVEL_RANGE[0] <= lvl <= LEVEL_RANGE[1]:
        out["level"] = lvl
    gs = c.get("gs") if isinstance(c.get("gs"), dict) else {}
    for k in GS_KEYS:
        v = gs.get(k)
        if _is_int(v) and GS_RANGE[0] <= v <= GS_RANGE[1]:
            out["gs"][k] = v
    return out


def _clean_track(t):
    if not isinstance(t, dict):
        return None
    tid, title, kind = t.get("id"), t.get("title"), t.get("kind")
    if not (isinstance(tid, str) and ID_RE.match(tid) and isinstance(title, str)
            and kind in KINDS):
        return None
    steps, seen = [], set()
    for st in t.get("steps") if isinstance(t.get("steps"), list) else []:
        if not (isinstance(st, dict) and isinstance(st.get("id"), str)
                and ID_RE.match(st["id"]) and isinstance(st.get("title"), str)
                and st["id"] not in seen):
            continue
        seen.add(st["id"])
        steps.append(dict({"id": st["id"], "title": st["title"],
                           "done_at": _parse_iso(st.get("done_at"))}, **_step_meta(st)))
    if kind != "season":
        sid = t.get("seed_id")
        return {"id": tid, "title": title, "kind": kind,
                "seed_id": sid if isinstance(sid, str) and SEED_ID_RE.match(sid) else None,
                "steps": steps}
    if not isinstance(t.get("objectives"), list):
        objs, seed = _migrate_steps(steps)
        return {"id": tid, "title": title, "kind": kind, "seed": seed, "objectives": objs}
    objs, seen = [], set()
    for o in t["objectives"]:
        c = _clean_objective(o)
        if c is not None and c["id"] not in seen:
            seen.add(c["id"])
            objs.append(c)
    return {"id": tid, "title": title, "kind": kind, "seed": t.get("seed") is True,
            "objectives": objs[:MAX_STEPS]}


def _level_label(o):
    return f"Lv {o['target']}" if o["kind"] == "level" else o["title"]


def _season_view(t, level):
    objs = []
    for o in t["objectives"]:
        reached = o["kind"] == "level" and level is not None and level >= o["target"]
        objs.append(dict(o, done=o["done_at"] is not None, claimed=o["claimed_at"] is not None,
                         auto=reached,
                         gap=(max(0, o["target"] - level) if o["kind"] == "level"
                              and level is not None else None)))
    steps = [{"id": o["id"], "title": o["title"], "done": o["done"], "done_at": o["done_at"]}
             for o in objs]
    done = sum(o["done"] for o in objs)
    nxt = [{"id": o["id"], "title": o["title"], "kind": o["kind"], "target": o["target"],
            "reward": o["reward"], "gap": o["gap"], "label": _level_label(o)}
           for o in objs if not o["done"]][:NEXT_OPEN]
    return {"id": t["id"], "title": t["title"], "kind": t["kind"], "seed": t["seed"],
            "objectives": objs, "steps": steps, "done": done, "total": len(objs),
            "pct": pct(done, len(objs)), "claimed": sum(o["claimed"] for o in objs),
            "unclaimed": [o["id"] for o in objs if o["done"] and not o["claimed"]],
            "next": nxt, "level": level}


class ProgressService:
    """Store domain `progress`: {"character": {name, cls, level, gs: {ap, aap, dp}},
    "tracks": [{id, title, kind, steps: [{id, title, done_at}]}], "updated"}.
    A `season` track (plan 013) holds {id, title, kind, seed, objectives: [{id,
    title, kind, target, reward, done_at, claimed_at}]} instead of steps.
    `level` returns the plan 011 sample level (or None); with the character
    level, the higher one auto-ticks level objectives. Plan 023: `brackets`
    in the view is the AP/DP bracket summary for the stored gs (tracked
    `data/brackets.json` + store domain `brackets_override`); `epoch` returns
    the newest started XP epoch (re-verify flag) or None."""

    def __init__(self, store, profile_client=None, clock=time.time, spawn=None, level=None,
                 epoch=None, bracket_tables=None, history=None, on_level=None,
                 auto_level=True):
        self.store = store
        self.profile = profile_client
        self.clock = clock
        self.spawn = spawn  # background refresh runner (None = daemon thread)
        self.level = level
        self.epoch = epoch
        # Plan 041: profile history + auto level marker (profile.auto_level).
        self.history = history
        self.on_level = on_level
        self.auto_level = auto_level is not False
        if history is not None and hasattr(profile_client, "on_snapshot"):
            profile_client.on_snapshot = self.record_profile
        self.bracket_error = None
        if bracket_tables is None:
            try:
                bracket_tables = brackets.load_tracked()
            except ValueError as e:  # a broken data file never breaks the Progress tab
                bracket_tables, self.bracket_error = {}, str(e)
        self.bracket_tables = bracket_tables
        self.seeds, self.seed_errors = load_seeds()  # plan 034; errors never break the tab
        self._lock = threading.Lock()  # read-modify-write; Store guards each file op
        with self._lock:
            doc = store.get("progress")
            if "tracks" not in doc:
                tracks, seen = [], set()
                for title, kind, steps in SEED:
                    tid = _unique(slug(title), seen)
                    seen.add(tid)
                    if kind == "season":
                        tracks.append({"id": tid, "title": title, "kind": kind, "seed": True,
                                       "objectives": _seed_objectives()})
                    else:
                        tracks.append({"id": tid, "title": title, "kind": kind,
                                       "steps": _make_steps(steps)})
                self._save(_default_character(), tracks)
            elif any(isinstance(t, dict) and t.get("kind") == "season"
                     and not isinstance(t.get("objectives"), list)
                     for t in (doc["tracks"] if isinstance(doc["tracks"], list) else [])):
                self._save(*self._load())  # persist the plan 004 -> 013 migration once

    def _load(self):
        doc = self.store.get("progress")
        raw = doc.get("tracks")
        tracks, seen = [], set()
        for t in raw if isinstance(raw, list) else []:
            c = _clean_track(t)
            if c is not None and c["id"] not in seen:
                seen.add(c["id"])
                tracks.append(c)
        return _clean_character(doc.get("character")), tracks

    def _save(self, character, tracks):
        self.store.put("progress", {"character": character, "tracks": tracks,
                                    "updated": _iso_now(self.clock)})

    @staticmethod
    def _find(tracks, tid):
        _check_id(tid, "track")
        for n, t in enumerate(tracks):
            if t["id"] == tid:
                return n
        raise ValueError(f"unknown track: {tid}")

    def _level(self, character):
        """Higher of the plan 011 sample level and the character level, or None."""
        try:
            sample = self.level() if self.level is not None else None
        except Exception:  # a broken feed never breaks the Progress tab
            sample = None
        vals = [v for v in (sample, character.get("level"))
                if _is_int(v) and LEVEL_RANGE[0] <= v <= LEVEL_RANGE[1]]
        return max(vals) if vals else None

    def _auto_tick(self, tracks, level):
        """Stamp done_at on every level objective whose target is reached."""
        changed = False
        if level is None:
            return changed
        for t in tracks:
            for o in t.get("objectives", ()):
                if o["kind"] == "level" and o["done_at"] is None and level >= o["target"]:
                    o["done_at"] = _iso_now(self.clock)
                    changed = True
        return changed

    def _season_obj(self, tracks, arg, key="objective"):
        t = tracks[self._find(tracks, arg.get("track"))]
        if t["kind"] != "season":
            raise ValueError(f"not a season track: {t['id']}")
        _check_id(arg.get(key), key)
        o = next((o for o in t["objectives"] if o["id"] == arg[key]), None)
        if o is None:
            raise ValueError(f"unknown objective: {arg[key]}")
        return t, o

    # -- reads -----------------------------------------------------------------

    def profile_view(self, refresh=False):
        """Cache only - a request never waits on upstream. `refresh` (GET)
        starts a background refresh when the cache is stale or empty."""
        if self.profile is None:
            return {"data": None, "freshness": None, "status": "none"}
        if refresh:
            self.profile.refresh(self.spawn)
        reason = self._off_reason()  # after refresh: a sync refresh may read robots.txt
        if reason is not None:  # plan 061: one muted line, no data, nothing scheduled
            return {"data": None, "freshness": None, "status": "off", "state": "off",
                    "reason": reason}
        res = self.profile.peek()
        status = _profile_status(res, self.profile.is_pending())
        if res is None:
            return {"data": None, "freshness": None, "status": status, "state": "on"}
        return {"data": res["data"], "freshness": freshness(res), "status": status,
                "state": "on"}

    def _off_reason(self):
        off = getattr(self.profile, "off_reason", None)
        return off() if off is not None else None

    def view(self, refresh=True):
        """GET /api/progress body; POST answers pass refresh=False (peek only)."""
        with self._lock:
            character, tracks = self._load()
            level = self._level(character)
            if self._auto_tick(tracks, level):
                self._save(character, tracks)
        tables = brackets.load(self.store.get("brackets_override"), tracked=self.bracket_tables)
        ap_rows = tables["ap"]["rows"] if "ap" in tables else None
        out, season = [], None
        for t in tracks:
            if t["kind"] == "season":
                sv = _season_view(t, level)
                out.append(sv)
                if season is None:
                    season = {"track": sv["id"], "title": sv["title"], "seed": sv["seed"],
                              "done": sv["done"], "total": sv["total"],
                              "claimed": sv["claimed"], "unclaimed": sv["unclaimed"],
                              "next": sv["next"], "level": level}
                continue
            steps = []
            for s in t["steps"]:
                gates, ready = _gates(s, level, character["gs"], ap_rows)
                steps.append(dict(s, done=s["done_at"] is not None, gates=gates, ready=ready))
            done = sum(s["done"] for s in steps)
            out.append({"id": t["id"], "title": t["title"], "kind": t["kind"],
                        "seed_id": t["seed_id"], "steps": steps,
                        "done": done, "total": len(steps), "pct": pct(done, len(steps))})
        profile = self.profile_view(refresh=refresh)  # before lifeskill: a sync refresh lands
        return {"character": character, "tracks": out, "season": season,
                "profile": profile,
                "brackets": self._brackets(character, tables),
                "seeds": self._seed_list(tracks), "lifeskill": self.lifeskill_view()}

    def lifeskill_view(self):
        """Plan 042 Life & CP card from the newest history snapshot; never
        fetches. A broken milestone file leaves CP without a next target."""
        try:
            ms = load_cp_milestones(CP_MILESTONES)
        except ValueError as e:
            ms = {"error": str(e), "milestones": []}
        try:
            snap = self.history.main_snapshot() if self.history is not None else None
        except Exception:  # noqa: BLE001 - a broken history never breaks the Progress tab
            snap = None
        return lifeskill_card(snap, ms)

    def _seed_list(self, tracks):
        """Plan 034 "Add track" choices; `track` is the store track a seed made."""
        made = {t["seed_id"]: t["id"] for t in tracks if t.get("seed_id")}
        return [{"id": s["id"], "title": s["title"], "kind": s["kind"],
                 "steps": len(s["steps"]),
                 "unverified": sum(st["verified"] is False for st in s["steps"]),
                 "source": s["source"], "verified": s["verified"],
                 "added": s["id"] in made, "track": made.get(s["id"])} for s in self.seeds]

    def _brackets(self, character, tables):
        try:
            epoch = self.epoch() if self.epoch is not None else None
        except Exception:  # a broken feed never breaks the Progress tab
            epoch = None
        return brackets.summary(character["gs"], tables, epoch=epoch)

    def record_profile(self, snaps):
        """Plan 041 (ProfileClient.on_snapshot): append one refresh to the
        history; when the main character's level rose since the previous
        refresh, hand it to `on_level` (plan 011 marker). A failing marker
        never loses the snapshot."""
        if self.history is None:
            return
        prev = self.history.main_level()
        self.history.append(snaps)
        lvl = _main_level(snaps)
        if self.auto_level and self.on_level is not None and _is_int(lvl) and _is_int(prev) \
                and lvl > prev:
            self.on_level(lvl)

    def history_view(self, query):
        """GET /api/progress/history?field=a,b&days=N&character=X (parse_qs dict)."""
        fields = [f for v in query.get("field", []) for f in v.split(",") if f]
        if not fields or any(f not in SERIES_FIELDS for f in fields):
            raise ValueError(f"field must be one or more of {', '.join(SERIES_FIELDS)}")
        days = query.get("days", [str(HISTORY_DEFAULT_DAYS)])[0]
        if not re.fullmatch(r"\d{1,3}", days) or not 1 <= int(days) <= HISTORY_DAYS:
            raise ValueError(f"days must be an int 1..{HISTORY_DAYS}")
        character = query.get("character", [None])[0]
        if character is not None:
            character = _check_text(character, "character", MAX_NAME)
        fields = list(dict.fromkeys(fields))
        if self.history is None:
            return {"days": int(days), "character": None,
                    "series": {f: {"points": [], "first": None, "last": None, "delta": None}
                               for f in fields}}
        return self.history.series(fields, int(days), character)

    def source(self):
        """`/api/state` sources.profile: {updated, ttl_s, status}; never fetches."""
        res = self.profile.peek() if self.profile is not None else None
        pend = self.profile.is_pending() if self.profile is not None else False
        status = "off" if self.profile is not None and self._off_reason() is not None \
            else _profile_status(res, pend)
        return {"updated": res["fetched_at"] if res else None, "ttl_s": PROFILE_TTL,
                "status": status}

    # -- writes (each returns the GET body) -----------------------------------

    def set_character(self, arg):
        """Partial merge: {name?, cls?, level?, gs?: {ap?, aap?, dp?}}."""
        if not isinstance(arg, dict) or not arg:
            raise ValueError("character must be a non-empty object")
        extra = set(arg) - {"name", "cls", "level", "gs"}
        if extra:
            raise ValueError(f"unknown field(s): {', '.join(sorted(extra))}")
        patch = {}
        if "name" in arg:
            patch["name"] = _check_text(arg["name"], "name", MAX_NAME, allow_empty=True) or None
        if "cls" in arg:
            patch["cls"] = _check_text(arg["cls"], "cls", MAX_NAME)
        if "level" in arg:
            patch["level"] = _check_range(arg["level"], "level", *LEVEL_RANGE)
        gs = arg.get("gs")
        if "gs" in arg:
            if not isinstance(gs, dict) or not gs or set(gs) - set(GS_KEYS):
                raise ValueError("gs must be an object of ap/aap/dp")
            for k, v in gs.items():
                _check_range(v, k, *GS_RANGE)
        with self._lock:
            character, tracks = self._load()
            character.update(patch)
            if gs:
                character["gs"].update(gs)
            self._save(character, tracks)
        return self.view(refresh=False)

    def step(self, arg):
        """{track, step, done: bool}; marking done twice keeps the first stamp."""
        if not isinstance(arg, dict) or set(arg) != {"track", "step", "done"}:
            raise ValueError("step must be {track, step, done}")
        if not isinstance(arg["done"], bool):
            raise ValueError("done must be a bool")
        _check_id(arg["step"], "step")
        with self._lock:
            character, tracks = self._load()
            t = tracks[self._find(tracks, arg["track"])]
            items = t["objectives"] if t["kind"] == "season" else t["steps"]
            st = next((s for s in items if s["id"] == arg["step"]), None)
            if st is None:
                raise ValueError(f"unknown step: {arg['step']}")
            if not arg["done"]:
                level = self._level(character)
                if st.get("kind") == "level" and level is not None and level >= st["target"]:
                    raise ValueError(f"level {st['target']} reached (level {level}); "
                                     "fix the level to untick")
                st["done_at"] = None
                if "claimed_at" in st:
                    st["claimed_at"] = None
            elif st["done_at"] is None:
                st["done_at"] = _iso_now(self.clock)
            self._save(character, tracks)
        return self.view(refresh=False)

    def claim(self, arg):
        """{track, objective, claimed: bool}; only a done objective can be claimed
        (claiming itself is the operator's act in game; this is the mark)."""
        if not isinstance(arg, dict) or set(arg) != {"track", "objective", "claimed"}:
            raise ValueError("claim must be {track, objective, claimed}")
        if not isinstance(arg["claimed"], bool):
            raise ValueError("claimed must be a bool")
        with self._lock:
            character, tracks = self._load()
            _, o = self._season_obj(tracks, arg)
            if not arg["claimed"]:
                o["claimed_at"] = None
            elif o["done_at"] is None:
                raise ValueError(f"objective not done: {o['id']}")
            elif o["claimed_at"] is None:
                o["claimed_at"] = _iso_now(self.clock)
            self._save(character, tracks)
        return self.view(refresh=False)

    @staticmethod
    def _obj_fields(arg, required):
        """Validated {title?, kind?, target?, reward?} from an obj_add / obj_edit arg."""
        out = {}
        if "title" in arg:
            out["title"] = _check_text(arg["title"], "title", MAX_TITLE)
        if "kind" in arg:
            if arg["kind"] not in OBJ_KINDS:
                raise ValueError(f"kind must be one of {', '.join(OBJ_KINDS)}")
            out["kind"] = arg["kind"]
        if "target" in arg:
            if arg["target"] is not None and not _is_int(arg["target"]):
                raise ValueError("target must be an int or null")
            out["target"] = arg["target"]
        if "reward" in arg:
            out["reward"] = _check_text(arg["reward"], "reward", MAX_REWARD, allow_empty=True)
        missing = [k for k in required if k not in out]
        if missing:
            raise ValueError(f"missing field(s): {', '.join(missing)}")
        return out

    @staticmethod
    def _check_target(o):
        if not _target_ok(o["kind"], o["target"]):
            if o["kind"] == "level":
                raise ValueError(f"level objective needs target {LEVEL_RANGE[0]}..{LEVEL_RANGE[1]}")
            if o["kind"] == "gear":
                raise ValueError(f"gear target must be null or {GEAR_RANGE[0]}..{GEAR_RANGE[1]}")
            raise ValueError(f"{o['kind']} objective takes no target")

    def obj_add(self, arg):
        """{track, title, kind, target?, reward?} appended to a season track."""
        if not isinstance(arg, dict) or not {"track", "title", "kind"} <= set(arg) \
                or set(arg) - {"track", "title", "kind", "target", "reward"}:
            raise ValueError("obj_add must be {track, title, kind, target?, reward?}")
        f = self._obj_fields(arg, ("title", "kind"))
        with self._lock:
            character, tracks = self._load()
            t = tracks[self._find(tracks, arg["track"])]
            if t["kind"] != "season":
                raise ValueError(f"not a season track: {t['id']}")
            if len(t["objectives"]) >= MAX_STEPS:
                raise ValueError(f"at most {MAX_STEPS} objectives")
            o = _objective(_unique(slug(f["title"]), {o["id"] for o in t["objectives"]}),
                           f["title"], f["kind"], f.get("target"), f.get("reward", ""))
            self._check_target(o)
            t["objectives"].append(o)
            t["seed"] = False
            self._save(character, tracks)
        return self.view(refresh=False)

    def obj_edit(self, arg):
        """{track, objective, title?, kind?, target?, reward?}; done/claim marks kept."""
        keys = {"title", "kind", "target", "reward"}
        if not isinstance(arg, dict) or not {"track", "objective"} <= set(arg) \
                or set(arg) - keys - {"track", "objective"} or not set(arg) & keys:
            raise ValueError("obj_edit must be {track, objective, title?|kind?|target?|reward?}")
        f = self._obj_fields(arg, ())
        with self._lock:
            character, tracks = self._load()
            t, o = self._season_obj(tracks, arg)
            new = dict(o, **f)
            if "kind" in f and "target" not in f and not _target_ok(new["kind"], new["target"]) \
                    and new["kind"] != "level":
                new["target"] = None  # a kind change drops a target the new kind cannot take
            self._check_target(new)
            o.update(new)
            t["seed"] = False
            self._save(character, tracks)
        return self.view(refresh=False)

    def obj_del(self, arg):
        """{track, objective}."""
        if not isinstance(arg, dict) or set(arg) != {"track", "objective"}:
            raise ValueError("obj_del must be {track, objective}")
        with self._lock:
            character, tracks = self._load()
            t, o = self._season_obj(tracks, arg)
            t["objectives"].remove(o)
            t["seed"] = False
            self._save(character, tracks)
        return self.view(refresh=False)

    def add_track(self, arg):
        """{title, kind, steps: [titles]}."""
        if not isinstance(arg, dict) or set(arg) != {"title", "kind", "steps"}:
            raise ValueError("add_track must be {title, kind, steps}")
        title = _check_text(arg["title"], "title", MAX_TITLE)
        if arg["kind"] not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}")
        steps = arg["steps"]
        if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
            raise ValueError(f"steps must be a list of 1..{MAX_STEPS} titles")
        steps = [_check_text(s, "step title", MAX_TITLE) for s in steps]
        with self._lock:
            character, tracks = self._load()
            if len(tracks) >= MAX_TRACKS:
                raise ValueError(f"at most {MAX_TRACKS} tracks")
            tid = _unique(slug(title), {t["id"] for t in tracks})
            if arg["kind"] == "season":
                tracks.append({"id": tid, "title": title, "kind": "season", "seed": False,
                               "objectives": _make_objectives(steps)})
            else:
                tracks.append({"id": tid, "title": title, "kind": arg["kind"],
                               "steps": _make_steps(steps)})
            self._save(character, tracks)
        return self.view(refresh=False)

    def track_seed(self, arg):
        """Seed id -> a plan 004 custom track with the seed's steps copied (later
        edits are the operator's). A seed already in the store is a no-op."""
        if not isinstance(arg, str) or not SEED_ID_RE.match(arg):
            raise ValueError("track_seed must be a seed id")
        seed = next((s for s in self.seeds if s["id"] == arg), None)
        if seed is None:
            raise ValueError(f"unknown track seed: {arg}")
        with self._lock:
            character, tracks = self._load()
            if not any(t.get("seed_id") == arg for t in tracks):
                if len(tracks) >= MAX_TRACKS:
                    raise ValueError(f"at most {MAX_TRACKS} tracks")
                tracks.append({"id": _unique(slug(seed["title"]), {t["id"] for t in tracks}),
                               "title": seed["title"], "kind": seed["kind"], "seed_id": arg,
                               "steps": [dict(st, done_at=None) for st in seed["steps"]]})
                self._save(character, tracks)
        return self.view(refresh=False)

    def brackets_set(self, arg):
        """{table: {source, verified, note, rows} | null, ...}: replace a bracket
        table with an operator copy, or null to fall back to the tracked one."""
        if not isinstance(arg, dict) or not arg or set(arg) - set(brackets.TABLES):
            raise ValueError(f"brackets_set must be an object of {', '.join(brackets.TABLES)}")
        patch = {k: None if v is None else brackets.validate_table(v) for k, v in arg.items()}
        with self._lock:
            doc = brackets.clean_overrides(self.store.get("brackets_override"))
            for k, v in patch.items():
                if v is None:
                    doc.pop(k, None)
                else:
                    doc[k] = v
            self.store.put("brackets_override", doc)
        return self.view(refresh=False)

    def remove_track(self, tid):
        with self._lock:
            character, tracks = self._load()
            del tracks[self._find(tracks, tid)]
            self._save(character, tracks)
        return self.view(refresh=False)
