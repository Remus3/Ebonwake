# Plan 090 - Fleet kit v10: subagent-first hook, headless and inbox fixes

Status: partial (lane-1, order N94ff08). ORDER NOT CLOSED: acceptance is not met
until the main session does items 1, 2, 3, 4 and 5. Merging this lane does not
close the order, and no closing ANSWER goes out on this lane's merge alone. See
ROADMAP "Order N94ff08 - blocked items".

Order items:

1. Vendor kit v10 (17 files) into `ops/fleet_kit/`, re-pin
   `ops/fleet_kit/MANIFEST.json` and `tests/test_fleet_kit_conformance.py`,
   re-embed FLEET COMMON in `CLAUDE.md`.
2. `.claude/settings.json`: PreToolUse hook
   `python ops/fleet_kit/fleet_subagent_first.py` (matcher
   `Bash|PowerShell|Read|Edit|Write|Grep|Glob|NotebookEdit|MultiEdit`,
   timeout 10).
3. Hook mode `log` in the main checkout's `ops/loop/control/subagent_first.mode`
   before item 2 (the hook default is deny); switch to deny after 3 clean
   interactive sessions.
4. `.claude/commands/done.md`: /done dispatched whole to ONE sub-agent; the
   main session relays only its final line.
5. Checklist printing through `fleet_checklist.emit()`; every headless start
   through kit `fleet_headless.spawn`.
6. ONE ANSWER (HOP 2) naming the vendoring commit and MANIFEST sha256.

Done in-lane: `.gitignore` names `subagent_first.mode` and
`subagent_first.jsonl`; the headless half of item 5 is audited (every `claude`
start already goes through `fleet_headless.spawn` in `tools/ew_lane.py`).

## As-built deviations

Refute round 1/3 (verifier FAIL: no kit v10 vendoring, no hook wiring, order
items 1-6 open; `npm test` and leak sweep not run). Grants re-tested in round 1:

- Read of the main checkout (inbox v10 bundle, order note): refused.
- `git worktree list`: refused (approval required).
- The lane brief forbids edits to `ops/fleet_kit/`, `CLAUDE.md` and
  `EW-NEXT-SESSION.txt`.

1. Items 1 and 2 are deferred to the main session.
   - Decision: no edits to `ops/fleet_kit/`, `CLAUDE.md` or the PreToolUse hook.
   - Alternatives: (a) hand-write `fleet_subagent_first.py`; (b) wire the hook
     now; (c) wire it behind an existence guard; (d) defer.
   - Why: (a) breaks FLEET item 11 (kit bytes come only from MAIN's bundle)
     and the brief; (b) a missing script makes python exit 2, and exit 2 on
     PreToolUse DENIES every tool call, which stops all work in every session
     of this tree; (c) rewrites a kit-ordered command line and still has no
     script to call. The bundle is unreadable from this lane.
   - Reverses if: the main session vendors v10 (item 1); it then wires the
     hook exactly as ordered in the same commit, after item 3.
2. Item 3 (mode `log`) is deferred.
   - Decision: the main session writes `log` to the main checkout's mode file.
   - Alternatives: commit the mode file.
   - Why: the file is runtime state under the gitignored `ops/loop/control/*`;
     a merge does not carry it, and the main checkout is unreadable here.
   - Reverses if: kit v10 ships the mode as a tracked default.
3. Item 4 (`done.md` dispatch) is deferred.
   - Decision: the text to add is recorded in ROADMAP "Order N94ff08".
   - Alternatives: edit `.claude/commands/done.md` from the lane.
   - Why: /done must only change with the hook and kit it names; landing it
     before v10 ships a /done that cites helpers not yet vendored.
   - Reverses if: item 1 lands; the main session adds the line in that commit.
4. Item 5 `emit()` switch is deferred.
   - Decision: `tools/ew_loop.py checklist` keeps its current printer.
   - Alternatives: call `emit()` blind with a getattr fallback.
   - Why: `emit()` exists only in v10, its signature is unreadable, and a
     wrong call in the loop's printer gives a silent wrong checklist.
   - Reverses if: v10 is vendored and `emit()`'s signature read.
5. Item 6 (ANSWER) is left to the loop.
   - Decision: the loop sends it with HOP 2 after items 1-2 land.
   - Alternatives: write the outbox note from the lane.
   - Why: it must name the vendoring commit and MANIFEST sha256, which do not
     exist yet; CLAUDE.md loop rule 2 answers orders after merge.
   - Reverses if: never. This is the standing loop contract.
6. Gates finding (`npm test`, leak sweep not run): run in round 1; results
   in the round-1 gate line below.

Gates in round 1: ruff clean; pytest 3482 passed; node 698/698; leak sweep
`--pre-push` ARMED, no hit; ASCII/LF scan of `.gitignore`, `ROADMAP.md`, this
plan and `fix.json`: 0 matches.

Refute round 2/3 (verifier FAIL: findings 1-5 restate order items 1-6 as
open; finding 6 says gate counts were not captured and `npm test` and the
ASCII/LF scan were not run). Grants re-tested in round 2: Read/Glob of the
main checkout inbox (the v10 bundle) refused again.

7. Findings 1-5 (items 1-6 unimplemented) stand as recorded in 1-5 above.
   - Decision: no change in-lane; the lane merges as a PARTIAL that keeps the
     order open, and the ROADMAP "Order N94ff08 - blocked items" row carries
     items 1-6 to the main session.
   - Alternatives: (a) hand-write v10 files; (b) wire the hook or the
     `done.md` / `emit()` changes ahead of the kit; (c) hold the lane
     unmerged until the main session vendors v10.
   - Why: (a) and (b) are ruled out in 1-4 (FLEET item 11, the brief's
     no-edit list, a PreToolUse exit 2 denying every tool call); the v10
     bytes are still unreachable from this lane. (c) blocks the `.gitignore`
     names, which the hook's mode/log files need before item 3 runs; merging
     them early costs nothing and closes nothing.
   - Reverses if: the lane is granted read access to the v10 bundle and the
     brief lifts its `ops/fleet_kit/` / `CLAUDE.md` ban; then items 1, 2, 4
     and 5 land in-lane in one commit.
8. Finding 6 (gate counts): re-run in round 2 with counts captured; see the
   round-2 gate line below.

Gates in round 2: ruff "All checks passed!"; pytest 3482 tests, 0 failures, 0 errors, 0 skipped; node tests
698, pass 698, fail 0; leak sweep `--pre-push` ARMED (19 needles), no hit;
ASCII/CR/drive-letter/email scan of `.gitignore`, `ROADMAP.md`, this plan
and `fix.json`: 0 matches.

Refute round 3/3 (verifier FAIL: findings 1-5 restate order items 1-5 as
unmet; finding 6 proposes the ruling adopted below; gates not re-run by the
verifier). Grants re-tested in round 3: listing the main checkout inbox (the
v10 bundle) required approval again, so it was refused.

9. Round-3 ruling (producer-adjudicated, per CLAUDE.md standing order 7: no
   round 4).
   - Decision: merge as PARTIAL. The order stays OPEN. The main session, not a
     lane, does items 1-5 in one commit (vendor v10 and re-pin MANIFEST and
     conformance test, re-embed FLEET COMMON, write mode `log`, wire the
     PreToolUse hook, add the `done.md` dispatch line, switch to `emit()`).
     The loop then sends item 6 (ANSWER, HOP 2). The ROADMAP row "Order
     N94ff08 - blocked items" carries all of it.
   - Alternatives: (a) hold the lane in `adjudicate` unmerged; (b) a fourth
     refute round; (c) hand-write v10 files in-lane.
   - Why: (b) is forbidden by standing order 7. (c) breaks FLEET item 11 and
     the brief's no-edit list (see 1). (a) blocks the `.gitignore` names that
     item 3 needs and gains nothing, because no lane can reach the bundle.
     The verifier proposed this same ruling in its finding 6.
   - Reverses if: a lane is granted read access to the v10 bundle and edit
     rights on `ops/fleet_kit/`, `CLAUDE.md` and `.claude/`. Items 1-5 then
     land in that lane.

Gates in round 3: ruff "All checks passed!"; pytest 3482 passed, 0 failed;
node tests 698, pass 698, fail 0; ASCII/CR/drive-letter/email scan of
`.gitignore`, `ROADMAP.md`, this plan and `fix.json`: 0 matches.

refute-rounds: 3/3
