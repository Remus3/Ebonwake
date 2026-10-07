# Plan 082 - Class portrait: FaceTexture archive, characterNo -> class binding, top-left class chip with empty state

Status: open. Portrait research 2026-10-06 (research 0011 F1, F2, F4, F5, F6). Lane hint: `build`.

Spec gap: operator request 2026-10-06 - show the most recent in-game
portrait of the class in the dashboard (top-left), as a reminder of which
class is in view as class tabs grow; a class without a portrait shows an
EMPTY slot, never another class's portrait. Zero-touch: nothing to click
or type; the newest portrait is picked automatically.

1. `server/ew/gamewatch.py`: new classifier side-channel (not a state):
   a log line matching
   `UserCache/(\d+)/\d+/(\d{6,20})/gameVariable\.xml` records a character
   load `{char_no, at}` (string parse only; nothing under UserCache is
   opened). `view()` gains `char_loads` (last 20) and `char_no` (most
   recent load in the current logged-in session, else None).
2. `server/ew/portraits.py` (new; pure helpers + store domain
   `portraits`):
   - `scan(documents_dir)`: `os.scandir` of `<docs>/FaceTexture` for
     `^\d{6,20}\.bmp$`; a file whose (mtime, size) changed and is at least
     2 s old is read once (open, read, close; `PermissionError` /
     sharing violation -> retry next poll), sha256'd, and if the hash is
     new archived to `ops/runtime/portraits/<char_no>-<mtime>.png` plus
     thumbs 56 x 72 and 312 x 402 (stdlib BMP 24/32-bit decode + zlib PNG
     encode + box downscale). Archive cap 30 per character (oldest
     dropped). Never writes, renames, deletes or touches anything in
     FaceTexture.
   - Character registry `characters: {char_no: {cls, cls_src: single|ocr|
     typed, first_seen, last_seen}}`. Binding rule A1 (research 0011 s2):
     exactly one char_no ever seen in the log AND the Progress character
     class is set -> bind, `cls_src: single`. A2 hook: `bind_ocr(char_no,
     cls, conf)` for plan 066 `ocrinfer` kind `class` (fixed class list in
     `server/ew/data/classes.json`; at or over the 063 gate) replaces a
     `single` binding. A second char_no never inherits a binding.
   - `current(cls)` -> newest archived portrait whose char_no is bound to
     `cls`, else None. Unknown-class entries are indexed, never returned.
3. Routes (127.0.0.1 only, `ports.SERVER`):
   - `GET /api/portraits` -> `{classes: {<cls>: {current: {id, at, char_no,
     from: "auto"} | null}}, unknown: N}`.
   - `GET /api/portraits/img/<id>?size=s|m|full` -> PNG from
     `ops/runtime/portraits/` (id validated against the index; no path
     input; `Cache-Control: no-store`).
   - Domain bus event `portraits` (plan 049) on a new archive / binding.
4. Dashboard:
   - `app/dashboard/index.html` CSP adds `img-src 'self'
     http://127.0.0.1:8940` (dashboard only; overlay unchanged).
   - `app/shared/ewcore.js` `portraitChip(view, activeCls)` -> `{cls,
     src|null, alt, title, empty}`: class = active class tab, else the
     class of the loaded char_no, else the Progress character class.
   - `app/dashboard/dashboard.js`: the `ew-brand` text is replaced by a
     chip button: 28 x 36 `<img>` (`object-fit: cover`, 1 px
     `--fk-border`, radius 4 px) + class name; click selects the class
     tab (optional). Empty: same frame, dashed, no image, `role="img"`
     `aria-label="Deadeye: no portrait yet"`, title "Take your character
     portrait in game; EW picks it up automatically." Below 600 px wide:
     thumbnail only. No toast, no onboarding step for a missing portrait.
   - `app/shared/ew.css`: `.ew-chip`, `.ew-chip-img`, `.ew-chip-empty`,
     tokens only (light + dark).
5. Docs: one line in section 4 of `docs/research/0001-bdo-data-and-tos.md`
   naming `Documents\Black Desert\FaceTexture` as an allowed read-only
   input with the adjudicator conditions (research 0011 s3).
6. Tests:
   - `tests/test_portraits.py`: BMP fixture (tiny 32-bit and 24-bit,
     bottom-up) -> PNG round trip and thumb size; unchanged file not
     re-read; same hash not re-archived; A1 binds one char_no, a second
     char_no stays unknown and `current()` never returns it; OCR binding
     replaces `single`; no portrait -> `current()` None (empty slot).
   - `tests/test_portraits_floor.py`: AST scan of `portraits.py` - no
     `open(..., 'w'|'a'|'x'|'+')`, no `os.remove|rename|replace|utime|chmod`
     on a FaceTexture path; no reference to `UserCache` /
     `Customization` / install dir outside the log-line regex.
   - `tests/test_gamewatch.py`: character-load line fixture -> `char_no`.
   - `tests/test_app_routes.py`: `/api/portraits/img/<id>` rejects ids not
     in the index and path-like ids.
   - `node --test` `app/test/portrait.test.js`: chip for bound class, empty
     chip for a class with no portrait (no src, aria-label), never another
     class's image when the active class has none.

Acceptance: on this host (one Deadeye, one FaceTexture portrait) the top
bar shows the Deadeye chip with the 2026-10-06 portrait with no click or
entry; re-taking the portrait in game updates the chip within one poll and
keeps the previous one archived; a store with no bound portrait shows the
empty frame; gates green; verifier PASS within 3 rounds; one push.

ToS check: adjudicator ruling 2026-10-06 (research 0011 s3) - FaceTexture is
an operator-created Documents image like ScreenShot; short-lived read-only
handle, never written; the characterNo comes from the existing session-log
tail string; nothing under UserCache, Customization or the install dir is
opened; no game input.

Depends on: 008, 062, 065, 066.

Dependency guard: before writing code the lane checks that
`server/ew/detect.py` (plan 065), `server/ew/playsession.py` (plan 062) and
`server/ew/ocrinfer.py` (plan 066) exist. If any is missing, the lane
changes nothing, writes `"status": "blocked", "needs": ["062", "065",
"066"]` into its progress JSON (`ops/loop/control/progress/p082-build.json`)
and exits 0.

## As-built deviations

Guard: 062 / 065 / 066 present; built. Each item below was self-adjudicated
by the lane (decision / alternatives / why / reverses if).

1. Log regex accepts `/` or `\` separators.
   Alternatives: the plan's `/`-only pattern. Why: research 0011 saw `/`, but
   a Windows client may write either; a strict superset costs nothing and the
   match is still a string parse only. Reverses if: a false match is seen.
2. `char_no` is reported while the current log session is live (state not
   `not_running` / `unconfigured`), cleared on a new log file, truncation or
   game exit - not only while `logged_in`. Alternatives: logged_in only. Why:
   the load line can arrive before or without a classified login line; a
   character stays loaded through a brief disconnect. Reverses if: the chip
   is seen naming a character from a closed session.
3. A2 is the `bind_ocr(char_no, cls, conf)` hook only (gate 0.9 = plan 063
   AUTO_COMMIT_MIN, class must be in `data/classes.json`, a `typed` binding is
   never replaced). No plan 066 `class` extractor is wired: ocrinfer has no
   class-name region yet. Alternatives: add an OCR region + extractor now.
   Why: no verified screen region; an unverified read would bind wrongly and
   the empty-slot rule depends on bindings being right. Reverses if: a plan
   adds the class region (it then calls `bind_ocr`).
4. Binding A1 keeps an existing `single` binding when a second characterNo
   appears later (the second stays unknown); it never moves or spreads.
   Alternatives: unbind both. Why: the first was the only character when it
   was bound, so the binding was right then; unbinding would blank the chip
   on a host that only ever plays one Deadeye. Reverses if: the operator adds
   a character and the first one is shown under the wrong class.
5. Archive cap drops the oldest version with a direct unlink of EW's OWN
   derived PNGs in `ops/runtime/portraits/` (never FaceTexture). Alternatives:
   Recycle Bin. Why: the plan fixes the cap; the files are runtime copies EW
   created, and a shell Recycle-Bin call would add a non-stdlib path to a
   2 s poller. Reverses if: plan 083 (gallery) wants every version kept.
6. `GET /api/portraits` also carries `char_no`, `loaded_cls` and
   `progress_cls`, and a class whose Progress class has no portrait appears
   with `current: null` (the empty chip needs both). `portraitChip(view,
   activeCls, opts)` takes an optional `opts.zone` for the hover time
   (plan 078 local time). Additive; reverses if: never.
7. The plan 049 `DOMAINS` literal is left as is and `portraits` is pushed
   after it (two older static tests pin the literal). Reverses if: those
   tests are relaxed.
8. Progress class is read per poll from the store (`progress._load()`), not
   `progress.view()`, to keep the 2 s poller cheap. Reverses if: never.
9. Plan 084 (2026-10-07): the top-bar chip is superseded at >= 600 CSS px
   by the left-rail character card; below 600 px the chip shows unchanged.
