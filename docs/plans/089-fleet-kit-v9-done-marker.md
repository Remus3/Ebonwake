# Plan 089 - Fleet kit v9: CLI display, /done marker, Stop hook, MIG-1 worktree prune

Status: partial (lane-0, order N67971f). ORDER NOT CLOSED: acceptance is not met
until the main session does items 1, 2, 3, 5 and 6. Merging this lane does not
close the order, and no closing ANSWER goes out on this lane's merge alone. See
ROADMAP "Order N67971f - blocked items".

Order items:

1. Vendor kit v9 (16 files, including `fleet_done.py`) into `ops/fleet_kit/`,
   re-pin `ops/fleet_kit/MANIFEST.json` and `tests/test_fleet_kit_conformance.py`,
   re-embed FLEET COMMON with item 15 in `CLAUDE.md`.
2. `.claude/commands/done.md`: the last act is `python ops/fleet_kit/fleet_done.py mark`.
3. `.claude/settings.json`: add the kit's Stop hook and remove `spinnerTipsEnabled`.
4. `.gitignore`: list the marker files `ops/loop/control/session_done.json` and `.seen`.
5. MIG-1: prune stale lane worktrees (`git worktree list` showed 4).
6. Send the ANSWER to ORDER 2155 (C-drive path inventory).

Done in-lane: item 4. Lane-1 and lane-2 HEADs (44d1006, e1d71be) have no commits
outside main.

## As-built deviations

Refute round 1/3 (verifier FAIL: items 1-3, 5 and 6 not done). All are
adjudicated by the producer. Each is a grant boundary, not a design choice,
and each was re-tested in round 1.

1. Items 1, 2 and the Stop hook in item 3 are deferred to the main session.
   - Decision: no edits to `ops/fleet_kit/`, `CLAUDE.md`, `done.md` or the hook.
   - Alternatives: (a) hand-write a `fleet_done.py`; (b) add a Stop hook
     that is skipped when the script is missing; (c) defer.
   - Why: the lane brief forbids editing `ops/fleet_kit/` and `CLAUDE.md`
     (FLEET item 11: kit files are byte-pinned from MAIN's bundle). The v9
     bundle and the order note sit in the main checkout inbox, and a read was
     refused in round 1. Without the bundle, the Stop-hook subcommand and
     arguments are unknown. A guessed hook would fire on every Stop and
     could fail. A write to `.claude/` was also refused in round 1.
   - Reverses if: the lane gets read access to the inbox bundle and write
     access to `.claude/` and `ops/fleet_kit/`.
2. Removing `spinnerTipsEnabled` (item 3) is deferred.
   - Decision: leave `.claude/settings.json` unchanged.
   - Alternatives: remove the key now.
   - Why: the write to `.claude/settings.json` was refused in round 1.
   - Reverses if: `.claude/` becomes writable from a lane. It is a one-line
     removal, and the main session does it in the same commit as the Stop hook.
3. Item 5 (MIG-1 prune) is deferred.
   - Decision: remove no worktree.
   - Alternatives: `git worktree remove` on lane-1 and lane-2.
   - Why: their uncommitted state could not be read from this lane, and FLEET
     item 9 says to check before any delete. lane-0 is this lane's own
     worktree and stays.
   - Reverses if: the main session reads that lane-1 and lane-2 are clean and
     their locks are FREE.
4. Item 6 (ANSWER to ORDER 2155) is deferred to the loop.
   - Decision: the loop sends it with HOP 2 after merge.
   - Alternatives: write the outbox note from the lane.
   - Why: the outbox is outside this lane's grant, and CLAUDE.md loop rule 2
     answers orders after merge.
   - Reverses if: never. This is the standing loop contract.

Refute round 2/3 (verifier FAIL, findings 1-8). Each grant was re-tested in
round 2, and the same boundaries hold:

- Read of the main checkout's `moon_sync_inbox/` (kit v9 bundle, order note):
  refused.
- Write to `.claude/settings.json` (remove `spinnerTipsEnabled`): refused.
- `git worktree list` and `git checkout -- <file>`: refused (not on the lane
  allow list).

5. Findings 1-6 stand as deviations 1-4 above; nothing new became doable.
   - Decision: route items 1, 2, 3, 5 and 6 to the main session as the first
     hand-off action, and mark the order open (Status line above), as finding
     6 asks.
   - Alternatives: (a) hand-write `fleet_done.py` and the Stop hook; (b) mark
     the order closed with the deferrals.
   - Why: (a) breaks FLEET item 11 (kit bytes come only from MAIN's bundle),
     and the round 2 write to `.claude/` was refused anyway; (b) is the
     acceptance gap finding 6 names.
   - Reverses if: the lane gets the read and write grants listed above.
6. Finding 7 (the `.gitignore` lines are redundant): the lines stay.
   - Decision: keep the explicit `session_done.json` and `.seen` entries.
   - Alternatives: drop them, since `ops/loop/control/*` already covers them.
   - Why: order item 4 asks for the marker files to be listed by name. The
     explicit lines also keep the marker ignored if the `ops/loop/control/*`
     glob is ever narrowed (for example, another `!` re-include). They cost
     nothing, and the comment states the overlap.
   - Reverses if: kit v9's conformance test or MAIN's order text turns out
     to require only the glob.
7. Finding 8 (the ASCII/LF check did not run): re-checked by the producer
   in round 2 with a ripgrep scan for `[^\x00-\x7F]|\r` over `.gitignore`,
   `ROADMAP.md`, this plan and `fix.json`: 0 matches. `tests/test_ascii_lf.py`
   in `pytest` also covers the tracked files. Gates in round 2: ruff clean,
   pytest green, node 693/693, leak sweep ARMED with no HALT.

Refute round 3/3 (verifier FAIL, findings 1-6: items 1, 2, 3, 5 and 6 still
not done). Grants re-tested in round 3:

- Read of the main checkout's `moon_sync_inbox/` (kit v9 bundle): refused.
- `git -C <lane-1|lane-2> status --porcelain`: refused.
- `git worktree list`: now allowed. It reads 4 entries: main, lane-0,
  lane-1 (44d1006) and lane-2 (e1d71be), all three lanes on detached HEAD.

8. Findings 1-5 stand as deviations 1-4. Finding 6 is the round-3 ruling.
   - Decision (adjudicated at round 3, standing order 7, no round 4): merge
     this partial diff, which has `.gitignore` item 4, the ROADMAP blocked
     section and this plan. The order stays OPEN. The main session does
     items 1, 2, 3, 5 and 6 as the first hand-off action. No closing ANSWER
     goes out on this merge.
   - Alternatives: (a) prune lane-1/2 now, since `git worktree list` works;
     (b) hand-write the v9 kit files; (c) hold the lane unmerged.
   - Why: (a) the lanes' uncommitted state is still unreadable, and FLEET
     item 9 says to check for a consumer before deleting. Their HEADs carry
     no unmerged commits, but a dirty tree could. (b) breaks FLEET item 11.
     (c) blocks the item-4 change for nothing, and the verifier rated the
     merge safe.
   - Reverses if: the main session reads lane-1 and lane-2 clean (then it
     prunes them) and vendors the v9 bundle (then items 1-3 land in one
     commit and the ANSWER goes out).

refute-rounds: 3/3
