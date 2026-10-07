# Plan 074 - Before-maintenance digest: loss warnings and everything that ends at the next maintenance

Status: open. Deep dive 2026-10-06b (research 0008 sections 1, 8). Lane hint: `data`.

Spec gap: the official 2026-10-08 maintenance notice (groupContentNo
10663) says all unclaimed Tag Characters EXP is deleted when the update
goes live, and lists the events that end at that maintenance. EW shows
neither: plan 064 reads only the notice's time table, and plan 069's
`maint` source says "Maintenance soon - wrap up" 2 h before, while its
`from_events` adapter ranks coupons only (events ending "before
maintenance" never reach What now). An irreversible loss is announced in
public text EW already downloads.

1. Loss-warning extraction (`server/ew/maintdigest.py`, pure, called from
   plan 064's `parse_notice` on the Detail page it already GETs - no new
   GET, no new host): plan 064's Detail cache keeps parsed fields only, not
   raw lines, so the cache entry gains a `loss` list and a `parse_v` field;
   an entry with an older `parse_v` counts as stale once (re-read inside
   064's existing 5-Detail-per-run cap). Keep sentences matching tracked
   patterns in `server/ew/data/maint_loss_patterns.json` (e.g. "will be
   deleted", "will be removed", "cannot be recovered", "claim ... before",
   "will disappear", "will be reset"), max 5 per notice, each cut to 200
   ASCII chars. Each warning: `{text, notice_no, url, due_utc}` where
   `due_utc` = that notice's maintenance start (plan 064 `maint_notices`).
   Patterns are data, not code; a notice with no match yields nothing.
2. Ending-at-maintenance list: every stored item (plan 006 / 059 / 064
   events and coupons, plan 011 / 064 Hot Time windows) whose `ends` falls
   in `[now, next_maint_start + 1 h]` and was produced from a "before
   maintenance" edge or ends within 1 h of the maintenance start.
3. Digest view: GET `/api/maint/digest` -> `{maint: {start_utc, end_utc,
   source}, warnings: [...], ending: [{kind, title, ends}], acked: [...]}`.
   Shown from T-24 h: a Home (025) card "Before maintenance (in 18 h)" and
   one What now (069) candidate per warning with a new source `maint_loss`
   (weight in `whatnow_weights.json`, horizon 86400 s, ranked above the
   plain `maint` row); plan 069 `from_events` also gains event rows (not
   only coupons) as `kind: "event"` ending candidates.
4. Ack: POST `/api/maint/digest {"ack": key}` hides one warning until the
   next maintenance; key = notice_no + sentence hash. Plan 070's 15/5/1
   ladder applies to unacked warnings only, and stays quiet while the game
   is closed except the T-24 h and T-1 h toasts (opt-in, plan 026).
5. Tests: `tests/test_maintdigest.py` - the 10663 body (fixture
   `tests/fixtures/notices/maint-20261008.html`, trimmed) yields the Tag
   Characters warning with `due_utc` 2026-10-08T08:00Z; a notice with no
   loss sentence yields none; ending-list edges (exactly at start, start +
   1 h, after); ack hides until the next maintenance; pattern file schema
   (ASCII, non-empty, compiles); What now ranks `maint_loss` above `maint`.
   No network in tests. Node formatter test for the Home card.

Acceptance: with the 10663 fixture and a clock at 2026-10-07T12:00Z the
digest shows one warning (Tag Characters EXP) and the events ending at the
2026-10-08 maintenance; What now's top row is that warning; at
2026-10-06T12:00Z (T-44 h) nothing shows; gates green; verifier PASS
within 3 rounds; one push.

ToS check: text of official public pages plan 064 already fetches
(unauthenticated, robots-allowed); no new host, no new GET, no login, no
game input, no memory read, no client file. EW tells the operator; the
operator claims in game.

Depends on: 064, 069, 070.

## As-built deviations (build lane, 2026-10-06; self-adjudicated)

1. Ack lives on the Home card. Decision: the "Before maintenance" card's
   warning rows carry an `ack` button (POST /api/maint/digest through the
   dashboard bridge); the plan 025 "Home is read-only" guard test now allows
   that one route. Alternatives: a new Events-tab section; API-only ack.
   Why: the card is where the warning is read; one click, no tab hop.
   Reverses if: Home must return to read-only (move the button to Events).
2. Notifications are a new opt-in rule `maintLoss` (default off, setting
   `notify.maintLoss`), fed by GET /api/prompts `maint_loss` (unacked
   warnings only). Ladder hits (15/5/1) are dropped while the game is closed;
   the T-24 h toast (fires in (T-24 h, T-23 h]) and the T-1 h toast (fires in
   (T-1 h, T-0)) carry `closed: true`, which the quiet gate (`promptGate` /
   `prompts.gate`) lets through. Alternatives: reuse `resetSoon` timers (no
   closed-game toasts); add `maintLoss` to `allow_closed` (would also let
   the ladder through). Why: the plan wants exactly two closed-game toasts.
   Reverses if: plan 026 grows a per-hit quiet policy of its own.
3. Loss sentences are kept only for maintenance notices (title match +
   parsed time table), and only those re-read once on a `parse_v` bump.
   Alternatives: every Detail page; re-read every cached entry. Why: a
   warning needs the notice's maintenance start as `due_utc`; event notices
   say "will be deleted after the event" routinely (noise); re-reading all
   60 cached notices would cost 12 runs of the 5-Detail budget for nothing.
   Reverses if: a loss warning is posted in a non-maintenance notice.
4. Ending list rule: an item ends "at this maintenance" when its `ends` is
   >= now and within 1 h either side of the maintenance start (stored items
   keep no edge; a "before maintenance" edge resolves to the start itself).
   Alternatives: store the edge on every item. Why: no store migration, same
   result for every resolved edge. Reverses if: items start keeping edges.
   Hot Time covers plan 064 dated windows and each plan 011 weekly window's
   occurrences from yesterday through the day after the start (refute r1).
5. What now gains source `event` (weight 0.5, horizon 86400 s, nominal
   86400 s) for the `from_events` event rows; `maint_loss` weight 2.0 (vs
   `maint` 1.5) so a warning outranks the plain row at the same due. Action
   texts are cut to 80 chars (ewcore TITLE_MAX; a longer one was dropped by
   the client). Reverses if: operator tuning in `whatnow_weights.json`.
6. GET /api/maint/digest also returns `show` (true from T-24 h), `in_s`,
   `lead_s` and `now`; warnings / ending are listed whatever the lead, the
   Home card and the What now horizon apply the T-24 h gate. Why: one
   payload serves the card, the ladder and debugging.
7. Fixture `tests/fixtures/notices/maint-20261008.html` is reconstructed
   from research 0008 section 1 (no network in a build lane); the loss
   sentence and time table match that record.
8. The notice's own "events ending at this maintenance" bullet list is not
   parsed; the ending list is built from STORED items only (plan item 2's
   wording), so the acceptance test seeds one event ending at 08:00 UTC.
   Alternatives: parse the bullets into title-only rows. Why: bullet titles
   carry no end time and never match stored titles reliably; the events
   reach the store through plan 059 / 064 with real windows. Reverses if:
   a notice lists ending events that plan 064 never imports.
9. Sentence split ignores a period after a known abbreviation (month / day
   names, "No.", "e.g.", ...) so "the Oct. 8 maintenance" stays one
   sentence (refute r1).

Dependency guard: before writing code the lane checks that `server/ew/eventnotices.py` with maintenance parsing (plan 064), `server/ew/whatnow.py` (plan 069) and `server/ew/prompts.py` (plan 070) exist. If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["064", "069", "070"]` into its progress JSON (`ops/loop/control/progress/p074-build.json`) and exits 0.
