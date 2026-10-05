---
description: Operator status in caveman-ultra form - done, left, +added, -retracted, in-flight with ETA. Reads progress files; never interrupts running work.
---

Read only. Never stop, restart or edit running work to answer.

Sources: `ops/loop/control/progress/*.json`, `python tools/ew_lane.py status`,
`docs/plans/ROADMAP.md`, `git log -5 --oneline`, `gh run list --limit 1`,
`GET http://127.0.0.1:8940/api/health`.

Output, one short line each, nothing else:

```
done: <items>
left: <items>
+added: <items>
-retracted: <items>
running: <task> <pct>% ETA <eta> (<status>)
ci: <green|red|pending> server: <ok|offline>
```
