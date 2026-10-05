"""Plan 015: tools/ew_loop.py tick. Every side effect is injected: no test
spawns claude, touches a git remote or calls schtasks. Merge tests use a
throwaway local git repo under tmp_path."""

import contextlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import ew_loop  # noqa: E402

ROADMAP = """# Ebonwake roadmap

| # | Plan | Status |
|---|---|---|
| 001 | Skeleton | [x] done 2026-10-04 |
| 012 | Grind spot recommender | [ ] open |
| 013 | Season pass tracker | [ ] open |
"""

HANDOFF = """EW NEXT SESSION
---------------
Next action: x

Carried forward (not acted on yet):
- OPERATOR (physical): press Ctrl+Alt+E and confirm the overlay toggles.
- OPERATOR: set profile.family in config.
- MAIN to record EW's port block in the registry.
- NOTE: OCR residual accepted.
- Fix the OCR cache key so a same-second rewrite is
  re-read.

Context: CLAUDE.md
"""


class FakeSpawn:
    def __init__(self, results=None):
        self.calls = []
        self.results = list(results or [])

    def __call__(self, root, code, prompt, **kw):
        self.calls.append(dict(kw, prompt=prompt, code=code))
        if self.results:
            r = self.results.pop(0)
            return r(kw) if callable(r) else r
        return {"rc": 0, "error": None, "result": "ok"}


def make_root(tmp_path, roadmap=ROADMAP, handoff=HANDOFF, local=None):
    root = tmp_path / "repo"
    (root / "docs" / "plans").mkdir(parents=True)
    (root / "docs" / "research").mkdir(parents=True)
    (root / "docs" / "plans" / "ROADMAP.md").write_text(roadmap, newline="\n")
    (root / "docs" / "plans" / "012-grind-spots.md").write_text(
        "# Plan 012 - Grind spot recommender by AP/DP\n", newline="\n")
    (root / "EW-NEXT-SESSION.txt").write_text(handoff, newline="\n")
    if local is not None:
        (root / "config").mkdir()
        (root / "config" / "local.json").write_text(json.dumps(local))
    return root


def deps(root, **over):
    seen = {"launch": [], "record": []}
    base = dict(
        spawn=FakeSpawn(),
        lane_state=lambda: [{"index": i, "state": "FREE", "lane": None} for i in range(3)],
        pid_alive=lambda pid: True,
        main_tree=root,
        launch=lambda iid: seen["launch"].append(iid) or 4242,
        gates=lambda cwd: (True, "green"),
        leak_pre_push=lambda cwd, line: True,
        estimate=lambda kind: 2700.0,
        record=lambda kind, s, ok=True: seen["record"].append(kind),
        clock=lambda: 1_790_000_000.0,
    )
    base.update(over)
    d = ew_loop.Deps(root, **base)
    d.seen = seen
    return d


def progress(root):
    return json.loads((root / "ops/loop/control/progress/loop.json").read_text())


# ---------------------------------------------------------------- parsing

def test_roadmap_rows_and_flip():
    rows = ew_loop.roadmap_rows(ROADMAP)
    assert [(r["id"], r["open"]) for r in rows] == [("001", False), ("012", True),
                                                     ("013", True)]
    out = ew_loop.flip_roadmap(ROADMAP, "012", "done 2026-10-05 (loop; refute 1/3 PASS)")
    assert "| 012 | Grind spot recommender | [x] done 2026-10-05 (loop; refute 1/3 PASS) |" in out
    assert "| 013 | Season pass tracker | [ ] open |" in out
    assert ew_loop.flip_roadmap(out, "001", "x") == out  # done rows never touched


def test_handoff_items_tags():
    items = ew_loop.handoff_items(HANDOFF)
    assert [i["skip"] for i in items] == ["operator", "operator", "other-tree", "info", None]
    assert items[-1]["text"].endswith("rewrite is re-read.")
    assert re.fullmatch(r"H[0-9a-f]{6}", items[-1]["id"])
    # the id is stable across whitespace re-wraps
    again = ew_loop.handoff_items(HANDOFF.replace("is\n  re-read", "is re-read"))
    assert again[-1]["id"] == items[-1]["id"]


def test_title_dedupe_by_token_overlap():
    existing = ["Grind spot recommender by AP/DP/level from a sourced community table",
                "Season pass tracker by objective"]
    assert ew_loop.is_duplicate("Grind spot recommender", existing) == existing[0]
    assert ew_loop.is_duplicate("Season Pass objective tracker", existing) == existing[1]
    assert ew_loop.is_duplicate("Lifeskill node empire planner", existing) is None


def test_ascii_text():
    raw = "a" + chr(0x2014) + "b " + chr(0x201C) + "q" + chr(0x201D) + chr(0xE9)
    assert ew_loop.ascii_text(raw) == 'a-b "q"?'


# ---------------------------------------------------------------- tick gates

def test_halt_writes_halted_and_spawns_nothing(tmp_path):
    root = make_root(tmp_path)
    (root / "ops/loop/control").mkdir(parents=True)
    (root / "ops/loop/control/HALT").write_text("")
    d = deps(root)
    doc = ew_loop.tick(deps=d)
    assert doc["state"] == "halted" and d.spawn.calls == [] and d.seen["launch"] == []
    st = json.loads((root / "ops/loop/control/inbox_status.json").read_text())
    assert st["state"] == "halted" and st["next_tick"]


def test_busy_lock_exits_quietly(tmp_path):
    root = make_root(tmp_path)

    @contextlib.contextmanager
    def busy(path):
        raise d.watch.LockBusy("held")
        yield

    d = deps(root, lock=busy)
    assert ew_loop.tick(deps=d) == {"state": "busy"}
    assert not (root / "ops/loop/control/progress/loop.json").exists()


def test_inbox_unconfigured_logged(tmp_path):
    root = make_root(tmp_path)
    d = deps(root)
    ew_loop.tick(deps=d, no_push=True)
    assert "inbox unconfigured" in progress(root)["log"]


# ---------------------------------------------------------------- inbox

def test_inbox_baseline_then_one_answer_per_note(tmp_path):
    inbox, outbox = tmp_path / "in", tmp_path / "out"
    inbox.mkdir()
    outbox.mkdir()
    (inbox / "old-from-MAIN-ORDER-x.md").write_text("# From MAIN - ORDER\nold\n")
    root = make_root(tmp_path, roadmap="", handoff="",
                     local={"loop": {"inbox_dir": str(inbox), "outbox_dir": str(outbox)}})
    sp = FakeSpawn([{"rc": 0, "error": None, "result": "# From EW - ANSWER\nyes " + chr(0x2014) + " ok"}])
    d = deps(root, spawn=sp)
    ew_loop.tick(deps=d, no_push=True)  # baseline: history is not news
    assert [c for c in sp.calls if c.get("note", "").endswith(".md")] == []
    (inbox / "n1-from-MAIN-QUESTION-y.md").write_text("# From MAIN - QUESTION\nq?\n")
    (inbox / "n2-from-EW-ANSWER-z.md").write_text("# From EW - ANSWER\nmine\n")
    (inbox / "n3-from-MAIN-INFORMATION-w.md").write_text("# From MAIN\nTERMINAL\n")
    ew_loop.tick(deps=d, no_push=True)
    inbox_calls = [c for c in sp.calls if c.get("note", "").endswith(".md")]
    assert [c["note"] for c in inbox_calls] == ["n1-from-MAIN-QUESTION-y.md"]
    c = inbox_calls[0]
    assert c["model"] == "sonnet" and c["effort"] == "medium" and c["writes_code"] is False
    assert "governor" not in c and c["stdin"] is True
    replies = list(outbox.glob("*-from-EW-ANSWER-re-n1-from-MAIN-QUESTION-y.md"))
    assert len(replies) == 1
    assert replies[0].read_bytes().decode("ascii").endswith("yes - ok\n")
    ew_loop.tick(deps=d, no_push=True)  # re-run is a no-op
    assert len([c for c in sp.calls if c.get("note", "").endswith(".md")]) == 1


def test_inbox_failed_answer_is_reoffered(tmp_path):
    inbox, outbox = tmp_path / "in", tmp_path / "out"
    inbox.mkdir()
    outbox.mkdir()
    root = make_root(tmp_path, roadmap="", handoff="",
                     local={"loop": {"inbox_dir": str(inbox), "outbox_dir": str(outbox)}})
    sp = FakeSpawn([{"rc": 1, "error": "boom", "result": None},
                    {"rc": 0, "error": None, "result": "fine"}])
    d = deps(root, spawn=sp)
    ew_loop.tick(deps=d, no_push=True)
    (inbox / "a-from-MAIN-ORDER-q.md").write_text("# From MAIN - ORDER\ndo\n")
    ew_loop.tick(deps=d, no_push=True)
    assert not list(outbox.iterdir())
    ew_loop.tick(deps=d, no_push=True)
    assert len(list(outbox.iterdir())) == 1


# ---------------------------------------------------------------- dispatch

def test_dispatch_open_rows_then_handoff_to_free_lanes(tmp_path):
    root = make_root(tmp_path)
    d = deps(root, lane_state=lambda: [
        {"index": 0, "state": "RUNNING", "lane": "build"},
        {"index": 1, "state": "FREE", "lane": None},
        {"index": 2, "state": "FREE", "lane": None}])
    ew_loop.tick(deps=d, no_push=True)
    assert d.seen["launch"] == ["012", "013"]
    items = ew_loop.Items(root)
    r12 = items.get("012")
    assert r12["state"] == "dispatched" and r12["lane"] == "data" and r12["pid"] == 4242
    assert "implement plan 012 per docs/plans/012-*.md" in r12["prompt"]
    assert "v7 checklist" in r12["prompt"] and "As-built deviations" in r12["prompt"]
    assert items.get("013")["lane"] == "review"
    ew_loop.tick(deps=d, no_push=True)  # all lanes taken: nothing new
    assert d.seen["launch"] == ["012", "013"]


def test_dead_lane_process_is_redispatched_once(tmp_path):
    root = make_root(tmp_path, roadmap=ROADMAP.replace("| 013 | Season pass tracker | [ ] open |\n", ""),
                     handoff="")
    alive = {"v": False}
    d = deps(root, pid_alive=lambda pid: alive["v"])
    for _ in range(4):
        ew_loop.tick(deps=d, no_push=True)
    assert d.seen["launch"].count("012") == ew_loop.MAX_ATTEMPTS


def test_runs_cap_headroom_stops_spawning(tmp_path):
    root = make_root(tmp_path)
    d = deps(root)
    now = d.clock()
    p = root / "ops/loop/control/headless_budget.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"starts": [now - 10] * (d.budget.cap - 3)}))
    d.budget.clock = lambda: now
    doc = ew_loop.tick(deps=d, no_push=True)
    assert d.seen["launch"] == [] and doc["state"] == "limit"


def test_backoff_doubles_and_caps(tmp_path):
    root, now = tmp_path, 1_790_000_000.0
    a = ew_loop.backoff_hit(root, now, "429")
    b = ew_loop.backoff_hit(root, now, "429")
    assert (a["delay_s"], b["delay_s"]) == (1800, 3600) and b["until"] == now + 3600
    for _ in range(10):
        c = ew_loop.backoff_hit(root, now, "usage limit")
    assert c["delay_s"] == 6 * 3600
    ew_loop.backoff_clear(root)
    assert ew_loop.backoff_until(root) == 0


def test_usage_limit_result_sets_backoff_and_pauses(tmp_path):
    inbox, outbox = tmp_path / "in", tmp_path / "out"
    inbox.mkdir()
    outbox.mkdir()
    root = make_root(tmp_path, local={"loop": {"inbox_dir": str(inbox),
                                               "outbox_dir": str(outbox)}})
    sp = FakeSpawn([{"rc": 1, "error": None, "result": "Claude usage limit reached"}])
    d = deps(root, spawn=sp)
    ew_loop.tick(deps=d, no_push=True)
    (inbox / "a-from-MAIN-ORDER-q.md").write_text("# From MAIN - ORDER\ndo\n")
    d.seen["launch"].clear()
    ew_loop.Items(root).dir.mkdir(parents=True, exist_ok=True)
    for p in ew_loop.Items(root).dir.glob("*.json"):
        p.unlink()
    doc = ew_loop.tick(deps=d, no_push=True)
    assert ew_loop.backoff_until(root) == d.clock() + 1800
    assert doc["state"] == "backoff" and d.seen["launch"] == []


def test_dry_run_spawns_and_launches_nothing(tmp_path):
    root = make_root(tmp_path)
    d = deps(root)
    doc = ew_loop.tick(dry_run=True, deps=d)
    assert d.spawn.calls == [] and d.seen["launch"] == [] and d.seen["record"] == []
    assert any("would dispatch" in s for s in doc["log"])


# ---------------------------------------------------------------- checklist

def test_checklist_lines(tmp_path):
    root = make_root(tmp_path)
    d = deps(root)
    doc = ew_loop.tick(deps=d, no_push=True)
    cl = doc["checklist"]
    assert cl[-1] == "[ ] /done"
    assert cl[0] == "[ ] 012: Grind spot recommender (dispatched, ~45m)"
    assert any(line.endswith("(operator-only, skipped)") for line in cl)
    assert any("(other-tree-only, skipped)" in line for line in cl)
    for line in cl:
        line.encode("ascii")
        assert re.match(r"^\[[ x]\] \S+", line)
    raw = (root / "ops/loop/control/progress/loop.json").read_bytes()
    raw.decode("ascii")
    st = json.loads((root / "ops/loop/control/inbox_status.json").read_text())
    assert st["code"] == "EW" and st["state"] == "running" and st["next_tick"]


# ---------------------------------------------------------------- finish + merge

def git(cwd, *args):
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
                        "-c", "core.hooksPath=/dev/null", *args], cwd=str(cwd),
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def real_git(args, cwd, input=None):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
                           "-c", "core.hooksPath=/dev/null", *args], cwd=str(cwd),
                          capture_output=True, text=True, input=input)


def git_world(tmp_path, kind="plan"):
    root = make_root(tmp_path, handoff="")
    git(root, "init", "-q", "-b", "main")
    (root / ".gitignore").write_text("ops/\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    wt = tmp_path / "ew-worktrees" / "lane-1"
    git(root, "worktree", "add", "-q", "--detach", str(wt))
    (wt / "feature.txt").write_text("work\n")
    rec = {"id": "012", "kind": kind, "title": "Grind spot recommender",
           "label": "plan 012: Grind spot recommender", "state": "ran",
           "worktree": str(wt), "rc": 0, "lane": "build", "rounds": 0}
    ew_loop.Items(root).put(rec)
    return root, wt


def test_finished_lane_is_verified_committed_merged_and_flipped(tmp_path):
    root, wt = git_world(tmp_path)
    sp = FakeSpawn([{"rc": 0, "error": None, "result": "fine\nVERDICT: PASS"}])
    d = deps(root, spawn=sp, git=real_git,
             lane_state=lambda: [{"index": i, "state": "RUNNING", "lane": n}
                                 for i, n in enumerate(("build", "data", "review"))])
    ew_loop.tick(deps=d, no_push=True)
    rec = ew_loop.Items(root).get("012")
    assert rec["state"] == "merged" and rec["verdict"] == "PASS"
    v = sp.calls[0]
    assert v["cwd"] == wt and v["governor"] == "queued" and v["writes_code"] is False
    assert "refute round 1/3" in v["prompt"]
    log = git(root, "log", "-1", "--format=%P%n%B", "main")
    assert len(log.splitlines()[0].split()) == 2  # --no-ff merge commit
    lane_msg = git(root, "log", "-1", "--format=%B", rec["commit"])
    assert "refute-rounds: 0/3" in lane_msg and "verifier: PASS" in lane_msg
    rm = (root / "docs/plans/ROADMAP.md").read_text()
    assert "| 012 | Grind spot recommender | [x] done" in rm and "refute 0/3 PASS" in rm
    assert (root / "feature.txt").exists()


def test_refute_rounds_cap_at_three_then_accept(tmp_path):
    root, wt = git_world(tmp_path)
    fail = {"rc": 0, "error": None, "result": "VERDICT: FAIL\n1. nit"}
    sp = FakeSpawn([fail, {"rc": 0}, fail, {"rc": 0}, fail, {"rc": 0}, fail])
    d = deps(root, spawn=sp, git=real_git,
             lane_state=lambda: [{"index": i, "state": "RUNNING", "lane": n}
                                 for i, n in enumerate(("build", "data", "review"))])
    ew_loop.tick(deps=d, no_push=True)
    rec = ew_loop.Items(root).get("012")
    assert rec["state"] == "merged" and rec["rounds"] == 3
    assert rec["verdict"] == "accepted after 3/3"
    fixes = [c for c in sp.calls if c["writes_code"]]
    assert len(fixes) == 3 and all("1. nit" in c["prompt"] for c in fixes)
    assert len([c for c in sp.calls if not c["writes_code"]]) == 4  # no round 4 fix
    assert "refute-rounds: 3/3" in git(root, "log", "-1", "--format=%B", rec["commit"])


def test_red_gates_after_three_rounds_keep_wip_unmerged(tmp_path):
    root, wt = git_world(tmp_path)
    sp = FakeSpawn()
    d = deps(root, spawn=sp, git=real_git, gates=lambda cwd: (False, "1 failed"),
             lane_state=lambda: [{"index": i, "state": "RUNNING", "lane": n}
                                 for i, n in enumerate(("build", "data", "review"))])
    head = git(root, "rev-parse", "main")
    ew_loop.tick(deps=d, no_push=True)
    rec = ew_loop.Items(root).get("012")
    assert rec["state"] == "failed" and git(root, "rev-parse", "main") == head
    assert git(wt, "status", "--porcelain") == ""  # lane unblocked
    assert len(sp.calls) == 3 and all(c["writes_code"] for c in sp.calls)


def test_clean_worktree_is_no_change(tmp_path):
    root, wt = git_world(tmp_path)
    (wt / "feature.txt").unlink()
    d = deps(root, git=real_git)
    ew_loop.tick(deps=d, no_push=True)
    assert ew_loop.Items(root).get("012")["state"] == "no-change"


def test_dirty_main_defers_merge(tmp_path):
    root, wt = git_world(tmp_path)
    (root / "stray.txt").write_text("x")
    sp = FakeSpawn([{"rc": 0, "error": None, "result": "VERDICT: PASS"}])
    d = deps(root, spawn=sp, git=real_git)
    ew_loop.tick(deps=d, no_push=True)
    assert ew_loop.Items(root).get("012")["state"] == "committed"
    (root / "stray.txt").unlink()
    ew_loop.tick(deps=d, no_push=True)
    assert ew_loop.Items(root).get("012")["state"] == "merged"


# ---------------------------------------------------------------- idle mode

def test_idle_dispatches_one_deep_dive_per_day(tmp_path):
    rm = ROADMAP.replace("[ ] open", "[x] done")
    root = make_root(tmp_path, roadmap=rm, handoff="",
                     local={"loop": {"max_new_plans_per_day": 1}})
    d = deps(root)
    ew_loop.tick(deps=d, no_push=True)
    assert len(d.seen["launch"]) == 1 and d.seen["launch"][0].startswith("DD-")
    rec = ew_loop.Items(root).get(d.seen["launch"][0])
    assert rec["kind"] == "deep-dive" and "AT MOST 1 new plans" in rec["prompt"]
    assert "robots.txt" in rec["prompt"] and "Grind spot recommender" in rec["prompt"]
    assert "docs/research/0001-deep-dive-" in rec["prompt"]
    ew_loop.tick(deps=d, no_push=True)
    assert len(d.seen["launch"]) == 1


def test_deep_dive_checks_cap_and_dupes(tmp_path):
    root = make_root(tmp_path, local={"loop": {"max_new_plans_per_day": 1}})
    wt = tmp_path / "wt"
    (wt / "docs" / "plans").mkdir(parents=True)
    (wt / "docs" / "research").mkdir(parents=True)
    (wt / "docs/plans/012-grind-spots.md").write_text("# Plan 012 - Grind spot recommender by AP/DP\n")
    (wt / "docs/plans/015-x.md").write_text("# Plan 015 - Grind spot recommender v2\n")
    (wt / "docs/plans/016-y.md").write_text("# Plan 016 - Lifeskill planner\n")
    t = ew_loop.Tick(deps(root))
    out = t.extra_checks({"kind": "deep-dive", "date": "20261005"}, wt)
    assert any("cap is 1" in f for f in out)
    assert any("plan 015" in f and "duplicates" in f for f in out)
    assert any("missing docs/research" in f for f in out)


# ---------------------------------------------------------------- push

class FakeGit:
    def __init__(self, ahead="1", dirty=""):
        self.calls, self.ahead, self.dirty = [], ahead, dirty

    def __call__(self, args, cwd, input=None):
        self.calls.append(list(args))
        out = {"rev-parse": "main" if "--abbrev-ref" in args else "abc",
               "status": self.dirty, "rev-list": self.ahead}.get(args[0], "")
        return subprocess.CompletedProcess(args, 0, out, "")


def test_push_once_when_clean_green_and_leak_clean(tmp_path):
    root = make_root(tmp_path, roadmap="", handoff="")
    g = FakeGit()
    leak = []
    d = deps(root, git=g, leak_pre_push=lambda cwd, line: leak.append(line) or True)
    ew_loop.tick(deps=d)
    assert [c for c in g.calls if c[0] == "push"] == [["push", "origin", "main"]]
    assert leak == ["refs/heads/main abc refs/heads/main abc"]


def test_push_skipped_no_push_red_gates_leak_or_nothing_ahead(tmp_path):
    root = make_root(tmp_path, roadmap="", handoff="")
    for kw, over in (({"no_push": True}, {}),
                     ({}, {"gates": lambda cwd: (False, "red")}),
                     ({}, {"leak_pre_push": lambda cwd, line: False}),
                     ({}, {"git": FakeGit(ahead="0")}),
                     ({}, {"git": FakeGit(dirty=" M x")})):
        g = over.pop("git", FakeGit())
        d = deps(root, git=g, **over)
        ew_loop.tick(deps=d, **kw)
        assert not [c for c in g.calls if c[0] == "push"]


# ---------------------------------------------------------------- lane worker

def test_lane_worker_records_result(tmp_path):
    root = make_root(tmp_path)
    items = ew_loop.Items(root)
    items.put({"id": "012", "kind": "plan", "state": "dispatched", "lane": "data",
               "prompt": "implement plan 012", "title": "t", "label": "l"})
    seen = []

    def run_lane(lane, prompt, **kw):
        seen.append((lane, prompt, kw["writes_code"]))
        return {"rc": 0, "error": None, "worktree": "wt"}

    assert ew_loop.lane_worker("012", deps=deps(root), run_lane=run_lane) == 0
    assert seen == [("data", "implement plan 012", True)]
    rec = items.get("012")
    assert rec["state"] == "ran" and rec["worktree"] == "wt"
    assert ew_loop.lane_worker("012", deps=deps(root), run_lane=run_lane) == 0
    assert len(seen) == 1  # only a dispatched record runs


def test_lane_worker_refusal_and_deep_dive_tools(tmp_path):
    root = make_root(tmp_path)
    items = ew_loop.Items(root)
    items.put({"id": "DD-1", "kind": "deep-dive", "state": "dispatched", "lane": "build",
               "prompt": "p", "title": "t", "label": "l"})
    sp = FakeSpawn()

    def run_lane(lane, prompt, spawn=None, root=None, **kw):
        spawn(root, "EW", prompt, extra=("x",))
        raise RuntimeError("lanes_full")

    assert ew_loop.lane_worker("DD-1", deps=deps(root, spawn=sp), run_lane=run_lane) == 1
    assert "WebFetch" in sp.calls[0]["extra"][3]
    assert items.get("DD-1")["state"] == "refused"


def test_launch_is_detached_hidden_with_breakaway_fallback(tmp_path):
    calls = []

    class P:
        pid = 7

    def popen(argv, creationflags=0, **kw):
        calls.append(creationflags)
        if len(calls) == 1 and ew_loop._BREAKAWAY:
            raise PermissionError("job forbids breakaway")
        assert argv[-2:] == ["lane", "012"] and kw["stdin"] == subprocess.DEVNULL
        return P()

    assert ew_loop._launch(tmp_path, "012", popen=popen) == 7
    assert all(f & ew_loop._NO_WINDOW == ew_loop._NO_WINDOW for f in calls)
    assert all(f & ew_loop._DETACHED == ew_loop._DETACHED for f in calls)


def test_no_machine_path_in_sources():
    for rel in ("tools/ew_loop.py", "tests/test_ew_loop.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert not re.search(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]", src)
        assert "\\" + "Users" + "\\" not in src
        src.encode("ascii")
