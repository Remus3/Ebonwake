# Plan 051 - First-run checklist: profile family, log path, ScreenShot folder, overlay corner, watch items, dailies

Status: open. Deep dive + UX audit 2026-10-05 session 4 (research 0003-0005). Lane hint: `build`.

Spec gap: research 0005 candidate F12 (value 3, S). Empty states say "Set
profile.family in config/local.json"; a fresh install has no guided path.

1. `server/ew/onboarding.py`: `status(root, store)` -> ordered steps with
   `done` computed from real state: profile family set, BDO documents dir
   found (plan 008), ScreenShot folder readable (plan 008), overlay anchor
   chosen (plan 022 key present), >= 3 market watch items, >= 1 daily,
   leveling sample present.
2. `GET /api/onboarding`; each step links to the Settings field (plan 030)
   or the relevant card; "dismiss" stored in the EW store.
3. Dashboard: a first-run card on Home (plan 025) or Today, hidden once all
   steps are done or dismissed.
4. Tests: `tests/test_onboarding.py` (each step's detection with temp dirs
   and stores, dismiss).

Acceptance: tests green; gates green; verifier PASS within 3 rounds.

ToS check: reads EW's own config and the plan 008 folders (existence only).

Depends on: 030.

Dependency guard: before writing code the lane checks that `server/ew/settings.py` exists (plan 030). If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["030"]` into its progress JSON (`ops/loop/control/progress/p051-build.json`) and exits 0. Plan 019's tick turns that clean, marked run into item state `blocked` (not `no-change`) and re-dispatches the row once every Depends-on row is `[x]`; plan 019's work-list gate normally keeps the row from being dispatched that early. If this row ran before plan 019 landed and was recorded `no-change`, 019's re-arm step makes it dispatchable again.

## As-built deviations (build lane, self-adjudicated 2026-10-05)

1. Seven steps, in order: `family`, `log`, `screenshots`, `overlay`, `watch`,
   `daily`, `leveling`. The plan's "BDO documents dir found" became `log`
   (`bdo.install_dir/Log` is a directory) because the title promises the log
   path and the ScreenShot step already proves the documents dir exists.
   Alternatives: a separate documents-dir step (redundant with ScreenShot), no
   log step (title not met). Reverses if: plan 008 moves the log under the
   documents dir.
2. `daily` is done when at least one daily-kind Today item has a recorded tick,
   not when one exists: plan 003 seeds five dailies, so "exists" is true on a
   fresh install and the step would never show. Alternatives: count items
   (always done), require an operator-added daily (penalises using the seed).
   Reverses if: the Today seed is removed.
3. `leveling` counts only typed samples (a valid pct); plan 041 profile
   markers (pct null) do not satisfy it - they need no operator set-up.
   Reverses if: the leveling ETA starts working from markers alone.
4. `status(root, store, config_path=None)`: the extra `config_path` lets the
   server pass the same (injectable) file plan 030 Settings writes, so tests
   never read the real config. Reverses if: never - the default keeps the
   plan's signature.
5. Dismiss/restore: POST `/api/onboarding` `{"dismiss": true}` or
   `{"restore": true}` (store domain `onboarding`, `dismissed` = ISO time or
   null); only `true` is accepted. The dashboard exposes dismiss only; restore
   is API-only for now. Alternatives: a Settings toggle (needs a config key,
   but the plan says the store). Reverses if: the operator asks for a visible
   "show again" control.
6. Card lives on Home (first card while `show`), titled "Get started", each
   open step has an `open` button that selects its tab and focuses the
   Settings input with the matching `data-key` when the link names a field.
   The `bdo.*` folders are not Settings-allowlisted (plan 030), so those steps
   link to the System tab with a config/local.json hint.
