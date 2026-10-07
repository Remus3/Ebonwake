"""Plan 081: typed planner inputs derived from live signals.

Pure helpers. `pick` chooses between an operator-typed value and a live read
(screenshot OCR, play sessions): the newer one wins, so a read after the
typing supersedes it and typing after the last read corrects it until the
next read. Every result carries its source and age; a derived value older
than STALE_S is flagged stale (the card shows the plan 077 muted marker,
never a silent number). `hours_per_day` is the 14-day median of logged-in
hours per played day from plan 062 play sessions.
"""

import datetime as _dt
import statistics

STALE_S = 7 * 86400
HOURS_WINDOW_DAYS = 14
HOURS_MIN_SESSIONS = 3


def _ts(v):
    if not isinstance(v, str):
        return None
    try:
        t = _dt.datetime.fromisoformat(v)
    except ValueError:
        return None
    return t.timestamp() if t.tzinfo is not None else None


def pick(typed, live, now, default=None):
    """{value, source, at, age_s, stale, superseded, typed}.

    `typed`: None or {value, at (ISO)}; `live`: None or {value, at (ISO),
    source}. The live read wins when there is no typed value or it is at least
    as new (`superseded` then says a typed value was overridden); else the
    typed value. Without either: `default`, source "default"."""
    t_at = _ts(typed.get("at")) if isinstance(typed, dict) else None
    l_at = _ts(live.get("at")) if isinstance(live, dict) else None
    has_typed = isinstance(typed, dict) and typed.get("value") is not None
    has_live = isinstance(live, dict) and live.get("value") is not None and l_at is not None
    typed_value = typed.get("value") if has_typed else None
    if has_live and (not has_typed or t_at is None or l_at >= t_at):
        age = max(0, int(now - l_at))
        return {"value": live["value"], "source": str(live.get("source") or "live"),
                "at": live["at"], "age_s": age, "stale": age > STALE_S,
                "superseded": has_typed, "typed": typed_value}
    if has_typed:
        return {"value": typed_value, "source": "typed", "at": typed.get("at"),
                "age_s": max(0, int(now - t_at)) if t_at is not None else None,
                "stale": False, "superseded": False, "typed": typed_value}
    return {"value": default, "source": "default", "at": None, "age_s": None,
            "stale": False, "superseded": False, "typed": None}


def hours_per_day(sessions, now, days=HOURS_WINDOW_DAYS, min_sessions=HOURS_MIN_SESSIONS):
    """{value (h, 0.1), n (sessions), days (played UTC dates), at (newest end)}
    from closed play sessions [{start, end}] overlapping the last `days` days,
    or None with fewer than `min_sessions`. A session crossing midnight is split
    across its UTC dates; the median is over played dates only (a rest day is
    not a 0 h day, or the projection would say "never")."""
    lo = now - days * 86400
    per_day, n, newest = {}, 0, None
    for s in sessions if isinstance(sessions, list) else []:
        st = _ts(s.get("start")) if isinstance(s, dict) else None
        en = _ts(s.get("end")) if isinstance(s, dict) else None
        if st is None or en is None or en <= st or en <= lo or st > now:
            continue
        n += 1
        newest = max(newest or en, en)
        a, b = max(st, lo), min(en, now)
        while a < b:
            day = _dt.datetime.fromtimestamp(a, _dt.timezone.utc).date()
            nxt = _dt.datetime.combine(day + _dt.timedelta(days=1), _dt.time(),
                                       _dt.timezone.utc).timestamp()
            cut = min(b, nxt)
            per_day[day] = per_day.get(day, 0) + (cut - a)
            a = cut
    if n < min_sessions or not per_day:
        return None
    med = statistics.median(v / 3600 for v in per_day.values())
    value = min(24.0, max(0.1, round(med, 1)))
    at = _dt.datetime.fromtimestamp(newest, _dt.timezone.utc).replace(microsecond=0).isoformat()
    return {"value": value, "n": n, "days": len(per_day), "at": at}
