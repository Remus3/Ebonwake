# Plan 059 - Official event-notice import: maintenance-relative windows, suggest-only

Status: open. Deep dive 2026-10-06 (research 0006 section 3). Lane hint: `data`.

Spec gap: plan 006 has the operator type every event and its end by hand;
the official Events board (`/en-US/News/Notice?boardType=3`, robots-allowed,
research 0006) carries ~15 live notices whose windows are written
`Mon D, YYYY (Ddd) after maintenance - Mon D, YYYY (Ddd) before maintenance`
or with explicit `HH:MM (UTC)` ends. EW has no notion of "before
maintenance" at all (nothing in `server/` mentions it), so even a hand-typed
end is a guess. Plan 014 already proves one gated, cached, suggest-only
fetch of the official news host.

1. Maintenance slot (data + setting): `server/ew/data/maintenance.json`
   (tracked, ASCII) `{weekday: "thu", start_utc: "HH:MM", duration_min,
   source, verified: false}` seeded from the publisher's usual NA/EU weekly
   slot with "verify" in the note; plan 030 settings gain an allowlisted
   `maintenance_start_utc` override. `server/ew/maint.py`:
   `resolve(date, edge)` -> UTC instant (`before` = slot start on that date,
   `after` = start + duration), pure, unit-tested across DST (UTC slot, so
   none) and a non-Thursday date (still resolves on that date).
2. Fetch: `server/ew/eventnotices.py` reuses plan 014's robots gate
   (`coupons.robots_verdict`, both `*` and the Ebonwake agent), injectable
   opener, plan 002 httpcache and the persisted attempt floor. Per run: one
   GET of the Events board list (every 6 h at most) plus at most 5 Detail
   GETs, only for groupContentNo values not yet cached (a Detail page is
   cached until its title's "Last Updated" stamp changes on the list).
   Same host and path prefix only; anything else is dropped.
3. Parse: from each Detail page extract `{title, url, group_no, starts,
   ends, ends_text, kind: "event"}` from the first window line; explicit
   `HH:MM (UTC)` wins, else `after/before maintenance` is resolved by step 1
   and the entry carries `maint_relative: true`. A notice with no parseable
   window yields no candidate (title still listed as "no dates found").
4. Suggest, never auto-add: GET `/api/events` gains `suggested_events`
   (same shape and status block as plan 014's `suggested`); the Events tab
   "Suggested events" card offers add / dismiss per row; add posts a normal
   plan 006 event entry with the source link; dismissed group numbers are
   remembered. Rows already in the store (same url or title + ends) are
   skipped. A `maint_relative` end shows "before maint. (~HH:MM local,
   verify)".
5. Toasts (plan 026) and the Home tab (plan 025) need nothing new: an added
   event is a normal event.
6. Tests: fixture list + 3 Detail pages under `tests/fixtures/eventnotices/`
   (saved from the 2026-10-06 pages, trimmed, ASCII); robots allow /
   disallow / unreachable = off; at most 5 Detail GETs a run; parse of
   both window styles; resolve() table; node tests for the card helpers.
   No network in tests.

Acceptance: fixture parse gives the 10656 window as Oct 1 after maint. -
Oct 15 before maint. with `maint_relative: true`; GET count per run <= 6;
gates green (ruff, pytest, node); verifier PASS within 3 rounds; one push.

ToS check: unauthenticated GETs of robots-allowed official pages only; no
login, no redemption, no game input, no client file.

Depends on: 014, 030.

Dependency guard: before writing code the lane checks that `server/ew/coupons.py` (plan 014) and `server/ew/settings.py` (plan 030) exist. If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["014", "030"]` into its progress JSON (`ops/loop/control/progress/p059-build.json`) and exits 0.

## As-built deviations
