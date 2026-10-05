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

## As-built (2026-10-05)

`server/ew/coupons.py` (CouponClient on the plan 002 httpcache, CouponService),
wired in `server/ew/app.py`: GET `/api/events` carries `suggested` {status, robots,
source, error, freshness, candidates[{code, title, url, date}]} and starts a
background refresh; POST `/api/events` answers carry it peek-only; `/api/state`
gains `sources.coupons`. Dashboard: "Suggested coupons" card (add / copy per
code, stale badge) and a coupon-check line in Sources (`app/dashboard/events.js`,
helpers `suggestedRows`, `suggestAddBody`, `suggestStatus` in `ewcore.js`).
Tests: `tests/test_coupons.py` + fixture `tests/fixtures/coupons/news_list.html`,
`app/test/events.test.js`.

## As-built deviations (self-adjudicated)

1. Page and parse scope. Decision: NEWS_URL is the news list already in
   `events.SOURCES[0]` (`/en-US/News`); codes come only from the text of list
   entries (anchors to `/en-US/News/Detail...` on the official host) - no
   detail-page GETs. Alternatives: also GET each coupon notice's detail page.
   Why: the plan allows one GET per 6 h; detail pages would multiply it.
   Reverses if: the live list page shows no codes in practice - then amend the
   plan to allow one detail GET per coupon notice under the same robots gate.
2. Code rule tightened. Decision: plan 006 regex plus no lowercase, at least one
   letter, no edge hyphen, and a digit or two hyphens. Alternatives: the bare
   regex. Why: the bare regex matches ordinary words ("Black", "Event"); a
   suggestion list full of prose is useless. Reverses if: a real letters-only
   code is missed on the live page.
3. Attempt floor. Decision: a persisted attempt stamp blocks any new robots +
   page attempt for 6 h, failures included; plan 002 backoff still drives the
   stale/error view. Alternatives: backoff only (30 s .. 30 min). Why: backoff
   alone would GET the page more often than every 6 h after a failure.
   Reverses if: the operator asks for faster retry after an outage.
4. Robots reading. Decision: both `*` and the `Ebonwake` agent must be allowed;
   any failure to read robots.txt (including 404), or a body with no
   `User-agent:` group (empty, garbage, an HTML soft-404 after a redirect) =
   unreachable = off.
   Alternatives: RFC 9309 (4xx = allow all). Why: the plan says unreachable =
   off; the stricter reading never crawls without an explicit policy.
   Reverses if: the publisher serves no robots.txt and the operator orders the
   RFC reading.
5. Wiring. Decision: `make_server` has the feature off (status `none`) unless a
   client is passed; only `main()` passes the live one. Alternatives: on by
   default with a config switch. Why: no test can reach the network (plan item
   4). Reverses if: a config toggle is wanted - add `coupons.enabled`.
6. Known-code filter at read time. Decision: the cache keeps every candidate;
   codes already in the Events store are dropped per view. Alternatives: filter
   once at fetch time and cache the filtered list. Why: a one-click add removes
   the suggestion at once without a re-fetch (which the 6 h floor would forbid).
   Reverses if: the Events store grows large enough that a per-view scan is
   measured slow - then cache a known-code set invalidated on Events writes.

refute-rounds: 2/3 (round 1: robots body without a User-agent group read as
allow, deviation 6 incomplete - both fixed; round 2: verifier PASS).
