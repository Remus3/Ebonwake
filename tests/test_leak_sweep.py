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


def test_regex_escape_time_pattern_is_not_a_drive_path():
    # fix-loop-stall: `\d\d:\d\d` (a HH:MM regex) read as drive `d:` + `\d\d`
    # refused plan 031's lane commit at the pre-commit hook
    bs = "\\"
    line = 'assert re.match(r"^' + bs + "d" + bs + "d:" + bs + "d" + bs + 'd$", s)'
    assert _kinds(line, needles=[]) == []
    assert _kinds("rows = [r" + '"' + bs + "| " + bs + "d" + bs + "d:" + bs + "d" + bs
                  + "d " + bs + '|"]', needles=[]) == []
    # a real drive path after a separator is still flagged
    assert "drive-path" in _kinds('p = "' + "C" + ":" + bs + 'x' + bs + 'y"', needles=[])


# --- pre-push: messages already public on the remote ref (MAIN 2246 sec 4) ---

import subprocess  # noqa: E402


def _g(repo, *args):
    return subprocess.run(["git", *args], cwd=str(repo), check=True,
                          capture_output=True).stdout.decode().strip()


def _commit(repo, name, text, msg):
    (repo / name).write_text(text, newline="\n")
    _g(repo, "add", name)
    _g(repo, "-c", "user.name=t", "-c", "user.email=t@example.com",
       "commit", "-q", "-m", msg)
    return _g(repo, "rev-parse", "HEAD")


def _repo(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    _g(repo, "init", "-q", "-b", "main")
    return repo


def _push_kinds(repo, local, remote):
    rx = ls.compile_needles(FAKE)
    f, _ = ls.scan_pre_push([f"refs/heads/main {local} refs/heads/main {remote}"], rx, cwd=repo)
    return [k for _, _, k in f]


def _rewritten(repo, old_msg, new_file_text="b\n", new_msg="later"):
    """A public commit holding old_msg, then a rewritten twin of it (new SHA,
    same message) plus one more commit, as a history rewrite leaves it."""
    base = _commit(repo, "a.txt", "a\n", "base")
    remote = _commit(repo, "b.txt", "b\n", old_msg)
    _g(repo, "checkout", "-q", "--detach", base)
    (repo / "pad.txt").write_text("p\n", newline="\n")
    _g(repo, "add", "pad.txt")
    _g(repo, "-c", "user.name=u", "-c", "user.email=u@example.com",
       "commit", "-q", "-m", old_msg)
    local = _commit(repo, "c.txt", new_file_text, new_msg)
    return local, remote


def test_pre_push_exempts_message_already_on_remote(tmp_path):
    repo = _repo(tmp_path)
    local, remote = _rewritten(repo, "Merge pull request #2 from zorblax-quux/dep")
    assert _push_kinds(repo, local, remote) == []


def test_pre_push_new_message_with_needle_still_fails(tmp_path):
    repo = _repo(tmp_path)
    local, remote = _rewritten(repo, "plain", new_msg="leak Zorblax Quux")
    assert _push_kinds(repo, local, remote) == ["needle-slot-0"]


def test_pre_push_one_byte_different_message_still_fails(tmp_path):
    repo = _repo(tmp_path)
    base = _commit(repo, "a.txt", "a\n", "base")
    remote = _commit(repo, "b.txt", "b\n", "Merge from zorblax-quux/dep")
    _g(repo, "checkout", "-q", "--detach", base)
    local = _commit(repo, "c.txt", "c\n", "Merge from zorblax-quux/dep!")
    assert _push_kinds(repo, local, remote) == ["needle-slot-0"]


def test_pre_push_added_line_with_needle_still_fails(tmp_path):
    repo = _repo(tmp_path)
    local, remote = _rewritten(repo, "from zorblax-quux/dep",
                               new_file_text="acct-0000-fake\n")
    assert _push_kinds(repo, local, remote) == ["needle-slot-1"]


def test_pre_push_new_branch_gets_no_exemption(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, "a.txt", "a\n", "base")
    local = _commit(repo, "b.txt", "b\n", "from zorblax-quux/dep")
    assert _push_kinds(repo, local, ls.ZERO) == ["needle-slot-0"]
