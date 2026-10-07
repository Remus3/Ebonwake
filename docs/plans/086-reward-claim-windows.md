# Plan 086 - Reward claim windows: claim-by deadlines that outlive the event

Status: open. Deep dive 2026-10-07 (research 0013 sections 2, 7). Lane hint: `data`.

Spec gap: two live events carry a claim deadline later than the event's
own end. Combat Special (10592): points and claims to 2026-10-15 before
maintenance, "after that, unclaimed rewards are no longer available", while
its pass sale ends 2026-10-08. Donghwa's one-day coupons (10656): event to
2026-10-15, but the rewards sit in Mail (B) "until the Oct 29, 2026 (Thu)
maintenance". EW stores one `ends` per event (plans 006 / 059 / 064); plan
074 lists only items ending at the NEXT maintenance and runs its loss
patterns on maintenance notices only (074 deviation 3). The 10-29 mail
cut-off never reaches What now.

1. Extraction (`server/ew/claimwindows.py`, pure, called from plan 064's
   `parse_notice` on Detail pages it already GETs - no new GET): keep
   sentences matching `server/ew/data/claim_patterns.json` (e.g. "can be
   claimed until", "claimable until", "available in Mail (B) until",
   "unclaimed rewards will no longer be available", "Black Spirit's Safe
   ... until"), max 3 per notice, each cut to 200 ASCII chars; resolve the
   date with plan 059 `parse_window` point parsing (a "before / after
   maintenance" edge via `resolve_window`, plan 064 `maint_notices`). A
   sentence with no resolvable date is dropped. Bump `parse_v` so cached
   entries re-read once inside 064's 5-Detail cap.
2. Store: each event item gains optional `claim_until` (UTC) and
   `claim_text`; only when `claim_until` is later than `ends` or the event
   has no `ends` (a same-moment claim adds nothing). Coupons inherit the
   claim window of their source notice.
3. Surfaces: Events tab row shows "claim by <local date>" with a countdown
   after the event end; Home Timers card (076) lists claim windows inside 7
   days; What now (069) source `claim` (weight in `whatnow_weights.json`,
   horizon 3 days) "Claim <title> rewards (Mail / Safe) - <n> days left";
   plan 074's ending list includes a claim window that ends at the next
   maintenance. Ack: POST `/api/events {"claimed": <group_no>}` hides it
   (single op, allowlisted like plan 006's ops); plan 070's 15/5/1 ladder
   does not apply (day-scale deadline), one T-24 h opt-in toast `claimDue`
   (plan 026, default off).
4. Tests: `tests/test_claimwindows.py` - the 10592 and 10656 sentences
   (trimmed fixtures under `tests/fixtures/notices/`) yield claim_until
   2026-10-15 08:00Z (resolved from the maintenance slot) and 2026-10-29
   maintenance start; a claim date equal to `ends` adds nothing; no date =
   dropped; pattern file schema (ASCII, compiles); ack hides; What now
   ranks a 1-day claim above a 3-day one. No network. Node formatter test
   for the Events row.

Acceptance: with both fixtures and a clock at 2026-10-16T12:00Z, the
Events tab shows the coupon event ended with "claim by Oct 29" and no
What now row; at 2026-10-27T12:00Z What now lists "Claim ... rewards";
the Combat Special claim row is gone after 10-15; gates
green; verifier PASS within 3 rounds; one push.

ToS check: text of official public pages plan 064 already fetches
(unauthenticated, robots-allowed); no new host, no new GET, no login, no
game input, no memory read, no client file. EW tells the operator; the
operator opens Mail / Safe in game.

Depends on: 064, 069.

## As-built deviations

Self-adjudicated in the build lane (operator standing order 6).

1. claimDue toast is ON, not opt-in default off.
   Decision: `claimDue` joins NOTIFY_RULES with `defaultOn: true`, one T-24 h
   toast per notice that passes the game-closed quiet (like plan 074's
   maintLoss T-24 h toast). Alternatives: `defaultOn: false` (as written).
   Why: plan 080 removed every per-rule notify switch and ignores
   `notify.<rule>` config values, so a default-off rule could never be turned
   on - dead code. Reverses if: per-rule notify switches come back.
2. parse_v re-read covers every notice, not only maintenance ones.
   Decision: `maintdigest.PARSE_V` is 3 and `_stale` re-reads ANY cached Detail
   entry below it once (plan 074 re-read only maintenance titles). Plan 074's
   test line asserting an old event entry is not stale now asserts it is.
   Alternatives: a second per-domain version field. Why: claim sentences sit
   on event notices, which 074's rule never re-read; one version keeps the
   cache simple and the 5-Detail cap still bounds the re-read. Reverses if:
   the re-read is measured to starve new notices of the Detail budget.
3. Claims are synced onto stored items on every notice read, not written
   once at auto-add. Decision: `NoticeService._import` (after auto-add) calls
   `EventsService.sync_claims({url: (claim_until, text)})`, matching items by
   their notice url, auto-added or typed with the official link, coupons
   included. Alternatives: store at auto-add only. Why: items added before
   086 (or whose notice is re-read with a new deadline) get the window too.
   A deadline on another UTC date clears a previous `claimed` ack; a time
   re-resolved on the same date (a maintenance notice imported, 07:00 ->
   08:30, or a slot override) keeps it, and the claimDue toast key carries
   the date only, so neither re-fires. A notice re-read without a claim
   sentence clears the stored window (url -> None); an unread notice leaves
   it. Verifier round 1 found both gaps. Reverses if: never.
4. "Same-moment adds nothing" is enforced at sync (claim must be strictly
   later than the item's `ends`); `purge_expired` keeps an ended item while
   its claim window is open (else the reminder would be purged).
5. Point parsing is `claimwindows.points`, a sibling of plan 059's `_point`
   regex (not `parse_window`, which needs a START - END pair) that also reads
   "the Oct 29, 2026 (Thu) maintenance" as that maintenance's start
   (edge `before`); the latest point of a sentence is its deadline. Raw points
   are cached and resolved on read via `eventnotices._point_utc` (slot or
   imported maintenance notice), like 064's windows.
6. What now text is clipped to the 80-char action limit by shortening the
   title ("Claim <title...> rewards (Mail) - 2 days left"); the place is
   Mail / Safe / "Mail / Safe" from the claim sentence. `<n> days left`
   counts started days (ceil), so the text changes at most once a day.
   Weight 0.9, horizon 3 days, nominal 1 day.
7. Home Timers rows use source `claim` (added to TIMERS_DEDUPE and the
   client What now tab map), labelled "Claim <title>", note
   "claim by <date> (<place>)".
8. Merge with plan 085 (resolve lane). `server/ew/eventnotices.py` conflicted
   in the import line and `_stale`. Decision: import both `claimwindows` (086)
   and `patchverify` (085); `_stale` keeps 085's patch-notes re-read check
   first, then 086's title-free `parse_v < PARSE_V` re-read (deviation 2)
   replaces 074's maintenance-only one. Alternatives: keep 074's title filter
   (claims on event notices would never be parsed from cache), or drop 085's
   patch check (patch text never kept for already-cached pages). Why: both
   are independent once-only re-reads; order is irrelevant since either
   returns True. Reverses if: never.
