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
