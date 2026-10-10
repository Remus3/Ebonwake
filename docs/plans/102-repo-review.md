# Plan 102 - Repo review and audit (repo-review-20261009)

Ordered by MAIN note 2026-10-08 2246 section 8 (operator order S7). Findings:
`docs/research/0018-repo-review-20261009.md`.

## History

1. 2026-10-09 09:22: queued as one headless review lane (run label
   `repo-review-20261009`, kind build, `tools/ew_lane.py run review`). The
   lane planned, counted 495 files in its own worktree and made four doc
   fixes, but its permission grant refused every main-checkout read, the
   git lock and the progress file, and it was reaped for low system memory
   about 10:00 before research 0018 was written. Its diff was kept on
   `refs/ew/keep/repo-review-20261009` (a808292).
2. 2026-10-09 20:00: re-run by a session sub-agent from that keep ref
   (adjudicated, below). Scope widened to the MAIN checkout, which is what
   section 8 asks for (tracked, untracked AND ignored files).

## Adjudicated (session 22)

Decision: run the review in a session sub-agent, starting from the keep
ref, instead of a second headless lane. Alternatives: (a) a `(priority)`
ROADMAP row for the loop; (b) a hand-started `ew_lane.py run review`.
Why: both (a) and (b) run inside a lane worktree whose grant cannot read
the gitignored main-checkout trees (runtime, loop control, inbox / outbox,
node_modules, sidecars), call the git lock or write the progress file -
the first run proved it (its S5 deviation) - so neither can meet the
ACCEPT line "file count reviewed equal to the count on disk"; (b) also
breaks the CLAUDE.md loop rule "a session does not run lanes by hand while
the loop is armed". Reverses if: lane grants gain main-checkout read access.

## Scope and file count

Every file on disk under the main checkout root, `.git` internals excluded.
Two folders are reviewed as one unit each, with their file counts:
`app/node_modules` (vendored npm tree) and `.claude/worktrees/agent-*` (a
LIVE sibling agent's worktree, not this review's to touch). Final count in
research 0018.

## Slices

| Slice | Work | Result |
|---|---|---|
| S1 | Plan, ROADMAP row 102, file count | done |
| S2 | Fold the reaped lane's partial: ROADMAP history split (rows 096-100 kept as done on main), ADR index, dead `.gitignore` negation | done |
| S3 | Main-checkout inventory (row 103): runtime, loop control, inbox / outbox, node_modules, sidecars, Claude project dirs | done |
| S4 | Reversible fixes: stale pyc, stale progress files and budget backups to the sidecar archive, duplicate project dir to the Recycle Bin, merged local branch, hash-pinned `ci/requirements-dev.txt`, row 015 status | done |
| S5 | Research 0018: findings per top-level folder, DONE / FILED / NOT-APPLICABLE, files reviewed N of N | done |
| S6 | Gates, commit through the git lock, push, ANSWER to MAIN | done |

## Acceptance

- Research 0018 lists every top-level folder with a verdict per finding and
  "files reviewed: N of N on disk".
- Gated `python -m pytest` and `npm test --prefix app` green.
- Nothing irreplaceable unlinked (Recycle Bin or sidecar moves only);
  nothing in `ops/fleet_kit/` or the FLEET-COMMON block touched; no game
  input.

refute-rounds: 0/3
