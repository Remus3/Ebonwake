# Research 0016 - official notice list links changed shape (2026-10-08)

Read-only diagnosis, session 17. One polite GET of the public Notice list
(robots.txt disallows only /MyPage/, /CS/QNA/, /InGame/).

## Finding (defect)

- The list page `.../en-US/News/Notice?boardType={b}` (fetched by
  `NoticeClient._download`, `server/ew/eventnotices.py`) now links each notice
  as `/News/Notice/Detail?groupContentNo=N&countryType=en-US`.
- The parser only accepts `DETAIL_PATH = "/en-US/News/Detail"`
  (`eventnotices.py` `_ListParser.handle_starttag` and `group_no_of`), so a
  200 page with 26 Detail anchors parses to 0 notices.
- An empty list is recorded as success (`ok_at` set), and the Detail cache is
  pruned to the empty list. No patch-notes text is cached, so the plan 085/087
  verifier shows 0 verdicts and the plan 018 epoch stays unverified.
- `server/ew/coupons.py` filters with `"/News/Detail" in href`, which does not
  match `/News/Notice/Detail`. Probably the same break; not confirmed by a
  fetch.
- The old Detail URL `/en-US/News/Detail?groupContentNo=N` still returns the
  same page, so `DETAIL_URL` / `detail_url()` can stay.

## Patch notes 2026-10-08 (published, groupContentNo 10678)

Title "[Updates] Patch Notes - October 8, 2026". Section "Combat Level and EXP
System Revamp":
- "Increased the max character level: 70 -> 75"
- "EXP no longer increases after reaching Lv. 75."
- "Changed the Combat EXP required to reach the next level for all level
  ranges." / "Adjusted the EXP obtained from defeating any monsters." /
  "Added a level-based cap on the Combat EXP obtainable from killing a single
  monster." (cap table, e.g. 65+ = 0.01%)
- Lv 70+ bonus Extra AP Against Monsters and Monster DR, +3 a level up to +18
  at 75.

## Fix (loop item, root-cause TDD)

1. Fixture cut from the live list page with the new anchors; the failing test
   is that `parse_list` returns 10678 "Patch Notes - October 8, 2026".
2. Accept both paths (`/en-US/News/Detail`, `/News/Notice/Detail`) in
   `_ListParser` and `group_no_of`; still reject other hosts and paths.
3. `coupons.py` link filter: accept both paths, with a fixture test.
4. Guard: a 200 list page with Detail-like anchors that parses to 0 is a parse
   failure, not `ok_at`, and does not prune the Detail cache.
5. After the merge, the next `/api/events` after the 6 h floor (or a cleared
   `eventnotices_attempt.json` "at") reads 10678. Confirm that
   `/api/data/verdicts` shows `cap75-xp-rescale` confirmed.

## As-built (lane fix, 2026-10-08)

- `coupons.DETAIL_PATHS` / `is_detail_href` shared by both parsers;
  `eventnotices.group_no_of` takes an exact path from DETAIL_PATHS, so other
  hosts and paths stay rejected. Detail pages are still fetched by the old
  `DETAIL_URL`. `coupons.official_url` also accepts the DETAIL_PATHS links
  (they sit outside `/en-US/News/`).
- Guard: a board page with `<a>` links carrying `groupContentNo=` that parses
  to 0 notices raises `UpstreamError` in `NoticeClient._lists`, so the run is
  a failure (`fail_since` set, `ok_at` kept, Detail cache not pruned, last
  good list served stale).

### As-built deviations

1. Fixture reconstructed, not cut from the live page.
   Decision: `tests/fixtures/eventnotices/list_notice_detail.html` is built
   from the anchor shape recorded above. Alternatives: one live GET in the
   lane (no network grant in the headless lane). Why: the shape is recorded
   verbatim here; the fixture pins it. Reverses if: a live read shows another
   anchor shape - cut a fresh fixture then.
2. A third accepted path, `/en-US/News/Notice/Detail`.
   Decision: accepted alongside the two in step 2. Alternatives: exactly the
   two recorded paths. Why: a relative `Notice/Detail?...` link resolved
   against the list URL lands there; same host, same notice board, no wider
   than needed. Reverses if: that path ever serves something that is not a
   notice.
3. Step 5 confirmed in-repo, live check left to the post-merge tick.
   Decision: `tests/test_patchverify.py::test_notice_detail_list_links_confirm_the_cap75_epoch`
   drives a new-shape Updates list through `_download` to
   `/api/data/verdicts` and asserts `cap75-xp-rescale` confirmed from 10678.
   Alternatives: hit the running server from the lane (it runs main's code,
   not this fix). Why: only the merged server can read 10678 live. Reverses
   if: the post-merge `/api/data/verdicts` does not show it confirmed.
4. No zero-parse guard on the coupon news page.
   Why: `coupons.extract` keeps only coupon-mentioning notices, so 0 is a
   normal result there. Reverses if: a coupon miss is traced to a link-shape
   change again.
