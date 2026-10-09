# Plan 091 - Loop: route ORDER items needing kit, CLAUDE.md or .claude/ edits to a session

Status: built (lane, 2026-10-08). Source: order N94ff08 (kit v10) post-mortem.

## Problem

Every lane brief (`GATES` in `tools/ew_loop.py`) forbids edits to
`ops/fleet_kit/` and `CLAUDE.md`, and lanes have no write grant on `.claude/`.
A kit-version ORDER needs exactly those paths, so its lane can only do the
repo-only crumbs, burns three refute rounds against a verifier that (correctly)
says the order is not done, lands in `adjudicate` with a partial WIP commit,
and the loop then answers the order as if a lane had carried it out
(N67971f, N94ff08). The blocked items are blocked by construction.

## Design

1. `session_paths(name, text)` names the protected targets an order touches:
   `ops/fleet_kit/` (also a backslash form and any `kit v<N>` / `kit-v<N>`
   mention, which is a vendoring order), `.claude/` and `CLAUDE.md`. Detection
   is stateless (read from the queued note each tick), so orders queued
   before this plan are routed too.
2. `Tick.work_list`: an order with any hit is NOT a lane item. It goes to the
   skipped list with `skip: "session"` and reason
   `session: needs <targets>`, so it is never dispatched and shows in the
   loop checklist (progress/loop.json) for the phone and the session.
3. `Tick.answer_order`: a session-routed order is answered only after a
   session marks it done (`session_done` on its item record, state
   `merged`). A lane `adjudicate` record no longer counts as done for it.
4. CLI, run in the main checkout by the interactive session:
   - `python tools/ew_loop.py session` lists pending session orders, one
     line each: `<id> <note> needs: <targets>`; `none` when empty.
   - `python tools/ew_loop.py session-done <id> [--commit SHA]` records the
     order done by the session (state `merged`, verdict `session`,
     `session_done: true`, commit). The next tick answers it (HOP n+1, one
     note, counted by OutboundCap). Unknown id: exit 2, nothing written.
     Re-running is a no-op that keeps the first record (idempotent).

## Acceptance

- A queued ORDER naming `ops/fleet_kit/`, `.claude/`, `CLAUDE.md` or a kit
  version is not dispatched; the checklist shows it with `session: needs ...`.
- An order naming none of them is dispatched to a lane as before.
- A session-routed order with an `adjudicate` lane record is not answered;
  after `session-done` the next tick answers it once, naming the commit.
- `session` lists pending ones; `session-done` on an unknown id exits 2.
- Gates: ruff, pytest, `npm test --prefix app` green; ASCII + LF.

## As-built deviations

1. Decision: route the WHOLE order to the session, not only its protected items.
   - Alternatives: (a) grant the lane those paths for that order (drop the
     brief's ban, add `.claude/` write); (b) split the order: lane does repo
     items, session does the rest.
   - Why: (a) a headless lane wiring a PreToolUse hook can deny every tool
     call in every session of this tree, and the kit bundle sits in the main
     checkout inbox a lane cannot read; FLEET item 11 also wants kit bytes
     copied by the tree's session from MAIN's bundle. (b) orders are not
     machine-split into items reliably, and the partial lane merge is what
     produced the N94ff08 mess; kit orders are small once the session holds
     the bundle.
   - Reverses if: lanes get a read grant on the main inbox and a hook-safe
     settings write path, or orders arrive pre-split into lane/session items.
2. Decision: detection biased to false positives (any mention of a target).
   - Alternatives: require an edit verb near the path.
   - Why: a false positive costs a session a few minutes; a false negative
     costs three lane refute rounds and a wrong ANSWER.
   - Reverses if: more than two ordinary orders a week land on the session.
3. Decision: the session surface is the CLI plus the loop checklist; no edit
   to CLAUDE.md, `.claude/commands/` or the hand-off from this lane.
   - Alternatives: write a hand-off bullet from the loop.
   - Why: the lane brief forbids those files and /done owns the hand-off.
   - Reverses if: the main session adds `ew_loop.py session` to the session
     start checklist (recommended, a one-line CLAUDE.md EW-rules change).

Gates (lane, 2026-10-08): ruff clean; pytest 3486 passed (4 new in
`tests/test_ew_loop.py`); node 699/699; ASCII/LF scan of the three changed
files: 0 matches.
