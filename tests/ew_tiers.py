"""Plan 097: test tiers and the tier guard.

Markers (registered in pytest.ini, --strict-markers):
  git     the test spawns a real git process (setup or call)
  server  the test binds a socket (HTTP server on 127.0.0.1:0, probes)
  slow    the test's call takes over about 1.5 s on the dev box

`server` is applied automatically at collection when the test, one of its
fixtures, or a module-level helper they call starts a server or binds a socket
in its source (SERVER_RX); `git` and `slow` are always explicit.

The guard is ONE stdlib audit hook (sys.addaudithook) that watches
`subprocess.Popen` and `socket.bind` while a test's setup and call run. An
unmarked test that spawned git or bound a socket FAILS, naming the marker; an
unmarked call over SLOW_WARN_S gets a TierWarning. So markers cannot drift
from what the tests really do.

`--fast` (the fast tier) deselects FAST_EXCLUDES. Installed from
tests/conftest.py:  ew_tiers.install(globals())
"""

import ast
import inspect
import os
import re
import shlex
import sys
import textwrap
import time

import pytest

FAST_EXCLUDES = ("slow", "git")
FAST_MARKEXPR = " and ".join(f"not {m}" for m in FAST_EXCLUDES)
SLOW_WARN_S = 3.0


class TierWarning(pytest.PytestWarning):
    """An unmarked test ran longer than SLOW_WARN_S."""


def _program(args):
    if args is None:
        return ""
    if isinstance(args, bytes):
        args = args.decode("utf-8", "replace")
    if isinstance(args, str):
        try:
            words = shlex.split(args, posix=(os.name != "nt"))
        except ValueError:
            words = args.split()
    elif isinstance(args, os.PathLike):
        words = [args]
    else:
        try:
            words = list(args)
        except TypeError:
            return ""
    if not words:
        return ""
    first = words[0]
    if isinstance(first, bytes):
        first = first.decode("utf-8", "replace")
    name = os.fspath(first).replace("\\", "/").rsplit("/", 1)[-1].strip("\"'").lower()
    return name[:-4] if name.endswith(".exe") else name


def is_git(args):
    """True when a Popen args value runs the git executable."""
    try:
        return _program(args) == "git"
    except Exception:  # noqa: BLE001 - never break a test from the audit hook
        return False


def problems(marks, saw_git, saw_bind):
    """Failure lines for activity the test's markers do not declare."""
    out = []
    if saw_git and "git" not in marks:
        out.append("spawned real git but is not marked @pytest.mark.git")
    if saw_bind and "server" not in marks:
        out.append("bound a socket but is not marked @pytest.mark.server")
    return out


def slow_warning(marks, seconds):
    if "slow" in marks or seconds <= SLOW_WARN_S:
        return ""
    return (f"call took {seconds:.1f} s (> {SLOW_WARN_S:.0f} s) but the test is not "
            "marked @pytest.mark.slow (plan 097)")


class _Recorder:
    def __init__(self):
        self.active = False
        self.git = False
        self.bind = False

    def reset(self, active):
        self.active, self.git, self.bind = active, False, False

    def __call__(self, event, args):
        if not self.active:
            return
        if event == "subprocess.Popen":
            if not self.git and len(args) > 1 and is_git(args[1]):
                self.git = True
        elif event == "socket.bind":
            self.bind = True


_REC = _Recorder()
_HOOKED = []


def _marks(item):
    return {m.name for m in item.iter_markers()}


# The server marker is applied automatically when the test function, one of
# its fixtures, or a module-level helper they call (transitively) starts a
# server or binds a socket in its source; the audit guard catches the rest.
SERVER_RX = re.compile(r"serve_forever|HTTPServer\(|\.bind\(|make_server\(")
_SRC = {}


def _source(fn):
    fn = inspect.unwrap(fn)
    key = getattr(fn, "__code__", fn)
    if key not in _SRC:
        try:
            _SRC[key] = textwrap.dedent(inspect.getsource(fn))
        except (OSError, TypeError):
            _SRC[key] = ""
    return _SRC[key]


def _names(src):
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return set()
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}


def source_binds(funcs, module):
    """True when any function in funcs, or a module-level function of
    `module` they reference (transitively), matches SERVER_RX."""
    seen, todo = set(), list(funcs)
    while todo:
        fn = todo.pop()
        key = getattr(inspect.unwrap(fn), "__code__", fn)
        if key in seen:
            continue
        seen.add(key)
        src = _source(fn)
        if SERVER_RX.search(src):
            return True
        for name in _names(src):
            obj = getattr(module, name, None)
            if inspect.isfunction(obj) and obj.__module__ == getattr(module, "__name__", None):
                todo.append(obj)
    return False


def _item_funcs(item):
    funcs = [item.obj] if inspect.isfunction(getattr(item, "obj", None)) else []
    info = getattr(item, "_fixtureinfo", None)
    for defs in (getattr(info, "name2fixturedefs", None) or {}).values():
        funcs.extend(d.func for d in defs if inspect.isfunction(inspect.unwrap(d.func)))
    return funcs


def install(ns):
    """Add the guard hooks and the --fast option to a conftest's globals()."""
    if not _HOOKED:
        sys.addaudithook(_REC)
        _HOOKED.append(True)

    def pytest_addoption(parser):
        parser.addoption("--fast", action="store_true", default=False,
                         help=f"fast tier: deselect {FAST_MARKEXPR!r} (plan 097)")

    @pytest.hookimpl(tryfirst=True)  # before -m selection sees the markers
    def pytest_collection_modifyitems(config, items):
        for it in items:
            if "server" not in _marks(it) and source_binds(_item_funcs(it),
                                                           getattr(it, "module", None)):
                it.add_marker(pytest.mark.server)
        if not config.getoption("--fast"):
            return
        keep, drop = [], []
        for it in items:
            (drop if _marks(it) & set(FAST_EXCLUDES) else keep).append(it)
        if drop:
            config.hook.pytest_deselected(items=drop)
            items[:] = keep

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_setup(item):
        _REC.reset(True)
        return (yield)

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_call(item):
        t0 = time.perf_counter()
        try:
            res = yield
        except BaseException:
            _REC.reset(False)
            raise
        seconds = time.perf_counter() - t0
        saw_git, saw_bind = _REC.git, _REC.bind
        _REC.reset(False)
        marks = _marks(item)
        warn = slow_warning(marks, seconds)
        if warn:
            item.warn(TierWarning(warn))
        found = problems(marks, saw_git, saw_bind)
        if found:
            pytest.fail("ew_tiers (plan 097): this test " + "; ".join(found), pytrace=False)
        return res

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_teardown(item):
        _REC.reset(False)
        return (yield)

    for f in (pytest_addoption, pytest_collection_modifyitems, pytest_runtest_setup,
              pytest_runtest_call, pytest_runtest_teardown):
        ns[f.__name__] = f
    return ns
