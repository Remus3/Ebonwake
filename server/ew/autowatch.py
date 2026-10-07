"""Self-curating market watch (plan 071).

The auto-watch set is the union of the plan 037 shopping list, the top
LOOT_TOP loot items by value of the current / last plan 039 spot and the
market-bought plan 054 recipe inputs, ranked by silver at stake and capped at
CAP. Silver at stake per source: a shopping line's total (qty x unit), a loot
item's cached price x the count logged in that spot's newest loot session
(1 when none), a recipe input's unit x qty x RECIPE_CRAFTS (the crafting
tab's per-1,000 batch). An id on several sources sums its stakes; an
unpriced id ranks with stake 0.

Auto entries carry `auto: true`; manual entries always stay; an auto entry
the operator removes is remembered and never re-added (market.Watchlist).
Prices come from caches only - nothing here fetches, reads the game or
places an order.
"""

import threading
import time

from . import market
from .market import MIN_BAND_N, band_thresholds  # noqa: F401 - plan 071 surface

CAP = 15
LOOT_TOP = 5
RECIPE_CRAFTS = 1000
SYNC_EVERY_S = 60  # the watch refreshes often; the sources change slowly


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _ok_id(v):
    return _is_int(v) and 0 < v <= market.MAX_ID


def _silver(v):
    return v if _is_int(v) and v > 0 else 0


def shopping_stakes(view):
    """Plan 037 view -> [(id, line total)]."""
    lines = view.get("lines") if isinstance(view, dict) else None
    return [(ln["id"], _silver(ln.get("total")))
            for ln in (lines if isinstance(lines, list) else [])
            if isinstance(ln, dict) and _ok_id(ln.get("id"))]


def loot_stakes(cands, price):
    """GrindService.loot_candidates() -> top LOOT_TOP [(id, price x count)]."""
    items = cands.get("items") if isinstance(cands, dict) else None
    out = {}
    for it in items if isinstance(items, list) else []:
        if not (isinstance(it, dict) and _ok_id(it.get("id"))):
            continue
        count = it.get("count") if _is_int(it.get("count")) and it["count"] > 0 else 1
        out[it["id"]] = max(out.get(it["id"], 0), _silver(price(it["id"])) * count)
    return sorted(out.items(), key=lambda kv: (-kv[1], kv[0]))[:LOOT_TOP]


def recipe_stakes(view):
    """Plan 054 view -> [(id, unit x qty x RECIPE_CRAFTS)] for market-bought inputs."""
    recipes = view.get("recipes") if isinstance(view, dict) else None
    out = []
    for r in recipes if isinstance(recipes, list) else []:
        inputs = r.get("inputs") if isinstance(r, dict) else None
        for x in inputs if isinstance(inputs, list) else []:
            if not (isinstance(x, dict) and _ok_id(x.get("id"))
                    and x.get("vendor_price") is None):
                continue
            qty = x.get("qty") if _is_int(x.get("qty")) and x["qty"] > 0 else 1
            out.append((x["id"], _silver(x.get("unit")) * qty * RECIPE_CRAFTS))
    return out


def rank(sources):
    """{source: [(id, stake)]} -> [{id, stake, sources}] by stake desc, then id."""
    merged = {}
    for name, rows in sources.items():
        for iid, stake in rows:
            m = merged.setdefault(iid, {"id": iid, "stake": 0, "sources": []})
            m["stake"] += stake
            if name not in m["sources"]:
                m["sources"].append(name)
    return sorted(merged.values(), key=lambda m: (-m["stake"], m["id"]))


class AutoWatch:
    """Curates `watchlist`'s auto entries from three injected sources:
    `shopping() -> plan 037 view`, `loot() -> GrindService.loot_candidates()`,
    `recipes() -> plan 054 view`, and `price(id) -> int|None` (cache only)."""

    def __init__(self, watchlist, shopping=None, loot=None, recipes=None, price=None,
                 cap=CAP, clock=time.time, every_s=SYNC_EVERY_S):
        self.watchlist = watchlist
        self.shopping = shopping or (lambda: {})
        self.loot = loot or (lambda: {})
        self.recipes = recipes or (lambda: {})
        self.price = price or (lambda iid: None)
        self.cap = cap
        self.clock = clock
        self.every_s = every_s
        self._last = None
        self._lock = threading.Lock()

    def _price(self, iid):
        try:
            return self.price(iid)
        except Exception:  # noqa: BLE001 - an unknown price ranks with stake 0
            return None

    def candidates(self):
        """Ranked candidates; raises if a source fails (sync then skips)."""
        return rank({"shopping": shopping_stakes(self.shopping()),
                     "loot": loot_stakes(self.loot(), self._price),
                     "recipes": recipe_stakes(self.recipes())})

    def sync(self):
        """Curate (at most once per every_s) and return the watch rows. A
        failing source skips the round so a hiccup never wipes the auto set."""
        with self._lock:
            now = self.clock()
            if self._last is not None and now - self._last < self.every_s:
                return self.watchlist.items()
            self._last = now
            try:
                ranked = self.candidates()
            except Exception:  # noqa: BLE001 - keep the current auto entries
                return self.watchlist.items()
            return self.watchlist.curate([m["id"] for m in ranked], self.cap)
