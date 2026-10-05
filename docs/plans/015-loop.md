# Plan 015 - EW loop tick: inbox, lanes, review, merge, idle deep-dive

Status: built in lane (operator order 2026-10-05). One lane (`build`).

The operator is mostly away (standing order 1). A scheduled, idempotent tick
does what the main session otherwise does by hand: answer the channel inbox,
run open ROADMAP plans through lanes, verify, merge and push, and when nothing
is open, research and propose the next plans. Game ToS floor unchanged: the
loop never touches the game, makes only read-only unauthenticated web calls,
and every headless run goes through the fleet kit.

1. `tools/ew_loop.py tick [--dry-run] [--no-push]` and `checklist`. One tick:
   a. `ops/loop/control/HALT` present -> status `halted`, exit 0. One tick at a
      time via `fleet_watch.watch_lock` (`ops/loop/control/loop.lock`).
   b. Inbox: `loop.inbox_dir` from gitignored `config/local.json` (absent ->
      step "inbox unconfigured"). New notes sorted by mtime, seen-set through
      `fleet_watch.run_source` (baseline first). A note
      `fleet_headless.should_skip` passes gets ONE headless run (sonnet,
      `pick_effort`); the reply goes to `loop.outbox_dir`. Never on EW's own
      notes or TERMINAL / no-reply notes.
   c. Work list: open ROADMAP rows (`[ ]`) plus hand-off items not tagged
      OPERATOR / physical (listed as skipped). Up to 3 lanes
      (`ew_lane.run_lane`, free lanes only, one item per lane per tick,
      governor queued, fail closed). Lane prompt: "implement plan NNN per
      docs/plans/NNN-*.md", the gates, the as-built-deviations rule and "write
      the v7 checklist into your progress JSON".
   d. Finished lane worktree with changes: pytest + node tests there, commit
      (body `refute-rounds: N/3`), review-lane verifier (max 3 rounds, then
      accept and record), merge --no-ff into main, flip the ROADMAP row. Push
      (unless --no-push) only when main is clean, gates green and
      `leak_sweep --pre-push` passes; one push per tick.
   e. Idle (no open item): one deep-dive lane - BDO patch notes, events,
      coupons, data sources, community tools / APIs, public fleet-sibling
      ideas - writes `docs/research/NNNN-deep-dive-<date>.md` and at most
      `loop.max_new_plans_per_day` (default 2) new plans, deduped against
      existing titles (normalized token overlap), each a plan doc + ROADMAP
      row with a value rank.
   f. Spawning stops at `RUNS_CAP - 3` (fleet `RunBudget`); a usage-limit /
      429 result backs off (`ops/loop/control/backoff.json`, 30 min doubling,
      cap 6 h).
   g. Every tick writes `inbox_status.json` (kit `write_status`, `next_tick`)
      and `ops/loop/control/progress/loop.json` with a `checklist` array of
      `[ ] ID: task (state, ~ETA)` lines (ETA from `tools/eta.py`), last line
      `[ ] /done`; ASCII only.
   All side effects injectable; tests never spawn, never touch a git remote,
   never call schtasks.
2. `tools/loop_task.py install|remove|status|run-now`: per-user Task Scheduler
   task `\EbonwakeOps\LaneLoop` every 15 min (TimeTrigger repetition PT15M,
   IgnoreNew, LeastPrivilege), action `pythonw.exe tools/ew_loop.py tick`,
   WorkingDirectory = repo root; status reads back state (Ready / Running /
   Disabled), last run, last result via `schtasks /Query /XML` + `/FO LIST /V`.
   Install is run by the main session, then read back.

Acceptance: gates green; `python tools/ew_loop.py tick --dry-run` lists the
work and writes the checklist; `loop_task.py install` + `status` read back
Ready / PT15M / IgnoreNew; `run-now` produces a tick whose `loop.json`
checklist ends in `[ ] /done`; one push.

## As-built deviations (adjudicated in-lane, 2026-10-05)

1. Lanes run as DETACHED worker processes, not inside the tick.
   Decision: step c records the item (`ops/loop/control/loop_items/<ID>.json`,
   state `dispatched`) and starts `pythonw tools/ew_loop.py lane <ID>`
   (CREATE_NO_WINDOW | DETACHED_PROCESS, CREATE_BREAKAWAY_FROM_JOB when the
   Task Scheduler job allows it). The worker makes the one
   `ew_lane.run_lane` call (kit lane lock + own worktree + one governor slot
   at the spawn) and writes state `ran` with its worktree. A later tick finds
   `ran` records and does step d. Alternatives: threads joined inside the
   tick (one tick would hold the loop lock for hours and the inbox would go
   unanswered); one lane per tick in the foreground. Why: ticks stay short
   and every step is re-runnable. Reverses if: a lane worker is measured
   dying with its tick (job breakaway refused) - then run the worker under
   its own scheduled task.
2. Step d runs BEFORE step c in a tick. Why: the kit claims the lowest free
   lane index and refuses a dirty worktree, so an unprocessed finished
   worktree would block every claim. A worktree whose gates stay red after 3
   rounds is committed as `WIP (gates failed, not merged)` on its detached
   HEAD (state `failed`); the kit saves that commit under
   `refs/fleet-lanes/` when the lane next moves, so the lane is never stuck
   and the work is never lost. Reverses if: the kit gains targeted-index
   claims.
3. Refute round = one verifier FAIL or one red gate, answered by one producer
   fix run (`writes_code`, acceptEdits, governor queued) in the same
   worktree. After round 3 (3 verifies, 3 fixes, never a
   4th verify): state `adjudicate`, WIP committed in the lane worktree and NOT
   merged - the next session's adjudicator rules (refute r1 blocking 1-2:
   the loop never self-accepts, deep-dive cap/dedupe findings block merge).
   Red gates -> WIP unmerged. The lane commit is made
   after review (its body carries the final `refute-rounds: N/3`, the
   verdict and the loop item id), so the count is never amended. The ROADMAP
   row is flipped inside the lane commit (`[x] done <date> (loop; refute N/3
   <verdict>)`), so flip and code land in one merge. The verifier is a
   direct kit spawn with `cwd` = the finished worktree (read-only allow list:
   git diff/status, gates), not a `run_lane` claim (a claim would take a new
   index and refuse the dirty worktree).
4. Hand-off tags. Besides OPERATOR / physical (`operator-only, skipped`), a
   bullet starting `MAIN` is another tree's work (`other-tree-only`) and one
   starting `NOTE:` / `INFO:` is information (`info-only`); both are listed,
   never dispatched. Hand-off ids are `H` + 6 hex of the normalized text, so
   a re-wrapped line keeps its id; each item is dispatched at most
   MAX_ATTEMPTS (2) times. Why: several carried items are records, not work.
   Reverses if: the hand-off gains explicit per-item tags.
5. Inbox: replies are named `<stamp>-from-EW-ANSWER-re-<note stem>.md`; an
   existing `-re-<stem>.md` in the outbox means answered (idempotent across a
   partially delivered batch, since `run_source` re-offers the whole batch).
   Reply text is ASCII-folded, written atomically and re-hashed (1/1
   reached). No governor slot (kit v6 ruling: acknowledgements stay outside
   the slots). Effort = `pick_effort(name)` passed explicitly.
6. Deep-dive: at most one per day (`DD-<yyyymmdd>` item, or an existing
   `*-deep-dive-<date>.md`), dispatched only when nothing is open AND nothing
   is in flight. The lane gets WebFetch / WebSearch on top of the code-lane
   allow list. The plan cap, title dedupe (overlap / min size >= 0.6 over
   lowercase tokens minus stopwords) and the research file are checked in
   step d as extra findings, so a violation costs a refute round.
7. "v7 checklist" is defined here, since no kit v7 exists yet: the FLEET
   item 12 progress fields plus `"checklist": ["[ ] ID: task (state, ~ETA)",
   ..., "[ ] /done"]`, ASCII. `ops/loop/control/progress/loop.json` also
   carries `state` and the tick's `log`.
8. Backoff triggers on a spawn result or Refused text matching usage limit /
   rate limit / 429 / overloaded; a clean run resets it. The kit's own
   RUNS_CAP refusal is not a backoff (headroom stops spawning first).
9. Push = `git push origin main` from the main tree, only when the local
   `origin/main..main` count is above zero (no fetch); leak sweep is fed the
   same ref line git's pre-push hook would get.
10. The task's ExecutionTimeLimit is PT6H (not PT0S): with IgnoreNew a hung
    tick would otherwise stop the loop forever; StartWhenAvailable is true so
    a tick missed while asleep runs on wake.
11. Inbox path (2026-10-05). MAIN delivers by byte-copying notes into
    `<root>/moon_sync_inbox/` (gitignored, fleet convention); the loop read
    only `loop.inbox_dir` from config, which was never set, so every tick said
    "inbox unconfigured" and the v7 and scorecard ORDERs sat unread.
    `load_config` now defaults `inbox_dir` / `outbox_dir` to
    `<root>/moon_sync_inbox` / `<root>/moon_sync_outbox`; config still
    overrides. The first watch state was seeded by hand (baselined, the four
    already-handled notes seen) so the scorecard ORDER is processed instead
    of being swallowed by the baseline.
12. Responder diet (operator 2026-10-05). A note whose name carries
    `-ORDER-`, `-FIX-` or `-RULING-` is ESCALATED: no acknowledgement spawn;
    it becomes a lane work item `N<6 hex>` (queue
    `ops/loop/control/loop_orders.json`, note text in the lane prompt),
    dispatched before ROADMAP rows. Once the item is merged / failed /
    adjudicate, the next tick writes the ANSWER with the item's state,
    verdict, rounds and commit. Every other note gets ONE triage answer,
    sonnet, effort low. At most `loop.max_notes_per_day` (default 12) answers
    per local day; the rest wait for the next day. Pending notes stay unseen,
    so `run_source` re-offers them every tick (idempotent: the outbox
    `-re-<stem>.md` check). ROADMAP rows whose status contains `priority`
    dispatch before other rows.

## Proposed CLAUDE.md rules (EW rules section; land on the operator's own go)

```
## Loop (plan 015) - armed as \EbonwakeOps\LaneLoop, every 15 min

1. The loop tick (`tools/ew_loop.py tick`) is the default executor. A session
   adds work by adding a ROADMAP row (`[ ] open`, `(priority)` to jump the
   queue) or a hand-off bullet; it does not run lanes by hand while the loop
   is armed. Pause with `ops/loop/control/HALT`; never kill a running lane.
2. INBOX is read by every tick, never on operator prompting:
   `moon_sync_inbox/` (default; config `loop.inbox_dir` overrides), replies
   to `moon_sync_outbox/`. ORDER / FIX / RULING notes escalate to a lane
   item and are answered after merge; everything else gets one sonnet
   low-effort triage answer; at most `loop.max_notes_per_day` (12) answers a
   day. A session still reads the inbox at start (FLEET item 5).
3. IDLE DEEP-DIVE: when no ROADMAP row or hand-off item is open and nothing
   is in flight, the tick runs one deep-dive lane per day (BDO patch notes,
   events, coupons, public data sources, community tools / APIs, public
   sibling ideas; read-only, unauthenticated, robots.txt respected), writes
   `docs/research/NNNN-deep-dive-<date>.md` and at most
   `loop.max_new_plans_per_day` (2) deduped new plans + ROADMAP rows, which
   the following ticks then build. The loop is self-continuing.
4. The loop never self-accepts after refute round 3 (state `adjudicate`,
   WIP unmerged); a session's adjudicator rules. Hand-off bullets that are
   records start `NOTE:`; another tree's work starts `MAIN`; operator acts
   start `OPERATOR`.
```
