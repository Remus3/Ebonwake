# Plan 013 - Season pass tracker by objective

Status: open. Ranked third (adjudicated 2026-10-05).

Spec gap: plan 004 seeds the season pass as coarse level steps. A leveling
Season player works the pass objective by objective (reach level N, enhance
Tuvala gear to +X, finish quest Y) and wants "next unclaimed reward".

1. Track kind `season` gains objectives: `{id, title, kind: level|gear|quest|
   other, target?, reward, done_at?, claimed_at?}`. Level objectives auto-tick
   from plan 011 level samples or the plan 004 character level.
2. Seed = structure only (titles + kinds), editable, labelled "seed, verify
   against the in-game pass"; the operator edits and ticks.
3. Progress tab card: n/N, next 3 open objectives, "done but unclaimed"
   highlighted (claiming stays the operator's act in game).
4. Overlay opt-in line: "Pass 23/40 - next: Lv 50 (2 lv)".

Acceptance: migration from the plan 004 coarse steps keeps done marks; gates
green; verifier PASS within 3 rounds; one push.
