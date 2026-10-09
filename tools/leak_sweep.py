#!/usr/bin/env python3
"""EW leak sweep - refuses to publish machine paths, emails, secrets, sibling names.

This repository is PUBLIC. Same design as the sibling-name sweep the fleet
already runs, reduced to EW's size:

* NEEDLE arm - literal strings (sibling project names, account ids, user names)
  loaded at RUN TIME from the gitignored `config/leak_needles.json`
  (`{"needles": [...]}`) or env `EW_LEAK_NEEDLES` (`;`-separated). This file and
  its tests never spell a real needle, and a finding prints the needle's SLOT
  NUMBER, never its text (hook output lands in logs and transcripts).
  `--explain N` prints slot N locally, on demand.
* STRUCTURAL arm - config-free: drive-rooted Windows paths, user-profile paths,
  e-mail addresses, and secret shapes (API key prefixes, private key blocks).
  It still runs in a fresh clone and on CI, where no needle file exists.

Modes: `--tree` (every tracked file), `--staged` (pre-commit, added lines),
`--pre-push` (stdin ref lines; added lines + commit messages of the pushed
range). Exit 0 clean, 2 HALT (finding), 3 FAULT (could not run). DEGRADED (no
needle config) is announced on every run and is not silent; it is not a FAULT,
because CI legitimately has no config.

Escape rate is above zero: `--no-verify` bypasses the hooks, and a needle
spelled in a shape nobody listed passes. A clean run is not proof.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "leak_needles.json"
ZERO = "0" * 40

# Byte-pinned shared files that legitimately carry a drive-rooted DEFAULT path.
STRUCT_ALLOW_FILES = {"ops/loop/slots.py"}
# Paths the structural arm never flags (generic, identify nobody).
STRUCT_ALLOW_PATHS = re.compile(
    r"^[A-Za-z]:[\\/]+(ProgramData|Program Files( \(x86\))?|Windows)([\\/]|$)", re.I)
EMAIL_ALLOW = re.compile(r"@(example\.(com|org|net)|users\.noreply\.github\.com)$", re.I)

STRUCTURAL = [
    ("drive-path", re.compile(r"(?<![A-Za-z0-9\\])[A-Za-z]:[\\/]{1,2}[^\s\"'`<>|*?]+")),
    ("user-profile", re.compile(r"(?i)[\\/]Users[\\/](?!Example|<|%|\{)[A-Za-z0-9._-]+")),
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")),
    ("anthropic-key", re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}")),
    ("github-token", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("aws-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]


def load_needles(config=CONFIG, environ=None):
    """Return (needles, mode). mode is ARMED, DEGRADED or raises ValueError (FAULT)."""
    env = os.environ if environ is None else environ
    raw = env.get("EW_LEAK_NEEDLES")
    if raw:
        needles = [n.strip() for n in raw.split(";") if n.strip()]
    elif Path(config).is_file():
        doc = json.loads(Path(config).read_text(encoding="utf-8"))
        needles = [n.strip() for n in doc.get("needles", []) if isinstance(n, str) and n.strip()]
        if not needles:
            raise ValueError("needle config present but holds no usable needle")
    else:
        return [], "DEGRADED"
    short = [i for i, n in enumerate(needles) if len(n) < 3]
    if short:
        raise ValueError(f"needle slot(s) {short} shorter than 3 chars")
    return needles, "ARMED"


def _variants(needle):
    """Spaced, hyphenated, underscored, tight spellings of a needle."""
    words = re.split(r"[\s_-]+", needle.strip())
    out = {needle}
    for sep in (" ", "-", "_", ""):
        out.add(sep.join(words))
    return sorted(out, key=len, reverse=True)


def compile_needles(needles):
    return [(i, re.compile("|".join(re.escape(v) for v in _variants(n)), re.I))
            for i, n in enumerate(needles)]


def scan_text(text, where, needles_rx, rel_path=""):
    """Findings for one blob of text: list of (where, line_no, kind)."""
    findings = []
    for ln, line in enumerate(text.splitlines(), 1):
        for slot, rx in needles_rx:
            if rx.search(line):
                findings.append((where, ln, f"needle-slot-{slot}"))
        if rel_path in STRUCT_ALLOW_FILES:
            continue
        for kind, rx in STRUCTURAL:
            for m in rx.finditer(line):
                hit = m.group(0)
                if kind == "drive-path" and STRUCT_ALLOW_PATHS.match(hit):
                    continue
                if kind == "email" and EMAIL_ALLOW.search(hit):
                    continue
                findings.append((where, ln, kind))
    return findings


def _git(*args, cwd=ROOT):
    out = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True)
    if out.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed")
    return out.stdout.decode("utf-8", "replace")


def _is_text(data):
    return b"\0" not in data[:8192]


def scan_tree(needles_rx, cwd=ROOT):
    findings = []
    files = [f for f in _git("ls-files", "-z", cwd=cwd).split("\0") if f]
    for rel in files:
        findings += scan_text(rel, "path", needles_rx)  # the path itself publishes
        p = Path(cwd) / rel
        try:
            data = p.read_bytes()
        except OSError:
            continue
        if _is_text(data):
            findings += scan_text(data.decode("utf-8", "replace"), rel, needles_rx, rel)
    return findings, len(files)


def _added_lines(diff_text):
    """{path: text of added lines} from a unified diff."""
    out, cur = {}, None
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            cur = line[6:] if line.startswith("+++ b/") else None
            if cur is not None:
                out.setdefault(cur, [])
        elif cur is not None and line.startswith("+") and not line.startswith("+++"):
            out[cur].append(line[1:])
    return {k: "\n".join(v) for k, v in out.items()}


def scan_diff(diff_text, needles_rx):
    findings = []
    for rel, text in _added_lines(diff_text).items():
        findings += scan_text(rel, "path", needles_rx)
        findings += scan_text(text, rel, needles_rx, rel)
    return findings


def scan_pre_push(ref_lines, needles_rx, cwd=ROOT):
    findings, scanned = [], 0
    for line in ref_lines:
        parts = line.split()
        if len(parts) != 4 or parts[1] == ZERO:
            continue
        local_sha, remote_sha = parts[1], parts[3]
        rng = local_sha if remote_sha == ZERO else f"{remote_sha}..{local_sha}"
        if remote_sha == ZERO:
            diff = _git("show", "--format=", "--no-color", "-p", "--root", local_sha, cwd=cwd)
            all_commits = _git("rev-list", local_sha, cwd=cwd).split()
            diff = "\n".join(_git("show", "--format=", "--no-color", "-p", c, cwd=cwd)
                             for c in all_commits) or diff
            msgs = _git("log", "--format=%B", local_sha, cwd=cwd)
        else:
            diff = _git("diff", "--no-color", rng, cwd=cwd)
            # A message byte-identical to one already reachable from the remote
            # ref is public already (a history rewrite re-pushes old messages
            # under new SHAs); only messages new to the remote are scanned.
            public = set(_git("log", "-z", "--format=%B", remote_sha, cwd=cwd).split("\0"))
            msgs = "\n".join(m for m in _git("log", "-z", "--format=%B", rng, cwd=cwd).split("\0")
                             if m not in public)
        scanned += len(diff) + len(msgs)
        findings += scan_diff(diff, needles_rx)
        findings += scan_text(msgs, "commit-message", needles_rx)
    return findings, scanned


def report(findings, mode, n_needles):
    print(f"[leak-sweep] {mode} needles={n_needles}", file=sys.stderr)
    if mode == "DEGRADED":
        print("[leak-sweep] DEGRADED: no needle config - the needle arm proved nothing; "
              "structural arm ran", file=sys.stderr)
    for where, ln, kind in findings[:200]:
        print(f"[leak-sweep] HALT {where}:{ln} {kind}", file=sys.stderr)
    if findings:
        print(f"[leak-sweep] {len(findings)} finding(s). Fix the bytes; never print the "
              "matched text into a tracked file or a note.", file=sys.stderr)
    return 2 if findings else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--tree", action="store_true")
    g.add_argument("--staged", action="store_true")
    g.add_argument("--pre-push", nargs="*", metavar="ARG")
    g.add_argument("--explain", type=int)
    args = ap.parse_args(argv)
    try:
        needles, mode = load_needles()
    except (ValueError, OSError) as exc:
        print(f"[leak-sweep] FAULT config: {exc}", file=sys.stderr)
        return 3
    if args.explain is not None:
        if 0 <= args.explain < len(needles):
            print(needles[args.explain])
            return 0
        return 3
    rx = compile_needles(needles)
    try:
        if args.tree:
            findings, n = scan_tree(rx)
            if n == 0:
                print("[leak-sweep] FAULT: zero tracked files scanned", file=sys.stderr)
                return 3
        elif args.staged:
            findings = scan_diff(_git("diff", "--cached", "--no-color"), rx)
        else:
            refs = [ln for ln in sys.stdin.read().splitlines() if ln.strip()]
            findings, scanned = scan_pre_push(refs, rx)
            if refs and scanned == 0 and any(r.split()[1] != ZERO for r in refs
                                             if len(r.split()) == 4):
                print("[leak-sweep] FAULT: zero bytes scanned for a non-empty push",
                      file=sys.stderr)
                return 3
    except RuntimeError as exc:
        print(f"[leak-sweep] FAULT: {exc}", file=sys.stderr)
        return 3
    return report(findings, mode, len(needles))


if __name__ == "__main__":
    sys.exit(main())
