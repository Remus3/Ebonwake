#!/usr/bin/env python3
"""EW loop tick (plan 015, operator order 2026-10-05).

    ew_loop.py tick [--dry-run] [--no-push]   one self-monitoring, idempotent pass
    ew_loop.py checklist                      print the last tick's checklist
    ew_loop.py lane <ID>                      (internal) run one dispatched item
    ew_loop.py review <ID>                    (internal, plan 098) review / fix / merge one item
    ew_loop.py push                           (internal, plan 098) gate main and push
    ew_loop.py session                        orders routed to the session (plan 091)
    ew_loop.py session-done <ID> [--commit S] the session marks a routed order done

One tick, under ops/loop/control/loop.lock (fleet_watch.watch_lock):
  a. HALT file -> status "halted", exit 0. A busy lock -> exit 0, nothing written.
  b. Inbox (config loop.inbox_dir / loop.outbox_dir): new notes by mtime through
     fleet_watch.run_source (baseline first), then kit v8 fleet_inbox (FLEET
     item 14): classify() free; skip / ack = a seen-ledger line, no note;
     ORDER / FIX / RULING escalate to a lane item, answered after merge (plan
     091: one naming ops/fleet_kit/, .claude/, CLAUDE.md or a kit version goes
     to the session instead, answered after `session-done`); else
     ONE triage spawn (kit v11 triage_spawn_kwargs(FLOORS_IN_HOOKS=False):
     sonnet, low, bare; kind triage). Answers
     to one destination go in ONE batch note, HOP lines, OutboundCap (6 a
     day). No governor slot (kit ruling: acknowledgements stay outside).
  d. Finished lane worktrees (before c, so a dirty worktree never blocks a
     claim). Plan 098: the tick only launches one DETACHED `review <ID>`
     worker per item (parallel, each in its own worktree), watches its pid
     and counts a dead one (MAX_ATTEMPTS, then parked); the worker runs:
     gates, review-lane verifier (refute rounds capped at 3, then
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
     Plan 085: + one data item per tracked row the server's runtime verdicts
     (ops/runtime/data_verdicts.json in main) mark contradicted by the
     official patch notes, so a lane edits the tracked file.
  e. Idle (nothing open, nothing in flight): one deep-dive lane per day.
  f. Spawning stops at RUNS_CAP - 3 and during a usage-limit backoff
     (ops/loop/control/backoff.json, 30 min doubling, cap 6 h). Plan 100: the
     run count is max(headless_budget.json, headless_usage.jsonl) in the
     window (tools/usage_ledger.py); each tick first labels kind-less usage
     rows kind build.
  g. inbox_status.json (kit write_status, next_tick) and
     ops/loop/control/progress/loop.json with a "checklist" array (FLEET item
     13 d: remaining tasks as kit rows {id, task, state, eta_s}, at most 20;
     "fire" = this tick's run count, the item-13 session number).
  Push (unless --no-push): when main is clean and ahead of origin/main the
  tick launches ONE detached `push` worker (plan 098), which under the merge
  lock needs gates green and leak_sweep --pre-push clean.
  Plan 096: every gate call goes through Tick.gate - one authoritative run per
  tree state (verdicts cached by git tree id in gate_verdicts.json); a merge
  that only adds the ROADMAP flip carries the lane tree's verdict to main, so
  the push worker does not re-gate it. The verifier runs no pytest.

Every side effect goes through Deps, so tests never spawn, never touch a git
remote and never call schtasks. Paths are resolved at run time.
"""

import argparse
import contextlib
import datetime as _dt
import hashlib  # noqa: F401
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import eta  # noqa: E402
import ew_lane  # noqa: E402
import usage_ledger  # noqa: E402
# Plan 107: constants + small helpers + item records (loop_base), lane prompts
# (loop_prompts), the work list (loop_roadmap), the inbox pass (loop_inbox) and
# the merge into main (loop_merge) live in their own modules. Every moved name
# is re-exported here, the same object: tests and tools/ew_tests.py read
# ew_loop.X. Names this file does not use itself are marked F401 (re-export).
from loop_base import (  # noqa: E402
    ATTENTION_STATES, BACKOFF_BASE_S, BACKOFF_CAP_S, BACKOFF_REL, CODE, DEEP_EXTRA,
    DIRTY_WT_RX, DONE_STATES, GATE_TIMEOUT_S, HALT_REL, HEADROOM, HOLD_STATES, INFRA_RX,
    IN_FLIGHT, Items, LOCK_REL, MARKER_RX, MAX_ATTEMPTS, MAX_ROUNDS, MERGE_LOCK_REL,
    NEED_RX, PROGRESS_TASK, PUSH_LAST_REL, PUSH_WORKER_REL, REVIEW_LOG_KEEP,
    REVIEW_START_S, TICK_TARGET_S, VERDICT_RX, VERIFY_EXTRA, _BREAKAWAY, _DETACHED,
    _NO_WINDOW, _norm_path, ascii_text, atomic_write, epoch_of, is_limit, iso,
    load_config, read_json, route_kind, route_kw,
    CONTROL_REL, DEFAULT_MAX_NOTES, DEFAULT_MAX_PLANS, DELIVERY_REL, DEPENDS_RX,  # noqa: F401
    DEP_ID_RX, GATE_CACHE_MAX, GATE_CACHE_REL, HOP_RX, INBOX_REL, ITEMS_REL, KEEP_REF,  # noqa: F401
    LANE_TIMEOUT_S, LEGACY_LEDGER_REL, LIMIT_RX, NOTE_HEAD, NOTE_MAX, NOTE_STAMP,  # noqa: F401
    ORDERS_REL, OUTBOX_REL, RED_TTL_S, RESOLVABLE, ROADMAP_REL, ROUTES, ROUTE_EFFORTS,  # noqa: F401
    ROW_RX, SKIP_TAGS, STOP, TICK_S, WATCH_REL, _DATA_ID, _ROUTE_MODEL, eta_label,  # noqa: F401
    load_routes, next_number, read_jsonl, with_hop,  # noqa: F401
)
from loop_prompts import (  # noqa: E402
    deep_dive_prompt, fix_prompt, handoff_prompt, plan_prompt, verify_prompt,
    GATES, NO_ROADMAP, resolve_prompt,  # noqa: F401
)
from loop_roadmap import (  # noqa: E402
    data_items, dependency_cycles, dispatchable, handoff_items, is_duplicate,
    plan_depends, plan_doc, plan_titles, resolve_item, roadmap_flip_time, roadmap_rows,
    RUN_FIELDS, VERDICTS_REL, _KEY_RX, _norm_words, base_kind, flip_roadmap,  # noqa: F401
    resolve_roadmap, title_tokens,  # noqa: F401
)
from loop_inbox import (  # noqa: E402
    InboxMixin, order_prompt, session_done, session_orders, session_paths,
    FLOORS_IN_HOOKS, SESSION_TARGETS, inbox_prompt, order_id, queued_orders,  # noqa: F401
    triage_spawn_kwargs,  # noqa: F401
)
from loop_merge import GateCache, MergeMixin  # noqa: E402


# ---------------------------------------------------------------- dependencies

# Kit v12 race guards (FLEET-COMMON 16): every commit / push takes the tree's
# git lock; every whole pytest suite holds a machine-wide suite-gate slot.
LOOP_OWNER = "ew-loop.main"
SUITE_GATE_REL = Path("ops/fleet_kit/fleet_suite_gate.py")


def _owner():
    return os.environ.get("FLEET_CLAIM_OWNER") or LOOP_OWNER


def _gitlock():
    return ew_lane._load("fleet_gitlock", "ops/fleet_kit/fleet_gitlock.py")


def _python():
    p = Path(sys.executable)
    alt = p.with_name("python.exe") if p.name.lower() == "pythonw.exe" else p
    return str(alt if alt.exists() else p)


def _pythonw():
    p = Path(sys.executable).with_name("pythonw.exe")
    return str(p if p.exists() else sys.executable)


def _git_run(args, cwd, input=None):
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                          input=input, env=env, timeout=600, creationflags=_NO_WINDOW)


def _git(args, cwd, input=None, lock=None):
    """git; a commit or push runs under the tree's kit v12 git lock."""
    if not args or args[0] not in ("commit", "push"):
        return _git_run(args, cwd, input)
    lock = lock or _gitlock().git_lock
    try:
        with lock(cwd, _owner(), args[0]):
            return _git_run(args, cwd, input)
    except Exception as exc:  # lock timeout / refusal: report as a failed git call
        return subprocess.CompletedProcess(["git", *args], 3, "",
                                           f"git lock: {type(exc).__name__}: {exc}")


def hooks_path_problem(root, git):
    """Perf audit 2.10: why core.hooksPath does not resolve to an existing
    directory inside the repo (a moved checkout left it pointing elsewhere), or
    None. Reported in the tick log only; it never fails the tick."""
    try:
        r = git(["config", "core.hooksPath"], root)
    except Exception as exc:  # noqa: BLE001 - a check, never a tick failure
        return ascii_text(f"hooksPath check failed: {type(exc).__name__}", 200)
    val = (r.stdout or "").strip() if r.returncode == 0 else ""
    if not val:
        return "hooksPath unset: hooks not installed (python tools/install_hooks.py)"
    root = Path(root).resolve()
    hp = Path(val)
    hp = (hp if hp.is_absolute() else root / hp).resolve()
    if hp != root and root not in hp.parents:
        return "hooksPath points outside the repo: re-run python tools/install_hooks.py"
    if not hp.is_dir():
        return "hooksPath does not exist: re-run python tools/install_hooks.py"
    return None


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


def _whole_suite(argv):
    """True for a pytest run that names no test FILE (the kit's whole suite)."""
    if "pytest" not in argv:
        return False
    rest = argv[argv.index("pytest") + 1:]
    return not any(a.endswith(".py") or "::" in a for a in rest)


# Plan 097: the whole suite runs on 4 xdist workers when pytest-xdist (an
# optional dev-only accelerator, never imported by EW code) is importable;
# loadfile keeps each module - every real-git module included - on one worker.
XDIST_ARGS = ("-n", "4", "--dist", "loadfile")


def _xdist_available():
    import importlib.util
    try:
        return importlib.util.find_spec("xdist") is not None
    except (ImportError, ValueError):
        return False


def _names_workers(argv):
    """True when a pytest argv already decides xdist itself."""
    for i, a in enumerate(argv):
        if a == "-n" or a.startswith("--numprocesses") or re.match(r"-n(\d|auto|logical)", a):
            return True
        if a == "-pno:xdist" or (a == "-p" and argv[i + 1:i + 2] == ["no:xdist"]):
            return True
    return False


def _gate_argv(cmd, xdist=None):
    argv = shlex.split(cmd)
    if argv[0] == "python":
        argv[0] = _python()
    elif argv[0] == "npm" and sys.platform == "win32":
        argv[0] = "npm.cmd"
    if _whole_suite(argv):
        use = _xdist_available() if xdist is None else xdist
        if use and not _names_workers(argv):
            argv = [*argv, *XDIST_ARGS]
        argv = [_python(), str(ROOT / SUITE_GATE_REL), "run", "--owner", _owner(), "--", *argv]
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


def tree_key(cwd):
    """Plan 096: the git tree id of cwd's working tree as `git add -A` would
    stage it (the gate cache key). Clean: HEAD^{tree}. Dirty: write-tree over
    a TEMPORARY copy of the index, so the real index and files are never
    touched. Ignored files are not tree state. None when cwd is not a work
    tree root or git fails (the gates then run uncached)."""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}

    def run(args, extra=None):
        return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                              env=dict(env, **(extra or {})), timeout=600,
                              creationflags=_NO_WINDOW)

    def out(r):
        return r.stdout.strip() if r.returncode == 0 else ""

    try:
        top = out(run(["rev-parse", "--show-toplevel"]))
        if not top or _norm_path(Path(top)) != _norm_path(cwd):
            return None
        st = run(["status", "--porcelain"])
        if st.returncode != 0:
            return None
        if not st.stdout.strip():
            return out(run(["rev-parse", "HEAD^{tree}"])) or None
        idx = Path(out(run(["rev-parse", "--git-path", "index"])) or "index")
        idx = idx if idx.is_absolute() else Path(cwd) / idx
        with tempfile.TemporaryDirectory(prefix="ew-gate-") as td:
            tmp = {"GIT_INDEX_FILE": str(Path(td) / "index")}
            if idx.is_file():
                shutil.copyfile(idx, tmp["GIT_INDEX_FILE"])
            elif run(["read-tree", "HEAD"], tmp).returncode != 0:
                return None
            if run(["add", "-A"], tmp).returncode != 0:
                return None
            return out(run(["write-tree"], tmp)) or None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _launch(root, iid, popen=subprocess.Popen, cmd="lane"):
    """Start a worker (`lane <ID>`, plan 098 `review <ID>` / `push`) detached.
    It breaks away from the Task Scheduler job when the job allows it, so the
    tick's end never ends the worker."""
    argv = [_pythonw(), str(Path(root) / "tools" / "ew_loop.py"), cmd] + ([iid] if iid else [])
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
        # plan 100: the governor counts max(budget file, usage log) runs
        self.budget = usage_ledger.UsageBudget(k.RunBudget(self.root / k.BUDGET_REL),
                                               self.root / k.USAGE_REL)
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
        self.launch_review = lambda iid: _launch(self.root, iid, cmd="review")
        self.launch_push = lambda: _launch(self.root, None, cmd="push")
        self.git = _git
        self.gates = _gates
        self.tree_key = tree_key  # plan 096: gate cache key
        self.leak_pre_push = _leak_pre_push
        self.estimate = eta.estimate
        self.record = eta.record
        self.clock = time.time
        # plan 093: the machine fleet roster ({"repos": [{code, root, inbox?}]}),
        # the same FLEET_ROSTER the kit statusline reads; tests pass None.
        self.roster_path = os.environ.get("FLEET_ROSTER") or None
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

class Tick(InboxMixin, MergeMixin):
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
        self.last_tree = None  # plan 096: tree id of the last gate() call

    def route(self, kind, note):
        """Plan 099: spawn() model / effort keywords for one run of `kind`."""
        return route_kw(self.cfg["routes"], kind, note, self.d.pick_effort)

    # -- plan 096: one authoritative gate run per tree state
    def gate(self, cwd):
        """(ok, detail) of the ci gates on cwd's tree, from the verdict cache
        when this exact tree was gated; else one run, recorded when the tree
        did not change under it and the verdict is not an infrastructure red."""
        now = self.d.clock()
        key = self.d.tree_key(cwd)
        self.last_tree = key
        cache = GateCache(self.root)
        e = cache.get(key, now) if key else None
        if e:
            self.step(f"gates cached {'green' if e['ok'] else 'red'} {key[:12]}")
            return e["ok"], e.get("detail") or ""
        ok, detail = self.d.gates(cwd)
        if key and not self.dry and (ok or not INFRA_RX.search(detail or "")) \
                and self.d.tree_key(cwd) == key:
            cache.put(key, ok, detail, Path(cwd).name, self.d.clock())
        return ok, detail

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
        """Plan 098: the tick never reviews inline. Each ran / committed item
        gets one detached `review <ID>` worker (gates in its own worktree,
        verifier, fix rounds, commit, merge), so independent items are gated
        in parallel and the tick stays short. The tick only launches, watches
        and reaps those workers."""
        now = self.d.clock()
        for rec in self.items.all().values():
            if rec.get("state") not in HOLD_STATES:
                continue
            if self.dry:
                self.step(f"{rec['id']}: would launch review")
                continue
            if rec.get("review_launch"):
                if self.review_alive(rec, now):
                    continue
                if not self.review_crashed(rec):
                    continue
            if rec["state"] == "ran":  # a committed item only merges: no spawn
                why = self.blocked()
                if why:
                    self.step(f"{rec['id']}: review waits: {why}")
                    continue
            self.launch_review(rec, now)

    def review_alive(self, rec, now):
        pid = rec.get("worker_pid")
        if pid:
            return bool(self.d.pid_alive(pid))
        launched = epoch_of(rec.get("review_launch"))
        return launched is not None and now - launched < REVIEW_START_S

    def review_crashed(self, rec):
        """A review worker that died (or never started): counted; True when it
        may be relaunched now. At MAX_ATTEMPTS the item is parked for a
        session: ran -> failed-dirty (retry_refused keeps the work as an
        unmerged WIP commit), committed -> merge-refused under its keep ref."""
        rec.pop("worker_pid", None)
        rec.pop("review_launch", None)
        n = rec.get("review_crashes", 0) + 1
        rec["review_crashes"] = n
        if n < MAX_ATTEMPTS:
            self.items.put(rec)
            self.step(f"{rec['id']}: review worker gone ({n}/{MAX_ATTEMPTS}), relaunch")
            return True
        rec["error"] = f"review worker died {n} times"
        if rec["state"] == "committed":
            self.keep(rec)
            rec["state"] = "merge-refused"
        else:
            rec["state"] = "failed-dirty"
        self.items.put(rec)
        self.step(f"{rec['id']}: {rec['error']}, parked {rec['state']}")
        return False

    def launch_review(self, rec, now):
        """The marker is written BEFORE the launch and the record is never
        written after it, so the tick never overwrites the worker's writes."""
        rec["review_launch"] = iso(now)
        rec.pop("worker_pid", None)
        self.items.put(rec)
        try:
            self.d.launch_review(rec["id"])
        except Exception as exc:  # noqa: BLE001 - OSError, ValueError: next tick retries
            cur = self.items.get(rec["id"]) or rec
            cur.pop("review_launch", None)
            cur["error"] = ascii_text(f"review launch: {type(exc).__name__}: {exc}", 300)
            self.items.put(cur)
            self.step(f"{rec['id']}: review launch failed ({type(exc).__name__})")
            return
        self.step(f"{rec['id']}: review worker launched")

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
            ok, detail = self.gate(wt)
            findings = [] if ok else ["gates failed: " + detail]
            findings += self.extra_checks(rec, wt)
            if not findings and rounds < MAX_ROUNDS:  # never a round 4 (rule 7)
                if self.blocked():
                    return  # retry next tick
                note = f"lane-review-{rec['id']}"
                line = self.spawn(verify_prompt(rec, rounds + 1, self.last_tree),
                                  note=note, **self.route("verify", note),
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
            note = f"lane-build-{rec['id']}"
            line = self.spawn(fix_prompt(rec, rounds, findings), note=note,
                              **self.route("fix", note), writes_code=True, cwd=wt, extra=ew_lane.CODE_EXTRA, kind="build",
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
        hand += data_items(main)  # plan 085: contradicted tracked data rows
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
        skipped = []
        for o in self.orders():
            if o["id"] in resolving:
                continue
            hits = session_paths(o["note"], o.get("text", ""))
            if hits:  # plan 091: never a lane item; the session carries it out
                if not (self.items.get(o["id"]) or {}).get("session_done"):
                    skipped.append({"id": o["id"], "text": o["title"], "skip": "session",
                                    "reason": "session: needs " + ", ".join(hits)})
                continue
            work.append({"id": o["id"], "kind": "order", "title": o["title"],
                         "note": o["note"], "label": f"order {o['id']}: {o['title']}",
                         "prompt": order_prompt(o)})
        # plan 019: a row whose plan doc depends on an open row waits (skipped)
        row_open = {r["id"]: r["open"] for r in rows}
        self.open_ids = {iid for iid, is_open in row_open.items() if is_open}
        waiting = {}
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
    def push_needed(self):
        """Commits main is ahead of origin/main (git only, no gates), 0 when
        there is nothing to push or main is not clean on main."""
        if self.no_push or self.dry:
            return 0
        main, g = self.d.main_tree, self.d.git
        if g(["rev-parse", "--abbrev-ref", "HEAD"], main).stdout.strip() != "main" or \
                self.dirty(main):
            self.step("push skipped: main not clean")
            return 0
        ahead = g(["rev-list", "--count", "origin/main..main"], main).stdout.strip()
        return int(ahead) if ahead.isdigit() else 0

    def launch_push(self):
        """Plan 098: the tick never gates main itself; when main is ahead it
        launches ONE detached push worker (none while one is in flight)."""
        if not self.push_needed():
            return
        now, p = self.d.clock(), self.root / PUSH_WORKER_REL
        doc = read_json(p, {})
        if isinstance(doc, dict) and doc:
            pid, launched = doc.get("pid"), epoch_of(doc.get("launched"))
            if (pid and self.d.pid_alive(pid)) or (
                    not pid and launched is not None and now - launched < REVIEW_START_S):
                self.step("push worker in flight")
                return
        atomic_write(p, json.dumps({"launched": iso(now)}))
        try:
            self.d.launch_push()
        except Exception as exc:  # noqa: BLE001 - next tick retries
            with contextlib.suppress(OSError):
                p.unlink()
            self.step(f"push launch failed ({type(exc).__name__})")
            return
        self.step("push worker launched")

    def push(self):
        ahead = self.push_needed()
        if not ahead:
            return
        main, g = self.d.main_tree, self.d.git
        ok, detail = self.gate(main)  # plan 096: an already gated tree is not re-run
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
            """Plan 058 step 5: a resolve lane in flight reads `resolving, run n/2`;
            plan 098: a held item with a review worker in flight reads `reviewing`."""
            if rec.get("kind") == "resolve" and state in IN_FLIGHT:
                return f"resolving, run {rec.get('resolve_runs', 1)}/{MAX_ATTEMPTS}"
            if state in HOLD_STATES and rec.get("review_launch"):
                return "reviewing"
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
        took = round(max(now - started, 0), 1)
        if took > TICK_TARGET_S:  # plan 098: the tick target is under 60 s
            self.step(f"tick took {int(took)}s, target {TICK_TARGET_S}s")
        doc = {"task": PROGRESS_TASK, "pct": 100, "step": "; ".join(self.log)[-200:] or state,
               "eta_s": self.cfg["tick_s"], "status": "done", "updated": iso(now),
               "state": state, "fire": fire, "tick_s": took, "log": self.log[-40:],
               "checklist": lines}
        atomic_write(self.root / self.d.kit.PROGRESS_REL / f"{PROGRESS_TASK}.json",
                     json.dumps(doc, indent=1))
        return doc

    def run(self):
        started = self.d.clock()
        bad_hooks = hooks_path_problem(self.root, self.d.git)
        if bad_hooks:
            self.step(bad_hooks)
        if not self.dry:  # plan 100: label pre-kit-v8 usage rows kind build
            n = usage_ledger.backfill_kind(self.root / self.d.kit.USAGE_REL)
            if n:
                self.step(f"usage rows backfilled kind build: {n}")
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
        self.launch_push()
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
    cfg = load_config(d.root)
    timeout = cfg["lane_timeout_s"]
    extra = {"extra": DEEP_EXTRA} if rec.get("kind") == "deep-dive" else {}
    # plan 099: model / effort by item kind, recorded on the item
    route = route_kw(cfg["routes"], route_kind(rec), f"lane-{rec['lane']}", d.pick_effort)
    rec["route"] = route

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
                        root=d.root, spawn=spawn, **route)
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


# ---------------------------------------------------------------- plan 098 workers

def review_worker(iid, deps=None):
    """The detached process reviewing one ran / committed item: Tick.process
    (gates in its own worktree, verifier, fix rounds, commit, merge under the
    merge lock). It owns the record while it runs; markers are cleared only on
    a normal end, so a crash leaves its dead pid for the tick to count."""
    d = deps or Deps()
    items = Items(d.root)
    rec = items.get(iid)
    if not rec or rec.get("state") not in HOLD_STATES:
        return 0
    me, pid = os.getpid(), rec.get("worker_pid")
    if pid and pid != me and d.pid_alive(pid):
        return 0
    t0 = d.clock()
    rec["worker_pid"] = me
    items.put(rec)
    t = Tick(d)
    rc = 0
    try:
        t.process(rec)
    except Exception as exc:  # noqa: BLE001 - recorded; the dead pid is counted
        t.step(f"{iid}: review worker error {type(exc).__name__}: {exc}")
        rc = 1
    cur = items.get(iid) or rec
    if rc == 0 and cur.get("worker_pid") == me:
        cur.pop("worker_pid", None)
        cur.pop("review_launch", None)
    cur["review_log"] = (t.log or cur.get("review_log") or [])[-REVIEW_LOG_KEEP:]
    items.put(cur)
    d.record("loop-review", d.clock() - t0)
    return rc


def push_worker(deps=None):
    """The detached push: Tick.push (main clean, ahead, gates green on main,
    leak sweep) under the merge lock, so main cannot move while it gates."""
    d = deps or Deps()
    p = d.root / PUSH_WORKER_REL
    doc = read_json(p, {})
    doc = doc if isinstance(doc, dict) else {}
    me, pid = os.getpid(), doc.get("pid")
    if pid and pid != me and d.pid_alive(pid):
        return 0
    t0 = d.clock()
    atomic_write(p, json.dumps({"pid": me, "launched": doc.get("launched") or iso(t0)}))
    t = Tick(d)
    try:
        with d.lock(d.root / MERGE_LOCK_REL):
            t.push()
    except d.watch.LockBusy:
        t.step("push deferred: merge lock busy")
    finally:
        if (read_json(p, {}) or {}).get("pid") == me:
            with contextlib.suppress(OSError):
                p.unlink()
    atomic_write(d.root / PUSH_LAST_REL,
                 json.dumps({"finished": iso(d.clock()), "pushed": t.pushed, "log": t.log[-10:]}))
    d.record("loop-push", d.clock() - t0)
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
    rv = sub.add_parser("review", help="plan 098: (internal) review / fix / merge one item")
    rv.add_argument("item")
    sub.add_parser("push", help="plan 098: (internal) gate main and push")
    sub.add_parser("session", help="plan 091: orders routed to the session")
    sd = sub.add_parser("session-done", help="plan 091: mark a routed order done")
    sd.add_argument("item")
    sd.add_argument("--commit")
    a = ap.parse_args(argv)
    if a.cmd == "session":
        rows = [f"{o['id']} {o.get('note', '')} needs: {', '.join(o['needs'])}"
                for o in session_orders(ROOT)]
        print(ascii_text("\n".join(rows) or "none"))
        return 0
    if a.cmd == "session-done":
        rec = session_done(ROOT, a.item, a.commit)
        print(f"{a.item}: {'session-done' if rec else 'not a queued order'}")
        return 0 if rec else 2
    if a.cmd == "tick":
        doc = tick(a.dry_run, a.no_push)
        print(f"loop: {doc.get('state')}")
        return 0
    if a.cmd == "lane":
        return lane_worker(a.item)
    if a.cmd == "review":
        return review_worker(a.item)
    if a.cmd == "push":
        return push_worker()
    doc = read_json(ROOT / "ops/loop/control/progress/loop.json", {}) or {}
    # kit v10: emit() never raises (a cp1252 console gets "[ ]", pythonw prints nothing)
    ew_lane._load("fleet_checklist", "ops/fleet_kit/fleet_checklist.py").emit(render_checklist(doc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
