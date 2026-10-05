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

## As-built deviations (lane build, 2026-10-05)

Dependency guard passed: `server/ew/bosses.py` (031) and `notifyRules` (026)
both present.

1. Server field `today.slots` added to GET /api/bosses (every slot of the PT
   day with `up` / `past`). Decision: additive field, tested in
   `tests/test_bosses.py`. Alternatives: tick buttons only on `remaining`
   (a boss becomes un-tickable 30 min after spawn, i.e. usually before the
   operator gets to it); a client copy of the table (two sources of truth).
   Why: looting is ticked after the kill, often after despawn. `C.bossTicks`
   falls back to `remaining` `up` rows on an older server. Reverses if: plan
   031's view gains its own full-day list.
2. `bossSoon` has no 5-or-15 setting: one boolean (`notify.bossSoon`, default
   off) fires twice per spawn, at 15 min and at 5 min before, each once via
   the ledger key `bossSoon:<at_utc>:<15|5>`. A spawn whose bosses are all
   looted (or Garmoth at its weekly cap) stays quiet. Alternatives: a
   `notify.bossSoonMin` number (prefs reach the sandboxed dashboard as a list
   of enabled rule names, so a number needs a new launch-argument shape);
   two rules `bossSoon5` / `bossSoon15`. Why: smallest change that honours
   "5 / 15" inside plan 026's boolean prefs contract. Reverses if: the
   operator asks for only one of the two pings.
3. Home card (plan 025 present) is read-only: next 3 with countdown and the
   Garmoth meta, no tick buttons; ticks live on the Today card. Why: Home's
   one write is the Today daily tick (guarded by `home.test.js`); a second
   write route there widens its surface for no glance value. Reverses if:
   the operator wants boss ticks on Home.
4. The card is its own module `app/dashboard/bosses.js` (`window.EWBosses`),
   mounted by `today.js` between Events and Add item. Why: keeps today.js's
   single-route guard (`/api/today`) intact.
5. `/api/bosses` joins the dashboard bridge allowlist (`validBossesBody`:
   exactly `{tick|untick: {boss, day}}`, ASCII boss name <= 40, real ISO
   day). Plan 031 shipped the server route but not the bridge entry.
6. Desktop self-test with `worldBoss` on was not run in the headless lane
   (needs the Electron desktop session); the widget adds at most two
   overlay rows, well inside `OVERLAY_HEIGHT` (max 600 px). The next
   interactive session's self-test covers it. Reverses if: that self-test
   reports the overlay outside its work area.
7. Merge onto main after plan 030 (Settings) - resolve lane. Conflict in
   `app/shared/ewcore.js` `POST_VALIDATORS` / `POST_LABELS`: both sides
   appended one route (`/api/settings`, `/api/bosses`); kept both. Not a
   textual conflict but a semantic one: plan 030's settings allowlist
   (`server/ew/settings.py` `WIDGETS` / `NOTIFY`, client
   `NOTIFY_RULES_PREFS`) did not know `worldBoss` or `bossSoon`, so
   `settings.test.js` (client vs server allowlist) failed and the Settings
   tab could not toggle either. Decision: add `worldBoss` (default false) to
   server `WIDGETS` and `bossSoon` (default false) to server `NOTIFY` and
   client `NOTIFY_RULES_PREFS`. Alternatives: drop `worldBoss` from the
   client allowlist (Settings could not enable the widget); leave `bossSoon`
   config-file-only. Why: keeps both features whole; defaults match
   `config/local.example.json`. Reverses if: plan 030 moves to deriving its
   allowlist from one shared table.

Dependency guard: before writing code the lane checks that `server/ew/bosses.py` exists (plan 031); `notifyRules` exists in `app/shared/ewcore.js` (plan 026). If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["031", "026"]` into its progress JSON (`ops/loop/control/progress/p032-build.json`) and exits 0. Plan 019's tick turns that clean, marked run into item state `blocked` (not `no-change`) and re-dispatches the row once every Depends-on row is `[x]`; plan 019's work-list gate normally keeps the row from being dispatched that early. If this row ran before plan 019 landed and was recorded `no-change`, 019's re-arm step makes it dispatchable again.
