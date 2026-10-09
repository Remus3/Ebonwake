# Plan 093 - Loop: deliver reply notes into the destination tree's inbox

Status: built (lane, 2026-10-08). Source: ROADMAP row 093 (session 20 hand-off).

## Problem

The loop tick wrote every reply only to `moon_sync_outbox/` and logged
"1/1 reached" without copying it anywhere, so no reply ever reached the
sender's inbox (FLEET-COMMON 7: delivery = destination copies re-hashed and an
N/M reached-count reported). Order answers (`answer_order`) also used an
undashed `YYYYMMDD-HHMM` stamp while the kit's `batch_note` and the fleet use
the dashed `YYYY-MM-DD-HHMM`.

## Design

1. Both reply paths (`send_batches`, `answer_order`) stamp names with
   `NOTE_STAMP = "%Y-%m-%d-%H%M"`.
2. After the outbox copy is written and verified, `Tick.deliver_note(src, to)`
   byte-copies it into the destination inbox under the same name (atomic: a
   dot-prefixed tmp in the destination dir, then replace; dot names are never
   scanned as notes), re-hashes the destination copy with SHA-256 against the
   outbox bytes and appends one line to `ops/loop/control/delivery.jsonl`:
   `{ts, note, to, sha256, reached, count: "N/1", dest_name?, detail?}`. The
   tick log reads `(N/1 reached)` from the real result.
3. Destination resolution (`Tick.dest_inbox(code)`), per-host only, nothing
   tracked: config `loop.dest_inboxes` `{CODE: dir}` first; else the fleet
   roster (`loop.roster` path, else env `FLEET_ROSTER`, the roster the kit
   statusline reads: `{"repos": [{code, root, inbox?}]}`) entry's `inbox`, else
   `<root>/moon_sync_inbox`. EW's own inbox is never a destination.
4. Not reached (no destination known, destination dir missing, copy error, or
   an existing destination file with other bytes - never overwritten): the
   outbox copy stays the record, the ledger line says `reached: false` with
   the reason, and every later tick retries it (`Tick.redeliver`, before new
   notes). A retry appends a line only when it reaches; the log reads
   `inbox: redelivered N/M reached`. A reached note is never copied again.

## Acceptance

- A triaged ANSWER and an order ANSWER both land in the destination inbox
  with the same name (dashed stamp) and a SHA-256 equal to the outbox copy;
  the ledger records `reached: true`; a re-run tick is a no-op.
- A roster entry resolves the destination to `<root>/moon_sync_inbox`.
- An unknown destination records 0/1 and is delivered by a later tick once
  configured, without a second spawn.
- An existing destination file with other bytes is left untouched.
- Tests: `tests/test_ew_loop.py` (plan 093 section), all under tmp_path; the
  test `deps()` passes `roster_path=None` so no test reads FLEET_ROSTER.

## As-built deviations

1. Destination lookup is config / roster, not a fixed sibling path.
   Alternatives: hard-code MAIN's checkout path (forbidden: no machine path
   in a tracked file); infer siblings from the worktree layout (MAIN's folder
   name is not knowable from this tree). Why: per-host values live in
   gitignored config, and FLEET_ROSTER is the kit's existing roster
   convention. Reverses if: the kit ships a delivery helper or a roster
   location of its own - then this code calls it.
2. No backfill of past outbox-only replies. Alternatives: copy every old
   outbox note on first run. Why: sessions already hand-copied the replies
   that mattered (hand-off session 20 records 1/1 re-hashed), and a blind
   backfill would re-send stale, already-answered notes past the daily cap.
   Reverses if: MAIN reports a specific missing reply - then copy that one.
3. Live config not set by this lane: the main checkout's gitignored
   `config/local.json` needs `loop.dest_inboxes.MAIN` (or FLEET_ROSTER must be
   set) before replies reach; until then each reply is logged 0/1 and retried
   every tick. Why: a lane may not write outside its worktree. Reverses if:
   never - it is a one-time per-host setting for the session to make.
