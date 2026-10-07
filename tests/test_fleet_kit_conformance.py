"""Fleet kit v8 is vendored byte-for-byte and the CLAUDE.md FLEET-COMMON block is
byte-identical. Never edit the kit locally; MAIN ships new versions."""

import hashlib
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KIT = ROOT / "ops" / "fleet_kit" / "fleet_headless.py"
SLOTS_SHA256 = "290cbf80ce6989e15ad778be9032503733c8820030bfa6a9b27439b29d70486e"


def _load():
    spec = importlib.util.spec_from_file_location("fleet_headless", KIT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_fleet_kit_conformance():
    assert KIT.is_file()
    assert _load().conformance(ROOT) == []


def test_kit_is_v8_with_all_files():
    import json
    man = json.loads((ROOT / "ops" / "fleet_kit" / "MANIFEST.json").read_text("ascii"))
    assert man["version"] == 8
    assert _load().KIT_VERSION == 8
    assert {"fleet_checklist.py", "fleet_headless.py", "fleet_inbox.py", "fleet_lanes.py",
            "fleet_secrets.py", "fleet_watch.py", "tokens.css", "tokens.json",
            "FLEET-COMMON.md", "LICENSE", "NOTICE"} == set(man["files"])
    on_disk = {p.name for p in (ROOT / "ops" / "fleet_kit").iterdir() if p.is_file()}
    assert on_disk == set(man["files"]) | {"MANIFEST.json"}


def test_shared_slots_governor_is_byte_identical():
    data = (ROOT / "ops" / "loop" / "slots.py").read_bytes()
    assert hashlib.sha256(data).hexdigest() == SLOTS_SHA256
