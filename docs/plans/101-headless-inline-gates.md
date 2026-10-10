# Plan 101 - Perf 2.9: headless lanes work inline

Status: open (plan doc written by a session, 2026-10-09). Source: ROADMAP
row 101; MAIN ORDER 2246 sec 2 item 9 (perf audit, LOW).

Depends on: none

## Problem

MAIN's perf audit (2246 sec 2.9) found 8 headless runs with at most 3
top-level turns that still took over 600 s: the lane dispatched background
sub-agents inside its own headless run and then sat waiting on them. A
headless lane is already a background worker with its own worktree, its own
governor slot and its own progress file; a nested background agent adds a
second model context, a wait and a reap, and buys nothing.

The lane prompts invite it. `tools/ew_loop.py` `GATES` (appended to every
plan, hand-off and fix prompt) ends with "Background agents share one
scratchpad: name every helper or scratch script after your task
({task}_*.py), never a generic name like prog.py." That clause was written
for interactive sessions (session 9: two background agents clobbered one
prog.py) and, inside a headless prompt, reads as permission to spawn
background agents.

## Design

1. `GATES` drops the "Background agents share one scratchpad" sentence and
   says instead: "Work inline in this run: do not start background
   sub-agents or background shell commands and wait on them; run each step
   in the foreground. Name any helper or scratch script after your task
   ({task}_*.py), never a generic name like prog.py." The helper-name rule
   stays (two lanes can still share a temp dir); only the background-agent
   framing goes.
2. Every other headless prompt builder in `tools/ew_loop.py` and
   `tools/ew_lane.py` that tells a lane to use background agents or
   sub-agents gets the same inline wording (grep for "background",
   "sub-agent", "Agent tool"). The verifier / review prompts may still use a
   read-only verifier sub-agent only where the loop itself does not already
   run one; if none needs it, none keeps it.
3. No change to interactive-session rules (CLAUDE.md, hand-off NOTE about
   unique helper names): those stay as they are.

## Acceptance

- `GATES` (formatted with any task id) contains no "Background agents share
  one scratchpad" text and does contain the inline instruction and the
  `{task}_*.py` helper-name rule.
- `plan_prompt`, `handoff_prompt` and the fix / resolve prompts all carry
  the inline instruction (they all append `GATES`).
- No headless prompt string in `tools/ew_loop.py` / `tools/ew_lane.py`
  tells the lane to start background agents.
- Tests: extend the existing prompt tests (`tests/test_ew_loop*.py`) or add
  `tests/test_headless_inline.py`; targeted tests green, fast tier green.
- Follow-up measure (not a gate): after a day of loop runs, count usage rows
  with <= 3 turns and > 600 s in `headless_usage.jsonl`; expect 0 new ones.

## As-built deviations

- Decision: the edit lands in `tools/loop_prompts.py`, not
  `tools/ew_loop.py`. Alternatives: move `GATES` back into ew_loop.py.
  Why: plan 107 split the prompts out; ew_loop.py re-exports the same
  object, so `ew_loop.GATES` and every prompt builder see the change.
  Reverses if: the prompts move back into ew_loop.py.
- Design item 2: the grep found no other prompt in `tools/` naming
  background agents, sub-agents or the Agent tool; only `GATES` changed.
  Tests in `tests/test_headless_inline.py`.

## Out of scope

- 2246 sec 2.10 (startup check that core.hooksPath resolves) - separate row
  if MAIN still wants it.
- Kit `fleet_headless` flags (vendored; a defect goes to MAIN).
