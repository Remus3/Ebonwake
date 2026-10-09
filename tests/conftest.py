"""Kit v12 race guard (FLEET-COMMON item 16 d): a suite that changes live state
(ops/loop/control, moon_sync_inbox, moon_sync_outbox) fails. EW code reads no
environment variable to locate a runtime root (roots resolve from the repo
path), so env_roots is empty."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops" / "fleet_kit"))
import fleet_test_guard  # noqa: E402

fleet_test_guard.install(globals(), root=Path(__file__).resolve().parents[1], env_roots={})
