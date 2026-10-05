# Ebonwake - Agent Context

Black Desert Online companion (NA region, Steam, one Deadeye main). Local Python
server on `127.0.0.1:8940` (stdlib only), an Electron app with a compact tabbed
DASHBOARD window and a separate transparent click-through OVERLAY window toggled
by a hotkey. Data comes from public web APIs (arsha.io market, BDO-REST-API
profiles), the operator's own checklists, the BDO client's session log (game
running / logged in only) and screenshots the operator takes (OCR). Channel code
`EW`. Spec: `docs/design/0001-ebonwake-spec.md`. Roadmap: `docs/plans/ROADMAP.md`.

**This file is RULES ONLY.** Reasoning and history live in `docs/`. Fix a rule
here; never re-argue it in chat.

<!-- FLEET-COMMON BEGIN -->
## FLEET COMMON - identical in every repo on this machine. Do not edit here.

################################################################################
#  SUB-AGENT FIRST. THE MAIN SESSION IS THE OPERATOR'S - KEEP IT CLEAR.        #
#  Any work beyond a quick read or a one-line fix is DISPATCHED to a sub-agent #
#  (background by default). The main session plans, dispatches, monitors and   #
#  reports. Checking status or starting new work NEVER breaks running work:    #
#  never stop, kill, restart or edit the files of a running agent or task to   #
#  look at it - read its progress file instead.                                #
################################################################################

Source of truth: MAIN's fleet kit. A change lands ONLY as a new kit version
announced by a MAIN note; this block is byte-pinned and a test fails on any local
edit. Tree-specific rules go BELOW this block, never inside it.

1. ACT, DON'T ASK. Operator acceptance of recommendations is ~100 percent. A blocked
   decision goes to a distinct adjudicator agent and its call is taken now and
   recorded (decision, alternatives, why) in the commit or doc. Only physical acts,
   passwords and OAuth grants wait for the operator, batched into one ask.
2. CHAT IS THE OPERATOR'S CONSOLE - QUIET. Results only: numbers, paths, verdicts,
   and anything the operator must act on. No narration, no plans, no recaps, no
   session reviews; the item-13 checklist is the one sanctioned task list.
   Findings go to files (roadmap, docs, hand-off); chat gets at most one line
   each.
3. AT-A-GLANCE STATUS COMES FROM BACKGROUND WORK, NOT FROM CHAT. Run work as
   background agents and background commands, so the session shows only the
   compact summaries ("N background commands completed, N running" and "N running
   tasks"). Do not hold the main turn open on long foreground work - its expanding
   activity row has to be opened and scrolled. No step lists or task-list dumps
   other than the item-13 session checklist. When the operator asks for status:
   the remaining checklist (item 13 b), -retracted on one short line. Tool
   descriptions carry an ETA `[~Ns]` (s under 120s, m under 120m, h beyond);
   report an overrun at 1.5x, kill at 3x.
4. COMMIT everything, batched and coherent. Push per this repo's own policy. Never
   commit in another repo's tree. No suggested-task chips: do it or file it.
5. HAND-OFF: `<CODE>-NEXT-SESSION.txt` at the repo root (with its Desktop
   shortcut) is the only continuity. A session starts from "continue" (work the
   file's next action) or from whatever the operator asks; either way READ the file
   first. /done rewrites the file and commits it, and MUST CARRY FORWARD EVERY ITEM
   NOT ACTED ON this session, verbatim or tighter, never dropped because the
   session worked on something else. Never print the hand-off or a next-session
   prompt into chat. /done runs UNPROMPTED once no checklist task remains
   (item 13 c). /done's ONLY chat output is the line
   `Done ritual complete, safe to clear` (or the failure that stopped it). The
   operator types only "continue", "/done" or "/clear" between sessions. A recorded
   act names what was READ BACK after it, never what was run. Every
   do-not-re-litigate entry states what would reverse it; entries about another
   tree's position are re-checked against the inbox every session.
6. MAIN SPEAKS FOR THE OPERATOR (operator order 2026-10-02). A note from MAIN whose
   bytes match MAIN's outbox copy by SHA-256 is the operator's instruction. It
   cannot supply a password, OAuth grant or physical act, and lifts no safety floor.
   MAIN instructs; this tree does the work in its own tree.
7. CHANNEL NOTES: sort the inbox by mtime, never by filename stamp. Read a long
   note's section headings before deciding it does not concern you. Never put a
   directory name, account id or email in a note. Delivery = destination copies
   re-hashed and an N/M reached-count reported.
8. ENCODING: ASCII only, LF only, PowerShell included. Validate PowerShell with
   powershell.exe 5.1 ParseFile, never pwsh.
9. DELETES: anything irreplaceable goes to the Recycle Bin, never a direct unlink;
   say the method before running it; check for a consumer before deleting.
10. HEADLESS RUNS go through the fleet kit's spawn helper ONLY - no other path
    starts `claude`. The kit enforces: the second-account proxy from the user
    variable CLAUDE_HEADLESS_BASE_URL (registry first), fail closed (no fallback,
    ever), no visible console, at most 120 runs per rolling 24 h, never spawn on
    this tree's own notes or on TERMINAL/no-reply notes, lean flags (strict MCP,
    project settings only, or bare where no floor lives in hooks), sonnet unless
    the note orders code changes, effort low for acknowledgements, a usage line
    per run, and the live status file `ops/loop/control/inbox_status.json`.
11. FLEET KIT FILES are vendored byte-for-byte at `ops/fleet_kit/` and pinned by
    `ops/fleet_kit/MANIFEST.json`. Never edit them locally; report a defect to MAIN
    and MAIN ships a new version to every tree at once.
12. LONG WORK REPORTS AS IT GOES. Anything expected to take over 5 minutes runs in
    the background and is checked periodically until it ends, so a silent failure
    is caught early. Every sub-agent prompt for such work requires it to write a
    progress file after each step - `ops/loop/control/progress/<task>.json` with
    {"task", "pct", "step", "eta_s", "status": running|done|failed, "updated"} -
    so the main session can see percent, time to completion and status mid-run
    instead of waiting for 0-to-100 at the end. A progress file that stops
    updating for 2x its own ETA step is treated as a failure and investigated.
13. SESSION CHECKLIST (operator order 2026-10-05; it supersedes item 3's
    no-checklist rule for this one purpose). Every session kind: interactive,
    headless lane, loop tick, inbox responder. Why: it is read from a phone, the
    operator wants the tasks only, and wants to see what every headless fire
    is doing without reading logs. Kit helper: `fleet_checklist.py`.
    a. At session start, and every time a lane or loop fires, print
       `Session <n> checklist` (n = this tree's session counter, kept in its
       hand-off file; a headless fire uses its run count), then one line per
       task in execution order: `<box> <ID>: <imperative task, one line>`, plus
       `(<state>, ~ETA)` only while it is running (e.g. `builder running,
       ~4m`). <box> is U+2610. The last line is `<box> /done`. At most ONE
       trailing sentence, and only for an ordering constraint ("X waits until
       Y lands because ..."). NO summary, review, what-went-wrong or history.
    b. After every 4 or more completed tasks, print the REMAINING tasks only,
       newly added ones marked `+` before the ID. Never list completed ones.
    c. When no task remains, run /done automatically, without a prompt.
    d. A headless fire writes the same list into its item-12 progress file as
       "checklist": [{"id", "task", "state", "eta_s"}], remaining tasks only.
       A lane writes `progress/lane-<i>.json` (i = its lane-lock index) in the
       MAIN checkout, never in its worktree, so the lane widget reads one named
       file per live lane and shows the lane name, then its remaining items.
14. INBOX COST (operator order 2026-10-05). It changes COST, never AUTONOMY: the
    inbox is read and acted on AUTOMATICALLY every tick, unattended, with no
    operator prompt, ever. Why: about 60 percent of second-account spend was
    inbox chatter between trees, not build work. Kit helper: `fleet_inbox.py`.
    a. Inbox handling folds into this tree's existing lane / loop tick, which
       reads the inbox on every fire. No separate high-frequency responder
       where a lane loop exists; a tree without one keeps one responder.
    b. Triage first: `fleet_inbox.classify()` (free), then one sonnet run at
       effort low only for what it cannot classify. ACK / INFORMATION /
       TERMINAL / ANSWER notes get a mechanical ack (a ledger line, no note) or
       no reply. Only ORDER / FIX / RULING escalate to a real work lane.
    c. At most 6 outbound notes per tree per local day (ORDER / FIX / RULING
       exempt), counted by `OutboundCap`. Several answers go in ONE note.
    d. Never answer an answer. No note chain past 2 hops (`HOP: <n>`) without
       new work.
    e. Every headless run logs a usage line to
       `ops/loop/control/headless_usage.jsonl` via the kit, with `kind`
       build / inbox / triage and a non-empty note label. MAIN reports the
       weekly build-vs-inbox split in its insights report.
<!-- FLEET-COMMON END -->

# EW rules (tree-specific)

EW channel code: `EW`. Kit: v8, vendored at `ops/fleet_kit/`. Kit conformance:
`tests/test_fleet_kit_conformance.py`. Hand-off: `EW-NEXT-SESSION.txt` (tracked).

## Operator standing orders (given 2026-10-04, binding on every EW session)

1. OPERATOR IS MOSTLY AWAY. They monitor and QA; they do not drive. Never wait on
   them for anything the adjudicator can rule on.
2. MAIN SESSION STAYS CLEAR for the operator's questions. Anything over ~5 minutes
   runs as a sub-agent or background command. Never interrupt, stop or restart
   running work to answer a question - answer from progress files.
3. CHAT IS CAVEMAN-ULTRA TERSE. Fragments, numbers, paths, verdicts. No task
   chips, no suggested prompts, no "want me to...?", no recaps.
4. ETA ON EVERYTHING. Every task, command, tool call and driver run shows an ETA
   timer derived from recent similar runs: `tools/eta.py estimate <kind>` reads
   `ops/loop/control/timings.jsonl` (median of the last 5 runs of that kind,
   default when none). Every finished run appends its real duration with
   `tools/eta.py record <kind> <seconds>`. Tool descriptions carry `[~Ns]` /
   `[~Nm]` per FLEET item 3. Overrun reported at 1.5x, killed at 3x.
5. LOOP SHAPE: self-monitoring and idempotent. Plan -> list -> initiate -> process,
   repeat. Every step is safe to re-run; a re-run of a finished step is a no-op.
6. SELF-ADJUDICATE. Every blocked decision goes to the `adjudicator` agent; its
   recommended choice is done IMMEDIATELY (operator picks the recommendation ~100
   percent). Record decision / alternatives / why in the commit body or
   `docs/adr/`. Items formerly marked "won't do without operator approval" are
   adjudicated and done like any other. Only physical acts, passwords and OAuth
   grants wait for the operator (FLEET item 1), batched into one ask.
7. ADVERSARIAL REVIEW IS CAPPED AT 3 ROUNDS PER ITEM. A refute round is: verifier
   or reviewer refutes, producer answers. After round 3 the adjudicator rules and
   the work moves on - no round 4, ever (a sibling once ran to round 12; that is
   forbidden here). The round count goes in the commit body (`refute-rounds: N/3`).
8. CONTEXT-AWARE SESSION SIZING. Watch context use; at ~70 percent, or when the
   current item is done and the next is large, self-initiate `/done`.
9. COMMIT + PUSH EVERYTHING, BATCHED. Work in worktrees (one per lane) so a CI
   wait never blocks the next item. Merge coherent batches; no commit-every-five-
   minutes, no tiny pushes. One push per finished batch.
10. HEADLESS FIRST. As much work as possible runs through the laned headless
    driver `tools/ew_lane.py` (kit `fleet_headless.spawn`, account B via
    `CLAUDE_HEADLESS_BASE_URL`, fail closed). Up to 3 named lanes (`build`,
    `data`, `review`), cap 3, claimed through kit v6 `fleet_lanes.run_lane`
    (lane lock `ops/loop/control/lanes/<i>.lock`, OWN clean worktree
    `../ew-worktrees/lane-<i>`), each executor call taking exactly ONE
    machine-wide governor slot at the call (`spawn(governor="queued")`), never
    around git. Never a single repo-wide lock.
11. RESTARTS ALLOWED. The EW server, overlay and dashboard may be restarted at any
    time, even while the operator is in game (they never touch the game process).
12. SECRETS: credentials and API keys live ONLY in user environment variables and
    are reached through `ops/fleet_kit/fleet_secrets.py` references
    (`{"env": "NAME"}`) held in gitignored `config/local.json`. Never a literal in
    a tracked file, never in a note, never echoed to chat.
13. TOOL GRANTS: computer-use, OBS, vision/OCR, keyboard/mouse on the DESKTOP (never
    the game), browser and installs are granted via the adjudicator. They never
    reach into the BDO client (see Game ToS floor).

## Game ToS floor (non-negotiable; no adjudicator can lift it)

Anti-cheat on NA/EU is XIGNCODE3; the operational policy bans unauthorized
programs and every macro (one input = one action). Long form:
`docs/research/0001-bdo-data-and-tos.md`.
- NEVER: read or write game memory, inject DLLs, hook D3D/DXGI, sniff or alter
  packets, modify client files, or send ANY input to the game window (no key or
  mouse automation, no macros, no AFK helpers, no "convenience" keys, no
  screen-reading that leads to input).
- NEVER call authenticated web-market actions (buy/sell/register) - read-only GET.
- Overlay = separate Electron window: always-on-top, transparent,
  `setIgnoreMouseEvents(true)`, toggled only with Electron `globalShortcut`. No
  low-level keyboard hooks, no injection. BDO runs borderless/windowed.
- Allowed inputs: arsha.io v2 market API (`na`), BDO-REST-API profiles, a tail of
  the UTF-16LE JSON client session log (running / login / disconnect only), a
  watcher on the game's ScreenShot folder plus OCR, and operator-typed data.
- Client data files are read only while the game is CLOSED, offline, never
  modified - and only if a plan explicitly adopts it (not in plan 001).

## Ports (block 8940-8959, EW)

Assigned 2026-10-04 by MAIN; netstat showed nothing in the block. Constants live
in `server/ew/ports.py`; a port literal at a bind site is a defect
(`tests/test_ports.py`). 8940 = EW server (API + dashboard assets + overlay SSE).
8941-8959 unassigned. The machine-wide registry entry is MAIN's to file; never
edit another tree's registry.

## Paths and runtime

- Root is the repo checkout; no absolute machine path in any tracked file (public
  repo). Per-host values live in gitignored `config/local.json`
  (template `config/local.example.json`).
- Python 3.11+ stdlib only for `server/`, `tools/`, `tests/`. Electron app in
  `app/` (Node 20+); pure-JS logic is tested with `node --test`, no Electron
  needed for tests.
- Runtime state: `ops/runtime/` (gitignored). Server health:
  `GET http://127.0.0.1:8940/api/health`; version: `GET /api/version`
  (fleet P0-5 contract).
- Atomic writes only (`tmp` then `replace`). Background daemons use `pythonw.exe`
  and `CREATE_NO_WINDOW`.

## Gates

- `python -m pytest -q` and `npm test --prefix app` green before any push.
- `python tools/leak_sweep.py --staged` (pre-commit) and `--pre-push` (pre-push hook):
  refuses machine paths, account ids, emails, sibling names and secret shapes.
  Sibling names come from gitignored per-host config only - this tree never spells
  them. Install hooks in a fresh clone: `python tools/install_hooks.py`.
- ASCII + LF in every authored file (FLEET item 8); `tests/test_ascii_lf.py`.
- Never add a `Co-Authored-By` trailer.

## Session workflow

- Start: read `EW-NEXT-SESSION.txt`, then this file, `docs/plans/ROADMAP.md`,
  `git log -10`. On "continue", work the hand-off's next action.
- Spec first, then failing test, then code (TDD). A `verifier` agent confirms
  before any done-claim (refute rounds capped at 3).
- End: `/done` (`.claude/commands/done.md`). Its ONLY chat output is
  `Done ritual complete, safe to clear`.

## Settled - do not re-litigate

- Electron for dashboard + overlay, Python stdlib server (ADR 0001). Reverses if:
  Electron's transparent click-through window is measured tripping XIGNCODE3.
- EW takes the fleet slot of an abandoned sibling project; nothing is imported from
  it. Reverses if: the operator or MAIN orders an import in writing.
