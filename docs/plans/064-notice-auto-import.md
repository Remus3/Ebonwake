# Plan 064 - Official notice auto-import: maintenance, Hot Time, events, coupons (auto-add with undo)

Status: open. Autonomy deep dive 2026-10-06 (research 0007 section 2.1). Lane hint: `data`.

Spec gap: Hot Time windows (011) are typed by hand, the maintenance slot
(059) is a guessed weekly constant, and events / coupons (059 / 014) are
suggest-only, so every week needs several "add" clicks. The official News
boards carry all of it in parseable form.

1. Boards: extend plan 059's fetcher (same robots gate, cache, attempt
   floor) to boardType 1 (notices) and 2 (updates) besides 3; list GET at
   most every 6 h per board, at most 5 new Detail GETs per run in total;
   cache key = groupContentNo + the title's "Last Updated" stamp.
2. Maintenance: a title shaped `<Month> <D> (<Ddd>) Maintenance` -> parse
   the time table, locating the UTC column BY HEADER TEXT (column order
   changes with DST); store `{date, start_utc, end_utc, source}` in store
   domain `maint_notices`; `maint.resolve` prefers a notice for that date
   over the `maintenance.json` slot.
3. Hot Time: posts with an "Ends <Mon D>" line and bonus lines such as
   "Combat EXP +1,000%" -> plan 011 Hot Time windows `{start, end, bonus,
   source, auto: true}`; the comma inside the number is accepted.
4. Coupons: widen plan 014's code regex to word-style codes (letters and
   digits, 8-24 chars, e.g. the span before `button.js-btnCopyCoupon`),
   keeping the dashed shape.
5. Auto-add: a parsed item with a full window (both ends resolved) is
   ADDED, not suggested, with `auto: true`, the source link and an undo /
   dismiss remembered by groupContentNo; partial parses stay suggestions.
   Setting `notices.auto_add: true` (030 allowlist).
6. Backup: when the official host is unreachable for 24 h, read the Steam
   store RSS `store.steampowered.com/feeds/news/app/582660/` (robots-gated
   on that host; `api.steampowered.com` stays banned) for titles only, as a
   "check official notices" hint - never a source of windows.
7. Tests: fixtures under `tests/fixtures/notices/` (a maintenance table in
   both DST column orders, a Hot Time post, a word-style coupon); robots
   off = no GET; per-run GET cap; auto-add + undo; a dismissed id is never
   re-added. No network in tests.

Acceptance: the fixture maintenance notice yields the same UTC window in
both column orders; the Hot Time fixture lands as an auto window on the
Leveling card; gates green; verifier PASS within 3 rounds; one push.

ToS check: unauthenticated, robots-allowed GETs of official public pages;
no login, no coupon redemption, no game input, no client file.

Depends on: 059, 011, 014.

Dependency guard: before writing code the lane checks that `server/ew/eventnotices.py` (plan 059), `server/ew/leveling.py` (plan 011) and `server/ew/coupons.py` (plan 014) exist. If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["059", "011", "014"]` into its progress JSON (`ops/loop/control/progress/p064-build.json`) and exits 0.

## As-built deviations (build lane, self-adjudicated 2026-10-06)

1. A failed board fails the whole run. Decision: one list GET per board
   (order 3, 1, 2); any of them failing raises, so plan 002 keeps serving
   the last good list. Alternatives: skip the failed board and keep the
   others. Why: a partial list makes the run forget that board's cached
   Detail reads (notices gone from the list are dropped) and re-spend the
   5-GET budget on them next time; the three boards share one host, so a
   failure is almost always host-wide. Reverses if: one board is seen
   failing on its own for days.
2. Detail fetch order inside the shared 5-GET budget puts maintenance
   titles first, then Hot Time titles, then list order. Alternatives: pure
   list order. Why: both are time-critical and a busy Events board could
   starve them for several runs. Reverses if: the budget is raised.
3. Hot Time windows are dated rows in a new leveling list `hot_auto`
   (`{id a<N>, start, end, label "Hot Time", bonus, pct, source, group_no,
   auto: true}`), beside the weekly `hot_windows`; `hot_status` counts both,
   so the card's ON / next line and the XP stack use them. They are
   resolved to UTC at import (a later maintenance-setting change does not
   move them). `bonus` is the Combat EXP line when present, else the first
   EXP line; a pct outside 1..1000 (plan 011's range) is ignored.
   Alternatives: squeeze them into the weekly shape (days + HH:MM), which
   cannot express "Oct 8 12:30 to Oct 22 07:00". Why: the plan's shape
   `{start, end, bonus, source, auto}` is a dated window. Reverses if: the
   card needs per-day Hot Time hours inside a dated period.
4. Hot Time "full window": the start is the notice's window line, else the
   list's "Last Updated" stamp (00:00 UTC); the end is the window line,
   else the "Ends <Mon D>" line (end of that day UTC). Missing either =
   partial = no auto window (the Events-board suggestion still shows).
   Why: an "Ends" post has no start line; its publish stamp is the
   earliest provable start (a later start only shortens the window).
   Reverses if: real posts carry daily hours.
5. Only Events-board (3) notices become event entries or suggestions;
   Notices / Updates contribute maintenance, Hot Time and coupon codes
   only, and a maintenance-titled notice is never an event. Why: patch
   notes carry many unrelated dated lines; the first one is noise.
   Reverses if: an operator-visible event is missed because it was posted
   on board 1 / 2 only.
6. A notice with coupon codes and a full window adds coupon entries (one
   per code not already stored, window + link), not an event entry; codes
   without a full window (or with auto-add off) join plan 014's coupon
   suggestions through `CouponService(extra=...)`, so they are hidden when
   `coupons.check` is off. Why: the coupon card is where codes are used;
   one notice should not appear twice.
7. Word-style codes are trusted only as the text right before a
   `js-btnCopyCoupon` button (`coupons.copy_codes`); prose still uses plan
   014's dashed / digit rule. Why: an 8-24 letter rule on prose would take
   upper-case words ("MAINTENANCE", "WEBSHOP") as codes.
8. Maintenance notices are imported whether or not `notices.auto_add` is
   on (store domain `maint_notices`, newest 30 dates, row date = the UTC
   start date). Why: it is reference data that corrects every
   maintenance-relative time, not an added item. Reverses if: an imported
   table is ever wrong in practice - then gate it behind the setting.
9. The import runs on reads: GET /api/events (after its background
   refresh), every POST /api/events and GET /api/leveling (cache only, no
   fetch), so the Leveling card shows a Hot Time window without the Events
   tab being opened. No new scheduler. Alternatives: a timer thread.
   Why: idempotent (ledger by groupContentNo), cheap, and the dashboard
   polls both tabs. Reverses if: the overlay needs windows while no
   dashboard tab ever polls.
10. Undo lives on the Events tab (POST `{undo_notice: <groupContentNo>}`,
    two-click armed button): it deletes the notice's entries (ones already
    deleted by hand are skipped), its Hot Time windows, and dismisses the
    notice so it is never re-added. The Leveling card shows auto windows
    with an `auto` badge and source link, no own undo. Why: one ledger,
    one place to reverse it.
11. Steam backup: "unreachable" = robots.txt unreadable or a list GET
    failed (robots disallow is reachable and does not count), measured from
    the first failure since the last good run (`fail_since` in the attempt
    file). From 24 h on, each run (still one per 6 h) reads the Steam
    robots.txt and, when allowed, the RSS; titles only, shown as a hint
    with a link to the human news page (a link, never fetched). The hint
    clears on the next good run.
12. Progress file: the lane's permission scope covers only its worktree, so
    `p064-build.json` was written under the worktree's gitignored
    `ops/loop/control/progress/`, not the MAIN checkout. Reverses if: the
    lane driver grants writes to the MAIN progress dir.
