# Plan 035 - Enhancement EV calculator: per-step chance tables, expected attempts and cost, Agris pity cap

Status: open. Deep dive + UX audit 2026-10-05 session 4 (research 0003-0005). Lane hint: `build`.

Spec gap: research 0004 F03 (value 4, effort M) and the threshold table
behind F06. The Deadeye tab enhancement plan (plan 007) lists steps with no
math; research 0004 section 1 shows the curve is NOT a single formula, so
EW must use per-step tables.

1. `server/ew/data/enhance_rates.json` (tracked): per gear family and step
   `{family, step, base_pct?, softcap_fs, max_pct_at_softcap, points:
   [[fs, pct], ...]?, crons_per_attempt?, agris_threshold?, source,
   verified}`. Seed: the bdogearguide 2026-07-30 rows (Kharazad, Sovereign,
   Edana) and the BDFoundry 2025-12-02 Agris thresholds; preview-only
   values (Blackstar bands, per-step cron lists) as `verified: false`.
   Operator overrides in the store, same pattern as plan 018 epochs.
2. `server/ew/enhance.py`: `chance(family, step, fs)` by linear
   interpolation inside `points`, else base * (1 + 0.1 * fs) capped at the
   soft cap then +base*0.02 per FS beyond, hard cap 90 percent, flagged
   `approx: true` when no table point covers it; `expected(family, step,
   fs, use_crons, prices)` -> `{p, attempts_mean, attempts_p90 (geometric),
   pity_cap (Agris threshold), cost_mean_silver}`; the pity cap truncates
   the geometric distribution.
3. Route `GET /api/deadeye/enhance?family=&step=&fs=&crons=0|1` (prices
   from the plan 002 cache for material ids in the row; missing price ->
   cost `null`, never an arsha call per request).
4. Dashboard: an "EV" button on each plan 007 enhancement step opens a
   small panel (chance, expected attempts, p90, pity cap, silver); pure
   formatter in `ewcore.js`.
5. Tests: `tests/test_enhance.py` (table lookup, interpolation, caps, pity
   truncation vs closed form, schema); node formatter tests.

Acceptance: the research 0004 table rows reproduce exactly at their
soft-cap points; pity truncation matches a brute-force sum within 1e-9;
gates green; verifier PASS within 3 rounds.

ToS check: math on sourced tables and cached read-only prices; no game or
market action.

Depends on: none.

## As-built deviations

Self-adjudicated by the build lane (2026-10-05).

1. Row key `step` = the level the attempt reaches (`PRI` = +0 -> PRI);
   Kharazad's preview cron list "PRI 120 .. NOV 3,650" is read as the
   attempt FROM that level (DUO 120 .. DEC 3,650), matching Sovereign's
   "PRI->DUO 320" list shape. Alternative: attempt TO that level. Why: both
   lists then have 9 entries over the same steps. Reverses if: a verified
   source shows per-target values.
2. Chance: fs == 0 (with `base_pct`) and fs == `softcap_fs` return the table
   value exactly; between them the plan formula capped at the soft-cap value
   (`approx: true`); no `base_pct` (Edana) derives base = max / (1 + 0.1 *
   softcap). Explicit `points` win inside their span. A row with no formula
   data outside its points (Blackstar PEN) or none at all (Blackstar TET,
   threshold only) gives `chance_pct: null`, never an extrapolation.
   Alternative: extrapolate linearly. Why: invented numbers are worse than a
   dash. Reverses if: a sourced Blackstar soft cap is found.
3. `softcap_fs` / `max_pct_at_softcap` are required keys but may both be
   null; an optional `unverified: [field, ...]` list names preview-only fields
   (crons, Blackstar points) so a verified chance row is not marked wholly
   unverified: the EV reply carries `unverified_used` (the preview fields the
   result rests on; a crons-off result ignores a preview cron count) and the
   panel tags `[unverified]` from it. Optional `materials: [[item_id, qty]]` is in the schema but
   unseeded (no verified per-step quantities); cost is null unless crons are
   on and the Cron Stone (16080) has a cached price.
4. `pity_cap` = threshold + 1 attempts (the guaranteed one); `agris_threshold`
   is returned beside it. Crons are billed on non-guaranteed attempts only.
   FS is held constant and a failure's downgrade re-climb is not modelled (the
   panel says so).
5. Operator overrides: POST `/api/deadeye` ops `rate_set` (a full row) /
   `rate_del` ({family, step}), store domain `enhance`; no dashboard editor
   and not added to the renderer bridge validator this slice (loopback POST
   only). Alternative: a new POST route + bridge entry + editor UI. Why: plan
   scope is the EV panel; the store pattern is in place for a later editor.
   Reverses if: the operator asks for in-app editing.
6. GET `/api/deadeye/enhance` with no query returns the effective table
   (families, rows) for the panel's family picker; `fs` is 0..999.
7. Merge onto main (resolve lane, 2026-10-05): the only conflict was the
   `server/ew/app.py` module import line - main added `weekly` (plan 033),
   this lane added `enhance`. Resolved as the union of both lists (both
   features kept). Alternative: none sensible (dropping either breaks its
   routes). Gates after resolve: ruff clean, pytest green, node 342/342.
