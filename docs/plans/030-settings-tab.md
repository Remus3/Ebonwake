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

Dependency guard: before writing code the lane checks that `overlayConfig` exists in `app/shared/ewcore.js` (plan 022). If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["022"]` into its progress JSON (`ops/loop/control/progress/p030-build.json`) and exits 0. Plan 019's tick turns that clean, marked run into item state `blocked` (not `no-change`) and re-dispatches the row once every Depends-on row is `[x]`; plan 019's work-list gate normally keeps the row from being dispatched that early. If this row ran before plan 019 landed and was recorded `no-change`, 019's re-arm step makes it dispatchable again.
