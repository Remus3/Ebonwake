"""Plan 096 (perf 2.1): one authoritative gate run per tree state. The gate
verdict is cached by the git tree of the working tree; push skips an already
gated tree; the verifier runs no pytest; producers run touched tests only.
Real git under tmp_path; no remote, no spawn."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
import ew_loop  # noqa: E402
import test_ew_loop as L  # noqa: E402

NOW = 1_790_000_000.0


class Counter:
    def __init__(self, result=(True, "green")):
        self.calls, self.result = [], result

    def __call__(self, cwd):
        self.calls.append(Path(cwd))
        return self.result


class HybridGit:
    """Real git for everything local; the remote side is faked: origin/main
    is always one commit behind, push only records the call."""

    def __init__(self):
        self.pushes = 0

    def __call__(self, args, cwd, input=None):
        if args[0] == "push":
            self.pushes += 1
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[0] == "rev-list" and "origin/main..main" in args:
            return subprocess.CompletedProcess(args, 0, "1\n", "")
        if args[0] == "rev-parse" and "origin/main" in args:
            return subprocess.CompletedProcess(args, 0, "abc\n", "")
        return L.real_git(args, cwd, input)


def repo(tmp_path):
    root = L.make_root(tmp_path, roadmap="", handoff="")
    L.git(root, "init", "-q", "-b", "main")
    (root / ".gitignore").write_text("ops/\nignored.txt\n", newline="\n")
    (root / "a.py").write_text("x = 1\n", newline="\n")
    L.git(root, "add", "-A")
    L.git(root, "commit", "-q", "-m", "init")
    return root


def running():
    return [{"index": i, "state": "RUNNING", "lane": n}
            for i, n in enumerate(("build", "data", "review"))]


# ---------------------------------------------------------------- tree key

def test_tree_key_clean_is_head_tree(tmp_path):
    root = repo(tmp_path)
    assert ew_loop.tree_key(root) == L.git(root, "rev-parse", "HEAD^{tree}")


def test_tree_key_dirty_equals_the_tree_add_all_would_commit_and_touches_nothing(tmp_path):
    root = repo(tmp_path)
    (root / "a.py").write_text("x = 2\n", newline="\n")
    (root / "new.py").write_text("y = 1\n", newline="\n")
    status = L.git(root, "status", "--porcelain")
    staged = L.git(root, "diff", "--cached", "--name-only")
    key = ew_loop.tree_key(root)
    assert key and key != L.git(root, "rev-parse", "HEAD^{tree}")
    assert L.git(root, "status", "--porcelain") == status  # index untouched
    assert L.git(root, "diff", "--cached", "--name-only") == staged
    (root / "ignored.txt").write_text("noise\n")
    assert ew_loop.tree_key(root) == key  # ignored files are not tree state
    L.git(root, "add", "-A")
    L.git(root, "commit", "-q", "-m", "c")
    assert L.git(root, "rev-parse", "HEAD^{tree}") == key


def test_tree_key_outside_git_is_none(tmp_path):
    assert ew_loop.tree_key(tmp_path) is None


# ---------------------------------------------------------------- Tick.gate

def test_gate_runs_once_per_tree_state(tmp_path):
    root = repo(tmp_path)
    gates = Counter()
    t = ew_loop.Tick(L.deps(root, git=L.real_git, gates=gates))
    assert t.gate(root)[0] and t.gate(root)[0]
    assert len(gates.calls) == 1
    assert any("gates cached green" in s for s in t.log)
    (root / "b.py").write_text("z = 3\n", newline="\n")  # untracked, not ignored
    assert t.gate(root)[0]
    assert len(gates.calls) == 2
    # a fresh tick (next fire) reads the same cache file
    t2 = ew_loop.Tick(L.deps(root, git=L.real_git, gates=gates))
    assert t2.gate(root)[0] and len(gates.calls) == 2


def test_red_verdict_is_reused_until_its_ttl(tmp_path):
    root = repo(tmp_path)
    gates = Counter((False, "python -m pytest -q rc=1\n1 failed"))
    clock = {"t": NOW}
    d = L.deps(root, git=L.real_git, gates=gates, clock=lambda: clock["t"])
    t = ew_loop.Tick(d)
    assert not t.gate(root)[0] and not t.gate(root)[0]
    assert len(gates.calls) == 1
    clock["t"] = NOW + ew_loop.RED_TTL_S + 1
    assert not t.gate(root)[0]
    assert len(gates.calls) == 2


def test_infrastructure_red_is_never_cached(tmp_path):
    root = repo(tmp_path)
    for detail in ("python -m pytest -q rc=3\nSUITE-GATE: no suite slot free after 3600s",
                   "python -m pytest -q: TimeoutExpired",
                   "npm test --prefix app: OSError",
                   "no .github/workflows/ci.yml: gates fail closed"):
        gates = Counter((False, detail))
        t = ew_loop.Tick(L.deps(root, git=L.real_git, gates=gates))
        t.gate(root)
        t.gate(root)
        assert len(gates.calls) == 2, detail


def test_no_tree_key_runs_uncached(tmp_path):
    root = L.make_root(tmp_path, roadmap="", handoff="")  # not a git repo
    gates = Counter()
    t = ew_loop.Tick(L.deps(root, gates=gates))
    t.gate(root)
    t.gate(root)
    assert len(gates.calls) == 2


def test_cache_keeps_the_newest_entries_only(tmp_path):
    root = tmp_path
    c = ew_loop.GateCache(root)
    for i in range(ew_loop.GATE_CACHE_MAX + 5):
        c.put(f"{i:040x}", True, "green", "x", NOW + i)
    doc = json.loads((root / ew_loop.GATE_CACHE_REL).read_text())
    assert len(doc["trees"]) == ew_loop.GATE_CACHE_MAX
    assert f"{0:040x}" not in doc["trees"]
    assert c.get(f"{ew_loop.GATE_CACHE_MAX + 4:040x}", NOW)["ok"] is True


# ---------------------------------------------------------------- push

def push_log(root):
    """Plan 098: the push runs in the detached push worker; its log lands in
    push_last.json, not in the tick's own log."""
    return json.loads((root / ew_loop.PUSH_LAST_REL).read_text())["log"]


def test_push_skips_an_already_gated_tree(tmp_path):
    root = repo(tmp_path)
    gates, g = Counter(), HybridGit()
    d = L.deps(root, git=g, gates=gates)
    ew_loop.tick(deps=d)
    assert len(gates.calls) == 1 and g.pushes == 1
    ew_loop.tick(deps=d)  # same tree: no second gate run, push still goes
    assert len(gates.calls) == 1 and g.pushes == 2
    assert any("cached green" in s for s in push_log(root))


def test_push_on_cached_red_tree_is_skipped_without_a_rerun(tmp_path):
    root = repo(tmp_path)
    gates, g = Counter((False, "python -m pytest -q rc=1\n1 failed")), HybridGit()
    d = L.deps(root, git=g, gates=gates)
    ew_loop.tick(deps=d)
    ew_loop.tick(deps=d)
    assert len(gates.calls) == 1 and g.pushes == 0


# ---------------------------------------------------------------- merge carry

def test_merged_plan_is_pushed_on_the_lane_verdict(tmp_path):
    root, wt = L.git_world(tmp_path)
    gates, g = Counter(), HybridGit()
    sp = L.FakeSpawn([{"rc": 0, "error": None, "result": "VERDICT: PASS"}])
    d = L.deps(root, spawn=sp, git=g, gates=gates, lane_state=running)
    ew_loop.tick(deps=d)
    assert ew_loop.Items(root).get("012")["state"] == "merged"
    assert gates.calls == [wt]  # the lane tree only; main's flip-only tree is carried
    assert g.pushes == 1
    head_tree = L.git(root, "rev-parse", "HEAD^{tree}")
    entry = ew_loop.GateCache(root).get(head_tree, NOW)
    assert entry["ok"] and entry.get("via")
    assert any("cached green" in s for s in push_log(root))


def test_moved_main_is_gated_again_before_push(tmp_path):
    root, wt = L.git_world(tmp_path)
    (root / "other.py").write_text("o = 1\n", newline="\n")
    L.git(root, "add", "-A")
    L.git(root, "commit", "-q", "-m", "main moved")
    gates, g = Counter(), HybridGit()
    sp = L.FakeSpawn([{"rc": 0, "error": None, "result": "VERDICT: PASS"}])
    d = L.deps(root, spawn=sp, git=g, gates=gates, lane_state=running)
    ew_loop.tick(deps=d)
    assert ew_loop.Items(root).get("012")["state"] == "merged"
    assert gates.calls == [wt, root]  # code differs from the gated tree: one run
    assert g.pushes == 1


def test_carry_refuses_any_difference_beyond_the_roadmap(tmp_path):
    root = repo(tmp_path)
    t0 = L.git(root, "rev-parse", "HEAD^{tree}")
    c = ew_loop.GateCache(root)
    c.put(t0, True, "green", "lane", NOW)
    (root / "a.py").write_text("x = 3\n", newline="\n")
    L.git(root, "commit", "-qam", "code")
    t1 = L.git(root, "rev-parse", "HEAD^{tree}")
    assert not c.carry(t0, t1, L.real_git, root, NOW)
    assert c.get(t1, NOW) is None
    rm = root / "docs/plans/ROADMAP.md"
    rm.write_text("| 012 | x | [x] done |\n", newline="\n")
    L.git(root, "commit", "-qam", "flip")
    t2 = L.git(root, "rev-parse", "HEAD^{tree}")
    assert not c.carry(t1, t2, L.real_git, root, NOW)  # t1 itself never gated
    c.put(t1, True, "green", "lane", NOW)
    assert c.carry(t1, t2, L.real_git, root, NOW)
    assert c.get(t2, NOW)["via"] == t1


# ---------------------------------------------------------------- prompts

def test_verifier_runs_no_pytest_and_is_told_the_gate_verdict():
    assert "pytest" not in ",".join(ew_loop.VERIFY_EXTRA)
    assert "npm test" in ",".join(ew_loop.VERIFY_EXTRA)
    item = {"label": "plan 012: x"}
    p = ew_loop.verify_prompt(item, 1, "abcdef1234567890")
    assert "Re-run the gates" not in p
    assert "do not re-run pytest" in p.lower() and "abcdef123456" in p


def test_verifier_spawn_uses_the_no_pytest_allow_list(tmp_path):
    root, wt = L.git_world(tmp_path)
    sp = L.FakeSpawn([{"rc": 0, "error": None, "result": "VERDICT: PASS"}])
    d = L.deps(root, spawn=sp, git=L.real_git, lane_state=running)
    ew_loop.tick(deps=d, no_push=True)
    v = sp.calls[0]
    assert v["extra"] == ew_loop.VERIFY_EXTRA and "do not re-run pytest" in v["prompt"].lower()


def test_producers_run_touched_tests_only():
    g = ew_loop.GATES.format(task="p012-build")
    assert "touched" in g and "authoritative" in g
    assert "fleet_suite_gate.py run" not in g  # no whole suite from a producer
    p = ew_loop.plan_prompt({"id": "012", "title": "x"})
    assert "touched" in p
