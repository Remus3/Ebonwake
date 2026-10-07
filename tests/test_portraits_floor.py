"""Plan 082 ToS floor: portraits.py only ever READS FaceTexture.

AST scan (research 0011 s3): no write-mode open(), no os-level remove /
rename / replace / utime / chmod / unlink, path-mutating methods only inside
the two archive helpers, and no reference to the per-character UI cache,
Customization or the game folder. A runtime check confirms a scan leaves the
FaceTexture bytes and timestamps untouched.
"""

import ast
import os
from pathlib import Path

from server.ew import portraits
from server.ew.store import Store

SRC = Path(portraits.__file__)
TREE = ast.parse(SRC.read_text(encoding="utf-8"))
MUTATORS = {"write_bytes", "write_text", "unlink", "replace", "rename", "touch", "chmod",
            "rmdir", "utime", "remove", "truncate"}
ARCHIVE_HELPERS = {"_write_archive", "_drop_archive"}


def _funcs():
    for node in ast.walk(TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def test_no_write_mode_open():
    for node in ast.walk(TREE):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "open":
            modes = [a for a in node.args[1:2]] + [k.value for k in node.keywords if k.arg == "mode"]
            assert modes, "open() without an explicit mode"
            for m in modes:
                assert isinstance(m, ast.Constant) and isinstance(m.value, str)
                assert not set(m.value) & set("wax+"), f"write-mode open at line {node.lineno}"


def test_no_os_level_mutation():
    for node in ast.walk(TREE):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                and node.value.id in ("os", "shutil"):
            assert node.attr not in MUTATORS | {"rmtree", "move", "copy", "copyfile",
                                                "makedirs", "mkdir"}, \
                f"{node.value.id}.{node.attr} at line {node.lineno}"


def test_path_mutators_only_in_archive_helpers():
    allowed = set()
    for fn in _funcs():
        if fn.name in ARCHIVE_HELPERS:
            allowed |= {id(n) for n in ast.walk(fn)}
    for node in ast.walk(TREE):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in MUTATORS:
            assert id(node) in allowed, f".{node.func.attr}() outside archive helpers, line {node.lineno}"


def test_archive_helpers_only_called_with_archive_dir():
    for node in ast.walk(TREE):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) in ARCHIVE_HELPERS:
            text = ast.unparse(node.args[0])
            assert "archive_dir" in text and "face" not in text.lower(), text


def test_no_cache_customization_or_game_folder_reference():
    text = SRC.read_text(encoding="utf-8")
    for word in ("UserCache", "Customization", "install", "gameVariable", "BlackDesert64"):
        assert word.lower() not in text.lower(), word


def test_scan_leaves_facetexture_untouched(tmp_path):
    from tests.test_portraits import bmp

    face = tmp_path / "docs" / "FaceTexture"
    face.mkdir(parents=True)
    p = face / "12345678901234567.bmp"
    data = bmp(8, 8)
    p.write_bytes(data)
    os.utime(p, (1_700_000_000, 1_700_000_000))
    before = (p.stat().st_mtime_ns, p.stat().st_size, sorted(os.listdir(face)))
    svc = portraits.PortraitService(Store(tmp_path / "s"), tmp_path / "arch",
                                    documents=lambda: tmp_path / "docs",
                                    loads=lambda: [{"char_no": "12345678901234567", "at": "x"}],
                                    progress_cls=lambda: "Deadeye")
    svc.poll("logged_in", 0)
    svc.scan()
    assert svc.current("Deadeye") is not None
    assert p.read_bytes() == data
    assert (p.stat().st_mtime_ns, p.stat().st_size, sorted(os.listdir(face))) == before
