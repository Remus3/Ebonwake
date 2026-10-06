# Profile fixtures (plan 041)

`adventurer_search.json`: one BDO-REST-API `/adventurer/search` hit in the
shape of the openapi read 2026-10-05 (field names `contributionPoints`,
`energy`, `combatFame`, `lifeFame`, `gs`, `specLevels`, `privacy`). Synthetic
values and a fake family name; no real account. The alt character has its
level hidden (privacy) so the history test covers an absent field.

Plan 042 (Life & CP card): `lifeskill_private.json` is the same hit with
everything but combat fame hidden (energy, contributionPoints, specLevels
absent); `lifeskill_partial.json` hides energy only and lists life skills out
of the in-game order. Same synthetic family, no real account.
