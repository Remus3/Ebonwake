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
