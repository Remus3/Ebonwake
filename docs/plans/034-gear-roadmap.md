# Plan 034 - Post-graduation gear roadmap track + graduation readiness + adventure-log seeds

Status: open. Deep dive + UX audit 2026-10-05 session 4 (research 0003-0005). Lane hint: `data`.

Spec gap: research 0003 C03 (value 5, effort M, top 3), folding the DUP-part
candidates C07 (graduation readiness) and C11 (Fughar / Igor / Emma
adventure logs). Plan 004 ships one Tuvala -> PEN track; the free-gear path
after the season (graduate -> Olvia Blackstar -> Alustin Kharazad -> Emma
OCT -> Jetina -> Sovereign -> Igor +6 AP/DP -> Black Shrine Origin armour ->
Hammer Challenge) lives only in a guide.

1. `server/ew/data/tracks/` (tracked, one JSON per track): `gear_roadmap`,
   `graduation_readiness`, `fughar_journal`, `igor_bartali`,
   `emma_bartali`; each step `{id, title, min_level?, ap?, dp?, note,
   source, verified}` from research 0003 sections 1.1/1.2 (BDFoundry
   new-player guide 2026-08-11 and linked pages); unverified steps carry
   `verified: false`.
2. `server/ew/progress.py`: `POST /api/progress {"track_seed": id}` adds a
   seeded track to the store as an ordinary plan 004 custom track (steps
   copied, later edits are the operator's); seeding the same id twice is a
   no-op. `GET /api/progress` lists available seeds and which are added.
3. Gates: each step's `min_level` / `ap` / `dp` is shown against the stored
   character as `ready` / `needs +N` (with plan 023 bonus-AP hint).
4. Dashboard (`app/dashboard/progress.js`): "Add track" select listing the
   seeds; gate chips on steps; `verify` badge on unverified steps.
5. Tests: `tests/test_progress.py` additions (seed idempotent, gates, data
   schema per file: ids unique, ASCII, every row sourced).

Acceptance: tests green; seeding all five tracks on a fresh store renders
within the Progress tab at 1264x761 (self-test at merge); gates green;
verifier PASS within 3 rounds.

ToS check: sourced static data and operator ticks only.

Depends on: 023.

## As-built deviations

Self-adjudicated by the build lane 2026-10-05; each names what reverses it.

1. Store tracks carry `seed_id` (null on operator / plan 004 tracks). Decision:
   one extra key marks a seeded track so "added" and idempotence need no title
   match. Alternatives: match by title (breaks on a clash or a rename), a
   separate store domain (two sources of truth). Reverses if: a plan adds track
   rename and wants re-seeding to follow the rename.
2. Step `verified` is a `YYYY-MM-DD` page stamp or `false`; tracks also carry
   track-level `source` / `verified` / `note`. Steps take `{id, title,
   min_level?, ap?, dp?, note, source, verified}` exactly; anything else fails
   `validate_seed`, and a malformed stored field is dropped, never guessed.
   Reverses if: research adopts a richer provenance shape fleet-wide.
3. Data is transcribed from research 0003 (its URLs and page dates), not
   re-fetched this run. Igor / Emma chapter lists are not in 0003, so their
   unlock and chapter steps are `verified: false`; Fughar's four known tasks
   (Kratuga 777 kills 250 AP / 310 DP, amity, 1 B silver show, 55 Calpheon
   topics) are separate steps because their chapter is not recorded.
   Reverses if: a deep dive transcribes the chapter lists (replace the rows).
4. Seed kinds are `quest` / `gear` only (no `season` seed). Graduation
   readiness is a `quest` track, not folded into the plan 013 pass objectives
   as research 0003 C07 suggested: plan 034 lists it as its own seed file.
   Reverses if: the operator wants it inside the season card.
5. Gates: level = the higher of the plan 011 sample and character level (same
   rule as plan 013 auto-ticks); `ap` compares sheet AP only (not AAP); a step
   is `ready: true` only when every gate is met, `false` when any needs more,
   `null` when it has no gate or a stat is unset. The plan 023 hint is
   `bonus_gain` on the AP gate (bonus AP at the gate minus bonus AP now, null
   when either bracket is untranscribed). Chips hide on done steps.
   Reverses if: a seed needs an AAP-specific gate (add `aap`).
6. Seed-file errors are kept on the service (`seed_errors`) and skip the file;
   they are not in the GET body, matching plan 023's `bracket_error`.
   Reverses if: the Server card grows a data-file health row.

Dependency guard: before writing code the lane checks that `server/ew/brackets.py` exists (plan 023). If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["023"]` into its progress JSON (`ops/loop/control/progress/p034-build.json`) and exits 0. Plan 019's tick turns that clean, marked run into item state `blocked` (not `no-change`) and re-dispatches the row once every Depends-on row is `[x]`; plan 019's work-list gate normally keeps the row from being dispatched that early. If this row ran before plan 019 landed and was recorded `no-change`, 019's re-arm step makes it dispatchable again.
