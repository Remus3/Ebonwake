#!/usr/bin/env python3
"""Inbox cost discipline (MAIN ORDER 2026-10-05, FLEET-COMMON item 14, kit v8).

Local stand-in for the kit's fleet_inbox until v8 is vendored into
ops/fleet_kit/ (plan 015 as-built deviation 13). Pure functions plus two
append-only ledgers; the loop tick (tools/ew_loop.py) is the only caller.

    classify(name, code, head)  skip | ack | work | triage
    hop(text) / may_reply(text) / next_hop(text)   HOP: <n> (absent = 1)
    triage_prompt(name, text) / parse_verdict(text) NOREPLY | ACK | ANSWER
    OutboundCap(root)            at most CAP notes per local day, ORDER/FIX/
                                 RULING exempt; allow(cls) then record(...)
    batch_note(code, to, pairs)  several answers to one destination, one note
    Ledger(root)                 mechanical acks and answered notes (no note)
    with_kind(spawn, kw, kind)   adds kind=build|inbox|triage when the kit's
                                 spawn takes it (v8), drops it on v6
"""

import datetime as _dt
import inspect
import json
import os
import re
from pathlib import Path

CONTROL_REL = Path("ops/loop/control")
CAP_REL = CONTROL_REL / "outbound_cap.jsonl"
LEDGER_REL = CONTROL_REL / "inbox_ledger.jsonl"
CAP = 6
HOP_LIMIT = 2
WORK = ("ORDER", "FIX", "RULING")
ACK_CLASSES = ("ACK", "INFORMATION", "INFO", "TERMINAL", "ANSWER", "REPLY", "NOREPLY")
VERDICTS = ("NOREPLY", "ACK", "ANSWER")
TRIAGE_SPAWN = {"model": "sonnet", "effort": "low", "bare": True}
KINDS = ("build", "inbox", "triage")

_SENDER = re.compile(r"(?:^|-)from-([A-Z][A-Z0-9]{1,7})-", re.I)
_CLASS = re.compile(r"(?:^|-)from-[A-Z][A-Z0-9]{1,7}-([A-Z]+)", re.I)
_TITLE = re.compile(r"^#\s*From\s+([A-Z][A-Z0-9]{1,7})\b(?:\s*-\s*([A-Z]+))?", re.I)
_HOP = re.compile(r"^\s*HOP:\s*(\d+)\s*$", re.M)
_MARK = re.compile(r"^(TERMINAL|NO-?REPLY)\b", re.I)
_VERDICT = re.compile(r"^\s*VERDICT:\s*(NOREPLY|ACK|ANSWER)\b", re.I | re.M)


def _title(head):
    for line in (head or "").splitlines():
        if line.strip():
            return _TITLE.match(line.strip())
    return None


def sender(name, head=""):
    m = _SENDER.search(Path(str(name)).name)
    if m:
        return m.group(1).upper()
    t = _title(head)
    return t.group(1).upper() if t else None


def note_class(name, head=""):
    m = _CLASS.search(Path(str(name)).name)
    if m:
        return m.group(1).upper()
    t = _title(head)
    return t.group(2).upper() if t and t.group(2) else None


def _terminal(name, head):
    tokens = re.split(r"[^A-Z0-9]+", Path(str(name)).name.upper())
    if "TERMINAL" in tokens or "NOREPLY" in tokens or "-NO-REPLY-" in \
            "-" + "-".join(tokens) + "-":
        return True
    return any(_MARK.match(line.strip().lstrip("#*>- \t").rstrip("* \t"))
               and len(line.strip().lstrip("#*>- \t").rstrip("* \t").split()) <= 2
               for line in (head or "").splitlines())


def hop(text):
    m = _HOP.search(text or "")
    return int(m.group(1)) if m else 1


def next_hop(text):
    return hop(text) + 1


def may_reply(text, cls=None):
    """Never answer an answer; no chain beyond HOP_LIMIT hops without new work."""
    if cls in WORK:
        return True
    if cls in ACK_CLASSES:
        return False
    return hop(text) < HOP_LIMIT


def classify(name, own_code, head=""):
    """skip (own / TERMINAL / no-reply), ack (ack class or past the hop limit),
    work (ORDER / FIX / RULING, never damped), else triage."""
    if sender(name, head) == own_code:
        return "skip"
    cls = note_class(name, head)
    if cls in WORK:
        return "work"
    if _terminal(name, head):
        return "skip"
    if cls in ACK_CLASSES or not may_reply(head, cls):
        return "ack"
    return "triage"


def triage_prompt(name, text, code="EW", limit=20000):
    return ("You are the " + code + " session triaging ONE channel note at low cost. "
            "First line exactly 'VERDICT: NOREPLY', 'VERDICT: ACK' or 'VERDICT: ANSWER'. "
            "NOREPLY when the note needs nothing from " + code + "; ACK when it only needs "
            "to be marked read; ANSWER only when it asks " + code + " a question that only "
            + code + " can answer. For ANSWER, the lines after the verdict are the reply "
            "body: markdown, ASCII, first line '# From " + code + " - ANSWER re " + name
            + "'. Do not change files. Never put a directory name, account id or email in "
            "the reply.\n\n--- NOTE " + name + " ---\n" + (text or "")[:limit]
            + "\n--- END NOTE ---")


def parse_verdict(text):
    """(verdict, body). No verdict line: a non-empty result is an ANSWER (the
    note is handled, never dropped); an empty one is NOREPLY."""
    text = (text or "").strip("\n")
    m = _VERDICT.search(text)
    if not m:
        return ("ANSWER", text) if text.strip() else ("NOREPLY", "")
    verdict = m.group(1).upper()
    body = (text[:m.start()] + text[m.end():]).strip("\n")
    return verdict, (body if verdict == "ANSWER" else "")


def with_hop(body, n):
    """Put `HOP: n` under the title line (replacing any HOP line)."""
    lines = [ln for ln in (body or "").splitlines() if not _HOP.match(ln)]
    at = 1 if lines and lines[0].lstrip().startswith("#") else 0
    lines.insert(at, f"HOP: {n}")
    return "\n".join(lines).rstrip("\n") + "\n"


def batch_note(code, to, pairs, hop_n=2):
    """One note answering several notes from one destination."""
    out = [f"# From {code} - ANSWER to {to} (batch of {len(pairs)})", f"HOP: {hop_n}", ""]
    for note, text in pairs:
        body = [ln for ln in (text or "").splitlines()
                if not _HOP.match(ln) and not ln.lstrip().startswith("# From ")]
        out += [f"## re {note}", ""] + body + [""]
    return "\n".join(out).rstrip("\n") + "\n"


def with_kind(spawn, kw, kind):
    """kw plus kind= when spawn accepts it (kit v8 or a **kw fake)."""
    assert kind in KINDS, kind
    try:
        params = inspect.signature(spawn).parameters.values()
    except (TypeError, ValueError):
        return dict(kw)
    if any(p.name == "kind" or p.kind is p.VAR_KEYWORD for p in params):
        return dict(kw, kind=kind)
    return dict(kw)


def _append(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(doc, sort_keys=True).encode("ascii", "replace") + b"\n"
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)


def _read(path):
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            doc = json.loads(ln)
        except ValueError:
            continue
        if isinstance(doc, dict):
            out.append(doc)
    return out


def _day(epoch):
    return _dt.datetime.fromtimestamp(epoch).strftime("%Y%m%d")


class OutboundCap:
    def __init__(self, root, cap=CAP):
        self.path = Path(root) / CAP_REL
        self.cap = min(int(cap), CAP)

    def used(self, now):
        day = _day(now)
        return sum(1 for d in _read(self.path)
                   if d.get("day") == day and d.get("cls") not in WORK and not d.get("exempt"))

    def allow(self, cls, now, pending=0):
        return cls in WORK or self.used(now) + pending < self.cap

    def record(self, cls, to, name, now, exempt=False):
        _append(self.path, {"day": _day(now), "cls": cls, "to": to, "name": name,
                            "exempt": bool(exempt)})


class Ledger:
    """Mechanical acks (a ledger line, no note) and answered notes."""

    def __init__(self, root):
        self.path = Path(root) / LEDGER_REL

    def mark(self, name, action, now, detail=""):
        _append(self.path, {"note": name, "action": action, "day": _day(now),
                            "detail": str(detail)[:200]})

    def answered(self, name):
        return any(d.get("note") == name and d.get("action") == "answered"
                   for d in _read(self.path))
