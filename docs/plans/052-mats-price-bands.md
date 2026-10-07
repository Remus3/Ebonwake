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

## As-built deviations

Self-adjudicated by the build lane (2026-10-06); each line: decision /
alternatives / why / reverses if.

1. Own samples are kept at most one per item an hour (`SAMPLE_GAP_S`), not
   one per 300 s refresh. Alt: every refresh. Why: 90 days x 288 refreshes x
   N items rewrites a multi-MB JSON every 5 min; hourly keeps ~2160 points an
   item and nearest-rank p20/p50/p80 barely move. Reverses if: bands need
   sub-hour resolution.
2. Samples live in `ops/runtime/cache/market_samples.json` (next to the
   plan 002 cache dir, so a test client's tmp cache keeps it offline). Alt:
   the Store. Why: runtime data, not operator state. Reverses if: a backup
   plan needs it in the Store.
3. Watch rows compute bands from the history cache via `peek` (stale or not,
   never a fetch), so history only refreshes when the operator opens the
   item detail; acceptance "no additional arsha calls" holds. Alt: fetch
   history at each watch refresh. Why: plan acceptance. Reverses if: the
   plan 002 cadence adds a history refresh.
4. The p20 opt-in is a watch-entry bool `p20`, stored only when true (old
   rows keep their exact shape); rows return `p20` and `bands` (with
   `basis` and `age_s`). `/api/market/item` also carries `bands`. Explicit
   below/above thresholds win over `below_p20`; `below_p20` is strict
   (price < p20). Alt: separate `alerts` list per row. Why: one `alert`
   field already feeds plan 026 `marketAlert` and the overlay ticker, which
   now treat `below_p20` as a hit. Reverses if: a row needs two alerts at once.
5. The current refresh's sample is recorded before the band is computed, so
   the band includes the current price. Alt: band from prior samples only.
   Why: simpler, and with n >= 5 the strict `<` still fires on a real dip.
   Reverses if: the alert is measured to miss dips on short series.
