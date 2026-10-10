"""pytest.ini keeps tmp_path dirs only for failed tests, at most 1 base dir
(MAIN FIX N7416e2, plan 105). Key pin only; retention itself is proved by the
post-merge run-dir count (plan 105 verification)."""

import configparser
from pathlib import Path

INI = Path(__file__).resolve().parent.parent / "pytest.ini"


def test_tmp_path_retention_configured():
    cfg = configparser.ConfigParser()
    cfg.read(INI, encoding="utf-8")
    assert cfg.get("pytest", "tmp_path_retention_policy") == "failed"
    assert cfg.get("pytest", "tmp_path_retention_count") == "1"
