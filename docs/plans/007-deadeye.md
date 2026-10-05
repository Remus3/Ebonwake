# Plan 007 - Deadeye tab

Status: open. Lanes: `build` (slice A), `data` (slice B).

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
