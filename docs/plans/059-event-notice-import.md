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

Self-adjudicated in the build lane (2026-10-06); each line gives decision /
alternatives / why / reverses if.

1. Fixtures are trimmed reconstructions, not byte-saved pages. Alt: block
   until a capture. Why: the headless lane has no network grant (curl and
   WebFetch were refused), and research 0006 section 3 quotes the list shape
   (titles + groupContentNo, no dates) and the 10656 / 10545 windows verbatim.
   Reverses if: a session captures the live pages and the parser misreads them
   (then swap the fixtures and fix the parser test-first).
2. Detail re-read rule. The list shows no "Last Updated" stamp (research
   0006), so: a date beside the list title is used as the stamp when present
   (a change re-reads that Detail); with no stamp a cached Detail is re-read
   after 7 days (`DETAIL_MAX_AGE_S`). Alt: never re-read / re-read every run.
   Why: keeps the GET budget (<= 1 list + 5 Detail per 6 h) while edited
   notices still refresh. Reverses if: the board starts showing a stamp
   (already handled) or notices are seen edited more often than weekly.
3. Settings keys are dotted: `events.maintenance_start_utc` ("" or HH:MM UTC,
   read live) plus a new `events.notice_check` toggle (default on, restart
   key, mirrors plan 014's `coupons.check`); a new "Events" settings group.
   Alt: a bare `maintenance_start_utc`; reuse `coupons.check`. Why: plan 030's
   allowlist and its client mirror require dotted keys, and switching off
   coupon suggestions should not silently stop event-notice GETs.
   Reverses if: the operator wants one switch for both checks.
4. Windows are cached raw (date + before/after edge or HH:MM UTC) and resolved
   through `maint` on every read, so a changed maintenance setting applies
   without a refetch. Date-only points resolve to 00:00 / 23:59:59 UTC (plan
   006's rule); windows already over are not suggested; a missing year comes
   from the other point or the fetch year (wrapping Dec -> Jan).
   Reverses if: notices start using a local-time zone other than UTC.
5. Dismiss is `POST /api/events {"dismiss_notice": <groupContentNo>}` (store
   domain `event_notices`, last 500 kept); the bridge guard accepts it.
   Alt: a separate route. Why: one Events POST route, same IPC guard.
6. `coupons.robots_verdict` gained a `urls` argument (default unchanged:
   NEWS_URL only); plan 059 checks the list URL and a Detail URL, both agents.
7. Maintenance slot seeded Thu 07:00 UTC, 180 min, `verified: false`, note
   says verify. Reverses if: the next official maintenance notice says
   otherwise (edit `server/ew/data/maintenance.json` or use the setting).
8. The maintenance-relative end label carries the local date first:
   "YYYY-MM-DD before maint. (~HH:MM local, verify)" (verifier round 1: the
   plan's bare form hid the end day). A window with no year at all takes the
   start year (fetch year -1 / 0 / +1) whose start lies nearest the fetch
   month (verifier rounds 1-2): "Dec 24 - Jan 7" or "Dec 10 - Dec 31" read in
   January is last December, "Jan 2 - Jan 20" read in December is next January.
9. Not added to `/api/state` sources (plan did not ask; the Sources card shows
   the "event check:" status line from GET /api/events instead).
