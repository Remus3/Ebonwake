#!/usr/bin/env python3
"""EW loop tick (plan 015, operator order 2026-10-05).

    ew_loop.py tick [--dry-run] [--no-push]   one self-monitoring, idempotent pass
    ew_loop.py checklist                      print the last tick's checklist
    ew_loop.py lane <ID>                      (internal) run one dispatched item

One tick, under ops/loop/control/loop.lock (fleet_watch.watch_lock):
  a. HALT file -> status "halted", exit 0. A busy lock -> exit 0, nothing written.
  b. Inbox (config loop.inbox_dir / loop.outbox_dir): new notes by mtime through
     fleet_watch.run_source (baseline first), then kit v8 fleet_inbox (FLEET
     item 14): classify() free; skip / ack = a seen-ledger line, no note;
     ORDER / FIX / RULING escalate to a lane item, answered after merge; else
     ONE triage spawn (TRIAGE_SPAWN: sonnet, low, bare; kind triage). Answers
     to one destination go in ONE batch note, HOP lines, OutboundCap (6 a
     day). No governor slot (kit ruling: acknowledgements stay outside).
  d. Finished lane worktrees (before c, so a dirty worktree never blocks a
     claim): gates, review-lane verifier (refute rounds capped at 3, then
     accept and record), commit, merge --no-ff into main, flip the ROADMAP row
     in main inside the merge commit (never in the lane commit); a conflict in
     ROADMAP.md alone is resolved row by row (resolve_roadmap). Any other
     conflict keeps the lane commit under refs/ew/keep/<id> and the item
     goes back out as a `resolve` run (plan 058, before new rows, at most
     MAX_ATTEMPTS per item) through the same gates / verifier / merge.
  c. Work list = open ROADMAP rows + hand-off items not tagged OPERATOR /
     physical / MAIN / NOTE. Each item is dispatched to a free lane as a
     DETACHED `ew_loop.py lane <ID>` process that runs tools/ew_lane.run_lane
     (kit lane + own worktree + one governor slot at the call, fail closed).
  e. Idle (nothing open, nothing in flight): one deep-dive lane per day.
  f. Spawning stops at RUNS_CAP - 3 and during a usage-limit backoff
     (ops/loop/control/backoff.json, 30 min doubling, cap 6 h).
  g. inbox_status.json (kit write_status, next_tick) and
     ops/loop/control/progress/loop.json with a "checklist" array (FLEET item
     13 d: remaining tasks as kit rows {id, task, state, eta_s}, at most 20;
     "fire" = this tick's run count, the item-13 session number).
  Push (unless --no-push): main clean, gates green, leak_sweep --pre-push
  clean, local main ahead of origin/main; at most one push per tick.

Every side effect goes through Deps, so tests never spawn, never touch a git
remote and never call schtasks. Paths are resolved at run time.
"""

import argparse
import contextlib
import datetime as _dt
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INBOX_REL = "moon_sync_inbox"
OUTBOX_REL = "moon_sync_outbox"
sys.path.insert(0, str(ROOT / "tools"))
import eta  # noqa: E402
import ew_lane  # noqa: E402

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
NOTE_HEAD = 600
NOTE_MAX = 20000
DONE_STATES = ("merged", "no-change", "failed")
# lane-dirty: the kit refused the claim on a dirty lane worktree; it waits
# until that worktree is clean and unheld, then retries (recover_lane_dirty);
# gave-up: MAX_ATTEMPTS refused / lost dispatches, error kept.
ATTENTION_STATES = ("merge-conflict", "failed-dirty", "lane-dirty", "gave-up")
# an item in these states holds its unmerged work in rec["worktree"]
HOLD_STATES = ("ran", "committed")
IN_FLIGHT = ("dispatched",) + HOLD_STATES
# plan 058: a conflicting lane commit is kept under this ref in the main
# checkout (a re-claimed lane worktree can never orphan it) until it merges
KEEP_REF = "refs/ew/keep/"
# a kept record in these states still waits on its commit reaching main
RESOLVABLE = ("merge-conflict", "refused", "lost", "paused", "no-change")
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
             ("info", re.compile(r"^(NOTE|INFO)\s*:", re.I)))
STOP = {"the", "and", "for", "from", "with", "per", "via", "tab", "new", "plan", "into",
        "by", "of", "a", "an", "to", "in", "on", "vs"}
VERIFY_EXTRA = ("--allowedTools",
                "Bash(git diff:*),Bash(git status:*),Bash(python -m pytest:*),"
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
            "max_new_plans_per_day": int(loop.get("max_new_plans_per_day",
                                                  DEFAULT_MAX_PLANS)),
            "max_notes_per_day": min(int(loop.get("max_notes_per_day", DEFAULT_MAX_NOTES)),
                                     DEFAULT_MAX_NOTES),
            "lane_timeout_s": int(loop.get("lane_timeout_s", LANE_TIMEOUT_S)),
            "tick_s": int(loop.get("tick_s", TICK_S))}


def is_limit(text):
    return bool(text) and bool(LIMIT_RX.search(str(text)))


# ---------------------------------------------------------------- work list

def roadmap_rows(text):
    """[{"id", "title", "status", "open"}] from the ROADMAP table."""
    rows = []
    for line in text.splitlines():
        m = ROW_RX.match(line)
        if m:
            rows.append({"id": m.group(1), "title": m.group(2), "status": m.group(3),
                         "open": m.group(3).startswith("[ ]")})
    return rows


def flip_roadmap(text, plan_id, note):
    """The ROADMAP text with row plan_id's "[ ] ..." status set to "[x] note"."""
    out = []
    for line in text.split("\n"):
        m = ROW_RX.match(line)
        if m and m.group(1) == plan_id and m.group(3).startswith("[ ]"):
            line = f"| {m.group(1)} | {m.group(2)} | [x] {note} |"
        out.append(line)
    return "\n".join(out)


def plan_depends(text):
    """The three-digit plan ids named by the first `Depends on:` line, in order
    ("none" or no such line -> [])."""
    m = DEPENDS_RX.search(text or "")
    out = []
    for dep in DEP_ID_RX.findall(m.group(1)) if m else ():
        if dep not in out:
            out.append(dep)
    return out


def plan_doc(root, plan_id):
    """docs/plans/<id>-*.md text, or None when there is none."""
    for p in sorted((Path(root) / "docs" / "plans").glob(f"{plan_id}-*.md")):
        with contextlib.suppress(OSError):
            return p.read_text(encoding="utf-8", errors="replace")
    return None


def base_kind(rec):
    """The item's own kind; a plan 058 resolve run keeps it in base_kind."""
    return rec.get("base_kind") or rec.get("kind")


# a resolve item copies its record minus these per-run fields
RUN_FIELDS = ("state", "pid", "rc", "error", "verdict", "verify_errors", "finished",
              "updated", "prompt", "worktree", "dispatched")


def resolve_item(rec):
    item = {k: v for k, v in rec.items() if k not in RUN_FIELDS}
    item.update(kind="resolve", base_kind=base_kind(rec))
    item["prompt"] = resolve_prompt(item)
    return item


def dependency_cycles(graph):
    """Each cycle of {id: [dep ids]} once, as [a, b, ..., a]."""
    cycles, state = [], {}

    def visit(node, stack):
        state[node] = 1
        stack.append(node)
        for dep in graph.get(node, ()):
            if state.get(dep) == 1:
                cycles.append(stack[stack.index(dep):] + [dep])
            elif dep in graph and not state.get(dep):
                visit(dep, stack)
        stack.pop()
        state[node] = 2

    for node in sorted(graph):
        if not state.get(node):
            visit(node, [])
    return cycles


def roadmap_flip_time(git, main, dep):
    """ISO commit time of the first-parent commit on main that introduced row
    dep's `[x]` line (the --no-ff merge commit for a loop merge), or None.
    Fallback for dependencies merged before merge() recorded merged_at."""
    r = git(["log", "-m", "--first-parent", "-1", "--format=%cI",
             f"-G^\\| {dep} \\|.*\\[x\\]", "--", ROADMAP_REL.as_posix()], main)
    out = (r.stdout or "").strip() if r.returncode == 0 else ""
    return out.splitlines()[0] if out else None


def resolve_roadmap(base, ours, theirs):
    """Row-level three-way merge of ROADMAP.md (fix-0130), or None. Rows are
    keyed by plan id: a row only the lane changed or added takes the lane's
    line, a row both sides changed keeps main's (main is the ledger). Any
    prose (non-row) change on both sides is not guessed at: None."""
    def split(text):
        rows, prose = {}, []
        for line in text.split("\n"):
            m = ROW_RX.match(line)
            if m:
                rows[m.group(1)] = line
            else:
                prose.append(line)
        return rows, prose

    (b_rows, b_prose), (o_rows, o_prose), (t_rows, t_prose) = \
        split(base), split(ours), split(theirs)
    if t_prose != b_prose and t_prose != o_prose:
        return None
    lines = ours.split("\n")
    for iid, line in t_rows.items():
        if line == b_rows.get(iid):
            continue
        if iid not in o_rows:
            at = [i for i, x in enumerate(lines) if ROW_RX.match(x)]
            last = at[-1] if at else len(lines) - 1
            lines.insert(last + 1, line)
            o_rows[iid] = line
        elif o_rows[iid] == b_rows.get(iid):
            lines[lines.index(o_rows[iid])] = line
    for iid, line in b_rows.items():
        if iid not in t_rows and o_rows.get(iid) == line:
            lines.remove(line)
    return "\n".join(lines)


def _norm_words(text):
    return re.findall(r"[a-z0-9]+", text.lower())


def handoff_items(text):
    """Bullets under "Carried forward" as [{"id", "text", "skip"}]; skip is
    None or the tag that keeps the loop off it (operator / other-tree / info)."""
    items, cur, inside = [], None, False
    for line in text.splitlines():
        if line.lower().startswith("carried forward"):
            inside = True
            continue
        if not inside:
            continue
        if line.startswith("- "):
            cur = [line[2:].strip()]
            items.append(cur)
        elif line.startswith("  ") and cur is not None and line.strip():
            cur.append(line.strip())
        elif line.strip() and not line.startswith(" "):
            break
    out = []
    for parts in items:
        body = " ".join(parts)
        skip = next((tag for tag, rx in SKIP_TAGS if rx.search(body)), None)
        hid = "H" + hashlib.sha1(" ".join(_norm_words(body)).encode()).hexdigest()[:6]
        out.append({"id": hid, "text": body, "skip": skip})
    return out


def title_tokens(title):
    return {w for w in _norm_words(title) if len(w) > 2 and w not in STOP}


def is_duplicate(title, existing, threshold=0.6):
    """Normalized token overlap |A&B| / min(|A|, |B|) >= threshold."""
    a = title_tokens(title)
    for other in existing:
        b = title_tokens(other)
        if a and b and len(a & b) / min(len(a), len(b)) >= threshold:
            return other
    return None


def plan_titles(root):
    titles = {}
    for p in sorted((Path(root) / "docs" / "plans").glob("[0-9][0-9][0-9]-*.md")):
        first = p.read_text(encoding="utf-8", errors="replace").split("\n", 1)[0]
        titles[p.name[:3]] = re.sub(r"^#\s*Plan\s*\d+\s*-\s*", "", first).strip()
    return titles


def next_number(root, rel, pattern, width):
    nums = [int(p.name[:width]) for p in (Path(root) / rel).glob(pattern)
            if p.name[:width].isdigit()]
    return (max(nums) + 1) if nums else 1


# ---------------------------------------------------------------- prompts

GATES = ("Gates before you finish (exactly what ci runs): `python -m ruff check server "
         "tools tests`, `python -m pytest -q` and `npm test --prefix app` green; every authored file ASCII + LF; no absolute machine path, drive "
         "letter, account id or email in a tracked file. Game ToS floor in CLAUDE.md "
         "is absolute (no game memory, injection, packets, client files or input to "
         "the game window; read-only GETs; robots.txt respected). Do NOT commit, do "
         "NOT edit CLAUDE.md, EW-NEXT-SESSION.txt or ops/fleet_kit/. Any deviation "
         "from the plan goes into an 'As-built deviations' section of the plan doc "
         "(decision, alternatives, why, reverses if) - adjudicate it yourself, never "
         "wait. Write the v7 checklist (FLEET item 13 d) into your progress JSON "
         "ops/loop/control/progress/{task}.json: the FLEET item 12 fields plus "
         "\"checklist\": [{{\"id\", \"task\", \"state\", \"eta_s\"}}, ...] - "
         "remaining steps only, ASCII, updated after each step.")


NO_ROADMAP = ("Do NOT edit docs/plans/ROADMAP.md: the loop flips your row in main when "
              "it merges (parallel lanes editing it collided). ")


def plan_prompt(item):
    return (f"You are an Ebonwake (EW) build lane in a detached git worktree. Read "
            f"CLAUDE.md first. Task: implement plan {item['id']} per docs/plans/"
            f"{item['id']}-*.md ({item['title']}) - TDD, stdlib only for Python. "
            + NO_ROADMAP + GATES.format(task=f"p{item['id']}-build"))


def handoff_prompt(item):
    return ("You are an Ebonwake (EW) build lane in a detached git worktree. Read "
            "CLAUDE.md first. Work this hand-off item: " + item["text"] + " If it needs "
            "no repo change, change nothing and say why in one line. "
            + NO_ROADMAP + GATES.format(task=item["id"]))


def deep_dive_prompt(root, date, max_plans, today_titles):
    rn = next_number(root, "docs/research", "[0-9][0-9][0-9][0-9]-*.md", 4)
    pn = next_number(root, "docs/plans", "[0-9][0-9][0-9]-*.md", 3)
    titles = "; ".join(f"{k} {v}" for k, v in sorted(today_titles.items()))
    return ("You are the Ebonwake (EW) deep-dive lane (idle mode) in a detached git "
            "worktree. Read CLAUDE.md and docs/research/0001-bdo-data-and-tos.md first. "
            "Research, read-only and unauthenticated, robots.txt respected: current BDO "
            "NA patch notes, events, coupons, public data sources, community tools and "
            "APIs, and ideas from public fleet-sibling projects. Write "
            f"docs/research/{rn:04d}-deep-dive-{date}.md (sources with dates). Propose "
            f"AT MOST {max_plans} new plans, numbered from {pn:03d}, each as "
            "docs/plans/NNN-<slug>.md (first line '# Plan NNN - Title') plus a "
            "docs/plans/ROADMAP.md row '| NNN | title | [ ] open |' and a one-line value "
            "rank in the Ranking text. Do not duplicate an existing plan: " + titles + ". "
            "Write no code. " + GATES.format(task="deep-dive"))


def verify_prompt(item, rnd):
    return (f"You are the EW review-lane verifier, refute round {rnd}/{MAX_ROUNDS}. This "
            f"worktree's uncommitted diff (git status, git diff HEAD) implements: "
            f"{item['label']}. Read-only: never edit. Re-run the gates, check the plan's "
            "acceptance, the Game ToS floor in CLAUDE.md, ASCII + LF, and no machine "
            "path. End with exactly one line 'VERDICT: PASS' or 'VERDICT: FAIL', then "
            "numbered findings for a FAIL.")


def fix_prompt(item, rnd, findings):
    return (f"You are the EW producer answering refute round {rnd}/{MAX_ROUNDS} for "
            f"{item['label']} in this worktree. Fix every finding below, or record why "
            "it stands in the plan's 'As-built deviations'. " + GATES.format(task="fix")
            + "\n\nFindings:\n" + "\n".join(findings)[:8000])


def resolve_prompt(item):
    """Plan 058: the lane worker has already started the merge of the kept
    lane commit onto current main in the lane's worktree."""
    ref, doc = item["keep_ref"], ""
    if item.get("base_kind") == "plan":
        doc = f" (docs/plans/{item['id']}-*.md)"
    return ("You are an Ebonwake (EW) resolve lane in a detached git worktree. Read "
            f"CLAUDE.md first. The loop could not merge {item['label']} into main: a merge "
            "conflict. Before you started, the loop ran `git merge --no-ff --no-commit "
            f"{ref}` (the kept lane commit) onto current main in THIS worktree; `git "
            "status` lists the unmerged paths. Resolve every conflict keeping BOTH sides' "
            "features (main's newer work and the lane's), leave no conflict marker, and "
            "record decision / alternatives / why for each non-trivial resolution in the "
            f"'As-built deviations' section of the plan doc{doc}, or in your final reply "
            "when the item has no plan doc. Leave the merge uncommitted: the loop "
            "verifies, commits and merges it. " + NO_ROADMAP
            + GATES.format(task=f"p{item['id']}-resolve"))


def inbox_prompt(name, text, context=None):
    return ("You are the Ebonwake (EW) session answering ONE channel note. Reply with the "
            "note body only: markdown, ASCII, first line '# From EW - ANSWER re " + name
            + "'. Answer what is asked; do not change files. Never put a directory name, "
            "account id or email in the reply." + (" " + context if context else "")
            + "\n\n--- NOTE " + name + " ---\n" + text[:NOTE_MAX] + "\n--- END NOTE ---")


def order_id(name):
    return "N" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:6]


def order_prompt(order):
    return ("You are an Ebonwake (EW) build lane in a detached git worktree. Read "
            "CLAUDE.md first. Carry out the channel ORDER note below in THIS tree. Do "
            "every item that needs only repo files. An item needing a password, an OAuth "
            "grant, a download, a tool outside your allow list or a physical act is left "
            "undone and listed as BLOCKED (one line, the reason) in a section 'Order "
            + order["id"] + " - blocked items' at the end of docs/plans/ROADMAP.md. Do not "
            "write the ANSWER note; the loop writes it after merge. "
            + GATES.format(task=order["id"])
            + "\n\n--- NOTE " + order["note"] + " ---\n" + order.get("text", "")[:NOTE_MAX]
            + "\n--- END NOTE ---")


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


def dispatchable(rec, open_ids=()):
    """paused (the lane worker found HALT / backoff / runs cap before it ran)
    costs no attempt; refused / lost get MAX_ATTEMPTS dispatches; blocked
    (plan 019) waits until no id it needs is an open ROADMAP row, for at most
    MAX_ATTEMPTS blocked runs; merge-conflict (plan 058) with a kept commit
    gets MAX_ATTEMPTS resolve runs."""
    if rec is not None and rec.get("state") == "merge-conflict":
        return bool(rec.get("keep_ref")) and rec.get("resolve_runs", 0) < MAX_ATTEMPTS
    if rec is not None and rec.get("state") == "blocked":
        return (rec.get("blocked_runs", 0) < MAX_ATTEMPTS
                and not set(rec.get("needs") or ()) & set(open_ids))
    return rec is None or rec.get("state") == "paused" or (
        rec.get("state") in ("refused", "lost") and rec.get("attempts", 0) < MAX_ATTEMPTS)


# ---------------------------------------------------------------- dependencies

def _python():
    p = Path(sys.executable)
    alt = p.with_name("python.exe") if p.name.lower() == "pythonw.exe" else p
    return str(alt if alt.exists() else p)


def _pythonw():
    p = Path(sys.executable).with_name("pythonw.exe")
    return str(p if p.exists() else sys.executable)


def _git(args, cwd, input=None):
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                          input=input, env=env, timeout=600, creationflags=_NO_WINDOW)


CI_WORKFLOW_REL = Path(".github/workflows/ci.yml")
CI_SETUP_RX = re.compile(r"\bpip\s+install\b")


def ci_gate_commands(text):
    """The `run:` commands of the ci workflow, in order, minus environment
    setup (pip install). The loop's gates ARE these commands (fix-0130), so a
    lane can never merge what ci rejects."""
    cmds, block, indent = [], False, 0
    for line in text.splitlines():
        if block:
            if not line.strip():
                continue
            if len(line) - len(line.lstrip()) > indent:
                cmds.append(line.strip())
                continue
            block = False
        m = re.match(r"^(\s*)(?:-\s+)?run:\s*(.*?)\s*$", line)
        if not m:
            continue
        if m.group(2) in ("|", ">", "|-", ">-"):
            block, indent = True, len(m.group(1))
        elif m.group(2):
            cmds.append(m.group(2).strip("\"'"))
    return [c for c in cmds if not CI_SETUP_RX.search(c)]


def _run_gate(argv, cwd):
    r = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True,
                       timeout=GATE_TIMEOUT_S, creationflags=_NO_WINDOW,
                       encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "")[-1500:] + (r.stderr or "")[-500:]


SHELL_OPERATOR_CHARS = set("();<>|&")


def _shell_operator(cmd):
    """The first unquoted shell operator in a ci run command, or None. The
    loop runs gates as argv without a shell (one local Python, not ci's
    3.11 + 3.14 matrix), so `a && b` would pass "&&" to a and never run b."""
    lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    for tok in lex:
        if tok and set(tok) <= SHELL_OPERATOR_CHARS:
            return tok
    return None


def _gate_argv(cmd):
    argv = shlex.split(cmd)
    if argv[0] == "python":
        argv[0] = _python()
    elif argv[0] == "npm" and sys.platform == "win32":
        argv[0] = "npm.cmd"
    return argv


def _gates(cwd, run=_run_gate):
    """(ok, detail): exactly the ci workflow's gate commands, in cwd's own
    .github/workflows/ci.yml, in order; fail closed without one."""
    try:
        cmds = ci_gate_commands((Path(cwd) / CI_WORKFLOW_REL).read_text(encoding="utf-8"))
    except OSError:
        return False, f"no {CI_WORKFLOW_REL.as_posix()}: gates fail closed"
    if not cmds:
        return False, f"no gate command in {CI_WORKFLOW_REL.as_posix()}: gates fail closed"
    for cmd in cmds:
        op = _shell_operator(cmd)
        if op is not None:
            return False, f"{cmd}: shell operator {op!r} unsupported by loop gates: fail closed"
    for cmd in cmds:
        try:
            rc, tail = run(_gate_argv(cmd), cwd)
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            return False, f"{cmd}: {type(exc).__name__}"
        if rc != 0:
            return False, f"{cmd} rc={rc}\n{tail}"
    return True, f"ci gates green ({len(cmds)})"


def _leak_pre_push(cwd, ref_line):
    r = subprocess.run([_python(), "tools/leak_sweep.py", "--pre-push"], cwd=str(cwd),
                       input=ref_line + "\n", capture_output=True, text=True, timeout=600,
                       creationflags=_NO_WINDOW)
    return r.returncode == 0


def _launch(root, iid, popen=subprocess.Popen):
    """Start the lane worker detached. It breaks away from the Task Scheduler
    job when the job allows it, so the tick's end never ends the lane."""
    argv = [_pythonw(), str(Path(root) / "tools" / "ew_loop.py"), "lane", iid]
    kw = dict(cwd=str(root), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
              stderr=subprocess.DEVNULL, close_fds=True)
    flags = _NO_WINDOW | _DETACHED
    try:
        return popen(argv, creationflags=flags | _BREAKAWAY, **kw).pid
    except OSError:
        return popen(argv, creationflags=flags, **kw).pid


class Deps:
    """Every side effect of a tick. Tests override attributes."""

    def __init__(self, root=ROOT, **over):
        self.root = Path(root)
        k, fw, fl = ew_lane.kit(), _load_watch(), ew_lane.lanes()
        self.kit, self.watch = k, fw
        self.inbox = ew_lane._load("fleet_inbox", "ops/fleet_kit/fleet_inbox.py")
        self.checklist = ew_lane._load("fleet_checklist", "ops/fleet_kit/fleet_checklist.py")
        self.budget = k.RunBudget(self.root / k.BUDGET_REL)
        self.spawn = k.spawn
        self.should_skip = k.should_skip
        self.pick_effort = k.pick_effort
        self.write_status = k.write_status
        self.lock = lambda path: fw.watch_lock(path)
        self.lane_state = lambda: fl.repo_lane_state(self.root, ew_lane.LANE_CAP)
        self.lane_worktree = lambda i: fl.worktree_path(self.main_tree, CODE, i)
        self.pid_alive = fl.pid_alive
        self.main_tree = fl.main_tree(self.root)
        self.launch = lambda iid: _launch(self.root, iid)
        self.git = _git
        self.gates = _gates
        self.leak_pre_push = _leak_pre_push
        self.estimate = eta.estimate
        self.record = eta.record
        self.clock = time.time
        for key, val in over.items():
            setattr(self, key, val)


def _load_watch():
    return ew_lane._load("fleet_watch", "ops/fleet_kit/fleet_watch.py")


# ---------------------------------------------------------------- backoff

def backoff_until(root):
    doc = read_json(Path(root) / BACKOFF_REL, {}) or {}
    return float(doc.get("until", 0) or 0)


def backoff_hit(root, now, reason):
    doc = read_json(Path(root) / BACKOFF_REL, {}) or {}
    prev = int(doc.get("delay_s", 0) or 0)
    delay = min(prev * 2, BACKOFF_CAP_S) if prev else BACKOFF_BASE_S
    doc = {"delay_s": delay, "until": now + delay, "reason": ascii_text(reason, 200),
           "since": iso(now)}
    atomic_write(Path(root) / BACKOFF_REL, json.dumps(doc))
    return doc


def backoff_clear(root):
    p = Path(root) / BACKOFF_REL
    if p.exists() and (read_json(p, {}) or {}).get("delay_s"):
        atomic_write(p, json.dumps({"delay_s": 0, "until": 0}))


def spawn_block(root, budget, now):
    """Why spawning must stop now (item f), or None. Shared by the tick and the
    detached lane worker, which re-checks after dispatch."""
    if (Path(root) / HALT_REL).exists():
        return "halted"
    if backoff_until(root) > now:
        return "backoff"
    if not budget.readable():
        return "budget unreadable"
    if budget.used() >= budget.cap - HEADROOM:
        return "runs cap"
    return None


# ---------------------------------------------------------------- the tick

class Tick:
    def __init__(self, deps, dry_run=False, no_push=False):
        self.d, self.dry, self.no_push = deps, dry_run, no_push
        self.root = deps.root
        self.cfg = load_config(self.root)
        self.items = Items(self.root)
        self.log = []
        self.pushed = None
        self.fi = deps.inbox
        self.cap = self.fi.OutboundCap(self.root, cap=self.cfg["max_notes_per_day"],
                                       clock=deps.clock)
        self.awaiting = self.capped = self.paused = 0
        self.open_ids = set()  # open ROADMAP row ids, set by work_list()

    # -- gates on spawning (item f)
    def blocked(self):
        return spawn_block(self.root, self.d.budget, self.d.clock())

    def spawn(self, prompt, kind="build", **kw):
        """kit spawn + backoff bookkeeping. Returns the usage line or None.
        kind (kit v8: build / inbox / triage) labels the usage line."""
        try:
            line = self.d.spawn(self.root, CODE, prompt, stdin=True, kind=kind, **kw)
        except self.d.kit.Refused as exc:
            self.step(f"spawn refused: {exc}")
            if is_limit(str(exc)):
                backoff_hit(self.root, self.d.clock(), str(exc))
            return None
        text = f"{line.get('error') or ''} {line.get('result') or ''}"
        if line.get("rc") != 0 and is_limit(text):
            backoff_hit(self.root, self.d.clock(), text)
            self.step("usage limit hit: backoff")
            return None
        if line.get("rc") == 0 and not line.get("error"):
            backoff_clear(self.root)
        return line

    def step(self, text):
        self.log.append(ascii_text(text, 200))

    # -- b. inbox
    def inbox(self):
        inbox, outbox = self.cfg["inbox_dir"], self.cfg["outbox_dir"]
        if not inbox or not Path(inbox).is_dir():
            self.step("inbox unconfigured")
            return
        if not outbox:
            self.step("outbox unconfigured")
            return
        inbox, outbox = Path(inbox), Path(outbox)

        def fetch():
            files = [p for p in inbox.iterdir() if p.is_file() and not p.name.startswith(".")]
            return [p.name for p in sorted(files, key=lambda p: (p.stat().st_mtime, p.name))]

        state = self.d.watch.WatchState(self.root / WATCH_REL)
        if self.dry:
            seen = set(state.seen("inbox"))
            new = [n for n in fetch() if n not in seen]
            self.step(f"inbox: {len(new)} new (dry run)")
            return

        def deliver(names):
            batch = {}  # destination -> [(note, answer text, incoming hop)]
            pending = [n for n in names if not self.answer(inbox / n, outbox, batch)]
            pending += self.send_batches(batch, outbox)
            if pending:  # left unseen: run_source re-offers them next tick
                return {"delivered": False, "detail": f"{len(pending)} pending"}
            return {"delivered": True}

        res = self.d.watch.run_source(state, "inbox", fetch, deliver,
                                      lambda s, n, d: {"delivered": True})
        outcome, detail = res["outcome"], res["detail"]
        if outcome == "deliver-failed":
            # Pending by design is not a delivery failure (2026-10-05 loop.json
            # read "deliver-failed 2 2 pending": two ORDERs awaiting lanes).
            n = int(detail.split()[0]) if detail[:1].isdigit() else -1
            failed = n - self.awaiting - self.capped - self.paused
            if n >= 0 and failed <= 0:
                outcome = "pending"
            detail = (f"pending: {self.awaiting} order(s) awaiting lane, {self.capped} "
                      f"capped, {self.paused} paused, {max(failed, 0)} failed")
        self.step(f"inbox: {outcome} {len(res['new'])} new; {detail}"[:200])

    def handled(self, name, outbox):
        """Already settled: in the kit seen ledger, or answered by a pre-v8 tick."""
        if name in self.fi.seen(self.root):
            return True
        legacy = read_jsonl(self.root / LEGACY_LEDGER_REL)  # plan 015 deviation 14e
        if any(d.get("note") == name for d in legacy):
            return True
        stem = Path(name).stem
        return outbox.is_dir() and any(p.name.endswith(f"-re-{stem}.md")
                                       for p in outbox.iterdir())

    def answer(self, path, outbox, batch):
        """True when the note needs nothing more (handled now or before); False =
        pending (re-offered next tick). FLEET item 14: skip / ack are ledger
        lines, ORDER / FIX / RULING escalate, anything else is triaged once."""
        name = path.name
        if self.handled(name, outbox):
            return True
        text = path.read_text(encoding="utf-8", errors="replace")
        head = text[:NOTE_HEAD]
        dec = self.fi.classify(name, CODE, head)
        skip = self.d.should_skip(name, CODE, head)
        # classify() decides escalation (class ORDER / FIX / RULING by the
        # note's own sender-class token); a filename that merely QUOTES an
        # order's name (an ACK or ANSWER re- it) is never escalated.
        if dec.action == self.fi.WORK:
            return self.answer_order(name, text, dec, outbox, batch)
        if skip or dec.action in (self.fi.SKIP, self.fi.ACK):
            self.fi.mark_seen(self.root, name, dec, clock=self.d.clock)
            self.step(f"inbox {dec.action} ({skip or dec.reason}): {name}")
            return True
        to = dec.sender or "MAIN"
        if to not in batch and self.cap.used() + len(batch) >= self.cap.cap:
            self.capped += 1
            self.step(f"inbox: daily note cap {self.cap.cap} reached")
            return False
        if self.blocked():
            self.paused += 1
            return False
        line = self.spawn(self.fi.triage_prompt(name, text[:NOTE_MAX]), note=name,
                          writes_code=False, kind="triage", **self.fi.TRIAGE_SPAWN)
        if not line or line.get("rc") != 0 or line.get("result") is None:
            return False
        verdict, body = self.fi.parse_verdict(ascii_text(line["result"]))
        if verdict != "ANSWER":
            self.fi.mark_seen(self.root, name, dec, verdict=verdict, clock=self.d.clock)
            self.step(f"inbox triage {verdict} (ledger, no note): {name}")
            return True
        batch.setdefault(to, []).append((name, body, dec))
        return True

    def send_batches(self, batch, outbox):
        """ONE note per destination (kit batch_note); returns notes left pending."""
        pending = []
        stamp = _dt.datetime.fromtimestamp(self.d.clock()).strftime("%Y-%m-%d-%H%M")
        for to, parts in batch.items():
            hop_n = self.fi.next_hop(max(dec.hop for _, _, dec in parts))
            fname, body, names = self.fi.batch_note(CODE, to, [(n, b) for n, b, _ in parts],
                                                    hop_n=hop_n, stamp=stamp)
            if not self.cap.allow("ANSWER"):
                self.capped += len(names)
                pending += names
                continue
            if not self.write_note(outbox / fname, body):
                pending += names
                continue
            self.cap.record(fname, "ANSWER", to, parts=len(parts))
            for n, _, dec in parts:
                self.fi.mark_seen(self.root, n, dec, verdict="ANSWER", clock=self.d.clock)
            self.step(f"inbox answered: {', '.join(names)} -> {to} (1/1 reached)"[:200])
        return pending

    def write_note(self, dest, body):
        body = ascii_text(body).rstrip("\n") + "\n"
        atomic_write(dest, body)
        return hashlib.sha256(dest.read_bytes()).hexdigest() == \
            hashlib.sha256(body.encode("ascii")).hexdigest()

    def answer_order(self, name, text, dec, outbox, batch):
        """An escalated ORDER / FIX / RULING: queued as a lane item, answered
        (one note, HOP incoming + 1, counted by OutboundCap) once it is done."""
        oid = order_id(name)
        rec = self.items.get(oid)
        if not rec or rec.get("state") not in DONE_STATES + ("adjudicate",):
            self.queue_order(oid, name, text)
            self.awaiting += 1
            return False  # answered after the lane item is done
        context = (f"EW's loop carried this order out as lane item {oid}: state "
                   f"{rec.get('state')}, verdict {rec.get('verdict', 'none')}, "
                   f"refute-rounds {rec.get('rounds', 0)}/{MAX_ROUNDS}, commit "
                   f"{rec.get('commit', 'none')}. Mark each item DONE in that commit, "
                   "or BLOCKED / NOT-APPLICABLE with the reason.")
        if self.cap.used() + len(batch) >= self.cap.cap:  # batches hold their slots
            self.capped += 1
            self.step(f"inbox: daily note cap {self.cap.cap} reached")
            return False
        if self.blocked():
            self.paused += 1
            return False
        line = self.spawn(inbox_prompt(name, text, context), note=name, writes_code=False,
                          model="sonnet", effort=self.d.pick_effort(name), timeout=1800,
                          kind="inbox")
        reply = (line or {}).get("result")
        if not line or line.get("rc") != 0 or not reply:
            return False
        stamp = _dt.datetime.fromtimestamp(self.d.clock()).strftime("%Y%m%d-%H%M")
        dest = outbox / f"{stamp}-from-{CODE}-ANSWER-re-{Path(name).stem}.md"
        if not self.write_note(dest, with_hop(ascii_text(reply), self.fi.next_hop(dec.hop))):
            return False
        self.cap.record(dest.name, "ANSWER", dec.sender or "MAIN")
        self.fi.mark_seen(self.root, name, dec, verdict="ANSWER", clock=self.d.clock)
        self.step(f"inbox answered: {name} (1/1 reached)")
        return True

    def orders(self):
        doc = read_json(self.root / ORDERS_REL, {}) or {}
        return [o for o in doc.get("orders", []) if isinstance(o, dict) and o.get("id")]

    def queue_order(self, oid, name, text):
        if self.dry or any(o["id"] == oid for o in self.orders()):
            return
        doc = {"orders": self.orders() + [{"id": oid, "note": name, "text": text,
                                           "title": ascii_text(name, 120)}]}
        atomic_write(self.root / ORDERS_REL, json.dumps(doc, indent=1))
        self.step(f"inbox escalated: {name} -> lane item {oid}")

    # -- lanes in flight
    def reap_lost(self):
        """Under the tick lock no dispatch is mid-flight, so a "dispatched"
        record without a pid is a launch that never happened (a crashed tick).
        A refused / lost record out of attempts becomes gave-up, never silent."""
        if self.dry:
            return
        for rec in self.items.all().values():
            state = rec.get("state")
            if state == "dispatched" and (not rec.get("pid") or
                                          not self.d.pid_alive(rec["pid"])):
                rec.update(state="lost", attempts=rec.get("attempts", 0))
                self.items.put(rec)
                self.step(f"{rec['id']}: lane process gone")
                state = "lost"
            if state in ("refused", "lost") and rec.get("attempts", 0) >= MAX_ATTEMPTS:
                rec["state"] = "gave-up"
                self.items.put(rec)
                self.step(f"{rec['id']}: gave up after {rec.get('attempts', 0)} attempts: "
                          f"{rec.get('error') or state}")
            if state == "blocked" and rec.get("blocked_runs", 0) >= MAX_ATTEMPTS:
                rec.update(state="gave-up",
                           error=f"blocked {rec['blocked_runs']} times on "
                                 f"{', '.join(rec.get('needs') or [])}")
                self.items.put(rec)
                self.step(f"{rec['id']}: gave up: {rec['error']}")

    # -- d. finished worktrees
    def finished(self):
        for rec in self.items.all().values():
            if rec.get("state") in ("ran", "committed"):
                if self.dry:
                    self.step(f"{rec['id']}: would review + merge")
                    continue
                self.process(rec)

    def dirty(self, wt):
        r = self.d.git(["status", "--porcelain"], wt)
        return r.returncode != 0 or bool(r.stdout.strip())

    def process(self, rec):
        wt = Path(rec["worktree"])
        if rec["state"] == "committed":
            return self.merge(rec)
        if not wt.is_dir() or not self.dirty(wt):
            if rec.get("rc") == 0 and self.blocked_marker(rec, wt):
                return
            rec["state"] = "no-change" if rec.get("rc") == 0 else "lost"
            self.items.put(rec)
            self.step(f"{rec['id']}: {rec['state']}")
            return
        rounds = rec.get("rounds", 0)
        while True:
            ok, detail = self.d.gates(wt)
            findings = [] if ok else ["gates failed: " + detail]
            findings += self.extra_checks(rec, wt)
            if not findings and rounds < MAX_ROUNDS:  # never a round 4 (rule 7)
                if self.blocked():
                    return  # retry next tick
                line = self.spawn(verify_prompt(rec, rounds + 1), note=f"lane-review-{rec['id']}",
                                  writes_code=False, cwd=wt, extra=VERIFY_EXTRA, kind="build",
                                  governor="queued", governor_timeout=GATE_TIMEOUT_S,
                                  timeout=GATE_TIMEOUT_S)
                if not line:
                    return  # refused or usage limit: backoff / next tick retries
                if line.get("rc") != 0:
                    # a crashed verifier is no verdict, but it is counted: after
                    # MAX_ROUNDS of them the item waits for the adjudicator
                    rec["verify_errors"] = rec.get("verify_errors", 0) + 1
                    rec["error"] = ascii_text(f"verifier rc={line.get('rc')} "
                                              f"{line.get('error') or ''}", 300)
                    self.items.put(rec)
                    self.step(f"{rec['id']}: verifier failed "
                              f"{rec['verify_errors']}/{MAX_ROUNDS}")
                    if rec["verify_errors"] < MAX_ROUNDS:
                        return
                    rec["verdict"] = "adjudicate"
                    break
                m = VERDICT_RX.findall(line.get("result") or "")
                if m and m[-1].upper() == "PASS":
                    rec["verdict"] = "PASS"
                    break
                findings = [(line.get("result") or "no verdict line")[-6000:]]
            if rounds >= MAX_ROUNDS:
                # after round 3 the adjudicator rules (CLAUDE.md rule 7): the
                # loop never self-accepts; the item waits unmerged for a session
                rec["verdict"] = "adjudicate"
                break
            if self.blocked():
                return
            rounds += 1
            rec["rounds"] = rounds
            self.items.put(rec)
            line = self.spawn(fix_prompt(rec, rounds, findings), note=f"lane-build-{rec['id']}",
                              writes_code=True, cwd=wt, extra=ew_lane.CODE_EXTRA, kind="build",
                              governor="queued", governor_timeout=self.cfg["lane_timeout_s"],
                              timeout=self.cfg["lane_timeout_s"])
            if not line:
                return
        rec["rounds"] = rounds
        self.commit(rec, wt, ok)

    def blocked_marker(self, rec, wt):
        """Plan 019 4a: True when this run's lane left a fresh `"status":
        "blocked"` progress marker (worktree first, then the main checkout)
        and the record is now blocked. Lane worktrees are reused, never
        cleaned, so a marker older than this run's dispatch is stale."""
        if rec.get("kind") != "plan":  # a resolve run is never a blocked plan
            return False
        rel = self.d.kit.PROGRESS_REL / f"p{rec['id']}-build.json"
        since = epoch_of(rec.get("dispatched"))
        for tree in (wt, self.d.main_tree):
            doc = read_json(Path(tree) / rel)
            if not isinstance(doc, dict) or doc.get("status") != "blocked":
                continue
            updated, needs = epoch_of(doc.get("updated")), doc.get("needs")
            if updated is None or not isinstance(needs, list) or not needs or \
                    not all(isinstance(n, str) and NEED_RX.match(n) for n in needs):
                self.step(f"{rec['id']}: malformed blocked marker ignored "
                          "(needs a three-digit 'needs' list and a parsable 'updated')")
                continue
            if since is None or updated < since:
                self.step(f"{rec['id']}: stale blocked marker ignored")
                continue
            needs = list(dict.fromkeys(needs))
            rec.update(state="blocked", needs=needs,
                       blocked_runs=rec.get("blocked_runs", 0) + 1)
            self.items.put(rec)
            self.step(f"{rec['id']}: blocked on {', '.join(needs)}")
            return True
        return False

    def rearm(self, rows):
        """Plan 019 4c, once per record: a no-change plan whose own row is
        still open and one of whose Depends-on rows flipped [x] after its
        dispatch ran against code that was not on main yet: blocked again."""
        row_open = {r["id"]: r["open"] for r in rows}
        for rec in self.items.all().values():
            iid = rec["id"]
            if rec.get("state") != "no-change" or "rearmed" in rec or not row_open.get(iid):
                continue
            deps = self.plan_deps(iid, row_open)
            if not deps:
                continue
            since = epoch_of(rec.get("dispatched"))
            late, pending = [], []
            for dep in deps:
                if row_open[dep]:
                    pending.append(dep)
                    continue
                flip = epoch_of((self.items.get(dep) or {}).get("merged_at"))
                if flip is None:
                    flip = epoch_of(roadmap_flip_time(self.d.git, self.d.main_tree, dep))
                if since is not None and flip is not None and flip > since:
                    late.append(dep)
            if pending and not late:
                continue  # decided once those rows flip
            if self.dry:
                self.step(f"{iid}: would {'re-arm' if late else 'settle no-change'}")
                continue
            if late:
                rec.update(state="blocked", needs=late + pending, rearmed=True)
                self.step(f"{iid}: re-armed, blocked on {', '.join(late + pending)}")
            else:
                rec["rearmed"] = False  # genuine no-change: never reconsidered
            self.items.put(rec)

    def plan_deps(self, iid, row_open, log=False):
        """Depends-on ids of plan iid that have a ROADMAP row; a missing doc or
        an unknown id is no dependency (fail open: a typo never wedges the
        queue)."""
        text = plan_doc(self.d.main_tree, iid)
        if text is None:
            if log:
                self.step(f"{iid}: no plan doc, no dependency gate")
            return []
        out = []
        for dep in plan_depends(text):
            if dep in row_open:
                out.append(dep)
            elif log:
                self.step(f"{iid}: depends on {dep}: no ROADMAP row, ignored")
        return out

    def extra_checks(self, rec, wt):
        if rec.get("kind") == "resolve":
            return self.marker_checks(wt)
        if rec.get("kind") != "deep-dive":
            return []
        main_titles = plan_titles(self.d.main_tree)
        new = {k: v for k, v in plan_titles(wt).items() if k not in main_titles}
        out = []
        cap = self.cfg["max_new_plans_per_day"]
        if len(new) > cap:
            out.append(f"{len(new)} new plans, cap is {cap} per day: keep the best {cap}")
        for k, title in sorted(new.items()):
            dup = is_duplicate(title, main_titles.values())
            if dup:
                out.append(f"plan {k} '{title}' duplicates existing '{dup}': drop it")
        if not list((wt / "docs" / "research").glob(f"*-deep-dive-{rec['date']}.md")):
            out.append(f"missing docs/research/NNNN-deep-dive-{rec['date']}.md")
        return out

    def marker_checks(self, wt):
        """Plan 058: a resolve run must leave no conflict marker in any file it
        changed (the commit's `git add -A` would stage a marker as resolved)."""
        names = set()
        for args in (["diff", "HEAD", "--name-only", "-z"],
                     ["ls-files", "--others", "--exclude-standard", "-z"]):
            r = self.d.git(args, wt)
            if r.returncode == 0:
                names.update(n for n in r.stdout.split("\0") if n)
        bad = []
        for name in sorted(names):
            with contextlib.suppress(OSError):
                if MARKER_RX.search((Path(wt) / name).read_text(encoding="utf-8",
                                                                errors="replace")):
                    bad.append(name)
        return [f"conflict marker left in {', '.join(bad)}: resolve it"] if bad else []

    def commit(self, rec, wt, gates_ok):
        rounds = rec.get("rounds", 0)
        passed = rec.get("verdict") == "PASS"
        # the ROADMAP row is flipped in main at merge, never in the lane commit
        # (fix-0130: lane flips of adjacent rows collided on every parallel merge)
        if not gates_ok:
            head = f"WIP (gates failed, not merged): {rec['label']}"
        elif not passed:
            head = f"WIP (no verifier PASS after {MAX_ROUNDS}/{MAX_ROUNDS}, adjudicate): {rec['label']}"
        else:
            head = rec["label"]
        msg = (f"{head}\n\nrefute-rounds: {rounds}/{MAX_ROUNDS}\n"
               f"verifier: {rec.get('verdict', 'none')}\nloop item: {rec['id']}\n")
        rec["gates_ok"] = gates_ok  # a refused commit is salvaged with the same verdict
        r = self.d.git(["add", "-A"], wt)
        if r.returncode == 0:
            r = self.d.git(["commit", "-q", "-F", "-"], wt, input=msg)
        if r.returncode != 0:
            return self.commit_refused(rec, wt, r, msg)
        rec["commit"] = self.d.git(["rev-parse", "HEAD"], wt).stdout.strip()
        if not gates_ok or not passed:
            rec["state"] = "failed" if not gates_ok else "adjudicate"
            self.items.put(rec)
            self.step(f"{rec['id']}: {rec['state']} after {rounds} rounds, WIP kept unmerged")
            return
        rec["state"] = "committed"
        self.items.put(rec)
        self.merge(rec)

    def commit_refused(self, rec, wt, r, msg):
        """fix-loop-stall: a refused lane commit (a pre-commit hook in the lane
        worktree, which runs the lane's own possibly stale tools) no longer
        parks staged work in the lane: the tree is kept as a commit under
        refs/ew/keep/<id> and the worktree reset, so the dirty lowest lane
        index stops wedging every dispatch. Verified work then goes through
        merge(), whose main-side commit runs main's hooks over the same lines;
        anything else is failed, kept unmerged for a session."""
        why = ((r.stderr or "") + (r.stdout or "")).strip()
        rec["error"] = ascii_text(f"lane commit refused: {why[-260:]}", 300)
        if not self.salvage(rec, wt, msg):
            rec["state"] = "failed-dirty"
            self.items.put(rec)
            self.step(f"{rec['id']}: commit refused, salvage failed, worktree left dirty")
            return
        ok = rec.get("gates_ok") and rec.get("verdict") == "PASS"
        rec["state"] = "committed" if ok else "failed"
        self.items.put(rec)
        self.step(f"{rec['id']}: lane commit refused, work kept under {rec['keep_ref']}, "
                  f"lane worktree reset")
        if ok:
            self.merge(rec)

    def salvage(self, rec, wt, msg):
        g = self.d.git
        if g(["add", "-A"], wt).returncode != 0:
            return False
        tree = g(["write-tree"], wt)
        if tree.returncode != 0:
            return False
        parents = ["-p", "HEAD"]
        mh = g(["rev-parse", "-q", "--verify", "MERGE_HEAD"], wt)
        if mh.returncode == 0 and mh.stdout.strip():
            parents += ["-p", mh.stdout.strip()]
        c = g(["commit-tree", tree.stdout.strip(), *parents, "-F", "-"], wt,
              input="salvaged (lane commit refused by hook): " + msg)
        if c.returncode != 0:
            return False
        rec["commit"] = c.stdout.strip()
        self.keep(rec)
        if not rec.get("keep_ref"):
            return False
        if mh.returncode == 0:
            g(["merge", "--abort"], wt)  # a resolve run's merge in progress
        return g(["reset", "-q", "--hard", "HEAD"], wt).returncode == 0

    def retry_refused(self):
        """fix-loop-stall backfill, idempotent: a failed-dirty record whose
        worktree still holds its staged work (and no other item's) re-runs
        commit(), which salvages it when the lane hook refuses again."""
        recs = self.items.all()
        for rec in recs.values():
            if rec.get("state") != "failed-dirty" or not rec.get("worktree"):
                continue
            wt = Path(rec["worktree"])
            others = self.held_worktrees({k: v for k, v in recs.items() if k != rec["id"]})
            if not wt.is_dir() or _norm_path(wt) in others or not self.dirty(wt):
                continue
            if self.dry:
                self.step(f"{rec['id']}: would retry the refused commit")
                continue
            self.commit(rec, wt, rec.get("gates_ok", rec.get("verdict") == "PASS"))

    def merge(self, rec):
        main = self.d.main_tree
        br = self.d.git(["rev-parse", "--abbrev-ref", "HEAD"], main).stdout.strip()
        if br != "main" or self.dirty(main):
            self.step(f"{rec['id']}: main not clean on main, merge deferred")
            return
        lane = Path(rec["worktree"]).name
        g = self.d.git
        if g(["merge-base", "--is-ancestor", rec["commit"], "HEAD"], main).returncode == 0:
            # already in main (a crash after the merge commit): re-run is a no-op
            rec["state"] = "merged"
            self.drop_keep(rec)
            self.step(f"{rec['id']}: already merged")
            self.items.put(rec)
            return
        r = g(["merge", "--no-ff", "--no-commit", "-q", rec["commit"]], main)
        why, flipped_row = None, False
        if r.returncode != 0 and not self.resolve_roadmap_conflict(main):
            why = "merge conflict"
        if why is None and base_kind(rec) == "plan":
            rounds = rec.get("rounds", 0)
            date = _dt.datetime.fromtimestamp(self.d.clock()).strftime("%Y-%m-%d")
            rm = main / ROADMAP_REL
            try:
                text = rm.read_text(encoding="utf-8")
                flipped = flip_roadmap(text, rec["id"],
                                       f"done {date} (loop; refute {rounds}/{MAX_ROUNDS} "
                                       f"{rec.get('verdict', 'PASS')})")
                if flipped != text:
                    atomic_write(rm, flipped)
                    if g(["add", ROADMAP_REL.as_posix()], main).returncode != 0:
                        why = "roadmap stage refused"
                    flipped_row = True
            except OSError:
                pass
        if why is None:
            msg = (f"merge {lane}: {rec['label']}\n\nrefute-rounds: "
                   f"{rec.get('rounds', 0)}/{MAX_ROUNDS}\n")
            if g(["commit", "-q", "-F", "-"], main, input=msg).returncode != 0:
                why = "merge commit refused"
        if why:
            g(["merge", "--abort"], main)
            rec["state"] = "merge-conflict"
            self.keep(rec)
            self.step(f"{rec['id']}: {why}, aborted")
        else:
            rec["state"] = "merged"
            if flipped_row:  # plan 019 4c: when this row became [x]
                rec["merged_at"] = iso(self.d.clock())
            self.drop_keep(rec)
            self.step(f"{rec['id']}: merged")
        self.items.put(rec)

    def keep(self, rec):
        """Plan 058 step 1: point refs/ew/keep/<id> at the unmerged commit in
        the main checkout; only a ref that was written is recorded."""
        ref = KEEP_REF + rec["id"]
        if rec.get("commit") and self.d.git(["update-ref", ref, rec["commit"]],
                                            self.d.main_tree).returncode == 0:
            rec["keep_ref"] = ref

    def drop_keep(self, rec):
        ref = rec.pop("keep_ref", None)
        if ref:
            self.d.git(["update-ref", "-d", ref], self.d.main_tree)

    def settle_conflicts(self):
        """Plan 058, idempotent, before dispatch: a merge-conflict whose commit
        is already in main (merged by hand) is merged and its ref dropped; one
        without a ref (recorded before plan 058) gets one if its commit still
        exists. Any other record drops its ref once that commit is in main
        (failed / adjudicate / gave-up keep theirs for a session)."""
        if self.dry:
            return
        g, main = self.d.git, self.d.main_tree
        for rec in self.items.all().values():
            state = rec.get("state")
            if state in IN_FLIGHT or not rec.get("commit"):
                continue
            if state != "merge-conflict" and not rec.get("keep_ref"):
                continue
            in_main = g(["merge-base", "--is-ancestor", rec["commit"], "HEAD"],
                        main).returncode == 0
            if in_main and state in RESOLVABLE:
                rec["state"] = "merged"
                self.drop_keep(rec)
                self.step(f"{rec['id']}: conflict merged by hand, ref dropped")
            elif state == "merge-conflict" and not rec.get("keep_ref"):
                self.keep(rec)
                if not rec.get("keep_ref"):
                    continue
                self.step(f"{rec['id']}: kept under {rec['keep_ref']}")
            elif state != "merge-conflict" and in_main:
                self.drop_keep(rec)
            else:
                continue
            self.items.put(rec)

    def resolve_roadmap_conflict(self, main):
        """True when the only unmerged path is ROADMAP.md and the row-level
        resolver settles it (staged); False leaves the merge for --abort."""
        g, rel = self.d.git, ROADMAP_REL.as_posix()
        u = g(["diff", "--name-only", "--diff-filter=U"], main)
        if u.returncode != 0 or u.stdout.split() != [rel]:
            return False

        def stage(n):
            s = g(["show", f":{n}:{rel}"], main)
            return s.stdout if s.returncode == 0 else ""

        out = resolve_roadmap(stage(1), stage(2), stage(3))
        if out is None:
            return False
        atomic_write(main / ROADMAP_REL, out)
        if g(["add", rel], main).returncode != 0:
            return False
        self.step("ROADMAP-only conflict auto-resolved by row")
        return True

    # -- c. work list
    def work_list(self):
        main = self.d.main_tree
        try:
            rows = roadmap_rows((main / "docs" / "plans" / "ROADMAP.md").read_text(encoding="utf-8"))
        except OSError:
            rows = []
        try:
            hand = handoff_items((main / f"{CODE}-NEXT-SESSION.txt").read_text(encoding="utf-8"))
        except OSError:
            hand = []
        # orders first, then ROADMAP rows whose status says "priority", then the rest
        open_rows = sorted((r for r in rows if r["open"]),
                           key=lambda r: "priority" not in r["status"].lower())
        # plan 058: resolve runs first, so a conflicting change is folded into
        # main before more lanes build on stale main; each replaces its own row
        work = [resolve_item(rec) for rec in self.items.all().values()
                if rec.get("keep_ref") and (rec.get("state") == "merge-conflict" or (
                    rec.get("kind") == "resolve"
                    and rec.get("state") in ("refused", "lost", "paused")))]
        resolving = {w["id"] for w in work}
        work += [{"id": o["id"], "kind": "order", "title": o["title"], "note": o["note"],
                  "label": f"order {o['id']}: {o['title']}", "prompt": order_prompt(o)}
                 for o in self.orders() if o["id"] not in resolving]
        # plan 019: a row whose plan doc depends on an open row waits (skipped)
        row_open = {r["id"]: r["open"] for r in rows}
        self.open_ids = {iid for iid, is_open in row_open.items() if is_open}
        skipped, waiting = [], {}
        for r in open_rows:
            if r["id"] in resolving:
                continue
            wait = [dep for dep in self.plan_deps(r["id"], row_open, log=True)
                    if row_open[dep]]
            if wait:
                waiting[r["id"]] = wait
                skipped.append({"id": r["id"], "text": r["title"], "skip": "waits",
                                "reason": f"waits on {', '.join(wait)}"})
            else:
                work.append({"id": r["id"], "kind": "plan", "title": r["title"],
                             "label": f"plan {r['id']}: {r['title']}"})
        for cycle in dependency_cycles(waiting):
            self.step(f"dependency cycle: {' -> '.join(cycle)}")
        for h in hand:
            if h["skip"]:
                skipped.append(h)
            elif h["id"] not in resolving:
                work.append({"id": h["id"], "kind": "handoff", "text": h["text"],
                             "title": h["text"], "label": f"hand-off {h['id']}"})
        return rows, work, skipped

    @staticmethod
    def held_worktrees(recs):
        return {_norm_path(r["worktree"]) for r in recs.values()
                if r.get("state") in HOLD_STATES and r.get("worktree")}

    def worktree_unusable(self, wt, held):
        """A lane worktree the kit would refuse (dirty) or that holds another
        item's unmerged work. A missing one is usable: the kit creates it."""
        return _norm_path(wt) in held or (Path(wt).is_dir() and self.dirty(wt))

    def free_lanes(self):
        """Lane names safe to dispatch now. Read fresh at dispatch time, lane
        locks first, then records: a worker that finished mid-tick is "ran"
        and still holds its worktree (fix 2026-10-05). The kit claims the
        LOWEST non-RUNNING index, not a named one, so slots stop at the first
        such index whose worktree is dirty or held (the claim would refuse)."""
        by_index = {r.get("index"): r for r in self.d.lane_state()}
        rows = [by_index.get(i) or {"index": i, "state": "FREE", "lane": None}
                for i in range(ew_lane.LANE_CAP)]  # no lock row: a FREE index
        recs = self.items.all()
        running = {r["lane"] for r in rows if r["state"] == "RUNNING"}
        busy = running | {r.get("lane") for r in recs.values()
                          if r.get("state") in ("dispatched",) + HOLD_STATES}
        names = [n for n in ew_lane.LANES if n not in busy]
        held = self.held_worktrees(recs)
        # dispatched but not yet claimed: those take the lowest free indexes
        unclaimed = sum(1 for r in recs.values()
                        if r.get("state") == "dispatched" and r.get("lane") not in running)
        slots = 0
        for row in rows:
            if row["state"] == "RUNNING":
                continue
            if unclaimed:
                unclaimed -= 1
                continue
            if self.worktree_unusable(self.d.lane_worktree(row["index"]), held):
                break
            slots += 1
        return names[:slots]

    def recover_lane_dirty(self):
        """A lane-dirty record whose refused worktree (named in its error) is
        now clean and holds no ran / committed item's work goes back to
        "refused": dispatchable, attempts kept, so MAX_ATTEMPTS still bounds
        it (out of attempts: gave-up). A still-dirty tree (a crashed run) keeps
        it waiting; no worktree in the error keeps it as is. Idempotent."""
        recs = self.items.all()
        held = self.held_worktrees(recs)
        for rec in recs.values():
            if rec.get("state") != "lane-dirty":
                continue
            m = DIRTY_WT_RX.search(rec.get("error") or "")
            if not m or self.worktree_unusable(Path(m.group(1)), held):
                continue
            if self.dry:
                self.step(f"{rec['id']}: would clear lane-dirty")
                continue
            if rec.get("attempts", 0) >= MAX_ATTEMPTS:
                rec["state"] = "gave-up"
                self.step(f"{rec['id']}: gave up after {rec.get('attempts', 0)} attempts: "
                          f"{rec.get('error')}")
            else:
                rec.update(state="refused",
                           error=ascii_text(f"lane-dirty cleared: {rec.get('error')}", 300))
                self.step(f"{rec['id']}: lane-dirty cleared, {Path(m.group(1)).name} clean")
            self.items.put(rec)

    def dispatch(self, work):
        free = self.free_lanes()
        for item in work:
            if not free:
                break
            rec = self.items.get(item["id"])
            if not dispatchable(rec, self.open_ids):
                continue
            if self.dry:
                self.step(f"{item['id']}: would dispatch to lane {free.pop(0)}")
                continue
            why = self.blocked()
            if why:
                self.step(f"dispatch paused: {why}")
                return
            lane = free.pop(0)
            prompt = {"plan": plan_prompt, "handoff": handoff_prompt}.get(item["kind"])
            prompt = item.get("prompt") or prompt(item)
            # plan 019: the blocked-run count and the one-time re-arm survive a dispatch
            keep = {k: rec[k] for k in ("blocked_runs", "rearmed") if rec and k in rec}
            # plan 058: a resolve run starts its own attempt count at a conflict
            attempts = 0 if rec and rec.get("state") == "merge-conflict" else \
                (rec or {}).get("attempts", 0)
            new = dict(item, **keep, state="dispatched", lane=lane, prompt=prompt,
                       attempts=attempts + 1, rounds=0, dispatched=iso(self.d.clock()))
            if item["kind"] == "resolve":
                new["resolve_runs"] = item.get("resolve_runs", 0) + 1
            self.items.put(new)
            try:
                new["pid"] = self.d.launch(item["id"])
            except Exception as exc:  # noqa: BLE001 - OSError, ValueError: retryable
                new.update(state="lost", error=ascii_text(f"launch: {type(exc).__name__}: "
                                                          f"{exc}", 300))
                self.items.put(new)
                free.insert(0, lane)
                self.step(f"{item['id']}: launch failed ({type(exc).__name__})")
                continue
            self.items.put(new)
            self.step(f"{item['id']}: dispatched to lane {lane}")

    # -- e. idle mode
    def deep_dive_item(self):
        date = _dt.datetime.fromtimestamp(self.d.clock()).strftime("%Y%m%d")
        iid = f"DD-{date}"
        if self.items.get(iid) and not dispatchable(self.items.get(iid)):
            return None
        if list((self.d.main_tree / "docs" / "research").glob(f"*-deep-dive-{date}.md")):
            return None
        prompt = deep_dive_prompt(self.d.main_tree, date, self.cfg["max_new_plans_per_day"],
                                  plan_titles(self.d.main_tree))
        return {"id": iid, "kind": "deep-dive", "date": date, "title": "idle deep-dive",
                "label": f"deep-dive {date}", "prompt": prompt}

    # -- push
    def push(self):
        if self.no_push or self.dry:
            return
        main, g = self.d.main_tree, self.d.git
        if g(["rev-parse", "--abbrev-ref", "HEAD"], main).stdout.strip() != "main" or \
                self.dirty(main):
            self.step("push skipped: main not clean")
            return
        ahead = g(["rev-list", "--count", "origin/main..main"], main).stdout.strip()
        if not ahead.isdigit() or int(ahead) == 0:
            return
        ok, detail = self.d.gates(main)
        if not ok:
            self.step("push skipped: gates red on main")
            return
        local = g(["rev-parse", "main"], main).stdout.strip()
        remote = g(["rev-parse", "origin/main"], main).stdout.strip()
        if not self.d.leak_pre_push(main, f"refs/heads/main {local} refs/heads/main {remote}"):
            self.step("push skipped: leak sweep HALT")
            return
        r = g(["push", "origin", "main"], main)
        self.pushed = r.returncode == 0
        self.step(f"push {'ok' if self.pushed else 'failed'}: {ahead} commit(s)")

    # -- g. checklist (FLEET item 13 d: remaining tasks only, kit rows)
    def checklist(self, rows, work, skipped):
        recs = self.items.all()
        now = self.d.clock()
        out, listed = [], set()

        def est(rec):
            """Lane-run median (tools/ew_lane records lane-<lane>-code) less the
            time already spent since dispatch; open items assume lane build."""
            e = self.d.estimate(f"lane-{rec.get('lane') or 'build'}-code")
            if rec.get("state") in ("dispatched", "ran", "committed"):
                with contextlib.suppress(TypeError, ValueError):
                    e -= now - _dt.datetime.fromisoformat(rec.get("dispatched")).timestamp()
            return max(int(e), 0)

        def add(iid, title, state, eta_s):
            if len(out) < self.d.checklist.ROWS_MAX:
                if state:
                    state = ascii_text(state, self.d.checklist.STATE_MAX)
                out.append(self.d.checklist.item(iid, ascii_text(" ".join(str(title).split()), 70)
                                                 or iid, state, eta_s))

        def blocked_on(rec):
            return "blocked on " + ", ".join(rec.get("needs") or [])

        def shown(rec, state):
            """Plan 058 step 5: a resolve lane in flight reads `resolving, run n/2`."""
            if rec.get("kind") == "resolve" and state in IN_FLIGHT:
                return f"resolving, run {rec.get('resolve_runs', 1)}/{MAX_ATTEMPTS}"
            return state

        for item in work:
            rec = recs.get(item["id"]) or {}
            state = rec.get("state", "open")
            listed.add(item["id"])
            if state in DONE_STATES:
                continue
            if state == "blocked":
                add(item["id"], item["title"], blocked_on(rec), None)
                continue
            e = 0 if state in ATTENTION_STATES else est(rec)
            add(item["id"], item["title"], None if state == "open" else shown(rec, state), e)
        for h in skipped:  # plan 019: rows waiting on an open dependency
            if h.get("reason"):
                listed.add(h["id"])
                rec = recs.get(h["id"]) or {}
                add(h["id"], h["text"],
                    blocked_on(rec) if rec.get("state") == "blocked" else h["reason"], None)
        for iid, rec in sorted(recs.items()):
            if iid not in listed and rec.get("state") not in DONE_STATES:
                if rec.get("state") == "blocked":
                    add(iid, rec.get("title", ""), blocked_on(rec), None)
                    continue
                add(iid, rec.get("title", ""), shown(rec, rec.get("state")),
                    0 if rec.get("state") in ATTENTION_STATES else est(rec))
        for h in skipped:
            if not h.get("reason"):
                add(h["id"], h["text"], f"{h['skip']}-only, skipped", None)
        return out

    def write(self, state, task, lines, started):
        now = self.d.clock()
        try:
            self.d.write_status(self.root, CODE, state, task, started, self.d.budget,
                                task_eta_s=None, next_tick=now + self.cfg["tick_s"])
        except OSError:
            pass
        prev = read_json(self.root / self.d.kit.PROGRESS_REL / f"{PROGRESS_TASK}.json", {}) or {}
        fire = int(prev.get("fire", 0) or 0) + 1 if isinstance(prev, dict) else 1
        doc = {"task": PROGRESS_TASK, "pct": 100, "step": "; ".join(self.log)[-200:] or state,
               "eta_s": self.cfg["tick_s"], "status": "done", "updated": iso(now),
               "state": state, "fire": fire, "log": self.log[-40:], "checklist": lines}
        atomic_write(self.root / self.d.kit.PROGRESS_REL / f"{PROGRESS_TASK}.json",
                     json.dumps(doc, indent=1))
        return doc

    def run(self):
        started = self.d.clock()
        self.reap_lost()
        if not self.blocked():
            self.inbox()
        else:
            self.step(f"spawning paused: {self.blocked()}")
        self.finished()
        self.retry_refused()
        self.settle_conflicts()
        self.recover_lane_dirty()
        rows, work, skipped = self.work_list()
        self.rearm(rows)
        self.dispatch(work)
        recs = self.items.all()
        in_flight = [r for r in recs.values() if r.get("state") in ("dispatched", "ran", "committed")]
        open_work = [w for w in work if dispatchable(recs.get(w["id"]), self.open_ids)]
        if not open_work and not in_flight:
            dd = self.deep_dive_item()
            if dd:
                work.append(dd)
                self.step("idle: deep-dive")
                self.dispatch([dd])
            else:
                self.step("idle: deep-dive already done today")
        self.push()
        lines = self.checklist(rows, work, skipped)
        why = self.blocked()
        state = {"halted": "halted", "backoff": "backoff", "runs cap": "limit",
                 "budget unreadable": "refused"}.get(why, "running" if in_flight else "idle")
        task = f"{len(in_flight)} lanes" if in_flight else "Idle"
        return self.write(state, task, lines, started)


def render_checklist(doc):
    """The item-13 block for a loop.json doc: `Session <fire> checklist`, one
    line per remaining task, then the /done line (kit fleet_checklist.render)."""
    cl = ew_lane._load("fleet_checklist", "ops/fleet_kit/fleet_checklist.py")
    rows = [r for r in (doc.get("checklist") or []) if isinstance(r, dict)]
    return cl.render(int(doc.get("fire", 0) or 0), rows)


def tick(dry_run=False, no_push=False, deps=None):
    d = deps or Deps()
    t = Tick(d, dry_run, no_push)
    t0 = d.clock()
    if (d.root / HALT_REL).exists():
        t.step("HALT file present")
        return t.write("halted", "Halted", [], t0)
    try:
        with d.lock(d.root / LOCK_REL):
            doc = t.run()
    except d.watch.LockBusy:
        return {"state": "busy"}
    if not dry_run:
        d.record("loop-tick", d.clock() - t0)
    return doc


# ---------------------------------------------------------------- lane worker

def lane_worker(iid, deps=None, run_lane=None):
    """The detached process for one dispatched item: one ew_lane.run_lane call."""
    d = deps or Deps()
    items = Items(d.root)
    rec = items.get(iid)
    if not rec or rec.get("state") != "dispatched":
        return 0
    halt = d.root / HALT_REL

    def pause(why):
        # the dispatch never ran: give its attempt (and resolve run) back, the
        # tick re-dispatches
        rec.update(state="paused", error=f"paused: {why}",
                   attempts=max(rec.get("attempts", 1) - 1, 0))
        if rec.get("kind") == "resolve":
            rec["resolve_runs"] = max(rec.get("resolve_runs", 1) - 1, 0)
        items.put(rec)
        return 0

    why = spawn_block(d.root, d.budget, d.clock())  # may have changed since dispatch
    if why:
        return pause(why)
    run_lane = run_lane or ew_lane.run_lane
    timeout = load_config(d.root)["lane_timeout_s"]
    extra = {"extra": DEEP_EXTRA} if rec.get("kind") == "deep-dive" else {}

    def spawn(root, code, prompt, **kw):
        kw.update(extra, halt_file=halt)  # the kit refuses if HALT appears meanwhile
        if rec.get("kind") == "resolve":
            # plan 058: lanes have no git merge in their allow list, so the
            # worker starts the merge in the claimed (clean, main) worktree
            # and the lane only resolves; conflicts are the expected rc 1
            r = d.git(["merge", "--no-ff", "--no-commit", "-q", rec["keep_ref"]], kw["cwd"])
            if r.returncode != 0 and d.git(["rev-parse", "-q", "--verify", "MERGE_HEAD"],
                                           kw["cwd"]).returncode != 0:
                return {"rc": 1, "error": ascii_text(f"resolve merge refused: {r.stderr}",
                                                     300), "result": None}
        return d.spawn(root, code, prompt, **kw)

    try:
        line = run_lane(rec["lane"], rec["prompt"], writes_code=True, timeout=timeout,
                        root=d.root, spawn=spawn)
    except Exception as exc:  # noqa: BLE001 - LaneRefused, Refused, OSError: all retryable
        text = f"{type(exc).__name__}: {exc}"
        if is_limit(text):
            backoff_hit(d.root, d.clock(), text)
        if halt.exists():
            return pause("halted")
        # a dirty lane worktree: retrying cannot clear it, so it waits without
        # burning attempts until the tick sees that tree clean and unheld
        state = "lane-dirty" if "dirty" in text.lower() else "refused"
        rec.update(state=state, error=ascii_text(text, 300))
        items.put(rec)
        return 1
    text = f"{line.get('error') or ''} {line.get('result') or ''}"
    if line.get("rc") != 0 and is_limit(text):
        backoff_hit(d.root, d.clock(), text)
    rec.update(state="ran", worktree=line.get("worktree"), rc=line.get("rc"),
               error=line.get("error"), finished=iso(d.clock()))
    items.put(rec)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="EW loop tick")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("tick")
    t.add_argument("--dry-run", action="store_true")
    t.add_argument("--no-push", action="store_true")
    sub.add_parser("checklist")
    ln = sub.add_parser("lane")
    ln.add_argument("item")
    a = ap.parse_args(argv)
    if a.cmd == "tick":
        doc = tick(a.dry_run, a.no_push)
        print(f"loop: {doc.get('state')}")
        return 0
    if a.cmd == "lane":
        return lane_worker(a.item)
    doc = read_json(ROOT / "ops/loop/control/progress/loop.json", {}) or {}
    sys.stdout.buffer.write((render_checklist(doc) + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
