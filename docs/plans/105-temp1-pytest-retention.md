# Plan 105 - TEMP-1: pytest tmp_path retention (MAIN FIX N7416e2)

## Goal

Cap the growth of pytest temp run dirs. By default pytest keeps every
test's `tmp_path` dir and the last 3 `pytest-N` base dirs; a large suite
run many times a day by lanes and the loop fills the user temp dir.

## Change

`pytest.ini`, under `[pytest]`, after the `markers =` block:

    tmp_path_retention_policy = failed
    tmp_path_retention_count = 1

`failed` deletes a test's `tmp_path` dir when the test passes; count 1 keeps
only the newest `pytest-N` base dir. Nothing else in the ini changes.

## Test

`tests/test_pytest_retention.py::test_tmp_path_retention_configured` reads
the repo-root `pytest.ini` with `configparser` and pins both keys (policy
`failed`, count `1`). Fast and deterministic: no subprocess, no git, no
socket.

## Verification

After one green run, count the `pytest-N` base dirs under the pytest temp
root (`pytest-of-<user>`): at most 1 created after the change, and the
latest one holds no dirs of passing tests. Older base dirs may predate the
policy and are pruned by the next runs.

## Root-cause note (carried from the WIP)

The WIP behavior test ran a child pytest with `-c <repo pytest.ini>`, so
the child's confcutdir was the repo root and its work dir under the user
temp dir counted as inside it; collection then built Dir nodes from the
filesystem root down and scanned the whole shared user temp dir. A temp
entry that another process deleted mid-scan raised FileNotFoundError in
collection (child rc 2), and the assertion failed. Reproduced with about
96 parallel copies of the test, about 40 failed that way. Lesson: a test
must never walk the shared temp dir; this plan therefore pins the keys
only and measures retention on the real run after merge.

## As-built deviations

1. Decision: fresh minimal rewrite as plan 105 (ini keys + key-pin test);
   generic wording replaces the WIP's leaking root-cause clause; the
   behavior test is dropped. Alternatives: cherry-pick WIP d6605e5; keep
   the behavior test marked slow. Why: the WIP base predates the plan 097
   ini (markers, `--strict-markers`); its behavior test flaked by walking
   the shared temp dir; retention is proved by the post-merge run-dir
   count. Reverses if: a green gated run leaves more than 1 `pytest-N`
   base dir or keeps dirs of passing tests. refute-rounds: 3/3
   (adjudicated).
2. Decision: plan number 105, not 102. Alternatives: 102 (taken by the
   repo-review partial; rows 102-104 reserved); 101 (an open row that
   needs its own doc). Why: next free number. Reverses if: never.
3. Decision: no ROADMAP row added by this lane. Alternatives: add row 105.
   Why: the lane order forbids editing ROADMAP (parallel lanes collided);
   the loop flips rows on merge. Reverses if: the loop needs a row to
   merge; then the session adds it.
4. Decision: the keep ref `refs/ew/keep/N7416e2` is dropped after merge by
   the merging session, not by this lane. Alternatives: drop it in the
   lane. Why: the lane may not commit or merge. Reverses if: the merge
   fails and the WIP is needed again; then the ref is kept until a
   rework lands.
