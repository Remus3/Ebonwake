# Plan 057 - Deadeye notes autosave + safe open-in-browser for source links

Status: open. Deep dive + UX audit 2026-10-05 session 4 (research 0003-0005). Lane hint: `build`.

Spec gap: research 0005 M11 (unsaved Deadeye note edits can be dropped by a
window close, a re-render after reconnect or a tray Restart; no autosave,
no `beforeunload` guard) and L10 (Sources card shows raw URLs with only a
`copy` button; links cannot open because navigation is blocked by design).

1. `app/dashboard/deadeye.js`: debounced (2 s) draft autosave per section
   to `localStorage` (try/catch, falls back silently); on load, a restore
   prompt when a draft differs from the saved text; Ctrl+S saves (keep the
   existing binding); `beforeunload` guard while unsaved.
2. Safe external links: new allowlisted IPC `ew:open-external {url}` in
   `app/preload.js` / `app/main.js` -> `shell.openExternal` only for
   `https:` URLs whose host is on an allowlist in `app/shared/ewcore.js`
   (`naeu.playblackdesert.com`, `www.naeu.playblackdesert.com`,
   `www.blackdesertfoundry.com`, `api.arsha.io`, `github.com`); in-app
   navigation stays blocked.
3. Events Sources card and plan data `source` links get an "open" button.
4. Tests: `app/test/deadeye.test.js` (draft diff/restore helpers),
   `app/test/shell.test.js` (allowlist accepts/rejects: http, other hosts,
   credentials in URL, `javascript:`).

Acceptance: tests green; gates green; verifier PASS within 3 rounds.

ToS check: opens the operator's browser on public pages; nothing reaches
the game.

Depends on: none.
