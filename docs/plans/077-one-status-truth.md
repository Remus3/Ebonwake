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

Dependency guard: before writing code the lane checks that
`server/ew/signals.py` (plan 073) and `server/ew/onboarding.py` (plan 051)
exist. If either is missing, the lane changes nothing, writes
`"status": "blocked", "needs": ["051", "073"]` into its progress JSON
(`ops/loop/control/progress/p077-build.json`) and exits 0.
