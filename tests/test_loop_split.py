"""Plan 107: tools/ew_loop.py split by domain, no behaviour change.

The constants / small helpers, prompts, roadmap work list, inbox and merge
code live in tools/loop_*.py; ew_loop still exposes every name it had (the
same object), and the Tick keeps every method (inbox and merge ones through
mixins). The seams tests monkeypatch stay defined in ew_loop itself."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import ew_loop  # noqa: E402

# Every module-level name of ew_loop before the split (imported modules aside).
NAMES = (
    'ATTENTION_STATES', 'BACKOFF_BASE_S', 'BACKOFF_CAP_S', 'BACKOFF_REL', 'CI_SETUP_RX',
    'CI_WORKFLOW_REL', 'CODE', 'CONTROL_REL', 'DEEP_EXTRA', 'DEFAULT_MAX_NOTES',
    'DEFAULT_MAX_PLANS', 'DELIVERY_REL', 'DEPENDS_RX', 'DEP_ID_RX', 'DIRTY_WT_RX',
    'DONE_STATES', 'Deps', 'FLOORS_IN_HOOKS', 'GATES', 'GATE_CACHE_MAX', 'GATE_CACHE_REL',
    'GATE_TIMEOUT_S', 'GateCache', 'HALT_REL', 'HEADROOM', 'HOLD_STATES', 'HOP_RX',
    'INBOX_REL', 'INFRA_RX', 'IN_FLIGHT', 'ITEMS_REL', 'Items', 'KEEP_REF',
    'LANE_TIMEOUT_S', 'LEGACY_LEDGER_REL', 'LIMIT_RX', 'LOCK_REL', 'LOOP_OWNER',
    'MARKER_RX', 'MAX_ATTEMPTS', 'MAX_ROUNDS', 'MERGE_LOCK_REL', 'NEED_RX', 'NOTE_HEAD',
    'NOTE_MAX', 'NOTE_STAMP', 'NO_ROADMAP', 'ORDERS_REL', 'OUTBOX_REL', 'PROGRESS_TASK',
    'PUSH_LAST_REL', 'PUSH_WORKER_REL', 'RED_TTL_S', 'RESOLVABLE', 'REVIEW_LOG_KEEP',
    'REVIEW_START_S', 'ROADMAP_REL', 'ROOT', 'ROUTES', 'ROUTE_EFFORTS', 'ROW_RX',
    'RUN_FIELDS', 'SESSION_TARGETS', 'SHELL_OPERATOR_CHARS', 'SKIP_TAGS', 'STOP',
    'SUITE_GATE_REL', 'TICK_S', 'TICK_TARGET_S', 'Tick', 'VERDICTS_REL', 'VERDICT_RX',
    'VERIFY_EXTRA', 'WATCH_REL', 'XDIST_ARGS', '_BREAKAWAY', '_DATA_ID', '_DETACHED',
    '_KEY_RX', '_NO_WINDOW', '_ROUTE_MODEL', '_gate_argv', '_gates', '_git', '_git_run',
    '_gitlock', '_launch', '_leak_pre_push', '_load_watch', '_names_workers', '_norm_path',
    '_norm_words', '_owner', '_python', '_pythonw', '_run_gate', '_shell_operator',
    '_whole_suite', '_xdist_available', 'ascii_text', 'atomic_write', 'backoff_clear',
    'backoff_hit', 'backoff_until', 'base_kind', 'ci_gate_commands', 'data_items',
    'deep_dive_prompt', 'dependency_cycles', 'dispatchable', 'epoch_of', 'eta_label',
    'fix_prompt', 'flip_roadmap', 'handoff_items', 'handoff_prompt', 'hooks_path_problem',
    'inbox_prompt', 'is_duplicate', 'is_limit', 'iso', 'lane_worker', 'load_config',
    'load_routes', 'main', 'next_number', 'order_id', 'order_prompt', 'plan_depends',
    'plan_doc', 'plan_prompt', 'plan_titles', 'push_worker', 'queued_orders', 'read_json',
    'read_jsonl', 'render_checklist', 'resolve_item', 'resolve_prompt', 'resolve_roadmap',
    'review_worker', 'roadmap_flip_time', 'roadmap_rows', 'route_kind', 'route_kw',
    'session_done', 'session_orders', 'session_paths', 'spawn_block', 'tick',
    'title_tokens', 'tree_key', 'triage_spawn_kwargs', 'verify_prompt', 'with_hop',
)

TICK_METHODS = (
    '_merge', 'answer', 'answer_order', 'blocked', 'blocked_marker', 'carry_verdict',
    'checklist', 'commit', 'commit_refused', 'deep_dive_item', 'deliver_note', 'dest_inbox',
    'dirty', 'dispatch', 'drop_keep', 'extra_checks', 'finished', 'free_lanes', 'gate',
    'handled', 'held_worktrees', 'inbox', 'keep', 'launch_push', 'launch_review',
    'marker_checks', 'merge', 'orders', 'plan_deps', 'process', 'push', 'push_needed',
    'queue_order', 'reap_lost', 'rearm', 'recover_lane_dirty', 'redeliver',
    'resolve_roadmap_conflict', 'retry_refused', 'review_alive', 'review_crashed', 'route',
    'run', 'salvage', 'send_batches', 'settle_conflicts', 'spawn', 'step', 'work_list',
    'worktree_unusable', 'write', 'write_note',
)

MOVED = {
    "loop_base": ("CODE", "CONTROL_REL", "INBOX_REL", "ROUTES", "MARKER_RX", "_NO_WINDOW",
                  "ascii_text", "atomic_write", "with_hop", "read_json", "read_jsonl", "iso",
                  "epoch_of", "load_config", "route_kw", "is_limit", "Items", "next_number"),
    "loop_prompts": ("GATES", "NO_ROADMAP", "plan_prompt", "handoff_prompt", "deep_dive_prompt",
                     "verify_prompt", "fix_prompt", "resolve_prompt"),
    "loop_roadmap": ("roadmap_rows", "flip_roadmap", "plan_depends", "plan_doc", "base_kind",
                     "RUN_FIELDS", "resolve_item", "dependency_cycles", "roadmap_flip_time",
                     "resolve_roadmap", "handoff_items", "VERDICTS_REL", "data_items",
                     "title_tokens", "is_duplicate", "plan_titles", "dispatchable"),
    "loop_inbox": ("inbox_prompt", "order_id", "SESSION_TARGETS", "session_paths",
                   "order_prompt", "queued_orders", "session_orders", "session_done",
                   "FLOORS_IN_HOOKS", "triage_spawn_kwargs"),
    "loop_merge": ("GateCache",),
}
INBOX_METHODS = ("inbox", "handled", "answer", "send_batches", "dest_inbox", "deliver_note",
                 "redeliver", "write_note", "answer_order", "orders", "queue_order")
MERGE_METHODS = ("merge", "_merge", "carry_verdict", "keep", "drop_keep", "settle_conflicts",
                 "resolve_roadmap_conflict")
# monkeypatched on ew_loop by tests (and called through ew_loop globals)
SEAMS = ("ROOT", "_git_run", "_git", "_xdist_available", "_gate_argv")


def test_every_pre_split_name_is_still_on_ew_loop():
    missing = [n for n in NAMES if not hasattr(ew_loop, n)]
    assert missing == []


def test_moved_names_are_the_same_objects():
    import importlib
    for mod, names in MOVED.items():
        m = importlib.import_module(mod)
        for n in names:
            assert getattr(m, n) is getattr(ew_loop, n), f"{mod}.{n}"


def test_tick_keeps_every_method_inbox_and_merge_via_mixins():
    import loop_inbox
    import loop_merge
    assert [m for m in TICK_METHODS if not hasattr(ew_loop.Tick, m)] == []
    assert issubclass(ew_loop.Tick, loop_inbox.InboxMixin)
    assert issubclass(ew_loop.Tick, loop_merge.MergeMixin)
    for m in INBOX_METHODS:
        assert m in vars(loop_inbox.InboxMixin) and m not in vars(ew_loop.Tick), m
    for m in MERGE_METHODS:
        assert m in vars(loop_merge.MergeMixin) and m not in vars(ew_loop.Tick), m


def test_patched_seams_stay_defined_in_ew_loop():
    for n in SEAMS:
        v = getattr(ew_loop, n)
        assert n in vars(ew_loop)
        if callable(v):
            assert v.__module__ == "ew_loop", n


def test_new_modules_never_import_ew_loop():
    # ew_loop runs as __main__; an `import ew_loop` in a part would load a
    # second copy whose monkeypatch seams and globals diverge.
    for mod in MOVED:
        src = (ROOT / "tools" / f"{mod}.py").read_text(encoding="utf-8")
        assert "import ew_loop" not in src and "from ew_loop" not in src, mod


def test_ew_loop_is_smaller():
    n = len((ROOT / "tools" / "ew_loop.py").read_text(encoding="utf-8").splitlines())
    assert n < 1700, n
