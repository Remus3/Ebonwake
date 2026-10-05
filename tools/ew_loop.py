#!/usr/bin/env python3
"""EW loop tick (plan 015, operator order 2026-10-05).

    ew_loop.py tick [--dry-run] [--no-push]   one self-monitoring, idempotent pass
    ew_loop.py checklist                      print the last tick's checklist
    ew_loop.py lane <ID>                      (internal) run one dispatched item

One tick, under ops/loop/control/loop.lock (fleet_watch.watch_lock):
  a. HALT file -> status "halted", exit 0. A busy lock -> exit 0, nothing written.
  b. Inbox (config loop.inbox_dir / loop.outbox_dir): new notes by mtime through
     fleet_watch.run_source (baseline first); each note the kit's should_skip
     passes gets ONE headless answer (sonnet, pick_effort, no governor slot -
     kit ruling: acknowledgements stay outside the slots).
  d. Finished lane worktrees (before c, so a dirty worktree never blocks a
     claim): gates, review-lane verifier (refute rounds capped at 3, then
     accept and record), commit, merge --no-ff into main, flip the ROADMAP row.
  c. Work list = open ROADMAP rows + hand-off items not tagged OPERATOR /
     physical / MAIN / NOTE. Each item is dispatched to a free lane as a
     DETACHED `ew_loop.py lane <ID>` process that runs tools/ew_lane.run_lane
     (kit lane + own worktree + one governor slot at the call, fail closed).
  e. Idle (nothing open, nothing in flight): one deep-dive lane per day.
  f. Spawning stops at RUNS_CAP - 3 and during a usage-limit backoff
     (ops/loop/control/backoff.json, 30 min doubling, cap 6 h).
  g. inbox_status.json (kit write_status, next_tick) and
     ops/loop/control/progress/loop.json with a "checklist" array.
  Push (unless --no-push): main clean, gates green, leak_sweep --pre-push
  clean, local main ahead of origin/main; at most one push per tick.

Every side effect goes through Deps, so tests never spawn, never touch a git
remote and never call schtasks. Paths are resolved at run time.
"""

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INBOX_REL = "moon_sync_inbox"
OUTBOX_REL = "moon_sync_outbox"
sys.path.insert(0, str(ROOT / "tools"))
import eta  # noqa: E402
import ew_inbox  # noqa: E402
import ew_lane  # noqa: E402

CODE = "EW"
CONTROL_REL = Path("ops/loop/control")
HALT_REL = CONTROL_REL / "HALT"
LOCK_REL = CONTROL_REL / "loop.lock"
BACKOFF_REL = CONTROL_REL / "backoff.json"
ITEMS_REL = CONTROL_REL / "loop_items"
WATCH_REL = CONTROL_REL / "loop_inbox_watch.json"
ORDERS_REL = CONTROL_REL / "loop_orders.json"
# Responder diet (operator 2026-10-05): an ORDER / FIX / RULING note escalates
# to a lane work item (a lane does the work, the loop answers after merge);
# any other note gets one sonnet low-effort bare triage spawn (kit v8 item 14:
# NOREPLY / ACK are ledger lines, only ANSWER writes a note). At most
# ew_inbox.CAP (6) notes per local day (config may lower it), escalated-order
# answers exempt; several answers to one destination go in ONE note.
ESCALATE_RX = re.compile(r"-(ORDER|FIX|RULING)-", re.I)
DEFAULT_MAX_NOTES = ew_inbox.CAP
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
ATTENTION_STATES = ("merge-conflict", "failed-dirty")
LIMIT_RX = re.compile(r"usage limit|rate[ -]?limit|\b429\b|limit reached|overloaded", re.I)
VERDICT_RX = re.compile(r"^\s*VERDICT:\s*(PASS|FAIL)\b", re.I | re.M)
ROW_RX = re.compile(r"^\|\s*(\d{3})\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*$")
SKIP_TAGS = (("operator", re.compile(r"^(OPERATOR\b|.*\(physical\))|\bphysical\b", re.I)),
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


def read_json(path, default=None):
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
    return doc


def iso(epoch):
    return _dt.datetime.fromtimestamp(epoch).astimezone().isoformat(timespec="seconds")


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
            "max_notes_per_day": int(loop.get("max_notes_per_day", DEFAULT_MAX_NOTES)),
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

GATES = ("Gates before you finish: `python -m pytest -q` and `npm test --prefix app` "
         "green; every authored file ASCII + LF; no absolute machine path, drive "
         "letter, account id or email in a tracked file. Game ToS floor in CLAUDE.md "
         "is absolute (no game memory, injection, packets, client files or input to "
         "the game window; read-only GETs; robots.txt respected). Do NOT commit, do "
         "NOT edit CLAUDE.md, EW-NEXT-SESSION.txt or ops/fleet_kit/. Any deviation "
         "from the plan goes into an 'As-built deviations' section of the plan doc "
         "(decision, alternatives, why, reverses if) - adjudicate it yourself, never "
         "wait. Write the v7 checklist into your progress JSON "
         "ops/loop/control/progress/{task}.json: the FLEET item 12 fields plus "
         "\"checklist\": [\"[ ] ID: step (state, ~ETA)\", ..., \"[ ] /done\"] "
         "(ASCII), updated after each step.")


def plan_prompt(item):
    return (f"You are an Ebonwake (EW) build lane in a detached git worktree. Read "
            f"CLAUDE.md first. Task: implement plan {item['id']} per docs/plans/"
            f"{item['id']}-*.md ({item['title']}) - TDD, stdlib only for Python. "
            + GATES.format(task=f"p{item['id']}-build"))


def handoff_prompt(item):
    return ("You are an Ebonwake (EW) build lane in a detached git worktree. Read "
            "CLAUDE.md first. Work this hand-off item: " + item["text"] + " If it needs "
            "no repo change, change nothing and say why in one line. "
            + GATES.format(task=item["id"]))


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


def dispatchable(rec):
    return rec is None or (rec.get("state") in ("refused", "lost")
                           and rec.get("attempts", 0) < MAX_ATTEMPTS)


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


def _gates(cwd):
    """(ok, detail): pytest then node tests in cwd."""
    npm = "npm.cmd" if sys.platform == "win32" else "npm"
    for argv in ([_python(), "-m", "pytest", "-q"], [npm, "test", "--prefix", "app"]):
        try:
            r = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True,
                               timeout=GATE_TIMEOUT_S, creationflags=_NO_WINDOW,
                               encoding="utf-8", errors="replace")
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"{argv[1]}: {type(exc).__name__}"
        if r.returncode != 0:
            tail = (r.stdout or "")[-1500:] + (r.stderr or "")[-500:]
            return False, f"{' '.join(argv[1:3])} rc={r.returncode}\n{tail}"
    return True, "pytest + node green"


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
        self.budget = k.RunBudget(self.root / k.BUDGET_REL)
        self.spawn = k.spawn
        self.should_skip = k.should_skip
        self.pick_effort = k.pick_effort
        self.write_status = k.write_status
        self.lock = lambda path: fw.watch_lock(path)
        self.lane_state = lambda: fl.repo_lane_state(self.root, ew_lane.LANE_CAP)
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


# ---------------------------------------------------------------- the tick

class Tick:
    def __init__(self, deps, dry_run=False, no_push=False):
        self.d, self.dry, self.no_push = deps, dry_run, no_push
        self.root = deps.root
        self.cfg = load_config(self.root)
        self.items = Items(self.root)
        self.log = []
        self.pushed = None
        self.cap = ew_inbox.OutboundCap(self.root, self.cfg["max_notes_per_day"])
        self.ledger = ew_inbox.Ledger(self.root)

    # -- gates on spawning (item f)
    def blocked(self):
        now = self.d.clock()
        if (self.root / HALT_REL).exists():
            return "halted"
        if backoff_until(self.root) > now:
            return "backoff"
        if not self.d.budget.readable():
            return "budget unreadable"
        if self.d.budget.used() >= self.d.budget.cap - HEADROOM:
            return "runs cap"
        return None

    def spawn(self, prompt, kind="build", **kw):
        """kit spawn + backoff bookkeeping. Returns the usage line or None.
        kind (build / inbox / triage) reaches the usage line on kit v8."""
        kw = ew_inbox.with_kind(self.d.spawn, kw, kind)
        try:
            line = self.d.spawn(self.root, CODE, prompt, stdin=True, **kw)
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
            batch = {}  # destination -> [(note, reply body, hop)]: ONE note each
            pending = [n for n in names if not self.answer(inbox / n, outbox, batch)]
            pending += self.send_batches(batch, outbox)
            if pending:  # left unseen: run_source re-offers them next tick
                return {"delivered": False, "detail": f"{len(pending)} pending"}
            return {"delivered": True}

        res = self.d.watch.run_source(state, "inbox", fetch, deliver,
                                      lambda s, n, d: {"delivered": True})
        self.step(f"inbox: {res['outcome']} {len(res['new'])} {res['detail']}"[:200])

    def answer(self, path, outbox, batch):
        """True when the note needs nothing more (answered now or before, acked,
        skipped, or its reply joined this tick's batch); False = pending.
        FLEET item 14: skip / ack cost nothing, ORDER / FIX / RULING escalate to
        a lane, everything else gets ONE sonnet-low bare triage spawn."""
        name = path.name
        text = path.read_text(encoding="utf-8", errors="replace")
        head = text[:NOTE_HEAD]
        skip = self.d.should_skip(name, CODE, head)
        cls = "skip" if skip else ew_inbox.classify(name, CODE, head)
        if cls == "skip":
            self.step(f"inbox skip {skip or 'terminal'}: {name}")
            return True
        stem = Path(name).stem
        if self.ledger.answered(name) or (outbox.is_dir() and any(
                p.name.endswith(f"-re-{stem}.md") for p in outbox.iterdir())):
            return True
        now = self.d.clock()
        if cls == "ack":  # mechanical ack: a ledger line, never a note
            self.ledger.mark(name, "ack", now, ew_inbox.note_class(name, head) or "hop")
            self.step(f"inbox ack (ledger, no note): {name}")
            return True
        if cls == "work" or ESCALATE_RX.search(name):
            return self.answer_order(path, name, text, outbox)
        to = ew_inbox.sender(name, head) or "MAIN"
        extra = 0 if to in batch else 1
        if not self.cap.allow("ANSWER", now, pending=len(batch) + extra - 1):
            self.step(f"inbox: daily note cap {self.cap.cap} reached")
            return False
        if self.blocked():
            return False
        kw = dict(ew_inbox.TRIAGE_SPAWN, note=name, writes_code=False, timeout=1800)
        line = self.spawn(ew_inbox.triage_prompt(name, text, CODE, NOTE_MAX),
                          kind="triage", **kw)
        if not line or line.get("rc") != 0 or not line.get("result"):
            return False
        verdict, body = ew_inbox.parse_verdict(ascii_text(line["result"]))
        if verdict != "ANSWER":
            self.ledger.mark(name, verdict.lower(), now, "triage")
            self.step(f"inbox triage {verdict}: {name}")
            return True
        batch.setdefault(to, []).append((name, body, ew_inbox.next_hop(text)))
        return True

    def answer_order(self, path, name, text, outbox):
        """An escalated ORDER / FIX / RULING: queued as a lane item, answered
        once the item is done. The closing answer is exempt from the daily cap
        (plan 015 deviation 13)."""
        oid = order_id(name)
        rec = self.items.get(oid)
        if not rec or rec.get("state") not in DONE_STATES + ("adjudicate",):
            self.queue_order(oid, name, text)
            return False  # answered after the lane item is done
        context = (f"EW's loop carried this order out as lane item {oid}: state "
                   f"{rec.get('state')}, verdict {rec.get('verdict', 'none')}, "
                   f"refute-rounds {rec.get('rounds', 0)}/{MAX_ROUNDS}, commit "
                   f"{rec.get('commit', 'none')}. Mark each item DONE in that commit, "
                   "or BLOCKED / NOT-APPLICABLE with the reason.")
        if self.blocked():
            return False
        kw = dict(note=name, writes_code=False, model="sonnet",
                  effort=self.d.pick_effort(name), timeout=1800)
        line = self.spawn(inbox_prompt(name, text, context), kind="inbox", **kw)
        reply = (line or {}).get("result")
        if not line or line.get("rc") != 0 or not reply:
            return False
        stem = Path(name).stem
        body = ew_inbox.with_hop(ascii_text(reply), ew_inbox.next_hop(text))
        to = ew_inbox.sender(name, text[:NOTE_HEAD]) or "MAIN"
        if not self.deliver_note(outbox, f"re-{stem}", body):
            return False
        now = self.d.clock()
        self.cap.record("ANSWER", to, name, now, exempt=True)
        self.ledger.mark(name, "answered", now, "order")
        self.step(f"inbox answered: {name} (1/1 reached)")
        return True

    def send_batches(self, batch, outbox):
        """Write ONE note per destination; returns the notes left pending."""
        pending = []
        for to, pairs in batch.items():
            names = [n for n, _, _ in pairs]
            hop_n = max(h for _, _, h in pairs)
            if len(pairs) == 1:
                tail = f"re-{Path(names[0]).stem}"
                body = ew_inbox.with_hop(pairs[0][1], hop_n)
            else:
                tail = f"to-{to}-batch-" + hashlib.sha1(
                    "\n".join(names).encode("utf-8")).hexdigest()[:6]
                body = ew_inbox.batch_note(CODE, to, [(n, b) for n, b, _ in pairs], hop_n)
            if not self.deliver_note(outbox, tail, body):
                pending += names
                continue
            now = self.d.clock()
            self.cap.record("ANSWER", to, ",".join(names)[:400], now)
            for n in names:
                self.ledger.mark(n, "answered", now, tail)
            self.step(f"inbox answered: {', '.join(names)} -> {to} (1/1 reached)"[:200])
        return pending

    def deliver_note(self, outbox, tail, body):
        stamp = _dt.datetime.fromtimestamp(self.d.clock()).strftime("%Y%m%d-%H%M")
        dest = outbox / f"{stamp}-from-{CODE}-ANSWER-{tail}.md"
        body = ascii_text(body).rstrip("\n") + "\n"
        atomic_write(dest, body)
        return hashlib.sha256(dest.read_bytes()).hexdigest() == \
            hashlib.sha256(body.encode("ascii")).hexdigest()

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
        for rec in self.items.all().values():
            if rec.get("state") == "dispatched" and rec.get("pid") and \
                    not self.d.pid_alive(rec["pid"]) and not self.dry:
                rec.update(state="lost", attempts=rec.get("attempts", 0))
                self.items.put(rec)
                self.step(f"{rec['id']}: lane process gone")

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
                                  writes_code=False, cwd=wt, extra=VERIFY_EXTRA,
                                  governor="queued", governor_timeout=GATE_TIMEOUT_S,
                                  timeout=GATE_TIMEOUT_S)
                if not line or line.get("rc") != 0:
                    return
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
                              writes_code=True, cwd=wt, extra=ew_lane.CODE_EXTRA,
                              governor="queued", governor_timeout=self.cfg["lane_timeout_s"],
                              timeout=self.cfg["lane_timeout_s"])
            if not line:
                return
        rec["rounds"] = rounds
        self.commit(rec, wt, ok)

    def extra_checks(self, rec, wt):
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

    def commit(self, rec, wt, gates_ok):
        date = _dt.datetime.fromtimestamp(self.d.clock()).strftime("%Y-%m-%d")
        rounds = rec.get("rounds", 0)
        passed = rec.get("verdict") == "PASS"
        if gates_ok and passed and rec.get("kind") == "plan":
            rm = wt / "docs" / "plans" / "ROADMAP.md"
            text = rm.read_text(encoding="utf-8")
            atomic_write(rm, flip_roadmap(text, rec["id"],
                                          f"done {date} (loop; refute {rounds}/{MAX_ROUNDS} "
                                          f"{rec.get('verdict', 'PASS')})"))
        if not gates_ok:
            head = f"WIP (gates failed, not merged): {rec['label']}"
        elif not passed:
            head = f"WIP (no verifier PASS after {MAX_ROUNDS}/{MAX_ROUNDS}, adjudicate): {rec['label']}"
        else:
            head = rec["label"]
        msg = (f"{head}\n\nrefute-rounds: {rounds}/{MAX_ROUNDS}\n"
               f"verifier: {rec.get('verdict', 'none')}\nloop item: {rec['id']}\n")
        if self.d.git(["add", "-A"], wt).returncode != 0 or \
                self.d.git(["commit", "-q", "-F", "-"], wt, input=msg).returncode != 0:
            rec["state"] = "failed-dirty"
            self.items.put(rec)
            self.step(f"{rec['id']}: commit refused, worktree left dirty")
            return
        rec["commit"] = self.d.git(["rev-parse", "HEAD"], wt).stdout.strip()
        if not gates_ok or not passed:
            rec["state"] = "failed" if not gates_ok else "adjudicate"
            self.items.put(rec)
            self.step(f"{rec['id']}: {rec['state']} after {rounds} rounds, WIP kept unmerged")
            return
        rec["state"] = "committed"
        self.items.put(rec)
        self.merge(rec)

    def merge(self, rec):
        main = self.d.main_tree
        br = self.d.git(["rev-parse", "--abbrev-ref", "HEAD"], main).stdout.strip()
        if br != "main" or self.dirty(main):
            self.step(f"{rec['id']}: main not clean on main, merge deferred")
            return
        lane = Path(rec["worktree"]).name
        r = self.d.git(["merge", "--no-ff", "-q", "-m",
                        f"merge {lane}: {rec['label']}\n\nrefute-rounds: "
                        f"{rec.get('rounds', 0)}/{MAX_ROUNDS}", rec["commit"]], main)
        if r.returncode != 0:
            self.d.git(["merge", "--abort"], main)
            rec["state"] = "merge-conflict"
            self.step(f"{rec['id']}: merge conflict, aborted")
        else:
            rec["state"] = "merged"
            self.step(f"{rec['id']}: merged")
        self.items.put(rec)

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
        work = [{"id": o["id"], "kind": "order", "title": o["title"], "note": o["note"],
                 "label": f"order {o['id']}: {o['title']}", "prompt": order_prompt(o)}
                for o in self.orders()]
        work += [{"id": r["id"], "kind": "plan", "title": r["title"],
                  "label": f"plan {r['id']}: {r['title']}"} for r in open_rows]
        skipped = []
        for h in hand:
            if h["skip"]:
                skipped.append(h)
            else:
                work.append({"id": h["id"], "kind": "handoff", "text": h["text"],
                             "title": h["text"], "label": f"hand-off {h['id']}"})
        return rows, work, skipped

    def free_lanes(self):
        running = {r["lane"] for r in self.d.lane_state() if r["state"] == "RUNNING"}
        pending = {r.get("lane") for r in self.items.all().values()
                   if r.get("state") == "dispatched"}
        return [n for n in ew_lane.LANES if n not in running | pending]

    def dispatch(self, work):
        free = self.free_lanes()
        for item in work:
            if not free:
                break
            rec = self.items.get(item["id"])
            if not dispatchable(rec):
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
            new = dict(item, state="dispatched", lane=lane, prompt=prompt,
                       attempts=(rec or {}).get("attempts", 0) + 1, rounds=0,
                       dispatched=iso(self.d.clock()))
            self.items.put(new)
            new["pid"] = self.d.launch(item["id"])
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

    # -- g. checklist
    def checklist(self, rows, work, skipped):
        recs = self.items.all()
        est = self.d.estimate
        lines, listed = [], set()
        for item in work:
            rec = recs.get(item["id"]) or {}
            state = rec.get("state", "open")
            if state in ("ran", "committed"):
                e = est("loop-review")
            elif state in DONE_STATES + ATTENTION_STATES:
                e = 0
            else:
                e = est("lane-build-code")
            mark = "x" if state in DONE_STATES else " "
            lines.append(f"[{mark}] {item['id']}: {ascii_text(item['title'], 70)} "
                         f"({state}, {eta_label(e)})")
            listed.add(item["id"])
        for iid, rec in sorted(recs.items()):
            if iid not in listed and rec.get("state") not in DONE_STATES:
                lines.append(f"[ ] {iid}: {ascii_text(rec.get('title', ''), 70)} "
                             f"({rec.get('state')}, {eta_label(est('lane-build-code'))})")
        for h in skipped:
            lines.append(f"[ ] {h['id']}: {ascii_text(h['text'], 70)} ({h['skip']}-only, skipped)")
        lines.append("[ ] /done")
        return lines

    def write(self, state, task, lines, started):
        now = self.d.clock()
        try:
            self.d.write_status(self.root, CODE, state, task, started, self.d.budget,
                                task_eta_s=None, next_tick=now + self.cfg["tick_s"])
        except OSError:
            pass
        doc = {"task": PROGRESS_TASK, "pct": 100, "step": "; ".join(self.log)[-200:] or state,
               "eta_s": self.cfg["tick_s"], "status": "done", "updated": iso(now),
               "state": state, "log": self.log[-40:], "checklist": lines}
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
        rows, work, skipped = self.work_list()
        self.dispatch(work)
        recs = self.items.all()
        in_flight = [r for r in recs.values() if r.get("state") in ("dispatched", "ran", "committed")]
        open_work = [w for w in work if dispatchable(recs.get(w["id"]))]
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


def tick(dry_run=False, no_push=False, deps=None):
    d = deps or Deps()
    t = Tick(d, dry_run, no_push)
    t0 = d.clock()
    if (d.root / HALT_REL).exists():
        t.step("HALT file present")
        return t.write("halted", "Halted", ["[ ] /done"], t0)
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
    run_lane = run_lane or ew_lane.run_lane
    timeout = load_config(d.root)["lane_timeout_s"]
    spawn = d.spawn
    if rec.get("kind") == "deep-dive":
        def spawn(root, code, prompt, **kw):
            kw["extra"] = DEEP_EXTRA
            kind = kw.pop("kind", "build")
            return d.spawn(root, code, prompt, **ew_inbox.with_kind(d.spawn, kw, kind))
    try:
        line = run_lane(rec["lane"], rec["prompt"], writes_code=True, timeout=timeout,
                        root=d.root, spawn=spawn)
    except Exception as exc:  # noqa: BLE001 - LaneRefused, Refused, OSError: all retryable
        text = f"{type(exc).__name__}: {exc}"
        if is_limit(text):
            backoff_hit(d.root, d.clock(), text)
        rec.update(state="refused", error=ascii_text(text, 300))
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
    print("\n".join(doc.get("checklist") or ["[ ] /done"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
