import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import verify_action_pins as vap  # noqa: E402

A = "a" * 40
B = "b" * 40


def _wf(tmp_path, body):
    p = tmp_path / "wf.yml"
    p.write_text(body, encoding="utf-8", newline="\n")
    return [p]


def test_repo_workflows_are_sha_pinned_offline():
    files = sorted(vap.WORKFLOWS.glob("*.yml"))
    assert files
    assert vap.check(files) == []


def test_tag_ref_is_rejected(tmp_path):
    files = _wf(tmp_path, "      - uses: actions/checkout@v4\n")
    assert "not a 40-hex SHA" in vap.check(files)[0]


def test_missing_tag_comment_is_rejected(tmp_path):
    files = _wf(tmp_path, f"      - uses: actions/checkout@{A}\n")
    assert "no tag comment" in vap.check(files)[0]


def test_subpath_action_resolves_against_repo(tmp_path):
    files = _wf(tmp_path,
                f"      - uses: github/codeql-action/init@{A} # v4.0.0\n")
    seen = []

    def resolve(repo, tag):
        seen.append((repo, tag))
        return A

    assert vap.check(files, resolve) == []
    assert seen == [("github/codeql-action", "v4.0.0")]


def test_sha_tag_mismatch_is_reported(tmp_path):
    files = _wf(tmp_path, f"      - uses: actions/setup-node@{A} # v4.4.0\n")
    out = vap.check(files, lambda repo, tag: B)
    assert len(out) == 1 and "pinned" in out[0]


def test_local_and_docker_refs_skipped(tmp_path):
    files = _wf(tmp_path, "      - uses: ./local\n      - uses: docker://x:1\n")
    assert vap.check(files) == []
