"""EW loop lane prompts (plan 107 split of tools/ew_loop.py, which
re-exports every name here). Stdlib only."""

from loop_base import MAX_ROUNDS, next_number


# ---------------------------------------------------------------- prompts

# Plan 096: producers run touched tests only; the loop runs the authoritative
# ci gates (whole suite included) once on the finished tree.
GATES = ("Gates before you finish: `python -m ruff check server tools tests` green, "
         "the test files your change touched or added green (targeted, e.g. `python -m "
         "pytest -q tests/test_x.py`), and `npm test --prefix app` green if you changed "
         "app/. If you changed shared server/ or tools/ code, also run the fast tier "
         "(plan 097: no slow or real-git tests) with `python tools/ew_tests.py fast "
         "--owner <id>` (it takes a kit suite-gate slot; <id> is the owner id a "
         "denied bare `python -m pytest -q` names). A test that spawns git or binds "
         "a socket needs @pytest.mark.git / @pytest.mark.server (the tier guard fails "
         "it otherwise). Do NOT run the whole pytest suite: the loop runs the authoritative ci "
         "gates (ruff, whole pytest suite, npm test) once on your finished tree and "
         "sends any failure back to you. Every authored file ASCII + LF "
         "(tests/test_ascii_lf.py); no absolute machine path, drive "
         "letter, account id or email in a tracked file. Game ToS floor in CLAUDE.md "
         "is absolute (no game memory, injection, packets, client files or input to "
         "the game window; read-only GETs; robots.txt respected). Do NOT commit, do "
         "NOT edit CLAUDE.md, EW-NEXT-SESSION.txt or ops/fleet_kit/. Any deviation "
         "from the plan goes into an 'As-built deviations' section of the plan doc "
         "(decision, alternatives, why, reverses if) - adjudicate it yourself, never "
         "wait. Write the v7 checklist (FLEET item 13 d) into your progress JSON "
         "ops/loop/control/progress/{task}.json: the FLEET item 12 fields plus "
         "\"checklist\": [{{\"id\", \"task\", \"state\", \"eta_s\"}}, ...] - "
         "remaining steps only, ASCII, updated after each step. Background agents "
         "share one scratchpad: name every helper or scratch script after your "
         "task ({task}_*.py), never a generic name like prog.py.")


NO_ROADMAP = ("Do NOT edit docs/plans/ROADMAP.md: the loop flips your row in main when "
              "it merges (parallel lanes editing it collided). ")


def plan_prompt(item):
    return (f"You are an Ebonwake (EW) build lane in a detached git worktree. Read "
            f"CLAUDE.md first. Task: implement plan {item['id']} per docs/plans/"
            f"{item['id']}-*.md ({item['title']}) - TDD, stdlib only for Python. "
            + NO_ROADMAP + GATES.format(task=f"p{item['id']}-build"))


def handoff_prompt(item):
    return ("You are an Ebonwake (EW) build lane in a detached git worktree. Read "
            "CLAUDE.md first. Work this hand-off item: " + item["text"] + " If it needs "
            "no repo change, change nothing and say why in one line. "
            + NO_ROADMAP + GATES.format(task=item["id"]))


def deep_dive_prompt(root, date, max_plans, today_titles):
    rn = next_number(root, "docs/research", "[0-9][0-9][0-9][0-9]-*.md", 4)
    pn = next_number(root, "docs/plans", "[0-9][0-9][0-9]-*.md", 3)
    titles = "; ".join(f"{k} {v}" for k, v in sorted(today_titles.items()))
    return ("You are the Ebonwake (EW) deep-dive lane (idle mode) in a detached git "
            "worktree. Read CLAUDE.md and docs/research/0001-bdo-data-and-tos.md first. "
            "Research, read-only and unauthenticated, robots.txt respected: current BDO "
            "NA patch notes, events, coupons, public data sources, community tools and "
            "APIs, and ideas from public fleet-sibling projects. Write "
            f"docs/research/{rn:04d}-deep-dive-{date}.md (sources with dates). Propose "
            f"AT MOST {max_plans} new plans, numbered from {pn:03d}, each as "
            "docs/plans/NNN-<slug>.md (first line '# Plan NNN - Title') plus a "
            "docs/plans/ROADMAP.md row '| NNN | title | [ ] open |' and a one-line value "
            "rank in the Ranking text. Do not duplicate an existing plan: " + titles + ". "
            "Write no code. " + GATES.format(task="deep-dive"))


def verify_prompt(item, rnd, tree=None):
    """Plan 096: the loop's ci gates already ran green on exactly this tree;
    the verifier reviews and does not re-run pytest (VERIFY_EXTRA has none)."""
    on = f" (tree {tree[:12]})" if tree else ""
    return (f"You are the EW review-lane verifier, refute round {rnd}/{MAX_ROUNDS}. This "
            f"worktree's uncommitted diff (git status, git diff HEAD) implements: "
            f"{item['label']}. Read-only: never edit. The loop's authoritative ci gates "
            f"(ruff, whole pytest suite, npm test) ran green on exactly this tree{on}: "
            "do not re-run pytest. Check the diff against the plan's acceptance and its "
            "tests, the Game ToS floor in CLAUDE.md, ASCII + LF, and no machine "
            "path. End with exactly one line 'VERDICT: PASS' or 'VERDICT: FAIL', then "
            "numbered findings for a FAIL.")


def fix_prompt(item, rnd, findings):
    return (f"You are the EW producer answering refute round {rnd}/{MAX_ROUNDS} for "
            f"{item['label']} in this worktree. Fix every finding below, or record why "
            "it stands in the plan's 'As-built deviations'. " + GATES.format(task="fix")
            + "\n\nFindings:\n" + "\n".join(findings)[:8000])


def resolve_prompt(item):
    """Plan 058: the lane worker has already started the merge of the kept
    lane commit onto current main in the lane's worktree."""
    ref, doc = item["keep_ref"], ""
    if item.get("base_kind") == "plan":
        doc = f" (docs/plans/{item['id']}-*.md)"
    return ("You are an Ebonwake (EW) resolve lane in a detached git worktree. Read "
            f"CLAUDE.md first. The loop could not merge {item['label']} into main: a merge "
            "conflict. Before you started, the loop ran `git merge --no-ff --no-commit "
            f"{ref}` (the kept lane commit) onto current main in THIS worktree; `git "
            "status` lists the unmerged paths. Resolve every conflict keeping BOTH sides' "
            "features (main's newer work and the lane's), leave no conflict marker, and "
            "record decision / alternatives / why for each non-trivial resolution in the "
            f"'As-built deviations' section of the plan doc{doc}, or in your final reply "
            "when the item has no plan doc. Leave the merge uncommitted: the loop "
            "verifies, commits and merges it. " + NO_ROADMAP
            + GATES.format(task=f"p{item['id']}-resolve"))
