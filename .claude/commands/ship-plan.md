---
description: One self-contained pass over the next ROADMAP plan - plan, list slices, initiate lanes, process results, verify, merge, push as one batch.
---

> **SUBAGENT-FIRST.** Main session plans, dispatches, merges, reports. Build work
> runs in lane worktrees via `tools/ew_lane.py` (headless, account B) or
> worktree-isolated sub-agents. Refute rounds capped at 3 per item.

Loop: **plan -> list -> initiate -> process**, idempotent at every step.

### 0. Pre-flight
- Read `EW-NEXT-SESSION.txt`, `docs/plans/ROADMAP.md`, the next plan doc.
- `python tools/ew_lane.py status` - lanes, worktrees, ETAs.
- Probe live state: `GET http://127.0.0.1:8940/api/health` (restart the server
  freely if stale - standing order 11).

### 1. Plan
If the plan doc lacks slices with acceptance checks, write them first (spec
before code). Blocked choice -> `adjudicator` agent -> do its recommendation now.

### 2. List
Split into disjoint slices, at most 3 concurrent (lanes `build`, `data`,
`review`). Each slice names its files, its failing tests, its acceptance.

### 3. Initiate
Per slice: `python tools/ew_lane.py ensure <lane>`, write the slice prompt to a
temp file (it must require a progress file after each step), then run
`python tools/ew_lane.py run <lane> --prompt-file <f> --writes-code` as a
background command with ETA `python tools/eta.py estimate lane-<lane>-code`.

### 4. Process
Poll progress files, not the agents. Overrun at 1.5x ETA = report one line;
3x = kill that run and re-slice. On finish: `verifier` agent on the lane branch;
up to 3 refute rounds; then merge into main.

### 5. Land
All slices merged -> full gates -> one commit batch -> one push -> CI collected
in the background -> ROADMAP flipped -> next plan.

Never touch the BDO client (ToS floor in CLAUDE.md).
