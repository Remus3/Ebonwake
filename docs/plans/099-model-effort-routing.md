# Plan 099 - Perf 2.5: model / effort routing by item kind

Status: built (lane, 2026-10-09). Source: ROADMAP row 099 (MAIN 2246 sec 2.5,
perf audit).

## Problem

Every loop lane run went out with `writes_code=True` and no model, so the kit's
`pick_model` gave opus to all of them: plan implementation, but also resolve
runs (a merge-conflict fold), plan 085 data-row refreshes, the idle deep-dive
(research, writes no code) and every refute fix round. The review-lane
verifier and the order-closing inbox answer took the kit's default effort
(`pick_effort` of a non-ack note name = medium). Opus at medium for a
mechanical fix round or a one-row data edit is the expensive end of the
second account's budget for work sonnet does as well.

## Design

1. One routing table `ew_loop.ROUTES` = {route kind: (model, effort)}:

   | route kind | model  | effort | runs                                       |
   |------------|--------|--------|--------------------------------------------|
   | plan       | opus   | medium | ROADMAP plan implementation                |
   | order      | opus   | medium | an ORDER / FIX / RULING lane item          |
   | handoff    | opus   | medium | a hand-off bullet (code work)              |
   | data       | sonnet | medium | a plan 085 data-row refresh (D-items)      |
   | resolve    | sonnet | medium | plan 058 resolve-merge run                 |
   | deep-dive  | sonnet | medium | idle research lane                         |
   | fix        | sonnet | medium | a refute fix round (process())             |
   | verify     | sonnet | low    | the review-lane verifier                   |
   | inbox      | sonnet | low    | the order-closing inbox answer             |

   Triage keeps the kit's `fleet_inbox.TRIAGE_SPAWN` (already sonnet / low);
   the kit owns it.
2. Effort always goes through the kit's `pick_effort(note, effort)`, so the
   table's value is validated against the kit's EFFORTS and an explicit value
   wins over the note-name heuristic.
3. The choice lives in the loop config: `load_config()` returns `"routes"`,
   the table above with any valid per-kind override from `config/local.json`
   `loop.routes` = {kind: {"model": alias-or-claude-id, "effort": level}}
   merged in. An unknown kind, a bad model string or an effort outside the
   kit's EFFORTS is ignored (the default stays), so a typo never stops a lane.
4. `route_kind(rec)`: a resolve run is `resolve` (whatever its base kind); a
   hand-off item whose id is a plan 085 data id (`D` + 6 hex) is `data`; any
   other item uses its own kind; an unknown kind falls back to `plan` routing.
5. Wiring: `lane_worker` passes `model` / `effort` through
   `ew_lane.run_lane` (new optional keywords, forwarded to the kit spawn only
   when given); `process()` routes the verifier as `verify` and each fix round
   as `fix`; `answer_order` routes as `inbox`. The lane worker records the
   chosen `{"model", "effort"}` on the item record (`route`), so a reader of
   `items.json` sees what each run used; the kit usage line already carries
   model and effort per run.

## Acceptance

- `ROUTES` holds exactly the rows above; `load_config` returns them by
  default and merges a valid override, ignoring invalid ones.
- A dispatched plan item runs opus / medium; resolve, data and deep-dive
  items run sonnet; the record carries `route`.
- In `process()` the verifier spawn is sonnet / low and each fix round is
  sonnet (writes_code still True).
- The order-closing inbox answer is sonnet / low.
- `ew_lane.run_lane` forwards `model` / `effort` when given and omits them
  otherwise (kit default unchanged for a by-hand lane run).
- Tests: `tests/test_ew_loop_routes.py`.

## As-built deviations

1. No plan doc existed for row 099; this lane wrote it from the ROADMAP row
   and MAIN 2246 sec 2.5. Alternatives: block on a session to write it. Why:
   the row names every route; blocking would idle the lane. Reverses if:
   MAIN's 2246 text names a different table - then ROUTES follows it.
2. Hand-off items stay on opus (the row lists only plan implementation for
   opus and is silent on hand-off bullets). Alternatives: sonnet for every
   hand-off. Why: hand-off bullets are code fixes of plan-implementation size
   (bug fixes, carried-forward build work); the data-refresh subset, which
   the row does name, is split out by its D-id. Reverses if: usage shows
   hand-off runs are mostly mechanical - then set `handoff` to sonnet.
3. "Recorded in loop config" is read as: the table is the loop config's
   default (`load_config()["routes"]`) with a gitignored per-host override,
   documented in `config/local.example.json`, rather than a value written
   into the template. Alternatives: a tracked routes file. Why: plan 080 keeps
   tunables out of the template; one constant plus an incident-switch
   override matches the other loop knobs. Reverses if: MAIN asks for the
   table in a tracked config file.
4. Effort for opus plan work stays medium (the kit default today), not high.
   Alternatives: high. Why: the row changes the model, not plan effort; a
   higher effort would raise cost, the opposite of a perf item. Reverses if:
   plan runs are measured failing on depth at medium.
