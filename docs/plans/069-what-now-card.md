# Plan 069 - One "What now" card: the next best action, Home + overlay

Status: open. Autonomy deep dive 2026-10-06 (research 0007 section 3). Lane hint: `build`.

Spec gap: the Home / Now tab (025) shows many facts side by side; the
operator still reads five tabs' worth of state to decide what to do next.

1. `server/ew/whatnow.py` (pure, clock injected): candidate actions from
   existing services - boss spawn soon (031), reset soon with rows left
   (021), buff ending in an open session (005), Hot Time active (011 / 064),
   maintenance soon (059 / 064), coupon expiring (006), dice ready (056 /
   068), a market alert hit (002), Olvia / level deadline (024), OCR review
   queue non-empty (063). Each has a score = urgency (time left) x weight
   (tracked `server/ew/data/whatnow_weights.json`).
2. Output: top 1 action + up to 2 next, each `{text, why, due, source}`;
   empty when nothing is due ("All clear - play").
3. GET `/api/whatnow`; SSE `whatnow` on change; Home tab (025) gets the
   card at the top; overlay widget `whatNow` (one line) added to the plan
   067 rules for in_game.
4. Tests: `tests/test_whatnow.py` - ordering by urgency, ties by weight,
   empty state, each source adapter with a fixture.

Acceptance: a fixture with a boss in 8 min, a reset in 25 min and a coupon
expiring tomorrow ranks boss, reset, coupon; gates green; verifier PASS
within 3 rounds; one push.

ToS check: inference over data EW already stores plus the clock; no game
input, no memory read, no client file.

Depends on: 025, 031, 021.

Dependency guard: before writing code the lane checks that `app/dashboard/home.js` (plan 025), `server/ew/bosses.py` (plan 031) and `server/ew/today.py` (plan 021) exist. If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["025", "031", "021"]` into its progress JSON (`ops/loop/control/progress/p069-build.json`) and exits 0.
