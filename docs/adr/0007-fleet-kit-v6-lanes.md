# ADR 0007 - Fleet kit v6 adopted: lanes through fleet_lanes, cap 3

Date: 2026-10-04. Status: accepted. Ordered by MAIN note 2237 (hash-verified
against MAIN's outbox, bundle 10/10 files equal).

Decision: vendor kit v6 over `ops/fleet_kit/` in one commit. `tools/ew_lane.py`
claims every lane through `fleet_lanes.run_lane` with cap 3 (LANE_CAP_MAX) and
takes its one governor slot at the executor call via
`spawn(governor="queued")`; `ops/loop/slots.py` is no longer held by EW (kept
vendored and byte-pinned for on-disk compatibility).

Worktrees move from `../ew-worktrees/<lane name>` on `lane/<name>` branches to
the kit layout `../ew-worktrees/lane-<i>`, detached at main's HEAD. Lanes still
never commit; the main session commits on the detached lane worktree and merges
that commit with --no-ff. Gitignored gate config is seeded into each worktree.

Alternatives: keep slots.hold around spawn (allowed by the kit, but two
mechanisms for one rule); cap below 3 (no measured reason - EW lanes are
independent server/dashboard slices).

Why: MAIN order; one-slot-per-call fairness across the machine.

Reverses if: proxy 429s appear in the usage lines with three EW lanes running,
or a kit v7 changes the lane contract.
