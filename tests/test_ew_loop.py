"""Plan 015: tools/ew_loop.py tick. Every side effect is injected: no test
spawns claude, touches a git remote or calls schtasks. Merge tests use a
throwaway local git repo under tmp_path."""

import contextlib
import datetime as _dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import ew_loop  # noqa: E402

# Plan 097: most tests here build a real git world under tmp_path; the
# module is one git unit (--dist loadfile keeps it on one worker). Tests over
# about 1.5 s are also marked slow.
pytestmark = pytest.mark.git

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
- NOTE (new, operator 2026-10-06): one character, no alts.
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
        roster_path=None,  # plan 093: never the machine roster from FLEET_ROSTER
    )
    base.update(over)
    d = ew_loop.Deps(root, **base)
    d.seen = seen
    # plan 098: the detached review / push workers run inline in tests (same
    # process, same deps), so a tick's observable end state is unchanged
    if "launch_review" not in over:
        d.launch_review = lambda iid: ew_loop.review_worker(iid, deps=d) or 4243
    if "launch_push" not in over:
        d.launch_push = lambda: ew_loop.push_worker(deps=d) or 4244
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
    assert [i["skip"] for i in items] == ["operator", "operator", "other-tree", "info", "info", None]
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


def test_inbox_defaults_to_fleet_moon_sync_dirs(tmp_path):
    cfg = ew_loop.load_config(tmp_path)
    assert Path(cfg["inbox_dir"]) == tmp_path / "moon_sync_inbox"
    assert Path(cfg["outbox_dir"]) == tmp_path / "moon_sync_outbox"


# ---------------------------------------------------------------- inbox

def test_inbox_baseline_then_one_answer_per_note(tmp_path):
    inbox, outbox = tmp_path / "in", tmp_path / "out"
    inbox.mkdir()
    outbox.mkdir()
    (inbox / "old-from-MAIN-ORDER-x.md").write_text("# From MAIN - ORDER\nold\n")
    root = make_root(tmp_path, roadmap="", handoff="",
                     local={"loop": {"inbox_dir": str(inbox), "outbox_dir": str(outbox)}})
    sp = FakeSpawn([{"rc": 0, "error": None,
                     "result": "VERDICT: ANSWER\nyes " + chr(0x2014) + " ok"}])
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
    # kit v8 item 14 b: one triage spawn, sonnet, effort low, bare, kind triage
    assert c["model"] == "sonnet" and c["effort"] == "low" and c["bare"] is True
    assert c["kind"] == "triage" and c["writes_code"] is False
    assert "governor" not in c and c["stdin"] is True
    replies = list(outbox.glob("*-from-EW-ANSWER-to-MAIN-*.md"))
    assert len(replies) == 1
    text = replies[0].read_bytes().decode("ascii")
    assert "HOP: 2" in text and "yes - ok" in text and "n1-from-MAIN-QUESTION-y.md" in text
    ew_loop.tick(deps=d, no_push=True)  # re-run is a no-op
    assert len([c for c in sp.calls if c.get("note", "").endswith(".md")]) == 1
    seen = (root / "ops/loop/control/inbox_seen.jsonl").read_text()
    assert "n1-from-MAIN-QUESTION-y.md" in seen and "n3-from-MAIN-INFORMATION-w.md" in seen


def test_inbox_failed_answer_is_reoffered(tmp_path):
    inbox, outbox = tmp_path / "in", tmp_path / "out"
    inbox.mkdir()
    outbox.mkdir()
    root = make_root(tmp_path, roadmap="", handoff="",
                     local={"loop": {"inbox_dir": str(inbox), "outbox_dir": str(outbox)}})
    sp = FakeSpawn([{"rc": 1, "error": "boom", "result": None},
                    {"rc": 0, "error": None, "result": "VERDICT: ANSWER\nfine"}])
    d = deps(root, spawn=sp)
    ew_loop.tick(deps=d, no_push=True)
    (inbox / "a-from-MAIN-QUESTION-q.md").write_text("# From MAIN - QUESTION\ndo\n")
    ew_loop.tick(deps=d, no_push=True)
    assert not list(outbox.iterdir())
    ew_loop.tick(deps=d, no_push=True)
    assert len(list(outbox.iterdir())) == 1


def test_triage_noreply_or_ack_writes_no_note(tmp_path):
    inbox, outbox = tmp_path / "in", tmp_path / "out"
    inbox.mkdir()
    outbox.mkdir()
    root = make_root(tmp_path, roadmap="", handoff="",
                     local={"loop": {"inbox_dir": str(inbox), "outbox_dir": str(outbox)}})
    sp = FakeSpawn([{"rc": 0, "error": None, "result": "VERDICT: NOREPLY"},
                    {"rc": 0, "error": None, "result": "no verdict at all"}])
    d = deps(root, spawn=sp)
    ew_loop.tick(deps=d, no_push=True)
    (inbox / "a-from-MAIN-QUESTION-q.md").write_text("# From MAIN - QUESTION\nq\n")
    (inbox / "b-from-SS-PROPOSAL-p.md").write_text("# From SS - PROPOSAL\np\n")
    ew_loop.tick(deps=d, no_push=True)
    assert not list(outbox.iterdir()) and len(sp.calls) == 2
    ew_loop.tick(deps=d, no_push=True)
    assert len(sp.calls) == 2


def _inbox_root(tmp_path, roadmap="", local_extra=None):
    inbox, outbox = tmp_path / "in", tmp_path / "out"
    inbox.mkdir()
    outbox.mkdir()
    loop = {"inbox_dir": str(inbox), "outbox_dir": str(outbox)}
    loop.update(local_extra or {})
    root = make_root(tmp_path, roadmap=roadmap, handoff="", local={"loop": loop})
    return root, inbox, outbox


def test_order_note_escalates_to_lane_item_then_answers_after_merge(tmp_path):
    root, inbox, outbox = _inbox_root(tmp_path, roadmap=ROADMAP)
    sp = FakeSpawn([{"rc": 0, "error": None, "result": "# From EW - ANSWER\ndone"}])
    d = deps(root, spawn=sp)
    ew_loop.tick(deps=d, no_push=True)  # baseline
    name = "b-from-MAIN-ORDER-to-EW-harden.md"
    (inbox / name).write_text("# From MAIN - ORDER\nadd dependabot\n")
    ew_loop.tick(deps=d, no_push=True)
    oid = ew_loop.order_id(name)
    assert [c for c in sp.calls if c.get("note") == name] == []  # no ack spawn
    assert d.seen["launch"][-1] == oid
    assert ew_loop.Tick(d).work_list()[1][0]["id"] == oid  # orders first
    rec = ew_loop.Items(root).get(oid)
    assert rec["kind"] == "order" and "add dependabot" in rec["prompt"]
    assert not list(outbox.iterdir())
    rec.update(state="merged", commit="abc123", verdict="PASS", rounds=1)
    ew_loop.Items(root).put(rec)
    ew_loop.tick(deps=d, no_push=True)
    calls = [c for c in sp.calls if c.get("note") == name]
    assert len(calls) == 1 and "abc123" in calls[0]["prompt"]
    assert calls[0]["kind"] == "inbox"
    replies = list(outbox.glob("*-re-b-from-MAIN-ORDER-to-EW-harden.md"))
    assert len(replies) == 1
    lines = replies[0].read_text().splitlines()
    assert lines[0] == "# From EW - ANSWER" and lines[1] == "HOP: 2"
    ew_loop.tick(deps=d, no_push=True)
    assert len([c for c in sp.calls if c.get("note") == name]) == 1


def test_daily_note_cap_defers_answers(tmp_path):
    root, inbox, outbox = _inbox_root(tmp_path, local_extra={"max_notes_per_day": 1})
    sp = FakeSpawn([{"rc": 0, "error": None, "result": "VERDICT: ANSWER\nok"}] * 3)
    d = deps(root, spawn=sp)
    ew_loop.tick(deps=d, no_push=True)
    (inbox / "q1-from-MAIN-QUESTION-a.md").write_text("q1\n")
    (inbox / "q2-from-SS-QUESTION-b.md").write_text("q2\n")
    doc = ew_loop.tick(deps=d, no_push=True)
    assert len(list(outbox.iterdir())) == 1
    assert any("daily note cap 1" in s for s in doc["log"])
    assert any("1 capped" in s for s in doc["log"])
    assert not any("deliver-failed" in s for s in doc["log"])


def test_answers_to_one_destination_batch_into_one_note(tmp_path):
    root, inbox, outbox = _inbox_root(tmp_path)
    sp = FakeSpawn([{"rc": 0, "error": None, "result": "VERDICT: ANSWER\nfirst"},
                    {"rc": 0, "error": None, "result": "VERDICT: ANSWER\nsecond"}])
    d = deps(root, spawn=sp)
    ew_loop.tick(deps=d, no_push=True)
    (inbox / "q1-from-MAIN-QUESTION-a.md").write_text("# From MAIN - QUESTION\nq1\n")
    (inbox / "q2-from-MAIN-QUESTION-b.md").write_text("# From MAIN - QUESTION\nq2\n")
    ew_loop.tick(deps=d, no_push=True)
    notes = list(outbox.iterdir())
    assert len(notes) == 1
    body = notes[0].read_text()
    assert "first" in body and "second" in body and "HOP: 2" in body
    ledger = (root / "ops/loop/control/outbound_notes.jsonl").read_text().splitlines()
    assert len(ledger) == 1 and json.loads(ledger[0])["parts"] == 2


# ---------------------------------------------------------------- plan 093 delivery

def _sha(path):
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _deliveries(root):
    return [json.loads(x) for x in
            (root / "ops/loop/control/delivery.jsonl").read_text().splitlines()]


def test_batch_reply_lands_in_destination_inbox_with_matching_hash(tmp_path):
    dest = tmp_path / "main_inbox"
    dest.mkdir()
    root, inbox, outbox = _inbox_root(tmp_path, local_extra={
        "dest_inboxes": {"MAIN": str(dest)}})
    sp = FakeSpawn([{"rc": 0, "error": None, "result": "VERDICT: ANSWER\nyes"}])
    d = deps(root, spawn=sp)
    ew_loop.tick(deps=d, no_push=True)
    (inbox / "q1-from-MAIN-QUESTION-a.md").write_text("# From MAIN - QUESTION\nq1\n")
    doc = ew_loop.tick(deps=d, no_push=True)
    [out] = list(outbox.iterdir())
    [copy] = list(dest.iterdir())
    assert copy.name == out.name
    assert re.match(r"\d{4}-\d{2}-\d{2}-\d{4}-from-EW-ANSWER-", copy.name)
    assert _sha(copy) == _sha(out)
    [rec] = _deliveries(root)
    assert rec["note"] == out.name and rec["to"] == "MAIN" and rec["reached"] is True
    assert rec["sha256"] == _sha(out)
    assert any("1/1 reached" in s for s in doc["log"])
    ew_loop.tick(deps=d, no_push=True)  # re-run is a no-op
    assert len(_deliveries(root)) == 1 and len(list(dest.iterdir())) == 1


def test_order_answer_uses_dashed_stamp_and_lands_in_sender_inbox(tmp_path):
    dest = tmp_path / "main_inbox"
    dest.mkdir()
    root, inbox, outbox = _inbox_root(tmp_path, roadmap=ROADMAP, local_extra={
        "dest_inboxes": {"MAIN": str(dest)}})
    sp = FakeSpawn([{"rc": 0, "error": None, "result": "# From EW - ANSWER\ndone"}])
    d = deps(root, spawn=sp)
    ew_loop.tick(deps=d, no_push=True)
    name = "b-from-MAIN-ORDER-to-EW-harden.md"
    (inbox / name).write_text("# From MAIN - ORDER\nadd dependabot\n")
    ew_loop.tick(deps=d, no_push=True)
    rec = ew_loop.Items(root).get(ew_loop.order_id(name))
    rec.update(state="merged", commit="abc123", verdict="PASS", rounds=1)
    ew_loop.Items(root).put(rec)
    ew_loop.tick(deps=d, no_push=True)
    [out] = list(outbox.iterdir())
    assert re.match(r"\d{4}-\d{2}-\d{2}-\d{4}-from-EW-ANSWER-re-b-from-MAIN", out.name)
    [copy] = list(dest.iterdir())
    assert copy.name == out.name and _sha(copy) == _sha(out)


def test_roster_resolves_destination_inbox(tmp_path):
    main_root = tmp_path / "mainrepo"
    (main_root / "moon_sync_inbox").mkdir(parents=True)
    roster = tmp_path / "fleet_roster.json"
    roster.write_text(json.dumps({"repos": [{"code": "MAIN", "root": str(main_root)}]}))
    root, inbox, outbox = _inbox_root(tmp_path)
    sp = FakeSpawn([{"rc": 0, "error": None, "result": "VERDICT: ANSWER\nyes"}])
    d = deps(root, spawn=sp, roster_path=roster)
    ew_loop.tick(deps=d, no_push=True)
    (inbox / "q1-from-MAIN-QUESTION-a.md").write_text("# From MAIN - QUESTION\nq1\n")
    ew_loop.tick(deps=d, no_push=True)
    [out] = list(outbox.iterdir())
    [copy] = list((main_root / "moon_sync_inbox").iterdir())
    assert _sha(copy) == _sha(out)


def test_unresolved_destination_is_recorded_0_of_1_then_retried(tmp_path):
    root, inbox, outbox = _inbox_root(tmp_path)
    sp = FakeSpawn([{"rc": 0, "error": None, "result": "VERDICT: ANSWER\nyes"}])
    d = deps(root, spawn=sp)
    ew_loop.tick(deps=d, no_push=True)
    (inbox / "q1-from-MAIN-QUESTION-a.md").write_text("# From MAIN - QUESTION\nq1\n")
    doc = ew_loop.tick(deps=d, no_push=True)
    assert len(list(outbox.iterdir())) == 1  # the outbox copy is still the record
    assert any("0/1 reached" in s for s in doc["log"])
    assert _deliveries(root)[-1]["reached"] is False
    # the destination becomes known: the next tick delivers the outbox copy
    dest = tmp_path / "main_inbox"
    dest.mkdir()
    cfg = json.loads((root / "config/local.json").read_text())
    cfg["loop"]["dest_inboxes"] = {"MAIN": str(dest)}
    (root / "config/local.json").write_text(json.dumps(cfg))
    doc = ew_loop.tick(deps=d, no_push=True)
    [out] = list(outbox.iterdir())
    [copy] = list(dest.iterdir())
    assert _sha(copy) == _sha(out) and _deliveries(root)[-1]["reached"] is True
    assert any("redelivered 1/1 reached" in s for s in doc["log"])
    assert len(sp.calls) == 1  # no second triage spawn


def test_existing_destination_copy_with_other_bytes_is_not_overwritten(tmp_path):
    dest = tmp_path / "main_inbox"
    dest.mkdir()
    root, inbox, outbox = _inbox_root(tmp_path, local_extra={
        "dest_inboxes": {"MAIN": str(dest)}})
    t = ew_loop.Tick(deps(root))
    note = outbox / "2026-10-08-2300-from-EW-ANSWER-to-MAIN-x.md"
    note.write_text("# From EW - ANSWER\nHOP: 2\nreal\n", newline="\n")
    (dest / note.name).write_text("other\n", newline="\n")
    assert t.deliver_note(note, "MAIN") is False
    assert (dest / note.name).read_text() == "other\n"
    assert _deliveries(root)[-1]["reached"] is False


def test_max_notes_default_is_kit_cap_and_config_cannot_raise_it(tmp_path):
    assert ew_loop.load_config(tmp_path)["max_notes_per_day"] == 6
    root = make_root(tmp_path, local={"loop": {"max_notes_per_day": 12}})
    assert ew_loop.load_config(root)["max_notes_per_day"] == 6
    root2 = make_root(tmp_path / "b", local={"loop": {"max_notes_per_day": 2}})
    assert ew_loop.load_config(root2)["max_notes_per_day"] == 2


def test_order_answer_respects_slot_held_by_a_batch(tmp_path):
    # verifier r1 finding 2: the order answer must not take the slot a batch holds
    root, inbox, outbox = _inbox_root(tmp_path, roadmap=ROADMAP,
                                      local_extra={"max_notes_per_day": 1})
    sp = FakeSpawn([{"rc": 0, "error": None, "result": "VERDICT: ANSWER\nq answer"},
                    {"rc": 0, "error": None, "result": "# From EW - ANSWER\ndone"}])
    d = deps(root, spawn=sp)
    ew_loop.tick(deps=d, no_push=True)
    order = "o-from-MAIN-ORDER-to-EW-x.md"
    (inbox / "a-from-MAIN-QUESTION-q.md").write_text("# From MAIN - QUESTION\nq\n")
    (inbox / order).write_text("# From MAIN - ORDER\ndo\n")
    rec = {"id": ew_loop.order_id(order), "kind": "order", "state": "merged",
           "commit": "c0ffee", "verdict": "PASS", "rounds": 0, "title": order}
    ew_loop.Items(root).put(rec)
    doc = ew_loop.tick(deps=d, no_push=True)
    assert len(list(outbox.iterdir())) == 1
    line = [s for s in doc["log"] if s.startswith("inbox:")][-1]
    assert "deliver-failed" not in line and "1 capped" in line
    assert [c.get("note") for c in sp.calls] == ["a-from-MAIN-QUESTION-q.md"]


def test_ack_class_and_hop_limit_are_ledger_lines_not_notes(tmp_path):
    root, inbox, outbox = _inbox_root(tmp_path)
    sp = FakeSpawn()
    d = deps(root, spawn=sp)
    ew_loop.tick(deps=d, no_push=True)
    (inbox / "a-from-MAIN-ANSWER-re-x.md").write_text("# From MAIN - ANSWER\nthanks\n")
    (inbox / "b-from-MAIN-ACK-y.md").write_text("# From MAIN - ACK\nok\n")
    (inbox / "c-from-MAIN-QUESTION-z.md").write_text("# From MAIN - QUESTION\nHOP: 2\nq?\n")
    doc = ew_loop.tick(deps=d, no_push=True)
    assert sp.calls == [] and not list(outbox.iterdir())
    assert sum(s.startswith("inbox ack") for s in doc["log"]) == 3
    seen = (root / "ops/loop/control/inbox_seen.jsonl").read_text().splitlines()
    assert [json.loads(x)["action"] for x in seen] == ["ack"] * 3


def test_ack_quoting_an_order_name_is_not_escalated(tmp_path):
    # verifier r2: escalation follows classify(), not an -ORDER- token anywhere
    root, inbox, outbox = _inbox_root(tmp_path, roadmap=ROADMAP)
    sp = FakeSpawn()
    d = deps(root, spawn=sp)
    ew_loop.tick(deps=d, no_push=True)
    for name in ("a-from-MAIN-ACK-re-b-from-EW-ANSWER-re-c-from-MAIN-ORDER-to-EW-x.md",
                 "d-from-MAIN-ANSWER-re-e-FIX-plan.md"):
        (inbox / name).write_text("# From MAIN - ACK\nseen\n")
    doc = ew_loop.tick(deps=d, no_push=True)
    assert sp.calls == [] and not list(outbox.iterdir())
    assert not (root / "ops/loop/control/loop_orders.json").exists()
    assert sum(s.startswith("inbox ack") for s in doc["log"]) == 2


def test_note_settled_by_pre_v8_ledger_is_not_retriaged(tmp_path):
    root, inbox, outbox = _inbox_root(tmp_path)
    sp = FakeSpawn()
    d = deps(root, spawn=sp)
    ew_loop.tick(deps=d, no_push=True)
    (root / "ops/loop/control/inbox_ledger.jsonl").write_text(
        json.dumps({"note": "q-from-MAIN-QUESTION-a.md", "action": "ack"}) + "\n")
    (inbox / "q-from-MAIN-QUESTION-a.md").write_text("# From MAIN - QUESTION\nq\n")
    ew_loop.tick(deps=d, no_push=True)
    assert sp.calls == []


def test_default_cap_reaches_the_kit_outbound_cap(tmp_path):
    root, _, _ = _inbox_root(tmp_path, local_extra={"max_notes_per_day": 40})
    assert ew_loop.Tick(deps(root)).cap.cap == 6


def test_unlabelled_tick_spawns_are_kind_build(tmp_path):
    sp = FakeSpawn()
    t = ew_loop.Tick(deps(make_root(tmp_path), spawn=sp))
    t.spawn("p", note="lane-review-x", writes_code=False)
    assert sp.calls[-1]["kind"] == "build"


def test_pending_orders_are_not_delivery_failures_and_handled_notes_stay_handled(tmp_path):
    # 2026-10-05: loop.json read "inbox: deliver-failed 2 2 pending" - two ORDERs
    # waiting for their lane items. Pending is by design; the label was wrong,
    # and every other note of the re-offered batch was processed again each tick.
    root, inbox, outbox = _inbox_root(tmp_path, roadmap=ROADMAP)
    sp = FakeSpawn([{"rc": 0, "error": None, "result": "VERDICT: ACK"}] * 5)
    d = deps(root, spawn=sp)
    ew_loop.tick(deps=d, no_push=True)  # baseline
    (inbox / "o-from-MAIN-ORDER-to-EW-x.md").write_text("# From MAIN - ORDER\nwork\n")
    (inbox / "q-from-MAIN-QUESTION-y.md").write_text("# From MAIN - QUESTION\nq\n")
    doc = ew_loop.tick(deps=d, no_push=True)
    line = [s for s in doc["log"] if s.startswith("inbox:")][-1]
    assert "deliver-failed" not in line and "1 order(s) awaiting lane" in line
    triage = [c for c in sp.calls if c.get("note") == "q-from-MAIN-QUESTION-y.md"]
    assert len(triage) == 1
    ew_loop.tick(deps=d, no_push=True)  # the order is still pending
    triage = [c for c in sp.calls if c.get("note") == "q-from-MAIN-QUESTION-y.md"]
    assert len(triage) == 1  # never re-triaged


def test_priority_rows_dispatch_first(tmp_path):
    rm = ROADMAP.replace("| 013 | Season pass tracker | [ ] open |",
                         "| 013 | Season pass tracker | [ ] open (priority) |")
    root = make_root(tmp_path, roadmap=rm, handoff="")
    d = deps(root)
    ew_loop.tick(deps=d, no_push=True)
    assert d.seen["launch"][:2] == ["013", "012"]


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
    (inbox / "a-from-MAIN-QUESTION-q.md").write_text("# From MAIN - QUESTION\ndo\n")
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
    # FLEET item 13 d (kit v7/v8): remaining tasks as {id, task, state, eta_s}
    root = make_root(tmp_path)
    d = deps(root)
    doc = ew_loop.tick(deps=d, no_push=True)
    cl = doc["checklist"]
    assert all(set(r) == {"id", "task", "state", "eta_s"} for r in cl)
    assert cl[0] == {"id": "012", "task": "Grind spot recommender", "state": "dispatched",
                     "eta_s": 2700}
    assert any(r["state"] == "operator-only, skipped" for r in cl)
    assert any(r["state"] == "other-tree-only, skipped" for r in cl)
    assert len(cl) <= 20 and doc["fire"] == 1
    assert ew_loop.tick(deps=d, no_push=True)["fire"] == 2
    block = ew_loop.render_checklist(progress(root))
    lines = block.splitlines()
    assert lines[0] == "Session 2 checklist" and lines[-1] == chr(0x2610) + " /done"
    assert lines[1] == chr(0x2610) + " 012: Grind spot recommender (dispatched, ~45m)"
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


def test_refute_rounds_cap_at_three_then_adjudicate_unmerged(tmp_path):
    root, wt = git_world(tmp_path)
    fail = {"rc": 0, "error": None, "result": "VERDICT: FAIL\n1. nit"}
    sp = FakeSpawn([fail, {"rc": 0}, fail, {"rc": 0}, fail, {"rc": 0}, fail])
    d = deps(root, spawn=sp, git=real_git,
             lane_state=lambda: [{"index": i, "state": "RUNNING", "lane": n}
                                 for i, n in enumerate(("build", "data", "review"))])
    ew_loop.tick(deps=d, no_push=True)
    rec = ew_loop.Items(root).get("012")
    assert rec["state"] == "adjudicate" and rec["rounds"] == 3
    assert rec["verdict"] == "adjudicate"
    fixes = [c for c in sp.calls if c["writes_code"]]
    assert len(fixes) == 3 and all("1. nit" in c["prompt"] for c in fixes)
    assert len([c for c in sp.calls if not c["writes_code"]]) == 3  # no round 4 verify
    assert not (root / "feature.txt").exists()  # never merged into main
    assert "refute-rounds: 3/3" in git(root, "log", "-1", "--format=%B", rec["commit"])


@pytest.mark.slow
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


def _dispatched(root, iid="012", **extra):
    items = ew_loop.Items(root)
    items.put(dict({"id": iid, "kind": "plan", "state": "dispatched", "lane": "data",
                    "prompt": "p", "title": "t", "label": "l", "attempts": 1}, **extra))
    return items


def test_lane_worker_rechecks_halt_backoff_and_cap_and_passes_halt_file(tmp_path):
    root = make_root(tmp_path)
    ran = []

    def run_lane(lane, prompt, spawn=None, root=None, **kw):
        ran.append(lane)
        spawn(root, "EW", prompt)
        return {"rc": 0, "error": None, "worktree": "wt"}

    items = _dispatched(root)
    halt = root / ew_loop.HALT_REL
    halt.parent.mkdir(parents=True, exist_ok=True)
    halt.write_text("")
    assert ew_loop.lane_worker("012", deps=deps(root), run_lane=run_lane) == 0
    rec = items.get("012")
    assert ran == [] and rec["state"] == "paused" and rec["attempts"] == 0
    assert ew_loop.dispatchable(rec)  # a pause costs no attempt
    halt.unlink()
    d = deps(root)
    ew_loop.backoff_hit(root, d.clock(), "429")
    _dispatched(root)
    ew_loop.lane_worker("012", deps=d, run_lane=run_lane)
    assert ran == [] and items.get("012")["error"] == "paused: backoff"
    ew_loop.backoff_clear(root)
    p = root / "ops/loop/control/headless_budget.json"
    p.write_text(json.dumps({"starts": [d.clock() - 10] * (d.budget.cap - 3)}))
    d.budget.clock = d.clock
    _dispatched(root)
    ew_loop.lane_worker("012", deps=d, run_lane=run_lane)
    assert ran == [] and items.get("012")["error"] == "paused: runs cap"
    p.unlink()
    sp = FakeSpawn()
    _dispatched(root)
    ew_loop.lane_worker("012", deps=deps(root, spawn=sp), run_lane=run_lane)
    assert ran == ["data"] and sp.calls[0]["halt_file"] == halt


def test_lane_worker_halt_raised_by_kit_mid_run_is_a_pause(tmp_path):
    root = make_root(tmp_path)
    items = _dispatched(root)
    halt = root / ew_loop.HALT_REL

    def run_lane(lane, prompt, **kw):
        halt.parent.mkdir(parents=True, exist_ok=True)
        halt.write_text("")
        raise RuntimeError("Refused: halt file present")

    ew_loop.lane_worker("012", deps=deps(root), run_lane=run_lane)
    assert items.get("012")["state"] == "paused"


def test_dirty_lane_claim_is_flagged_not_dropped(tmp_path):
    root = make_root(tmp_path, handoff="")
    items = _dispatched(root)

    wt = tmp_path / "ew-worktrees" / "lane-1"
    wt.mkdir(parents=True)

    def run_lane(lane, prompt, **kw):
        raise RuntimeError(f"LaneRefused: lane worktree {wt} is dirty - resolve it by hand")

    assert ew_loop.lane_worker("012", deps=deps(root), run_lane=run_lane) == 1
    rec = items.get("012")
    assert rec["state"] == "lane-dirty" and "dirty" in rec["error"]
    assert not ew_loop.dispatchable(rec)
    # still dirty: it waits (recover_lane_dirty clears it once clean)
    doc = ew_loop.tick(deps=deps(root, git=FakeGit(dirty=" M x")), no_push=True)
    row = next(r for r in doc["checklist"] if r["id"] == "012")
    assert row["state"] == "lane-dirty" and row["eta_s"] == 0


def test_exhausted_attempts_become_gave_up_with_the_error(tmp_path):
    root = make_root(tmp_path, roadmap=ROADMAP.replace("| 013 | Season pass tracker | [ ] open |\n", ""),
                     handoff="")
    ew_loop.Items(root).put({"id": "012", "kind": "plan", "state": "refused", "lane": "data",
                             "attempts": ew_loop.MAX_ATTEMPTS, "error": "lanes_full",
                             "title": "Grind spot recommender", "label": "l"})
    d = deps(root)
    doc = ew_loop.tick(deps=d, no_push=True)
    assert ew_loop.Items(root).get("012")["state"] == "gave-up"
    assert any("gave up after 2 attempts: lanes_full" in s for s in doc["log"])
    assert next(r for r in doc["checklist"] if r["id"] == "012")["state"] == "gave-up"
    assert "012" not in d.seen["launch"]


def test_launch_exception_marks_lost_and_frees_the_lane(tmp_path):
    root = make_root(tmp_path, handoff="")
    calls = []

    def launch(iid):
        calls.append(iid)
        if iid == "012":
            raise OSError("no pythonw")
        return 99

    d = deps(root, launch=launch)
    ew_loop.tick(deps=d, no_push=True)
    items = ew_loop.Items(root)
    r12 = items.get("012")
    assert r12["state"] == "lost" and "OSError" in r12["error"] and not r12.get("pid")
    assert items.get("013")["lane"] == "build"  # the failed launch's lane is reused
    ew_loop.tick(deps=d, no_push=True)
    assert calls.count("012") == ew_loop.MAX_ATTEMPTS


def test_dispatched_without_pid_is_reaped(tmp_path):
    root = make_root(tmp_path, roadmap="", handoff="")
    _dispatched(root, title="t")  # no pid: a tick died between its two writes
    d = deps(root)
    doc = ew_loop.tick(deps=d, no_push=True)
    assert any("012: lane process gone" in s for s in doc["log"])
    assert ew_loop.Items(root).get("012")["state"] == "lost"


def test_handoff_password_oauth_per_host_items_are_operator_only():
    text = ("Carried forward:\n- Enter the account password in the launcher.\n"
            "- Grant the OAuth scope for the API.\n- Set the per-host OCR path.\n"
            "- Fix the OCR cache key.\n")
    assert [i["skip"] for i in ew_loop.handoff_items(text)] == ["operator"] * 3 + [None]


def test_checklist_eta_is_lane_median_less_elapsed(tmp_path):
    root = make_root(tmp_path, roadmap="", handoff="")
    kinds = []
    d = deps(root, estimate=lambda kind: kinds.append(kind) or 2700.0)
    ew_loop.Items(root).put({"id": "012", "kind": "plan", "state": "dispatched",
                             "lane": "data", "pid": 1, "title": "t", "label": "l",
                             "dispatched": ew_loop.iso(d.clock() - 600)})
    doc = ew_loop.tick(deps=d, no_push=True)
    assert next(r for r in doc["checklist"] if r["id"] == "012")["eta_s"] == 2100
    assert "lane-data-code" in kinds and "loop-review" not in kinds


@pytest.mark.slow
def test_verifier_crash_is_counted_then_adjudicated(tmp_path):
    root, wt = git_world(tmp_path)
    crash = {"rc": 1, "error": "exit 1", "result": "boom"}
    sp = FakeSpawn([crash, crash, crash])
    d = deps(root, spawn=sp, git=real_git,
             lane_state=lambda: [{"index": i, "state": "RUNNING", "lane": n}
                                 for i, n in enumerate(("build", "data", "review"))])
    ew_loop.tick(deps=d, no_push=True)
    rec = ew_loop.Items(root).get("012")
    assert rec["state"] == "ran" and rec["verify_errors"] == 1
    ew_loop.tick(deps=d, no_push=True)
    ew_loop.tick(deps=d, no_push=True)
    rec = ew_loop.Items(root).get("012")
    assert rec["verify_errors"] == 3 and rec["state"] == "adjudicate"
    assert len(sp.calls) == 3 and not (root / "feature.txt").exists()
    ew_loop.tick(deps=d, no_push=True)
    assert len(sp.calls) == 3  # never retried past the cap


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


# ---------------------------------------------------------------- fix-0130: ci-equal gates

CI_YML = ROOT / ".github" / "workflows" / "ci.yml"


def test_ci_gate_commands_are_read_from_the_ci_workflow():
    cmds = ew_loop.ci_gate_commands(CI_YML.read_text(encoding="utf-8"))
    assert "python -m ruff check server tools tests" in cmds
    assert "python -m pytest -q" in cmds and "npm test --prefix app" in cmds
    assert not any("pip install" in c for c in cmds)  # environment setup, not a gate
    assert cmds.index("python -m ruff check server tools tests") < cmds.index("python -m pytest -q")


def _ci_tree(tmp_path, text=None):
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text(text if text is not None else CI_YML.read_text(encoding="utf-8"),
                               newline="\n")
    return tmp_path


def test_loop_gates_run_exactly_what_ci_runs(tmp_path, monkeypatch):
    # plan 097: xdist args are covered by tests/test_tiers.py; serial here
    monkeypatch.setattr(ew_loop, "_xdist_available", lambda: False)
    cwd, ran = _ci_tree(tmp_path), []

    def run(argv, cwd):
        ran.append(argv)
        return 0, ""

    ok, detail = ew_loop._gates(cwd, run=run)
    assert ok, detail
    want = ew_loop.ci_gate_commands(CI_YML.read_text(encoding="utf-8"))
    assert len(ran) == len(want)
    for argv, cmd in zip(ran, want):
        words = cmd.split()
        if words[0] == "python" and ew_loop._whole_suite(words):
            # kit v12: the whole suite runs through fleet_suite_gate.py
            i = argv.index("--")
            assert argv[0] == ew_loop._python() and "fleet_suite_gate.py" in argv[1]
            assert argv[i + 1] == ew_loop._python() and argv[i + 2:] == words[1:]
        elif words[0] == "python":
            assert argv[0] == ew_loop._python() and argv[1:] == words[1:]
        elif words[0] == "npm":
            assert Path(argv[0]).stem == "npm" and argv[1:] == words[1:]
        else:
            assert argv == words


def test_loop_gates_stop_red_at_ruff(tmp_path):
    cwd, ran = _ci_tree(tmp_path), []

    def run(argv, cwd):
        ran.append(argv)
        return (1, "F401 unused import") if "ruff" in argv else (0, "")

    ok, detail = ew_loop._gates(cwd, run=run)
    assert not ok and "ruff" in detail and "F401" in detail
    assert not any("pytest" in a for a in ran)


def test_loop_gates_fail_closed_without_ci_workflow(tmp_path):
    ok, detail = ew_loop._gates(tmp_path, run=lambda argv, cwd: (0, ""))
    assert not ok and "ci.yml" in detail
    _ci_tree(tmp_path / "x", "name: ci\njobs: {}\n")
    ok, detail = ew_loop._gates(tmp_path / "x", run=lambda argv, cwd: (0, ""))
    assert not ok


def test_loop_gates_fail_closed_on_shell_operators(tmp_path):
    # the loop runs argv without a shell: `a && b` would hand "&&" to a as an
    # argument and never run b, so the loop could merge what ci rejects
    for i, op in enumerate(("&&", "||", "|", ";", ">", "<", "2>&1", "a&&b")):
        cwd, ran = _ci_tree(tmp_path / str(i),
                            f"jobs:\n  check:\n    steps:\n      - run: python a.py {op} python b.py\n"), []
        ok, detail = ew_loop._gates(cwd, run=lambda argv, cwd: ran.append(argv) or (0, ""))
        assert not ok and "shell operator" in detail and not ran, op
    assert ew_loop._shell_operator("python a.py 'x|y' \"a && b\"") is None  # quoted: plain args
    assert ew_loop._shell_operator("python -m pytest -q") is None


# ---------------------------------------------------------------- fix-0130: ROADMAP collisions

def test_resolve_roadmap_takes_both_sides_row_changes():
    base = ROADMAP
    ours = ROADMAP.replace("| 012 | Grind spot recommender | [ ] open |",
                           "| 012 | Grind spot recommender | [x] done A |")
    theirs = ROADMAP.replace("| 013 | Season pass tracker | [ ] open |",
                             "| 013 | Season pass tracker | [x] done B |") + \
        "| 020 | New idea | [ ] open |\n"
    out = ew_loop.resolve_roadmap(base, ours, theirs)
    assert "| 012 | Grind spot recommender | [x] done A |" in out
    assert "| 013 | Season pass tracker | [x] done B |" in out
    assert "| 020 | New idea | [ ] open |" in out
    assert out.index("| 013 |") < out.index("| 020 |")
    # both sides changed one row: main (ours) wins
    both = theirs.replace("[ ] open |\n| 013", "[x] lane says |\n| 013")
    assert "[x] done A" in ew_loop.resolve_roadmap(base, ours, both)
    # a non-row (prose) change on both sides is not auto-resolved
    assert ew_loop.resolve_roadmap(base, ours + "prose A\n", theirs + "prose B\n") is None


def _lane(root, tmp_path, name, iid, edit):
    wt = tmp_path / "ew-worktrees" / name
    git(root, "worktree", "add", "-q", "--detach", str(wt))
    edit(wt)
    ew_loop.Items(root).put({"id": iid, "kind": "plan", "title": f"plan {iid}",
                             "label": f"plan {iid}: t", "state": "ran", "worktree": str(wt),
                             "rc": 0, "lane": name, "rounds": 0})
    return wt


def _flip_in(wt, iid):
    rm = wt / "docs/plans/ROADMAP.md"
    rm.write_text(ew_loop.flip_roadmap(rm.read_text(), iid, "done by lane"), newline="\n")


def _two_lane_world(tmp_path, edit_a, edit_b):
    root = make_root(tmp_path, handoff="")
    git(root, "init", "-q", "-b", "main")
    (root / ".gitignore").write_text("ops/\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    _lane(root, tmp_path, "lane-0", "012", edit_a)
    _lane(root, tmp_path, "lane-1", "013", edit_b)
    sp = FakeSpawn([{"rc": 0, "error": None, "result": "VERDICT: PASS"}] * 2)
    d = deps(root, spawn=sp, git=real_git,
             lane_state=lambda: [{"index": i, "state": "RUNNING", "lane": n}
                                 for i, n in enumerate(("build", "data", "review"))])
    ew_loop.tick(deps=d, no_push=True)
    return root


def test_lane_commit_never_flips_roadmap_merge_does(tmp_path):
    root, wt = git_world(tmp_path)
    sp = FakeSpawn([{"rc": 0, "error": None, "result": "VERDICT: PASS"}])
    d = deps(root, spawn=sp, git=real_git,
             lane_state=lambda: [{"index": i, "state": "RUNNING", "lane": n}
                                 for i, n in enumerate(("build", "data", "review"))])
    ew_loop.tick(deps=d, no_push=True)
    rec = ew_loop.Items(root).get("012")
    assert rec["state"] == "merged"
    lane_files = git(root, "show", "--name-only", "--format=", rec["commit"]).split()
    assert "docs/plans/ROADMAP.md" not in lane_files
    assert "| 012 | Grind spot recommender | [x] done" in git(root, "show", "main:docs/plans/ROADMAP.md")
    assert len(git(root, "log", "-1", "--format=%P", "main").split()) == 2
    assert git(root, "status", "--porcelain") == ""


@pytest.mark.slow
def test_parallel_lanes_flipping_adjacent_rows_both_merge(tmp_path):
    # the Nfa7953 / 013 / 014 shape: two lanes from one base, adjacent ROADMAP rows
    def a(wt):
        (wt / "a.txt").write_text("a\n")
        _flip_in(wt, "012")

    def b(wt):
        (wt / "b.txt").write_text("b\n")
        _flip_in(wt, "013")

    root = _two_lane_world(tmp_path, a, b)
    items = ew_loop.Items(root)
    assert items.get("012")["state"] == "merged" and items.get("013")["state"] == "merged"
    rm = (root / "docs/plans/ROADMAP.md").read_text()
    assert "<<<<" not in rm and rm.count("| 012 |") == 1 and rm.count("| 013 |") == 1
    assert "| 012 | Grind spot recommender | [x] done" in rm
    assert "| 013 | Season pass tracker | [x] done" in rm
    assert (root / "a.txt").exists() and (root / "b.txt").exists()
    assert git(root, "status", "--porcelain") == ""


@pytest.mark.slow
def test_real_code_conflict_still_aborts_cleanly(tmp_path):
    root = _two_lane_world(tmp_path, lambda wt: (wt / "f.txt").write_text("a\n"),
                           lambda wt: (wt / "f.txt").write_text("b\n"))
    states = sorted(ew_loop.Items(root).get(i)["state"] for i in ("012", "013"))
    assert states == ["merge-conflict", "merged"]
    assert git(root, "status", "--porcelain") == ""
    assert not (root / ".git" / "MERGE_HEAD").exists()


def test_plan_and_handoff_prompts_keep_lanes_off_roadmap_and_name_ruff():
    item = {"id": "012", "title": "t", "text": "x"}
    for prompt in (ew_loop.plan_prompt(item), ew_loop.handoff_prompt(item)):
        assert "Do NOT edit docs/plans/ROADMAP.md" in prompt
        assert "python -m ruff check server tools tests" in prompt


def test_lane_prompts_give_scratch_scripts_a_task_unique_name():
    # session 9: two agents' generic prog.py clobbered each other's progress file
    item = {"id": "012", "title": "t", "text": "x"}
    assert "(p012-build_*.py)" in ew_loop.plan_prompt(item)
    assert "(012_*.py)" in ew_loop.handoff_prompt(item)


def test_merge_rerun_of_an_already_merged_commit_is_a_no_op(tmp_path):
    root, wt = git_world(tmp_path)
    sp = FakeSpawn([{"rc": 0, "error": None, "result": "VERDICT: PASS"}])
    d = deps(root, spawn=sp, git=real_git,
             lane_state=lambda: [{"index": i, "state": "RUNNING", "lane": n}
                                 for i, n in enumerate(("build", "data", "review"))])
    ew_loop.tick(deps=d, no_push=True)
    items = ew_loop.Items(root)
    rec = items.get("012")
    assert rec["state"] == "merged"
    head = git(root, "rev-parse", "main")
    rec["state"] = "committed"  # crash between the merge commit and the record write
    items.put(rec)
    ew_loop.tick(deps=d, no_push=True)
    assert items.get("012")["state"] == "merged" and git(root, "rev-parse", "main") == head


def test_ci_gate_commands_block_run_with_blank_line():
    text = "steps:\n  - name: a\n    run: |\n      echo one\n\n      echo two\n  - name: b\n    run: echo three\n"
    assert ew_loop.ci_gate_commands(text) == ["echo one", "echo two", "echo three"]


# ---------------------------------------------------------------- plan 019: dependency gate

DEP_ROADMAP = """# Ebonwake roadmap

| # | Plan | Status |
|---|---|---|
| 001 | Skeleton | [x] done 2026-10-04 |
| 012 | Grind spot recommender | [ ] open |
| 013 | Season pass tracker | [ ] open |
| 014 | Coupon check | [ ] open |
"""

T0 = 1_790_000_000.0
ALL_RUNNING = [{"index": i, "state": "RUNNING", "lane": n}
               for i, n in enumerate(("build", "data", "review"))]


def dep_root(tmp_path, deps_of, roadmap=DEP_ROADMAP):
    """make_root plus one plan doc per id in deps_of with its Depends-on line."""
    root = make_root(tmp_path, roadmap=roadmap, handoff="")
    for iid, line in deps_of.items():
        body = f"# Plan {iid} - t\n\nText mentioning 099 here.\n\n"
        if line is not None:
            body += f"Depends on: {line}\n"
        (root / "docs" / "plans" / f"{iid}-x.md").write_text(body, newline="\n")
    if "012" in deps_of:
        (root / "docs" / "plans" / "012-grind-spots.md").unlink()
    return root


def test_plan_depends_parser():
    assert ew_loop.plan_depends("# Plan 1\n\nDepends on: none.\n") == []
    assert ew_loop.plan_depends("Depends on: 021.\n") == ["021"]
    assert ew_loop.plan_depends("x\nDepends on: 035, 028.\n") == ["035", "028"]
    assert ew_loop.plan_depends("Depends on: 027 (net proceeds) and 031\n") == ["027", "031"]
    assert ew_loop.plan_depends("# Plan 2\nno such line, mentions 027\n") == []
    # only the first matching line, only at a line start
    assert ew_loop.plan_depends("carry `Depends on:` 010\nDepends on: 022.\n"
                                "Depends on: 023.\n") == ["022"]
    assert ew_loop.plan_depends("Depends on: 1234, 12, 05x\n") == []


def test_work_list_skips_rows_waiting_on_open_dependencies(tmp_path):
    root = dep_root(tmp_path, {"012": "013.", "013": "none.", "014": "001."})
    d = deps(root)
    rows, work, skipped = ew_loop.Tick(d).work_list()
    assert [w["id"] for w in work] == ["013", "014"]
    wait = next(s for s in skipped if s["id"] == "012")
    assert wait["reason"] == "waits on 013"
    ew_loop.tick(deps=d, no_push=True)
    assert d.seen["launch"] == ["013", "014"]
    row = next(r for r in progress(root)["checklist"] if r["id"] == "012")
    assert row["state"] == "waits on 013"
    assert ew_loop.render_checklist(progress(root)).count(" 012: ") == 1


def test_done_dependency_dispatches_and_priority_stays_first(tmp_path):
    rm = DEP_ROADMAP.replace("| 013 | Season pass tracker | [ ] open |",
                             "| 013 | Season pass tracker | [x] done |")
    rm = rm.replace("| 014 | Coupon check | [ ] open |",
                    "| 014 | Coupon check | [ ] open (priority) |")
    root = dep_root(tmp_path, {"012": "013.", "014": "001."}, roadmap=rm)
    d = deps(root)
    ew_loop.tick(deps=d, no_push=True)
    assert d.seen["launch"] == ["014", "012"]


def test_dependency_cycle_leaves_both_skipped_and_is_logged(tmp_path):
    root = dep_root(tmp_path, {"012": "013.", "013": "012.", "014": "none."})
    d = deps(root)
    doc = ew_loop.tick(deps=d, no_push=True)
    assert d.seen["launch"] == ["014"]
    assert any("dependency cycle" in s for s in doc["log"])


def test_missing_doc_or_unknown_dependency_fails_open_with_a_step_line(tmp_path):
    root = dep_root(tmp_path, {"012": "077.", "014": "none."})  # 013 has no doc
    d = deps(root)
    doc = ew_loop.tick(deps=d, no_push=True)
    assert d.seen["launch"] == ["012", "013", "014"]
    assert any("012" in s and "077" in s and "no ROADMAP row" in s for s in doc["log"])
    assert any("013" in s and "no plan doc" in s for s in doc["log"])


# -- 4a blocked markers

def blocked_world(tmp_path, dispatched=T0 - 600):
    root, wt = git_world(tmp_path)
    (wt / "feature.txt").unlink()  # a clean worktree: the lane changed nothing
    items = ew_loop.Items(root)
    rec = items.get("012")
    rec["dispatched"] = ew_loop.iso(dispatched)
    items.put(rec)
    return root, wt


def marker(tree, updated, status="blocked", needs=("013",)):
    p = Path(tree) / "ops/loop/control/progress/p012-build.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    doc = {"task": "p012-build", "status": status, "updated": updated}
    if needs is not None:
        doc["needs"] = list(needs)
    p.write_text(json.dumps(doc))


def finish(root):
    d = deps(root, git=real_git, lane_state=lambda: ALL_RUNNING)
    doc = ew_loop.tick(deps=d, no_push=True)
    rec = ew_loop.Items(root).get("012")
    # plan 098: the review worker's steps land in the record, not the tick log
    doc["log"] = doc["log"] + rec.get("review_log", [])
    return rec, doc


def test_fresh_blocked_marker_sets_blocked_not_no_change(tmp_path):
    root, wt = blocked_world(tmp_path)
    marker(wt, ew_loop.iso(T0 - 60))
    rec, doc = finish(root)
    assert rec["state"] == "blocked" and rec["needs"] == ["013"] and rec["blocked_runs"] == 1
    assert "012: blocked on 013" in doc["log"]
    row = next(r for r in doc["checklist"] if r["id"] == "012")
    assert row["state"] == "blocked on 013"


def test_stale_marker_in_a_reused_worktree_is_plain_no_change(tmp_path):
    root, wt = blocked_world(tmp_path)
    marker(wt, ew_loop.iso(T0 - 7200))
    rec, doc = finish(root)
    assert rec["state"] == "no-change" and "blocked_runs" not in rec
    assert "012: stale blocked marker ignored" in doc["log"]


def test_worktree_marker_is_preferred_over_main_checkout(tmp_path):
    root, wt = blocked_world(tmp_path)
    marker(wt, ew_loop.iso(T0 - 60), needs=("013",))
    marker(root, ew_loop.iso(T0 - 60), needs=("014",))
    rec, _ = finish(root)
    assert rec["needs"] == ["013"]
    root2, _ = blocked_world(tmp_path / "b")
    marker(root2, ew_loop.iso(T0 - 60), needs=("014",))
    rec2, _ = finish(root2)
    assert rec2["state"] == "blocked" and rec2["needs"] == ["014"]


@pytest.mark.slow
def test_malformed_markers_are_plain_no_change_with_a_step_line(tmp_path):
    cases = [{"updated": ew_loop.iso(T0 - 60), "needs": None},
             {"updated": ew_loop.iso(T0 - 60), "needs": ("13", "abc")},
             {"updated": ew_loop.iso(T0 - 60), "needs": ()},
             {"updated": "yesterday", "needs": ("013",)}]
    for i, kw in enumerate(cases):
        root, wt = blocked_world(tmp_path / str(i))
        marker(wt, **kw)
        rec, doc = finish(root)
        assert rec["state"] == "no-change", kw
        assert any(s.startswith("012: malformed blocked marker") for s in doc["log"]), kw
    root, wt = blocked_world(tmp_path / "done")
    marker(wt, ew_loop.iso(T0 - 60), status="done")
    rec, doc = finish(root)
    assert rec["state"] == "no-change" and not any("marker" in s for s in doc["log"])


# -- 4b blocked dispatch

def test_blocked_dispatchable_only_once_needs_are_done():
    rec = {"state": "blocked", "needs": ["013"], "blocked_runs": 1}
    assert not ew_loop.dispatchable(rec, {"013"})
    assert ew_loop.dispatchable(rec, set())
    assert ew_loop.dispatchable(rec, {"099"})
    assert not ew_loop.dispatchable(dict(rec, blocked_runs=ew_loop.MAX_ATTEMPTS), set())
    assert "blocked" not in ew_loop.DONE_STATES


def test_blocked_record_waits_then_redispatches_after_need_flips(tmp_path):
    root = dep_root(tmp_path, {"012": "none."}, roadmap=DEP_ROADMAP.replace(
        "| 014 | Coupon check | [ ] open |\n", ""))
    ew_loop.Items(root).put({"id": "012", "kind": "plan", "state": "blocked", "needs": ["013"],
                             "blocked_runs": 1, "attempts": 1,
                             "title": "Grind spot recommender", "label": "l"})
    d = deps(root, lane_state=lambda: ALL_RUNNING[:1])
    doc = ew_loop.tick(deps=d, no_push=True)
    assert "012" not in d.seen["launch"]
    assert next(r for r in doc["checklist"] if r["id"] == "012")["state"] == "blocked on 013"
    rm = root / "docs/plans/ROADMAP.md"
    rm.write_text(ew_loop.flip_roadmap(rm.read_text(), "013", "done"), newline="\n")
    (ew_loop.Items(root).dir / "013.json").unlink()
    d = deps(root)
    ew_loop.tick(deps=d, no_push=True)
    assert d.seen["launch"] == ["012"]
    rec = ew_loop.Items(root).get("012")
    assert rec["state"] == "dispatched" and rec["blocked_runs"] == 1 and rec["attempts"] == 2


def test_blocked_runs_cap_becomes_gave_up(tmp_path):
    root = dep_root(tmp_path, {"012": "none."})
    ew_loop.Items(root).put({"id": "012", "kind": "plan", "state": "blocked", "needs": ["013"],
                             "blocked_runs": ew_loop.MAX_ATTEMPTS, "attempts": 2,
                             "title": "Grind spot recommender", "label": "l"})
    d = deps(root)
    doc = ew_loop.tick(deps=d, no_push=True)
    rec = ew_loop.Items(root).get("012")
    assert rec["state"] == "gave-up" and rec["error"] == "blocked 2 times on 013"
    assert "012" not in d.seen["launch"]
    assert next(r for r in doc["checklist"] if r["id"] == "012")["state"] == "gave-up"


# -- 4c re-arm

def rearm_world(tmp_path, dep_merged_at, dep_row_done=True, own_deps="013."):
    rm = DEP_ROADMAP.replace("| 014 | Coupon check | [ ] open |\n", "")
    if dep_row_done:
        rm = rm.replace("| 013 | Season pass tracker | [ ] open |",
                        "| 013 | Season pass tracker | [x] done |")
    root = dep_root(tmp_path, {"012": own_deps}, roadmap=rm)
    items = ew_loop.Items(root)
    items.put({"id": "012", "kind": "plan", "state": "no-change", "attempts": 1,
               "dispatched": ew_loop.iso(T0 - 3600), "title": "Grind spot recommender",
               "label": "l"})
    if dep_merged_at is not None:
        items.put({"id": "013", "kind": "plan", "state": "merged", "title": "t", "label": "l",
                   "merged_at": ew_loop.iso(dep_merged_at)})
    return root, items


def test_no_change_rearms_once_when_dependency_flipped_after_dispatch(tmp_path):
    root, items = rearm_world(tmp_path, T0 - 1800)
    d = deps(root)
    doc = ew_loop.tick(deps=d, no_push=True)
    assert d.seen["launch"] == ["012"]
    assert any("012: re-armed" in s for s in doc["log"])
    rec = items.get("012")
    assert rec["rearmed"] is True and rec["state"] == "dispatched"
    rec.update(state="no-change", pid=None)  # the re-run changed nothing either
    items.put(rec)
    ew_loop.tick(deps=d, no_push=True)
    assert d.seen["launch"].count("012") == 1 and items.get("012")["state"] == "no-change"


def test_no_change_with_dependencies_done_before_dispatch_stays(tmp_path):
    root, items = rearm_world(tmp_path, T0 - 7200)
    d = deps(root)
    for _ in range(3):
        ew_loop.tick(deps=d, no_push=True)
    rec = items.get("012")
    assert "012" not in d.seen["launch"] and rec["state"] == "no-change"
    assert rec["rearmed"] is False


def test_no_change_untouched_with_own_row_done_or_no_dependencies(tmp_path):
    root, items = rearm_world(tmp_path / "a", T0 - 1800, own_deps="none.")
    d = deps(root)
    ew_loop.tick(deps=d, no_push=True)
    assert "012" not in d.seen["launch"] and "rearmed" not in items.get("012")
    root, items = rearm_world(tmp_path / "b", T0 - 1800)
    rm = root / "docs/plans/ROADMAP.md"
    rm.write_text(ew_loop.flip_roadmap(rm.read_text(), "012", "done"), newline="\n")
    d = deps(root)
    ew_loop.tick(deps=d, no_push=True)
    assert "012" not in d.seen["launch"] and "rearmed" not in items.get("012")


def test_no_change_with_dependency_still_open_is_reconsidered_later(tmp_path):
    root, items = rearm_world(tmp_path, None, dep_row_done=False)
    d = deps(root)
    ew_loop.tick(deps=d, no_push=True)
    assert "rearmed" not in items.get("012") and d.seen["launch"] == ["013"]


@pytest.mark.slow
def test_merge_records_merged_at_only_when_it_flips_and_succeeds(tmp_path):
    root, wt = git_world(tmp_path)
    sp = FakeSpawn([{"rc": 0, "error": None, "result": "VERDICT: PASS"}])
    d = deps(root, spawn=sp, git=real_git, lane_state=lambda: ALL_RUNNING)
    ew_loop.tick(deps=d, no_push=True)
    rec = ew_loop.Items(root).get("012")
    assert rec["state"] == "merged" and rec["merged_at"] == ew_loop.iso(d.clock())
    root2 = _two_lane_world(tmp_path / "c", lambda w: (w / "f.txt").write_text("a\n"),
                            lambda w: (w / "f.txt").write_text("b\n"))
    states = []
    for iid in ("012", "013"):
        r = ew_loop.Items(root2).get(iid)
        states.append(r["state"])
        assert ("merged_at" in r) == (r["state"] == "merged")
    assert sorted(states) == ["merge-conflict", "merged"]


def _git_at(cwd, when, *args):
    env = dict(os.environ, GIT_COMMITTER_DATE=when, GIT_AUTHOR_DATE=when)
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
                        "-c", "core.hooksPath=/dev/null", *args], cwd=str(cwd),
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_flip_time_fallback_finds_the_no_ff_merge_commit(tmp_path):
    root = make_root(tmp_path, roadmap=DEP_ROADMAP, handoff="")
    day = "2026-10-0{}T10:00:00+00:00".format
    _git_at(root, day(1), "init", "-q", "-b", "main")
    _git_at(root, day(1), "add", "-A")
    _git_at(root, day(1), "commit", "-q", "-m", "add rows")
    _git_at(root, day(2), "checkout", "-q", "-b", "lane")
    rm = root / "docs/plans/ROADMAP.md"
    rm.write_text(ew_loop.flip_roadmap(rm.read_text(), "013", "done"), newline="\n")
    _git_at(root, day(2), "commit", "-q", "-am", "flip on lane")
    _git_at(root, day(2), "checkout", "-q", "main")
    (root / "other.txt").write_text("x\n")
    _git_at(root, day(3), "add", "-A")
    _git_at(root, day(3), "commit", "-q", "-m", "main moves")
    _git_at(root, day(4), "merge", "--no-ff", "-q", "-m", "merge", "lane")
    when = ew_loop.roadmap_flip_time(real_git, root, "013")
    assert when is not None
    assert _dt.datetime.fromisoformat(when) == _dt.datetime.fromisoformat(day(4))
    assert ew_loop.roadmap_flip_time(real_git, root, "012") is None


def test_current_roadmap_dispatches_only_ready_rows(tmp_path):
    # acceptance on the real ROADMAP + plan docs (read-only): every open row is
    # either ready (no open Depends-on row) or waiting on exactly its open ones
    d = deps(make_root(tmp_path), main_tree=ROOT)
    rows, work, skipped = ew_loop.Tick(d, dry_run=True).work_list()
    open_ids = {r["id"] for r in rows if r["open"]}
    for w in work:
        if w["kind"] == "plan":
            text = ew_loop.plan_doc(ROOT, w["id"]) or ""
            assert not set(ew_loop.plan_depends(text)) & open_ids, w["id"]
    for s in skipped:
        if s.get("reason"):
            want = [x for x in ew_loop.plan_depends(ew_loop.plan_doc(ROOT, s["id"]))
                    if x in open_ids]
            assert want and s["reason"] == "waits on " + ", ".join(want)
    plans = [w["id"] for w in work if w["kind"] == "plan"]
    waits = [s["id"] for s in skipped if s.get("reason")]
    assert sorted(plans + waits) == sorted(open_ids)


def test_rearm_falls_back_to_git_when_dependency_has_no_merged_at(tmp_path):
    root, items = rearm_world(tmp_path, None)
    calls = []

    def g(args, cwd, input=None):
        calls.append(list(args))
        out = ew_loop.iso(T0 - 1800) if args and args[0] == "log" else ""
        return subprocess.CompletedProcess(args, 0, out, "")

    d = deps(root, git=g)
    ew_loop.tick(deps=d, no_push=True)
    assert d.seen["launch"] == ["012"] and items.get("012")["rearmed"] is True
    assert any(a[0] == "log" and "--first-parent" in a for a in calls)


# ---------------------------------------------------------------- lane race (fix 2026-10-05)
# An item that finished ("ran") or was committed holds its unmerged work in its
# lane worktree; the kit claims the LOWEST non-RUNNING index and refuses a
# dirty worktree, so a dispatch onto it became lane-dirty and waited forever.

ONE_ROW = ROADMAP.replace("| 013 | Season pass tracker | [ ] open |\n", "")


def lane_wt(tmp_path, i):
    return tmp_path / "ew-worktrees" / f"lane-{i}"


def finishes_mid_tick(root, wt, rows, state="ran"):
    """lane_state that, like the 14:00:27 run, flips item 013 from
    dispatched to `state` between tick start and the dispatch step."""
    def lane_state():
        rec = ew_loop.Items(root).get("013")
        if rec.get("state") == "dispatched":
            ew_loop.Items(root).put(dict(rec, state=state, worktree=str(wt), rc=0))
        return rows
    return lane_state


def test_item_finishing_mid_tick_keeps_its_lane_busy(tmp_path):
    root = make_root(tmp_path, roadmap=ONE_ROW, handoff="")
    _dispatched(root, iid="013", lane="review", pid=7)
    rows = [{"index": 0, "state": "RUNNING", "lane": "build"},
            {"index": 1, "state": "RUNNING", "lane": "data"},
            {"index": 2, "state": "FREE", "lane": None}]
    for state in ("ran", "committed"):
        ew_loop.Items(root).put(dict(ew_loop.Items(root).get("013"), state="dispatched"))
        d = deps(root, lane_state=finishes_mid_tick(root, lane_wt(tmp_path, 2), rows, state))
        ew_loop.tick(deps=d, no_push=True)
        assert d.seen["launch"] == [], state
        assert ew_loop.Items(root).get("012") is None


def test_held_worktree_at_a_lower_index_blocks_dispatch(tmp_path):
    """The kit picks the lowest non-RUNNING index, not a lane name: a held
    index 0 under a free index 2 would still be claimed and refused."""
    root = make_root(tmp_path, roadmap=ONE_ROW, handoff="")
    _dispatched(root, iid="013", lane="build", pid=7)
    rows = [{"index": 0, "state": "FREE", "lane": None},
            {"index": 1, "state": "RUNNING", "lane": "data"},
            {"index": 2, "state": "FREE", "lane": None}]
    d = deps(root, lane_state=finishes_mid_tick(root, lane_wt(tmp_path, 0), rows))
    ew_loop.tick(deps=d, no_push=True)
    assert d.seen["launch"] == []


def dirty_lane_world(tmp_path, index=0):
    root = make_root(tmp_path, roadmap=ONE_ROW, handoff="")
    git(root, "init", "-q", "-b", "main")
    (root / ".gitignore").write_text("ops/\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    wt = lane_wt(tmp_path, index)
    git(root, "worktree", "add", "-q", "--detach", str(wt))
    (wt / "crash.txt").write_text("left by a crashed run\n")
    return root, wt


def test_physically_dirty_lowest_worktree_blocks_dispatch_until_clean(tmp_path):
    root, wt = dirty_lane_world(tmp_path)
    d = deps(root, git=real_git)
    ew_loop.tick(deps=d, no_push=True)
    assert d.seen["launch"] == []
    (wt / "crash.txt").unlink()
    ew_loop.tick(deps=d, no_push=True)
    assert d.seen["launch"] == ["012"]


def lane_dirty(root, wt, attempts=1):
    ew_loop.Items(root).put({
        "id": "012", "kind": "plan", "state": "lane-dirty", "lane": "review", "prompt": "p",
        "title": "Grind spot recommender", "label": "plan 012: Grind spot recommender",
        "attempts": attempts, "dispatched": ew_loop.iso(T0 - 600),
        "error": f"LaneRefused: lane worktree {wt} is dirty - resolve it by hand"})


def test_lane_dirty_recovers_once_its_worktree_is_clean_and_unheld(tmp_path):
    root, wt = dirty_lane_world(tmp_path, index=2)
    lane_dirty(root, wt)
    d = deps(root, git=real_git)
    ew_loop.tick(deps=d, no_push=True)  # still dirty: waits, no attempt spent
    rec = ew_loop.Items(root).get("012")
    assert rec["state"] == "lane-dirty" and "012" not in d.seen["launch"]
    # another item's unmerged work holds that worktree: still waits
    ew_loop.Items(root).put({"id": "013", "kind": "plan", "state": "committed",
                             "lane": "build", "worktree": str(wt), "title": "t",
                             "label": "l", "commit": "x"})
    (wt / "crash.txt").unlink()
    ew_loop.Tick(d, False, True).recover_lane_dirty()
    assert ew_loop.Items(root).get("012")["state"] == "lane-dirty"
    ew_loop.Items(root).put(dict(ew_loop.Items(root).get("013"), state="merged"))
    doc = ew_loop.tick(deps=d, no_push=True)
    rec = ew_loop.Items(root).get("012")
    assert d.seen["launch"].count("012") == 1 and rec["state"] == "dispatched"
    assert rec["attempts"] == 2  # bounded by MAX_ATTEMPTS like any retry
    assert any("012: lane-dirty cleared" in s for s in doc["log"])
    ew_loop.Tick(d, False, True).recover_lane_dirty()  # idempotent: no-op now
    assert ew_loop.Items(root).get("012")["state"] == "dispatched"


def test_lane_dirty_out_of_attempts_gives_up_with_the_error(tmp_path):
    root, wt = dirty_lane_world(tmp_path, index=2)
    (wt / "crash.txt").unlink()
    lane_dirty(root, wt, attempts=ew_loop.MAX_ATTEMPTS)
    d = deps(root, git=real_git)
    ew_loop.tick(deps=d, no_push=True)
    rec = ew_loop.Items(root).get("012")
    assert rec["state"] == "gave-up" and "dirty" in rec["error"]
    assert "012" not in d.seen["launch"]


def test_lane_dirty_without_a_worktree_in_its_error_stays(tmp_path):
    root = make_root(tmp_path, roadmap=ONE_ROW, handoff="")
    lane_dirty(root, "x")
    ew_loop.Items(root).put(dict(ew_loop.Items(root).get("012"), error="LaneRefused: dirty"))
    d = deps(root)
    ew_loop.tick(deps=d, no_push=True)
    assert ew_loop.Items(root).get("012")["state"] == "lane-dirty"
    assert "012" not in d.seen["launch"]


# ---------------------------------------------------------------- plan 058: resolve lanes

def _conflict_world(tmp_path):
    """012 merges, 013 conflicts on f.txt (finished() runs ids in order)."""
    root = _two_lane_world(tmp_path, lambda wt: (wt / "f.txt").write_text("a\n"),
                           lambda wt: (wt / "f.txt").write_text("b\n"))
    return root, ew_loop.Items(root)


def _free(root, tmp_path, **over):
    return deps(root, git=real_git, lane_worktree=lambda i: tmp_path / "none" / str(i),
                **over)


def keep_refs(root):
    return git(root, "for-each-ref", "--format=%(refname) %(objectname)", "refs/ew/keep")


@pytest.mark.slow
def test_conflict_keeps_the_lane_commit_under_a_ref(tmp_path):
    root, items = _conflict_world(tmp_path)
    rec = items.get("013")
    assert rec["state"] == "merge-conflict" and rec["keep_ref"] == "refs/ew/keep/013"
    assert keep_refs(root) == f"refs/ew/keep/013 {rec['commit']}"
    assert items.get("012")["state"] == "merged"


@pytest.mark.slow
def test_roadmap_only_conflict_never_keeps_a_ref(tmp_path):
    def a(wt):
        (wt / "a.txt").write_text("a\n")
        _flip_in(wt, "012")

    def b(wt):
        (wt / "b.txt").write_text("b\n")
        _flip_in(wt, "013")

    root = _two_lane_world(tmp_path, a, b)
    assert keep_refs(root) == ""
    d = _free(root, tmp_path)
    ew_loop.tick(deps=d, no_push=True)
    assert not {"012", "013"} & set(d.seen["launch"])  # idle deep-dive only
    assert all(r.get("kind") != "resolve" for r in ew_loop.Items(root).all().values())


@pytest.mark.slow
def test_next_tick_dispatches_a_resolve_lane_first(tmp_path):
    root, items = _conflict_world(tmp_path)
    rm = root / "docs/plans/ROADMAP.md"
    rm.write_text(rm.read_text() + "| 014 | Other | [ ] open |\n", newline="\n")
    git(root, "commit", "-q", "-am", "row 014")
    d = _free(root, tmp_path)
    ew_loop.tick(deps=d, no_push=True)
    assert d.seen["launch"][:2] == ["013", "014"] and d.seen["launch"].count("013") == 1
    rec = items.get("013")
    assert rec["kind"] == "resolve" and rec["base_kind"] == "plan"
    assert rec["resolve_runs"] == 1 and rec["state"] == "dispatched"
    assert rec["keep_ref"] == "refs/ew/keep/013" and rec["attempts"] == 1
    assert "git merge --no-ff --no-commit refs/ew/keep/013" in rec["prompt"]
    assert "BOTH" in rec["prompt"] and "As-built deviations" in rec["prompt"]
    assert "Do NOT edit docs/plans/ROADMAP.md" in rec["prompt"]
    row = [r for r in progress(root)["checklist"] if r["id"] == "013"][0]
    assert row["state"] == "resolving, run 1/2"


def _run_resolve(root, tmp_path, items, name, content):
    """The lane worker for a dispatched resolve item; the fake lane resolves
    f.txt to `content`. Returns the merge state the lane saw."""
    wt = tmp_path / "ew-worktrees" / name
    git(root, "worktree", "add", "-q", "--detach", str(wt), "main")
    saw = {}

    def lane(kw):
        cwd = Path(kw["cwd"])
        saw["merging"] = real_git(["rev-parse", "-q", "--verify", "MERGE_HEAD"],
                                  cwd).returncode == 0
        saw["markers"] = "<<<<<<<" in (cwd / "f.txt").read_text()
        (cwd / "f.txt").write_text(content)
        return {"rc": 0, "error": None, "result": "resolved"}

    sp = FakeSpawn([lane])

    def run_lane(lane_name, prompt, spawn=None, root=None, **kw):
        line = spawn(root, "EW", prompt, cwd=wt)
        return dict(line, worktree=str(wt))

    assert ew_loop.lane_worker("013", deps=deps(root, spawn=sp, git=real_git),
                               run_lane=run_lane) == 0
    assert items.get("013")["state"] == "ran"
    return saw


def _review(root, verdicts=1):
    sp = FakeSpawn([{"rc": 0, "error": None, "result": "VERDICT: PASS"}] * verdicts)
    return deps(root, spawn=sp, git=real_git,
                lane_state=lambda: [{"index": i, "state": "RUNNING", "lane": n}
                                    for i, n in enumerate(("build", "data", "review"))])


@pytest.mark.slow
def test_resolve_lane_merges_through_the_normal_path_and_drops_the_ref(tmp_path):
    root, items = _conflict_world(tmp_path)
    kept = items.get("013")["commit"]
    ew_loop.tick(deps=_free(root, tmp_path), no_push=True)
    saw = _run_resolve(root, tmp_path, items, "lane-2", "a\nb\n")
    assert saw == {"merging": True, "markers": True}
    ew_loop.tick(deps=_review(root), no_push=True)
    rec = items.get("013")
    assert rec["state"] == "merged" and rec["verdict"] == "PASS"
    assert "keep_ref" not in rec and keep_refs(root) == ""
    assert (root / "f.txt").read_text() == "a\nb\n"
    assert git(root, "merge-base", "--is-ancestor", kept, "main") == ""
    assert git(root, "log", "-1", "--format=%s", "main") == "merge lane-2: plan 013: t"
    assert "| 013 | Season pass tracker | [x] done" in (root / "docs/plans/ROADMAP.md").read_text()
    assert git(root, "status", "--porcelain") == ""


@pytest.mark.slow
def test_second_conflict_reenters_resolve_then_caps(tmp_path):
    root, items = _conflict_world(tmp_path)
    ew_loop.tick(deps=_free(root, tmp_path), no_push=True)
    _run_resolve(root, tmp_path, items, "lane-2", "a\nb\n")
    (root / "f.txt").write_text("c\n")  # main moves on under the resolve lane
    git(root, "commit", "-q", "-am", "main moves")
    ew_loop.tick(deps=_review(root), no_push=True)
    rec = items.get("013")
    assert rec["state"] == "merge-conflict" and rec["resolve_runs"] == 1
    assert keep_refs(root) == f"refs/ew/keep/013 {rec['commit']}"
    d = _free(root, tmp_path)
    ew_loop.tick(deps=d, no_push=True)
    assert d.seen["launch"] == ["013"] and items.get("013")["resolve_runs"] == 2
    items.put(dict(items.get("013"), state="merge-conflict"))
    d = _free(root, tmp_path)
    ew_loop.tick(deps=d, no_push=True)
    assert "013" not in d.seen["launch"] and items.get("013")["state"] == "merge-conflict"
    row = [r for r in progress(root)["checklist"] if r["id"] == "013"][0]
    assert row["state"] == "merge-conflict"


@pytest.mark.slow
def test_lost_resolve_lane_is_redispatched_as_resolve(tmp_path):
    root, items = _conflict_world(tmp_path)
    ew_loop.tick(deps=_free(root, tmp_path), no_push=True)
    items.put(dict(items.get("013"), state="lost"))
    d = _free(root, tmp_path)
    ew_loop.tick(deps=d, no_push=True)
    rec = items.get("013")
    assert d.seen["launch"] == ["013"] and rec["kind"] == "resolve"
    assert rec["attempts"] == 2 and "refs/ew/keep/013" in rec["prompt"]


@pytest.mark.slow
def test_hand_merged_conflict_settles_as_merged_and_drops_the_ref(tmp_path):
    root, items = _conflict_world(tmp_path)
    git(root, "merge", "-q", "--no-ff", "-X", "theirs", "-m", "by hand",
        items.get("013")["commit"])
    d = _free(root, tmp_path)
    ew_loop.tick(deps=d, no_push=True)
    assert items.get("013")["state"] == "merged" and keep_refs(root) == ""
    assert "013" not in d.seen["launch"]


@pytest.mark.slow
def test_legacy_conflict_without_a_ref_gets_one(tmp_path):
    root, items = _conflict_world(tmp_path)
    rec = items.get("013")
    git(root, "update-ref", "-d", "refs/ew/keep/013")
    rec.pop("keep_ref")
    items.put(rec)
    d = _free(root, tmp_path)
    ew_loop.tick(deps=d, no_push=True)
    assert keep_refs(root) == f"refs/ew/keep/013 {rec['commit']}"
    assert d.seen["launch"] == ["013"] and items.get("013")["kind"] == "resolve"


def test_resolve_lane_with_conflict_markers_left_fails_review(tmp_path):
    root, wt = git_world(tmp_path)
    (wt / "feature.txt").write_text("<<<<<<< HEAD\nx\n=======\ny\n>>>>>>> abc\n")
    t = ew_loop.Tick(deps(root, git=real_git))
    rec = {"id": "012", "kind": "resolve", "base_kind": "plan"}
    out = t.extra_checks(rec, wt)
    assert len(out) == 1 and "feature.txt" in out[0] and "conflict marker" in out[0]
    (wt / "feature.txt").write_text("x\ny\n")
    assert t.extra_checks(rec, wt) == []


def test_paused_resolve_gives_its_run_back(tmp_path):
    root = make_root(tmp_path)
    items = _dispatched(root, kind="resolve", base_kind="plan", resolve_runs=1,
                        keep_ref="refs/ew/keep/012")
    (root / "ops/loop/control").mkdir(parents=True, exist_ok=True)
    (root / "ops/loop/control/HALT").write_text("")
    assert ew_loop.lane_worker("012", deps=deps(root)) == 0
    rec = items.get("012")
    assert rec["state"] == "paused" and rec["resolve_runs"] == 0


# ---------------------------------------------------------------- fix-loop-stall: refused commits

def _refusing_git(wt, refuse):
    """real_git, but `commit` in the lane worktree is refused (a pre-commit hook)
    while refuse["on"] holds."""
    def g(args, cwd, input=None):
        if args[:1] == ["commit"] and Path(cwd) == Path(wt) and refuse["on"]:
            return subprocess.CompletedProcess(args, 1, "", "[leak-sweep] HALT t.py:1 drive-path")
        return real_git(args, cwd, input=input)
    return g


def _passing(n=1):
    return FakeSpawn([{"rc": 0, "error": None, "result": "VERDICT: PASS"}] * n)


def test_refused_lane_commit_is_salvaged_and_merged_in_main(tmp_path):
    root, wt = git_world(tmp_path)
    d = deps(root, spawn=_passing(), git=_refusing_git(wt, {"on": True}),
             lane_state=lambda: [{"index": i, "state": "RUNNING", "lane": n}
                                 for i, n in enumerate(("build", "data", "review"))])
    ew_loop.tick(deps=d, no_push=True)
    rec = ew_loop.Items(root).get("012")
    assert rec["state"] == "merged" and "leak-sweep" in rec["error"]
    assert (root / "feature.txt").exists() and keep_refs(root) == ""
    assert git(wt, "status", "--porcelain") == ""  # lane index usable again
    assert "refute-rounds: 0/3" in git(root, "log", "-1", "--format=%B", rec["commit"])
    assert "| 012 | Grind spot recommender | [x] done" in (
        root / "docs/plans/ROADMAP.md").read_text()


def test_main_hook_refused_merge_is_parked_without_resolve_runs(tmp_path):
    # a real leak main's hook refuses no longer burns MAX_ATTEMPTS blind resolve
    # runs: the merge is clean, so a resolve run could only be refused again
    root, wt = git_world(tmp_path)

    def g(args, cwd, input=None):
        if args[:1] == ["commit"] and Path(cwd) == Path(root):
            return subprocess.CompletedProcess(args, 1, "", "[leak-sweep] HALT t.py:1 email")
        return real_git(args, cwd, input=input)
    lanes = lambda: [{"index": i, "state": "RUNNING", "lane": n}  # noqa: E731
                     for i, n in enumerate(("build", "data", "review"))]
    d = deps(root, spawn=_passing(), git=g, lane_state=lanes)
    ew_loop.tick(deps=d, no_push=True)
    rec = ew_loop.Items(root).get("012")
    assert rec["state"] == "merge-refused" and "leak-sweep" in rec["error"]
    assert keep_refs(root) == f"refs/ew/keep/012 {rec['commit']}"
    assert not (root / "feature.txt").exists()  # never merged
    assert git(root, "status", "--porcelain") == ""  # merge aborted
    assert not ew_loop.dispatchable(rec)
    d = deps(root, spawn=_passing(), git=g, lane_state=lanes)
    ew_loop.tick(deps=d, no_push=True)
    assert "012" not in d.seen["launch"]
    assert ew_loop.Items(root).get("012")["state"] == "merge-refused"


@pytest.mark.slow
def test_pre_fix_failed_dirty_record_is_retried(tmp_path):
    # backfill: the 031 shape - failed-dirty, verdict PASS, work staged, no counters
    root, wt = git_world(tmp_path)
    git(wt, "add", "-A")
    rec = ew_loop.Items(root).get("012")
    rec.update(state="failed-dirty", verdict="PASS", error=None)
    ew_loop.Items(root).put(rec)
    d = deps(root, git=real_git, lane_state=lambda: [
        {"index": i, "state": "RUNNING", "lane": n}
        for i, n in enumerate(("build", "data", "review"))])
    ew_loop.tick(deps=d, no_push=True)
    rec = ew_loop.Items(root).get("012")
    assert rec["state"] == "merged" and (root / "feature.txt").exists()
    assert "refute-rounds: 0/3" in git(root, "log", "-1", "--format=%B", rec["commit"])


@pytest.mark.slow
def test_refused_commit_of_red_work_is_kept_unmerged_and_frees_the_lane(tmp_path):
    root, wt = git_world(tmp_path)
    d = deps(root, git=_refusing_git(wt, {"on": True}), gates=lambda cwd: (False, "red"),
             lane_state=lambda: [{"index": i, "state": "RUNNING", "lane": n}
                                 for i, n in enumerate(("build", "data", "review"))])
    ew_loop.tick(deps=d, no_push=True)
    rec = ew_loop.Items(root).get("012")
    assert rec["state"] == "failed" and rec["keep_ref"] == "refs/ew/keep/012"
    assert keep_refs(root) == f"refs/ew/keep/012 {rec['commit']}"
    assert git(wt, "status", "--porcelain") == ""  # lane index usable again
    assert git(root, "show", f"{rec['commit']}:feature.txt") == "work"
    assert "commit refused" in git(root, "log", "-1", "--format=%B", rec["commit"])
    assert not (root / "feature.txt").exists()  # never merged
    ew_loop.tick(deps=d, no_push=True)  # idempotent
    assert ew_loop.Items(root).get("012")["state"] == "failed"


@pytest.mark.slow
def test_dirty_failed_dirty_lane_zero_no_longer_wedges_dispatch(tmp_path):
    # the stall: lane-0 held a failed-dirty item's staged work, the kit claims
    # the lowest index, so free_lanes() gave 0 slots and every tick read idle
    root, wt = dirty_lane_world(tmp_path, index=0)
    git(wt, "add", "-A")
    items = ew_loop.Items(root)
    items.put({"id": "099", "kind": "plan", "state": "failed-dirty", "verdict": "PASS",
               "title": "t", "label": "plan 099: t", "lane": "build", "rc": 0,
               "worktree": str(wt), "rounds": 0})
    d = deps(root, git=real_git, lane_worktree=lambda i: lane_wt(tmp_path, i))
    ew_loop.tick(deps=d, no_push=True)
    assert items.get("099")["state"] == "merged"
    assert d.seen["launch"] == ["012"]


def test_contradicted_data_rows_become_work_items(tmp_path):
    """Plan 085: the server's runtime verdicts list contradicted rows; each is a
    data item a lane works (tracked data is only ever edited by a lane)."""
    root = dep_root(tmp_path, {"013": "none."})
    ok = {"verdict": "confirmed", "evidence": "fine", "date": "2026-10-08", "url": "u"}
    bad = {"verdict": "contradicted", "evidence": 'Adventure "Blessing" 15% to 30%.',
           "date": "2026-10-08", "url": "https://example.invalid/d?no=1"}
    p = root.joinpath(*ew_loop.VERDICTS_REL)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"verdicts": {"a.json#x": ok, "xp_buffs.json#adventure-blessing": bad,
                                          "../evil#x": bad}}), newline="\n")
    items = ew_loop.data_items(root)
    assert len(items) == 1 and items[0]["id"].startswith("D") and items[0]["skip"] is None
    assert "server/ew/data/xp_buffs.json" in items[0]["text"]
    assert "'Blessing'" in items[0]["text"]
    assert items == ew_loop.data_items(root), "stable id across ticks"
    rows, work, skipped = ew_loop.Tick(deps(root)).work_list()
    assert [w["id"] for w in work][-1] == items[0]["id"]
    assert work[-1]["kind"] == "handoff" and work[-1]["text"] == items[0]["text"]
    p.write_text("junk", newline="\n")
    assert ew_loop.data_items(root) == []


# ---------------------------------------------------------------- plan 091

def test_session_paths_names_protected_targets():
    sp = ew_loop.session_paths
    assert sp("o-from-MAIN-ORDER-to-EW-x.md", "add dependabot\n") == []
    assert sp("n.md", "vendor into ops/fleet_kit/ and re-pin") == ["ops/fleet_kit/"]
    assert sp("n.md", "edit ops\\fleet_kit\\MANIFEST.json") == ["ops/fleet_kit/"]
    assert sp("2026-FLEET-KIT-v10-ORDER.md", "ship it") == ["ops/fleet_kit/"]
    assert sp("n.md", "wire .claude/settings.json and re-embed CLAUDE.md") == \
        [".claude/", "CLAUDE.md"]
    assert sp("n.md", "the app/.claudex dir and NOTCLAUDE.mdx") == []


KIT_ORDER = "k-from-MAIN-ORDER-to-EW-kit-v10.md"


def _session_order(tmp_path):
    root, inbox, outbox = _inbox_root(tmp_path, roadmap=ROADMAP)
    sp = FakeSpawn([{"rc": 0, "error": None, "result": "# From EW - ANSWER\nvendored"}])
    d = deps(root, spawn=sp)
    ew_loop.tick(deps=d, no_push=True)  # baseline
    (inbox / KIT_ORDER).write_text("# From MAIN - ORDER\nvendor kit into ops/fleet_kit/\n"
                                   "wire the hook in .claude/settings.json\n")
    return root, outbox, sp, d


def test_session_order_is_not_dispatched_and_shows_in_checklist(tmp_path):
    root, outbox, sp, d = _session_order(tmp_path)
    doc = ew_loop.tick(deps=d, no_push=True)
    oid = ew_loop.order_id(KIT_ORDER)
    assert oid not in d.seen["launch"] and ew_loop.Items(root).get(oid) is None
    _, work, skipped = ew_loop.Tick(d).work_list()
    assert oid not in [w["id"] for w in work]
    hit = [h for h in skipped if h["id"] == oid]
    assert hit and hit[0]["skip"] == "session"
    row = [r for r in doc["checklist"] if r["id"] == oid]
    assert row and row[0]["state"].startswith("session: needs ops/fleet_kit/")
    assert not list(outbox.iterdir())


def test_session_order_waits_past_adjudicate_then_answers_after_session_done(tmp_path):
    root, outbox, sp, d = _session_order(tmp_path)
    ew_loop.tick(deps=d, no_push=True)
    oid = ew_loop.order_id(KIT_ORDER)
    ew_loop.Items(root).put({"id": oid, "kind": "order", "state": "adjudicate",
                             "commit": "wip0", "rounds": 3, "title": KIT_ORDER})
    ew_loop.tick(deps=d, no_push=True)
    assert not list(outbox.iterdir())  # a partial lane run does not close it
    assert [o["id"] for o in ew_loop.session_orders(root)] == [oid]
    rec = ew_loop.session_done(root, oid, "abc1234", clock=lambda: 1_790_000_000.0)
    assert rec["state"] == "merged" and rec["session_done"] and rec["commit"] == "abc1234"
    assert ew_loop.session_orders(root) == []
    again = ew_loop.session_done(root, oid, "other", clock=lambda: 1_790_000_100.0)
    assert again["commit"] == "abc1234"  # idempotent: the first record stands
    ew_loop.tick(deps=d, no_push=True)
    calls = [c for c in sp.calls if c.get("note") == KIT_ORDER]
    assert len(calls) == 1 and "abc1234" in calls[0]["prompt"]
    assert len(list(outbox.glob("*-re-k-from-MAIN-ORDER-to-EW-kit-v10.md"))) == 1
    ew_loop.tick(deps=d, no_push=True)
    assert len([c for c in sp.calls if c.get("note") == KIT_ORDER]) == 1


def test_session_cli_lists_and_refuses_unknown_id(tmp_path, monkeypatch, capsys):
    root, outbox, sp, d = _session_order(tmp_path)
    ew_loop.tick(deps=d, no_push=True)
    monkeypatch.setattr(ew_loop, "ROOT", root)
    oid = ew_loop.order_id(KIT_ORDER)
    assert ew_loop.main(["session"]) == 0
    out = capsys.readouterr().out
    assert out.startswith(oid + " " + KIT_ORDER) and "needs: ops/fleet_kit/, .claude/" in out
    assert ew_loop.main(["session-done", "Nffffff"]) == 2
    assert ew_loop.Items(root).get("Nffffff") is None
    assert ew_loop.main(["session-done", oid, "--commit", "f00d"]) == 0
    assert ew_loop.Items(root).get(oid)["commit"] == "f00d"
    capsys.readouterr()
    assert ew_loop.main(["session"]) == 0
    assert capsys.readouterr().out.strip() == "none"


# ---------------------------------------------------------------- perf audit 2.10: hooksPath

def _cp(rc, out=""):
    import subprocess
    return subprocess.CompletedProcess(["git"], rc, out, "")


def test_hooks_path_check_ok_when_inside_repo(tmp_path):
    (tmp_path / ".githooks").mkdir()
    git = lambda args, cwd, input=None: _cp(0, ".githooks\n")  # noqa: E731
    assert ew_loop.hooks_path_problem(tmp_path, git) is None


def test_hooks_path_check_flags_missing_outside_or_unset(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (tmp_path / "elsewhere").mkdir()
    gone = lambda args, cwd, input=None: _cp(0, ".githooks\n")  # noqa: E731
    assert "does not exist" in ew_loop.hooks_path_problem(root, gone)
    outside = lambda args, cwd, input=None: _cp(0, str(tmp_path / "elsewhere") + "\n")  # noqa: E731
    assert "outside" in ew_loop.hooks_path_problem(root, outside)
    unset = lambda args, cwd, input=None: _cp(1, "")  # noqa: E731
    assert "unset" in ew_loop.hooks_path_problem(root, unset)


def test_tick_logs_a_bad_hooks_path_without_failing(tmp_path):
    root = make_root(tmp_path)

    def git(args, cwd, input=None):
        if args[:2] == ["config", "core.hooksPath"]:
            return _cp(0, "gone-hooks\n")
        return real_git(args, cwd, input)

    doc = ew_loop.tick(deps=deps(root, git=git), no_push=True)
    assert doc.get("state") != "busy"
    assert any("hooksPath" in line for line in progress(root)["log"])


# ---------------------------------------------------------------- plan 098: detached review worker

def _recorder(seen, key, pid=5150):
    def launch(*args):
        seen.setdefault(key, []).append(args[0] if args else None)
        return pid
    return launch


def _all_running():
    return [{"index": i, "state": "RUNNING", "lane": n}
            for i, n in enumerate(("build", "data", "review"))]


def test_tick_launches_a_review_worker_and_runs_no_gate_or_spawn(tmp_path):
    root, wt = git_world(tmp_path)
    seen, gated = {}, []
    sp = FakeSpawn()
    d = deps(root, spawn=sp, git=real_git, lane_state=_all_running,
             gates=lambda cwd: gated.append(cwd) or (True, "green"),
             launch_review=_recorder(seen, "review"))
    doc = ew_loop.tick(deps=d, no_push=True)
    assert seen["review"] == ["012"] and gated == [] and sp.calls == []
    rec = ew_loop.Items(root).get("012")
    assert rec["state"] == "ran" and rec["review_launch"]
    assert any(r["id"] == "012" and r["state"] == "reviewing" for r in doc["checklist"])
    assert isinstance(doc["tick_s"], (int, float))
    ew_loop.tick(deps=d, no_push=True)  # worker in flight (startup grace): no relaunch
    assert seen["review"] == ["012"]


def test_two_finished_items_are_reviewed_in_parallel(tmp_path):
    root, wt = git_world(tmp_path)
    ew_loop.Items(root).put({"id": "013", "kind": "plan", "title": "t", "label": "plan 013: t",
                             "state": "committed", "worktree": str(wt), "commit": "abc",
                             "lane": "data", "rounds": 0})
    seen = {}
    d = deps(root, git=real_git, lane_state=_all_running, launch_review=_recorder(seen, "review"))
    ew_loop.tick(deps=d, no_push=True)
    assert sorted(seen["review"]) == ["012", "013"]


def test_ran_item_waits_while_spawning_is_blocked(tmp_path):
    root, wt = git_world(tmp_path)
    ew_loop.backoff_hit(root, 1_790_000_000.0, "usage limit")
    seen = {}
    d = deps(root, git=real_git, lane_state=_all_running, launch_review=_recorder(seen, "review"))
    ew_loop.tick(deps=d, no_push=True)
    assert "review" not in seen and "review_launch" not in ew_loop.Items(root).get("012")


def test_dead_review_worker_is_relaunched_then_parked_at_the_cap(tmp_path):
    root, wt = git_world(tmp_path)
    items = ew_loop.Items(root)
    rec = items.get("012")
    rec.update(review_launch=ew_loop.iso(1_790_000_000.0), worker_pid=999)
    items.put(rec)
    seen = {}
    d = deps(root, git=real_git, lane_state=_all_running, pid_alive=lambda pid: pid != 999,
             launch_review=_recorder(seen, "review"))
    ew_loop.tick(deps=d, no_push=True)
    rec = items.get("012")
    assert seen["review"] == ["012"] and rec["review_crashes"] == 1
    assert "worker_pid" not in rec
    rec["worker_pid"] = 999  # the relaunched worker died too
    items.put(rec)
    ew_loop.tick(deps=d, no_push=True)
    rec = items.get("012")
    assert seen["review"] == ["012"] and rec["review_crashes"] == ew_loop.MAX_ATTEMPTS
    # parked: retry_refused salvages the dirty worktree as an unmerged WIP
    assert rec["state"] in ("failed-dirty", "failed") and "review worker" in rec["error"]
    assert "review_launch" not in rec


def test_review_worker_that_never_started_counts_as_a_crash(tmp_path):
    root, wt = git_world(tmp_path)
    items = ew_loop.Items(root)
    rec = items.get("012")
    rec["review_launch"] = ew_loop.iso(1_790_000_000.0 - ew_loop.REVIEW_START_S - 1)
    items.put(rec)
    seen = {}
    d = deps(root, git=real_git, lane_state=_all_running, launch_review=_recorder(seen, "review"))
    ew_loop.tick(deps=d, no_push=True)
    assert seen["review"] == ["012"] and items.get("012")["review_crashes"] == 1


def test_review_worker_processes_its_item_and_clears_its_markers(tmp_path):
    root, wt = git_world(tmp_path)
    sp = FakeSpawn([{"rc": 0, "error": None, "result": "VERDICT: PASS"}])
    d = deps(root, spawn=sp, git=real_git, lane_state=_all_running)
    assert ew_loop.review_worker("012", deps=d) == 0
    rec = ew_loop.Items(root).get("012")
    assert rec["state"] == "merged" and "worker_pid" not in rec and "review_launch" not in rec
    assert any("merged" in line for line in rec["review_log"])
    assert "loop-review" in d.seen["record"]
    assert ew_loop.review_worker("012", deps=d) == 0  # not ran / committed: no-op
    assert len(sp.calls) == 1


def test_review_worker_refuses_an_item_another_live_worker_holds(tmp_path):
    root, wt = git_world(tmp_path)
    items = ew_loop.Items(root)
    rec = items.get("012")
    rec["worker_pid"] = os.getpid() + 1
    items.put(rec)
    sp = FakeSpawn()
    d = deps(root, spawn=sp, git=real_git, pid_alive=lambda pid: True)
    assert ew_loop.review_worker("012", deps=d) == 0
    assert sp.calls == [] and items.get("012")["worker_pid"] == os.getpid() + 1


def test_busy_merge_lock_defers_the_merge(tmp_path):
    root, wt = git_world(tmp_path)
    sp = FakeSpawn([{"rc": 0, "error": None, "result": "VERDICT: PASS"}])
    real_lock = ew_loop._load_watch().watch_lock

    @contextlib.contextmanager
    def lock(path):
        if Path(path).name == "merge.lock":
            raise d.watch.LockBusy("held")
        with real_lock(path):
            yield

    d = deps(root, spawn=sp, git=real_git, lane_state=_all_running, lock=lock)
    ew_loop.tick(deps=d, no_push=True)
    rec = ew_loop.Items(root).get("012")
    assert rec["state"] == "committed" and not (root / "feature.txt").exists()
    d.lock = real_lock
    ew_loop.tick(deps=d, no_push=True)
    assert ew_loop.Items(root).get("012")["state"] == "merged"


def test_tick_launches_one_push_worker_when_main_is_ahead(tmp_path):
    root = make_root(tmp_path, roadmap="", handoff="")
    seen = {}
    gated = []
    d = deps(root, git=FakeGit(), launch_push=_recorder(seen, "push"),
             gates=lambda cwd: gated.append(cwd) or (True, "green"))
    ew_loop.tick(deps=d)
    assert seen["push"] == [None] and gated == []  # the tick never gates main
    ew_loop.tick(deps=d)  # launched, not started yet: no second worker
    assert seen["push"] == [None]
    for kw, g in (({"no_push": True}, FakeGit()), ({}, FakeGit(ahead="0"))):
        (root / ew_loop.PUSH_WORKER_REL).unlink(missing_ok=True)
        seen.clear()
        ew_loop.tick(deps=deps(root, git=g, launch_push=_recorder(seen, "push")), **kw)
        assert "push" not in seen


def test_launch_review_and_push_argv(tmp_path):
    argvs = []

    class P:
        pid = 9

    def popen(argv, creationflags=0, **kw):
        argvs.append(argv)
        return P()

    assert ew_loop._launch(tmp_path, "012", popen=popen, cmd="review") == 9
    assert ew_loop._launch(tmp_path, None, popen=popen, cmd="push") == 9
    assert argvs[0][-2:] == ["review", "012"] and argvs[1][-1] == "push"
