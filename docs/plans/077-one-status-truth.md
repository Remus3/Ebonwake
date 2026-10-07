# Plan 077 - One status truth: onboarding, profile card and stale styling from the signal digest

Status: open. UI/UX audit 2026-10-06 (research 0009 H1, M4, M5, M6). Lane hint: `build`.

Spec gap: spec section 4 says every number shows its source and age. Live,
three surfaces disagree about one signal: Get started says the BDO
Documents folder is "Not found", Settings shows it auto-detected and in use,
Signal health (073) says "no ScreenShot folder yet - take one in game". The
Get started `open` link lands on an already-correct Settings field (dead
end). The Profile card shows a red `profile 6h ago` while Signal health
says the profile source is off by design (061 robots gate).

1. `server/ew/onboarding.py`: the `log` and `screenshots` steps read their
   state from `server/ew/signals.py` (the 073 digest) instead of their own
   folder probes. Step text per signal state:
   - Documents not set / not found -> current text, link to Settings field.
   - Documents found, no ScreenShot subfolder -> "Take one screenshot in
     game - EW reads the folder it creates", no link (nothing to set).
   - Watcher ok -> step done.
   Same pattern for `log` (install folder vs Log folder). Hint copy names
   Settings labels, not config keys (shared label map from plan 078 when
   present; plain strings otherwise).
2. `server/ew/data/signal_hints.json`: `boss_drift.not_built` reworded to a
   state ("no drift check yet - runs after the next schedule fetch"); a test
   forbids `plan NNN` in any hint.
3. `app/shared/ewcore.js` `profilePill` / profile and Life & CP cards
   (`app/dashboard/progress.js`): when `/api/signals` reports profile `off`,
   both cards collapse to one muted line with the signal's hint; no red
   pill for an expected-off source. `bad` stays for a real fetch error.
4. Stale styling (`app/shared/ew.css:83,283`): `.ew-stale` and expired
   rows use the muted colour with opacity >= .85 plus a text marker
   (`stale`, `ended`), meeting 4.5:1 on `--fk-surface-2` in both themes.
5. Tests:
   - `tests/test_onboarding.py`: Documents detected + no ScreenShot ->
     step open with the screenshot text and no link; folder present ->
     done; Documents unset -> Settings link. Same three for `log`.
   - `tests/test_signals.py`: no hint text matches `plan \d{3}`.
   - `node --test` (`app/test/progress.test.js`, `signals.test.js`):
     `profilePill` with signal off -> cls `unknown`, label from the hint;
     with a fetch error -> `bad`.
   - `app/test/density.test.js`: contrast helper on the token pairs for
     stale text >= 4.5 (dark and light).

Acceptance: on the 0009 live state (Documents detected, no ScreenShot yet,
profile robots-off) Get started, Settings, Signal health and the Profile
card tell the same story and no `open` link lands on a field with nothing
to change; gates green; verifier PASS within 3 rounds; one push.

ToS check: reads only EW's own signal digest and folder existence (no
client file content); no game input.

Depends on: 051, 061, 065, 073.

## As-built deviations

1. Digest source for onboarding. Decision: `OnboardingService(signals=...)`
   reads the live `/api/signals` body (wired in `app.py`); `status()` without
   one (or with an unreadable one) runs the same `signals.digest()` classifier
   over the resolved folders. Alternatives: live digest only (breaks bare
   `status()` callers and tests); keep onboarding's own probes (two truths).
   Why: one classifier either way, no second rule set. Reverses if: the
   digest grows a folder state onboarding must not see.
2. "Documents not found" needed a digest state. Decision: `gamewatch.health()`
   adds `install_ok` / `documents_ok` (parent folder stat), and a set-but-gone
   parent is row reason `unconfigured` (detail "... folder not found") so
   Get started keeps its Settings link. Alternatives: a new reason (hint file
   + REASONS churn); leave it as `folder_missing` (would tell the operator to
   take a screenshot when the folder path is wrong). Why: the plan's
   "not set / not found -> Settings link" needs the digest to tell them apart.
   Reverses if: Signal health must show "not found" and "not set" differently.
3. Plan 065's "detected folder counts as done before Log / ScreenShot exist"
   is replaced (tests in `tests/test_detect.py` updated): detected without the
   subfolder is now the in-game act step, no link - the plan's own spec.
   Reverses if: the operator wants first-run to hide on detection alone.
4. Opacity 0.9, not 0.85. `--fk-text-muted` at .85 on `--fk-surface-2` is
   4.3:1 in light (4.5 needed); .9 is 4.8:1 light, dark well above.
   `app/test/density.test.js` computes it from `ops/fleet_kit/tokens.css`.
   Reverses if: the kit tokens change so .85 passes.
5. Text markers: `.ew-list.ew-stale::before` prints `stale`; expired event
   rows already read `ended` in their clock (`fmtLeft`), so no new node.
   Row-level `.ew-stale` (market rows, leveling rows, overlay values) gets the
   contrast fix only - a pseudo-element in their grid layouts would shift
   columns. Alternatives: a marker span at each of ~25 call sites. Why: same
   reading with no layout risk. Reverses if: a row-level stale state is found
   with no other text cue.
6. Profile pill label: the hint's lead clause (before " - ", <= 48 chars);
   the full hint is the card's muted line and the pill title. 078's label
   map was absent, so hints use plain "Settings > Group > Field" strings;
   signal hints that named config keys (`ocr.auto`, `events.notice_check`,
   "Settings > paths") were reworded too (test forbids `plan NNN` and
   `bdo.` / `ocr.` / `events.` / `profile.` keys in hints).
7. JS tests for `profilePill` live in `app/test/signals.test.js` (with the
   signal fixtures), not `progress.test.js`.
8. Verification record: plan 077 accepted at refute-rounds 2/3. Both refutes
   were procedural only (the verifier's Bash was denied, so it could not re-run
   the gates). Neither round found a code defect; round 1's copy point
   (progress.js config-key text) was fixed. The producer re-ran the gates
   green, and the adjudicator re-ran them independently: pytest pass,
   app 632/632, ruff clean. leak_sweep --tree was clean (producer run).
   Round 3 was not spent. Alternatives rejected: a round-3 verifier (same tool
   denial) and leaving the work unmerged. Reverses if: the loop merge's CI
   re-run fails on this diff, or a later review finds a defect.
   Follow-up for the loop: give the verifier agent Bash for read-only gate
   commands.

Dependency guard: before writing code the lane checks that
`server/ew/signals.py` (plan 073) and `server/ew/onboarding.py` (plan 051)
exist. If either is missing, the lane changes nothing, writes
`"status": "blocked", "needs": ["051", "073"]` into its progress JSON
(`ops/loop/control/progress/p077-build.json`) and exits 0.
