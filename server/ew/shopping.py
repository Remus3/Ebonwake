"""Shopping list (plan 037): plan 007 open steps x plan 035 expected attempts x
per-attempt materials -> quantities per item id, priced from the plan 002
market cache, and a can-afford-by date from plan 005's average silver/h.

Math only. Prices come through an injected `quote(item_id)` that reads the
cache and never fetches; a missing price leaves the line `null` with note
`missing price`. Nothing here touches the game or places a market order: the
dashboard's "watch" button only edits EW's own watchlist.

Per open step the levels (current, target] are each one plan 035 rate row of
the step's gear family (set by the operator, else guessed from the item text).
FS is held constant across the levels (as in the EV panel). Expected
quantities are summed per item id over every level and step, then rounded up
to whole items.
"""

import datetime as _dt
import math
import threading
import time

from . import enhance
from .deadeye import LEVELS

MAX_SILVER = 10 ** 15
DEFAULT_HOURS = 3
MAX_HOURS = 24
MAX_STEP_SETTINGS = 200
_EPS = 1e-9


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _ok_silver(v):
    return _is_int(v) and 0 <= v <= MAX_SILVER


def _ok_hours(v):
    return _is_num(v) and 0 < v <= MAX_HOURS


def _ok_fs(v):
    return _is_int(v) and 0 <= v <= enhance.MAX_FS


def family_guess(item, families):
    """A gear family named inside the operator's item text (`_` read as a
    space, mirrors ewcore enhanceFamilyGuess), else None."""
    low = item.lower() if isinstance(item, str) else ""
    for f in families:
        if f and f.replace("_", " ") in low:
            return f
    return None


def average_silver_per_h(spots):
    """Minute-weighted silver/h over plan 005 spot rows, floored; None when no
    minutes are logged."""
    minutes = silver = 0
    for s in spots if isinstance(spots, list) else []:
        m, r = (s.get("minutes"), s.get("silver_per_h")) if isinstance(s, dict) else (None, None)
        if _is_int(m) and m > 0 and _is_num(r) and r >= 0:
            minutes += m
            silver += r * m
    return int(silver // minutes) if minutes else None


def levels_between(current, target):
    """Levels a step climbs through: (current, target], each the level one
    attempt reaches (the plan 035 row `step` key)."""
    a, b = LEVELS.index(current), LEVELS.index(target)
    return list(LEVELS[a + 1:b + 1])


class ShoppingService:
    """Store domain `shopping`: {"silver_on_hand": int, "hours_per_day": num,
    "steps": {step_id: {family?, fs?, crons?}}}."""

    def __init__(self, store, plan, enhance, quote, name, silver_per_h, watched=None,
                 clock=time.time):
        self.store = store
        self.plan = plan                    # () -> plan 007 view rows
        self.enhance = enhance              # plan 035 EnhanceService
        self.quote = quote                  # id -> {price, preorder}, never fetches
        self.name = name                    # id -> name or None (plan 028 index)
        self.silver_per_h = silver_per_h    # () -> int or None (plan 005)
        self.watched = watched or (lambda: set())
        self.clock = clock
        self._lock = threading.Lock()

    # -- stored settings (corrupt entries degrade, never raise) ---------------

    def _load(self):
        doc = self.store.get("shopping")
        doc = doc if isinstance(doc, dict) else {}
        on_hand = doc.get("silver_on_hand")
        hours = doc.get("hours_per_day")
        raw = doc.get("steps") if isinstance(doc.get("steps"), dict) else {}
        steps = {}
        for sid, s in raw.items():
            if not isinstance(s, dict):
                continue
            c = {}
            if isinstance(s.get("family"), str) and enhance.FAMILY_RE.fullmatch(s["family"]):
                c["family"] = s["family"]
            if _ok_fs(s.get("fs")):
                c["fs"] = s["fs"]
            if isinstance(s.get("crons"), bool):
                c["crons"] = s["crons"]
            steps[sid] = c
        return {"silver_on_hand": on_hand if _ok_silver(on_hand) else 0,
                "hours_per_day": hours if _ok_hours(hours) else DEFAULT_HOURS,
                "steps": steps}

    # -- math ------------------------------------------------------------------

    def _families(self, rows):
        return sorted({r["family"] for r in rows})

    def _step(self, st, conf, rows, families, cron_id, hard_cap, need):
        """One open plan step -> its breakdown; adds expected qty into `need`."""
        family = conf.get("family")
        source = "set" if family else None
        if family is None:
            family = family_guess(st["item"], families)
            source = "guess" if family else None
        fs, crons = conf.get("fs", 0), conf.get("crons", False)
        out = {"id": st["id"], "item": st["item"], "current": st["current"],
               "target": st["target"], "family": family, "family_source": source,
               "fs": fs, "crons": crons, "levels": [], "note": None}
        if family is None:
            out["note"] = "no gear family"
            return out
        for lv in levels_between(st["current"], st["target"]):
            row = rows.get((family, lv))
            entry = {"step": lv, "chance_pct": None, "approx": None, "attempts_mean": None,
                     "unverified_used": [], "note": None}
            out["levels"].append(entry)
            if row is None:
                entry["note"] = "no rate row"
                continue
            c = enhance.chance(row, fs, hard_cap)
            entry.update(chance_pct=c["pct"], approx=c["approx"],
                         unverified_used=enhance._unverified_used(row, crons))
            if c["pct"] is None:
                entry["note"] = "no chance data at this FS"
                continue
            try:
                a = enhance.attempts(c["pct"] / 100, row.get("agris_threshold"))
            except (OverflowError, ValueError):
                a = {"mean": math.inf, "crons_attempts": math.inf}
            if not (math.isfinite(a["mean"]) and math.isfinite(a["crons_attempts"])):
                entry["note"] = "chance too small to total"  # a 1e-320 % override
                continue
            entry["attempts_mean"] = a["mean"]
            for item_id, qty in row.get("materials", []):
                need[item_id] = need.get(item_id, 0) + qty * a["mean"]
            per = row.get("crons_per_attempt") or 0
            if crons and per:
                need[cron_id] = need.get(cron_id, 0) + per * a["crons_attempts"]
        return out

    def _line(self, item_id, expected, watched):
        q = self.quote(item_id) or {}
        unit = q.get("price") if _is_int(q.get("price")) and q.get("price") > 0 else None
        qty = math.ceil(expected - _EPS)
        return {"id": item_id, "name": self.name(item_id), "qty": qty, "expected": expected,
                "unit": unit, "total": qty * unit if unit is not None else None,
                "preorder": q.get("preorder"), "watched": item_id in watched,
                "note": None if unit is not None else "missing price"}

    def _rate(self):
        try:
            r = self.silver_per_h()
        except Exception:  # noqa: BLE001 - the afford date is an extra, never fatal
            return None
        return r if _is_num(r) and r >= 0 else None

    def _afford(self, total, conf):
        today = _dt.datetime.fromtimestamp(self.clock(), _dt.timezone.utc).date()
        rate = self._rate()
        hours = conf["hours_per_day"]
        out = {"need": None, "silver_per_h": rate, "hours_per_day": hours, "per_day": None,
               "days": None, "reason": None}
        if total is None:
            out["reason"] = "missing price"
            return None, out
        out["need"] = need = max(0, total - conf["silver_on_hand"])
        if need == 0:
            out["days"] = 0
            return today.isoformat(), out
        if not rate:
            out["reason"] = "no grind silver/h logged"
            return None, out
        out["per_day"] = per_day = rate * hours
        out["days"] = days = math.ceil(need / per_day)
        try:
            return (today + _dt.timedelta(days=days)).isoformat(), out
        except OverflowError:
            out["reason"] = "beyond the calendar"
            return None, out

    # -- reads -----------------------------------------------------------------

    def view(self):
        """GET /api/deadeye/shopping body."""
        conf = self._load()
        rows_list = self.enhance.rows()
        rows = {(r["family"], r["step"]): r for r in rows_list}
        families = self._families(rows_list)
        table = self.enhance.table
        cron_id = table["cron_item_id"]
        hard_cap = table.get("hard_cap_pct", enhance.HARD_CAP_PCT)
        need, steps = {}, []
        for st in self.plan():
            if st.get("done"):
                continue
            steps.append(self._step(st, conf["steps"].get(st["id"], {}), rows, families,
                                    cron_id, hard_cap, need))
        try:
            watched = set(self.watched())
        except Exception:  # noqa: BLE001 - the watch flag is an extra, never fatal
            watched = set()
        lines = [self._line(i, e, watched) for i, e in need.items() if e > _EPS]
        # Unpriced lines first (they need the operator's eye), then by total.
        lines.sort(key=lambda ln: (ln["total"] is not None, -(ln["total"] or 0), ln["id"]))
        missing = sorted(ln["id"] for ln in lines if ln["total"] is None)
        priced = sum(ln["total"] for ln in lines if ln["total"] is not None)
        total = None if missing else priced
        by, afford = self._afford(total, conf)
        return {"lines": lines, "total": total, "priced_total": priced,
                "missing_prices": missing, "can_afford_by": by, "afford": afford,
                "steps": steps, "families": families, "cron_item_id": cron_id,
                "settings": {"silver_on_hand": conf["silver_on_hand"],
                             "hours_per_day": conf["hours_per_day"]}}

    # -- writes (each returns the GET body) -----------------------------------

    def _save(self, conf):
        self.store.put("shopping", conf)

    def set(self, arg):
        """`shop_set` {silver_on_hand?, hours_per_day?} (at least one)."""
        if (not isinstance(arg, dict) or not arg
                or not set(arg) <= {"silver_on_hand", "hours_per_day"}):
            raise ValueError("shop_set must be {silver_on_hand?, hours_per_day?} (at least one)")
        if "silver_on_hand" in arg and not _ok_silver(arg["silver_on_hand"]):
            raise ValueError(f"silver_on_hand must be an int in 0..{MAX_SILVER}")
        if "hours_per_day" in arg and not _ok_hours(arg["hours_per_day"]):
            raise ValueError(f"hours_per_day must be a number in (0, {MAX_HOURS}]")
        with self._lock:
            conf = self._load()
            conf.update(arg)
            self._save(conf)
        return self.view()

    def step(self, arg):
        """`shop_step` {id, family? (null = guess), fs?, crons?} (at least one)."""
        if (not isinstance(arg, dict) or "id" not in arg or len(arg) < 2
                or not set(arg) <= {"id", "family", "fs", "crons"}):
            raise ValueError("shop_step must be {id, family?, fs?, crons?} (at least one)")
        sid = arg["id"]
        if not isinstance(sid, str) or sid not in {s["id"] for s in self.plan()}:
            raise ValueError(f"unknown step: {sid}")
        fam = arg.get("family")
        if "family" in arg and fam is not None and (
                not isinstance(fam, str) or fam not in self._families(self.enhance.rows())):
            raise ValueError("family must be a rate table family or null")
        if "fs" in arg and not _ok_fs(arg["fs"]):
            raise ValueError(f"fs must be an int in 0..{enhance.MAX_FS}")
        if "crons" in arg and not isinstance(arg["crons"], bool):
            raise ValueError("crons must be true or false")
        with self._lock:
            conf = self._load()
            live = {s["id"] for s in self.plan()}
            steps = {k: v for k, v in conf["steps"].items() if k in live}  # prune deleted
            cur = dict(steps.get(sid, {}))
            for k in ("family", "fs", "crons"):
                if k in arg:
                    if arg[k] is None:
                        cur.pop(k, None)
                    else:
                        cur[k] = arg[k]
            steps[sid] = cur
            if len(steps) > MAX_STEP_SETTINGS:
                raise ValueError(f"at most {MAX_STEP_SETTINGS} step settings")
            conf["steps"] = steps
            self._save(conf)
        return self.view()
