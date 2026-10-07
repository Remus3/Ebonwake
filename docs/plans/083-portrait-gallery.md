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
