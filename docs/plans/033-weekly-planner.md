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

## As-built deviations

1. Unsourced gates are `null`, not guessed. Decision: `min_level` / `ap` / `dp`
   = 0 means "no gate", `null` means "gate not sourced" and the row gates as
   `unknown` (shown between eligible and locked, with "gate not sourced" or
   "set X on Progress"). Jetina AP/DP, LoML and Garmoth level/AP/DP and Edania
   level are null: research 0003 gives no number. Alternatives: invent
   community "recommended AP" numbers; drop the rows. Why: the brackets
   precedent (plan 023: null = not transcribed, never a guess); the
   acceptance fixture (Lv 58 / 240 AP) still shows only Black Shrine
   eligible. Reverses if: a sourced gate is found - fill the number and
   `verified` date.
2. `verified` is a `YYYY-MM-DD` source date (plan 023 style), not the plan 021
   bool; the URL lives in `source`. Why: the plan asks for URLs and dates.
   Reverses if: a shared schema for sourced rows is adopted.
3. `POST /api/today {"weekly_untick": id}` added beside `weekly_tick` (undo a
   mis-click; removes the newest tick of the period). A tick at the cap or an
   untick at 0 is a 400. Ticks live in their own store domain `weekly` (the
   `today` domain's save rewrites the doc and would drop extra keys); only the
   current period is kept on write. Every `/api/today` answer (GET and every
   POST op) carries `weekly_plan`. Reverses if: undo is moved elsewhere.
4. Gate state is `eligible | unknown | locked` with `needs` (positive gaps),
   `unknown` (stats) and `bracket` ({ap, to_next, next_gain} from the plan 023
   summary, only when AP is a gap). `ap_kind` `kutum` reads sheet AP (gs.ap,
   with the Kutum sub equipped); `aap` reads gs.aap. LoML `per_week` = 4 (one
   kill of each of the four boss types). Level is the higher of the plan 004
   character level and the plan 011 sample (same rule as Progress).
5. Merge onto main after plan 030: `server/ew/app.py` import list conflicted
   (main added `settings`, this lane `weekly`). Decision: union, both kept.
   Alternatives: none viable (dropping either breaks a feature). Why: the
   two plans touch disjoint routes. Reverses if: never (mechanical).

Dependency guard: before writing code the lane checks that `last_reset` exists in `server/ew/today.py` (plan 021); `server/ew/brackets.py` exists (plan 023). If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["021", "023"]` into its progress JSON (`ops/loop/control/progress/p033-build.json`) and exits 0. Plan 019's tick turns that clean, marked run into item state `blocked` (not `no-change`) and re-dispatches the row once every Depends-on row is `[x]`; plan 019's work-list gate normally keeps the row from being dispatched that early. If this row ran before plan 019 landed and was recorded `no-change`, 019's re-arm step makes it dispatchable again.
