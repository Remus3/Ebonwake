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
