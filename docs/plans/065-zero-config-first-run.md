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

## As-built deviations (build lane, self-adjudicated 2026-10-06)

1. Documents validity marker: `ScreenShot` folder OR `GameOption.txt`
   (stat only, never opened) instead of "the session log the plan 008
   classifier reads" - plan 008 as built reads the log from
   `<install_dir>/Log`, not Documents, and the ScreenShot folder only appears
   after the first PrtSc. Alternatives: ScreenShot only (misses a fresh
   install), any `Black Desert` folder (false positives from leftovers).
   Reverses if: plan 008 moves the log under Documents.
2. Steam root comes from the registry (`HKCU\Software\Valve\Steam`
   SteamPath, then the HKLM InstallPath keys) then `%ProgramFiles(x86)%` /
   `%ProgramFiles%` + `Steam`; `steamapps/libraryfolders.vdf` (fallback
   `config/libraryfolders.vdf`, old format too). A library whose app 582660
   manifest exists wins over one with only a leftover folder; the manifest's
   `installdir` names the folder. Alternatives: the vdf `apps` list (absent
   in old formats). Reverses if: Steam drops `libraryfolders.vdf`.
3. Re-detect runs as a plan 062 GameWatch poller (`Detector.on_poll`): at
   most once per `RETRY_S` (3600 s) while the state is `unconfigured`, plus
   once at server start. Only `main()` passes a `Detector`, so no test probes
   the real disk or registry. Alternatives: a separate timer thread (one more
   thread for the same cadence). Reverses if: gamewatch stops polling while
   unconfigured.
4. A Settings save that changes `bdo.*` re-points the watcher live
   (`GameWatch.configure`); blank goes back to the detected folder. Not added
   to `restart_keys`. `bdo.*` reads that are not an existing absolute folder
   show as blank (auto-detect) in Settings, but gamewatch still honours the
   raw explicit string (explicit config > detected, per the plan).
5. `config/local.example.json` `bdo` values became `""` (blank = auto-detect)
   with a `_doc`; the old `<steam library>/...` placeholders, if copied, were
   an explicit (bogus) path that would have beaten detection.
6. Onboarding: `optional` per step - `watch`, `daily`, `leveling` are
   optional (they happen through use); `family`, `log`, `screenshots`,
   `overlay` are required. `show` = a required step is open and not
   dismissed; `complete` still means all seven done. A detected folder that
   still exists ticks its step even before `Log` / `ScreenShot` appear; a
   missing `overlay.anchor` (default kept) ticks `overlay`; `watch` also ticks
   on any plan 071 `auto: true` entry. The `log` / `screenshots` links now
   open the Settings fields instead of the System tab. Alternatives: family
   optional too (the card would never show on a detected machine, losing the
   only nudge for an input EW cannot detect). Reverses if: the operator
   finds the family nudge noisy.
7. Dashboard: a "Game folders" Settings group (type `dir`; client checks
   shape only, the server checks the folder exists) with an
   "auto-detected: <path>" / "using this folder" note from the new GET
   `/api/settings` `detected` block.
8. Merge into main (resolve lane, 2026-10-06): main had landed plan 063
   (auto-OCR) in the same four spots. Resolved as a union, both features
   kept: `app.py` imports both `detect` and `ocrauto`; `settings.py` SPEC
   keeps the `ocr.*` keys then the `bdo.*` keys; ewcore `SETTINGS_GROUPS`
   orders `Screenshots (OCR)` before `Game folders` (main's group first,
   the newer lane group appended); the settings test expects that order.
   Alternatives: `Game folders` before OCR (would reorder a group main
   already shipped). Reverses if: the operator wants folder setup higher
   in the Settings form.
