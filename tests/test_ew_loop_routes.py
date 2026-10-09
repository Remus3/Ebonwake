"""Plan 099 (perf 2.5): model / effort routing by item kind. Opus for plan
implementation; sonnet for fix rounds, data refreshes, resolve-merge and
research; effort low for the verifier and the inbox answer via the kit's
pick_effort. The table is the loop config's default (load_config "routes")."""

import contextlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
import ew_lane  # noqa: E402
import ew_loop  # noqa: E402
import test_ew_loop as L  # noqa: E402

EXPECTED = {
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


def test_route_table_is_the_plan_table():
    assert ew_loop.ROUTES == EXPECTED


def test_route_validation_matches_the_kit():
    kit = ew_lane.kit()
    assert ew_loop.ROUTE_EFFORTS == kit.EFFORTS
    assert ew_loop._ROUTE_MODEL.pattern == kit._MODEL.pattern
    for model, effort in ew_loop.ROUTES.values():
        assert kit.pick_model(True, model) == model
        assert kit.pick_effort("lane-build", effort) == effort


def test_load_config_carries_routes_and_merges_valid_overrides(tmp_path):
    root = L.make_root(tmp_path)
    assert ew_loop.load_config(root)["routes"] == EXPECTED
    (root / "config").mkdir()
    (root / "config" / "local.json").write_text(json.dumps({"loop": {"routes": {
        "fix": {"model": "opus"},
        "handoff": {"model": "sonnet", "effort": "high"},
        "verify": {"effort": "turbo"},          # bad effort: ignored
        "resolve": {"model": "gpt-4"},          # bad model: ignored
        "bogus": {"model": "opus"},             # unknown kind: ignored
        "plan": "opus",                         # not an object: ignored
    }}}))
    routes = ew_loop.load_config(root)["routes"]
    assert routes["fix"] == ("opus", "medium")
    assert routes["handoff"] == ("sonnet", "high")
    assert routes["verify"] == EXPECTED["verify"]
    assert routes["resolve"] == EXPECTED["resolve"]
    assert routes["plan"] == EXPECTED["plan"]
    assert "bogus" not in routes
    assert ew_loop.ROUTES == EXPECTED  # the default table is never mutated


def test_route_kind():
    rk = ew_loop.route_kind
    assert rk({"kind": "plan", "id": "012"}) == "plan"
    assert rk({"kind": "resolve", "base_kind": "plan", "id": "012"}) == "resolve"
    assert rk({"kind": "handoff", "id": "Dab12cd"}) == "data"
    assert rk({"kind": "handoff", "id": "Hab12cd"}) == "handoff"
    assert rk({"kind": "deep-dive", "id": "DD-20261009"}) == "deep-dive"
    assert rk({"kind": "order", "id": "Oabc"}) == "order"
    assert rk({"kind": "mystery", "id": "x"}) == "plan"


def _worker(tmp_path, kind, iid="012", **extra):
    root = L.make_root(tmp_path)
    items = ew_loop.Items(root)
    items.put(dict({"id": iid, "kind": kind, "state": "dispatched", "lane": "build",
                    "prompt": "p", "title": "t", "label": "l", "attempts": 1}, **extra))
    seen = []

    def run_lane(lane, prompt, **kw):
        seen.append(kw)
        return {"rc": 0, "error": None, "worktree": "wt"}

    assert ew_loop.lane_worker(iid, deps=L.deps(root), run_lane=run_lane) == 0
    return seen[0], items.get(iid)


def test_plan_lane_runs_opus_and_records_the_route(tmp_path):
    kw, rec = _worker(tmp_path, "plan")
    assert kw["model"] == "opus" and kw["effort"] == "medium" and kw["writes_code"] is True
    assert rec["route"] == {"model": "opus", "effort": "medium"}


def test_data_deep_dive_and_handoff_routes(tmp_path):
    kw, rec = _worker(tmp_path / "a", "handoff", iid="Dab12cd")
    assert (kw["model"], kw["effort"]) == ("sonnet", "medium")
    assert rec["route"]["model"] == "sonnet"
    kw, _ = _worker(tmp_path / "b", "deep-dive", iid="DD-20261009")
    assert kw["model"] == "sonnet"
    kw, _ = _worker(tmp_path / "c", "handoff", iid="Hab12cd")
    assert kw["model"] == "opus"


def test_resolve_lane_runs_sonnet(tmp_path):
    root = L.make_root(tmp_path)
    items = ew_loop.Items(root)
    items.put({"id": "012", "kind": "resolve", "base_kind": "plan", "state": "dispatched",
               "lane": "build", "prompt": "p", "title": "t", "label": "l", "attempts": 1,
               "keep_ref": "refs/ew/keep/012", "resolve_runs": 1})
    seen = []

    def run_lane(lane, prompt, **kw):
        seen.append(kw)
        return {"rc": 0, "error": None, "worktree": "wt"}

    ew_loop.lane_worker("012", deps=L.deps(root), run_lane=run_lane)
    assert (seen[0]["model"], seen[0]["effort"]) == ("sonnet", "medium")


def test_verifier_is_sonnet_low_and_fix_rounds_are_sonnet(tmp_path):
    root, wt = L.git_world(tmp_path)
    fail = {"rc": 0, "error": None, "result": "VERDICT: FAIL\n1. nit"}
    sp = L.FakeSpawn([fail, {"rc": 0}, {"rc": 0, "error": None, "result": "VERDICT: PASS"}])
    d = L.deps(root, spawn=sp, git=L.real_git,
               lane_state=lambda: [{"index": i, "state": "RUNNING", "lane": n}
                                   for i, n in enumerate(("build", "data", "review"))])
    ew_loop.tick(deps=d, no_push=True)
    verifies = [c for c in sp.calls if not c["writes_code"]]
    fixes = [c for c in sp.calls if c["writes_code"]]
    assert len(verifies) == 2 and len(fixes) == 1
    assert all(c["model"] == "sonnet" and c["effort"] == "low" for c in verifies)
    assert fixes[0]["model"] == "sonnet" and fixes[0]["effort"] == "medium"


def test_order_answer_is_sonnet_low(tmp_path):
    root, inbox, outbox = L._inbox_root(tmp_path, roadmap=L.ROADMAP)
    sp = L.FakeSpawn([{"rc": 0, "error": None, "result": "# From EW - ANSWER\ndone"}])
    d = L.deps(root, spawn=sp)
    ew_loop.tick(deps=d, no_push=True)  # baseline
    name = "b-from-MAIN-ORDER-to-EW-harden.md"
    (inbox / name).write_text("# From MAIN - ORDER\nadd dependabot\n")
    ew_loop.tick(deps=d, no_push=True)
    oid = ew_loop.order_id(name)
    rec = ew_loop.Items(root).get(oid)
    rec.update(state="merged", commit="abc123", verdict="PASS", rounds=1)
    ew_loop.Items(root).put(rec)
    ew_loop.tick(deps=d, no_push=True)
    calls = [c for c in sp.calls if c.get("note") == name]
    assert len(calls) == 1 and calls[0]["kind"] == "inbox"
    assert calls[0]["model"] == "sonnet" and calls[0]["effort"] == "low"


def _lane_fakes(tmp_path):
    seen = []
    wt = tmp_path / "ew-worktrees" / "lane-1"

    @contextlib.contextmanager
    def lane_ctx(root, code, name, run_id, cap):
        yield {"worktree": wt, "index": 1}

    def spawn(root, code, prompt, **kw):
        seen.append(kw)
        return {"rc": 0, "error": None}

    return seen, dict(lane_ctx=lane_ctx, spawn=spawn,
                      progress=lambda *a, **k: None, record=lambda *a, **k: None)


def test_run_lane_forwards_model_and_effort_only_when_given(tmp_path):
    seen, f = _lane_fakes(tmp_path)
    ew_lane.run_lane("build", "x", writes_code=True, root=tmp_path, **f)
    assert "model" not in seen[0] and "effort" not in seen[0]
    ew_lane.run_lane("build", "x", writes_code=True, root=tmp_path, model="sonnet",
                     effort="low", **f)
    assert seen[1]["model"] == "sonnet" and seen[1]["effort"] == "low"
