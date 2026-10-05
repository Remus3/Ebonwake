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
import json
import re
import threading
import time
import urllib.parse
from pathlib import Path

from .httpcache import CachedClient, Pending, UpstreamError, freshness
from .today import slug

DEFAULT_BASE = "https://api.cutepap.us/community/v1"
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
LEVEL_RANGE = (1, 70)
GS_RANGE = (0, 999)
GS_KEYS = ("ap", "aap", "dp")
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
    """BDO-REST-API adventurer search by family name, region NA, GET only."""

    def __init__(self, family, base_url=None, fetch=None, clock=time.time, cache_dir=None):
        super().__init__(fetch=fetch, clock=clock,
                         cache_dir=cache_dir if cache_dir is not None else _DEFAULT_CACHE)
        self.family = family
        base = base_url if isinstance(base_url, str) and base_url.startswith("https://") \
            else DEFAULT_BASE
        self.base = base.rstrip("/")
        # Cache file name never spells the family name.
        h = hashlib.sha256(f"{REGION}:{family.lower()}".encode("utf-8")).hexdigest()[:16]
        self.key = f"profile_{h}"

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
        # profileTarget is an opaque account-level id: never cached or served.
        return {"family": pick["familyName"],
                "region": pick.get("region") if isinstance(pick.get("region"), str) else REGION,
                "guild": guild.get("name") if isinstance(guild, dict)
                and isinstance(guild.get("name"), str) else None,
                "characters": _chars(pick.get("characters"))}

    def get(self):
        return self.cached_get(self.key, PROFILE_TTL, self._download)

    def peek(self):
        return super().peek(self.key, PROFILE_TTL)

    def refresh(self, spawn=None):
        """Background refresh, at most one in flight; never blocks the caller."""
        return self.refresh_async(self.key, PROFILE_TTL, self._download, spawn)

    def is_pending(self):
        return self.pending(self.key)


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
        steps.append({"id": st["id"], "title": st["title"],
                      "done_at": _parse_iso(st.get("done_at"))})
    if kind != "season":
        return {"id": tid, "title": title, "kind": kind, "steps": steps}
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
    level, the higher one auto-ticks level objectives."""

    def __init__(self, store, profile_client=None, clock=time.time, spawn=None, level=None):
        self.store = store
        self.profile = profile_client
        self.clock = clock
        self.spawn = spawn  # background refresh runner (None = daemon thread)
        self.level = level
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
        res = self.profile.peek()
        status = _profile_status(res, self.profile.is_pending())
        if res is None:
            return {"data": None, "freshness": None, "status": status}
        return {"data": res["data"], "freshness": freshness(res), "status": status}

    def view(self, refresh=True):
        """GET /api/progress body; POST answers pass refresh=False (peek only)."""
        with self._lock:
            character, tracks = self._load()
            level = self._level(character)
            if self._auto_tick(tracks, level):
                self._save(character, tracks)
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
            steps = [{"id": s["id"], "title": s["title"], "done": s["done_at"] is not None,
                      "done_at": s["done_at"]} for s in t["steps"]]
            done = sum(s["done"] for s in steps)
            out.append({"id": t["id"], "title": t["title"], "kind": t["kind"], "steps": steps,
                        "done": done, "total": len(steps), "pct": pct(done, len(steps))})
        return {"character": character, "tracks": out, "season": season,
                "profile": self.profile_view(refresh=refresh)}

    def source(self):
        """`/api/state` sources.profile: {updated, ttl_s, status}; never fetches."""
        res = self.profile.peek() if self.profile is not None else None
        pend = self.profile.is_pending() if self.profile is not None else False
        return {"updated": res["fetched_at"] if res else None, "ttl_s": PROFILE_TTL,
                "status": _profile_status(res, pend)}

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

    def remove_track(self, tid):
        with self._lock:
            character, tracks = self._load()
            del tracks[self._find(tracks, tid)]
            self._save(character, tracks)
        return self.view(refresh=False)
