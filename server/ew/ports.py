"""EW port registry. Block 8940-8959 (assigned 2026-10-04 by MAIN).

Import these constants; a port literal at a bind site is a defect
(tests/test_ports.py).
"""

BLOCK = range(8940, 8960)

SERVER = 8940  # EW server: API, dashboard assets, overlay SSE

ASSIGNED = {"server": SERVER}
