"""Leak sweep: synthetic needles only - this file never spells a real one."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import leak_sweep as ls  # noqa: E402

FAKE = ["Zorblax Quux", "acct-0000-fake"]


def _kinds(text, needles=FAKE, rel="x.md"):
    return [k for _, _, k in ls.scan_text(text, rel, ls.compile_needles(needles), rel)]


def test_needle_variants_and_slot_reporting():
    assert _kinds("see zorblax-quux here") == ["needle-slot-0"]
    assert _kinds("ZorblaxQuux") == ["needle-slot-0"]
    assert _kinds("id acct-0000-fake") == ["needle-slot-1"]
    assert _kinds("nothing to see") == []


def test_structural_arm_without_config():
    drive = "C" + ":" + "\\" + "Secret Project\\x.py"
    assert "drive-path" in _kinds(drive, needles=[])
    user = "/" + "Users/" + "somebody/file"
    assert "user-profile" in _kinds(user, needles=[])
    mail = "ping someone" + "@" + "mail.test.org"
    assert "email" in _kinds(mail, needles=[])
    key = "sk-" + "ant-" + "abcdefghijklmnop"
    assert "anthropic-key" in _kinds(key, needles=[])


def test_structural_allowlist():
    generic = "C" + ":" + "\\" + "ProgramData\\thing"
    assert _kinds(generic, needles=[]) == []
    assert _kinds("x" + "@" + "example.com", needles=[]) == []
    pinned = "C" + ":" + "\\" + "Anything\\here"
    assert ls.scan_text(pinned, "ops/loop/slots.py", [], "ops/loop/slots.py") == []


def test_load_needles_modes(tmp_path):
    assert ls.load_needles(tmp_path / "none.json", environ={}) == ([], "DEGRADED")
    assert ls.load_needles(tmp_path / "none.json", environ={"EW_LEAK_NEEDLES": "abc;def"}) == \
        (["abc", "def"], "ARMED")
    cfg = tmp_path / "c.json"
    cfg.write_text('{"needles": []}')
    try:
        ls.load_needles(cfg, environ={})
        raise AssertionError("empty config must FAULT")
    except ValueError:
        pass


def test_diff_scans_added_lines_and_paths():
    diff = ("+++ b/docs/zorblax_quux.md\n@@\n+clean line\n-removed " + "C" + ":" +
            "\\" + "Old\\gone\n")
    kinds = [k for _, _, k in ls.scan_diff(diff, ls.compile_needles(FAKE))]
    assert kinds == ["needle-slot-0"]


def test_tracked_tree_is_clean_structurally():
    findings, n = ls.scan_tree(ls.compile_needles([]))
    assert n > 0
    assert findings == []
