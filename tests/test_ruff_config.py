"""The ruff gate must select its rules explicitly (plan 017 deviation).

CI installs ruff from the hash-pinned ci/requirements-ci.txt; a developer box
may run another ruff version. Without a checked-in rule selection the gate
follows each version's built-in default, so a box can pass while CI fails
(seen 2026-10-05: local 0.15 clean, CI 0.16.10 118 findings). An explicit
`[lint] select` makes the gate the same rule set on every ruff version.
"""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_ruff_toml_pins_lint_select():
    cfg = tomllib.loads((ROOT / "ruff.toml").read_text(encoding="utf-8"))
    select = cfg.get("lint", {}).get("select")
    assert select, "ruff.toml must set [lint] select explicitly"
    assert {"E4", "E7", "E9", "F"} <= set(select)
    assert "extend-select" not in cfg.get("lint", {})


def test_ci_ruff_is_hash_pinned():
    txt = (ROOT / "ci" / "requirements-ci.txt").read_text(encoding="utf-8")
    block = txt.split("ruff==", 1)[1].split("\n\n", 1)[0]
    assert "--hash=sha256:" in block
