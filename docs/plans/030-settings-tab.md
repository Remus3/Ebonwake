# Plan 030 - Settings tab + allowlisted POST /api/settings (overlay, hotkeys, profile, theme, scale, notifications)

Status: open. Deep dive + UX audit 2026-10-05 session 4 (research 0003-0005). Lane hint: `build`.

Spec gap: research 0005 candidate F4 (value 4, M), M7 (overlay widgets need
a hand edit of `config/local.json` and an app restart) and M13 (theme forced
dark, no scale control).

1. `server/ew/settings.py`: an allowlist of non-secret keys with validators:
   `overlay.widgets.*` (bool), `overlay.anchor` / `display` / `scale` /
   `opacity` (plan 022 validators), `hotkeys.toggleOverlay` /
   `showDashboard` (Electron accelerator grammar, no bare letters),
   `profile.family` (1-32 chars, `[A-Za-z0-9_]`), `ui.theme`
   (`system|dark|light`), `ui.scale` (0.9-1.3), `notify.*` (bool, plan
   026), `market.vp` / `market.fame_pct` (plan 027 when present),
   `coupons.check` (bool). Anything else -> 400. Keys under `secrets`, any
   `{"env": ...}` reference and `loop.*` are never readable or writable here.
2. `GET /api/settings` returns the allowlisted subset with defaults;
   `POST /api/settings {"set": {"a.b": v, ...}}` validates all, then writes
   `config/local.json` atomically (tmp + replace), preserving every other
   key and key order.
3. Dashboard `app/dashboard/settings.js` (tab `settings`, placed last):
   grouped form (Overlay, Hotkeys, Profile, Appearance, Notifications,
   Market), save -> toast; overlay changes call IPC `ew:reload-overlay`
   (allowlisted, no payload) so main re-reads config and recreates the
   overlay; hotkey changes re-register `globalShortcut` and report a
   conflict back.
4. Theme and scale: `data-theme` from `ui.theme` (system follows
   `prefers-color-scheme`), `webContents.setZoomFactor(ui.scale)`; minimum
   text 12 px in `ew.css` (M13).
5. Tests: `tests/test_settings.py` (allowlist, each validator, secrets
   untouched byte-for-byte, unknown keys preserved, atomic write);
   `app/test/settings.test.js` (form model pure helpers).

Acceptance: a settings round trip leaves `secrets` and `loop` blocks
byte-identical; overlay reload works without app restart at merge; gates
green; verifier PASS within 3 rounds.

ToS check: local config only; hotkeys stay Electron `globalShortcut` (no
low-level hooks); secrets stay env references (standing order 12).

Depends on: 022.

## As-built deviations (self-adjudicated by the build lane, 2026-10-05)

1. `profile.family` takes `progress.FAMILY_RE` (2-16 chars, `[A-Za-z0-9_]`)
   or `""` (clear), not 1-32. Alternatives: the plan's 1-32. Why: the
   profile client already rejects anything outside 2-16, so a 1-32 name
   would save but never fetch; `""` is the only way to unset. Reverses if:
   BDO family names outgrow 2-16 (change `FAMILY_RE` once, both follow).
2. Writes splice the new value text in place (`settings.splice`) instead of
   re-serializing the doc. Alternatives: `json.dumps` of the whole doc.
   Why: the acceptance wants `secrets` / `loop` byte-identical; splicing
   keeps every untouched byte (spacing, `_doc`, key order) identical, and
   the result is re-parsed and compared to the intended doc before the
   atomic replace. Reverses if: never (strictly stronger).
3. Form-model pure helpers live in `app/shared/ewcore.js`
   (`SETTINGS_GROUPS`, `validSettingsBody`, `parseSettingInput`,
   `settingsBody`, `settingsEffects`, `themeAttr`, `uiScale`), tested by
   `app/test/settings.test.js`; `app/dashboard/settings.js` is DOM only.
   Why: repo convention (every tab's logic is in ewcore and node-tested).
   Reverses if: ewcore is split per module.
4. Two payload-free IPCs: `ew:reload-overlay` (recreate the overlay, same
   visibility) and `ew:reload-shell` (re-register changed hotkeys + re-apply
   `setZoomFactor(ui.scale)`; returns `{ok, conflicts}`, keeps the old
   hotkey on a conflict). Alternatives: one combined channel. Why: the plan
   names `ew:reload-overlay`; hotkeys and zoom are a separate concern. Both
   are dashboard-sender-checked; main re-reads `config/local.json` itself.
   Reverses if: a later plan folds all reloads into one channel.
5. Save feedback is an inline status pill in the Settings save bar, not a
   toast: plan 026's `toastQueue` is not on main yet. Reverses if: 026
   lands (switch `say()` to `toast()`).
6. Defaults: `ui.theme` `dark` (keeps today's look; `system` is one click),
   `notify.*` per plan 026 (on: `marketAlert`, `buffEnding`),
   `coupons.check` true. `market.*` applies live on save; `profile.family`
   and `coupons.check` are read at server start, so the POST reply lists
   them under `restart` and the tab says so (`coupons.check` false now keeps
   the coupon client off in `app.main()`). `data-theme` is set by
   `settings.js` from `GET /api/settings` on page load (not by main).
7. Extra checks: the two hotkeys must differ (400); at most 64 keys per
   POST; `overlay.display` capped at 16; an unparsable or non-object
   `config/local.json` is refused (400), never clobbered. GET also returns
   `defaults`, `restart_keys` and `error`.
8. `config/local.example.json` not edited (parallel lane 026 owns the
   `notify` block there); `GET /api/settings` serves every default.
   Reverses if: a docs pass wants the example to list `ui` / `coupons`.

Dependency guard: before writing code the lane checks that `overlayConfig` exists in `app/shared/ewcore.js` (plan 022). If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["022"]` into its progress JSON (`ops/loop/control/progress/p030-build.json`) and exits 0. Plan 019's tick turns that clean, marked run into item state `blocked` (not `no-change`) and re-dispatches the row once every Depends-on row is `[x]`; plan 019's work-list gate normally keeps the row from being dispatched that early. If this row ran before plan 019 landed and was recorded `no-change`, 019's re-arm step makes it dispatchable again.
