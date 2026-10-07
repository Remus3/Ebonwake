# Plan 071 - Self-curating market watch: seeded from plans, thresholds from price bands

Status: open. Autonomy deep dive 2026-10-06 (research 0007 sections 1, 2.2). Lane hint: `data`.

Spec gap: the watchlist (002 / 029) and alert thresholds are typed by
hand, although EW already knows which items matter (enhancement shopping
list 037, loot tables 039, crafting recipes 054) and their price bands
(052). Per-item arsha endpoints are still Imperva-blocked for tradable
items (research 0007 s2.2); the search list endpoint works.

1. Auto-watch set: union of item ids from the plan 037 shopping list, the
   top 5 loot items by value of the current / last plan 039 spot, and plan
   054 recipe inputs; capped at 15, ranked by silver at stake. Auto entries
   carry `auto: true`; manual entries always stay; a removed auto entry is
   remembered and not re-added.
2. Thresholds: an auto entry without an operator threshold gets buy-alert
   = plan 052 p20 and sell-alert = p80 (shown as "auto band"); a manual
   threshold always wins.
3. Fetch: batch prices for the watch set with one `GetWorldMarketSearchList`
   call per refresh (ids list), falling back to plan 002's cache and
   backoff when blocked; never more requests than plan 002 allows today.
4. Tests: `tests/test_autowatch.py` - union + cap + rank, removed entry not
   re-added, band thresholds, manual wins, one batched GET per refresh
   (stub opener).

Acceptance: fixture plans 037 / 039 / 054 produce a 15-item watch with band
thresholds and one batched GET; gates green; verifier PASS within 3 rounds;
one push.

ToS check: arsha.io v2 read-only GETs only; no authenticated market action,
no game input, no memory read, no client file.

Depends on: 037, 039, 052, 054.

## As-built (lane build, 2026-10-06)

- `server/ew/autowatch.py`: stakes per source, `rank`, `AutoWatch.sync`
  (run by `MarketService.watch` before each refresh via `market.curate`).
- `server/ew/market.py`: `ArshaClient.search(ids)` (one
  `GetWorldMarketSearchList?ids=a,b,...&lang=en` GET, cache + backoff key
  `search_watch`), `Watchlist.curate` / `removed` (store key
  `market.auto_removed`), `band_thresholds`, batch-first `watch()`;
  `cached_price` / `cached_quote` fall back to the batch cache (sid 0).
- `server/ew/grind.py`: `GrindService.loot_candidates()` (never prices).
- `server/ew/itemnames.py`: the name index also scans the batch cache.
- Dashboard: `C.autoWatchBadge` badge ("auto" / "auto band") on watch rows.

## As-built deviations

1. Silver at stake per source. Decision: shopping line total (qty x unit);
   loot = cached price x count in that spot's newest loot session (1 when
   none); recipe input = unit x qty x 1,000 crafts (the crafting tab's
   per-1,000 unit); an id on several sources sums; unpriced = 0.
   Alternatives: unit price only; per-craft recipe cost. Why: the plan says
   "silver at stake" without a unit; per-craft recipe cost would never rank
   against a shopping total. Reverses if: the operator finds recipe inputs
   crowding out enhancement mats.
2. Auto band needs n >= 5 points (`market.MIN_BAND_N`). Alternatives: any
   band. Why: plan 052 samples start at n=1 where p20 = p80 = price, which
   would fire both alerts on the first refresh. Reverses if: bands come from
   full arsha history again (n always large) and the guard proves noisy.
3. Batch blocked -> per-item plan 002 sublist path (its own cache + backoff),
   then a stale batch row. Alternatives: cache peek only, no per-item GET.
   Why: on 2026-10-05 search was blocked while sublist worked; peek-only
   would lose live prices. Cost: one extra GET per batch backoff window
   (30 s growing to 1,800 s), so the request count stays plan 002's.
   Reverses if: arsha publishes a rate limit this exceeds.
4. Enhanced entries (sid > 0) stay on the per-item sublist path: the search
   endpoint answers base items only. Reverses if: search gains a sid param.
5. Editing an auto entry (any `add` for its id) makes it manual. Why: the
   operator touched it, and "manual threshold always wins". A changed id set
   refetches the batch at once (behind the shared backoff). Curation runs at
   most once a minute and is skipped when any source raises, so a hiccup
   never wipes the auto set. An edited auto row keeps `auto_origin: true`
   in the store, so removing it later is remembered too (verifier round 1).
6. Price source per base row (verifier round 1): a fresh sublist cache
   wins (it carries lastSoldPrice / priceMax), else the fresh batch row
   (basePrice only - search rows have no lastSoldPrice, so `capped`
   pre-order cannot show from them), else the plan 002 path.
   `cached_price` / `cached_quote` take the newer of the two caches.
   Alternatives: batch for auto rows only. Why: one GET for the whole set
   is the plan; manual rows priced per item would keep N GETs. Reverses if:
   the operator wants last-sold prices back on manual rows while sublist is
   Imperva-blocked (it is the only source of them).
7. Verifier round 1 finding 4 (N + 1 requests in the refresh where the
   batch fails) is kept as deviation 3: holding per-item GETs that refresh
   would blank prices of freshly added rows and change plan 002 behaviour.

Dependency guard: before writing code the lane checks that `server/ew/shopping.py` (plan 037), `server/ew/grind.py` (plan 039), `server/ew/market.py` with price bands (plan 052) and `server/ew/crafting.py` (plan 054) exist. If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["037", "039", "052", "054"]` into its progress JSON (`ops/loop/control/progress/p071-build.json`) and exits 0.
