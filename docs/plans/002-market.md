# Plan 002 - Market tab

Status: in progress (2026-10-04). Lanes: `build` (slice A), `data` (slice B).

## Goal

Watchlist prices, 90-day sparkline, order-book depth, alert thresholds and the
hot list on the Market tab, from the arsha.io v2 API (`na`), read-only GET,
cached on disk, never hammering the API, honest about staleness.

Observed 2026-10-04: arsha v2 answered HTTP 500 `{"code": 103, "message":
"...blocked by Imperva..."}` for every endpoint. Upstream outages are normal;
the cache + backoff + stale display is the core of this plan, not an extra.

## Upstream (arsha.io v2, region `na`, `lang=en`)

Base `https://api.arsha.io/v2/na/`. Responses are a single object or a list of
objects (one per `sid`); the client normalises both.
- `GetWorldMarketSubList?id=<id>` -> `{name, id, sid, basePrice, currentStock,
  totalTrades, priceMin, priceMax, lastSoldPrice, lastSoldTime, ...}`
- `GetMarketPriceInfo?id=<id>&sid=<sid>` -> `{name, id, sid, history: {"<epoch
  ms>": price, ...}}` (about 90 days)
- `GetBiddingInfoList?id=<id>&sid=<sid>` -> `{name, id, sid, orders: [{price,
  sellers, buyers}, ...]}`
- `GetWorldMarketHotList` -> list of SubList-shaped objects.
- Error body `{status, message, code}`; code 103 = upstream blocked (transient).

## Slice A - server (lane `build`)

Files: `server/ew/market.py` (new), `server/ew/app.py` (routes + POST),
`tests/test_market.py` (new), `tests/test_server.py` (POST guards).

1. `ArshaClient(fetch=None, clock=time.time, cache_dir=RUNTIME/"cache"/"market")`.
   `fetch(url, timeout) -> bytes` is injectable (default urllib GET, UA
   `Ebonwake/<version>`, timeout 10 s); tests never touch the network.
2. Disk cache: one JSON file per (endpoint, id, sid), atomic write, holding
   `{fetched_at, data}`. TTLs: sublist 300 s, orders 120 s, history 3600 s,
   hot 600 s. Fresh cache -> no fetch.
3. Backoff per endpoint+key: after a failure (HTTP error, JSON error, body with
   `code`, timeout) next attempt waits `min(30 * 2**n, 1800)` s; state persisted
   beside the cache so restarts honour it. During backoff or failure, serve the
   last cached data with `stale: true` and `error` text; no cache -> `data: null`.
4. Every result is `{data, fetched_at (iso|null), age_s, ttl_s, stale, error}`.
5. Watchlist in `Store` domain `market`: `{"watch": [{id, sid, below, above}]}`,
   seeded once from `config/local.json` `market_watch` (ints) when empty.
6. Alerts: `alert = "below"` if price <= below, `"above"` if price >= above,
   else null (price = lastSoldPrice, falling back to basePrice).
7. Routes: `GET /api/market/watch` -> `{items: [{id, sid, name, price, stock,
   trades, below, above, alert, freshness}], updated}`;
   `GET /api/market/item?id=&sid=` -> `{sub, history: [[epoch_ms, price], ...]
   sorted, orders: [...], freshness}`; `GET /api/market/hot` -> `{items, freshness}`;
   `POST /api/market/watch` JSON `{"add": {id, sid, below?, above?}}` or
   `{"remove": {id, sid}}` -> new watchlist. POST requires a loopback Host,
   `Content-Type: application/json`, body <= 4 KiB, ints validated; no
   OPTIONS/CORS preflight is answered (browser pages cannot POST).
8. `/api/state` `sources.market = {updated, ttl_s, status: ok|stale|error|none}`.
9. No authenticated or write call to arsha or Pearl Abyss, ever (ToS floor).

Acceptance: `python -m pytest -q` green with tests covering TTL hit/miss,
backoff growth + persistence, stale-on-error, list-vs-object normalisation,
alert edges, watchlist add/remove/validation, POST guards (bad host, bad type,
oversize), routes with a fake client.

## Slice B - dashboard (lane `data`)

Files: `app/dashboard/market.js` (new), `app/dashboard/dashboard.js` (mount),
`app/dashboard/index.html` (script tag), `app/shared/ewcore.js` (pure helpers),
`app/shared/ew.css`, `app/test/market.test.js` (new).

1. Pure helpers in `ewcore.js`: `fmtSilver(n)` (`1.23B`, `45.6M`, `789K`,
   `950`), `sparkPath(points, w, h)` -> SVG path `d` (empty input -> ''),
   `alertFor(price, below, above)` (same rule as slice A),
   `depthBars(orders, n)` -> top-n sell/buy levels with relative widths.
2. Market tab cards (grid, fits the window, lists scroll inside cards):
   Watchlist (rows: name, price, alert badge, freshness pill `arsha 4m ago`,
   stale = muted), Item detail (selected row: sparkline SVG, min/max/last,
   order book depth bars), Add/edit (id, sid, below, above -> POST; remove),
   Hot list.
3. Polls `/api/market/watch` every 60 s and on tab show; never more often.
   Offline/error -> last data muted with the error, never blank.
4. CSP stays `connect-src http://127.0.0.1:8940`; SVG built with DOM APIs
   (no innerHTML from data).

Acceptance: `npm test --prefix app` green (helpers covered); desktop self-test
still 7/7 fit; Market tab renders with the server's fake/empty data.

## Gates

pytest + node --test + leak sweep; verifier on each lane branch, refute rounds
capped at 3; one merge batch, one push.
