"""Enhancement EV calculator (plan 035): per-step chance tables, expected
attempts and cost, Agris pity cap.

Math only, on the tracked `data/enhance_rates.json` rows (sourced, research
0004 s1) merged with operator overrides (store domain `enhance`). Prices come
from the plan 002 market cache through an injected `prices(item_id)` callable
that never fetches. Nothing here touches the game or a market action.

Chance model per row (`step` = the level the attempt reaches):
- inside the span of explicit `points`: linear interpolation (exact);
- fs == 0 with `base_pct`, or fs == `softcap_fs`: the table value (exact);
- otherwise base * (1 + 0.1 * fs) capped at `max_pct_at_softcap`, then
  +base * 0.02 per FS beyond the soft cap (approx). With no `base_pct` the
  base is derived as max / (1 + 0.1 * softcap_fs);
- always at most `hard_cap_pct` (90).

Attempts at one step are geometric in the per-attempt chance p (FS held
constant). The Agris threshold T truncates it: after T failures the next
attempt is guaranteed, so at most T + 1 attempts (`pity_cap`). Crons are
spent on every attempt except a guaranteed one.
"""

import json
import math
import re
import threading
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parent / "data" / "enhance_rates.json"
STEPS = tuple(f"+{n}" for n in range(1, 16)) + (
    "PRI", "DUO", "TRI", "TET", "PEN", "HEX", "SEP", "OCT", "NOV", "DEC")
FAMILY_RE = re.compile(r"[a-z][a-z0-9_]{0,23}")  # always fullmatch: no trailing newline
MAX_FS = 999
MAX_OVERRIDES = 200
MAX_ID = 2 ** 31 - 1
HARD_CAP_PCT = 90
REQUIRED = ("family", "step", "softcap_fs", "max_pct_at_softcap", "source", "verified")
OPTIONAL = ("base_pct", "points", "crons_per_attempt", "agris_threshold", "materials",
            "unverified")
_FIELDS = REQUIRED + OPTIONAL

_table_cache = None


def load_table():
    """Tracked table, read once and validated (a bad row is a defect, it raises)."""
    global _table_cache
    if _table_cache is None:
        doc = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        doc["rows"] = [validate_row(r) for r in doc["rows"]]
        _table_cache = doc
    return _table_cache


# -- validation ----------------------------------------------------------------

def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _pct(v, name):
    if not _is_num(v) or not 0 < v <= HARD_CAP_PCT:
        raise ValueError(f"{name} must be a number in (0, {HARD_CAP_PCT}]")
    return v


def _int(v, name, lo, hi):
    if not _is_int(v) or not lo <= v <= hi:
        raise ValueError(f"{name} must be an int in {lo}..{hi}")
    return v


def validate_row(r):
    """Returns `r` unchanged when it is a well-formed rate row, else ValueError."""
    if not isinstance(r, dict):
        raise ValueError("row must be an object")
    if not set(REQUIRED) <= set(r) or not set(r) <= set(_FIELDS):
        raise ValueError(f"row must be {{{', '.join(REQUIRED)}, "
                         f"{', '.join(k + '?' for k in OPTIONAL)}}}")
    if not isinstance(r["family"], str) or not FAMILY_RE.fullmatch(r["family"]):
        raise ValueError("family must be 1-24 of a-z 0-9 _ (starting a-z)")
    if r["step"] not in STEPS:
        raise ValueError(f"step must be one of {', '.join(STEPS)}")
    if (r["softcap_fs"] is None) != (r["max_pct_at_softcap"] is None):
        raise ValueError("softcap_fs and max_pct_at_softcap are both set or both null")
    if r["softcap_fs"] is not None:
        _int(r["softcap_fs"], "softcap_fs", 0, MAX_FS)
        _pct(r["max_pct_at_softcap"], "max_pct_at_softcap")
    if "base_pct" in r:
        _pct(r["base_pct"], "base_pct")
    if "points" in r:
        pts = r["points"]
        if not isinstance(pts, list) or not pts:
            raise ValueError("points must be a non-empty list of [fs, pct]")
        last = -1
        for p in pts:
            if not isinstance(p, list) or len(p) != 2:
                raise ValueError("points must be a non-empty list of [fs, pct]")
            _int(p[0], "points fs", 0, MAX_FS)
            _pct(p[1], "points pct")
            if p[0] <= last:
                raise ValueError("points fs must be strictly increasing")
            last = p[0]
    if "crons_per_attempt" in r:
        _int(r["crons_per_attempt"], "crons_per_attempt", 0, 10 ** 6)
    if "agris_threshold" in r:
        _int(r["agris_threshold"], "agris_threshold", 1, 1000)
    if "materials" in r:
        mats = r["materials"]
        if not isinstance(mats, list):
            raise ValueError("materials must be a list of [item_id, qty]")
        for m in mats:
            if not (isinstance(m, list) and len(m) == 2 and _is_int(m[0]) and 0 <= m[0] <= MAX_ID
                    and _is_num(m[1]) and m[1] > 0):
                raise ValueError("materials must be a list of [item_id, qty > 0]")
    if not isinstance(r["source"], str) or not 1 <= len(r["source"]) <= 300:
        raise ValueError("source must be 1..300 characters")
    if not isinstance(r["verified"], bool):
        raise ValueError("verified must be true or false")
    if "unverified" in r:
        u = r["unverified"]
        if not isinstance(u, list) or not all(isinstance(k, str) and k in _FIELDS for k in u):
            raise ValueError("unverified must list row field names")
    return r


# -- math ----------------------------------------------------------------------

def chance(row, fs, hard_cap=HARD_CAP_PCT):
    """{pct, approx}: success chance in percent at `fs`, or pct None when the
    row has no data that reaches `fs`."""
    pts = row.get("points")
    if pts and pts[0][0] <= fs <= pts[-1][0]:
        for (f0, p0), (f1, p1) in zip(pts, pts[1:]):
            if f0 <= fs <= f1:
                pct = p0 if fs == f0 else p1 if fs == f1 else p0 + (p1 - p0) * (fs - f0) / (f1 - f0)
                return {"pct": min(pct, hard_cap), "approx": False}
        return {"pct": min(pts[0][1], hard_cap), "approx": False}  # single point
    soft, top, base = row.get("softcap_fs"), row.get("max_pct_at_softcap"), row.get("base_pct")
    if soft is not None:
        if fs == soft:
            return {"pct": min(top, hard_cap), "approx": False}
        if fs == 0 and base is not None:
            return {"pct": min(base, hard_cap), "approx": False}
        b = base if base is not None else top / (1 + 0.1 * soft)
        pct = min(b * (1 + 0.1 * fs), top) if fs < soft else top + b * 0.02 * (fs - soft)
        return {"pct": min(pct, hard_cap), "approx": True}
    if base is not None:
        if fs == 0:
            return {"pct": min(base, hard_cap), "approx": False}
        return {"pct": min(base * (1 + 0.1 * fs), hard_cap), "approx": True}
    return {"pct": None, "approx": True}


def _p90(p):
    """Smallest k with 1 - (1 - p)^k >= 0.9 (geometric)."""
    if p >= 0.9:
        return 1
    k = max(1, math.ceil(math.log(0.1) / math.log1p(-p)))
    while k > 1 and 1 - (1 - p) ** (k - 1) >= 0.9:  # float guard on the ceil
        k -= 1
    while 1 - (1 - p) ** k < 0.9:
        k += 1
    return k


def attempts(p, threshold):
    """{mean, p90, cap, crons_attempts} for per-attempt chance 0 < p <= 1 and
    an Agris threshold (None = no pity). `crons_attempts` is the expected
    number of non-guaranteed attempts (the ones crons are spent on)."""
    if not _is_num(p) or not 0 < p <= 1:
        raise ValueError("p must be in (0, 1]")
    q = 1 - p
    geo = _p90(p)
    if threshold is None:
        return {"mean": 1 / p, "p90": geo, "cap": None, "crons_attempts": 1 / p}
    t = threshold
    return {"mean": (1 - q ** (t + 1)) / p, "p90": min(geo, t + 1), "cap": t + 1,
            "crons_attempts": (1 - q ** t) / p}


def bill_ids(row, use_crons, cron_id):
    """Item ids whose price the cost of one attempt needs."""
    ids = [m[0] for m in row.get("materials", [])]
    if use_crons and row.get("crons_per_attempt") and cron_id not in ids:
        ids.append(cron_id)
    return ids


def expected(row, fs, use_crons, prices, cron_id, hard_cap=HARD_CAP_PCT):
    """Expected attempts and silver for one step at constant `fs`. `prices` maps
    item id -> silver or None (missing -> cost null, never a fetch)."""
    c = chance(row, fs, hard_cap)
    t = row.get("agris_threshold")
    crons = (row.get("crons_per_attempt") or 0) if use_crons else 0
    out = {"chance_pct": c["pct"], "approx": c["approx"], "p": None, "attempts_mean": None,
           "attempts_p90": None, "pity_cap": t + 1 if t else None, "agris_threshold": t,
           "crons_per_attempt": crons, "crons_mean": None, "cost_mean_silver": None,
           "missing_prices": [], "cost_note": None}
    ids = bill_ids(row, use_crons, cron_id)
    out["missing_prices"] = [i for i in ids if not _is_num(prices.get(i))]
    if c["pct"] is None:
        out["cost_note"] = "no chance data at this FS"
        return out
    p = c["pct"] / 100
    a = attempts(p, t)
    out.update(p=p, attempts_mean=a["mean"], attempts_p90=a["p90"],
               crons_mean=crons * a["crons_attempts"])
    if not ids:
        out["cost_note"] = "no priced materials in the table for this step"
    elif out["missing_prices"]:
        out["cost_note"] = "no cached market price"
    else:
        per = sum(m[1] * prices[m[0]] for m in row.get("materials", []))
        out["cost_mean_silver"] = per * a["mean"] + out["crons_mean"] * (
            prices[cron_id] if crons else 0)
    return out


def _unverified_used(row, use_crons):
    """Unverified fields this evaluation actually rests on: a preview cron count
    does not taint a crons-off result. A row marked unverified with no field
    list is unverified as a whole (`row`)."""
    fields = row.get("unverified")
    if not fields:
        return [] if row["verified"] else ["row"]
    return [f for f in fields if f != "crons_per_attempt" or use_crons]


# -- service -------------------------------------------------------------------

def _key(r):
    return (r["family"], r["step"])


class EnhanceService:
    """Store domain `enhance`: {"rates": [row]} operator overrides; a row with
    the same (family, step) as a tracked row replaces it."""

    def __init__(self, store, prices, table=None):
        self.store = store
        self.prices = prices
        self.table = table if table is not None else load_table()
        self._lock = threading.Lock()

    def _overrides(self):
        raw = self.store.get("enhance").get("rates")
        out = {}
        for r in raw if isinstance(raw, list) else []:
            try:
                out[_key(validate_row(r))] = r
            except (ValueError, TypeError):
                continue  # corrupt entry degrades, never a 500
        return out

    def rows(self):
        merged = {_key(r): r for r in self.table["rows"]}
        for k, r in self._overrides().items():
            merged[k] = dict(r, override=True)
        return list(merged.values())

    def row(self, family, step):
        for r in self.rows():
            if r["family"] == family and r["step"] == step:
                return r
        raise KeyError(f"no rate row for {family} {step}")

    def chance(self, family, step, fs):
        return chance(self.row(family, step), fs, self.table.get("hard_cap_pct", HARD_CAP_PCT))

    def view(self):
        """GET /api/deadeye/enhance (no query): the effective table."""
        rows = sorted(self.rows(), key=lambda r: (r["family"], STEPS.index(r["step"])))
        return {"families": sorted({r["family"] for r in rows}), "steps": list(STEPS),
                "rows": rows, "max_fs": MAX_FS, "cron_item_id": self.table["cron_item_id"]}

    def evaluate(self, family, step, fs, use_crons):
        r = self.row(family, step)
        cron_id = self.table["cron_item_id"]
        prices = {i: self.prices(i) for i in bill_ids(r, use_crons, cron_id)}
        out = expected(r, fs, use_crons, prices, cron_id,
                       self.table.get("hard_cap_pct", HARD_CAP_PCT))
        return dict(out, family=family, step=step, fs=fs, crons=use_crons,
                    prices=prices, source=r["source"], verified=r["verified"],
                    unverified=r.get("unverified", []), unverified_used=_unverified_used(r, use_crons),
                    override=bool(r.get("override")))

    def query(self, q):
        """Parsed query dict (parse_qs) -> evaluate body or the table view."""
        def one(k):
            v = q.get(k)
            return v[0] if v else None
        family, step = one("family"), one("step")
        if family is None and step is None:
            return self.view()
        if family is None or step is None:
            raise ValueError("family and step are both required")
        fs_s, crons_s = one("fs") or "0", one("crons") or "0"
        if not re.fullmatch(r"[0-9]{1,3}", fs_s):
            raise ValueError(f"fs must be an int in 0..{MAX_FS}")
        if crons_s not in ("0", "1"):
            raise ValueError("crons must be 0 or 1")
        try:
            return self.evaluate(family, step, int(fs_s), crons_s == "1")
        except KeyError as e:
            raise ValueError(e.args[0]) from None

    # -- writes ----------------------------------------------------------------

    def override_set(self, row):
        # A row read back from GET carries `override: true`; that flag is not stored.
        r = validate_row({k: v for k, v in row.items() if k != "override"}
                         if isinstance(row, dict) else row)
        with self._lock:
            ov = self._overrides()
            if _key(r) not in ov and len(ov) >= MAX_OVERRIDES:
                raise ValueError(f"at most {MAX_OVERRIDES} overrides")
            ov[_key(r)] = r
            self.store.put("enhance", {"rates": list(ov.values())})
        return self.view()

    def override_del(self, arg):
        if not (isinstance(arg, dict) and set(arg) == {"family", "step"}
                and isinstance(arg["family"], str) and isinstance(arg["step"], str)):
            raise ValueError("rate_del must be {family, step} (strings)")
        with self._lock:
            ov = self._overrides()
            k = (arg["family"], arg["step"])
            if k not in ov:
                raise ValueError(f"no override for {arg['family']} {arg['step']}")
            del ov[k]
            self.store.put("enhance", {"rates": list(ov.values())})
        return self.view()
