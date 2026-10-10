"""Plan 101 (MAIN 2246 sec 2.9): headless lanes work inline - no lane prompt
invites background sub-agents, every GATES-carrying prompt says so."""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import ew_loop  # noqa: E402

INLINE = "Work inline in this run: do not start background sub-agents"
ITEM = {"id": "012", "title": "t", "text": "x", "label": "plan 012",
        "keep_ref": "abc123", "base_kind": "plan"}


def _prompts():
    return {
        "plan": ew_loop.plan_prompt(ITEM),
        "handoff": ew_loop.handoff_prompt(ITEM),
        "fix": ew_loop.fix_prompt(ITEM, 1, ["1. x"]),
        "resolve": ew_loop.resolve_prompt(ITEM),
        "verify": ew_loop.verify_prompt(ITEM, 1),
    }


def test_gates_drops_the_scratchpad_clause_and_says_work_inline():
    g = ew_loop.GATES.format(task="p012-build")
    assert "Background agents share one scratchpad" not in g
    assert INLINE in g
    assert "run each step in the foreground" in g
    assert "(p012-build_*.py)" in g
    assert "never a generic name like prog.py" in g


def test_every_gates_prompt_carries_the_inline_instruction():
    p = _prompts()
    for name in ("plan", "handoff", "fix", "resolve"):
        assert INLINE in p[name], name


def test_no_headless_prompt_invites_background_agents():
    bad = re.compile(r"(?i)background agents share|use (a |the )?background|"
                     r"spawn (a |the )?(background )?sub-?agent|Agent tool")
    for name, text in _prompts().items():
        assert not bad.search(text), name
