# Plan 044 - Mount tracker: horses (tier, level, skills), Royal Fern Root counter, T10 breed pity calc

Status: open. Deep dive + UX audit 2026-10-05 session 4 (research 0003-0005). Lane hint: `build`.

Spec gap: research 0003 C17 (value 2, M) and section 1.5. Tier 10 breeding
needs two T9 Lv 30 parents of the same type, a Mythical Censer, 100 Royal
Fern Roots (time-gated), 100 Flowers of Oblivion and 10 Mythical Feathers;
success 3 percent base, +0.2 percent per failure (BDFoundry 2026-07-03).
Camel / mini elephant are quest unlocks.

1. `server/ew/data/mounts.json` (tracked, sourced): T10 recipe, pity rule,
   camel and elephant unlock notes, `verified` per row.
2. `server/ew/mounts.py` + store: mounts `{name, kind, tier, level, gender,
   skills [..]}`; material counters `{royal_fern_root, flower_of_oblivion,
   mythical_feather, censer}`; `breed_odds(failures)` and
   `attempts_for(p_target)` with the +0.2 per failure pity.
3. Routes `GET/POST /api/mounts`; card "Mounts" on the Progress tab with
   "materials 63/100 fern roots, ~N days at the daily rate typed".
4. Tests: `tests/test_mounts.py` (validation, pity math vs brute force,
   schema).

Acceptance: tests green; gates green; verifier PASS within 3 rounds.

ToS check: operator-typed data, sourced static data.

Depends on: none.

## As-built deviations

Self-adjudicated by the build lane (2026-10-05).

1. Pity state: the failure count since the last success is stored (store
   domain `mounts`, POST op `failures`; the card's "+1 fail" / "reset"
   buttons). `breed_odds(failures, pity)` and `attempts_for(p_target,
   failures, pity)` take the pity row from the data file instead of
   hard-coding 3 / 0.2; `expected_attempts` and `cumulative` were added for
   the card. Math runs on exact fractions. Alternative: stateless
   calculator with a typed failure count per visit. Why: the count is the
   one number the operator must not lose between sessions. Reverses if: the
   game starts showing the pity in the breed UI.
2. Fern rate: the "daily rate typed" is a separate op `fern_rate` (number in
   (0, 100] or null, decimals allowed for weekly sources); days =
   ceil(left / rate). Alternative: fold it into `materials`. Why: materials
   are whole counts keyed by the data file; the rate is not a material.
   Reverses if: a second time-gated material needs a rate too.
3. Kinds: `horse`, `donkey`, `camel`, `elephant`, with per-kind level caps
   from the data file (camel 20 per BDFoundry 2025-03-26, others 30). Plan
   named only horse / camel / elephant. Why: donkeys are ordinary owned
   mounts; the cap stops a camel Lv 30 typo. Reverses if: a kind needs a
   different cap source.
4. T10 readiness (`t10.ready.parents`) checks one male + one female T9 Lv 30
   horse; "same type" is not checked because a mount row has no breed-type
   field (the note is shown in the pill title). Reverses if: a `type` field
   is added to mount rows.
5. `t10.per_attempt` row is `verified: false`: the source does not say
   whether a failed attempt consumes all materials, so the card shows one
   attempt's need only. Reverses if: a sourced page states consumption.
6. The Mounts card sits on the Progress tab after Profile, before the track
   cards; POST goes through the dashboard bridge (`/api/mounts` added to the
   ewcore allowlist with an exact-shape validator). Alternative: ops under
   `/api/progress`. Why: plan names `/api/mounts`; a separate route keeps the
   progress validator untouched.
