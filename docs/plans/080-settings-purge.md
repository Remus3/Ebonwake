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

## As-built deviations

Self-adjudicated by the build lane (2026-10-06); each reverses only as stated.

1. Notification rules: plan said "notify.<rule> x 8"; the server had 10
   (`marketAlert`, `buffEnding` plus 8 default-off). Decision: all 10 removed
   and every rule `defaultOn: true` in `ewcore.NOTIFY_RULES`; a config
   `notify.<rule>` boolean is IGNORED, not read as an incident switch.
   Alternatives: import each as a 24 h plan 079 entry (the client reads
   rules from the config file in the Electron main process, not through the
   server, so this needed a new server->launch-arg path). Why: the single
   `notify.mute_until` is the incident control for alerts; a forgotten
   per-rule false is exactly what the plan removes. Reverses if: the operator
   asks for per-rule silencing back.
2. `overlay.widgets.*` in config: ignored by the server (no manual layout
   branch, as planned). An incident `overlay.auto: false` shows the PINNED
   widgets only (no context rules), not a stored layout. The renderer's
   `overlayWidgets(config)` start-up fallback (shown only until the first
   context payload, plan 067) is unchanged. Alternatives: fall back to the
   default widget set; strip config reads from the renderer fallback too.
   Why: pins are the operator's remaining explicit choice; the renderer
   fallback lasts seconds and its six test files guard plan 022 behaviour.
   Reverses if: a later plan drops the query fallback.
3. Incident switches = the 7 kill switches + the 6 tunables (13 keys,
   `settings.FIXED`, policy rows `days:1` + new `first_sight: true`, cards
   `shell` + the owning card). `from_config` sets `set_at` = first read (not
   the file mtime) for `first_sight` rows, so a months-old config still gets
   its 24 h. `events.maintenance_start_utc` stays a config-imported ledger key
   (`until:maint_end`, mtime-based as in plan 079); it is no longer typable.
   Why: matches "forced 24 h expiry from first sight". Reverses if: never.
4. `coupons.check` / `events.notice_check` incident switches apply at server
   start (the clients are dropped when the effective value is false); expiry
   restores them at the next start. Alternatives: gate every client call live
   (about 20 call sites). Why: these were already restart keys; restarts are
   allowed any time (standing order 11). Reverses if: a live toggle is needed.
5. Settings groups: plan named five (Folders, Overlay placement, Hotkeys,
   Profile, Display) but `market.vp` / `market.fame_pct` stay settable (plan
   079 ledger keys, not in the removal list). Decision: they join Profile;
   `notify.mute_until` joins Display as a select (off / 1 h / 8 h / 24 h, an
   active mute kept). Why: the 5-group cap. Reverses if: never.
6. Mute delivery: `GET /api/prompts` carries `muted_until` (effective value,
   null once past); `C.promptGate(hits, game, prompts, nowMs)` DROPS every hit
   while muted (consumed, nothing fires late). Shell badge: `EWOverrides.mount`
   on the header with policy card `shell` (also `ui.scale` with expiry `none`,
   added to `overrides.NONE_OK`, and every incident switch).
7. `app.fixed_settings()` (effective settings without live-signal probes) is
   what the plan 062 / 063 / 064 / 068 / 070 readers use: the VP live probe
   reads the grind view, which re-enters the play-session lock held by its
   own listener (a deadlock found by `test_server_sse_timeline_no_settings_write`).
8. `ui.theme` default `system`; `themeAttr` treats an unset theme as system
   (Chromium's `prefers-color-scheme` follows Electron `nativeTheme`). The
   unused `ladder` field type and the per-rule labels were dropped from the
   form model; the `hhmm` type and `settingLocalNote` stay as generic helpers.
9. `config/local.example.json`: also dropped the `ocr.auto*` keys (now
   incident-only) besides `ocr.engine`; `notify` keeps `silent` and gains
   `mute_until`.
10. `POST /api/settings {"clear": key}` also accepts the FIXED incident keys:
    it retires the ledger entry only (config untouched, never re-imported);
    `restart` names `coupons.check` / `events.notice_check`.
11. Merge-gate refute round 1 (`test_dice_never_auto_ticks_today`): not a
    plan 080 regression - a wall-clock flake. The test logged in at
    `time.time() - 3700`, but played time counts from the 05:00 UTC dice
    reset, so a run within ~62 min after 05:00 UTC earned 1, not 3. Decision:
    pin the test to `today_clock` = 12:00 UTC (the DiceClock already takes
    it). Alternatives: skip near the reset; change DiceClock. Why: the code
    is right, the test was clock-dependent; no other test uses
    `time.time() - N` (swept). Reverses if: never.

12. Session merge 2026-10-07 (refs/ew/keep/080 onto main after 081, 082 and
    hand-off Hdcc881, loop state merge-conflict). Conflicts: ewcore.js,
    profile_gate.test.js, local.example.json, app.py, settings.py,
    test_gamewatch.py. Decision: 080's deletions applied, main's additions
    kept - `profile.multi_character` (Hdcc881, plan 074 one-character fact)
    stays in Profile (settable, not an automation switch or tunable);
    plan 081 `market.fame_pct` live signal joins 080's `live=` gate (so
    `fixed_settings()` reads no live signal); `ui.theme` default `system`;
    `base_url` blank. Seam test: `test_get_lists_no_removed_key` now allows
    the two operator-fact bools (`profile.multi_character`, `market.vp`);
    same in `app/test/settings.test.js` (no-checkbox test).
    Alternatives: drop multi_character as a toggle; rebuild 080 on main.
    Why: it is a fact about the operator, not a switch that gets forgotten;
    the conflicts were textual. Reverses if: the operator wants the
    one-character fact fixed instead of settable.

Verification: refute-rounds 3/3. Round 1: stale signal hints / docs and the
start-read badge wording (fixed). Round 2: three stale "setting" docstrings
(fixed). Round 3: two more comment-only docstrings (`playsession.py`,
`prompts.parse_ladder`). Adjudicated by the lane at the cap: fix both
(comment-only, no behaviour change, gates re-run green) and accept - no
round 4. Alternatives: leave the comments; hand to a session adjudicator.
Why: zero-risk text fixes inside the plan's scope. Reverses if: never.
