# Plan 107 - Split the two monoliths by domain, no behaviour change

Source: research 0018 F-APP-1. Depends on: none

## Goal

`app/shared/ewcore.js` (7562 lines) and `tools/ew_loop.py` (2563 lines) are
split into per-domain modules. No behaviour change: every existing test passes
unchanged, every public name stays importable from its old place.

## Spec

1. `app/shared/ewcore.js` becomes an aggregator. The code moves verbatim, in
   its original order, into per-feature parts `app/shared/core/<part>.part.js`
   (base, market, entry, bus, today, progress, grind, events, deadeye, ocr,
   leveling, bosses, home, settings, inventory, shell, notify).
   - Each part is a plain script (UMD): under node `require()` returns its
     `install(K)`; in the dashboard / overlay page it registers
     `window.EWCoreParts.<part>`. No bundler, no eval (CSP `script-src 'self'`).
   - `install(K)` destructures the names it uses from earlier parts out of the
     shared namespace `K`, runs the moved code, publishes every top-level name
     into `K`, and returns `link()`, which binds the few forward references
     (names a part uses from a later part, only ever called after load) as
     `let` bindings. Function identity and `this` are unchanged.
   - `ewcore.js` keeps `SERVER` (tests/test_ports.py reads the literal there),
     the ordered `PARTS` list, installs the parts, calls every `link()`, and
     exports the same `api` literal, byte for byte: same keys, same order.
   - Both pages load every part, in `PARTS` order, before `ewcore.js`. A
     missing part throws `EWCore part missing: <name>`.
2. `tools/ew_loop.py` keeps the Tick, Deps, the git / gate seams and the
   workers; moved verbatim:
   - `tools/loop_base.py`: constants, small helpers, `Items`, `next_number`.
   - `tools/loop_prompts.py`: lane prompts (`GATES`, `plan_prompt`, ...).
   - `tools/loop_roadmap.py`: the work list (ROADMAP rows, plan docs,
     dependencies, hand-off and data items, `resolve_roadmap`, `dispatchable`).
   - `tools/loop_inbox.py`: note / order prompts, routed orders, triage
     kwargs, and the Tick's inbox methods as `InboxMixin`.
   - `tools/loop_merge.py`: `GateCache` and the Tick's merge methods as
     `MergeMixin`.
   - `ew_loop` re-imports every moved name (same object) and
     `class Tick(InboxMixin, MergeMixin)`. The monkeypatched seams (`ROOT`,
     `_git_run`, `_git`, `_xdist_available`, `_gate_argv`) stay defined in
     `ew_loop`; no moved code calls them, so patches keep working. The new
     modules never import `ew_loop` (it runs as `__main__`; a second copy would
     split its globals). Import order is acyclic: base, prompts, roadmap,
     inbox, merge.

## Tests (written first)

- `app/test/ewcore_split.test.js` + `app/test/ewcore_exports.json` (the
  pre-split export surface, name and typeof, in order, captured before the
  split): require surface identical; part files == `PARTS`; both pages load
  every part in order before `ewcore.js`; a plain-script `vm` load gives the
  same surface and results; a missing part throws; ewcore.js under 800 lines,
  each part under 1000.
- `tests/test_loop_split.py`: every pre-split `ew_loop` name still present;
  moved names are the same objects as in their module; Tick keeps all 52
  methods, inbox / merge ones via the mixins; seams stay in `ew_loop`; no new
  module imports `ew_loop`; ew_loop.py under 1700 lines.

## As-built deviations

1. Decision: this plan doc was written by the build lane. The lane base
   (34541f2) carried no `docs/plans/107-*.md` and no research 0018, and the
   main checkout was not readable from the lane. Alternatives: stop and wait
   for the doc. Why: the brief named the goal, the constraints and the tests;
   operator standing order 6 (self-adjudicate, never wait). Reverses if: the
   authored plan 107 doc lands on main with a different module layout - its
   layout wins and the parts are re-cut (the split tests stay valid).
2. Decision: the shadowed duplicate `intIn` in the login-day section of
   ewcore.js was dropped (a comment marks the spot). In the single closure,
   function hoisting made the later stacks-section `intIn` the one every
   caller used; after the split the events part would have used its own
   copy. The events part now links the deadeye part's `intIn` (a forward
   reference), so every caller keeps the definition it ran before.
   Alternatives: keep both (a silent behaviour change for the events part).
   Reverses if: never - it is the pre-split runtime behaviour.
3. Decision: parts are contiguous runs of the original file (named by their
   main domain) rather than regrouped by feature across the file. Why: keeps
   load-time order and every top-level initializer's inputs exactly as
   before (`POST_VALIDATORS` reads `validPortraitsBody` through hoisting, so
   the imperial-to-portraits run stays one part, `shell`). Reverses if: a
   later plan wants finer parts - move a run, the tests catch a load-time
   forward reference.
4. Decision: `ew_loop` keeps its stdlib imports even where no longer used
   (marked `noqa: F401`), so `ew_loop.json` / `ew_loop.time` etc. stay
   attributes. Alternatives: drop them (an attribute-surface change).
5. Decision: the Tick's inbox and merge methods move as mixins; the other
   Tick methods (review, commit, dispatch, push, checklist) stay in ew_loop.
   Why: the brief scoped the move to inbox / roadmap / merge; those methods
   touch only `self.d` and moved constants / helpers, so no test seam is
   crossed. Reverses if: a later plan splits the review / dispatch half.
6. Decision: the lane's progress file (FLEET item 12) was not written: the
   lane's permission set denied writes outside the worktree and running
   ad-hoc scripts. Alternatives: write it through a test runner (would
   launder a denied permission). Reverses if: the lane grant allows the
   progress path.
7. Decision: a new `app/test/levels_parts.test.js` runs plan 018's old
   level-cap scan (`1-70`, `[1, 70]`, `numInput(70)`) over ewcore.js plus
   every `shared/core/*.part.js`. The split moved the level code out of
   ewcore.js, so `levels.test.js`'s ewcore.js scan no longer covered it.
   Alternatives: edit `levels.test.js` (existing tests stay untouched in
   this plan); drop the guard (a silent coverage loss). Why: restores the
   guard without changing a pinned test. Reverses if: the parts are folded
   back into ewcore.js, or `levels.test.js` is widened to the parts - then
   the new file is redundant and goes.
8. Decision: at the merge of main into the lane, the one remaining conflict
   in `tools/ew_loop.py` was resolved by dropping the HEAD-side inbox block
   (inbox(), handled(), answer(), send_batches(), dest_inbox(),
   deliver_note(), redeliver(), write_note(), answer_order(), orders(),
   queue_order()) from ew_loop.py. Main's one change in that block, the
   `handled()` backfill of the kit seen ledger for legacy-settled notes
   (verdict LEGACY), was ported into `tools/loop_inbox.py`. Alternatives:
   keep the monolith block in ew_loop.py and re-import it (duplicates the
   mixin and defeats the split); take the lane side wholesale and lose
   main's backfill. Why: the lane moved that code to loop_inbox.py, so the
   port keeps both changes with one definition. Reverses if: loop_inbox.py
   is folded back into ew_loop.py, or main's backfill diverges again.

refute-rounds: 0/3
