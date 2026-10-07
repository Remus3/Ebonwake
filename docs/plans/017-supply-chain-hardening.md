# Plan 017 - Supply-chain hardening (MAIN order Nfa7953, 2026-10-05 scorecard audit)

Status: built in lane 2026-10-05; Scorecard before/after measurement BLOCKED
(operator-approved download). One lane (`build`).

Source: MAIN ORDER 2026-10-05-0300 (repo scorecard audit, EW 3.6). Items:

| Item | What | State |
|---|---|---|
| 2.1 | `.github/dependabot.yml`: github-actions `/`, pip `/ci`, npm `/app`, weekly, one group each | done |
| 2.2a | Every `uses:` pinned to a 40-char SHA from the release tag, tag as comment | done (ci.yml 3, codeql.yml 3) |
| 2.2b | CI pip from hash-pinned `ci/requirements-ci.txt` (`uv pip compile --universal --generate-hashes`), `--require-hashes` | done; no unhashed pip line left, no `pip install --upgrade pip`, no editable install |
| 2.3 | `.github/workflows/codeql.yml`: python, javascript-typescript, actions; `build-mode: none`; push/PR/weekly; job-level security-events write + contents/actions read | done |
| 2.4 | Top-level `permissions: contents: read` on every workflow | done (ci.yml had it; codeql.yml has it) |
| 2.6 | Fuzzing ruled out, recorded | done (SECURITY.md "Supply chain") |
| 3a | npm advisories | done earlier: Electron 44.5.1, `npm audit` 0 (55dd0d5) |
| 3b | Pinning (3 uses + pytest pip line) | done (see 2.2) |
| 3c | Dependabot npm/pip/actions | done (see 2.1) |
| 3d | Linter gate + ROADMAP.md | done: `python -m ruff check server tools tests` step in ci (2 findings fixed: F401 tools/ew_loop.py, F841 tests/test_leveling.py); root ROADMAP.md, open work only. ESLint not configured in app/, so no JS lint step (CodeQL covers JS static analysis) |
| 4 | Scorecard v5.5.0 Linux binary in WSL, before/after | BLOCKED: needs a binary download into WSL (operator-approved download) |

## As-built deviations

1. `persist-credentials: false` on every `actions/checkout`. Decision: add it.
   Alternatives: leave the default (token kept in `.git/config` for later
   steps). Why: no step pushes or calls the API with the checkout token;
   dropping it shrinks token exposure and is what Scorecard/zizmor flag.
   Reverses if: a step ever needs an authenticated git operation.
2. `workflow_dispatch` added to codeql.yml beside push/PR/schedule. Decision:
   add. Alternatives: the three ordered triggers only. Why: lets a re-scan run
   without a dummy push; read-only token, no new scope. Reverses if: MAIN's
   audit counts it against a check.
3. Separate "install CI deps" and "ruff" steps ahead of pytest (the old pytest
   step installed pytest inline). Decision: split. Alternatives: one combined
   step. Why: lint failure reads as lint, install failure as install.
   Reverses if: never - cosmetic.
4. No JS linter step. Decision: none now. Alternatives: add ESLint +
   config + lockfile churn. Why: the order says "the app's ESLint if it is
   configured" - it is not; CodeQL javascript-typescript covers static
   analysis. Reverses if: ESLint is adopted in app/.
5. Refute round 1 (pins unverifiable locally: no network grant in the lane
   sandbox). Decision: add `tools/verify_action_pins.py` and run it as a CI
   step - shape check (40-hex SHA + tag comment) offline in pytest, and online
   in CI each tag is resolved with `git ls-remote` (peeled ref preferred) and
   must equal the pin. Alternatives: accept the gap; wait for an operator
   network grant. Why: turns a one-off manual lookup into a standing gate that
   also catches a hand-edited pin drifting from its comment; CI has network.
   Reverses if: Dependabot or Scorecard grows an equivalent pin-to-tag check.
6. Refute round 1 (ruff unverified by the verifier). Re-run by the producer:
   `ruff check server tools tests` -> "All checks passed!" (ruff 0.15.12).
   No change needed. Reverses if: never - a measurement.

## Post-merge fix (2026-10-05)

First push of the merge (f5d51af) failed `ci`: CI's hash-pinned ruff 0.16.10
reported 118 findings that the lane's local ruff 0.15.12 did not, because the
tree had no ruff config and the two versions' built-in default rule sets
differ. Decision: check in `ruff.toml` with an explicit `[lint] select =
["E4", "E7", "E9", "F"]` (the rule set the lane gated on) plus
`tests/test_ruff_config.py`. Alternatives: fix all 118 findings now (rule
set chosen by a version bump, not by us; large unreviewed diff), or pin the
local box to 0.16.10 (needs a download, and still drifts on the next
dependabot bump). Why: the gate's scope becomes a reviewed repo decision.
Reverses if: a later plan widens the rule set on purpose (edit `select`).
