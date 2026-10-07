# Plan 083 - Deadeye portrait gallery: portrait history + class-attributed screenshots, optional pick as a plan 079 override

Status: open. Portrait research 2026-10-06 (research 0011 F3, F6, section 4 gallery). Lane hint: `build`.

Spec gap: operator request 2026-10-06 - UI to switch the dashboard portrait
to other Deadeye portraits, and collect in-game screenshots per class.
Zero-touch: switching is optional; the default stays the newest portrait;
a manual pick is a plan 079 override (visible, superseded by a newer
portrait).

1. Screenshot index (`server/ew/portraits.py`, store domain `portraits`):
   every ScreenShot file listed by plan 008 is recorded once `{id, file,
   at, char_no|null}`; `char_no` = the most recent character load (plan 082)
   before `at` within the same logged-in window (plan 062); no load line or
   outside a window -> null (unknown). Index cap 200 (oldest dropped);
   files are referenced, never copied (JPEG; stdlib cannot thumbnail it).
2. `GET /api/portraits?cls=<cls>` gains `history` (archived FaceTexture
   versions of characters bound to `cls`, newest first) and `shots` (newest
   24 screenshots whose char_no is bound to `cls`); `unknown` lists
   unattributed items. `GET /api/portraits/img/<id>` serves a screenshot
   original by index id (no path input).
3. Pick rule (plan 079 ledger): key `portrait.<cls>`, value = image id,
   `source: typed`. Live value = the newest FaceTexture portrait for the
   class; a portrait newer than the entry's `set_at` retires it
   (`retired_by: "portrait"`). POLICY `none` (allowlisted: cosmetic, changes
   no number) - no time expiry. `{"clear": "portrait.<cls>"}` = "Use
   newest". A pinned id that left the index retires the entry
   (`retired_by: "missing"`).
4. Deadeye tab, first card "Portraits" (`app/dashboard/deadeye.js`):
   current image 156 x 201 with a source line (`auto - newest portrait,
   <local time>` or the 079 `override` badge `pinned <date>` + "Use
   newest"); a strip of 48 x 62 thumbnails (history, then screenshots,
   `loading="lazy"`, `object-fit: cover`); roving tabindex, arrow keys move,
   Enter picks, `aria-pressed` on the current one; `alt` "Deadeye portrait,
   <local date time>" / "Deadeye screenshot, <local date time>". Empty:
   one muted line "No Deadeye portrait yet" - no placeholder art.
   "Unknown character" group, collapsed, with an optional class select per
   item (research 0011 A4; a 079 `typed` binding) - never required.
5. Plan 082 chip shows the pinned image and its accent dot + title while
   the override is live.
6. Tests:
   - `tests/test_portraits.py`: screenshot attribution (load before shot in
     the same window -> char_no; shot before any load -> null; shot after
     logout -> null); `shots` for a class never includes another class or
     unknown; pick retires on a newer FaceTexture portrait; clear returns
     to auto; pinned id missing retires.
   - `tests/test_overrides.py`: `portrait.*` allowlisted `none`.
   - `node --test` `app/test/portrait.test.js`: gallery model (order,
     aria-pressed, alt text), override badge text, empty line.

Acceptance: Deadeye tab shows the current portrait, its history and the
Deadeye screenshots without any input; picking an older one shows the
`override` badge on the card and the dot on the chip, and a new in-game
portrait retires the pick; an unknown-character image appears in no class
slot; gates green; verifier PASS within 3 rounds; one push.

ToS check: same inputs as plan 082 plus the plan 008 ScreenShot listing
and the session-log tail; files read in place, nothing written outside
`ops/runtime/`; no game input.

Depends on: 008, 062, 063, 079, 082.

Dependency guard: before writing code the lane checks that
`server/ew/portraits.py` (plan 082) and `server/ew/overrides.py` (plan 079)
exist. If either is missing, the lane changes nothing, writes `"status":
"blocked", "needs": ["079", "082"]` into its progress JSON
(`ops/loop/control/progress/p083-build.json`) and exits 0.

## As-built deviations

Guard: `portraits.py` (082) and `overrides.py` (079) present; built. Each item
was self-adjudicated by the lane (decision / alternatives / why / reverses if).

1. `GET /api/portraits` keeps the plan 082 `unknown` COUNT; with `?cls=` the
   unattributed list is `unknown_items` (plus `cls`, `history`, `shots`).
   Alternatives: turn `unknown` into a list. Why: the 082 chip and its tests
   read `unknown` as a number. Reverses if: the 082 count gets no reader.
2. Pick / Use newest / class bind go through a new `POST /api/portraits`
   (`{pick: {cls, id}}`, `{clear: "portrait.<cls>"}`, `{bind: {id, cls}}`);
   `POST /api/settings {clear: "portrait.<cls>"}` is accepted too, so the
   plan 079 Signal health `clear` works on a pick. Alternatives: settings
   only. Why: `portrait.*` is not a config/local.json setting. Reverses if:
   never.
3. The optional unknown-character class (research 0011 A4) is stored in the
   `portraits` domain, not as a ledger row: a portrait or an attributed shot
   binds its characterNo `cls_src: typed` (ranked above single / ocr); a shot
   with no characterNo carries `cls` itself. Alternatives: a ledger entry
   per item. Why: it has no expiry and no live signal, and a second copy could
   drift (same call as plan 079 deviation 2). Reverses if: a binding gains an
   expiry rule.
4. Logged-in windows come from plan 062 play sessions (new
   `PlaySession.windows()`: closed history + the open session ending at
   `left_at`). With auto play sessions off there is no window and every shot
   stays unknown. Alternatives: a separate login-window ledger. Why: 062 is
   the plan's named window. Reverses if: a plan records windows outside 062.
5. A naive client-log `Date` (the character-load time) is read as local wall
   time; tz-aware values are used as is. Alternatives: UTC. Why: the client
   stamps its own wall clock with no zone. Reverses if: a sample log shows
   UTC stamps.
6. Only screenshots the plan 008 watcher lists (taken since EW started,
   newest 50) are indexed; older files are not back-filled, and once the
   index is full a shot older than its oldest row is skipped (no churn).
   Reverses if: the operator asks for a back-fill.
7. A screenshot is served as its original file for every `size` (the chip
   may load a full JPEG). Why: stdlib cannot decode JPEG (plan text).
8. The Portraits card renders its own `override` badge + "pinned <date>" +
   "Use newest" in the source line (policy row `portrait.*`, rule `none`,
   card id `portraits`), not through `EWOverrides.mount`, so the badge is not
   shown twice; Signal health and the Home pill still list the pick.
9. Strip items are buttons: click, Enter or Space picks; picking the newest
   portrait itself also pins (retired by the next newer portrait).
10. Tests: the plan 083 server cases live in a new
    `tests/test_portraits_gallery.py` (fixtures shared with
    `tests/test_portraits.py`) plus one route test in
    `tests/test_app_routes.py`; one 082 assertion gained the additive `kind`
    field. Reverses if: never.
11. Refute round 1: the gate failure was `test_dice_never_auto_ticks_today`
    (plan 056), not plan 083 code. Its login at wall clock minus 3700 s
    straddles the 05:00 UTC dice reset when run 05:00-06:02 UTC, so only 2
    of 3 dice were earned. The test now pins `today_clock` to
    2026-10-06 12:00 UTC. Alternatives: skip near reset; shorten the login.
    Why: a fixed clock is deterministic at any hour and keeps the assertion.
    Reverses if: never.
12. Merge onto main after plan 080 (resolve lane, 6 files). (a) `NONE_OK`
    holds both main's `ui.scale` and `portrait.*`; `override_policy.json`
    keeps every plan 080 incident row plus `portrait.*`. (b) Settings
    `clear` (server `_post_settings` and bridge `validSettingsBody`) accepts
    the union: a `portrait.<cls>` pick is routed to "Use newest" first, then
    main's SPEC | FIXED check and incident-key path apply unchanged.
    (c) `test_dice_never_auto_ticks_today`: main had already pinned the
    clock (2026-10-07 12:00 UTC) for the same flake; main's version is kept
    and this lane's duplicate fix (item 11) dropped. (d) `test_overrides.py`
    keeps both main's plan 080 tests and the plan 083 allowlist test.
    Alternatives: either side alone (drops a feature). Why: both features are
    independent key spaces (`portrait.` vs settings / incident keys).
    Reverses if: a settings key ever starts with `portrait.`.
