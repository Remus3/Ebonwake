# Plan 026 - Toasts + opt-in Windows notifications (alert hit, buff ending, Hot Time, reset, coupon, game exit)

Status: open. Deep dive + UX audit 2026-10-05 session 4 (research 0003-0005). Lane hint: `build`.

Spec gap: research 0005 candidate F3 (value 5, effort M), M9 (feedback far
from the action, no global toast) and section 3 "missing glanceable
information": a market alert hit, a buff with 5 min left, Hot Time starting,
a reset passing, a new coupon suggestion and "game exited with a grind
session running" reach the operator nowhere outside their own tab.

1. Toasts: `app/shared/ewcore.js` `toastQueue` (pure: push, expire, max 4,
   dedupe by key) + a toast region in `app/dashboard/index.html`; every
   module's POST result goes through `toast(ok|warn|bad, text)`; Today tick
   failures render inline on the row (fix M9) as well.
2. Rule engine (pure, node-tested) `notifyRules(prev, next, nowMs, prefs)`
   -> `[{key, rule, title, body}]` over the same payloads the dashboard
   already polls. Rule names (stable; later plans and the `notify.*` prefs
   use them): `marketAlert` (threshold crossed, plan 002), `buffEnding`
   (buff remaining <= 5 min, plan 005), `hotTime` (Hot Time window starts,
   plan 011), `resetPassed` (daily/weekly reset passed, plan 003/021),
   `newCoupon` (new coupon suggestion, plan 014), `gameExit` (game state
   running -> not running while a grind session is open, plan 008). Rules
   are registered in an exported `NOTIFY_RULES` table so later plans add
   rules (e.g. 032 `bossSoon`) without editing the engine. Each key fires
   once (dedupe ledger in memory, cleared on reset).
3. OS notifications: new allowlisted IPC `ew:notify {title, body}` in
   `app/preload.js` / `app/main.js` -> Electron `Notification` (silent
   option, no actions, clicking focuses the dashboard). Main validates
   lengths (title <= 64, body <= 200, ASCII-printable) and rate-limits to
   6 a minute.
4. Preferences: `notify` block in `config/local.json` (example updated):
   per-rule on/off keyed by rule name (`notify.gameExit`, ...), default all
   off except `marketAlert` and `buffEnding`;
   plan 030 later exposes them in Settings.
5. Tests: `app/test/notify.test.js` (each rule, dedupe, prefs off, rate
   limit helper), `app/test/shell.test.js` asserts the new IPC channel is
   on the allowlist and nothing else was added.

Acceptance: node tests green; manual merge check: one toast and one OS
notification fire from a synthetic alert (self-test hook); gates green;
verifier PASS within 3 rounds.

ToS check: OS-level notifications from EW's own data; never a key or click
to the game; overlay unchanged.

Depends on: none.

## As-built deviations

Self-adjudicated by the build lane (2026-10-05); each line: decision /
alternatives / why / reverses if.

1. DOM half of toasts lives in a new `app/dashboard/toast.js`
   (`window.EWToast`), loaded right after `ewcore.js`; `ewcore.js` holds only
   the pure `toastQueue` + `postToast`. Alt: `toast()` inside ewcore. Why:
   ewcore is DOM-free and node-tested. Reverses if: ewcore gains a DOM layer.
2. Module POSTs go through `window.EWToast.via(b).post(route, body)` (same
   reply as the bridge, plus one toast); inline form messages stay. Alt:
   `EWToast.post(b, route, body)`. Why: keeps the existing `.post('/api/..'`
   route-pinning tests valid unchanged. Reverses if: those tests are retired.
3. `notify.*` prefs gate the rule hit as a whole (toast and OS notification).
   Alt: always toast, gate only the OS notification. Why: a rule the operator
   switched off should stay quiet everywhere. POST-result toasts are never
   gated. Reverses if: plan 030 Settings splits toast vs OS switches.
4. Prefs reach the sandboxed dashboard as a launch argument
   (`--ew-notify=<enabled names>`, preload `notifyPrefs()`), like the plan 020
   commit. Alt: a second IPC channel. Why: plan item 5 allows exactly one new
   channel (`ew:notify`). Reverses if: plan 030 needs live pref edits (then an
   IPC read is due).
5. Added `notify.silent` (default true) as the plan's "silent option". Alt:
   always silent. Why: no sound over the game by default, operator can opt in.
   Reverses if: operator asks for a fixed behaviour.
6. Transition rules (marketAlert, hotTime, resetPassed, newCoupon, gameExit)
   treat the first snapshot as a baseline and fire only on a change seen
   while the dashboard runs; buffEnding is state-based (fires on first look).
   Alt: fire every active condition at app start. Why: no burst of stale
   notifications on every launch / restart. Reverses if: the operator wants
   "alerts already active" on launch.
7. resetPassed also covers plan 021 per-item reset rules (`Reset: <title>`),
   from `/api/today`. Alt: daily/weekly only. Why: plan names 003/021.
8. The dedupe ledger empties at the UTC daily reset ("cleared on reset");
   keys carry the arming (buff `ends`, item reset time, game `since`) so a
   re-armed buff or a second exit fires again the same day.
9. The notify loop polls on its own (game 5 s, market/grind/leveling/events/
   today 60 s; server caches arsha at 300 s) instead of hooking each tab's
   poll. Alt: tap the tab modules. Why: tabs only poll while mounted/shown;
   notifications must not depend on the open tab.
10. hotTime watches the plan 011 leveling hot windows; the grind "Hot Time"
    buff is covered by buffEnding. Alt: both. Why: one source per event.
11. Desktop self-test fires one synthetic market alert after the tab captures
    and REPORTS `res.notify {hits, toasts, bridge, osShown, ok}` without
    failing the run. Alt: part of `res.ok`. Why: OS notification support
    varies by host (Focus Assist, unsupported); the manual merge check reads
    the field. Reverses if: a CI desktop runner can assert notifications.
