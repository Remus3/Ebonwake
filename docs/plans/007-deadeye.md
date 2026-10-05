# Plan 007 - Deadeye tab

Status: done (2026-10-05); refute rounds 1/3 PASS. Lanes: `build` (slice A), `data` (slice B).

## Goal

Operator-authored build notes for the Deadeye main (skill add-ons, crystals,
artifacts, lightstones, PvE rotation, misc) as markdown with a rendered
preview, plus an ordered enhancement plan. Text only: nothing here is ever
executed, sent to the game or turned into input (Game ToS floor).

## Slice A - server (lane `build`)

Files: `server/ew/deadeye.py`, `server/ew/app.py` (GET + POST_ROUTES + state
source), `tests/test_deadeye.py`.

1. Store domain `deadeye`: `{"notes": {section: {text, updated}}, "plan":
   [{id, item, current, target, note, done}], "next_id": int}`. Fixed
   `SECTIONS` (ordered `(id, title)`): addons "Skill add-ons", crystals
   "Crystals", artifacts "Artifacts", lightstones "Lightstones", rotation
   "PvE rotation", misc "Misc". Seed: every section empty text, empty plan.
2. `LEVELS`: `+0`..`+15`, then `PRI`, `DUO`, `TRI`, `TET`, `PEN` (21 values,
   ordered). `current`/`target` must be in LEVELS and target strictly above
   current.
3. `GET /api/deadeye` -> `{now, levels, sections: [{id, title, text,
   updated}] (fixed order), plan: [... in stored order, each adds `steps` =
   index distance target - current], progress: {done, total}}`.
4. `POST /api/deadeye`, exactly one op per body: `{"note": {section, text}}`
   (text 0-20000 chars, `\r\n` normalised to `\n`, `updated` = now UTC ISO);
   `{"add_step": {item, current, target, note?}}` -> step (`id` `d<N>`
   never reused); `{"edit_step": {id, item?, current?, target?, note?}}`;
   `{"step_done": {id, done: bool}}`; `{"delete_step": id}`;
   `{"move_step": {id, dir}}` (dir -1 or 1; moving past an end is a no-op).
   Limits: item 1-60 chars, note 0-200 chars, max 100 steps. Unknown
   section/id -> ValueError (400). Same guard/lock style as `grind.py`.
   POST body cap: raise `MAX_POST_BYTES` only if needed for 20000-char notes,
   and only for this route (document the number).
5. `/api/state` `sources.deadeye` (`{done, total}`).

## Slice B - dashboard (lane `data`)

Files: `app/dashboard/deadeye.js`, `dashboard.js`, `index.html`,
`app/shared/ewcore.js` (`renderMarkdown`, `levelIndex`, `validDeadeyeBody` +
allowlist route `/api/deadeye`), `app/shared/ew.css`,
`app/test/deadeye.test.js`.

1. `ewcore.renderMarkdown(text)` -> HTML string, safe subset only: escape
   ALL of `& < > " '` first, then headings `#`-`###`, `-`/`*` bullet lists,
   `1.` ordered lists, `**bold**`, `*em*`, inline `` `code` ``, fenced code
   blocks, paragraphs. No links, no images, no raw HTML ever (tests prove
   `<script>`, `<img onerror>` and `javascript:` come out inert).
2. Cards: Notes (section tabs, textarea editor + rendered preview toggle,
   save, unsaved-changes marker, char counter vs 20000), Enhancement plan
   (rows in order: item, current -> target, steps, note, done toggle,
   up/down, delete with two-click confirm; add form with LEVELS selects).
   Fits 1280x800; lists and preview scroll inside their card.

## Acceptance

- pytest, node --test and leak sweep green; ASCII + LF.
- Contract above covered by tests (every op, every limit, ordering, markdown
  escaping).
- Dashboard self-test 7/7 tabs fit; Deadeye tab renders with an empty store.
- Verifier PASS within 3 refute rounds; one push; CI green.

### Slice A deviations (adjudicated in-lane, 2026-10-04)

1. POST cap. Decision: `MAX_DEADEYE_POST_BYTES = 131072` (128 KiB) via a
   per-route `POST_CAPS` map in `app.py`; every other route stays at 4096.
   Alternatives: raise the global cap; 64 KiB. Why: a 20000-char BMP note
   JSON-escaped at 6 bytes/char is 120000 bytes, so 128 KiB covers the worst
   BMP case and nothing else grows. Reverses if: notes heavy in astral chars
   (12 bytes escaped) hit 413 in practice.
2. Text length is counted after `\r\n` -> `\n`. Alternatives: count raw.
   Why: the stored text is what the counter in slice B shows. Reverses if:
   slice B counts raw textarea input and the two disagree.
3. `item` and `note` are stripped and refuse control characters (same rule as
   grind names); note text keeps every character. Alternatives: no strip.
   Why: single-line fields in a table row. Reverses if: operator needs
   multi-line step notes.
4. `edit_step` with only `id` is a 400; unknown keys in any op are a 400; the
   merged step must keep target strictly above current. `dir` must be an int
   (`1.0`, `true` refused). Why: one shape per op, like grind. Reverses if:
   slice B needs a no-op edit.
5. GET `sections[].updated` is `null` until first saved; `/api/state`
   `sources.deadeye` is exactly `{done, total}`. Store doc also carries a
   top-level `updated` stamp (as grind). Reverses if: state consumers need a
   `status` key.

### Slice B deviations (adjudicated in-lane, 2026-10-04)

1. Paragraph lines are joined with `\n` inside one `<p>` and shown as line
   breaks (`white-space: pre-line`), not folded to spaces. Alternatives:
   CommonMark soft-wrap to a space; emit `<br>`. Why: build notes are
   line-oriented (rotation steps) and `pre-line` adds no tag to the safe
   subset. Reverses if: the operator wants soft-wrapped prose.
2. `*em*` needs a non-word edge on both sides (`2*3*4` stays literal); a
   bullet needs a space after `-`/`*`; headings need a space after `#`;
   `####`+ is a paragraph; links/images stay literal text. Alternatives:
   full CommonMark emphasis rules. Why: small, linear-time regexes; no
   ambiguity with multiplication or combos like `S+LMB*2`. Reverses if: a
   real note renders wrong.
3. Client guard `validDeadeyeBody` is stricter than the plan text in two
   places: step `note` must be single-line (no control chars), note text may
   hold only TAB/LF/CR controls. `edit_step` order (target above current) is
   checked only when both levels are in the body; the server checks the rest.
   Alternatives: mirror the plan text only. Why: the inputs are single-line
   fields / a textarea, so nothing legitimate is refused. Reverses if: slice A
   needs multi-line step notes.
4. POST replies are taken as the new GET body only when they carry
   `sections` and `plan` arrays; any other reply triggers a re-poll.
   Alternatives: always re-poll. Why: the plan does not fix the POST reply
   shape; this works with either. Reverses if: never needed.
5. Unsaved drafts are kept per section across section switches and polls
   (marked `*` on the section tab); Ctrl+S in the editor saves (a
   dashboard-window shortcut, never a game input). Alternatives: discard on
   switch. Why: no lost typing. Reverses if: the operator prefers discard.
