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

Dependency guard: before writing code the lane checks that `server/ew/shopping.py` (plan 037), `server/ew/grind.py` (plan 039), `server/ew/market.py` with price bands (plan 052) and `server/ew/crafting.py` (plan 054) exist. If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["037", "039", "052", "054"]` into its progress JSON (`ops/loop/control/progress/p071-build.json`) and exits 0.
