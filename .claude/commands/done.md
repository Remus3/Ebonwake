---
description: End-of-session ritual - gate, commit, push (fires CI), paperwork while CI runs, collect CI, rewrite EW-NEXT-SESSION.txt carrying forward every unacted item, then print ONLY "Done ritual complete, safe to clear".
---

> **SUBAGENT-FIRST.** Orchestrated + multi-agent + self-adjudicating + self-adversarial
> is the default shape. Producer never grades its own work; a `verifier` agent
> confirms. Refute rounds are CAPPED AT 3 per item, then the `adjudicator` rules.

> **CHAT OUTPUT CONTRACT (FLEET items 2, 3, 5).** /done's ONLY chat output is the
> single line `Done ritual complete, safe to clear`, or one line
> `/done stopped: <reason>`. No banner, recap, checklist, hand-off or
> next-session prompt in chat. Everything else goes into `EW-NEXT-SESSION.txt`.
> Long steps (suites, CI watch) run as background commands with an ETA from
> `python tools/eta.py estimate <kind>`.

Shape: **Phase 1** fast local gate - **Phase 2** commit + push (fires CI) -
**Phase 3** paperwork while CI runs - **Phase 4** collect CI, write the hand-off,
print the one line. Every step is idempotent: re-running /done after a partial
run finishes the remaining steps and repeats nothing destructive.

### 0. Local gate - commit only when green

- Pre-flight (FLEET item 13): every session-checklist task is done or carried
  into the hand-off. /done runs unprompted once no checklist task remains.

- `git -C <repo> status -s` - identify this session's files.
- `python tools/eta.py run pytest -- python -m pytest -q` (records its own time).
- `python tools/eta.py run nodetest -- npm test --prefix app`.
- `python tools/leak_sweep.py --tree` - must print no HALT. A DEGRADED banner is
  expected without `config/leak_needles.json`; with it, the sweep must be ARMED.
- Run fresh this turn; never carry a sub-agent's green forward.
- RED: fix and re-run. A pre-existing unrelated red goes into the hand-off as the
  first next action; never commit over a regression you introduced.

### 1. Commit (batched, coherent)

- Refuse to stage `config/local.json`, `config/leak_needles.json`, `.env*`,
  `*.key`, `*.pem`, anything under `ops/runtime/`, or anything that looks like a
  credential. Secrets live in user env vars only (standing order 12).
- Stage named files only (`git add <files>`), never `git add -A`.
- One commit per coherent theme; body records adjudicated decisions
  (decision / alternatives / why) and `refute-rounds: N/3` per reviewed item.
- No `Co-Authored-By` trailer (the commit-msg hook strips it).
- Hook failure: fix, new commit. Never `--amend`, never `--no-verify`.
- Lane worktrees: merge each finished lane branch into main here (fast-forward or
  a merge commit), then reset the lane branch to main. Unfinished lanes stay on
  their branch and are listed as in-flight in the hand-off.

### 2. Push (one push per batch)

- `git -C <repo> log @{u}.. --oneline`; if non-empty, `git push origin main`.
- The pre-push hook runs the leak sweep over the pushed range; a HALT means fix
  the bytes (never print the matched text), re-commit, re-push.
- Record the push range in the hand-off. Push failure: `/done stopped: push failed - <cause>`.
- Note the CI run id: `gh run list --branch main --limit 1`. Do not block on it.

### 3. Background work

- Monitors this session armed: stop them. Running agents / lane runs are NOT
  killed (FLEET banner); record each as in-flight with its progress file
  (`ops/loop/control/progress/<task>.json`) and ETA.

### 4. Paperwork (while CI runs)

- `docs/plans/ROADMAP.md`: flip finished plans/slices, add new open items.
- Finished plan docs: mark done in place.
- New settled decisions: one line in CLAUDE.md "Settled" with a `Reverses if:`.
- `ops/loop/control/timings.jsonl` is runtime (gitignored); nothing to commit.

### 5. Session size

- Find the newest session jsonl under the Claude projects directory for this
  checkout; over 10 MB = note "clear overdue" in the hand-off; over 20 MB = first
  line of the hand-off.

### 6. Collect CI

- `gh run watch <id> --exit-status` as a background command with ETA
  `python tools/eta.py estimate ci`; record the duration with
  `python tools/eta.py record ci <seconds>`.
- Red = the first next action in the hand-off ("resolve CI <job>"). A 2-3 second
  "job was not started" failure is billing, not code.

### 7. Hand-off - ALWAYS, never skipped

1. READ the current `EW-NEXT-SESSION.txt`.
2. Every item in it: acted on (done or retracted with a reason) or not. Every
   unacted item is copied forward verbatim or tighter. Never drop one.
3. Add this session's items from ground truth (ROADMAP top open item, blockers,
   in-flight lanes, status figures).

```
EW NEXT SESSION
---------------
SESSION: <n+1>   (FLEET item 13 counter: the previous SESSION line plus one)
Next action: <the one thing "continue" should work on>
Carried forward (not acted on yet): <every unacted item>
Context: <files / endpoints / live state to probe first>
Acceptance: <how the next session knows it is done>
Do NOT redo: <shipped this session that still looks open>
In flight: <lane / agent, progress file, ETA>
Status: <commits / push range / gates / CI / server health / session size>
Start with: read this file, then CLAUDE.md, docs/plans/ROADMAP.md, git log -10.
```

Overwrite the whole file, read it back (the read-back is the recorded act),
commit it, push it (same batch if CI has not started, else one more small push
is acceptable for the hand-off only).

### 8. Done marker, then the final output - ONE line

The LAST act of /done (kit v9, FLEET-COMMON item 15), after the commit and the
hand-off are READ BACK and immediately before the chat line:

    python ops/fleet_kit/fleet_done.py mark --session <n> --status done

`<n>` is the `SESSION:` number this session worked under (the hand-off now
carries n+1). Any step that stops /done still calls it, as its last act:

    python ops/fleet_kit/fleet_done.py mark --session <n> --status failed --reason "<step>"

Then print `Done ritual complete, safe to clear` - or `/done stopped: <reason>`.
Nothing else. The project Stop hook (`fleet_done.py stop-hook`) turns the
marker into the tab title; never run it by hand.

### Safety rails

- Never force-push, `--amend`, or `--no-verify`.
- Never commit secrets or runtime state.
- Never run `/clear` yourself.
- Never touch the BDO client in any step (ToS floor in CLAUDE.md).
