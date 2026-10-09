"""Plan 097: run the fast test tier through the kit suite gate.

    python tools/ew_tests.py fast --owner <session_id>.<agent_id> [pytest args]

The fast tier (`pytest --fast`, tests/ew_tiers.py) deselects slow and
real-git tests. It names no test file, so the kit counts it as a whole suite:
it always runs through ops/fleet_kit/fleet_suite_gate.py (one machine-wide
slot), never bare. With pytest-xdist importable it runs on the loop gate's
xdist arguments (ew_loop.XDIST_ARGS). Exit code = the suite's. Stdlib only.
"""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import ew_loop  # noqa: E402


def fast_argv(owner, extra, xdist=None):
    use = ew_loop._xdist_available() if xdist is None else xdist
    suite = [ew_loop._python(), "-m", "pytest", "-q", "--fast",
             *(ew_loop.XDIST_ARGS if use else ()), *extra]
    return [ew_loop._python(), str(ROOT / ew_loop.SUITE_GATE_REL), "run", "--owner", owner,
            "--", *suite]


def _run(argv):
    return subprocess.call(argv, cwd=str(ROOT))


def main(argv=None, run=_run):
    ap = argparse.ArgumentParser(prog="ew_tests.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="verb", required=True)
    f = sub.add_parser("fast", help="fast tier through the kit suite gate")
    f.add_argument("--owner", required=True, help="claims-hook owner id")
    args, extra = ap.parse_known_args(sys.argv[1:] if argv is None else argv)
    return run(fast_argv(args.owner, extra))


if __name__ == "__main__":
    sys.exit(main())
