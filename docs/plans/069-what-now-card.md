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

## As-built deviations

Built 2026-10-06 in lane `build` (worktree lane-2). Self-adjudicated per
standing order 6.

1. Overlay widget is a plain default-on `whatNow` widget, not a plan 067
   in_game rule. Decision: add `whatNow` to `settings.WIDGETS` /
   `EWCore.WIDGETS` (default on, opt-out with `overlay.widgets.whatNow:
   false`), shown as the top overlay row. Alternatives: build plan 067's
   `overlay_contexts.json` here (scope creep into an unbuilt plan); wait for
   067 (blocks 069). Why: plan 067 is still open (no `server/ew/context.py`,
   no rules file), so there are no in_game rules to add to. Reverses if:
   plan 067 lands - it lists `whatNow` first in its in_game rule and the
   boolean becomes a pin / block like the other eight.
2. Scoring formula fixed as `score = weight x 3600 / max(left_s, 60)`; each
   source in `whatnow_weights.json` also carries `horizon_s` (drop anything
   due later) and `nominal_s` (due time assumed for undated actions: market
   alert, OCR review). Alternatives: linear urgency (needs a cap per source);
   undated actions always last (a market hit would never surface). Why:
   inverse time is monotone, needs no cap, and the plan's acceptance order
   (boss 8 min, reset 25 min, coupon tomorrow) holds with every weight equal.
   Ties: higher weight, then sooner due, then source name. Reverses if: the
   operator reports the card ranking a clearly less urgent action first.
3. Action texts carry no countdown; clients count `due` down locally. The
   SSE `whatnow` event (full view as data, unlike the plan 049 domain-name
   events) fires only when the ranked texts / dues change, checked at most
   every 10 s per stream (one shared cached computation) and at once after
   any domain change. Alternatives: a background thread pushing on a timer
   (one more daemon); per-minute text change events (noise). Why: no new
   thread, no event per minute. Reverses if: a change is seen to lag > 10 s.
4. Home order: What now leads; an all-clear What now yields the top slot to
   the plan 051 first-run card (which otherwise sits second). Alternatives:
   What now always first (an "All clear - play" above "Get started" reads
   wrong on first run); first-run always first (contradicts "card at the
   top"). Reverses if: the operator wants What now pinned first regardless.
5. Source adapters as built: boss (next spawn or one up now, looted bosses
   skipped), reset (one action per reset with open daily / weekly rows,
   plan 021 per-row rules included), buff (only while a grind session is
   open), hot (active window, due its end), maint (next slot start or an
   official notice for that date), coupon (open coupon rows with an end;
   suggested-but-unadded codes are not actions), dice (dice earned and the
   dice row open, due the dice reset), market (alerts from the sublist cache
   only - What now never fetches), deadline (plan 024 rows tight or late),
   ocr (review queue non-empty). A source that raises is skipped and named
   in `errors`. Reverses if: a source needs a richer rule (file a plan).
6. Verifier round 1/3: PASS, three minors fixed with regression tests - Hot
   Time due rounded to the minute (no spurious SSE event from two clock
   reads), a late deadline past enrolment falls back to its quest cut-off,
   junk boss rows are skipped instead of blanking the source.
   refute-rounds: 1/3.
7. Merge onto main after plan 067 landed (resolve lane, 2026-10-06). Item 1's
   reverse condition fired. Conflicts in `server/ew/app.py` (docstring, the
   import list, `_sse` setup) resolved as a union: both `/api/overlay/context`
   and `/api/whatnow`, both `autotick`/`context` and `whatnow` imports, and the
   SSE loop keeps both the `overlay_context` signature check and the `whatnow`
   recompute (an overlay_context change counts as a change and forces a
   What now recompute - harmless, cached). Non-textual follow-ups: `whatNow`
   added to `context.WIDGETS` (so it gets a plan 067 auto | pin | block mode
   and the rules-shape test holds), to `EWCore.OVERLAY_ROWS`
   (`ov-whatnow-row`) and listed FIRST in the `idle` and `in_game` rules of
   `overlay_contexts.json`. Alternatives: keep `whatNow` outside plan 067
   (auto mode would never show it - the card disappears from the overlay);
   list it in every context (duplicates boss / maint / reset rows, which the
   urgent contexts already show). Why: matches item 1's stated reverse;
   urgent contexts keep their specific widgets. Cost: under the cap of 4,
   `in_game` drops `eventsSoon` and `idle` drops `season` unless pinned.
   Reverses if: the operator wants those back (raise `max_widgets` or pin).
   Five plan 067 tests in `tests/test_context.py` asserted the old in_game
   order; updated to the new rule (whatNow first), no behaviour change
   beyond that.

Dependency guard: before writing code the lane checks that `app/dashboard/home.js` (plan 025), `server/ew/bosses.py` (plan 031) and `server/ew/today.py` (plan 021) exist. If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["025", "031", "021"]` into its progress JSON (`ops/loop/control/progress/p069-build.json`) and exits 0.
