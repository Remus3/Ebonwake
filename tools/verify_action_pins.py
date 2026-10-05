#!/usr/bin/env python3
"""Check that every workflow `uses:` is a full SHA pin matching its tag comment.

    verify_action_pins.py            shape check + resolve each tag on GitHub
    verify_action_pins.py --offline  shape check only (no network)

Every `uses: owner/repo[/path]@<40-hex> # vX.Y.Z` line in .github/workflows/
must carry a 40-hex SHA and a tag comment. Online, each tag is resolved with
`git ls-remote` (peeled `^{}` ref preferred, for annotated tags) and must
equal the pinned SHA. Local `./` actions and `docker://` refs are skipped.
Exit 0 clean, 1 on any finding.
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"

USES = re.compile(r"^\s*-?\s*uses:\s*(\S+)(?:\s*#\s*(\S+))?\s*$")
SHA = re.compile(r"^[0-9a-f]{40}$")


def parse(text):
    """Yield (lineno, repo, ref, tag) for each remote `uses:` line."""
    for n, line in enumerate(text.splitlines(), 1):
        m = USES.match(line)
        if not m:
            continue
        target, tag = m.group(1), m.group(2)
        if target.startswith("./") or target.startswith("docker://"):
            continue
        action, _, ref = target.partition("@")
        repo = "/".join(action.split("/")[:2])
        yield n, repo, ref, tag


def ls_remote(repo, tag):
    out = subprocess.run(
        ["git", "ls-remote", f"https://github.com/{repo}.git",
         f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"],
        capture_output=True, text=True, timeout=60, check=True).stdout
    refs = {}
    for line in out.splitlines():
        sha, _, name = line.partition("\t")
        refs[name] = sha
    return refs.get(f"refs/tags/{tag}^{{}}") or refs.get(f"refs/tags/{tag}")


def check(files, resolve=None):
    findings = []
    cache = {}
    for path in files:
        for n, repo, ref, tag in parse(Path(path).read_text(encoding="utf-8")):
            where = f"{Path(path).name}:{n}"
            if not SHA.match(ref):
                findings.append(f"{where}: {repo}@{ref} is not a 40-hex SHA")
                continue
            if not tag:
                findings.append(f"{where}: {repo}@{ref} has no tag comment")
                continue
            if resolve is None:
                continue
            key = (repo, tag)
            if key not in cache:
                cache[key] = resolve(repo, tag)
            if cache[key] != ref:
                findings.append(
                    f"{where}: {repo} {tag} resolves to {cache[key]}, pinned {ref}")
    return findings


def main(argv):
    offline = "--offline" in argv
    files = sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))
    findings = check(files, resolve=None if offline else ls_remote)
    for f in findings:
        print(f)
    print(f"verify_action_pins: {len(files)} workflow(s), "
          f"{len(findings)} finding(s){' (offline)' if offline else ''}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
