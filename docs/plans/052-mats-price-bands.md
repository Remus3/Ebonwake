# Plan 052 - Enhancement-mats price bands (p20/p50/p80) with cached-history fallback and below-p20 alert

Status: open. Deep dive + UX audit 2026-10-05 session 4 (research 0003-0005). Lane hint: `build`.

Spec gap: research 0004 F13 (value 3, S). The Market tab draws a sparkline
but no statistics; buying Black Stone, Memory Fragment, Caphras or Essence
of Dawn below their usual band is the main saving lever. arsha `/history`
is often Imperva-blocked (500 code 103), so the feature must degrade to the
last cached series.

1. `server/ew/market.py`: `price_bands(series, days=90)` -> `{p20, p50, p80,
   n, from, to}` (nearest-rank percentiles, stdlib only); computed from the
   plan 002 history cache, falling back to EW's own `/item` price samples
   (one per refresh, kept 90 days in `ops/runtime/`) when history is
   blocked; payload says which (`basis: history|samples`) and its age.
2. Alert kind `below_p20` per watched item (opt-in), evaluated at refresh;
   feeds plan 026 notifications when present.
3. `GET /api/market/watch` rows gain `bands`; item detail shows the band as
   a strip with the current price marked.
4. Tests: `tests/test_market.py` additions (percentiles, short series,
   fallback path with a blocked-history fixture, alert edge).

Acceptance: tests green; no additional arsha calls beyond plan 002's
cadence; gates green; verifier PASS within 3 rounds.

ToS check: arsha.io v2 read-only GET via the plan 002 client.

Depends on: none.
