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

## As-built deviations

1. Unload guard needs a main-process dialog.
   Decision: `beforeunload` in `deadeye.js` flushes pending drafts and holds
   the unload while a note is unsaved; `main.js` answers
   `will-prevent-unload` with a Leave / Stay `dialog.showMessageBoxSync`.
   Alternatives: renderer-only `beforeunload` (Electron cancels silently: the
   window and tray Quit would just stop working); flush only, no guard.
   Why: the plan's guard without a prompt traps the window. Reverses if:
   Electron starts showing its own prompt for `beforeunload`.
2. Restore prompt is an inline bar (Restore / Discard) in the Build notes
   card, not `window.confirm`. Alternatives: modal confirm on load. Why: a
   blocking modal on tab mount interrupts; the bar is per section and stays
   until acted on. Reverses if: the operator asks for a modal.
3. `ew:open-external` is rate-limited (6 a minute, `core.OPEN_RATE`) like
   `ew:notify`, and opens the normalized href (`core.externalUrl`), not the
   raw string. Also refused: any non-default port (`:443` is normalized away), a URL not starting with the
   literal `https://`, whitespace / non-ASCII, over 2048 chars. Why: same
   defence-in-depth as the other bridge channels. Reverses if: never needed.
4. "Plan data `source` links" = the render sites that already surface a
   data `source`: Progress unlocks, Pets alpha / exchange rules, Inventory
   warehouse rule. One shared `EWToast.linkButton(url)` (toast.js, loaded
   before every tab) builds the button, or nothing for a non-allowlisted url,
   so bdocodex.com loot-table sources (Grind) get no button - the allowlist
   is the plan's, unchanged. Alternatives: per-module button copies; adding
   bdocodex.com. Why: one audited click path; host list is a plan decision.
   Reverses if: a later plan widens `EXTERNAL_HOSTS`.
