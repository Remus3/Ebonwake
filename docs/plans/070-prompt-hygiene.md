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

Dependency guard: before writing code the lane checks that `app/dashboard/toast.js` (plan 026) and `server/ew/gamewatch.py` (plan 008) exist. If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["026", "008"]` into its progress JSON (`ops/loop/control/progress/p070-build.json`) and exits 0.
