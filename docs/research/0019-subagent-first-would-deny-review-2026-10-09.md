# 0019 - subagent-first hook: would-deny review (2026-10-09)

Source: hand-off item SF. Input: `ops/loop/control/subagent_first.jsonl`
(gitignored; mode file `ops/loop/control/subagent_first.mode` = `log` since
2026-10-08). The hook rows carry only time / tool / thread, so each
would-deny row was matched to the main-thread tool call at the same second
in the session-19 transcript (read-only).

## Counts (read 2026-10-09 ~20:00 local)

| day | allow | would-deny |
| --- | --- | --- |
| 2026-10-08 (session 19) | 163 | 19 |
| 2026-10-09 (sessions 20-21) | 378 | 0 |
| total | 541 | 19 |

## The 19 would-deny rows (all session 19, all main thread)

| local time | tool | what it was | verdict |
| --- | --- | --- | --- |
| 18:30:49 | Bash | no main-thread tool call at that second; follows a sub-agent allow row 1 s earlier - the v10 hook going live (synthetic / first call) | dispatchable |
| 18:48:56, 18:48:59 | Bash | read loop item / lane progress / git status | dispatchable |
| 18:49:05 | Bash | foreground poll loop waiting on loop item H0a5846 (~45 m) | dispatchable |
| 19:14:43 | Bash | read a background watcher's output file + item states | dispatchable |
| 19:14:48 | Bash | foreground poll loop on H0a5846 (~9 m) | dispatchable |
| 19:23:22, 19:23:25, 19:23:31 | Bash | read watcher output, loop.json, lane progress, loop task status | dispatchable |
| 19:23:36 | Bash | foreground poll loop on H0a5846 / Nf0e1ea (~9 m) | dispatchable |
| 19:32:42, 19:32:46 | Bash | read item states, usage log tail, inbox_status | dispatchable |
| 19:32:52 | PowerShell | list python / claude processes | dispatchable |
| 19:32:56 | Bash | foreground poll loop on verify (~9 m) | dispatchable |
| 19:35:31, 19:36:18 | Bash | read item states | dispatchable |
| 19:36:22 | Bash | foreground poll loop on v11 verify (~9 m) | dispatchable |
| 19:45:04, 20:20:02 | Bash | read v11 order item state / loop state | dispatchable |

Verdict: 19 would-deny, 0 genuinely undispatchable. Every row is a
read-only status read or a foreground wait loop; both belong in a
background agent (or a background command watched via its progress file),
which is what FLEET item 12 / standing order 2 already require. Sessions 20
and 21 logged 0 would-deny rows.

## Consequence for the deny switch

The hand-off rule stands unchanged: on 2026-10-11, if 3 interactive
sessions show no undispatchable would-deny row, write `deny` into
`ops/loop/control/subagent_first.mode` and say so in the next batched note
to MAIN. Sessions 19, 20, 21 count toward it (0 undispatchable each). The
mode file was NOT changed by this review.
