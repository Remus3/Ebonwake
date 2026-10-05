# Market fixtures (plan 027)

`hot_preorder.json` follows the arsha.io v2 `GetWorldMarketHotList` /
`GetWorldMarketSubList` field shape (name, id, sid, minEnhance, maxEnhance,
basePrice, currentStock, totalTrades, priceMin, priceMax, lastSoldPrice,
lastSoldTime). Values mirror the 2026-10-05 market state named in plan 027:
Essence of Dawn pinned at its 100 M cap with no stock (`capped`), a stock-0
item below its cap (`no_stock`), and a normal listing (`null`). See the plan's
As-built deviations for why it is shape-faithful rather than byte-recorded.
