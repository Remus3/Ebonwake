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
