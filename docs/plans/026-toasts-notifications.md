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
