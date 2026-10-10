"""EW loop constants, small helpers and item records (plan 107 split of
tools/ew_loop.py, which re-exports every name here). Stdlib only."""

import contextlib
import datetime as _dt
import json
import os
import re
import subprocess
import time
from pathlib import Path

import eta
import ew_lane


INBOX_REL = "moon_sync_inbox"
OUTBOX_REL = "moon_sync_outbox"

CODE = "EW"
CONTROL_REL = Path("ops/loop/control")
HALT_REL = CONTROL_REL / "HALT"
LOCK_REL = CONTROL_REL / "loop.lock"
ROADMAP_REL = Path("docs/plans/ROADMAP.md")
BACKOFF_REL = CONTROL_REL / "backoff.json"
ITEMS_REL = CONTROL_REL / "loop_items"
WATCH_REL = CONTROL_REL / "loop_inbox_watch.json"
ORDERS_REL = CONTROL_REL / "loop_orders.json"
LEGACY_LEDGER_REL = CONTROL_REL / "inbox_ledger.jsonl"  # pre-kit-v8 shim (deviation 13)
DELIVERY_REL = CONTROL_REL / "delivery.jsonl"  # plan 093: one line per destination copy
# plan 098: review / fix / merge and push run in detached workers; merges into
# main serialize on MERGE_LOCK_REL; one push worker at a time (PUSH_WORKER_REL)
MERGE_LOCK_REL = CONTROL_REL / "merge.lock"
PUSH_WORKER_REL = CONTROL_REL / "push_worker.json"
PUSH_LAST_REL = CONTROL_REL / "push_last.json"
REVIEW_START_S = 300  # a launched worker that wrote no pid by then never started
TICK_TARGET_S = 60
REVIEW_LOG_KEEP = 10
NOTE_STAMP = "%Y-%m-%d-%H%M"  # the fleet's dashed note stamp (kit batch_note)
# Plan 096 (perf 2.1): one authoritative gate run per tree state. Verdicts are
# cached by the git tree of the gated working tree; a red one only for
# RED_TTL_S (a flake clears), an infrastructure red (suite-gate refusal or slot
# timeout, subprocess timeout / OSError, no ci workflow) never.
GATE_CACHE_REL = CONTROL_REL / "gate_verdicts.json"
GATE_CACHE_MAX = 200
RED_TTL_S = 2 * 3600
INFRA_RX = re.compile(r"SUITE-GATE:|TimeoutExpired|OSError|SubprocessError|fail closed")
# Responder diet (operator 2026-10-05) + kit v8 item 14: an ORDER / FIX /
# RULING note escalates to a lane work item (a lane does the work, the loop
# answers after merge); any other note is classified for free and only what
# classify() cannot settle gets one sonnet low-effort bare triage spawn. At
# most loop.max_notes_per_day (kit OUTBOUND_CAP 6; config may lower it, never
# raise it) outbound notes per local day.
DEFAULT_MAX_NOTES = 6
PROGRESS_TASK = "loop"
TICK_S = 900
HEADROOM = 3
BACKOFF_BASE_S = 1800
BACKOFF_CAP_S = 6 * 3600
MAX_ROUNDS = 3
MAX_ATTEMPTS = 2
DEFAULT_MAX_PLANS = 2
LANE_TIMEOUT_S = 5400
GATE_TIMEOUT_S = 1800
# Plan 099 (perf 2.5): model / effort per run kind. Opus only for implementation
# (plan, order, hand-off); sonnet for data refreshes, resolve-merge, research and
# fix rounds; effort low for the verifier and the order-closing inbox answer.
# Effort always goes through the kit's pick_effort (validated, explicit wins).
# load_config()["routes"] is this table plus valid loop.routes overrides.
ROUTES = {
    "plan": ("opus", "medium"),
    "order": ("opus", "medium"),
    "handoff": ("opus", "medium"),
    "data": ("sonnet", "medium"),
    "resolve": ("sonnet", "medium"),
    "deep-dive": ("sonnet", "medium"),
    "fix": ("sonnet", "medium"),
    "verify": ("sonnet", "low"),
    "inbox": ("sonnet", "low"),
}
ROUTE_EFFORTS = ("low", "medium", "high", "xhigh", "max")  # the kit's EFFORTS
_ROUTE_MODEL = re.compile(r"^(?:opus|sonnet|haiku|claude-[a-z0-9][a-z0-9.-]*)(?:\[1m\])?$")
_DATA_ID = re.compile(r"^D[0-9a-f]{6}$")  # plan 085 data_items id
NOTE_HEAD = 600
NOTE_MAX = 20000
DONE_STATES = ("merged", "no-change", "failed")
# lane-dirty: the kit refused the claim on a dirty lane worktree; it waits
# until that worktree is clean and unheld, then retries (recover_lane_dirty);
# gave-up: MAX_ATTEMPTS refused / lost dispatches, error kept.
ATTENTION_STATES = ("merge-conflict", "merge-refused", "failed-dirty", "lane-dirty",
                    "gave-up")
# an item in these states holds its unmerged work in rec["worktree"]
HOLD_STATES = ("ran", "committed")
IN_FLIGHT = ("dispatched",) + HOLD_STATES
# plan 058: a conflicting lane commit is kept under this ref in the main
# checkout (a re-claimed lane worktree can never orphan it) until it merges
KEEP_REF = "refs/ew/keep/"
# a kept record in these states still waits on its commit reaching main
RESOLVABLE = ("merge-conflict", "merge-refused", "refused", "lost", "paused", "no-change")
# one conflict line `<<<<<<< x` / `>>>>>>> x` left in a resolved file
MARKER_RX = re.compile(r"^(<{7}|>{7})(\s|$)", re.M)
DIRTY_WT_RX = re.compile(r"lane worktree (.+?) is dirty")
LIMIT_RX = re.compile(r"usage limit|rate[ -]?limit|\b429\b|limit reached|overloaded", re.I)
VERDICT_RX = re.compile(r"^\s*VERDICT:\s*(PASS|FAIL)\b", re.I | re.M)
ROW_RX = re.compile(r"^\|\s*(\d{3})\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*$")
# plan 019: a plan doc's first `Depends on:` line names the plans it builds on
DEPENDS_RX = re.compile(r"^Depends on:\s*(.+)$", re.M)
DEP_ID_RX = re.compile(r"(?<![0-9A-Za-z])\d{3}(?![0-9A-Za-z])")
NEED_RX = re.compile(r"^\d{3}$")
# FLEET item 1: physical acts, passwords and OAuth grants wait for the operator;
# per-host values (gitignored config/local.json) are set on the host, not by a lane.
SKIP_TAGS = (("operator", re.compile(r"^(OPERATOR\b|.*\(physical\))|\bphysical\b|"
                                     r"\bpasswords?\b|\boauth\b|\bper-host\b", re.I)),
             ("other-tree", re.compile(r"^MAIN\b")),
             ("info", re.compile(r"^(NOTE|INFO)\s*[:(]", re.I)))
STOP = {"the", "and", "for", "from", "with", "per", "via", "tab", "new", "plan", "into",
        "by", "of", "a", "an", "to", "in", "on", "vs"}
# Plan 096: no pytest - the loop's cached gate run on the same tree is the
# authoritative whole suite; the verifier reviews, it does not re-gate.
VERIFY_EXTRA = ("--allowedTools",
                "Bash(git diff:*),Bash(git status:*),"
                "Bash(npm test:*),Bash(node --test:*),Bash(python tools/leak_sweep.py:*)")
DEEP_EXTRA = ("--permission-mode", "acceptEdits", "--allowedTools",
              ew_lane.CODE_EXTRA[3] + ",WebFetch,WebSearch")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0)
_BREAKAWAY = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)


# ---------------------------------------------------------------- small helpers

def ascii_text(text, limit=None):
    table = {0x2018: "'", 0x2019: "'", 0x201C: '"', 0x201D: '"', 0x2013: "-",
             0x2014: "-", 0x2026: "...", 0xA0: " ", 0x0D: ""}
    s = str(text).translate(table)
    s = s.encode("ascii", "replace").decode("ascii")
    return s[:limit] if limit else s


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(ascii_text(text).encode("ascii"))
    tmp.replace(path)


HOP_RX = re.compile(r"^\s*HOP:\s*\d+\s*$")


def with_hop(body, n):
    """`HOP: n` under the title line (FLEET item 14 d), replacing any HOP line."""
    lines = [ln for ln in (body or "").splitlines() if not HOP_RX.match(ln)]
    at = 1 if lines and lines[0].lstrip().startswith("#") else 0
    lines.insert(at, f"HOP: {n}")
    return "\n".join(lines).rstrip("\n") + "\n"


def read_jsonl(path):
    """Dict lines of a JSON-lines file; [] when absent; bad lines skipped."""
    try:
        raw = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out = []
    for line in raw.splitlines():
        with contextlib.suppress(ValueError):
            doc = json.loads(line)
            if isinstance(doc, dict):
                out.append(doc)
    return out


def read_json(path, default=None):
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
    return doc


def iso(epoch):
    return _dt.datetime.fromtimestamp(epoch).astimezone().isoformat(timespec="seconds")


def _norm_path(p):
    return os.path.normcase(os.path.abspath(str(p)))


def epoch_of(text):
    """Epoch seconds of an ISO timestamp (a naive one is local time), or None."""
    try:
        return _dt.datetime.fromisoformat(str(text)).timestamp()
    except (TypeError, ValueError):
        return None


def eta_label(seconds):
    return eta.fmt(seconds)[1:-1]  # "[~45m]" -> "~45m"


def load_config(root):
    doc = read_json(Path(root) / "config" / "local.json", {}) or {}
    loop = doc.get("loop") if isinstance(doc.get("loop"), dict) else {}
    # Fleet convention: MAIN byte-copies notes into <root>/moon_sync_inbox
    # (gitignored); replies go to <root>/moon_sync_outbox. Config overrides.
    return {"inbox_dir": loop.get("inbox_dir") or str(Path(root) / INBOX_REL),
            "outbox_dir": loop.get("outbox_dir") or str(Path(root) / OUTBOX_REL),
            # plan 093: where a reply to <CODE> is copied (per-host, gitignored)
            "dest_inboxes": {str(k).upper(): str(v) for k, v in
                             (loop.get("dest_inboxes") or {}).items()
                             if v} if isinstance(loop.get("dest_inboxes"), dict) else {},
            "roster": loop.get("roster") or None,
            "max_new_plans_per_day": int(loop.get("max_new_plans_per_day",
                                                  DEFAULT_MAX_PLANS)),
            "max_notes_per_day": min(int(loop.get("max_notes_per_day", DEFAULT_MAX_NOTES)),
                                     DEFAULT_MAX_NOTES),
            "lane_timeout_s": int(loop.get("lane_timeout_s", LANE_TIMEOUT_S)),
            "tick_s": int(loop.get("tick_s", TICK_S)),
            "routes": load_routes(loop.get("routes"))}


def load_routes(over):
    """Plan 099: ROUTES with each valid per-kind override merged in. An unknown
    kind, a model that is not an alias / claude-* id, or an effort outside the
    kit's EFFORTS is ignored, so a config typo never stops a lane."""
    routes = dict(ROUTES)
    for kind, val in (over.items() if isinstance(over, dict) else ()):
        if kind not in routes or not isinstance(val, dict):
            continue
        model, effort = routes[kind]
        m, e = val.get("model", model), val.get("effort", effort)
        if isinstance(m, str) and _ROUTE_MODEL.match(m) and e in ROUTE_EFFORTS:
            routes[kind] = (m, e)
    return routes


def route_kind(rec):
    """Plan 099: the ROUTES key for a lane item. A resolve run is `resolve`
    whatever its base kind; a plan 085 data hand-off item is `data`; an
    unknown kind routes as a plan."""
    kind = rec.get("kind")
    if kind == "handoff" and _DATA_ID.match(str(rec.get("id", ""))):
        return "data"
    return kind if kind in ROUTES else "plan"


def route_kw(routes, kind, note, pick_effort):
    """{"model", "effort"} for one run: the route's model, its effort passed
    through the kit's pick_effort (validates it; an explicit effort wins)."""
    model, effort = routes.get(kind) or routes["plan"]
    return {"model": model, "effort": pick_effort(note, effort)}


def is_limit(text):
    return bool(text) and bool(LIMIT_RX.search(str(text)))


def next_number(root, rel, pattern, width):
    nums = [int(p.name[:width]) for p in (Path(root) / rel).glob(pattern)
            if p.name[:width].isdigit()]
    return (max(nums) + 1) if nums else 1


# ---------------------------------------------------------------- item records

class Items:
    """One JSON file per work item under ops/loop/control/loop_items/."""

    def __init__(self, root):
        self.dir = Path(root) / ITEMS_REL

    def get(self, iid):
        return read_json(self.dir / f"{iid}.json")

    def put(self, rec):
        rec["updated"] = iso(time.time())
        atomic_write(self.dir / f"{rec['id']}.json", json.dumps(rec, indent=1, sort_keys=True))
        return rec

    def all(self):
        out = {}
        for p in sorted(self.dir.glob("*.json")):
            rec = read_json(p)
            if isinstance(rec, dict) and rec.get("id"):
                out[rec["id"]] = rec
        return out
