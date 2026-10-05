# Plan 033 - Weekly content planner gated by level and gear (Black Shrine, Atoraxxion, Jetina, LoML, Garmoth, Edania)

Status: open. Deep dive + UX audit 2026-10-05 session 4 (research 0003-0005). Lane hint: `build`.

Spec gap: research 0003 C06 (value 4, effort M, top 5 with C05). The Today
weekly list has no gating: the operator cannot see which weekly content is
reachable now and what is missing.

1. `server/ew/data/weekly_content.json` (tracked): rows `{id, name, reset
   (plan 021 rule), min_level, ap, dp, ap_kind: "main"|"aap"|"kutum",
   per_week, source, verified, note}`: Black Shrine solo (5/week, Sunday
   00:00), Atoraxxion normal (Lv 60, ~250 AP / 300 DP, Thursday), Jetina
   "Imperfect Beings" (Lv 60, Thursday), Land of the Morning Light bosses
   (Thursday), Garmoth loot (3/week, Thursday), Edania weeklies (350-420 AP
   / 427-505 DP, Thursday) - values from research 0003 sections 1.2/1.3 with
   their BDFoundry / official URLs and dates.
2. `server/ew/weekly.py`: `gate(row, character)` -> `eligible | needs
   {level, ap, dp}` using plan 004's stored level and gs; with plan 023
   present the hint adds the bonus-AP view ("+6 AP crosses a bracket").
3. `GET /api/today` adds `weekly_plan` (rows with gate, done count this
   period, next reset); `POST /api/today {"weekly_tick": id}` counts
   completions per period (Black Shrine 0..5).
4. Dashboard: "This week" card on the Today tab: eligible rows first with
   `n/per_week` ticks, then locked rows with their gap.
5. Tests: `tests/test_weekly.py` (gating, Sunday vs Thursday periods,
   per_week cap, schema of the data file); node formatter tests.

Acceptance: tests green; a Lv 58 / 240 AP fixture shows only Black Shrine
eligible; gates green; verifier PASS within 3 rounds.

ToS check: sourced static data plus operator-typed level and gear.

Depends on: 021, 023.

Dependency guard: before writing code the lane checks that `last_reset` exists in `server/ew/today.py` (plan 021); `server/ew/brackets.py` exists (plan 023). If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["021", "023"]` into its progress JSON (`ops/loop/control/progress/p033-build.json`) and exits 0. Plan 019's tick turns that clean, marked run into item state `blocked` (not `no-change`) and re-dispatches the row once every Depends-on row is `[x]`; plan 019's work-list gate normally keeps the row from being dispatched that early. If this row ran before plan 019 landed and was recorded `no-change`, 019's re-arm step makes it dispatchable again.
