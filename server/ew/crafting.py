"""Cooking / alchemy margin calculator (plan 054).

Operator-typed recipes (store domain `crafting`): inputs by market id or by
name, each with an optional vendor price; outputs and optional procs by market
id with an average yield per craft. Input lines with a vendor price use it,
others the plan 002 sublist cache through an injected `price(item_id)` that
never fetches; outputs are sold through plan 027's `net_proceeds` at the
current market settings (VP / family fame), so the 65 / 84.5 percent collect
rate is the same rule the Market tab uses. A line with no price is flagged in
`missing` and the profit is left null, never guessed. Nothing is read from the
game and nothing is bought or sold.
"""

import math
import re
import threading
from fractions import Fraction

from . import market
from .market import net_proceeds
from .today import slug

ID_RE = re.compile(r"^[a-z0-9-]{1,40}$")
KINDS = ("cooking", "alchemy")
RECIPE_KEYS = {"name", "kind", "inputs", "outputs", "procs"}
INPUT_KEYS = {"id", "name", "qty", "vendor_price"}
OUTPUT_KEYS = {"id", "name", "qty_avg"}
MAX_INPUTS = 20
MAX_OUTPUTS = 10
MAX_RECIPES = 50
MAX_NAME = 60
MAX_LINE_NAME = 40
MAX_QTY = 9999
MAX_QTY_AVG = 1000
MAX_SILVER = 10 ** 13


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _int_in(v, lo, hi):
    return _is_int(v) and lo <= v <= hi


def _ascii(v, max_len):
    return (isinstance(v, str) and v.strip() != "" and len(v) <= max_len
            and all(32 <= ord(ch) <= 126 for ch in v))


def _qty_avg_ok(v):
    return (isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
            and 0 < v <= MAX_QTY_AVG)


def _exact(v):
    # str() of a float is its shortest decimal, so 1.3 is 13/10, not the binary value.
    return Fraction(str(v)) if isinstance(v, float) else Fraction(v)


# -- validation -------------------------------------------------------------------

def _line_name(line, where):
    n = line.get("name")
    if n is not None and not _ascii(n, MAX_LINE_NAME):
        raise ValueError(f"{where} name must be 1..{MAX_LINE_NAME} printable ASCII chars")
    return n


def _line_id(line, where, required):
    v = line.get("id")
    if v is None and not required:
        return None
    if not _int_in(v, 0, market.MAX_ID):
        raise ValueError(f"{where} id must be an int in 0..{market.MAX_ID}")
    return v


def _input(line, i):
    where = f"input {i + 1}"
    if not isinstance(line, dict) or set(line) - INPUT_KEYS:
        raise ValueError(f"{where} must be an object with keys {sorted(INPUT_KEYS)}")
    out = {"id": _line_id(line, where, False), "name": _line_name(line, where)}
    if out["id"] is None and out["name"] is None:
        raise ValueError(f"{where} needs an id or a name")
    if not _int_in(line.get("qty"), 1, MAX_QTY):
        raise ValueError(f"{where} qty must be an int in 1..{MAX_QTY}")
    out["qty"] = line["qty"]
    vp = line.get("vendor_price")
    if vp is not None and not _int_in(vp, 0, MAX_SILVER):
        raise ValueError(f"{where} vendor_price must be null or an int in 0..{MAX_SILVER}")
    out["vendor_price"] = vp
    return out


def _output(line, where):
    if not isinstance(line, dict) or set(line) - OUTPUT_KEYS:
        raise ValueError(f"{where} must be an object with keys {sorted(OUTPUT_KEYS)}")
    out = {"id": _line_id(line, where, True), "name": _line_name(line, where)}
    if not _qty_avg_ok(line.get("qty_avg")):
        raise ValueError(f"{where} qty_avg must be a number in (0, {MAX_QTY_AVG}]")
    out["qty_avg"] = line["qty_avg"]
    return out


def _lines(v, key, lo, hi):
    if not isinstance(v, list) or not lo <= len(v) <= hi:
        raise ValueError(f"{key} must be a list of {lo}..{hi} lines")
    return v


def validate_recipe(r):
    """An operator recipe, checked and normalised; raises ValueError."""
    if not isinstance(r, dict) or set(r) - RECIPE_KEYS:
        raise ValueError(f"recipe must be an object with keys {sorted(RECIPE_KEYS)}")
    if not _ascii(r.get("name"), MAX_NAME):
        raise ValueError(f"name must be 1..{MAX_NAME} printable ASCII chars")
    kind = r.get("kind", "cooking")
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {list(KINDS)}")
    inputs = _lines(r.get("inputs"), "inputs", 1, MAX_INPUTS)
    outputs = _lines(r.get("outputs"), "outputs", 1, MAX_OUTPUTS)
    procs = _lines(r.get("procs", []), "procs", 0, MAX_OUTPUTS)
    return {"name": r["name"], "kind": kind,
            "inputs": [_input(x, i) for i, x in enumerate(inputs)],
            "outputs": [_output(x, f"output {i + 1}") for i, x in enumerate(outputs)],
            "procs": [_output(x, f"proc {i + 1}") for i, x in enumerate(procs)]}


# -- margin -----------------------------------------------------------------------

def input_unit(line, prices):
    """Silver per unit of one input: the vendor price, else the market price."""
    if line["vendor_price"] is not None:
        return line["vendor_price"]
    return prices.get(line["id"]) if line["id"] is not None else None


def margin(recipe, prices, vp, fame):
    """Per-craft cost / gross / net-after-tax / profit of a validated recipe.

    `prices` maps market id -> silver. Sums are exact fractions floored once
    at the end, so per 1,000 crafts is not 1,000 x a floored per-craft value.
    A line without a price is listed in `missing`; the totals then cover the
    priced lines only and profit, per 1,000 and margin_pct are null.
    """
    missing = []
    cost = 0
    for x in recipe["inputs"]:
        unit = input_unit(x, prices)
        if unit is None:
            missing.append({"side": "input", "id": x["id"], "name": x["name"]})
        else:
            cost += unit * x["qty"]
    gross = net = Fraction(0)
    for side, rows in (("output", recipe["outputs"]), ("proc", recipe["procs"])):
        for x in rows:
            price = prices.get(x["id"])
            if price is None:
                missing.append({"side": side, "id": x["id"], "name": x["name"]})
                continue
            q = _exact(x["qty_avg"])
            gross += price * q
            net += net_proceeds(price, vp, fame) * q
    complete = not missing
    profit = net - cost
    return {"cost": cost, "gross": math.floor(gross), "net": math.floor(net),
            "profit": math.floor(profit) if complete else None,
            "profit_1000": math.floor(profit * 1000) if complete else None,
            "margin_pct": (round(float(profit / cost * 100), 1)
                           if complete and cost > 0 else None),
            "complete": complete, "missing": missing}


# -- service ------------------------------------------------------------------------

class CraftingService:
    """Store domain `crafting`: {"recipes": [{id, name, kind, inputs, outputs, procs}]}."""

    def __init__(self, store, price, tax, name=None):
        self.store = store
        self.price = price                  # id -> silver or None, never fetches
        self.tax = tax                      # () -> plan 027 {vp, fame_pct}
        self.name = name or (lambda iid: None)
        self._lock = threading.Lock()

    def _load(self):
        doc = self.store.get("crafting")
        raw = doc.get("recipes") if isinstance(doc, dict) else None
        out, seen = [], set()
        for r in raw if isinstance(raw, list) else []:
            if not isinstance(r, dict):
                continue
            rid = r.get("id")
            if not (isinstance(rid, str) and ID_RE.match(rid)) or rid in seen:
                continue
            try:
                clean = validate_recipe({k: v for k, v in r.items() if k != "id"})
            except ValueError:
                continue
            seen.add(rid)
            out.append(dict(clean, id=rid))
        return out

    def _save(self, recipes):
        self.store.put("crafting", {"recipes": recipes})

    def _label(self, line):
        if line["name"]:
            return line["name"]
        n = self.name(line["id"])
        return n if isinstance(n, str) and n else f"#{line['id']}"

    def view(self):
        tax = market.settings_from({"market": self.tax()})
        vp, fame = tax["vp"], tax["fame_pct"]
        rows = []
        for r in self._load():
            ids = {x["id"] for x in r["inputs"] if x["vendor_price"] is None
                   and x["id"] is not None}
            ids |= {x["id"] for x in r["outputs"] + r["procs"]}
            prices = {}
            for iid in sorted(ids):
                p = self.price(iid)
                if _is_int(p) and p >= 0:
                    prices[iid] = p
            sold = [dict(x, label=self._label(x), unit=prices.get(x["id"]),
                         net_unit=net_proceeds(prices[x["id"]], vp, fame)
                         if x["id"] in prices else None)
                    for x in r["outputs"] + r["procs"]]
            rows.append(dict(
                r, inputs=[dict(x, label=self._label(x), unit=input_unit(x, prices))
                           for x in r["inputs"]],
                outputs=sold[:len(r["outputs"])], procs=sold[len(r["outputs"]):],
                margin=margin(r, prices, vp, fame)))
        return {"recipes": rows, "tax": tax, "max_recipes": MAX_RECIPES}

    @staticmethod
    def _id_arg(arg):
        if not (isinstance(arg, str) and ID_RE.match(arg)):
            raise ValueError("id must match ^[a-z0-9-]{1,40}$")
        return arg

    def add(self, arg):
        clean = validate_recipe(arg)
        with self._lock:
            recipes = self._load()
            if len(recipes) >= MAX_RECIPES:
                raise ValueError(f"at most {MAX_RECIPES} recipes")
            taken = {r["id"] for r in recipes}
            base = slug(clean["name"])[:34].strip("-") or "recipe"
            rid, n = base, 2
            while rid in taken:
                rid, n = f"{base}-{n}", n + 1
            recipes.append(dict(clean, id=rid))
            self._save(recipes)
        return self.view()

    def edit(self, arg):
        if not isinstance(arg, dict):
            raise ValueError("edit must be an object with an id")
        rid = self._id_arg(arg.get("id"))
        clean = validate_recipe({k: v for k, v in arg.items() if k != "id"})
        with self._lock:
            recipes = self._load()
            for i, r in enumerate(recipes):
                if r["id"] == rid:
                    recipes[i] = dict(clean, id=rid)
                    break
            else:
                raise ValueError(f"no recipe {rid}")
            self._save(recipes)
        return self.view()

    def delete(self, arg):
        rid = self._id_arg(arg)
        with self._lock:
            recipes = self._load()
            keep = [r for r in recipes if r["id"] != rid]
            if len(keep) == len(recipes):
                raise ValueError(f"no recipe {rid}")
            self._save(keep)
        return self.view()
