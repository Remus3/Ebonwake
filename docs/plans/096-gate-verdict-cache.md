# Plan 096 - Perf 2.1: one authoritative gate run per tree state

Status: built (lane, 2026-10-09). Source: ROADMAP row 096 (MAIN 2246 sec 2.1,
perf audit).

## Problem

One finished lane item paid for the whole pytest suite up to four times on the
same content: the producer ran it before finishing, the loop's `_gates` ran it
on the lane worktree, the review-lane verifier re-ran it (its allow list held
`Bash(python -m pytest:*)` and its prompt said "Re-run the gates"), and the
tick's `push()` ran the gates again on main after the merge - although main's
tree differed from the gated lane tree only by the loop's own ROADMAP row
flip. A red main re-ran the full suite on every 15-minute tick. Each whole
suite also holds a machine-wide suite-gate slot (kit v12), so the repeats
starved sibling trees too.

## Design

1. Tree key. `tree_key(cwd)` = the git tree object id of the working tree as
   `git add -A` would stage it: a clean tree is `HEAD^{tree}`; a dirty one is
   `git write-tree` over a TEMPORARY index (a copy of the real index, then
   `add -A`, env `GIT_INDEX_FILE`), so the lane's real index and files are
   never touched. Ignored files (node_modules, config/local.json) are not part
   of the key, exactly as they are not part of a commit. Any git failure gives
   no key (None) and the gates simply run uncached.
2. Verdict cache `ops/loop/control/gate_verdicts.json` in the main checkout
   (gitignored control dir): `{"trees": {<tree>: {ok, detail, ts, where,
   via?}}}`, newest 200 kept, atomic write, only written under the tick's
   loop lock. `Tick.gate(cwd)` is the one gate entry point: a cached green
   tree returns at once (log `gates cached green <tree12>`); a cached red one
   is reused for `RED_TTL_S` (2 h) so a flake can clear; anything else runs
   `Deps.gates` once and records the verdict. A red verdict that looks like
   infrastructure (suite-gate refusal / slot timeout `SUITE-GATE:`, a
   subprocess timeout or OSError, a missing ci workflow) is never cached.
3. Carry across the merge. After the loop's merge commit, `merge()` carries a
   green verdict from the lane commit's tree to main's new tree when the two
   differ ONLY in `docs/plans/ROADMAP.md` (the loop's own row flip, no code
   reads it). So a merge on an un-moved main makes the next `push()` skip the
   gates (`push: gates cached green`). If main moved meanwhile the trees
   differ in code and push runs the gates once on that new tree.
4. `push()` and `process()` call `Tick.gate`; the verifier is told the gate
   verdict and tree, and its `VERIFY_EXTRA` allow list drops
   `Bash(python -m pytest:*)` (it keeps npm / node tests, git diff / status
   and the leak sweep). Its prompt no longer says "Re-run the gates".
5. Producers run touched tests only: the lane `GATES` prompt asks for ruff,
   the test files the change touched or added (targeted, bare), and
   `npm test --prefix app` only when `app/` changed; it says the loop runs the
   authoritative whole suite (the ci commands) on the finished tree, so the
   producer does not run it.

## Acceptance

- Two gate calls on one unchanged tree run the gate commands once; a change
  to a tracked or untracked (non-ignored) file changes the key and re-runs.
- A dirty worktree's key equals the tree of the commit `add -A` + commit then
  makes, and computing it leaves `git status` and the index unchanged.
- push() on a tree already gated green runs no gate and still pushes; on an
  ungated tree it runs the gates once.
- A merged plan item whose lane tree was gated green is pushed without a
  second gate run (ROADMAP-only carry); a code difference is never carried.
- Infrastructure reds are not cached; a red verdict expires after 2 h.
- VERIFY_EXTRA holds no pytest; verify_prompt does not ask to re-run gates;
  GATES says touched tests only.
- Tests: `tests/test_ew_loop_gate_cache.py`, real git under tmp_path.

## As-built deviations

1. No plan doc existed for row 096; this lane wrote it from the ROADMAP row
   and MAIN 2246 sec 2.1. Alternatives: block on a session to write it. Why:
   the row names the three mechanisms exactly; blocking would idle the lane.
   Reverses if: MAIN's 2246 text specifies a different mechanism - then this
   doc and the code follow it.
2. Red verdicts are cached too, for 2 h, and infrastructure reds never.
   Alternatives: cache green only (a red main re-runs the full suite every
   15-minute tick, the waste this plan removes); cache red forever (a flaky
   test would block push until the tree changes). Why: bounded reuse keeps
   one run per tree state while a flake still clears within two hours.
   Reverses if: a cached red is seen hiding a since-fixed environment fault -
   then lower RED_TTL_S or cache green only.
3. ROADMAP-only carry at merge. Alternatives: key on the tree minus *.md
   (would also skip real doc changes that test_ascii_lf gates); no carry
   (push could never skip, since the loop's merge commit always adds the row
   flip). Why: the flip is written by the loop itself and no code reads the
   ROADMAP; the carry refuses any other difference. Reverses if: a test or
   gate starts reading docs/plans/ROADMAP.md - then drop the carry.
4. The key covers tracked + untracked non-ignored files only; the local
   Python / Node versions and ignored files are not part of it, and the loop
   still runs one local interpreter, not ci's 3.11 + 3.14 matrix. Why: that
   is exactly what a commit (and ci) sees; ci remains the cross-version
   check. Reverses if: a cached green is seen going red on the same tree
   locally - then add the interpreter version to the key.
5. The /done ritual and the git pre-push hook are unchanged. Why: the
   pre-push hook runs no suite (identity + leak sweep only) and /done lives
   in .claude/, which lanes do not edit (plan 091). Reverses if: a session
   wants /done to reuse the cache - then expose Tick.gate as a CLI verb.
6. The gate result is recorded only when the tree key is unchanged after
   the run (a file edited mid-run would otherwise mark the old tree with a
   mixed verdict). Alternatives: record unconditionally. Why: one extra
   cheap key computation. Reverses if: never.
