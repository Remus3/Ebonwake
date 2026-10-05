# Plan 015 - EW loop tick: inbox, lanes, review, merge, idle deep-dive

Status: LANDED 2026-10-05 (operator order 2026-10-05): armed as
`\EbonwakeOps\LaneLoop` (PT15M, IgnoreNew); rules in CLAUDE.md "Loop (plan
015)". One lane (`build`).

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
13. SUPERSEDED 2026-10-05 by 14 (kit v8 vendored: 13a's reversal condition
    met, `tools/ew_inbox.py` and its test removed; 13c and 13d reversed, see
    14a and 14d). Kept as the record. Kit v8 inbox cost discipline (MAIN ORDER
    2026-10-05, FLEET-COMMON item 14; lane item N715c44). The tick already reads the inbox on every fire
    (14 rule 1: no separate responder exists, none disabled). New
    `tools/ew_inbox.py` does the item-14 rules: `classify` -> skip (own /
    TERMINAL / no-reply) | ack (ACK / INFORMATION / TERMINAL / ANSWER class,
    or `HOP:` at the 2-hop limit: a line in
    `ops/loop/control/inbox_ledger.jsonl`, never a note) | work (ORDER / FIX /
    RULING -> lane item, as deviation 12) | triage (ONE spawn, sonnet, effort
    low, bare, kind `triage`; `VERDICT: NOREPLY|ACK` -> ledger line only,
    `ANSWER` -> reply). Replies carry `HOP: <incoming + 1>`; several answers
    to one destination in one tick go in ONE batch note
    (`-ANSWER-to-<TO>-batch-<6 hex>.md`). Outbound cap 6 a local day
    (`ops/loop/control/outbound_cap.jsonl`; config may lower, never raise;
    the default fell from 12). Lane, review and fix spawns pass kind `build`,
    the order-closing answer kind `inbox`.
    a. Local module, not the kit's `fleet_inbox`. Decision: implement the
       rules in `tools/ew_inbox.py` now. Alternatives: wait for the vendored
       v8 kit (inbox keeps costing until a session vendors it); copy the
       bundle (unreadable from the lane, and `ops/fleet_kit/` is the main
       session's to replace in one commit). Why: the cost rules take effect at
       the next tick. Reverses if: kit v8 is vendored - then the tick calls
       `fleet_inbox` directly and `tools/ew_inbox.py` keeps only what the kit
       lacks.
    b. `kind=` is passed only when the kit's `spawn` accepts it
       (`ew_inbox.with_kind` inspects the signature): kit v6 rejects unknown
       keywords, v8 takes them. Reverses if: never (no-op once v8 lands).
    c. The answer that closes an escalated ORDER / FIX / RULING is exempt from
       the daily cap (recorded `exempt` in the cap ledger). Alternative: count
       it as an ANSWER. Why: MAIN's orders demand that answer (its drift sweep
       reads it) and the cap's purpose is chatter, not order closure; the
       note's own rule 0 forbids a handled note waiting on anything. Reverses
       if: MAIN rules order answers count.
    d. A triage result with no `VERDICT:` line is treated as ANSWER (handled,
       never dropped; rule 0). Reverses if: the kit's `parse_verdict` defines
       otherwise.
14. Kit v8 inbox cost on the vendored kit (MAIN ORDER 0310, FLEET-COMMON
    item 14; kit vendored 2026-10-05; replaces 13). The tick calls the kit's `fleet_inbox` directly:
    `classify()` first (free); skip / ack = one `mark_seen` line in
    `ops/loop/control/inbox_seen.jsonl`, never a note; ORDER / FIX / RULING ->
    lane item (deviation 12); anything else ONE spawn with
    `**fleet_inbox.TRIAGE_SPAWN` (sonnet, effort low, bare), `kind="triage"`,
    `triage_prompt()`, `parse_verdict()` (NOREPLY / ACK = ledger line only).
    ANSWERs to one destination in one tick go in ONE `batch_note` (HOP:
    incoming + 1); the order-closing answer is one note `-re-<stem>.md` with a
    `HOP:` line under its title, `kind="inbox"`. Every outbound note is
    checked with `OutboundCap.allow` and recorded (`outbound_notes.jsonl`);
    `loop.max_notes_per_day` defaults to 6 and config can lower it, never
    raise it. Lane, review and fix spawns pass `kind="build"`. No separate
    responder task exists; none disabled.
    a. Order-closing answers COUNT against the cap (kit-literal: only notes
       whose own class is ORDER / FIX / RULING are exempt). Alternative: exempt
       them. Why: MAIN's drift sweep reads the ledger with the kit's rule; 6 a
       day is far above EW's order rate. Reverses if: MAIN rules order answers
       exempt.
    b. Root cause of `inbox: deliver-failed 2 2 pending` (loop.json
       2026-10-05 03:15): the two pending notes were ORDERs waiting for their
       lane items - pending by design, mislabelled as a delivery failure. And
       because `fleet_watch.run_source` re-offers the WHOLE batch while any
       note is pending, every other note in it was re-processed each tick (a
       triage-ACK note would be re-triaged, one paid spawn per tick). Fix: the
       kit seen ledger short-circuits handled notes, and the step reads
       `inbox: pending N new; pending: A order(s) awaiting lane, C capped, P
       paused, F failed` (`deliver-failed` only when F > 0). Escalation is
       `classify()`'s WORK decision only: a filename that quotes an order's
       name (an ACK / ANSWER re- it) is not escalated (verifier r2). Tests:
       `test_pending_orders_are_not_delivery_failures_and_handled_notes_stay_handled`.
    c. FLEET item 13 d (kit v7, rides with v8): loop.json "checklist" is now
       kit rows `{id, task, state, eta_s}`, remaining tasks only, at most 20,
       built with `fleet_checklist.item`; open rows carry no state (no
       parenthesis); "fire" counts ticks and is the block's session number;
       `ew_loop.py checklist` prints the kit block. Lane prompts ask for the
       same row shape. Lanes still write `progress/lane-<name>.json`, not
       `lane-<i>.json`. Reverses if: MAIN's lane widget needs the index name -
       then `ew_lane.run_lane` writes `fleet_checklist.lane_task(claim index)`.
       REVERSED by 17.
    d. A triage result with no `VERDICT:` line is ACK (kit `parse_verdict`:
       a triage that cannot decide never generates a note; the note is
       marked seen, so it is handled, not dropped). Reverses 13d. Reverses
       if: MAIN changes the kit.
    e. Migration: `handled()` also reads 13's ledger
       (`ops/loop/control/inbox_ledger.jsonl`) and the outbox `-re-<stem>.md`
       names, so a note settled by the 13 code is never re-triaged; 13's cap
       ledger (`outbound_cap.jsonl`) is not read - at worst one extra note on
       the day of the switch. Reverses if: never (read-only shim).
15. Verifier r1 minors (hand-off item, 2026-10-05).
    a. Lane worker re-checks HALT / backoff / runs cap (`spawn_block`, shared
       with the tick) before `run_lane` and passes `halt_file` to every
       spawn. A block, or a kit refusal while HALT exists, sets state
       `paused` and gives the attempt back; `paused` is always dispatchable.
       Alternative: `refused` (burns one of the 2 attempts on a pause). Why: a
       pause is not a failure. Reverses if: never.
    b. A verifier run with rc != 0 that is not a usage limit counts
       `verify_errors` (separate from refute rounds: no verdict was given);
       at MAX_ROUNDS (3) the item goes to `adjudicate`, WIP unmerged.
       Alternative: count it as a FAIL round and run a fix (a fix against a
       crash has no findings to fix). Reverses if: verifier crashes are
       measured to be transient enough that 3 is too few.
    c. A `_launch` exception marks the item `lost` (error kept) and frees the
       lane for the next item; `reap_lost` also reaps a `dispatched` record
       with no pid (safe: it runs under the tick lock, so no launch is in
       flight).
    d. A refused / lost item out of attempts becomes `gave-up` (attention
       state, error kept, logged). A claim refused for a dirty lane worktree
       is `lane-dirty` at once (attention state, never retried). Alternative:
       retry on another index. Why: the kit claims the lowest free index, so a
       retry hits the same dirty worktree and burns the attempt; a person
       resolves the crashed run's work (kit rule: never cleaned). Reverses
       if: the kit gains targeted-index claims.
    e. Hand-off items naming a password, OAuth or per-host value are tagged
       `operator` (FLEET item 1). `config/local.json` alone is not a tag (a
       lane may change code that reads it). Reverses if: the hand-off gains
       explicit per-item tags.
    f. Checklist ETA = median of `lane-<lane>-code` (what `ew_lane.run_lane`
       records; `lane-build-code` for an item not yet on a lane) less the
       time since dispatch for dispatched / ran / committed items, floored at
       0; attention states 0. `loop-review` was never recorded (always the
       60 s default).

16. Gates = ci, and ROADMAP rows are flipped in main at merge (fix-0130,
    2026-10-05).
    a. Pre-merge gates run exactly the `run:` commands of the tree's own
       `.github/workflows/ci.yml` (in order, minus `pip install` setup):
       action-pin check, `python -m ruff check server tools tests` (repo
       `ruff.toml`), pytest, `npm test --prefix app`, `leak_sweep --tree`. No
       ci.yml or no command = gates fail closed. Why: f5f8857 merged a lane
       whose unused import (ruff F401) ci rejected; the loop gated on pytest +
       node only. Alternatives: hard-code ruff into the old list (drifts the
       next time ci gains a step). Reverses if: ci gains a step that cannot
       run on this host - then that step is skipped by name, with a test.
    b. Merge conflicts: 0e4393f (Nfa7953), 013 and 014 all conflicted in
       `docs/plans/ROADMAP.md`. Root cause: `commit()` flipped the item's row
       to `[x] done` INSIDE the lane commit, on a worktree cut from an older
       main; parallel lanes flip ADJACENT rows of one table, which git always
       reports as one conflicting hunk. Decision: (1) the lane commit never
       touches ROADMAP.md - `merge()` runs `merge --no-ff --no-commit`, flips
       the row in main, and commits once; (2) plan and hand-off lane prompts
       say "Do NOT edit docs/plans/ROADMAP.md"; (3) a conflict whose ONLY
       unmerged path is ROADMAP.md is resolved row by row
       (`resolve_roadmap`: rows keyed by plan id; a row only the lane changed
       or added takes the lane's line, a row both sides changed keeps main's,
       prose changed on both sides is not guessed -> still `merge-conflict`).
       (3) covers deep-dive / order lanes, which legitimately add rows, and
       lanes already in flight with the old flip. Alternatives: rebase the
       lane commit onto main before merging (still conflicts on adjacent
       rows; rewrites the verified commit); `merge=union` gitattribute
       (duplicates both versions of a flipped row); one ROADMAP file per plan
       (churns every reader of the table). Why: removes the collision at its
       source with no new file layout, and the fallback never drops a side's
       row. Real code conflicts (014 vs 012 in `server/ew/app.py`) still
       abort to `merge-conflict` for a session. Reverses if: a ROADMAP row
       is measured lost or duplicated by the resolver.
17. Loop gate limits (hand-off He6bbd0, 2026-10-05). (a) The loop runs the
    gates on ONE local Python (`_python()`); ci runs the 3.11 + 3.14 matrix.
    A version-only break is caught by ci after push, not before merge.
    Accepted, no code: installing and switching interpreters per tick costs
    minutes per gate run for a class of break not yet seen. (b) Gates run as
    argv without a shell (`shlex.split`), so a ci step using `&&`, `||`, `|`,
    `;` or a redirect would hand the operator to the first program as an
    argument and silently skip the rest - the loop could merge what ci
    rejects. None exists today; `_gates` now fails closed on any unquoted
    shell operator (`_shell_operator`, test
    `test_loop_gates_fail_closed_on_shell_operators`). Alternatives: run
    steps through `bash -c` (no bash guaranteed on this host; Git Bash path
    is per-host); split `a && b` into two gates (wrong for `||`, pipes,
    redirects). Why: fail closed is the fix-0130 posture and costs nothing
    while ci has no such step. Reverses if: ci gains a step that needs a
    shell - then that step is rewritten as a `tools/*.py` script, or the loop
    gains a per-host shell, with a test.

17. Lane progress file = `progress/lane-<i>.json` (hand-off item Ha1f38b,
    2026-10-05; reverses 14c). `ew_lane.run_lane` writes
    `fleet_checklist.lane_task(claim["index"])` under the MAIN checkout
    (`fleet_lanes.main_tree(root)`), never inside the lane worktree; the
    step text starts `<lane name>: ` so the widget shows the lane name. No
    progress file before the index is claimed: a dry run or a refused
    claim writes none (no live lane to show). The kit claim `run_id` stays
    `lane-<name>` (exclusive-name check, usage note label, ETA kinds
    `lane-<name>-code|read` unchanged). Alternatives: keep `lane-<name>`
    (14c; contradicts the byte-pinned FLEET item 13 d, which the widget
    reads); write both names (two files per lane, the stale one never
    cleared); write a pre-claim `lane-<name>` file then switch (same
    staleness). Why: FLEET item 13 d is the kit rule and one named file per
    live lane is exactly the lock index. Reverses if: MAIN ships a kit
    version naming lane progress files otherwise.

## CLAUDE.md rules - LANDED 2026-10-05 (CLAUDE.md "Loop (plan 015)"; text below is the proposal, CLAUDE.md is authoritative: cap 6, not 12)

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
