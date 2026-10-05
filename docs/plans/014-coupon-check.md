# Plan 014 - Coupon auto-check against the official news page

Status: open. Ranked fourth (adjudicated 2026-10-05): useful for a new player,
but gated by the site owner's crawl policy.

Reverses the plan 006 "no scraping" decision only under these recorded
conditions: the operator asked for it (2026-10-05); one unauthenticated GET of
the official NA news list page at most every 6 h, descriptive User-Agent, and
only when the publisher's robots.txt allows that path for a generic agent
(checked with `urllib.robotparser` each run; disallow or unreachable = feature
off, shown in the Events Sources card). Never logged in, never redeems.

1. `server/ew/coupons.py`: fetch (injectable opener), extract candidate codes
   with the plan 006 rule `^[A-Za-z0-9-]{4,40}$` from notices that mention
   "coupon", plus the notice date; codes already in the Events store skipped.
2. Candidates show in the Events tab as "suggested" (source link, notice
   date); one click adds a normal coupon entry. Nothing is auto-added.
3. Cache + backoff like plan 002; failure = stale badge.
4. Fixture-based tests only (no network in pytest).

Acceptance: robots gate unit-tested (allow / disallow / unreachable = off);
extractor tested on saved fixture HTML; gates green; verifier PASS within 3
rounds; one push.
