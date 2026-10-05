#!/usr/bin/env python3
"""Point git at the tracked hooks: `git config core.hooksPath .githooks`.

Git hooks are the authoritative gate and a fresh clone has none until this runs.
Idempotent.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    subprocess.run(["git", "-C", str(ROOT), "config", "core.hooksPath", ".githooks"],
                   check=True)
    print("hooks: core.hooksPath=.githooks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
