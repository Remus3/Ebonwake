# Plan 097 - Perf 2.2 rest: test tiers (slow / server / git) and xdist at the loop gate

Status: built (lane, 2026-10-09). Source: ROADMAP row 097 (MAIN 2246 sec 2.2,
perf audit). Follows plan 096 (one authoritative gate run per tree state).

## Problem

Plan 096 cut the number of whole-suite runs per tree state to one, but that
one run is serial and its cost is dominated by a small set of tests that build
real git repositories under tmp_path (the loop's merge / conflict / resolve
tests: 34 s idle and up to 98 s under sibling load, of a 1.5 to 2.5 minute
serial suite on the dev box). A
producer answering a refute round has no cheap middle ground between "the test
files I touched" and "the whole suite": a cross-file break is only found by
the loop's authoritative gate, which costs a whole fix round.

## Design

1. Markers (`pytest.ini`, `--strict-markers`):
   - `git`: the test spawns a real `git` process (in setup or call).
   - `server`: the test binds a socket (an HTTP server on 127.0.0.1:0, the
     single-instance probe, ...).
   - `slow`: the test's call phase takes over about 1.5 s on the dev box.
2. Tier guard (`tests/ew_tiers.py`, installed by `tests/conftest.py` next to
   the kit's fleet_test_guard). One stdlib audit hook (`sys.addaudithook`)
   watches `subprocess.Popen` and `socket.bind` while a test's setup and call
   run. A test that spawned git without the `git` marker, or bound a socket
   without the `server` marker, FAILS with a message naming the marker; a test
   whose call took over `SLOW_WARN_S` (3 s) without the `slow` marker gets a
   `TierWarning`. So the markers cannot drift from what the tests really do.
   `server` is applied automatically at collection when the test, one of its
   fixtures, or a module-level helper they reference (transitively) contains
   `serve_forever`, `HTTPServer(`, `.bind(` or `make_server(` in its source;
   the audit guard fails any binder the scan misses. `git` and `slow` are
   always explicit (module `pytestmark` for the real-git modules).
   As built: 3607 tests; git 143, slow 18 (17 of them git), server 364
   (auto), fast tier 3463.
3. Fast tier: `python -m pytest -q --fast` (conftest option) deselects `slow`
   and `git` tests (`FAST_MARKEXPR` = `not slow and not git`). Server tests
   stay in it: loopback servers on port 0 cost milliseconds.
4. Producer / refute loop: `python tools/ew_tests.py fast --owner <id>` runs
   the fast tier THROUGH the kit suite gate (a fast tier names no test file,
   so the claims hook counts it as a whole suite) with xdist when present.
   Lanes get `Bash(python tools/ew_tests.py:*)` in CODE_EXTRA; the GATES
   prompt asks for touched tests plus the fast tier when the change touches
   shared server/ or tools/ code. The kit suite gate stays the only path for a
   whole-tree run.
5. Loop gate: `_gate_argv` appends `-n 4 --dist loadfile` to the whole-suite
   pytest when pytest-xdist is importable in the gate interpreter and the ci
   command names no `-n` of its own; without xdist the gate runs serially,
   unchanged. `--dist loadfile` keeps every module (and so every real-git
   module and its module-scope state) on one worker.
6. Dev-only test dependency (stdlib-only rule): runtime code in server/,
   tools/ and tests/ stays stdlib only; pytest-xdist is an optional, dev-only
   accelerator, imported by nobody - only named on the pytest command line by
   the gate when present. Its pin lives in `ci/requirements-dev.in`; ci keeps
   its hash-pinned `ci/requirements-ci.txt` and runs the suite serially.

## Acceptance

- `pytest.ini` registers slow, server, git and sets `--strict-markers`.
- An unmarked test that runs git, or binds a socket, fails; marked, it
  passes; `--fast` deselects slow and git tests and keeps server tests
  (`tests/test_tiers.py`, a nested pytest run under tmp_path).
- `_gate_argv("python -m pytest -q")` with xdist present ends in
  `-n 4 --dist loadfile` inside the suite gate; with xdist absent it is
  unchanged; a ci command that already names `-n` is left alone.
- `tools/ew_tests.py fast` builds the suite-gate argv with `--fast` (and the
  xdist args when present), passes the exit code through, and refuses
  without `--owner`.
- GATES mentions the fast tier; CODE_EXTRA allows `python tools/ew_tests.py`.
- Every real-git and socket-binding test in the suite carries its marker (the
  guard fails the suite otherwise).

## As-built deviations

1. No plan doc existed for row 097; this lane wrote it from the ROADMAP row
   and the plan 096 doc. Alternatives: block on a session. Why: the row names
   the mechanisms. Reverses if: MAIN's 2246 text specifies a different
   mechanism - then this doc and the code follow it.
2. Dev-only dep adjudication: pytest-xdist is OPTIONAL and NOT added to the
   hash-pinned ci file in this lane. `ci/requirements-dev.in` pins
   `pytest-xdist==3.8.0` (the version on the dev box), but its hashed
   `ci/requirements-dev.txt` is not generated here: the lane had no network,
   pip or uv permission, and a hand-typed or unverified sha256 would make
   `pip install --require-hashes` fail ci. Alternatives: (a) add xdist +
   execnet to `ci/requirements-ci.in/.txt` with hashes from memory - rejected,
   unverifiable; (b) make xdist mandatory at the gate - rejected, a box
   without it would fail closed for no code reason; (c) drop xdist - rejected,
   the plan row orders it. Why: the gate degrades to the serial run it has
   today, so nothing breaks while the pin is completed. Reverses if: a session
   with network runs `uv pip compile ci/requirements-dev.in --universal
   --generate-hashes --python-version 3.11 -o ci/requirements-dev.txt` - then
   the gate box installs from that file with `--require-hashes`, and ci may
   add `-n 4 --dist loadfile` to its pytest step with the same pins.
3. `--dist loadfile` for the WHOLE suite, not only real-git modules.
   Alternatives: `--dist loadgroup` with an xdist_group per real-git module
   (non-git tests balanced per test). Why: loadfile is what the row names,
   keeps every module's module-scope fixtures and ordering on one worker, and
   could not be load-tested here on the whole suite (see 8). The wall time is
   bounded by the largest module (tests/test_ew_loop.py: the two real-git
   loop modules took 34 s serial and 28 s at -n 4 loadfile on an idle box,
   98 s serial under sibling load). Reverses if: a measured gate run shows test_ew_loop.py as the long
   pole - then split that module or switch to loadgroup.
4. The verifier keeps no pytest (plan 096): it is told the authoritative
   verdict for exactly its tree, so a fast tier there would only re-run a
   subset of what already ran green. The fast tier serves the producer and
   its refute-round fixes. Reverses if: verifiers are seen missing a break
   the gate verdict did not cover - then add `Bash(python tools/ew_tests.py
   fast:*)` to VERIFY_EXTRA.
5. The `slow` marker is set from measured call times (>= 1.5 s, dev box,
   serial) on the real-git tests; every slow test today is also a git test,
   so the fast tier's `not git` already drops them - the marker remains for
   `-m "git and not slow"` style selections and for future non-git slow
   tests (the guard warns at 3 s). Reverses if: never; the warning keeps it
   current.
6. `server` is auto-applied by a source scan instead of a hand-written
   marker in 52 modules. Alternatives: `pytestmark = pytest.mark.server` in
   every module (marks whole modules, most of whose tests bind nothing, and
   rots as tests move). Why: the scan is per test, and the audit guard is the
   backstop - a binder the scan misses fails until it is marked by hand.
   Reverses if: the scan is seen marking non-binding tests in bulk - then
   mark explicitly. `git` stays explicit because it decides fast-tier
   membership; test_ew_loop.py and test_ew_loop_gate_cache.py are marked
   whole-module (nearly every test builds a git world), so a handful of
   their pure unit tests leave the fast tier; a producer touching ew_loop
   runs those files by name anyway.
7. tests/test_ascii_lf.py and the leak sweep's tracked-tree test run
   `git ls-files` and so are `git`, outside the fast tier. Why: the guard
   rule is "spawns git", with no read-only exemption to argue about; GATES
   already names test_ascii_lf.py for producers. Reverses if: producers are
   seen landing ASCII/LF breaks the fast tier would have caught - then
   exempt read-only `git ls-files` in ew_tiers.is_git.
8. This lane's own sandbox allowed only targeted `python -m pytest <files>`
   runs: the whole suite through the kit suite gate, ruff, and
   `tools/ew_tests.py` itself could not be executed here, and the item-12
   progress file in the main checkout could not be written (write outside
   the worktree denied); the loop's authoritative gate runs the gates on the
   finished tree. Every test module was run under the guard by name, in
   batches (3607 collected, all green). Reverses if: never - it
   is a record of what was and was not run.
