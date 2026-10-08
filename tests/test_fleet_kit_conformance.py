"""Fleet kit v10 is vendored byte-for-byte and the CLAUDE.md FLEET-COMMON block is
byte-identical. Never edit the kit locally; MAIN ships new versions."""

import hashlib
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KIT = ROOT / "ops" / "fleet_kit" / "fleet_headless.py"
SLOTS_SHA256 = "290cbf80ce6989e15ad778be9032503733c8820030bfa6a9b27439b29d70486e"


def _load():
    spec = importlib.util.spec_from_file_location("fleet_headless", KIT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_fleet_kit_conformance():
    assert KIT.is_file()
    assert _load().conformance(ROOT) == []


def test_kit_is_v10_with_all_files():
    import json
    man = json.loads((ROOT / "ops" / "fleet_kit" / "MANIFEST.json").read_text("ascii"))
    assert man["version"] == 10
    assert _load().KIT_VERSION == 10
    assert {"cli_display.json", "fleet_checklist.py", "fleet_done.py", "fleet_headless.py",
            "fleet_inbox.py", "fleet_lanes.py", "fleet_secrets.py", "fleet_statusline.js",
            "fleet_subagent_first.py", "fleet_subagent_status.js", "fleet_watch.py", "tokens.css", "tokens.json",
            "FLEET-COMMON.md", "LICENSE", "NOTICE"} == set(man["files"])
    on_disk = {p.name for p in (ROOT / "ops" / "fleet_kit").iterdir() if p.is_file()}
    assert on_disk == set(man["files"]) | {"MANIFEST.json"}


def _settings_files():
    return sorted((ROOT / ".claude").glob("settings*.json"))


def _keys(node):
    if isinstance(node, dict):
        for k, v in node.items():
            yield k
            yield from _keys(v)
    elif isinstance(node, list):
        for v in node:
            yield from _keys(v)


def test_no_tree_level_display_keys():
    """Kit v9 item 15: display keys live only in the account settings."""
    import json
    forbidden = set(json.loads((ROOT / "ops" / "fleet_kit" / "cli_display.json")
                               .read_text("ascii"))["tree_forbidden"])
    for f in _settings_files():
        found = forbidden & set(_keys(json.loads(f.read_text("utf-8"))))
        assert not found, f"{f.name}: {sorted(found)}"


def test_stop_hook_runs_kit_done_with_relative_path():
    import json
    doc = json.loads((ROOT / ".claude" / "settings.json").read_text("ascii"))
    cmds = [h["command"] for g in doc["hooks"]["Stop"] for h in g["hooks"]]
    assert "python ops/fleet_kit/fleet_done.py stop-hook" in cmds
    assert all(":" not in c for c in cmds)  # no drive path


def test_done_ritual_last_act_is_the_marker():
    text = (ROOT / ".claude" / "commands" / "done.md").read_text("ascii")
    mark = "python ops/fleet_kit/fleet_done.py mark --session <n> --status done"
    assert mark in text
    assert '--status failed --reason "<step>"' in text
    tail = text[text.index(mark):]
    assert "Done ritual complete, safe to clear" in tail
    # nothing but the chat line and safety rails follows the marker step
    assert "### 8." in text[:text.index(mark)] and "git push" not in tail


def test_pretooluse_hook_runs_kit_subagent_first():
    """Kit v10 item 2: the project settings wire the subagent-first hook exactly."""
    import json
    doc = json.loads((ROOT / ".claude" / "settings.json").read_text("ascii"))
    want = {"matcher": "Bash|PowerShell|Read|Edit|Write|Grep|Glob|NotebookEdit|MultiEdit",
            "hooks": [{"type": "command",
                       "command": "python ops/fleet_kit/fleet_subagent_first.py",
                       "timeout": 10}]}
    assert want in doc["hooks"]["PreToolUse"]
    assert (ROOT / "ops" / "fleet_kit" / "fleet_subagent_first.py").is_file()


def test_done_ritual_is_dispatched_to_one_subagent():
    """Kit v10 item 4: /done runs as ONE sub-agent; main relays its final line."""
    text = (ROOT / ".claude" / "commands" / "done.md").read_text("ascii")
    assert ("DISPATCH (kit v10):** the main session runs no tool for /done; it dispatches "
            "the whole ritual to ONE background sub-agent and relays only that agent's "
            "final line verbatim.") in text

def test_shared_slots_governor_is_byte_identical():
    data = (ROOT / "ops" / "loop" / "slots.py").read_bytes()
    assert hashlib.sha256(data).hexdigest() == SLOTS_SHA256
