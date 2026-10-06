# Plan 065 - Zero-config first run: path auto-detect, self-ticking onboarding

Status: open. Autonomy deep dive 2026-10-06 (research 0007 section 1). Lane hint: `build`.

Spec gap: `bdo.install_dir` and `documents_dir` are hand-edited in
`config/local.json`; gamewatch never guesses a path and the Settings form
(030) has no field for them. Until they are set, plans 008 / 009 / 056 /
062 / 063 all sit in `unconfigured`.

1. `server/ew/detect.py` (pure, injectable fs + env): documents dir = the
   user's Documents known folder (the `Personal` shell-folder value, which
   follows OneDrive redirection; fallback `%USERPROFILE%` + `Documents`)
   joined with `Black Desert`; valid when it holds a `ScreenShot` folder or
   the session log the plan 008 classifier reads. Install dir = Steam's
   `libraryfolders.vdf` (Steam's own config, read-only) -> each library's
   `steamapps/common/Black Desert Online` that exists; the app 582660
   manifest confirms it. No absolute path literal in tracked code.
2. Precedence: explicit config > detected. Detected values show in
   Settings as "auto-detected" with a "use other" field (newly allowlisted:
   `bdo.documents_dir`, `bdo.install_dir`, validated as existing dirs).
3. Onboarding (051): steps whose condition is met by detection or by data
   (paths found, overlay corner default kept, at least one watch item
   seeded by plan 071) tick themselves; the checklist hides when every step
   left is optional.
4. Re-detect on server start and once an hour while `unconfigured`.
5. Tests: `tests/test_detect.py` with a fake fs: redirected Documents, two
   Steam libraries, missing game, explicit override wins; onboarding
   auto-tick; no drive-path literals in tests (leak sweep).

Acceptance: on a fake fs with the game in a second Steam library and
Documents redirected, gamewatch leaves `unconfigured` with no config edit;
gates green; verifier PASS within 3 rounds; one push.

ToS check: reads directory listings and Steam's library config only; never
reads or edits BDO client data files; no game input, no memory read.

Depends on: 008, 030, 051.

Dependency guard: before writing code the lane checks that `server/ew/gamewatch.py` (plan 008), `server/ew/settings.py` (plan 030) and `server/ew/onboarding.py` (plan 051) exist. If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["008", "030", "051"]` into its progress JSON (`ops/loop/control/progress/p065-build.json`) and exits 0.
