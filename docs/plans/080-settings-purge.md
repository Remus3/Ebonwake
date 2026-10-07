# Plan 080 - Settings purge: delete kill switches, manual overlay layout, per-rule notify toggles and tunables

Status: open. Zero-touch audit 2026-10-06 (research 0010 H2, H3, H4, L1, L2, L3, L4, L7, L8). Lane hint: `build`.

Spec gap: operator order 2026-10-06 - toggling is not wanted; a changed
setting gets forgotten. Settings (030) exposes 54 keys. Seven are one-click
kill switches for automation (`ocr.auto`, `play.auto_session`,
`notices.auto_add`, `events.notice_check`, `coupons.check`,
`checklist.auto`, `overlay.auto`), nine are a manual overlay layout used
only when context mode is off, eight are per-rule notification opt-ins
(four default off, so zero config gives no boss / reset / Hot Time alert),
six are internal tunables.

1. `server/ew/settings.py` SPEC: remove from the settable allowlist
   - the seven kill switches (values fixed `true`),
   - `overlay.widgets.*` and `overlay.auto` (context always on; manual
     layout code path in `server/ew/context.py` deleted),
   - `notify.<rule>` x 8 (fixed policy: every rule on while logged in,
     plan 070 game-closed quiet and ladder govern volume),
   - tunables `ocr.auto_commit_min`, `ocr.daily_cap`, `play.grace_s`,
     `overlay.idle_min`, `notify.ladder_min`, `notify.quiet_closed`
     (fixed at their current defaults),
   - `events.maintenance_start_utc` (now a plan 079 ledger key only).
   Added: `notify.mute_until` (ISO UTC, max now + 24 h; plan 079 entry,
   badge on the shell while active).
2. Config file: the removed keys are still READ from `config/local.json`
   as incident switches, but only through plan 079 `from_config` (badged,
   listed in the digest) with a forced 24 h expiry from first sight; after
   expiry the fixed value applies and the digest says so.
3. `ui.theme` default `system` (Electron `nativeTheme`); `ui.scale` stays,
   badged via 079 when not 1.0.
4. `config/local.example.json`: drop `overlay.widgets`, `notify` rules,
   `ocr.engine`, `market_watch`; `profile.base_url` blank to match SPEC
   (blank = off); `_doc` lines say "blank = auto".
5. `app/dashboard/settings.js` / `ewcore.js` settings groups: removed
   keys disappear; remaining groups: Folders (detected value + "use other"),
   Overlay placement, Hotkeys, Profile, Display. Overlay per-widget rows
   from plan 078 stay (pin / block are 079 overrides with a 7 d expiry).
6. Tests:
   - `tests/test_settings.py`: every removed key rejected by POST with
     "not a settable key"; GET lists no removed key; zero-config defaults
     produce every notification rule enabled.
   - `tests/test_context.py`: no manual-layout branch; a stored
     `overlay.auto: false` in config is a 079 entry and expires in 24 h.
   - `tests/test_overrides.py`: config incident switch badged, expires.
   - `node --test` `app/test/settings.test.js`: Settings renders at most
     5 groups and no checkbox for a removed key.

Acceptance: Settings has no automation switch, no notification checkbox,
no manual overlay layout and no tunable; a fresh install alerts for bosses,
resets and Hot Time with zero config; an old config with `ocr.auto: false`
shows an override badge and auto-OCR resumes after 24 h; gates green;
verifier PASS within 3 rounds; one push.

ToS check: removes controls only; the overlay stays click-through and
`globalShortcut`-toggled; nothing reaches the game.

Depends on: 030, 067, 070, 079.

Dependency guard: before writing code the lane checks that
`server/ew/overrides.py` (plan 079) and `server/ew/context.py` (plan 067)
exist. If either is missing, the lane changes nothing, writes
`"status": "blocked", "needs": ["067", "079"]` into its progress JSON
(`ops/loop/control/progress/p080-build.json`) and exits 0.
