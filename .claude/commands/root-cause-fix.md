---
description: Root-cause-first TDD loop for any bug or data fix - failing reproduction test first, sibling-case sweep, minimal fix, backfill of already-corrupted state. Refute rounds capped at 3.
---

> **SUBAGENT-FIRST.** Dispatch the work; the main session plans, merges and reports.
> Producer never grades its own fix; a `verifier` agent confirms (max 3 refute
> rounds, then the `adjudicator` rules and the work moves on).

ETA: `python tools/eta.py estimate root-cause-fix` before starting; record the
real duration at the end.

### 1. Reproduce BEFORE fixing (RED)
Write a failing test that reproduces the exact symptom. Run it; confirm it fails
for the stated reason.

### 2. Find every sibling case
Grep for the same pattern across `server/`, `app/`, `tools/` and the store
documents. Add a failing case per sibling. Record the list.

### 3. Minimal fix (GREEN)
Fix at the single chokepoint. `python -m pytest -q` and `npm test --prefix app`
fresh; zero regressions.

### 4. Backfill
If the bug already corrupted store documents or caches under `ops/runtime/`,
write and run the repair in the same change and verify it. Otherwise state "no
historical corruption possible" in the commit body.

### 5. Verify and land
`verifier` agent confirms (rounds `N/3` in the commit body). Commit with the
batch; push per `/done`.
