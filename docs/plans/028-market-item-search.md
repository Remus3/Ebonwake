# Plan 028 - Market item-name search (local name index) + exact silver in detail

Status: open. Deep dive + UX audit 2026-10-05 session 4 (research 0003-0005). Lane hint: `build`.

Spec gap: research 0005 H5 (market add needs a raw numeric id + sid) and M5
(silver always shortened to 3 significant digits), candidate F5 part 1.
arsha `/v2/na/GetWorldMarketSearchList` and `/category` answered 500 code
103 (Imperva) on 2026-10-05 (research 0004 section 5), so the index cannot
depend on them.

1. Name index `server/ew/itemnames.py`, built from three read-only
   sources: (a) tracked seed `server/ew/data/market_items.json` (`{id, sid,
   name, source, verified}`; seed the enhancement and grind staples named
   in research 0004 with their verified ids - Cron Stone 16080, Essence of
   Dawn 820979, Caphras Stone 721003 - and others only where the lane can
   confirm the id via arsha `/util/db`); (b) every name seen in cached
   `/item` and `/hot` responses (plan 002 cache); (c) arsha `/util/db?id=`
   (GET, max 100 ids per call, cached 7 days) to name ids the operator
   types. Index persisted in `ops/runtime/` (atomic write).
2. `GET /api/market/search?q=<text>` -> top 20 `{id, sid, name}` by
   case-insensitive prefix then substring; `q` length 2-40, ASCII
   printable; 400 otherwise. If arsha `/search` starts answering 200 again
   it may be added as a fourth source behind the same backoff (optional).
3. Dashboard `app/dashboard/market.js`: typeahead in the add form (debounced
   250 ms); raw id/sid entry stays as "advanced". Item detail shows exact
   silver with thousands separators (`fmtSilverExact` in `ewcore.js`);
   list cells keep the short form with the exact value in `title`.
4. Tests: `tests/test_itemnames.py` (merge precedence seed > util/db >
   cache, ranking, validation, offline arsha -> seed only);
   `app/test/market.test.js` (exact formatter, typeahead pure helpers).

Acceptance: search works with the network off (seed + cache); a mocked
`/util/db` call names an unknown id; gates green; verifier PASS within 3
rounds.

ToS check: arsha.io v2 / util GET only through the plan 002 client
(cache, UA, backoff); never an authenticated market action.

Depends on: none.

## As-built deviations

Self-adjudicated by the build lane (2026-10-05).

1. Seed holds only the three research-verified ids (Cron Stone 16080,
   Essence of Dawn 820979, Caphras Stone 721003). Alternatives: add
   staples from memory; confirm more via `/util/db`. Why: the lane had no
   network grant, and the plan allows extra ids only when confirmed.
   Reverses if: a later run confirms more ids via `/util/db` and adds them.
2. `/util/db` is called only for an all-digit query (an id the operator
   types) whose id the seed does not already name; results (including
   "unknown id" misses) live in the persisted index file
   `ops/runtime/market_names.json` with a per-id 7-day TTL, and the
   endpoint has ONE shared backoff key (`utildb`) rather than per-chunk
   httpcache keys. Alternatives: per-chunk `cached_get` files; a lookup for
   every unnamed watch id. Why: one endpoint-wide backoff is politer under
   Imperva and a single runtime file is the index the plan asks for; a
   failed refresh keeps the stale name. Reverses if: watch rows need names
   arsha `/item` does not return.
3. `/util/db` has no sid, so its names index at sid 0; cached `/item` and
   `/hot` rows keep their own sid. Cache rescan is throttled to once per
   30 s. Reverses if: names must appear the instant a cache file lands.
4. Optional arsha `/search` fourth source not added (still 500 code 103).
   Reverses if: it answers 200 again.
5. Item detail's order-book depth rows also use the exact formatter (they
   are detail, not list cells); the hot list "stock" count keeps the short
   form. Reverses if: depth rows get too wide for the card.
