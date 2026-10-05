# Plan 032 - World boss overlay widget + Today card + 5/15-min notification

Status: open. Deep dive + UX audit 2026-10-05 session 4 (research 0003-0005). Lane hint: `build`.

Spec gap: research 0003 C04 second half (overlay widget) - the schedule from
plan 031 is only useful at a glance.

1. Overlay widget `worldBoss` (opt-in, `WIDGETS` in `ewcore.js`): next spawn
   name(s) and countdown, the following one muted; bosses already ticked
   "looted" today are greyed; Garmoth shows `n/3`.
2. Dashboard: a "World bosses" card on the Today tab (and on Home when plan
   025 is present): next 3 with countdown, tick buttons, week's Garmoth
   count; pure formatter `fmtBossRow` in `ewcore.js`.
3. Notification rule `bossSoon` (minutes before: 5 / 15, default off) added
   to plan 026's `notifyRules`; if plan 026 is absent the card still works
   and the rule is skipped.
4. Tests: `app/test/bosses.test.js` (formatter, greyed rows, countdown at
   wrap), rule test inside `app/test/notify.test.js` when present.

Acceptance: node tests green; self-test with `worldBoss` on fits the
overlay; gates green; verifier PASS within 3 rounds.

ToS check: overlay click-through, data from plan 031; nothing read from the
game's own boss notification.

Depends on: 031, 026.

Dependency guard: before writing code the lane checks that `server/ew/bosses.py` exists (plan 031); `notifyRules` exists in `app/shared/ewcore.js` (plan 026). If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["031", "026"]` into its progress JSON (`ops/loop/control/progress/p032-build.json`) and exits 0. Plan 019's tick turns that clean, marked run into item state `blocked` (not `no-change`) and re-dispatches the row once every Depends-on row is `[x]`; plan 019's work-list gate normally keeps the row from being dispatched that early. If this row ran before plan 019 landed and was recorded `no-change`, 019's re-arm step makes it dispatchable again.
