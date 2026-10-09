# Plan 092 - Fleet kit v11 -> v13 catch-up (orders Nf0e1ea, N0fcd68; v13 bundle)

Status: done 2026-10-08 (session 20, main checkout; plan 091 routes kit
orders to a session). Supersedes the lane-2 partial on keep ref 930fe43.

## Scope

- v11 ORDER 1840 (Nf0e1ea) section 4: vendor, anchored subagent-first hook
  command, triage spawns via `fleet_inbox.triage_spawn_kwargs(flag)`; section
  5: v10 steps (done in 92801b4) and FIX 0848 (done in 9ed2bbf).
- v12 ORDER 2031 (N0fcd68) section 3: vendor, FLEET-COMMON re-embed, claims
  PreToolUse + SubagentStop hooks, gitignore the race-guard files, commits /
  pushes through `fleet_gitlock`, whole suite through `fleet_suite_gate`,
  `tests/conftest.py` test guard.
- v13 bundle `2026-10-08-2128-from-MAIN-FLEET-KIT-v13` (committed in MAIN's
  outbox, no ORDER note yet): vendor 22 files, FLEET-COMMON 17 (no AI / bot
  attribution), `fleet_identity` commit-msg + pre-push hooks, local git
  config `fleet.operatorIdent`, no attribution keys in tree settings.

## As-built

- `ops/fleet_kit/` = v13 bundle byte-for-byte (22 files + MANIFEST.json,
  manifest sha256 1311801a...74e0); CLAUDE.md FLEET-COMMON block sha256
  fb6c129a...f02b; EW rules line says `Kit: v13`.
- `.claude/settings.json`: subagent-first command anchored
  (`python "$CLAUDE_PROJECT_DIR/ops/fleet_kit/fleet_subagent_first.py"`, mode
  file stays `log`); claims `hook` (PreToolUse,
  `Edit|Write|NotebookEdit|MultiEdit|Bash|PowerShell`) and `release-hook`
  (SubagentStop), timeout 10.
- `.githooks/commit-msg` runs `fleet_identity.py commit-msg`;
  `.githooks/pre-push` runs `fleet_identity.py pre-push` before the leak sweep.
- `tools/ew_loop.py`: `triage_spawn_kwargs(fi)` = kit
  `triage_spawn_kwargs(FLOORS_IN_HOOKS=False)`; `_git` runs every `commit` /
  `push` under `fleet_gitlock.git_lock` (owner env `FLEET_CLAIM_OWNER`, else
  `ew-loop.main`); `_gate_argv` wraps a whole-suite pytest in
  `fleet_suite_gate.py run --owner ...`; the lane brief names the gate.
- `.claude/commands/done.md`: suite via the gate, commit / push via the lock.
- `tests/conftest.py` installs `fleet_test_guard` (env_roots empty: EW reads
  no env var for a runtime root).
- Conformance re-pinned to v13 plus hook / gitignore / conftest / identity /
  done.md checks; `tests/test_ew_loop_kit_v13.py` covers the loop wiring
  against the REAL vendored `fleet_inbox` signature.

## As-built deviations (adjudicated 2026-10-08)

1. D1 vendor v13 from the staged, committed bundle (22/22 re-hashed) on the
   operator relay ("catch EW up through v13"). Alt: stop at v12 and wait for
   the v13 ORDER. Reverses if: the v13 ORDER lists a different step or hash -
   then match the ORDER.
2. D2 keep ref 930fe43 not merged: its `triage_spawn_kwargs(fi)` called the
   kit function with no argument, a TypeError on the v11+ signature. Alt:
   merge or cherry-pick. Reverses if: the kit gives the flag a default.
3. D3 `FLOORS_IN_HOOKS = False` (EW floors are prompt / code rules; triage
   stays bare). Reverses if: a ToS or secrets floor moves into a hook.
4. D4 no harness `Claude-*:` / session-URL commit trailer (FLEET-COMMON 17
   outranks harness attribution; the commit-msg hook strips it anyway).
5. D5 `fleet.operatorIdent` set in local git config only. History holds 3
   non-operator commits (dependabot / web merges); a rewrite runs only on
   MAIN's ORDER via `fleet_rewrite.py`.
6. Found on the way: `core.hooksPath` still pointed at the pre-move C:
   checkout (recycled), so no git hook ran since the move to E:;
   `tools/install_hooks.py` re-run, now the relative `.githooks`.
