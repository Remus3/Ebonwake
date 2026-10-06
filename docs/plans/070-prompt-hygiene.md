# Plan 070 - Prompt hygiene: stale-prompt expiry, dedupe, game-closed quiet, 15/5/1 alert ladder

Status: open. Autonomy deep dive 2026-10-06 (research 0007 section 2.5). Lane hint: `build`.

Spec gap: suggestions, review rows and toasts (014, 026, 046, 059) stay
until the operator dismisses them, repeat across tabs, and notify while the
game is closed. Each needs a click to clear.

1. Prompt registry `server/ew/prompts.py`: every prompt-like item (coupon /
   event suggestion, pending stop, OCR review row, onboarding step) is
   registered with `{key, kind, created, expires}`; expired ones vanish
   with no click (defaults per kind in tracked
   `server/ew/data/prompt_ttl.json`, e.g. a suggestion 7 d, a toast 10 min,
   a pending stop when the next session opens).
2. Dedupe by key across tabs and the toast queue: one prompt shows once.
3. Quiet: while gamewatch is `not_running` only market-alert and coupon-
   expiry rules may notify (setting); everything else waits for the next
   `logged_in` and is dropped if stale by then.
4. Alert ladder: time-based notifications (boss, reset, maintenance, Hot
   Time end, buff end) fire at 15 / 5 / 1 min before (configurable list),
   each once.
5. Tests: `tests/test_prompts.py` - expiry, dedupe, quiet gate, ladder fires
   once per step; node test for the toast queue dedupe.

Acceptance: a fixture with a 9-day-old suggestion, a duplicate toast and a
boss in 16 min while the game is closed shows nothing until login, then the
5 and 1 min alerts once each; gates green; verifier PASS within 3 rounds;
one push.

ToS check: local state and clock only; no game input, no memory read, no
client file.

Depends on: 026, 008.

## As-built deviations

Self-adjudicated by the build lane (2026-10-06); refute-rounds recorded at merge.

1. Prompt age = first seen by the registry, not the row's own date.
   Alternatives: coupon `date` / OCR `shot_at` as `created`. Why: a post date
   is not when the operator was first prompted, and fixture dates against the
   real clock made existing tests time bombs (test_coupons failed). The
   registry still accepts a `createdfn` (tested). Reverses if: a source
   carries a real "prompted at" time.
2. Onboarding steps register with TTL null (never expire). Alternatives: a
   7 d TTL. Why: plan 065 steps self-tick; hiding an unfinished setup step
   loses it. Reverses if: the operator wants stale onboarding hidden.
3. Quiet allowlist = marketAlert, couponExpiry, gameExit (tracked
   `quiet.allow`). Alternatives: the plan's two only. Why: gameExit fires AT
   the exit (state not_running) and would never fire otherwise; no
   couponExpiry rule exists yet - the name is listed so a later rule passes
   the gate. Quiet states = `not_running` only (plan wording); `unconfigured`
   is never quiet (an unconfigured watcher would silence everything).
   Reverses if: the operator wants gameExit quiet too.
4. "Waits for the next logged_in, dropped if stale": hits are recomputed each
   round, so a held state hit re-fires at login if its rule still produces
   it; a held ladder step is consumed by the ledger (never fires late); a
   held transition hit (newCoupon, resetPassed) is lost. Alternatives: a
   persistent deferred queue with per-hit TTL. Why: no new state, and the
   rows stay visible in their tabs. Reverses if: a missed transition matters.
5. The ladder is state-based (the smallest step whose mark has passed, once
   per key through the ledger), not a grace window. Applied to bossSoon,
   buffEnding (was once at 5 min; default on, so it now also fires at 15 and
   1), Hot Time end (under hotTime) and a new rule `resetSoon` (default off:
   daily / weekly reset + maintenance start from GET /api/prompts `timers`).
6. Settings: `notify.quiet_closed` (default true) and `notify.ladder_min`
   stored as text "15,5,1" (settings form field type `ladder`), not a list -
   the settings splicer and form only handle scalars.
7. Toast dedupe: queue `once` keys (notify toasts) show once per 10 min
   (prompt_ttl.json `toast`) even after dismiss / expiry; POST-result toasts
   keep the plan 026 replace-and-repeat behaviour.
8. Merge onto main after plan 068 (resolve lane): `server/ew/app.py` keeps
   both wirings - plan 068's AutoTick block runs first, then the plan 070
   PromptRegistry (both append their `on_game` listener) - and GET
   /api/bosses keeps main's `bosses_view()` (068 `suggested` decoration)
   with the lane's GET /api/prompts route added after it. Alternatives:
   the lane's raw `bosses.view()` (drops 068's boss-shot suggestion), or
   registering 068 suggestions in the prompt registry now. Why: purely
   additive, no behaviour lost on either side; routing 068 suggestions
   through the registry is new scope. Reverses if: a later plan folds 068
   suggestions into the registry for expiry / dedupe.

Dependency guard: before writing code the lane checks that `app/dashboard/toast.js` (plan 026) and `server/ew/gamewatch.py` (plan 008) exist. If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["026", "008"]` into its progress JSON (`ops/loop/control/progress/p070-build.json`) and exits 0.
