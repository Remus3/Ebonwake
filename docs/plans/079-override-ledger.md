# Plan 079 - Override ledger: source, set-at, expiry, superseded by live signals, badges and digest list

Status: open (priority). Zero-touch audit 2026-10-06 (research 0010 H1, M1, M2, M3, L10, L15 and section 3). Lane hint: `build`.

Spec gap: operator order 2026-10-06 - EW self-adjusts from the live game;
a changed setting gets forgotten and silently affects output. Today an
operator-set value carries no source, no set-at time and no expiry, and no
card says it is in effect. `market.vp` is the worst case: read straight into
Grind / Crafting net proceeds (`server/ew/app.py:767`) while Inventory
prefers the "Value Pack" buff timer (`server/ew/inventory.py:246-260`), so
two tabs disagree and a lapsed VP inflates every silver figure forever.
Gear AP / DP already do it right (`progress.py:1169-1206`, `gs_src`).

1. `server/ew/overrides.py` (new, pure + store domain `overrides`):
   - Entry `{key, value, source: typed|config, set_at, expires_at|null,
     reason, retired_at|null, retired_by|null}`; at most one live entry
     per key; retired entries kept (last 50) for the digest history.
   - `POLICY` (tracked `server/ew/data/override_policy.json`): per key the
     expiry rule - `days:N`, `until:maint_end`, `until:path_missing`, or
     `none` (allowlisted: `market.fame_pct`, `profile.family`, folders) -
     and the cards it affects (for badges).
   - `effective(key, live, default, now)` -> `{value, from: live|override|
     default, entry|null}`: a fresh live value retires a live override
     (`retired_by: "<signal>"`); an expired one retires with
     `retired_by: "expired"`.
   - `from_config(doc, defaults, mtime)`: a `config/local.json` value that
     differs from its default becomes a `source: config` entry, set_at =
     file mtime when first seen (idempotent on re-read).
2. Keys moved onto the resolver (one call site each, every reader goes
   through it):
   - `market.vp`: live = armed "Value Pack" timer (plan 005 / 066 buffs);
     override expires set_at + 30 d. Grind, Crafting, sell-or-vendor and
     Inventory all read `effective("market.vp")` - fixes the disagreement.
   - `events.maintenance_start_utc`: live = 064 maintenance notice for the
     date; override expires at the end of the next maintenance it applied
     to (`until:maint_end`).
   - `overlay.mode.*` pin / block: override, 7 d.
   - `bdo.install_dir` / `documents_dir`: `until:path_missing`; live =
     065 detection.
   - Gear AP / AAP / DP: wrap the existing `gs_src` as ledger entries
     (no behaviour change; typed stays superseded by OCR).
   - Market manual watch thresholds: `source: typed` entries next to the
     071 band thresholds (badge only, no expiry).
3. `POST /api/settings` writes for the keys above create / replace the
   ledger entry; `{"clear": "<key>"}` retires it (`retired_by: "operator"`).
4. `GET /api/overrides` -> active entries with labels (plan 078 `LABELS`
   when present, else the key), value, set-at, expires-in, affected cards.
   `server/ew/signals.py` digest gains `overrides: {count, items}`; Home's
   status line shows `N overrides` when N > 0.
5. `app/shared/ewcore.js` `overrideBadge(entry)` -> `{cls: 'ew-ovr',
   text: 'override', title}`; every affected card renders it in its header
   with a `clear` action. Signal health card (073) lists overrides.
6. Tests:
   - `tests/test_overrides.py`: live beats override and retires it;
     expiry at `days:N` and `until:maint_end`; `none` never expires;
     config import idempotent; at most one live entry per key.
   - `tests/test_market_vp.py`: VP timer armed -> Grind, Crafting and
     Inventory all net with VP; typed VP 31 d old -> all three without VP
     and the entry retired `expired`.
   - `tests/test_signals.py`: digest carries the overrides section.
   - `node --test` `app/test/overrides.test.js`: badge text / title, no
     badge when list empty.

Acceptance: with no config every card is correct and shows no badge; a
typed VP shows an `override` badge on Grind, Crafting and Inventory, is
listed in Signal health, and disappears at 30 d or when a VP timer is read;
gates green; verifier PASS within 3 rounds; one push.

ToS check: bookkeeping over values EW already stores and signals it already
reads (buff timers, notices, folder existence). No game input, no memory
read.

Depends on: 005, 027, 030, 064, 065, 067, 073.

## As-built deviations

1. Folders (`bdo.install_dir` / `documents_dir`): rule `until:path_missing`
   with NO live signal. Alternatives: 065 detection as the live value (the
   plan text). Why: a "use other" folder exists precisely to beat detection;
   treating detection as live would retire every such override on the next
   read. The ledger badges and retires them (folder gone); the game watcher
   still reads the configured folder as before (plan 065 path unchanged).
   Reverses if: detection gains a confidence signal that should outrank a
   typed folder.
2. Gear AP / AAP / DP and manual watch thresholds are listed in
   `GET /api/overrides` as derived rows (`clearable: false`) built from
   `gs_src` and the watchlist, not stored in the `overrides` domain.
   Alternatives: copy them into the ledger. Why: their own stores already are
   the record (no behaviour change, as the plan asks); a copy could drift.
   Reverses if: either gains an expiry rule.
3. Config import de-duplicates on the config VALUE (`seen[key]`), not the
   file mtime. Alternatives: re-import on every mtime change. Why: any
   settings write bumps the mtime, which would resurrect an expired or
   superseded value; keyed on value, it stays retired until the file changes
   that key. Reverses if: config gains a per-key timestamp.
4. `{"clear": key}` also writes the key's default back to config/local.json
   (so the Settings tab agrees). Expiry and live supersession do NOT touch
   the file; the resolver output is what every reader uses. Alternatives:
   rewrite config on expiry too. Why: a GET path never writes the operator's
   config. Reverses if: the Settings tab is made to show effective values.
5. `market.fame_pct` and `profile.family` carry rule `none` and are listed
   (badge) whenever set away from their default. Alternatives: track
   without listing. Why: plan text lists them as `none` keys; a set fame
   changes every net figure, so it is shown. Reverses if: the operator finds
   the permanent badge noise.
6. Badge placement: Grind `Session`, Crafting margin, Inventory, Market
   `Watchlist`, Events `Events and drops` card headers, from one shared
   `app/dashboard/overrides.js` (cached `/api/overrides`). The overlay is not
   badged (overlay mode keys list card `overlay`; Signal health and the Home
   pill show them). Reverses if: the overlay gains a header.
7. No plan 078 `LABELS` exist yet; labels come from the tracked policy file
   (`settings.LABELS` wins when present).

Dependency guard: before writing code the lane checks that
`server/ew/signals.py` (plan 073), `server/ew/settings.py` (plan 030) and
`server/ew/maint.py` (plan 059 / 064) exist. If any is missing, the lane
changes nothing, writes `"status": "blocked", "needs": ["030", "064",
"073"]` into its progress JSON (`ops/loop/control/progress/p079-build.json`)
and exits 0.
