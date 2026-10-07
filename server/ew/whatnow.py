"""What now (plan 069): the next best action, ranked by urgency.

Pure inference over views EW already serves (bosses 031, resets 021, grind
buffs 005, Hot Time 011 / 064, maintenance 059 / 064, coupons 006, dice 056,
market alerts 002, level deadlines 024, OCR review 063, login days 075) plus an injected
clock; nothing reads the game. Each adapter turns one view into candidates
{source, text, why, due, left_s}; `rank` scores them

    score = weight x 3600 / max(left_s, 60)

with per-source weights from tracked `data/whatnow_weights.json`, drops any
candidate due beyond its source's horizon (or already past), and returns the
top action plus up to two next. Undated candidates (a market alert hit, the
OCR review queue) count as due in the source's `nominal_s`. Texts carry no
countdown so the result only changes when the actions do; clients count
`due` down locally.
"""

import datetime as _dt
import json
import threading
import time
from pathlib import Path

from . import maint as _maint
from .today import _parse_iso

WEIGHTS_FILE = Path(__file__).resolve().parent / "data" / "whatnow_weights.json"
SOURCES = ("boss", "reset", "buff", "hot", "maint", "coupon", "dice", "market", "deadline", "ocr",
           "maint_loss", "event")
DEFAULT_WEIGHTS = {
    # plan 074: a loss warning outranks the plain maintenance row at the same due
    "maint_loss": {"weight": 2.0, "horizon_s": 86400, "nominal_s": 3600},
    "event": {"weight": 0.5, "horizon_s": 86400, "nominal_s": 86400},
    "boss": {"weight": 1.0, "horizon_s": 1800, "nominal_s": 1800},
    "reset": {"weight": 1.0, "horizon_s": 10800, "nominal_s": 3600},
    "buff": {"weight": 1.2, "horizon_s": 900, "nominal_s": 900},
    "hot": {"weight": 0.6, "horizon_s": 86400, "nominal_s": 3600},
    "maint": {"weight": 1.5, "horizon_s": 7200, "nominal_s": 3600},
    "coupon": {"weight": 0.8, "horizon_s": 259200, "nominal_s": 86400},
    "dice": {"weight": 0.7, "horizon_s": 86400, "nominal_s": 7200},
    "market": {"weight": 0.5, "horizon_s": 86400, "nominal_s": 3600},
    "deadline": {"weight": 0.6, "horizon_s": 1209600, "nominal_s": 86400},
    "ocr": {"weight": 0.3, "horizon_s": 86400, "nominal_s": 7200},
}
EMPTY_TEXT = "All clear - play"
TEXT_MAX = 80  # = ewcore.js TITLE_MAX: a longer action text is dropped by the client
NEXT_MAX = 2
MIN_LEFT_S = 60
DEADLINE_STATES = ("tight", "late")
DICE_ITEM_ID = "black-spirits-adventure-dice"
DICE_TITLE = "black spirit's adventure dice"
OUT_KEYS = ("text", "why", "due", "source")
_UTC = _dt.timezone.utc


def _iso(when):
    return when.astimezone(_UTC).replace(microsecond=0).isoformat()


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _list(v):
    return v if isinstance(v, list) else []


def _dict(v):
    return v if isinstance(v, dict) else {}


def _cand(source, text, why, due, now):
    """One candidate; `due` a UTC datetime or None (undated)."""
    left = None if due is None else int((due - now).total_seconds())
    return {"source": source, "text": text, "why": why,
            "due": None if due is None else _iso(due), "left_s": left}


# -- weights -----------------------------------------------------------------------

def _clean_weight(row):
    if not isinstance(row, dict) or set(row) != {"weight", "horizon_s", "nominal_s"}:
        return None
    if not all(_num(row[k]) and row[k] > 0 for k in row):
        return None
    return {"weight": float(row["weight"]), "horizon_s": int(row["horizon_s"]),
            "nominal_s": int(row["nominal_s"])}


def load_weights(path=None):
    """{source: {weight, horizon_s, nominal_s}}; a bad or missing row falls back
    to DEFAULT_WEIGHTS for that source."""
    try:
        doc = json.loads(Path(path or WEIGHTS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        doc = None
    doc = _dict(doc)
    out = {}
    for s in SOURCES:
        row = _clean_weight(doc.get(s))
        out[s] = row if row is not None else dict(DEFAULT_WEIGHTS[s])
    return out


# -- ranking -----------------------------------------------------------------------

def score(left_s, weight):
    return weight * 3600.0 / max(left_s, MIN_LEFT_S)


def rank(cands, weights):
    """{top, next, empty, empty_text}: the best candidate and up to NEXT_MAX
    more. Ordered by score, then weight, then the sooner due, then source."""
    keyed = []
    for c in cands:
        w = weights.get(c.get("source"))
        if w is None:
            continue
        left = c.get("left_s")
        if left is None:
            left = w["nominal_s"]
        elif left < 0 or left > w["horizon_s"]:
            continue
        keyed.append(((-score(left, w["weight"]), -w["weight"], left, c["source"], c["text"]), c))
    keyed.sort(key=lambda kc: kc[0])
    picked = [{k: c[k] for k in OUT_KEYS} for _, c in keyed[:1 + NEXT_MAX]]
    return {"top": picked[0] if picked else None, "next": picked[1:], "empty": not picked,
            "empty_text": EMPTY_TEXT}


# -- source adapters (each: view dict + now -> [candidate]) ------------------------

def from_bosses(view, now):
    """Plan 031: a boss up now (due its despawn) or the next spawns; bosses
    ticked looted for that PT day are left out."""
    view = _dict(view)
    looted = _dict(view.get("looted"))
    out, seen = [], set()

    def names(row):
        done = set(_list(looted.get(row.get("day"))))
        return [b for b in _list(row.get("bosses")) if isinstance(b, str) and b not in done]

    for row in _list(_dict(view.get("today")).get("remaining")):
        row = _dict(row)
        at = _parse_iso(row.get("at_utc"))
        bs = names(row)
        if row.get("up") is True and at is not None and bs and _num(row.get("despawn_min")):
            seen.add(row["at_utc"])
            out.append(_cand("boss", " + ".join(bs) + (" is up" if len(bs) == 1 else " are up"),
                             "world boss, despawns soon", at + _dt.timedelta(
                                 minutes=row["despawn_min"]), now))
    for row in _list(view.get("next")):
        row = _dict(row)
        at = _parse_iso(row.get("at_utc"))
        bs = names(row)
        if at is None or not bs or row["at_utc"] in seen or at < now:
            continue
        pt = row.get("at_pt")
        why = "world boss" + (f", {pt[11:16]} PT" if isinstance(pt, str) and len(pt) >= 16 else "")
        out.append(_cand("boss", " + ".join(bs) + (" spawns" if len(bs) == 1 else " spawn"),
                         why, at, now))
    return out


def _plural(n, kind):
    if kind == "daily":
        return "daily" if n == 1 else "dailies"
    if kind == "weekly":
        return "weekly" if n == 1 else "weeklies"
    return "task" if n == 1 else "tasks"


def from_today(view, now):
    """Plan 003 / 021: one candidate per upcoming reset with rows still open."""
    view = _dict(view)
    groups = {}
    for it in _list(view.get("items")):
        if not isinstance(it, dict) or it.get("done") is not False:
            continue
        kind = it.get("kind")
        if kind not in ("daily", "weekly"):
            continue
        due = _parse_iso(it.get("next_reset")) or _parse_iso(
            view.get("daily_reset" if kind == "daily" else "weekly_reset"))
        if due is None or not isinstance(it.get("title"), str):
            continue
        groups.setdefault(due, []).append(it)
    out = []
    for due in sorted(groups):
        rows = groups[due]
        kinds = {r["kind"] for r in rows}
        kind = kinds.pop() if len(kinds) == 1 else None
        titles = [r["title"] for r in rows]
        why = ", ".join(titles[:3]) + (f" +{len(titles) - 3}" if len(titles) > 3 else "")
        out.append(_cand("reset", f"Finish {len(rows)} {_plural(len(rows), kind)} before reset",
                         why, due, now))
    return out


def from_grind(view, now):
    """Plan 005: an armed buff running out while a grind session is open."""
    view = _dict(view)
    if not isinstance(view.get("active"), dict):
        return []
    out = []
    for b in _list(view.get("buffs")):
        ends = _parse_iso(_dict(b).get("ends"))
        if ends is not None and ends > now and isinstance(b.get("name"), str):
            out.append(_cand("buff", f"Re-arm {b['name']}", "buff ends during your grind session",
                             ends, now))
    return out


def hot_time(view, now):
    """Plan 011 / 064: a Hot Time window live now, due its end."""
    out = []
    for a in _list(_dict(_dict(view).get("hot")).get("active")):
        a = _dict(a)
        if not _num(a.get("ends_in_s")) or a["ends_in_s"] <= 0:
            continue
        pct = f" +{a['pct']}%" if _num(a.get("pct")) else ""
        why = a["label"] if isinstance(a.get("label"), str) else "Hot Time window"
        # windows end on a minute; rounding keeps `due` stable across the two
        # clock reads (the leveling view's and ours), so no spurious change event
        end = int(round((now.timestamp() + a["ends_in_s"]) / 60.0)) * 60
        out.append(_cand("hot", f"Hot Time{pct} - grind now", why,
                         _dt.datetime.fromtimestamp(end, _UTC), now))
    return out


def deadlines(view, now):
    """Plan 024: a level-gated deadline that is tight or late, due enrolment
    close (once that passed, the quest cut-off when the row has one)."""
    out = []
    for d in _list(_dict(view).get("deadlines")):
        d = _dict(d)
        enrol = _parse_iso(d.get("enrol_by_utc"))
        if enrol is None or d.get("state") not in DEADLINE_STATES or not _num(d.get("needs_level")):
            continue
        quests = _parse_iso(d.get("quests_by_utc"))
        if enrol <= now and quests is not None:
            enrol = quests
        label = d["label"] if isinstance(d.get("label"), str) else "a deadline"
        out.append(_cand("deadline", f"Reach Lv {d['needs_level']} for {label}",
                         f"level deadline {d['state']}", enrol, now))
    return out


def maintenance(now, slot, notices):
    """Plan 059 / 064: the next maintenance start after `now` - an official
    notice for a date wins over the weekly slot (`maint.resolve`)."""
    slot = _maint._clean(slot)
    notices = notices or {}
    wd = _maint.WEEKDAYS.index(slot["weekday"])
    day = now.astimezone(_UTC).date()
    for n in range(0, 9):
        d = day + _dt.timedelta(days=n)
        if d.isoformat() not in notices and d.weekday() != wd:
            continue
        start = _maint.resolve(d, "before", slot, notices)
        if start is not None and start > now:
            why = "official notice" if d.isoformat() in notices else (
                f"weekly slot {slot['weekday'].title()} {slot['start_utc']} UTC")
            return [_cand("maint", "Maintenance soon - wrap up", why, start, now)]
    return []


def _clip(s, n=TEXT_MAX):
    return s if len(s) <= n else s[:n - 3].rstrip() + "..."


def from_events(view, now):
    """Plan 006: an open coupon with an end date, due that end; plan 074: an
    open event with an end date too (source `event`)."""
    out = []
    for it in _list(_dict(view).get("items")):
        it = _dict(it)
        kind = it.get("kind")
        if kind not in ("coupon", "event") or it.get("done") is True:
            continue
        if it.get("status") not in ("active", "upcoming"):
            continue
        ends = _parse_iso(it.get("ends"))
        if ends is None or ends <= now:
            continue
        if kind == "event":
            if isinstance(it.get("title"), str) and it["title"]:
                out.append(_cand("event", _clip(f"{it['title']} ends"), "event ends", ends, now))
            continue
        name = it["code"] if isinstance(it.get("code"), str) else it.get("title")
        title = it["title"] if isinstance(it.get("title"), str) else name
        out.append(_cand("coupon", f"Redeem coupon {name}", f"{title} expires", ends, now))
    return out


def login_days(view, now):
    """Plan 075: a login-day event at risk (today not yet credited), due the
    end of today's UTC date."""
    out = []
    end = _dt.datetime.combine(now.astimezone(_UTC).date() + _dt.timedelta(days=1),
                               _dt.time(0), _UTC)
    for r in _list(_dict(view).get("rows")):
        r = _dict(r)
        if r.get("at_risk") is not True or not isinstance(r.get("title"), str):
            continue
        why = f"login days {r.get('credited')}/{r.get('needed')}, {r.get('days_left')} days left"
        out.append(_cand("deadline", f"Log in today for {r['title']}", why, end, now))
    return out


def maint_loss(view, now):
    """Plan 074: one candidate per unacked loss warning of the before-maintenance
    digest, due its maintenance start."""
    out = []
    for w in _list(_dict(view).get("warnings")):
        w = _dict(w)
        due = _parse_iso(w.get("due_utc"))
        if due is None or not isinstance(w.get("text"), str) or not w["text"]:
            continue
        why = f"official notice {w['notice_no']}" if _num(w.get("notice_no")) else "official notice"
        out.append(_cand("maint_loss", _clip("Before maintenance: " + w["text"]), why, due, now))
    return out


def _is_dice(it):
    return isinstance(it, dict) and (it.get("id") == DICE_ITEM_ID or (
        isinstance(it.get("title"), str) and it["title"].strip().lower().replace(
            "spirits", "spirit's") == DICE_TITLE))


def dice(view, now):
    """Plan 056: dice earned and the dice row still open, due the dice reset."""
    view = _dict(view)
    d = _dict(view.get("dice"))
    rows = [it for it in _list(view.get("items")) if _is_dice(it)]
    if not rows or all(it.get("done") is True for it in rows):
        return []
    if not (_num(d.get("earned")) and _num(d.get("max")) and d["earned"] >= 1):
        return []
    due = _parse_iso(d.get("next_reset"))
    return [_cand("dice", f"Roll the dice ({d['earned']}/{d['max']} earned)",
                  "Black Spirit's Adventure, before the dice reset", due, now)]


def market_alerts(items, now):
    """Plan 002 / 052: watched items whose alert fired (undated)."""
    out = []
    for it in _list(items):
        it = _dict(it)
        alert = it.get("alert")
        if not isinstance(alert, str):
            continue
        name = it["name"] if isinstance(it.get("name"), str) and it["name"] else f"#{it.get('id')}"
        side = it.get("below") if alert.startswith("below") else it.get("above")
        why = f"price {it.get('price')}" + (f" vs {side}" if _num(side) else "")
        out.append(_cand("market", f"Market: {name} {alert.replace('_', ' ')}", why, None, now))
    return out


def ocr_review(view, now):
    """Plan 063: OCR reads waiting for review (undated)."""
    n = len(_list(_dict(view).get("review")))
    if n < 1:
        return []
    return [_cand("ocr", f"Review {n} OCR read" + ("" if n == 1 else "s"),
                  "System tab review queue", None, now)]


# input name -> adapter(data, now); `maint` data is {slot, notices}
ADAPTERS = {
    "bosses": [from_bosses],
    "today": [from_today, dice],
    "grind": [from_grind],
    "leveling": [hot_time, deadlines],
    "maint": [lambda d, now: maintenance(now, _dict(d).get("slot"), _dict(d).get("notices"))],
    "events": [from_events],
    "maint_digest": [maint_loss],
    "market": [market_alerts],
    "ocr": [ocr_review],
    "logins": [login_days],
}


def collect(inputs, now, weights):
    """Rank candidates from `inputs` {name: zero-arg callable -> view}. A
    source that raises is skipped and named in `errors`; the rest still rank."""
    cands, errors = [], []
    for name, adapters in ADAPTERS.items():
        get = inputs.get(name)
        if get is None:
            continue
        try:
            data = get()
            for a in adapters:
                cands.extend(a(data, now))
        except Exception:  # noqa: BLE001 - one bad source never blanks the card
            errors.append(name)
    return dict(rank(cands, weights), errors=errors)


class WhatNowService:
    """GET /api/whatnow and the SSE `whatnow` event. `refresh(max_age_s)`
    recomputes when the cached result is older than `max_age_s` and bumps
    `seq` only when the ranked actions change (texts and due, not the clock)."""

    def __init__(self, inputs, clock=time.time, weights=None):
        self.inputs = inputs
        self.clock = clock
        self.weights = weights or load_weights()
        self.seq = 0
        self._lock = threading.Lock()
        self._at = None
        self._sig = None
        self._view = None

    def _compute(self):
        now = _dt.datetime.fromtimestamp(self.clock(), _UTC)
        return dict(collect(self.inputs, now, self.weights), now=_iso(now))

    def refresh(self, max_age_s=0):
        with self._lock:
            t = self.clock()
            if self._view is None or self._at is None or t - self._at >= max_age_s:
                view = self._compute()
                sig = json.dumps([view["top"], view["next"]], sort_keys=True)
                if sig != self._sig:
                    self._sig = sig
                    self.seq += 1
                self._view, self._at = view, t
            return self.seq, self._view

    def view(self):
        return self.refresh(0)[1]
